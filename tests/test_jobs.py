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
