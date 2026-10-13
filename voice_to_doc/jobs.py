"""Jobs: on-disk state, the processing pipeline and a single background worker."""

import builtins
import json
import logging
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from voice_to_doc.audio import AudioError, prepare_audio
from voice_to_doc.config import load_settings
from voice_to_doc.fsutil import write_text_atomic
from voice_to_doc.groq_client import AuthError, DailyLimitExceeded, GroqError, Unavailable, make_client
from voice_to_doc.ratelimit import WAIT_PREFIXES, RateLimiter
from voice_to_doc.render import to_docx, to_markdown
from voice_to_doc.structure import LLM, make_document, structure_transcript
from voice_to_doc.transcribe import transcribe_chunks

KINDS = ("lecture", "interview")
STEPS = ("prepare", "transcribe", "structure", "summary", "render")
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_FILENAME_JUNK = re.compile(r"[\x00-\x1f\x7f]")
MAX_FILENAME = 200
ACTIVE = ("uploading", "queued", "running")
MEDIA_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac", ".aiff", ".aif",
              ".caf", ".wma", ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv"}


def clean_filename(name: str) -> str:
    """The original filename is only displayed: drop control characters and cap the length."""
    name = _FILENAME_JUNK.sub("", name).strip() or "запись"
    if len(name) > MAX_FILENAME:
        suffix = Path(name).suffix[:10]
        stem = name[: len(name) - len(suffix)] if suffix else name
        name = stem[: MAX_FILENAME - len(suffix)] + suffix
    return name


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)  # transcripts are private
        self._lock = threading.Lock()

    def dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _ID.match(job_id):
            raise KeyError(job_id)
        return self.data_dir / job_id

    def create(self, filename: str, kind: str) -> dict:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        filename = clean_filename(filename)
        ext = Path(filename).suffix.lower()
        job = {
            "id": job_id, "filename": filename, "source": "source" + (ext if ext in MEDIA_EXTS else ""),
            "kind": kind, "created_at": datetime.now().isoformat(timespec="seconds"), "duration_s": None,
            "status": "uploading", "step": None, "progress": {"done": 0, "total": 0},
            "step_started_at": None, "message": "", "reason": None,
        }
        self.dir(job_id).mkdir(mode=0o700)
        self._write(job)
        return job

    def get(self, job_id: str) -> dict:
        path = self.dir(job_id) / "job.json"
        if not path.exists():
            raise KeyError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **fields) -> dict:
        with self._lock:
            job = {**self.get(job_id), **fields}
            self._write(job)
            return job

    def transition(self, job_id: str, from_statuses: tuple[str, ...], **fields) -> dict | None:
        """Atomically update the job only if its status is one of from_statuses."""
        with self._lock:
            job = self.get(job_id)
            if job["status"] not in from_statuses:
                return None
            job.update(fields)
            self._write(job)
            return job

    def list(self) -> builtins.list[dict]:
        jobs = []
        for path in self.data_dir.glob("*/job.json"):
            try:  # a job may be deleted mid-scan; a damaged file must not hide the others
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(jobs, key=lambda j: (j["created_at"], j["id"]), reverse=True)

    def log(self, job_id: str, message: str) -> None:
        with open(self.dir(job_id) / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def delete_if_idle(self, job_id: str) -> bool:
        """Delete a job and all its files unless it is being uploaded or processed."""
        with self._lock:
            if self.get(job_id)["status"] in ACTIVE:
                return False
            self.delete(job_id)
            return True

    def recover_interrupted(self) -> builtins.list[str]:
        queued = []
        for job in reversed(self.list()):
            if job["status"] == "running":
                self.update(job["id"], status="paused", reason="restart", message="Работа прервана перезапуском программы")
            elif job["status"] == "queued":
                queued.append(job["id"])
            elif job["status"] == "uploading":
                self.delete(job["id"])  # the upload was cut off; the file is incomplete
        return queued

    def _write(self, job: dict) -> None:
        write_text_atomic(self.dir(job["id"]) / "job.json", json.dumps(job, ensure_ascii=False))


class Pipeline:
    def __init__(self, store: JobStore, settings_loader=load_settings, client_factory=make_client,
                 sleep=time.sleep, clock=time.monotonic):
        self.store = store
        self.settings_loader = settings_loader
        self.client_factory = client_factory
        self.sleep = sleep
        settings = settings_loader()
        # Limiters live as long as the pipeline so pacing carries over between jobs.
        self.whisper_limiter = RateLimiter(settings.whisper_rpm, clock=clock, sleep=sleep)
        self.llm_limiter = RateLimiter(settings.llm_rpm, settings.llm_tpm, clock=clock, sleep=sleep)

    def run(self, job_id: str) -> None:
        def log(message: str) -> None:
            self.store.log(job_id, message)
            if message.startswith(WAIT_PREFIXES):
                self.store.update(job_id, message=message)

        self.store.update(job_id, status="running", message="", reason=None)
        log("Старт обработки")
        try:
            self._run_steps(job_id, log)
        except AudioError as e:
            log(f"ffmpeg: {e.detail}")
            self._stop(job_id, "error", "file", str(e))
        except AuthError as e:
            self._stop(job_id, "paused", "auth", str(e))
        except DailyLimitExceeded:
            self._stop(job_id, "paused", "daily", "Дневной лимит Groq исчерпан — нажмите «Продолжить» позже")
        except Unavailable:
            self._stop(job_id, "paused", "network", "Нет связи с Groq — проверьте интернет и нажмите «Продолжить»")
        except GroqError as e:
            self._stop(job_id, "error", "groq", str(e))
        except Exception:  # noqa: BLE001 — the worker must survive anything
            log(traceback.format_exc())
            self._stop(job_id, "error", "internal", "Внутренняя ошибка — подробности в файле log.txt задания")
        else:
            self.store.update(job_id, status="done", step=None, message="")
            log("Готово")

    def restructure(self, job_id: str, kind: str) -> None:
        d = self.store.dir(job_id)
        shutil.rmtree(d / "blocks", ignore_errors=True)
        for name in ("structured.json", "document.json", "result.md", "result.docx"):
            (d / name).unlink(missing_ok=True)
        self.store.update(job_id, kind=kind, status="queued", step=None, message="", reason=None,
                          progress={"done": 0, "total": 0})

    def _stop(self, job_id, status, reason, message):
        self.store.update(job_id, status=status, reason=reason, message=message)
        self.store.log(job_id, f"{status}: {message}")

    def _step(self, job_id, name):
        self.store.update(job_id, step=name, progress={"done": 0, "total": 0}, step_started_at=time.time())
        return lambda done, total: self.store.update(job_id, progress={"done": done, "total": total}, message="")

    def _run_steps(self, job_id: str, log) -> None:
        settings = self.settings_loader()
        job = self.store.get(job_id)
        d = self.store.dir(job_id)
        client = None

        def get_client():
            nonlocal client
            client = client or self.client_factory(settings)
            return client

        def llm():
            return LLM(get_client(), self.llm_limiter, settings.max_output_tokens, settings.chars_per_token,
                       sleep=self.sleep, log=log)

        if not (d / "chunks.json").exists():
            self._step(job_id, "prepare")
            prepare_audio(d / job["source"], d, settings.chunk_seconds)
        manifest = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
        self.store.update(job_id, duration_s=manifest["duration_s"])

        if not (d / "transcript.json").exists():
            progress = self._step(job_id, "transcribe")
            transcribe_chunks(d, get_client(), self.whisper_limiter, settings.language,
                              on_progress=progress, sleep=self.sleep, log=log)
        shutil.rmtree(d / "chunks", ignore_errors=True)  # only needed for transcription; frees disk

        if (d / "structured.json").exists():
            structured = json.loads((d / "structured.json").read_text(encoding="utf-8"))
        else:
            progress = self._step(job_id, "structure")
            structured = structure_transcript(d, job["kind"], llm(), settings.block_tokens, on_progress=progress)

        if (d / "document.json").exists():
            doc = json.loads((d / "document.json").read_text(encoding="utf-8"))
        else:
            self._step(job_id, "summary")
            doc = make_document(structured, llm(), Path(job["filename"]).stem, settings.block_tokens)
            write_text_atomic(d / "document.json", json.dumps(doc, ensure_ascii=False))

        self._step(job_id, "render")
        write_text_atomic(d / "result.md", to_markdown(doc))
        to_docx(doc, d / "result.docx.tmp")
        (d / "result.docx.tmp").replace(d / "result.docx")


class Worker:
    """Runs one job at a time: Groq limits are shared, parallel jobs would only fight over them."""

    def __init__(self, pipeline: Pipeline, inline: bool = False):
        self.pipeline = pipeline
        self.inline = inline
        self._queue: queue.Queue[str] = queue.Queue()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="voice-to-doc-worker").start()

    def submit(self, job_id: str) -> None:
        if self.inline:
            self.pipeline.run(job_id)
        else:
            self._queue.put(job_id)

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self.pipeline.run(job_id)
            except Exception:  # noqa: BLE001 — one broken job must not stop the queue
                logging.getLogger(__name__).exception("job %s crashed the worker", job_id)
