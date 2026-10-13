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
    loader = lambda: replace(load_settings(env), data_dir=tmp_path / "data", chunk_seconds=3, llm_tpm=10**9)  # noqa: E731
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
