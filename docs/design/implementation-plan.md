# voice-to-doc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Локальное веб-приложение: загружаешь запись лекции/интервью → получаешь структурированный документ (выжимка + почти дословный текст по разделам/репликам с таймкодами) в Markdown и .docx.

**Architecture:** FastAPI-сервер на `127.0.0.1` с одним фоновым потоком-обработчиком. Конвейер задания: ffmpeg режет звук на куски → Groq Whisper распознаёт → Groq LLM оформляет блоками → выжимка → рендер. Каждый шаг пишет результат в `data/<job_id>/`, поэтому задание возобновляется с первого незавершённого шага. Все сетевые вызовы идут через `groq_client.py`, который в тестах подменяется `devfake.FakeGroq`.

**Tech Stack:** Python 3.14, FastAPI 0.143, uvicorn 0.54, groq SDK 1.7, python-docx 1.2, imageio-ffmpeg 0.6 (встроенный ffmpeg 7.1 с libopus), python-dotenv, pytest 9, Playwright 1.63; фронтенд — ванильные HTML/CSS/JS + marked 12.0.2 (cdnjs).

**Spec:** `docs/design/spec.md`

## Global Constraints

- Проект: `~/Projects/voice-to-doc`; пакет `voice_to_doc/`; тесты `tests/`; окружение `.venv` (`python3 -m venv .venv`).
- Все команды запускаются из корня проекта; Python — `.venv/bin/python`, тесты — `.venv/bin/pytest`.
- Модели: `whisper-large-v3`, `openai/gpt-oss-120b`; лимиты LLM: 8 000 токенов/мин, 30 запросов/мин; Whisper: 20 запросов/мин, файл ≤ 25 МБ. Все — в `Settings`, переопределяются через `.env`.
- Куски звука: моно, 16 кГц, ogg/opus 32 кбит/с, по 600 с.
- Блок для LLM: ≈ 2 500 токенов входа; оценка токенов = `len(text) / 2.5 + 1`.
- Проверка длины: оформленный текст < 70 % символов исходного (для блоков ≥ 200 символов) → повтор, затем запасной вариант.
- Повторы при сетевых сбоях / 5xx: задержки 2, 4, 8 с, затем пауза задания.
- Загрузка > 2 ГБ отклоняется (413). Сервер слушает только `127.0.0.1:8000`.
- Ключ API никогда не пишется в журнал и не отдаётся через API.
- Тексты интерфейса и сообщения — на русском. UI: нейтральная палитра + один акцент, без градиентов и эмодзи, светлая/тёмная тема по системе, ширина документа ≤ 760 px, без горизонтальной прокрутки на 375 px.
- Таймкоды: `MM:SS` для записей короче 3600 с; для записей ≥ 3600 с — везде `H:MM:SS`.
- Отклонение от спецификации (уточнение): в `structured.json`/`document.json` разделы и реплики хранятся в одном поле `items` (тип определяется `kind`); таймкоды хранятся в секундах (float).
- Коммиты заканчиваются строкой `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Тишина/музыка в записи** — Whisper галлюцинирует («Продолжение следует…») или возвращает пусто. Ожидание: сегменты с `no_speech_prob ≥ 0.8` отбрасываются; пустая расшифровка даёт документ с замечанием «В записи не найдено речи», а не падение. Тесты: Task 5 (`test_drops_empty_and_no_speech_segments`), Task 7 (`test_empty_transcript_gives_warning`).
2. **Имя файла с кириллицей, пробелами, без расширения или с `../`** — Ожидание: файл сохраняется как `source<.ext>` внутри папки задания, исходное имя только отображается; id задания с `..` отклоняется. Тесты: Task 9 (`test_source_name_is_sanitized`, `test_dir_rejects_bad_ids`).
3. **Один сегмент длиннее бюджета блока** — Ожидание: попадает в отдельный блок, без бесконечного цикла и потери текста. Тест: Task 6 (`test_oversized_segment_gets_own_block`).
4. **Модель вернула таймкоды не по порядку, выдуманные или пустые разделы** — Ожидание: таймкоды привязываются к ближайшим входным и становятся неубывающими, пустые разделы отбрасываются. Тест: Task 6 (`test_validate_lecture_snaps_orders_and_drops_empty`).
5. **Перезапуск посреди распознавания** — Ожидание: уже распознанные куски повторно не отправляются (лимит не тратится). Тесты: Task 5 (`test_resume_skips_done_chunks`), Task 9 (`test_recover_interrupted`).

---

## File Structure

```
voice-to-doc/
  requirements.txt, requirements-dev.txt, pyproject.toml, run.sh, README.md, .gitignore
  voice_to_doc/
    __init__.py
    config.py       Settings, load_settings, save_api_key
    audio.py        prepare_audio (ffmpeg), AudioError
    groq_client.py  GroqClient, make_client, исключения, translate_error
    devfake.py      FakeGroq — офлайн-замена Groq для тестов и проверки UI
    ratelimit.py    RateLimiter, call_with_retries
    transcribe.py   shift_segments, transcribe_chunks
    structure.py    таймкоды, блоки, валидация, промпты, LLM, structure_transcript, make_document
    render.py       to_markdown, to_docx
    jobs.py         JobStore, Pipeline, Worker
    app.py          create_app (FastAPI)
    static/         index.html, style.css, app.js
  tests/
    conftest.py, test_config.py, test_audio.py, test_groq_client.py, test_ratelimit.py,
    test_transcribe.py, test_structure_basics.py, test_structure_llm.py, test_render.py,
    test_jobs.py, test_app.py, test_live.py
  scripts/ui_check.py
```

---

### Task 1: Каркас проекта и настройки

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `voice_to_doc/__init__.py`, `voice_to_doc/config.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`
- Modify: `.gitignore` (уже содержит `.env`, `data/`, `.venv/`, `__pycache__/`, `.pytest_cache/` — добавить `screenshots/`)

**Interfaces:**
- Produces: `Settings` (frozen dataclass: `groq_api_key: str, whisper_model: str, llm_model: str, language: str, chunk_seconds: int, block_tokens: int, chars_per_token: float, max_output_tokens: int, llm_tpm: int, llm_rpm: int, whisper_rpm: int, data_dir: Path, env_path: Path, fake_groq: bool, fake_delay: float`); `load_settings(env_path: Path = ENV_PATH) -> Settings`; `save_api_key(key: str, env_path: Path = ENV_PATH) -> None`; `ROOT`, `ENV_PATH`, `_FIELDS`; фикстура `FakeClock` в `tests/conftest.py`.

- [ ] **Step 1: Создать окружение и файлы зависимостей**

`requirements.txt`:
```
fastapi==0.143.0
uvicorn==0.54.0
python-multipart==0.0.32
groq==1.7.0
python-docx==1.2.0
imageio-ffmpeg==0.6.0
python-dotenv==1.2.4
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest==9.1.1
httpx==0.28.1
playwright==1.63.0
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: calls the real Groq API (needs GROQ_API_KEY in .env)"]
addopts = "-m 'not live'"
```

Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
mkdir -p voice_to_doc tests && touch voice_to_doc/__init__.py tests/__init__.py
echo "screenshots/" >> .gitignore
```
Expected: установка без ошибок.

- [ ] **Step 2: Написать падающие тесты**

`tests/conftest.py`:
```python
import pytest

from voice_to_doc.config import _FIELDS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not see the developer's real environment variables."""
    for key in _FIELDS:
        monkeypatch.delenv(key, raising=False)


class FakeClock:
    """Monotonic clock whose sleep() just advances time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()
```

`tests/test_config.py`:
```python
import pytest

from voice_to_doc.config import load_settings, save_api_key


def test_defaults_when_env_file_missing(tmp_path):
    s = load_settings(tmp_path / ".env")
    assert s.groq_api_key == ""
    assert s.llm_model == "openai/gpt-oss-120b"
    assert s.whisper_model == "whisper-large-v3"
    assert (s.chunk_seconds, s.block_tokens, s.llm_tpm) == (600, 2500, 8000)
    assert s.env_path == tmp_path / ".env"


def test_reads_values_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=gsk_abc\nCHUNK_SECONDS=300\nVTD_FAKE_GROQ=1\nDATA_DIR=/tmp/x\n")
    s = load_settings(env)
    assert (s.groq_api_key, s.chunk_seconds, s.fake_groq) == ("gsk_abc", 300, True)
    assert str(s.data_dir) == "/tmp/x"


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=a\n")
    monkeypatch.setenv("LLM_MODEL", "b")
    assert load_settings(env).llm_model == "b"


def test_save_api_key_replaces_old_key_and_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=x\nGROQ_API_KEY=old\n")
    save_api_key("  gsk_new  ", env)
    assert env.read_text() == "LLM_MODEL=x\nGROQ_API_KEY=gsk_new\n"
    assert load_settings(env).groq_api_key == "gsk_new"


@pytest.mark.parametrize("bad", ["", "   ", "gsk a", "gsk\nX=1"])
def test_save_api_key_rejects_empty_and_whitespace(tmp_path, bad):
    with pytest.raises(ValueError):
        save_api_key(bad, tmp_path / ".env")
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.config'`

- [ ] **Step 4: Реализовать `voice_to_doc/config.py`**

```python
"""Settings loaded from .env; environment variables take precedence."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = ""
    whisper_model: str = "whisper-large-v3"
    llm_model: str = "openai/gpt-oss-120b"
    language: str = "ru"
    chunk_seconds: int = 600
    block_tokens: int = 2500
    chars_per_token: float = 2.5
    max_output_tokens: int = 4000
    llm_tpm: int = 8000
    llm_rpm: int = 30
    whisper_rpm: int = 20
    data_dir: Path = ROOT / "data"
    env_path: Path = ENV_PATH
    fake_groq: bool = False
    fake_delay: float = 0.0


# .env key -> (Settings attribute, converter)
_FIELDS = {
    "GROQ_API_KEY": ("groq_api_key", str),
    "WHISPER_MODEL": ("whisper_model", str),
    "LLM_MODEL": ("llm_model", str),
    "LANGUAGE": ("language", str),
    "CHUNK_SECONDS": ("chunk_seconds", int),
    "BLOCK_TOKENS": ("block_tokens", int),
    "CHARS_PER_TOKEN": ("chars_per_token", float),
    "MAX_OUTPUT_TOKENS": ("max_output_tokens", int),
    "LLM_TPM": ("llm_tpm", int),
    "LLM_RPM": ("llm_rpm", int),
    "WHISPER_RPM": ("whisper_rpm", int),
    "DATA_DIR": ("data_dir", Path),
    "VTD_FAKE_GROQ": ("fake_groq", lambda v: v == "1"),
    "VTD_FAKE_DELAY": ("fake_delay", float),
}


def load_settings(env_path: Path = ENV_PATH) -> Settings:
    raw = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    raw.update({k: os.environ[k] for k in _FIELDS if k in os.environ})
    kwargs = {
        attr: convert(raw[key])
        for key, (attr, convert) in _FIELDS.items()
        if raw.get(key, "") != ""
    }
    return Settings(env_path=env_path, **kwargs)


def save_api_key(key: str, env_path: Path = ENV_PATH) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Ключ не должен быть пустым или содержать пробелы")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [line for line in lines if not line.startswith("GROQ_API_KEY=")]
    lines.append(f"GROQ_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: project scaffold and settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Подготовка звука (ffmpeg)

**Files:**
- Create: `voice_to_doc/audio.py`, `tests/test_audio.py`
- Modify: `tests/conftest.py` (добавить `make_media`)

**Interfaces:**
- Consumes: —
- Produces: `class AudioError(Exception)` с атрибутом `detail: str`; `READ_ERROR: str`; `parse_segment_list(csv_text: str) -> list[dict]` (`{"file", "start", "end"}`); `prepare_audio(src: Path, job_dir: Path, chunk_seconds: int) -> dict` — пишет `job_dir/chunks/NNN.ogg` и `job_dir/chunks.json` = `{"duration_s": float, "chunks": [{"file": "000.ogg", "start": 0.0, "end": 600.0}, ...]}`; фикстура `make_media(path: Path, seconds: float, video: bool = False) -> Path`.

- [ ] **Step 1: Добавить в `tests/conftest.py` генератор тестовых файлов**

```python
import subprocess

import imageio_ffmpeg


def make_media(path, seconds, video=False):
    """Generate a real audio (sine) or video file with ffmpeg."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-shortest", "-c:v", "mpeg4", "-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path
```

- [ ] **Step 2: Написать падающие тесты** — `tests/test_audio.py`:

```python
import json

import pytest

from tests.conftest import make_media
from voice_to_doc.audio import AudioError, READ_ERROR, parse_segment_list, prepare_audio


def test_parse_segment_list():
    assert parse_segment_list("000.ogg,0.000000,3.000000\n001.ogg,3.000000,5.7365\n") == [
        {"file": "000.ogg", "start": 0.0, "end": 3.0},
        {"file": "001.ogg", "start": 3.0, "end": 5.7365},
    ]


def test_splits_audio_into_contiguous_chunks(tmp_path):
    src = make_media(tmp_path / "in.wav", 7)
    manifest = prepare_audio(src, tmp_path / "job", chunk_seconds=3)
    chunks = manifest["chunks"]
    assert [c["file"] for c in chunks] == ["000.ogg", "001.ogg", "002.ogg"]
    assert chunks[0]["start"] == 0.0
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["start"] == pytest.approx(prev["end"])
    assert manifest["duration_s"] == pytest.approx(7, abs=0.1)
    assert all((tmp_path / "job" / "chunks" / c["file"]).stat().st_size > 0 for c in chunks)
    assert json.loads((tmp_path / "job" / "chunks.json").read_text()) == manifest


def test_accepts_video(tmp_path):
    src = make_media(tmp_path / "in.mp4", 4, video=True)
    manifest = prepare_audio(src, tmp_path / "job", chunk_seconds=600)
    assert len(manifest["chunks"]) == 1
    assert manifest["duration_s"] == pytest.approx(4, abs=0.2)


def test_rerun_removes_stale_chunks(tmp_path):
    job = tmp_path / "job"
    (job / "chunks").mkdir(parents=True)
    (job / "chunks" / "009.ogg").write_bytes(b"old")
    prepare_audio(make_media(tmp_path / "in.wav", 2), job, chunk_seconds=600)
    assert not (job / "chunks" / "009.ogg").exists()


def test_garbage_file_raises_readable_error(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_text("not audio")
    with pytest.raises(AudioError) as exc:
        prepare_audio(bad, tmp_path / "job", chunk_seconds=600)
    assert str(exc.value) == READ_ERROR
    assert exc.value.detail
    assert not (tmp_path / "job" / "chunks.json").exists()
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_audio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.audio'`

- [ ] **Step 4: Реализовать `voice_to_doc/audio.py`**

```python
"""Re-encode any audio/video into small mono chunks for speech recognition."""

import csv
import io
import json
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg

READ_ERROR = "Не удалось прочитать файл — это точно аудио или видео?"


class AudioError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def parse_segment_list(csv_text: str) -> list[dict]:
    rows = csv.reader(io.StringIO(csv_text))
    return [{"file": r[0], "start": float(r[1]), "end": float(r[2])} for r in rows if r]


def prepare_audio(src: Path, job_dir: Path, chunk_seconds: int) -> dict:
    chunks_dir = job_dir / "chunks"
    shutil.rmtree(chunks_dir, ignore_errors=True)
    chunks_dir.mkdir(parents=True)
    list_path = chunks_dir / "list.csv"
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-segment_list", str(list_path), "-segment_list_type", "csv",
        "-reset_timestamps", "1", str(chunks_dir / "%03d.ogg"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    chunks = parse_segment_list(list_path.read_text()) if list_path.exists() else []
    if proc.returncode != 0 or not chunks:
        raise AudioError(READ_ERROR, proc.stderr.strip())
    manifest = {"duration_s": chunks[-1]["end"], "chunks": chunks}
    (job_dir / "chunks.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_audio.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: audio preparation with ffmpeg chunking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Клиент Groq и офлайн-подделка

**Files:**
- Create: `voice_to_doc/groq_client.py`, `voice_to_doc/devfake.py`, `tests/test_groq_client.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - Исключения: `GroqError(Exception)`; `AuthError(GroqError)`; `Unavailable(GroqError)`; `InvalidJSON(GroqError)`; `DailyLimitExceeded(GroqError)`; `RateLimited(GroqError)` с атрибутами `retry_after: float`, `daily: bool`.
  - `is_daily_limit(message: str) -> bool`; `translate_error(e: Exception) -> Exception`.
  - `GroqClient(api_key: str, whisper_model: str, llm_model: str)` (пустой ключ → `AuthError("Не задан ключ Groq")`), методы `transcribe(path: Path, language: str) -> list[dict]` (`{"start", "end", "text", "no_speech_prob"}`) и `chat_json(system: str, user: str, max_tokens: int) -> str`.
  - `make_client(settings: Settings) -> GroqClient | FakeGroq`.
  - `FakeGroq(delay: float = 0.0, segments_per_chunk: int = 3)` с теми же методами и списком `calls: list[tuple[str, str]]`; системные промпты распознаются по первой строке `# task: lecture|interview|summary`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_groq_client.py`:

```python
import json

import groq
import httpx
import pytest

from voice_to_doc.config import Settings
from voice_to_doc.devfake import FakeGroq
from voice_to_doc.groq_client import (
    AuthError, GroqClient, GroqError, InvalidJSON, RateLimited, Unavailable,
    is_daily_limit, make_client, translate_error,
)

REQ = httpx.Request("POST", "https://api.groq.com/x")


def response(status, headers=None):
    return httpx.Response(status, headers=headers or {}, request=REQ)


def test_is_daily_limit():
    assert is_daily_limit("Rate limit reached ... on tokens per day (TPD): Limit 200000")
    assert is_daily_limit("... audio seconds per day (ASD) ...")
    assert not is_daily_limit("Rate limit reached ... on tokens per minute (TPM): Limit 8000")
    assert not is_daily_limit("... audio seconds per hour (ASH) ...")


def test_translate_rate_limit_minute_and_day():
    e = translate_error(groq.RateLimitError("tokens per minute (TPM)", response=response(429, {"retry-after": "7"}), body=None))
    assert isinstance(e, RateLimited) and e.retry_after == 7.0 and not e.daily
    e = translate_error(groq.RateLimitError("tokens per day (TPD)", response=response(429), body=None))
    assert isinstance(e, RateLimited) and e.retry_after == 30.0 and e.daily


def test_translate_auth_network_server_json_other():
    assert isinstance(translate_error(groq.AuthenticationError("bad", response=response(401), body=None)), AuthError)
    assert isinstance(translate_error(groq.APIConnectionError(request=REQ)), Unavailable)
    assert isinstance(translate_error(groq.APITimeoutError(request=REQ)), Unavailable)
    assert isinstance(translate_error(groq.InternalServerError("boom", response=response(503), body=None)), Unavailable)
    assert isinstance(translate_error(groq.BadRequestError("json_validate_failed", response=response(400), body=None)), InvalidJSON)
    other = translate_error(groq.NotFoundError("no model", response=response(404), body=None))
    assert type(other) is GroqError and "404" in str(other)


def test_empty_key_is_auth_error():
    with pytest.raises(AuthError, match="Не задан ключ Groq"):
        GroqClient("", "whisper-large-v3", "openai/gpt-oss-120b")


def test_make_client_returns_fake_when_enabled():
    client = make_client(Settings(fake_groq=True, fake_delay=0.0))
    assert isinstance(client, FakeGroq)
    assert isinstance(make_client(Settings(groq_api_key="gsk_x")), GroqClient)


def test_fake_transcribe_and_chat(tmp_path):
    fake = FakeGroq()
    segs = fake.transcribe(tmp_path / "001.ogg", "ru")
    assert len(segs) == 3 and segs[0]["start"] == 0.0 and "001" in segs[0]["text"]
    user = "Фрагмент:\n[00:00] Раз.\n[00:02] Два."
    lecture = json.loads(fake.chat_json("# task: lecture\n...", user, 100))
    assert lecture["sections"][0]["start"] == "00:00"
    assert lecture["sections"][0]["paragraphs"] == ["Раз. Два."]
    interview = json.loads(fake.chat_json("# task: interview\n...", user, 100))
    assert [u["speaker"] for u in interview["utterances"]] == ["Интервьюер", "Собеседник"]
    summary = json.loads(fake.chat_json("# task: summary\n...", user, 100))
    assert summary["title"] and summary["summary"]
    assert [c[0] for c in fake.calls] == ["transcribe", "chat", "chat", "chat"]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_groq_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.devfake'`

- [ ] **Step 3: Реализовать `voice_to_doc/groq_client.py`**

```python
"""Thin wrapper over the Groq SDK: plain-dict results and our own exception types."""

from pathlib import Path

import groq


class GroqError(Exception):
    """A Groq call failed in a way we do not retry."""


class AuthError(GroqError):
    pass


class Unavailable(GroqError):
    """Network problem or 5xx — worth retrying a few times."""


class InvalidJSON(GroqError):
    """The model's output failed Groq's JSON-mode validation."""


class DailyLimitExceeded(GroqError):
    pass


class RateLimited(GroqError):
    def __init__(self, message: str, retry_after: float, daily: bool):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


_DAILY_MARKERS = ("per day", "(TPD)", "(RPD)", "(ASD)")


def is_daily_limit(message: str) -> bool:
    return any(marker in message for marker in _DAILY_MARKERS)


def _retry_after(response) -> float:
    try:
        return float(response.headers.get("retry-after", 30))
    except (TypeError, ValueError):
        return 30.0


def translate_error(e: Exception) -> Exception:
    message = str(e)
    if isinstance(e, groq.AuthenticationError):
        return AuthError("Ключ Groq не подходит")
    if isinstance(e, groq.RateLimitError):
        return RateLimited(message, _retry_after(e.response), is_daily_limit(message))
    if isinstance(e, (groq.APIConnectionError, groq.InternalServerError)):
        return Unavailable("Нет связи с Groq")
    if isinstance(e, groq.BadRequestError) and "json_validate_failed" in message:
        return InvalidJSON(message)
    if isinstance(e, groq.APIStatusError):
        return GroqError(f"Groq вернул ошибку {e.status_code}: {message}")
    return e


class GroqClient:
    def __init__(self, api_key: str, whisper_model: str, llm_model: str):
        if not api_key:
            raise AuthError("Не задан ключ Groq")
        self._client = groq.Groq(api_key=api_key, max_retries=0, timeout=300)
        self.whisper_model = whisper_model
        self.llm_model = llm_model

    def transcribe(self, path: Path, language: str) -> list[dict]:
        try:
            with open(path, "rb") as f:
                resp = self._client.audio.transcriptions.create(
                    model=self.whisper_model,
                    file=(Path(path).name, f),
                    language=language,
                    response_format="verbose_json",
                    temperature=0,
                )
        except groq.GroqError as e:
            raise translate_error(e) from e
        return [
            {
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": s["text"],
                "no_speech_prob": float(s.get("no_speech_prob") or 0.0),
            }
            for s in resp.to_dict().get("segments") or []
        ]

    def chat_json(self, system: str, user: str, max_tokens: int) -> str:
        extra = {}
        if "gpt-oss" in self.llm_model:
            # Reasoning models: keep thinking short so the answer fits the token budget.
            extra = {"reasoning_effort": "low", "include_reasoning": False}
        try:
            resp = self._client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
                temperature=0.2,
                **extra,
            )
        except groq.GroqError as e:
            raise translate_error(e) from e
        return resp.choices[0].message.content or ""


def make_client(settings):
    if settings.fake_groq:
        from voice_to_doc.devfake import FakeGroq

        return FakeGroq(delay=settings.fake_delay)
    return GroqClient(settings.groq_api_key, settings.whisper_model, settings.llm_model)
```

- [ ] **Step 4: Реализовать `voice_to_doc/devfake.py`**

```python
"""Offline stand-in for GroqClient, used by tests and by VTD_FAKE_GROQ=1 for UI checks."""

import json
import re
import time
from pathlib import Path

_LINE = re.compile(r"^\[(\d+(?::\d+){1,2})\]\s?(.*)$")
_SPEAKERS = ("Интервьюер", "Собеседник")


class FakeGroq:
    def __init__(self, delay: float = 0.0, segments_per_chunk: int = 3):
        self.delay = delay
        self.segments_per_chunk = segments_per_chunk
        self.calls: list[tuple[str, str]] = []

    def transcribe(self, path: Path, language: str) -> list[dict]:
        self.calls.append(("transcribe", Path(path).name))
        time.sleep(self.delay)
        stem = Path(path).stem
        return [
            {"start": i * 2.0, "end": i * 2.0 + 2.0, "text": f" Фраза {i + 1} из куска {stem}.", "no_speech_prob": 0.01}
            for i in range(self.segments_per_chunk)
        ]

    def chat_json(self, system: str, user: str, max_tokens: int) -> str:
        task = system.splitlines()[0]
        self.calls.append(("chat", task))
        time.sleep(self.delay)
        lines = [m.groups() for m in map(_LINE.match, user.splitlines()) if m]
        if task == "# task: summary":
            data = {"title": "Тестовая запись", "summary": ["Первый тезис", "Второй тезис"]}
        elif task == "# task: interview":
            data = {"utterances": [
                {"speaker": _SPEAKERS[i % 2], "start": ts, "text": text} for i, (ts, text) in enumerate(lines)
            ]}
        else:
            sections = []
            if lines:
                sections = [{"title": f"Раздел с {lines[0][0]}", "start": lines[0][0],
                             "paragraphs": [" ".join(text for _, text in lines)]}]
            data = {"continues_previous": False, "sections": sections}
        return json.dumps(data, ensure_ascii=False)
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_groq_client.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: Groq client wrapper and offline fake

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Ограничитель запросов и повторы

**Files:**
- Create: `voice_to_doc/ratelimit.py`, `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: `RateLimited`, `Unavailable`, `DailyLimitExceeded` (Task 3); фикстура `clock` (Task 1).
- Produces: `RateLimiter(rpm: int, tpm: int | None = None, clock=time.monotonic, sleep=time.sleep)` с методом `acquire(tokens: int = 0) -> None`; `UNAVAILABLE_DELAYS = (2, 4, 8)`; `call_with_retries(fn, *, sleep=time.sleep, log=lambda m: None)`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_ratelimit.py`:

```python
import pytest

from voice_to_doc.groq_client import DailyLimitExceeded, RateLimited, Unavailable
from voice_to_doc.ratelimit import RateLimiter, call_with_retries


def test_requests_per_minute(clock):
    rl = RateLimiter(rpm=2, clock=clock, sleep=clock.sleep)
    rl.acquire(); rl.acquire()
    assert clock.now == 0
    rl.acquire()
    assert clock.now == pytest.approx(60)


def test_tokens_per_minute(clock):
    rl = RateLimiter(rpm=100, tpm=8000, clock=clock, sleep=clock.sleep)
    rl.acquire(5000)
    clock.now = 10
    rl.acquire(5000)  # 10000 > 8000 → wait until first expires at t=60
    assert clock.now == pytest.approx(60)


def test_request_bigger_than_tpm_is_clamped(clock):
    rl = RateLimiter(rpm=100, tpm=8000, clock=clock, sleep=clock.sleep)
    rl.acquire(20000)  # must not hang
    assert clock.now == 0


def test_daily_limit_raises(clock):
    def fn():
        raise RateLimited("per day", retry_after=10, daily=True)
    with pytest.raises(DailyLimitExceeded):
        call_with_retries(fn, sleep=clock.sleep)


def test_minute_limit_waits_and_retries(clock):
    calls = iter([RateLimited("per minute", retry_after=7, daily=False), "ok"])
    def fn():
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item
    assert call_with_retries(fn, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [7]


def test_unavailable_backoff_then_success(clock):
    attempts = []
    def fn():
        attempts.append(1)
        if len(attempts) <= 3:
            raise Unavailable("down")
        return "ok"
    assert call_with_retries(fn, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [2, 4, 8]


def test_unavailable_gives_up_after_three_retries(clock):
    def fn():
        raise Unavailable("down")
    with pytest.raises(Unavailable):
        call_with_retries(fn, sleep=clock.sleep)
    assert clock.sleeps == [2, 4, 8]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.ratelimit'`

- [ ] **Step 3: Реализовать `voice_to_doc/ratelimit.py`**

```python
"""Client-side pacing for Groq per-minute limits and the retry policy for transient errors."""

import time
from collections import deque

from voice_to_doc.groq_client import DailyLimitExceeded, RateLimited, Unavailable

WINDOW = 60.0
UNAVAILABLE_DELAYS = (2, 4, 8)


class RateLimiter:
    def __init__(self, rpm: int, tpm: int | None = None, clock=time.monotonic, sleep=time.sleep):
        self.rpm = rpm
        self.tpm = tpm
        self.clock = clock
        self.sleep = sleep
        self._events: deque[tuple[float, int]] = deque()

    def acquire(self, tokens: int = 0) -> None:
        if self.tpm:
            tokens = min(tokens, self.tpm)
        while True:
            now = self.clock()
            while self._events and now - self._events[0][0] >= WINDOW:
                self._events.popleft()
            used = sum(t for _, t in self._events)
            if len(self._events) < self.rpm and (not self.tpm or used + tokens <= self.tpm):
                self._events.append((now, tokens))
                return
            self.sleep(max(0.05, WINDOW - (now - self._events[0][0])))


def call_with_retries(fn, *, sleep=time.sleep, log=lambda message: None):
    failures = 0
    while True:
        try:
            return fn()
        except RateLimited as e:
            if e.daily:
                raise DailyLimitExceeded("Дневной лимит Groq исчерпан") from e
            log(f"Лимит Groq, жду {e.retry_after:.0f} с")
            sleep(e.retry_after)
        except Unavailable:
            if failures >= len(UNAVAILABLE_DELAYS):
                raise
            delay = UNAVAILABLE_DELAYS[failures]
            failures += 1
            log(f"Нет связи с Groq, повтор через {delay} с")
            sleep(delay)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_ratelimit.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: rate limiter and retry policy

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Распознавание кусков

**Files:**
- Create: `voice_to_doc/transcribe.py`, `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `chunks.json` (Task 2); `FakeGroq` (Task 3); `RateLimiter`, `call_with_retries` (Task 4).
- Produces: `NO_SPEECH_THRESHOLD = 0.8`; `shift_segments(segments: list[dict], offset: float) -> list[dict]` (`{"start", "end", "text"}`); `transcribe_chunks(job_dir: Path, client, limiter, language: str, *, on_progress=lambda done, total: None, sleep=time.sleep, log=lambda m: None) -> list[dict]` — пишет `transcript/NNN.json` и `transcript.json`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_transcribe.py`:

```python
import json

from voice_to_doc.devfake import FakeGroq
from voice_to_doc.ratelimit import RateLimiter
from voice_to_doc.transcribe import shift_segments, transcribe_chunks


def write_manifest(job_dir, starts):
    (job_dir / "chunks").mkdir(parents=True)
    chunks = [{"file": f"{i:03d}.ogg", "start": s, "end": s + 600} for i, s in enumerate(starts)]
    for c in chunks:
        (job_dir / "chunks" / c["file"]).write_bytes(b"x")
    (job_dir / "chunks.json").write_text(json.dumps({"duration_s": starts[-1] + 600, "chunks": chunks}))


def test_drops_empty_and_no_speech_segments():
    segs = [
        {"start": 1.0, "end": 2.0, "text": "  Привет.  ", "no_speech_prob": 0.1},
        {"start": 2.0, "end": 3.0, "text": "   ", "no_speech_prob": 0.1},
        {"start": 3.0, "end": 9.0, "text": "Продолжение следует...", "no_speech_prob": 0.95},
    ]
    assert shift_segments(segs, 600) == [{"start": 601.0, "end": 602.0, "text": "Привет."}]


def test_offsets_and_progress(tmp_path, clock):
    write_manifest(tmp_path, [0, 600])
    progress = []
    segs = transcribe_chunks(tmp_path, FakeGroq(), RateLimiter(20, clock=clock, sleep=clock.sleep), "ru",
                             on_progress=lambda d, t: progress.append((d, t)))
    assert len(segs) == 6
    assert segs[3] == {"start": 600.0, "end": 602.0, "text": "Фраза 1 из куска 001."}
    assert progress == [(1, 2), (2, 2)]
    assert json.loads((tmp_path / "transcript.json").read_text()) == segs


def test_resume_skips_done_chunks(tmp_path, clock):
    write_manifest(tmp_path, [0, 600])
    (tmp_path / "transcript").mkdir()
    (tmp_path / "transcript" / "000.json").write_text(json.dumps([{"start": 0.0, "end": 1.0, "text": "Готово."}]))
    fake = FakeGroq()
    segs = transcribe_chunks(tmp_path, fake, RateLimiter(20, clock=clock, sleep=clock.sleep), "ru")
    assert fake.calls == [("transcribe", "001.ogg")]
    assert segs[0]["text"] == "Готово." and len(segs) == 4
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.transcribe'`

- [ ] **Step 3: Реализовать `voice_to_doc/transcribe.py`**

```python
"""Speech-to-text for prepared chunks; resumable chunk by chunk."""

import json
import time
from pathlib import Path

from voice_to_doc.ratelimit import call_with_retries

# Whisper "hears" phrases in silence/music; such segments carry a high no_speech_prob.
NO_SPEECH_THRESHOLD = 0.8


def shift_segments(segments: list[dict], offset: float) -> list[dict]:
    out = []
    for s in segments:
        text = s["text"].strip()
        if text and s.get("no_speech_prob", 0.0) < NO_SPEECH_THRESHOLD:
            out.append({"start": round(s["start"] + offset, 2), "end": round(s["end"] + offset, 2), "text": text})
    return out


def transcribe_chunks(job_dir: Path, client, limiter, language: str, *,
                      on_progress=lambda done, total: None, sleep=time.sleep, log=lambda message: None) -> list[dict]:
    chunks = json.loads((job_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    out_dir = job_dir / "transcript"
    out_dir.mkdir(exist_ok=True)
    segments = []
    for i, chunk in enumerate(chunks):
        part = out_dir / f"{i:03d}.json"
        if not part.exists():
            audio = job_dir / "chunks" / chunk["file"]

            def call():
                limiter.acquire()
                return client.transcribe(audio, language)

            raw = call_with_retries(call, sleep=sleep, log=log)
            part.write_text(json.dumps(shift_segments(raw, chunk["start"]), ensure_ascii=False), encoding="utf-8")
            log(f"Распознан кусок {i + 1}/{len(chunks)}")
        segments.extend(json.loads(part.read_text(encoding="utf-8")))
        on_progress(i + 1, len(chunks))
    (job_dir / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
    return segments
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_transcribe.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: resumable chunk transcription

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Оформление — таймкоды, блоки, проверки ответа

**Files:**
- Create: `voice_to_doc/structure.py` (первая часть), `tests/test_structure_basics.py`

**Interfaces:**
- Consumes: сегменты `{"start", "end", "text"}` (Task 5).
- Produces: `fmt_ts(seconds: float, long: bool = False) -> str`; `parse_ts(value) -> float | None`; `estimate_tokens(text: str, chars_per_token: float) -> int`; `pack_blocks(segments, budget_tokens: int, chars_per_token: float) -> list[list[dict]]`; `render_lines(block, long: bool) -> str`; `snap_start(value, allowed: list[float]) -> float`; `SPEAKERS = ("Интервьюер", "Собеседник")`; `validate_lecture(data, allowed) -> dict | None` (`{"continues_previous": bool, "sections": [{"title", "start", "paragraphs"}]}`); `validate_interview(data, allowed) -> dict | None` (`{"utterances": [{"speaker": str | None, "start", "text"}]}`); `output_text(result) -> str`; `is_too_short(block, result, min_ratio=0.7, min_chars=200) -> bool`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_structure_basics.py`:

```python
import pytest

from voice_to_doc.structure import (
    estimate_tokens, fmt_ts, is_too_short, pack_blocks, parse_ts, render_lines,
    snap_start, validate_interview, validate_lecture,
)


def seg(start, text):
    return {"start": start, "end": start + 1, "text": text}


@pytest.mark.parametrize("seconds,long,expected", [
    (0, False, "00:00"), (65.9, False, "01:05"), (3599, False, "59:59"),
    (3725, True, "1:02:05"), (5, True, "0:00:05"),
])
def test_fmt_ts(seconds, long, expected):
    assert fmt_ts(seconds, long) == expected


@pytest.mark.parametrize("value,expected", [
    ("01:05", 65.0), ("1:02:05", 3725.0), (" 00:07 ", 7.0), ("7", None), ("ab:cd", None), (None, None), (12, None),
])
def test_parse_ts(value, expected):
    assert parse_ts(value) == expected


def test_estimate_tokens():
    assert estimate_tokens("a" * 25, 2.5) == 11


def test_pack_blocks_respects_budget_and_order():
    segments = [seg(i, "x" * 50) for i in range(10)]  # 21 + 4 = 25 tokens each
    blocks = pack_blocks(segments, budget_tokens=60, chars_per_token=2.5)
    assert [len(b) for b in blocks] == [2, 2, 2, 2, 2]
    assert [s for b in blocks for s in b] == segments


def test_oversized_segment_gets_own_block():
    segments = [seg(0, "a"), seg(1, "x" * 1000), seg(2, "b")]
    blocks = pack_blocks(segments, budget_tokens=60, chars_per_token=2.5)
    assert blocks == [[segments[0]], [segments[1]], [segments[2]]]


def test_render_lines():
    assert render_lines([seg(0, "Раз."), seg(3725, "Два.")], long=True) == "[0:00:00] Раз.\n[1:02:05] Два."


def test_snap_start():
    allowed = [0.0, 12.6, 30.2]
    assert snap_start("00:12", allowed) == 12.6
    assert snap_start("00:25", allowed) == 30.2
    assert snap_start("мусор", allowed) == 0.0


def test_validate_lecture_snaps_orders_and_drops_empty():
    allowed = [0.0, 10.0, 20.0]
    data = {"continues_previous": True, "sections": [
        {"title": " Введение ", "start": "00:10", "paragraphs": ["  Текст. ", ""]},
        {"title": "Пусто", "start": "00:20", "paragraphs": []},
        {"title": "", "start": "00:00", "paragraphs": ["Ещё."]},
    ]}
    assert validate_lecture(data, allowed) == {"continues_previous": True, "sections": [
        {"title": "Введение", "start": 10.0, "paragraphs": ["Текст."]},
        {"title": "Без названия", "start": 10.0, "paragraphs": ["Ещё."]},
    ]}


@pytest.mark.parametrize("data", [None, [], {"sections": "x"}, {"sections": [1]}, {"sections": []}])
def test_validate_lecture_rejects_bad_shapes(data):
    assert validate_lecture(data, [0.0]) is None


def test_validate_interview():
    data = {"utterances": [
        {"speaker": "Интервьюер", "start": "00:00", "text": "Вопрос?"},
        {"speaker": "Ведущий", "start": "00:05", "text": "Ответ."},
        {"speaker": "Собеседник", "start": "00:09", "text": " "},
    ]}
    assert validate_interview(data, [0.0, 5.0]) == {"utterances": [
        {"speaker": "Интервьюер", "start": 0.0, "text": "Вопрос?"},
        {"speaker": None, "start": 5.0, "text": "Ответ."},
    ]}
    assert validate_interview({"utterances": []}, [0.0]) is None


def test_is_too_short():
    block = [seg(0, "x" * 300)]
    short = {"sections": [{"title": "t", "start": 0.0, "paragraphs": ["x" * 100]}]}
    ok = {"utterances": [{"speaker": None, "start": 0.0, "text": "x" * 250}]}
    assert is_too_short(block, short)
    assert not is_too_short(block, ok)
    assert not is_too_short([seg(0, "Э-э, ну.")], {"utterances": [{"speaker": None, "start": 0.0, "text": "Ну."}]})
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_structure_basics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.structure'`

- [ ] **Step 3: Реализовать первую часть `voice_to_doc/structure.py`**

```python
"""Turn a raw transcript into a structured document with the help of an LLM."""

SPEAKERS = ("Интервьюер", "Собеседник")


def fmt_ts(seconds: float, long: bool = False) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if long else f"{h * 60 + m:02d}:{s:02d}"


def parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return float(total)


def estimate_tokens(text: str, chars_per_token: float) -> int:
    return int(len(text) / chars_per_token) + 1


def pack_blocks(segments: list[dict], budget_tokens: int, chars_per_token: float) -> list[list[dict]]:
    blocks, current, used = [], [], 0
    for seg in segments:
        cost = estimate_tokens(seg["text"], chars_per_token) + 4  # +4 for the "[MM:SS] " prefix
        if current and used + cost > budget_tokens:
            blocks.append(current)
            current, used = [], 0
        current.append(seg)
        used += cost
    if current:
        blocks.append(current)
    return blocks


def render_lines(block: list[dict], long: bool) -> str:
    return "\n".join(f"[{fmt_ts(s['start'], long)}] {s['text']}" for s in block)


def snap_start(value, allowed: list[float]) -> float:
    """Model timestamps are untrusted: map them onto the nearest real segment start."""
    t = parse_ts(value)
    if t is None:
        return allowed[0]
    return min(allowed, key=lambda a: abs(a - t))


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _monotonic(items: list[dict]) -> list[dict]:
    prev = 0.0
    for item in items:
        item["start"] = prev = max(item["start"], prev)
    return items


def validate_lecture(data, allowed: list[float]) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return None
    sections = []
    for s in data["sections"]:
        if not isinstance(s, dict):
            return None
        raw = s.get("paragraphs") if isinstance(s.get("paragraphs"), list) else []
        paragraphs = [p for p in map(_clean, raw) if p]
        if paragraphs:
            sections.append({"title": _clean(s.get("title")) or "Без названия",
                             "start": snap_start(s.get("start"), allowed), "paragraphs": paragraphs})
    if not sections:
        return None
    return {"continues_previous": data.get("continues_previous") is True, "sections": _monotonic(sections)}


def validate_interview(data, allowed: list[float]) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("utterances"), list):
        return None
    utterances = []
    for u in data["utterances"]:
        if not isinstance(u, dict):
            return None
        text = _clean(u.get("text"))
        if text:
            speaker = u.get("speaker") if u.get("speaker") in SPEAKERS else None
            utterances.append({"speaker": speaker, "start": snap_start(u.get("start"), allowed), "text": text})
    if not utterances:
        return None
    return {"utterances": _monotonic(utterances)}


def output_text(result: dict) -> str:
    if "sections" in result:
        return " ".join(p for s in result["sections"] for p in s["paragraphs"])
    return " ".join(u["text"] for u in result["utterances"])


def is_too_short(block: list[dict], result: dict, min_ratio: float = 0.7, min_chars: int = 200) -> bool:
    source = sum(len(s["text"]) for s in block)
    return source >= min_chars and len(output_text(result)) < min_ratio * source
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_structure_basics.py -v`
Expected: all passed (≈ 25 с параметризацией)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: timestamps, block packing and LLM output validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Оформление — запросы к LLM, склейка, выжимка

**Files:**
- Modify: `voice_to_doc/structure.py` (дописать в конец)
- Create: `tests/test_structure_llm.py`

**Interfaces:**
- Consumes: всё из Task 6; `RateLimiter`, `call_with_retries` (Task 4); `InvalidJSON` (Task 3); `transcript.json`, `chunks.json`.
- Produces: `SYSTEM_LECTURE`, `SYSTEM_INTERVIEW`, `SYSTEM_SUMMARY`, `STRICT_NOTE`; `@dataclass LLM(client, limiter, max_output_tokens: int, chars_per_token: float, sleep=time.sleep, log=lambda m: None)` с методом `ask(system: str, user: str) -> object | None`; `context_for(kind, items) -> str`; `build_user(block, context, long) -> str`; `fallback_block(block, kind) -> dict`; `structure_block(block, kind, context, llm, long) -> dict`; `merge_blocks(kind, results, long) -> tuple[list[dict], list[str]]`; `structure_transcript(job_dir: Path, kind: str, llm: LLM, block_tokens: int, *, on_progress=lambda d, t: None) -> dict` — пишет `blocks/NNN.json` и `structured.json` = `{"kind", "duration_s", "items", "warnings"}`; `summary_input(structured, long, max_chars) -> str`; `make_document(structured, llm, fallback_title: str, block_tokens: int) -> dict` = structured + `{"title", "summary"}`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_structure_llm.py`:

```python
import json

from voice_to_doc.devfake import FakeGroq
from voice_to_doc.groq_client import InvalidJSON
from voice_to_doc.ratelimit import RateLimiter
from voice_to_doc.structure import (
    LLM, STRICT_NOTE, context_for, make_document, merge_blocks, structure_block, structure_transcript,
)


class Scripted:
    """Returns queued answers; records (system, user) of every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def chat_json(self, system, user, max_tokens):
        self.prompts.append((system, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def make_llm(client, clock):
    return LLM(client, RateLimiter(30, 8000, clock=clock, sleep=clock.sleep), 4000, 2.5, sleep=clock.sleep)


BLOCK = [{"start": 0.0, "end": 5.0, "text": "Ну, э-э, сегодня про сети."}, {"start": 5.0, "end": 9.0, "text": "Начнём с истории."}]
GOOD = {"continues_previous": False, "sections": [{"title": "Сети", "start": "00:00", "paragraphs": ["Сегодня про сети. Начнём с истории."]}]}


def test_structure_block_success_and_prompt(clock):
    client = Scripted(GOOD)
    result = structure_block(BLOCK, "lecture", "Последний раздел: «X»", make_llm(client, clock), long=False)
    assert result["sections"][0]["title"] == "Сети" and "fallback" not in result
    system, user = client.prompts[0]
    assert system.startswith("# task: lecture")
    assert "Последний раздел: «X»" in user and "[00:05] Начнём с истории." in user


def test_invalid_then_strict_retry(clock):
    client = Scripted("не json", GOOD)
    result = structure_block(BLOCK, "lecture", "", make_llm(client, clock), long=False)
    assert "fallback" not in result
    assert client.prompts[1][0].endswith(STRICT_NOTE)


def test_two_failures_give_fallback(clock):
    client = Scripted(InvalidJSON("bad"), {"sections": []})
    result = structure_block(BLOCK, "interview", "", make_llm(client, clock), long=False)
    assert result == {"fallback": True, "utterances": [
        {"speaker": None, "start": 0.0, "text": "Ну, э-э, сегодня про сети. Начнём с истории.", "raw": True}]}


def test_too_short_answer_is_retried(clock):
    long_block = [{"start": 0.0, "end": 60.0, "text": "слово " * 100}]
    short = {"utterances": [{"speaker": "Собеседник", "start": "00:00", "text": "Кратко."}]}
    full = {"utterances": [{"speaker": "Собеседник", "start": "00:00", "text": "слово " * 90}]}
    client = Scripted(short, full)
    result = structure_block(long_block, "interview", "", make_llm(client, clock), long=False)
    assert len(client.prompts) == 2 and result["utterances"][0]["text"].startswith("слово")


def test_merge_continues_previous_and_warnings():
    a = {"continues_previous": False, "sections": [{"title": "A", "start": 0.0, "paragraphs": ["p1"]}]}
    b = {"continues_previous": True, "sections": [{"title": "A2", "start": 50.0, "paragraphs": ["p2"]},
                                                  {"title": "B", "start": 70.0, "paragraphs": ["p3"]}]}
    c = {"fallback": True, "continues_previous": False,
         "sections": [{"title": "Не оформлено", "start": 90.0, "paragraphs": ["raw"], "raw": True}]}
    items, warnings = merge_blocks("lecture", [a, b, c], long=False)
    assert [(i["title"], i["paragraphs"]) for i in items] == [("A", ["p1", "p2"]), ("B", ["p3"]), ("Не оформлено", ["raw"])]
    assert warnings == ["Фрагмент с 01:30 не оформлен — показан исходный текст распознавания."]


def test_context_for():
    assert context_for("lecture", []) == ""
    assert context_for("lecture", [{"title": "Итоги"}]) == "Последний раздел: «Итоги»"
    items = [{"speaker": "Интервьюер", "text": "a"}, {"speaker": None, "text": "b"}, {"speaker": "Собеседник", "text": "c"}]
    assert context_for("interview", items) == "Неизвестно: b\nСобеседник: c"


def write_job(job_dir, segments, duration=120.0):
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False))
    (job_dir / "chunks.json").write_text(json.dumps({"duration_s": duration, "chunks": []}))


def test_structure_transcript_resumes_and_passes_context(tmp_path, clock):
    segments = [{"start": float(i * 10), "end": float(i * 10 + 9), "text": f"Фраза номер {i}. " * 10} for i in range(4)]
    write_job(tmp_path, segments)
    fake = FakeGroq()
    progress = []
    structured = structure_transcript(tmp_path, "lecture", make_llm(fake, clock), block_tokens=100,
                                      on_progress=lambda d, t: progress.append((d, t)))
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert structured["kind"] == "lecture" and len(structured["items"]) == 4
    assert json.loads((tmp_path / "structured.json").read_text()) == structured
    (tmp_path / "blocks" / "003.json").unlink()
    again = FakeGroq()
    structure_transcript(tmp_path, "lecture", make_llm(again, clock), block_tokens=100)
    assert len(again.calls) == 1


def test_empty_transcript_gives_warning(tmp_path, clock):
    write_job(tmp_path, [])
    structured = structure_transcript(tmp_path, "interview", make_llm(FakeGroq(), clock), block_tokens=2500)
    assert structured["items"] == [] and structured["warnings"] == ["В записи не найдено речи."]
    doc = make_document(structured, make_llm(FakeGroq(), clock), "файл", 2500)
    assert doc["title"] == "файл" and doc["summary"] == []


def test_make_document_uses_summary_and_falls_back(clock):
    structured = {"kind": "lecture", "duration_s": 60.0, "warnings": [],
                  "items": [{"title": "A", "start": 0.0, "paragraphs": ["Текст."]}]}
    doc = make_document(structured, make_llm(FakeGroq(), clock), "файл", 2500)
    assert doc["title"] == "Тестовая запись" and doc["summary"] == ["Первый тезис", "Второй тезис"]
    bad = make_document(structured, make_llm(Scripted({"title": 5}), clock), "файл", 2500)
    assert bad["title"] == "файл" and bad["warnings"] == ["Не удалось составить выжимку."]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_structure_llm.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLM' from 'voice_to_doc.structure'`

- [ ] **Step 3: Дописать в начало `voice_to_doc/structure.py` импорты**

Заменить первую строку-докстринг блоком:
```python
"""Turn a raw transcript into a structured document with the help of an LLM."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from voice_to_doc.groq_client import InvalidJSON
from voice_to_doc.ratelimit import RateLimiter, call_with_retries
```

- [ ] **Step 4: Дописать в конец `voice_to_doc/structure.py`**

```python
SYSTEM_LECTURE = """# task: lecture
Ты редактор расшифровок лекций на русском языке. На входе — фрагмент автоматической расшифровки: строки вида [таймкод] текст.
Сделай из него читаемый текст.
- НЕ пересказывай и НЕ сокращай по смыслу. Сохрани все мысли, примеры, числа и формулировки лектора.
- Можно только: убрать слова-паразиты («э-э», «ну», «как бы», «вот»), повторы и оговорки; расставить знаки препинания; исправить явные ошибки распознавания; разбить текст на абзацы.
- Раздели фрагмент на тематические разделы с короткими заголовками (2–6 слов).
- start раздела — таймкод первой строки раздела, в точности как во входе.
- continues_previous = true, если первый раздел продолжает тему предыдущего фрагмента (его заголовок дан в контексте); иначе false.
Ответь только JSON:
{"continues_previous": bool, "sections": [{"title": str, "start": str, "paragraphs": [str]}]}"""

SYSTEM_INTERVIEW = """# task: interview
Ты редактор расшифровок интервью на русском языке. На входе — фрагмент автоматической расшифровки: строки вида [таймкод] текст. Говорят двое: «Интервьюер» задаёт вопросы, «Собеседник» отвечает.
- Раздели текст на реплики; новая реплика — при смене говорящего. Определи говорящего по смыслу; учти последние реплики из контекста.
- НЕ пересказывай и НЕ сокращай по смыслу. Можно только убрать слова-паразиты, повторы и оговорки, расставить знаки препинания, исправить явные ошибки распознавания.
- start реплики — таймкод её первой строки, в точности как во входе.
Ответь только JSON:
{"utterances": [{"speaker": "Интервьюер" | "Собеседник", "start": str, "text": str}]}"""

SYSTEM_SUMMARY = """# task: summary
На входе — содержание документа: строки с таймкодом, заголовком раздела или ролью говорящего и началом текста.
Придумай короткое название документа (до 8 слов) и выдели 3–7 главных мыслей.
Опирайся только на входной текст, ничего не добавляй от себя.
Ответь только JSON:
{"title": str, "summary": [str]}"""

STRICT_NOTE = (
    "\n\nВАЖНО: прошлый ответ был неполным или неверным. Верни ВЕСЬ текст фрагмента в указанном JSON, "
    "удалив только слова-паразиты и повторы."
)


@dataclass
class LLM:
    client: object
    limiter: RateLimiter
    max_output_tokens: int
    chars_per_token: float
    sleep: Callable = time.sleep
    log: Callable = field(default=lambda message: None)

    def ask(self, system: str, user: str):
        estimate = estimate_tokens(system + user, self.chars_per_token) + self.max_output_tokens

        def call():
            self.limiter.acquire(estimate)
            return self.client.chat_json(system, user, self.max_output_tokens)

        try:
            return json.loads(call_with_retries(call, sleep=self.sleep, log=self.log))
        except (InvalidJSON, json.JSONDecodeError) as e:
            self.log(f"Модель вернула не-JSON: {str(e)[:200]}")
            return None


def context_for(kind: str, items: list[dict]) -> str:
    if not items:
        return ""
    if kind == "lecture":
        return f"Последний раздел: «{items[-1]['title']}»"
    return "\n".join(f"{u['speaker'] or 'Неизвестно'}: {u['text'][:300]}" for u in items[-2:])


def build_user(block: list[dict], context: str, long: bool) -> str:
    parts = [f"Контекст предыдущего фрагмента:\n{context}"] if context else []
    parts.append(f"Фрагмент:\n{render_lines(block, long)}")
    return "\n\n".join(parts)


def fallback_block(block: list[dict], kind: str) -> dict:
    text = " ".join(s["text"] for s in block)
    start = block[0]["start"]
    if kind == "lecture":
        return {"fallback": True, "continues_previous": False,
                "sections": [{"title": "Не оформлено", "start": start, "paragraphs": [text], "raw": True}]}
    return {"fallback": True, "utterances": [{"speaker": None, "start": start, "text": text, "raw": True}]}


def structure_block(block: list[dict], kind: str, context: str, llm: LLM, long: bool) -> dict:
    allowed = [s["start"] for s in block]
    system = SYSTEM_LECTURE if kind == "lecture" else SYSTEM_INTERVIEW
    validate = validate_lecture if kind == "lecture" else validate_interview
    user = build_user(block, context, long)
    for attempt in range(2):
        result = validate(llm.ask(system + (STRICT_NOTE if attempt else ""), user), allowed)
        if result and not is_too_short(block, result):
            return result
        llm.log(f"Блок с {fmt_ts(block[0]['start'], long)}: ответ не прошёл проверку (попытка {attempt + 1})")
    return fallback_block(block, kind)


def merge_blocks(kind: str, results: list[dict], long: bool) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    for r in results:
        parts = [dict(x) for x in (r["sections"] if kind == "lecture" else r["utterances"])]
        if r.get("fallback"):
            warnings.append(f"Фрагмент с {fmt_ts(parts[0]['start'], long)} не оформлен — показан исходный текст распознавания.")
        if kind == "lecture" and r.get("continues_previous") and items and not items[-1].get("raw"):
            items[-1] = {**items[-1], "paragraphs": items[-1]["paragraphs"] + parts[0]["paragraphs"]}
            parts = parts[1:]
        items.extend(parts)
    return items, warnings


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def structure_transcript(job_dir: Path, kind: str, llm: LLM, block_tokens: int, *,
                         on_progress=lambda done, total: None) -> dict:
    segments = _read_json(job_dir / "transcript.json")
    duration = _read_json(job_dir / "chunks.json")["duration_s"]
    long = duration >= 3600
    blocks = pack_blocks(segments, block_tokens, llm.chars_per_token)
    out_dir = job_dir / "blocks"
    out_dir.mkdir(exist_ok=True)
    results = []
    for i, block in enumerate(blocks):
        path = out_dir / f"{i:03d}.json"
        if path.exists():
            result = _read_json(path)
        else:
            previous, _ = merge_blocks(kind, results[-1:], long)
            result = structure_block(block, kind, context_for(kind, previous), llm, long)
            _write_json(path, result)
        results.append(result)
        on_progress(i + 1, len(blocks))
    items, warnings = merge_blocks(kind, results, long)
    if not segments:
        warnings.append("В записи не найдено речи.")
    structured = {"kind": kind, "duration_s": duration, "items": items, "warnings": warnings}
    _write_json(job_dir / "structured.json", structured)
    return structured


def summary_input(structured: dict, long: bool, max_chars: int) -> str:
    lines = []
    for it in structured["items"]:
        ts = fmt_ts(it["start"], long)
        if structured["kind"] == "lecture":
            lines.append(f"[{ts}] {it['title']}: {' '.join(it['paragraphs'])[:300]}")
        else:
            lines.append(f"[{ts}] {it['speaker'] or 'Неизвестно'}: {it['text'][:200]}")
    step = 1
    while step < len(lines) and len("\n".join(lines[::step])) > max_chars:
        step += 1
    return "\n".join(lines[::step])


def make_document(structured: dict, llm: LLM, fallback_title: str, block_tokens: int) -> dict:
    doc = {**structured, "title": fallback_title, "summary": [], "warnings": list(structured["warnings"])}
    if not structured["items"]:
        return doc
    long = structured["duration_s"] >= 3600
    data = llm.ask(SYSTEM_SUMMARY, summary_input(structured, long, int(block_tokens * llm.chars_per_token)))
    data = data if isinstance(data, dict) else {}
    title, summary = data.get("title"), data.get("summary")
    if isinstance(title, str) and title.strip():
        doc["title"] = title.strip()
    if isinstance(summary, list):
        doc["summary"] = [s.strip() for s in summary if isinstance(s, str) and s.strip()][:7]
    if not doc["summary"]:
        doc["warnings"].append("Не удалось составить выжимку.")
    return doc
```

- [ ] **Step 5: Убедиться, что тесты проходят (и старые тоже)**

Run: `.venv/bin/pytest tests/test_structure_llm.py tests/test_structure_basics.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: LLM structuring with retries, fallback, merge and summary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Markdown и Word

**Files:**
- Create: `voice_to_doc/render.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: документ из `make_document` (Task 7): `{"title", "summary", "kind", "duration_s", "items", "warnings"}`; `fmt_ts` (Task 6).
- Produces: `to_markdown(doc: dict) -> str`; `to_docx(doc: dict, path: Path) -> None`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_render.py`:

```python
from docx import Document

from voice_to_doc.render import to_docx, to_markdown

LECTURE = {"title": "История сети", "summary": ["Тезис"], "kind": "lecture", "duration_s": 600.0, "warnings": [],
           "items": [{"title": "Введение", "start": 0.0, "paragraphs": ["Абзац 1.", "Абзац 2."]},
                     {"title": "ARPANET", "start": 452.0, "paragraphs": ["Текст."]}]}
INTERVIEW = {"title": "Беседа", "summary": [], "kind": "interview", "duration_s": 4000.0,
             "warnings": ["Фрагмент с 1:00:00 не оформлен — показан исходный текст распознавания."],
             "items": [{"speaker": "Интервьюер", "start": 190.0, "text": "Вопрос?"},
                       {"speaker": None, "start": 3600.0, "text": "сырой текст", "raw": True}]}


def test_lecture_markdown():
    assert to_markdown(LECTURE) == (
        "# История сети\n\n## Кратко\n- Тезис\n\n"
        "## [00:00] Введение\n\nАбзац 1.\n\nАбзац 2.\n\n"
        "## [07:32] ARPANET\n\nТекст.\n"
    )


def test_interview_markdown_long_timestamps_and_warnings():
    assert to_markdown(INTERVIEW) == (
        "# Беседа\n\n"
        "**Интервьюер [0:03:10]:** Вопрос?\n\n"
        "**Не определено [1:00:00]:** сырой текст\n\n"
        "---\n\n**Замечания:**\n- Фрагмент с 1:00:00 не оформлен — показан исходный текст распознавания.\n"
    )


def test_docx_structure(tmp_path):
    path = tmp_path / "r.docx"
    to_docx(LECTURE, path)
    paras = [(p.style.name, p.text) for p in Document(path).paragraphs]
    assert paras == [("Heading 1", "История сети"), ("Heading 2", "Кратко"), ("List Bullet", "Тезис"),
                     ("Heading 2", "[00:00] Введение"), ("Normal", "Абзац 1."), ("Normal", "Абзац 2."),
                     ("Heading 2", "[07:32] ARPANET"), ("Normal", "Текст.")]


def test_docx_interview_bold_speaker(tmp_path):
    path = tmp_path / "r.docx"
    to_docx(INTERVIEW, path)
    paras = Document(path).paragraphs
    first = paras[1]
    assert first.text == "Интервьюер [0:03:10]: Вопрос?"
    assert first.runs[0].bold and not first.runs[1].bold
    assert paras[-1].style.name == "List Bullet" and "не оформлен" in paras[-1].text
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.render'`

- [ ] **Step 3: Реализовать `voice_to_doc/render.py`**

```python
"""Render a structured document to Markdown and Word."""

from pathlib import Path

from docx import Document

from voice_to_doc.structure import fmt_ts

UNKNOWN_SPEAKER = "Не определено"


def _ts(doc: dict, seconds: float) -> str:
    return fmt_ts(seconds, doc["duration_s"] >= 3600)


def to_markdown(doc: dict) -> str:
    out = [f"# {doc['title']}", ""]
    if doc["summary"]:
        out += ["## Кратко", *[f"- {s}" for s in doc["summary"]], ""]
    for it in doc["items"]:
        if doc["kind"] == "lecture":
            out += [f"## [{_ts(doc, it['start'])}] {it['title']}", ""]
            for p in it["paragraphs"]:
                out += [p, ""]
        else:
            out += [f"**{it['speaker'] or UNKNOWN_SPEAKER} [{_ts(doc, it['start'])}]:** {it['text']}", ""]
    if doc["warnings"]:
        out += ["---", "", "**Замечания:**", *[f"- {w}" for w in doc["warnings"]], ""]
    return "\n".join(out)


def to_docx(doc: dict, path: Path) -> None:
    d = Document()
    d.add_heading(doc["title"], level=1)
    if doc["summary"]:
        d.add_heading("Кратко", level=2)
        for s in doc["summary"]:
            d.add_paragraph(s, style="List Bullet")
    for it in doc["items"]:
        if doc["kind"] == "lecture":
            d.add_heading(f"[{_ts(doc, it['start'])}] {it['title']}", level=2)
            for p in it["paragraphs"]:
                d.add_paragraph(p)
        else:
            para = d.add_paragraph()
            para.add_run(f"{it['speaker'] or UNKNOWN_SPEAKER} [{_ts(doc, it['start'])}]: ").bold = True
            para.add_run(it["text"])
    if doc["warnings"]:
        d.add_heading("Замечания", level=2)
        for w in doc["warnings"]:
            d.add_paragraph(w, style="List Bullet")
    d.save(path)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_render.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: markdown and docx rendering

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Задания — хранилище, конвейер, очередь

**Files:**
- Create: `voice_to_doc/jobs.py`, `tests/test_jobs.py`

**Interfaces:**
- Consumes: `Settings`, `load_settings` (1); `prepare_audio`, `AudioError` (2); `make_client`, `AuthError`, `DailyLimitExceeded`, `Unavailable`, `GroqError` (3); `RateLimiter` (4); `transcribe_chunks` (5); `LLM`, `structure_transcript`, `make_document` (7); `to_markdown`, `to_docx` (8); `make_media`, `clock` (conftest).
- Produces:
  - `KINDS = ("lecture", "interview")`, `STEPS = ("prepare", "transcribe", "structure", "summary", "render")`.
  - `JobStore(data_dir: Path)`: `create(filename: str, kind: str) -> dict`, `dir(job_id) -> Path` (KeyError для неверного id), `get(job_id) -> dict` (KeyError), `update(job_id, **fields) -> dict`, `list() -> list[dict]` (новые сверху), `log(job_id, message) -> None`, `delete(job_id) -> None`, `recover_interrupted() -> list[str]` (id заданий в статусе `queued`).
  - Задание: `{"id", "filename", "source", "kind", "created_at", "duration_s", "status", "step", "progress": {"done", "total"}, "step_started_at", "message", "reason"}`; `status ∈ queued|running|done|paused|error`; `reason ∈ None|file|auth|daily|network|groq|internal|restart`.
  - `Pipeline(store, settings_loader=load_settings, client_factory=make_client, sleep=time.sleep, clock=time.monotonic)`: `run(job_id) -> None` (не бросает исключений), `restructure(job_id, kind) -> None`.
  - `Worker(pipeline, inline: bool = False)`: `start()`, `submit(job_id)`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_jobs.py`:

```python
import json
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
    assert (job["status"], job["reason"]) == ("error", "internal") and "boom" in job["message"]
    assert "Traceback" in (store.dir(job["id"]) / "log.txt").read_text()


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
    store.update(done["id"], status="done")
    assert store.recover_interrupted() == [queued["id"]]
    r = store.get(running["id"])
    assert (r["status"], r["reason"]) == ("paused", "restart")
    assert store.get(done["id"])["status"] == "done"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.jobs'`

- [ ] **Step 3: Реализовать `voice_to_doc/jobs.py`**

```python
"""Jobs: on-disk state, the processing pipeline and a single background worker."""

import json
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
from voice_to_doc.groq_client import AuthError, DailyLimitExceeded, GroqError, Unavailable, make_client
from voice_to_doc.ratelimit import RateLimiter
from voice_to_doc.render import to_docx, to_markdown
from voice_to_doc.structure import LLM, make_document, structure_transcript
from voice_to_doc.transcribe import transcribe_chunks

KINDS = ("lecture", "interview")
STEPS = ("prepare", "transcribe", "structure", "summary", "render")
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_EXT = re.compile(r"^\.[a-z0-9]{1,5}$")


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _ID.match(job_id):
            raise KeyError(job_id)
        return self.data_dir / job_id

    def create(self, filename: str, kind: str) -> dict:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        ext = Path(filename).suffix.lower()
        job = {
            "id": job_id, "filename": filename, "source": "source" + (ext if _EXT.match(ext) else ""),
            "kind": kind, "created_at": datetime.now().isoformat(timespec="seconds"), "duration_s": None,
            "status": "queued", "step": None, "progress": {"done": 0, "total": 0},
            "step_started_at": None, "message": "", "reason": None,
        }
        self.dir(job_id).mkdir(parents=True)
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

    def list(self) -> list[dict]:
        jobs = [json.loads(p.read_text(encoding="utf-8")) for p in self.data_dir.glob("*/job.json")]
        return sorted(jobs, key=lambda j: (j["created_at"], j["id"]), reverse=True)

    def log(self, job_id: str, message: str) -> None:
        with open(self.dir(job_id) / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def recover_interrupted(self) -> list[str]:
        queued = []
        for job in reversed(self.list()):
            if job["status"] == "running":
                self.update(job["id"], status="paused", reason="restart", message="Работа прервана перезапуском программы")
            elif job["status"] == "queued":
                queued.append(job["id"])
        return queued

    def _write(self, job: dict) -> None:
        path = self.dir(job["id"]) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


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
        log = lambda message: self.store.log(job_id, message)  # noqa: E731
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
        except Exception as e:  # noqa: BLE001 — the worker must survive anything
            log(traceback.format_exc())
            self._stop(job_id, "error", "internal", f"Внутренняя ошибка: {e}")
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
        return lambda done, total: self.store.update(job_id, progress={"done": done, "total": total})

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
            (d / "document.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        self._step(job_id, "render")
        (d / "result.md").write_text(to_markdown(doc), encoding="utf-8")
        to_docx(doc, d / "result.docx")


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
            self.pipeline.run(self._queue.get())
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_jobs.py -v`
Expected: 11 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv/bin/pytest -q`
Expected: все тесты зелёные

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: job store, resumable pipeline and worker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: HTTP API

**Files:**
- Create: `voice_to_doc/app.py`, `voice_to_doc/static/index.html` (временная заглушка `<!doctype html><title>voice-to-doc</title>`; заменяется в Task 11), `tests/test_app.py`

**Interfaces:**
- Consumes: `Settings`, `load_settings`, `save_api_key` (1); `make_client` (3); `FakeGroq` (3); `JobStore`, `Pipeline`, `Worker`, `KINDS` (9).
- Produces: `create_app(settings_loader=load_settings, client_factory=make_client, inline: bool = False) -> FastAPI`; маршруты из спецификации, раздел 4; `MAX_UPLOAD = 2 * 1024**3`. Запуск: `uvicorn --factory voice_to_doc.app:create_app`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_app.py`:

```python
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_media
from voice_to_doc.app import create_app
from voice_to_doc.config import load_settings
from voice_to_doc.devfake import FakeGroq


@pytest.fixture
def env(tmp_path):
    return tmp_path / ".env"


@pytest.fixture
def fake():
    return FakeGroq()


@pytest.fixture
def client(tmp_path, env, fake):
    loader = lambda: replace(load_settings(env), data_dir=tmp_path / "data", chunk_seconds=3)  # noqa: E731
    return TestClient(create_app(settings_loader=loader, client_factory=lambda s: fake, inline=True))


def upload(client, path, kind="lecture"):
    with open(path, "rb") as f:
        return client.post("/api/jobs", files={"file": (path.name, f, "audio/wav")}, data={"kind": kind})


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_settings_key_roundtrip(client, env):
    assert client.get("/api/settings").json() == {"has_key": False}
    assert client.post("/api/settings", json={"groq_api_key": "a b"}).status_code == 400
    assert client.post("/api/settings", json={"groq_api_key": "gsk_123"}).json() == {"has_key": True}
    assert client.get("/api/settings").json() == {"has_key": True}
    assert "GROQ_API_KEY=gsk_123" in env.read_text()


def test_upload_process_and_download(client, tmp_path):
    r = upload(client, make_media(tmp_path / "Лекция.wav", 4))
    assert r.status_code == 200
    job_id = r.json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["filename"] == "Лекция.wav"
    assert [j["id"] for j in client.get("/api/jobs").json()] == [job_id]
    md = client.get(f"/api/jobs/{job_id}/result.md")
    assert md.status_code == 200 and md.text.startswith("# Тестовая запись")
    assert "Лекция.md" in md.headers["content-disposition"] or "%D0%9B" in md.headers["content-disposition"]
    docx = client.get(f"/api/jobs/{job_id}/result.docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"


def test_bad_kind_and_unknown_job(client, tmp_path):
    assert upload(client, make_media(tmp_path / "a.wav", 1), kind="podcast").status_code == 400
    assert client.get("/api/jobs/20261012-000000-abcdef").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404


def test_bad_file_error_then_retry_allowed(client, tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_text("nope")
    job_id = upload(client, bad).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "error" and "Не удалось прочитать файл" in job["message"]
    assert client.get(f"/api/jobs/{job_id}/result.md").status_code == 404
    assert client.post(f"/api/jobs/{job_id}/resume").json()["status"] == "error"


def test_resume_rejected_for_done_job(client, tmp_path):
    job_id = upload(client, make_media(tmp_path / "a.wav", 1)).json()["id"]
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 409


def test_restructure_to_interview(client, tmp_path, fake):
    job_id = upload(client, make_media(tmp_path / "a.wav", 4)).json()["id"]
    transcribed = sum(1 for c in fake.calls if c[0] == "transcribe")
    r = client.post(f"/api/jobs/{job_id}/restructure", json={"kind": "interview"})
    assert r.status_code == 200 and r.json()["kind"] == "interview"
    assert "**Интервьюер [00:00]:**" in client.get(f"/api/jobs/{job_id}/result.md").text
    assert sum(1 for c in fake.calls if c[0] == "transcribe") == transcribed
    assert client.post(f"/api/jobs/{job_id}/restructure", json={"kind": "x"}).status_code == 400
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_to_doc.app'`

- [ ] **Step 3: Создать заглушку страницы и реализовать `voice_to_doc/app.py`**

```bash
mkdir -p voice_to_doc/static && echo '<!doctype html><title>voice-to-doc</title>' > voice_to_doc/static/index.html
```

```python
"""HTTP API and static page. Run: uvicorn --factory voice_to_doc.app:create_app"""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from voice_to_doc.config import load_settings, save_api_key
from voice_to_doc.groq_client import make_client
from voice_to_doc.jobs import KINDS, JobStore, Pipeline, Worker

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 2 * 1024**3
MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class KeyIn(BaseModel):
    groq_api_key: str


class KindIn(BaseModel):
    kind: str


def create_app(settings_loader=load_settings, client_factory=make_client, inline: bool = False) -> FastAPI:
    store = JobStore(settings_loader().data_dir)
    pipeline = Pipeline(store, settings_loader, client_factory)
    worker = Worker(pipeline, inline=inline)
    queued = store.recover_interrupted()
    if not inline:
        worker.start()
    for job_id in queued:
        worker.submit(job_id)

    app = FastAPI(title="voice-to-doc")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def job_or_404(job_id: str) -> dict:
        try:
            return store.get(job_id)
        except KeyError:
            raise HTTPException(404, "Запись не найдена") from None

    def check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise HTTPException(400, "Тип записи должен быть «лекция» или «интервью»")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/settings")
    def get_settings():
        s = settings_loader()
        return {"has_key": bool(s.groq_api_key) or s.fake_groq}

    @app.post("/api/settings")
    def set_settings(body: KeyIn):
        try:
            save_api_key(body.groq_api_key, settings_loader().env_path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"has_key": True}

    @app.post("/api/jobs")
    async def create_job(file: UploadFile = File(...), kind: str = Form(...)):
        check_kind(kind)
        job = store.create(file.filename or "запись", kind)
        size = 0
        with open(store.dir(job["id"]) / job["source"], "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    break
                out.write(chunk)
        if size > MAX_UPLOAD:
            store.delete(job["id"])
            raise HTTPException(413, "Файл больше 2 ГБ")
        worker.submit(job["id"])
        return {"id": job["id"]}

    @app.get("/api/jobs")
    def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id)

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        if job_or_404(job_id)["status"] not in ("paused", "error"):
            raise HTTPException(409, "Запись уже обрабатывается или готова")
        store.update(job_id, status="queued", message="", reason=None)
        worker.submit(job_id)
        return store.get(job_id)

    @app.post("/api/jobs/{job_id}/restructure")
    def restructure(job_id: str, body: KindIn):
        job = job_or_404(job_id)
        check_kind(body.kind)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "Запись ещё обрабатывается")
        if not (store.dir(job_id) / "transcript.json").exists():
            raise HTTPException(409, "Сначала нужна расшифровка")
        pipeline.restructure(job_id, body.kind)
        worker.submit(job_id)
        return store.get(job_id)

    def result(job_id: str, fmt: str):
        job = job_or_404(job_id)
        path = store.dir(job_id) / f"result.{fmt}"
        if job["status"] != "done" or not path.exists():
            raise HTTPException(404, "Документ ещё не готов")
        return FileResponse(path, media_type=MEDIA_TYPES[fmt], filename=f"{Path(job['filename']).stem}.{fmt}")

    @app.get("/api/jobs/{job_id}/result.md")
    def result_md(job_id: str):
        return result(job_id, "md")

    @app.get("/api/jobs/{job_id}/result.docx")
    def result_docx(job_id: str):
        return result(job_id, "docx")

    return app
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: HTTP API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Веб-страница

**Files:**
- Modify: `voice_to_doc/static/index.html` (заменить заглушку целиком)
- Create: `voice_to_doc/static/style.css`, `voice_to_doc/static/app.js`, `run.sh`, `scripts/ui_check.py`

**Interfaces:**
- Consumes: API из Task 10; поля задания из Task 9 (`status`, `step`, `progress`, `step_started_at`, `message`, `reason`, `kind`, `duration_s`, `filename`, `created_at`).
- Produces: страница с маршрутами `#/` и `#/job/<id>`; `run.sh`; `scripts/ui_check.py` (скриншоты в `screenshots/`).

- [ ] **Step 1: `voice_to_doc/static/index.html`**

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Расшифровка записей</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js" defer></script>
  <script src="/static/app.js" defer></script>
</head>
<body>
<main class="page">
  <section id="home" hidden>
    <h1>Расшифровка записей</h1>

    <div id="setup" class="panel" hidden>
      <h2>Нужен ключ Groq</h2>
      <ol class="steps-help">
        <li>Зарегистрируйтесь на <a href="https://console.groq.com" target="_blank" rel="noopener">console.groq.com</a> — это бесплатно.</li>
        <li>Откройте раздел «API Keys» и создайте ключ.</li>
        <li>Вставьте ключ сюда — он сохранится только на этом компьютере.</li>
      </ol>
      <form id="key-form" class="row">
        <input id="key-input" type="password" placeholder="gsk_…" autocomplete="off" required>
        <button type="submit">Сохранить</button>
      </form>
      <p id="key-error" class="error" hidden></p>
    </div>

    <form id="upload-form" hidden>
      <label id="drop" class="drop">
        <span id="drop-text" class="drop-text">Перетащите файл сюда или <u>выберите</u></span>
        <span class="muted">mp3, m4a, ogg, wav, mp4 и другие аудио и видео</span>
        <input id="file-input" type="file" accept="audio/*,video/*" class="visually-hidden">
      </label>
      <div class="row between">
        <fieldset class="kind">
          <legend>Тип записи</legend>
          <label><input type="radio" name="kind" value="lecture" checked> Лекция</label>
          <label><input type="radio" name="kind" value="interview"> Интервью</label>
        </fieldset>
        <button id="upload-btn" type="submit" disabled>Расшифровать</button>
      </div>
      <p id="upload-error" class="error" hidden></p>
    </form>

    <h2 class="list-title">Недавние</h2>
    <ul id="jobs" class="jobs"></ul>
    <p id="jobs-empty" class="muted">Пока ничего не загружено.</p>
  </section>

  <section id="job" hidden>
    <a href="#/" class="back">← Назад</a>
    <header class="job-head">
      <h1 id="job-name"></h1>
      <p id="job-meta" class="muted"></p>
    </header>

    <div id="progress">
      <ol id="steps" class="steps"></ol>
      <div class="bar" role="progressbar" aria-label="Общий прогресс"><div id="bar-fill"></div></div>
    </div>

    <div id="job-alert" class="alert" hidden>
      <p id="job-message"></p>
      <form id="job-key-form" class="row" hidden>
        <input id="job-key-input" type="password" placeholder="Новый ключ gsk_…" autocomplete="off" required>
        <button type="submit">Сохранить и продолжить</button>
      </form>
      <p id="job-key-error" class="error" hidden></p>
      <button id="resume-btn" type="button">Продолжить</button>
    </div>

    <div id="result" hidden>
      <div class="actions">
        <a id="dl-docx" class="button" download>Скачать .docx</a>
        <a id="dl-md" class="button secondary" download>Скачать .md</a>
        <button id="copy-btn" type="button" class="secondary">Копировать</button>
        <span class="spacer"></span>
        <select id="re-kind" aria-label="Тип записи">
          <option value="lecture">Лекция</option>
          <option value="interview">Интервью</option>
        </select>
        <button id="re-btn" type="button" class="secondary">Переоформить</button>
      </div>
      <article id="doc" class="doc"></article>
    </div>
  </section>
</main>
</body>
</html>
```

- [ ] **Step 2: `voice_to_doc/static/style.css`**

```css
:root {
  --bg: #f7f7f5;
  --surface: #ffffff;
  --text: #1d1d1b;
  --muted: #6b6b66;
  --border: #e2e2dd;
  --accent: #1f6f5c;
  --accent-text: #ffffff;
  --accent-soft: #e3f0ec;
  --danger: #a33a2c;
  --warn-bg: #fbf3e4;
  --warn-border: #e8cf9e;
  --radius: 8px;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615;
    --surface: #1f1f1d;
    --text: #ecece8;
    --muted: #9a9a93;
    --border: #33332f;
    --accent: #5fb39c;
    --accent-text: #0e1b17;
    --accent-soft: #1d302a;
    --danger: #e0806f;
    --warn-bg: #2b2418;
    --warn-border: #5c4a26;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --surface: #1f1f1d; --text: #ecece8; --muted: #9a9a93; --border: #33332f;
  --accent: #5fb39c; --accent-text: #0e1b17; --accent-soft: #1d302a; --danger: #e0806f;
  --warn-bg: #2b2418; --warn-border: #5c4a26; color-scheme: dark;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.page { max-width: 820px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; line-height: 1.25; margin: 0 0 24px; }
h2 { font-size: 1.1rem; margin: 32px 0 12px; }
a { color: var(--accent); }
.muted { color: var(--muted); font-size: 0.9rem; }
.error { color: var(--danger); margin: 8px 0 0; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.between { justify-content: space-between; margin-top: 16px; }

button, .button, select, input[type="password"] {
  font: inherit; font-size: 0.95rem; border-radius: var(--radius); padding: 9px 16px;
}
button, .button {
  background: var(--accent); color: var(--accent-text); border: 1px solid var(--accent);
  cursor: pointer; text-decoration: none; display: inline-block;
}
button:disabled { opacity: 0.45; cursor: default; }
button.secondary, .button.secondary { background: transparent; color: var(--text); border-color: var(--border); }
button:focus-visible, .button:focus-visible, select:focus-visible, input:focus-visible, .drop:focus-within {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
select, input[type="password"] { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
input[type="password"] { flex: 1 1 220px; min-width: 0; }

.panel, .alert { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.alert { background: var(--warn-bg); border-color: var(--warn-border); margin: 16px 0; }
.alert p { margin: 0 0 12px; }
.steps-help { padding-left: 20px; margin: 0 0 16px; }

.drop {
  display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center;
  padding: 40px 16px; border: 1.5px dashed var(--border); border-radius: var(--radius);
  background: var(--surface); cursor: pointer;
}
.drop.over { border-color: var(--accent); background: var(--accent-soft); }
.drop-text { font-size: 1.05rem; overflow-wrap: anywhere; }
.kind { border: 0; padding: 0; margin: 0; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.kind legend { float: left; margin-right: 8px; color: var(--muted); }

.jobs { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--border); }
.jobs a {
  display: grid; grid-template-columns: 1fr auto auto; gap: 16px; align-items: baseline;
  padding: 12px 4px; border-bottom: 1px solid var(--border); color: var(--text); text-decoration: none;
}
.jobs a:hover { background: var(--surface); }
.jobs .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { font-size: 0.85rem; font-weight: 600; }
.s-done { color: var(--accent); }
.s-error { color: var(--danger); }
.s-paused, .s-queued, .s-running { color: var(--muted); }

.back { display: inline-block; margin-bottom: 16px; text-decoration: none; }
.job-head h1 { margin-bottom: 4px; overflow-wrap: anywhere; }
.job-head { margin-bottom: 24px; }
.steps { list-style: none; padding: 0; margin: 0 0 16px; }
.steps li { display: flex; gap: 12px; align-items: baseline; padding: 6px 0; }
.steps li::before { content: ""; width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--border); flex: none; transform: translateY(1px); }
.steps li.done::before { background: var(--accent); border-color: var(--accent); }
.steps li.current::before { border-color: var(--accent); }
.steps li.todo .label { color: var(--muted); }
.steps li.current .label { font-weight: 600; }
.bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
#bar-fill { height: 100%; width: 0; background: var(--accent); transition: width 0.4s ease; }

.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 24px; }
.spacer { flex: 1; }
.doc {
  max-width: 760px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 28px 32px; overflow-wrap: anywhere;
}
.doc h1 { font-size: 1.5rem; margin-top: 0; }
.doc h2 { font-size: 1.15rem; margin-top: 28px; }
.doc p { margin: 0 0 12px; }
.doc hr { border: 0; border-top: 1px solid var(--border); margin: 28px 0; }
.doc pre.plain { white-space: pre-wrap; font: inherit; margin: 0; }

@media (max-width: 600px) {
  .page { padding-top: 20px; }
  .jobs a { grid-template-columns: 1fr auto; }
  .jobs a .muted { grid-column: 1; grid-row: 2; }
  .doc { padding: 20px 16px; }
  .spacer { display: none; }
}
```

- [ ] **Step 3: `voice_to_doc/static/app.js`**

```js
const $ = (id) => document.getElementById(id);
const STEPS = [
  ["prepare", "Подготовка звука"],
  ["transcribe", "Распознавание"],
  ["structure", "Оформление текста"],
  ["summary", "Выжимка"],
  ["render", "Сборка документа"],
];
const UNITS = { transcribe: "кусков", structure: "блоков" };
const STATUS = { queued: "В очереди", running: "В работе", done: "Готово", paused: "Пауза", error: "Ошибка" };
const KIND = { lecture: "Лекция", interview: "Интервью" };

let timer = null;
let selectedFile = null;
let currentJob = null;
let renderedFor = null;

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new Error((isJson && body.detail) || `Ошибка ${res.status}`);
  return body;
}

const postJson = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const m = Math.max(1, Math.round(seconds / 60));
  return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, "0")} мин`;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function showError(id, message) {
  $(id).textContent = message;
  $(id).hidden = false;
}

function route() {
  clearTimeout(timer);
  const match = location.hash.match(/^#\/job\/([\w-]+)$/);
  if (match) showJob(match[1]);
  else showHome();
}

/* ---------- home ---------- */

async function showHome() {
  $("job").hidden = true;
  $("home").hidden = false;
  const { has_key } = await api("/api/settings");
  $("setup").hidden = has_key;
  $("upload-form").hidden = !has_key;
  const jobs = await api("/api/jobs");
  $("jobs-empty").hidden = jobs.length > 0;
  $("jobs").replaceChildren(...jobs.map(jobRow));
  if (jobs.some((j) => j.status === "queued" || j.status === "running")) timer = setTimeout(showHome, 3000);
}

function jobRow(job) {
  const link = el("a");
  link.href = `#/job/${job.id}`;
  link.append(
    el("span", "name", job.filename),
    el("span", "muted", `${fmtDate(job.created_at)} · ${fmtDuration(job.duration_s)}`),
    el("span", `status s-${job.status}`, STATUS[job.status]),
  );
  const item = el("li");
  item.append(link);
  return item;
}

function resetUpload() {
  selectedFile = null;
  $("file-input").value = "";
  $("drop-text").innerHTML = "Перетащите файл сюда или <u>выберите</u>";
  $("upload-btn").disabled = true;
}

function bindHome() {
  const drop = $("drop");
  const pick = (file) => {
    if (!file) return;
    selectedFile = file;
    $("drop-text").textContent = file.name;
    $("upload-btn").disabled = false;
  };
  $("file-input").addEventListener("change", (e) => pick(e.target.files[0]));
  ["dragenter", "dragover"].forEach((type) =>
    drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); pick(e.dataTransfer.files[0]); });

  $("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("upload-btn");
    button.disabled = true;
    button.textContent = "Загружаю…";
    $("upload-error").hidden = true;
    const form = new FormData();
    form.append("file", selectedFile);
    form.append("kind", new FormData(e.target).get("kind"));
    try {
      const { id } = await api("/api/jobs", { method: "POST", body: form });
      resetUpload();
      location.hash = `#/job/${id}`;
    } catch (err) {
      showError("upload-error", err.message);
      button.disabled = false;
    } finally {
      button.textContent = "Расшифровать";
    }
  });

  bindKeyForm("key-form", "key-input", "key-error", showHome);
}

function bindKeyForm(formId, inputId, errorId, after) {
  $(formId).addEventListener("submit", async (e) => {
    e.preventDefault();
    $(errorId).hidden = true;
    try {
      await postJson("/api/settings", { groq_api_key: $(inputId).value });
      $(inputId).value = "";
      after();
    } catch (err) {
      showError(errorId, err.message);
    }
  });
}

/* ---------- job ---------- */

async function showJob(id) {
  $("home").hidden = true;
  $("job").hidden = false;
  let job;
  try {
    job = await api(`/api/jobs/${id}`);
  } catch {
    location.hash = "#/";
    return;
  }
  currentJob = job;
  $("job-name").textContent = job.filename;
  $("job-meta").textContent = `${KIND[job.kind]} · ${fmtDuration(job.duration_s)} · ${STATUS[job.status]}`;

  const done = job.status === "done";
  const stopped = job.status === "paused" || job.status === "error";
  $("progress").hidden = done;
  if (!done) renderSteps(job);

  $("job-alert").hidden = !stopped;
  if (stopped) {
    $("job-message").textContent = job.message;
    $("job-key-form").hidden = job.reason !== "auth";
    $("resume-btn").hidden = job.reason === "auth";
    $("resume-btn").textContent = job.status === "error" ? "Повторить" : "Продолжить";
  }

  $("result").hidden = !done;
  if (done && renderedFor !== job.id) await renderResult(job);
  if (!done) renderedFor = null;

  if (job.status === "queued" || job.status === "running") timer = setTimeout(() => showJob(id), 2000);
}

function renderSteps(job) {
  const current = STEPS.findIndex(([key]) => key === job.step);
  const { done, total } = job.progress;
  $("steps").replaceChildren(...STEPS.map(([key, label], i) => {
    const state = current >= 0 && i < current ? "done" : i === current ? "current" : "todo";
    const item = el("li", state);
    item.append(el("span", "label", label));
    if (state === "current" && total) {
      const eta = estimate(job);
      item.append(el("span", "muted", `${done} из ${total} ${UNITS[key] || ""}`.trim() + (eta ? ` · ≈ ${eta}` : "")));
    }
    return item;
  }));
  const fraction = current < 0 ? 0 : (current + (total ? done / total : 0)) / STEPS.length;
  $("bar-fill").style.width = `${Math.round(fraction * 100)}%`;
}

function estimate(job) {
  const { done, total } = job.progress;
  if (!done || done >= total || !job.step_started_at || job.status !== "running") return "";
  const elapsed = Date.now() / 1000 - job.step_started_at;
  const left = (elapsed / done) * (total - done);
  return left < 60 ? "меньше минуты" : `${Math.round(left / 60)} мин`;
}

async function renderResult(job) {
  const md = await api(`/api/jobs/${job.id}/result.md`);
  const doc = $("doc");
  doc.dataset.md = md;
  if (window.marked) {
    // The text comes from speech recognition and an LLM: never let it inject HTML.
    const escaped = md.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    doc.innerHTML = marked.parse(escaped);
  } else {
    doc.replaceChildren(el("pre", "plain", md));
  }
  $("dl-docx").href = `/api/jobs/${job.id}/result.docx`;
  $("dl-md").href = `/api/jobs/${job.id}/result.md`;
  $("re-kind").value = job.kind;
  renderedFor = job.id;
}

async function act(path, data) {
  clearTimeout(timer);
  try {
    await postJson(path, data);
  } catch (err) {
    showError("job-key-error", err.message);
  }
  renderedFor = null;
  showJob(currentJob.id);
}

function bindJob() {
  $("resume-btn").addEventListener("click", () => act(`/api/jobs/${currentJob.id}/resume`, {}));
  $("re-btn").addEventListener("click", () =>
    act(`/api/jobs/${currentJob.id}/restructure`, { kind: $("re-kind").value }));
  $("copy-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("doc").dataset.md || "");
    const button = $("copy-btn");
    button.textContent = "Скопировано";
    setTimeout(() => { button.textContent = "Копировать"; }, 1500);
  });
  bindKeyForm("job-key-form", "job-key-input", "job-key-error",
    () => act(`/api/jobs/${currentJob.id}/resume`, {}));
}

window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", () => {
  bindHome();
  bindJob();
  route();
});
```


- [ ] **Step 4: `run.sh`**

```bash
#!/bin/bash
# Start voice-to-doc on http://127.0.0.1:8000 and open it in the browser.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
(sleep 2 && open http://127.0.0.1:8000) &
exec .venv/bin/uvicorn --factory voice_to_doc.app:create_app --host 127.0.0.1 --port 8000
```

Run: `chmod +x run.sh`

- [ ] **Step 5: `scripts/ui_check.py` — проверка страницы в браузере на подделке Groq**

```python
"""Drive the UI in Chromium against FakeGroq and save screenshots to screenshots/.

Usage: .venv/bin/python scripts/ui_check.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.conftest import make_media  # noqa: E402

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "screenshots"


def wait_for_server():
    for _ in range(50):
        try:
            urllib.request.urlopen(URL, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def main():
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    audio = make_media(tmp / "Лекция по истории интернета.wav", 8)
    env = {**os.environ, "VTD_FAKE_GROQ": "1", "VTD_FAKE_DELAY": "1.5", "DATA_DIR": str(tmp / "data"), "CHUNK_SECONDS": "3"}
    server = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "--factory", "voice_to_doc.app:create_app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT, env=env,
    )
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for scheme in ("light", "dark"):
                for name, viewport in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 375, "height": 812})):
                    page = browser.new_page(viewport=viewport, color_scheme=scheme)
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(URL)
                    expect(page.locator("#upload-form")).to_be_visible()
                    page.screenshot(path=OUT / f"home-{name}-{scheme}.png", full_page=True)
                    page.set_input_files("#file-input", str(audio))
                    page.click("#upload-btn")
                    expect(page.locator("#steps li.current")).to_be_visible()
                    page.screenshot(path=OUT / f"progress-{name}-{scheme}.png", full_page=True)
                    expect(page.locator("#doc h1")).to_have_text("Тестовая запись", timeout=30000)
                    page.screenshot(path=OUT / f"result-{name}-{scheme}.png", full_page=True)
                    scroll_width = page.evaluate("document.documentElement.scrollWidth")
                    assert scroll_width <= viewport["width"], f"horizontal scroll on {name}: {scroll_width}px"
                    assert not errors, errors
                    page.close()
            browser.close()
        print(f"OK — screenshots in {OUT}")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Запустить проверку UI**

Run: `.venv/bin/playwright install chromium && .venv/bin/python scripts/ui_check.py`
Expected: `OK — screenshots in .../screenshots`

- [ ] **Step 7: Просмотреть скриншоты глазами**

Открыть все `screenshots/*.png` (инструментом Read). Проверить: нет наложений и обрезанного текста; контраст текста и кнопок достаточный в обеих темах; на 375 px всё в одну колонку, без горизонтальной прокрутки; шаги и полоса прогресса читаемы; документ — заголовки, абзацы, выжимка. Найденные дефекты исправить в CSS/JS и повторить Step 6.

- [ ] **Step 8: Прогнать весь набор тестов**

Run: `.venv/bin/pytest -q`
Expected: все зелёные

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "feat: web UI, run script and browser check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Проверка на настоящем Groq и README

**Files:**
- Create: `tests/test_live.py`, `README.md`

**Interfaces:**
- Consumes: `load_settings`, `ENV_PATH` (1); `make_client` (3); `JobStore`, `Pipeline` (9).
- Produces: живой тест `-m live`; инструкция для пользователя.

- [ ] **Step 1: `tests/test_live.py`**

```python
"""Real Groq round trip. Run: .venv/bin/pytest -m live -s"""

import shutil
import subprocess
from dataclasses import replace

import pytest

from voice_to_doc.config import ENV_PATH, load_settings
from voice_to_doc.groq_client import make_client
from voice_to_doc.jobs import JobStore, Pipeline

TEXT = (
    "Добрый день. Сегодня мы поговорим об истории интернета. "
    "Всё началось в конце шестидесятых годов с сети ARPANET, которую создали для обмена данными между университетами. "
    "Первое сообщение передали в октябре тысяча девятьсот шестьдесят девятого года. "
    "Потом появились протоколы TCP и IP, и разные сети смогли общаться между собой. "
    "В начале девяностых Тим Бернерс-Ли придумал всемирную паутину: страницы, ссылки и браузер. "
    "С этого момента интернет стал доступен обычным людям, а не только учёным. "
    "На следующей лекции мы обсудим, как устроены поисковые системы."
)


@pytest.mark.live
def test_real_groq_end_to_end(tmp_path):
    settings = replace(load_settings(ENV_PATH), data_dir=tmp_path / "data")
    if not settings.groq_api_key:
        pytest.skip("нет GROQ_API_KEY в .env")
    speech = tmp_path / "speech.aiff"
    subprocess.run(["say", "-v", "Milena", "-o", str(speech), TEXT], check=True)
    store = JobStore(settings.data_dir)
    job = store.create("speech.aiff", "lecture")
    shutil.copy(speech, store.dir(job["id"]) / job["source"])
    Pipeline(store, lambda: settings, make_client).run(job["id"])
    job = store.get(job["id"])
    log = (store.dir(job["id"]) / "log.txt").read_text()
    assert job["status"] == "done", f"{job['message']}\n{log}"
    md = (store.dir(job["id"]) / "result.md").read_text()
    print("\n" + md)
    for word in ("arpanet", "интернет", "паутин"):
        assert word in md.lower()
    assert "не оформлен" not in md
```

- [ ] **Step 2: Запустить живой тест**

Требует, чтобы пользователь вставил ключ Groq (через страницу или в `.env`). Если ключа нет — спросить пользователя, не продолжать без него.

Run: `.venv/bin/pytest -m live -s`
Expected: 1 passed; в выводе — документ с заголовком, выжимкой и разделами. Показать вывод пользователю. Если тест упал из-за параметров API (например, `response_format` или `reasoning_effort` не поддерживаются моделью) — исправить `groq_client.py`, добавить модульный тест на исправление, повторить.

- [ ] **Step 3: `README.md`**

```markdown
# voice-to-doc

Превращает запись лекции или интервью в документ: выжимка, текст по разделам с таймкодами, реплики с подписями. Результат — Markdown и Word.

## Запуск

    ./run.sh

Откроется http://127.0.0.1:8000. При первом запуске страница попросит бесплатный ключ Groq (console.groq.com → API Keys).

## Как это работает

1. ffmpeg режет запись на 10-минутные куски (моно, 32 кбит/с).
2. Groq Whisper large-v3 распознаёт речь.
3. Groq `openai/gpt-oss-120b` чистит текст от слов-паразитов, делит на разделы/реплики, пишет выжимку.

Бесплатный тариф Groq: ~8 часов аудио и ~200 тыс. токенов текста в сутки (≈ 3–4 часа записей). Если лимит кончился, задание встаёт на паузу — нажмите «Продолжить» позже.

Файлы заданий лежат в `data/`. Настройки (модели, лимиты, размер кусков) — в `.env`, см. `voice_to_doc/config.py`.

## Разработка

    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/pytest                 # модульные тесты, без сети
    .venv/bin/pytest -m live -s      # проверка на настоящем Groq
    .venv/bin/python scripts/ui_check.py   # браузерная проверка, скриншоты в screenshots/
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test: live Groq check; docs: README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Передать пользователю на приёмку**

Попросить пользователя запустить `./run.sh`, загрузить реальную запись ~1 ч и оценить: пришёл ли документ быстрее чем за 15 минут и читается ли без правки.
