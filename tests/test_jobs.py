import shutil

import pytest

from tests.conftest import make_media
from voice_to_doc.config import Settings
from voice_to_doc.devfake import FakeGroq
from voice_to_doc.groq_client import RateLimited, make_client
from voice_to_doc.jobs import JobStore, Pipeline


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "data")


def add_job(store, tmp_path, kind="lecture", seconds=7, filename="Лекция 1.MP3"):
    job = store.create(filename, kind)
    shutil.copy(make_media(tmp_path / "src.wav", seconds), store.dir(job["id"]) / job["source"])
    return job


def pipeline(store, clock, client=None, **settings):
    s = Settings(groq_api_key="k", chunk_seconds=3, **settings)
    fake = client or FakeGroq()
    return Pipeline(store, lambda: s, lambda _s: fake, sleep=clock.sleep, clock=clock), fake


def test_source_name_is_sanitized(store):
    assert store.create("Лекция 1.MP3", "lecture")["source"] == "source.mp3"
    assert store.create("без расширения", "lecture")["source"] == "source"
    assert store.create("../../etc/passwd.очень-длинное", "lecture")["source"] == "source"


def test_dir_rejects_bad_ids(store):
    for bad in ["../x", "", "20261012-215800-zzzzzz"]:
        with pytest.raises(KeyError):
            store.dir(bad)


def test_list_newest_first(store):
    a = store.create("a.mp3", "lecture")
    b = store.create("b.mp3", "lecture")
    store.update(a["id"], created_at="2026-01-01T00:00:00")
    assert [j["id"] for j in store.list()] == [b["id"], a["id"]]


def test_full_run(store, tmp_path, clock):
    job = add_job(store, tmp_path)
    p, fake = pipeline(store, clock)
    p.run(job["id"])
    job = store.get(job["id"])
    assert job["status"] == "done" and job["step"] is None and job["reason"] is None
    assert job["duration_s"] == pytest.approx(7, abs=0.1)
    d = store.dir(job["id"])
    md = (d / "result.md").read_text()
    assert md.startswith("# Тестовая запись") and "## [00:00]" in md
    assert (d / "result.docx").stat().st_size > 0
    assert sum(1 for c in fake.calls if c[0] == "transcribe") == 3
    assert "Распознан кусок 3/3" in (d / "log.txt").read_text()


def test_bad_file_is_error(store, tmp_path, clock):
    job = store.create("bad.mp3", "lecture")
    (store.dir(job["id"]) / job["source"]).write_text("not audio")
    p, _ = pipeline(store, clock)
    p.run(job["id"])
    job = store.get(job["id"])
    assert (job["status"], job["reason"]) == ("error", "file")
    assert "Не удалось прочитать файл" in job["message"]


def test_missing_key_pauses_with_auth_reason(store, tmp_path, clock):
    job = add_job(store, tmp_path)
    p = Pipeline(store, lambda: Settings(groq_api_key="", chunk_seconds=3), make_client, sleep=clock.sleep, clock=clock)
    p.run(job["id"])
    job = store.get(job["id"])
    assert (job["status"], job["reason"], job["message"]) == ("paused", "auth", "Не задан ключ Groq")


class DailyOnceFake(FakeGroq):
    def __init__(self):
        super().__init__()
        self.failed = False

    def chat_json(self, system, user, max_tokens):
        if not self.failed:
            self.failed = True
            raise RateLimited("tokens per day (TPD)", retry_after=10, daily=True)
        return super().chat_json(system, user, max_tokens)


def test_daily_limit_pauses_then_resumes_without_retranscribing(store, tmp_path, clock):
    job = add_job(store, tmp_path)
    p, fake = pipeline(store, clock, client=DailyOnceFake())
    p.run(job["id"])
    assert (store.get(job["id"])["status"], store.get(job["id"])["reason"]) == ("paused", "daily")
    p.run(job["id"])
    assert store.get(job["id"])["status"] == "done"
    assert sum(1 for c in fake.calls if c[0] == "transcribe") == 3


def test_unexpected_exception_is_internal_error(store, tmp_path, clock):
    class Boom(FakeGroq):
        def transcribe(self, path, language):
            raise RuntimeError("boom")
    job = add_job(store, tmp_path)
    p, _ = pipeline(store, clock, client=Boom())
    p.run(job["id"])
    job = store.get(job["id"])
    assert (job["status"], job["reason"]) == ("error", "internal") and "boom" not in job["message"]
    assert "log.txt" in job["message"]
    log = (store.dir(job["id"]) / "log.txt").read_text()
    assert "Traceback" in log and "boom" in log


def test_restructure_reuses_transcript(store, tmp_path, clock):
    job = add_job(store, tmp_path)
    p, fake = pipeline(store, clock)
    p.run(job["id"])
    p.restructure(job["id"], "interview")
    assert store.get(job["id"])["status"] == "queued"
    assert not (store.dir(job["id"]) / "result.md").exists()
    p.run(job["id"])
    assert "**Интервьюер [00:00]:**" in (store.dir(job["id"]) / "result.md").read_text()
    assert sum(1 for c in fake.calls if c[0] == "transcribe") == 3


def test_recover_interrupted(store):
    running = store.create("a.mp3", "lecture")
    queued = store.create("b.mp3", "lecture")
    done = store.create("c.mp3", "lecture")
    store.update(running["id"], status="running")
    store.update(queued["id"], status="queued")
    store.update(done["id"], status="done")
    assert store.recover_interrupted() == [queued["id"]]
    r = store.get(running["id"])
    assert (r["status"], r["reason"]) == ("paused", "restart")
    assert store.get(done["id"])["status"] == "done"


def test_new_job_is_uploading_and_recover_drops_unfinished_uploads(store):
    job = store.create("a.mp3", "lecture")
    assert job["status"] == "uploading"
    assert store.recover_interrupted() == []
    with pytest.raises(KeyError):
        store.get(job["id"])


def test_transition_only_from_allowed_status(store):
    job = store.create("a.mp3", "lecture")
    store.update(job["id"], status="paused")
    assert store.transition(job["id"], ("paused", "error"), status="queued")["status"] == "queued"
    assert store.transition(job["id"], ("paused", "error"), status="queued") is None


def test_worker_survives_crashing_run():
    import time

    from voice_to_doc.jobs import Worker

    done = []

    class Flaky:
        def run(self, job_id):
            if job_id == "bad":
                raise RuntimeError("disk gone")
            done.append(job_id)

    worker = Worker(Flaky())
    worker.start()
    worker.submit("bad")
    worker.submit("good")
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        time.sleep(0.01)
    assert done == ["good"]


def test_rate_limit_wait_is_shown_in_job_message(store, tmp_path, clock):
    class MinuteOnce(FakeGroq):
        hit = False

        def transcribe(self, path, language):
            if not self.hit:
                self.hit = True
                raise RateLimited("tokens per minute (TPM)", retry_after=5, daily=False)
            return super().transcribe(path, language)

    job = add_job(store, tmp_path)
    seen = []

    def sleep(seconds):
        seen.append(store.get(job["id"])["message"])
        clock.sleep(seconds)

    fake = MinuteOnce()
    p = Pipeline(store, lambda: Settings(groq_api_key="k", chunk_seconds=3), lambda _s: fake, sleep=sleep, clock=clock)
    p.run(job["id"])
    assert any(m.startswith("Лимит Groq") for m in seen)
    assert store.get(job["id"])["message"] == ""


def test_data_dirs_are_private(store):
    job = store.create("a.mp3", "lecture")
    assert store.data_dir.stat().st_mode & 0o777 == 0o700
    assert store.dir(job["id"]).stat().st_mode & 0o777 == 0o700


def test_unknown_extensions_are_not_kept(store):
    assert store.create("x.m3u8", "lecture")["source"] == "source"
    assert store.create("x.FLAC", "lecture")["source"] == "source.flac"


def test_annotations_evaluate_on_older_pythons():
    """Python < 3.14 evaluates annotations eagerly; `list` inside JobStore is the method, not the builtin."""
    for name in dir(JobStore):
        attr = getattr(JobStore, name)
        if callable(attr) and not name.startswith("__"):
            attr.__annotations__  # noqa: B018 — raises TypeError if an annotation is broken


def test_list_skips_broken_or_vanishing_jobs(store):
    good = store.create("a.mp3", "lecture")
    broken = store.create("b.mp3", "lecture")
    (store.dir(broken["id"]) / "job.json").write_text("{not json")
    (store.data_dir / "20261013-000000-abcdef").mkdir()  # no job.json at all
    assert [j["id"] for j in store.list()] == [good["id"]]


def test_filename_is_cleaned_and_capped(store):
    job = store.create("a\x00b\nc" + "я" * 500 + ".mp3", "lecture")
    assert "\x00" not in job["filename"] and "\n" not in job["filename"]
    assert len(job["filename"]) <= 200 and job["filename"].endswith(".mp3")
    assert job["source"] == "source.mp3"


def test_chunks_removed_after_transcription(store, tmp_path, clock):
    job = add_job(store, tmp_path)
    p, _ = pipeline(store, clock)
    p.run(job["id"])
    d = store.dir(job["id"])
    assert not (d / "chunks").exists() and (d / "transcript.json").exists()
    p.restructure(job["id"], "interview")
    p.run(job["id"])
    assert store.get(job["id"])["status"] == "done"


def test_delete_refuses_active_jobs(store):
    job = store.create("a.mp3", "lecture")
    store.update(job["id"], status="running")
    assert store.delete_if_idle(job["id"]) is False
    store.update(job["id"], status="done")
    assert store.delete_if_idle(job["id"]) is True
    assert not store.dir(job["id"]).exists()
