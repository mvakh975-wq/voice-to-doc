import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_media
from voice_to_doc.app import STATIC, create_app
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
    return TestClient(create_app(settings_loader=loader, client_factory=lambda s: fake, inline=True),
                      base_url="http://127.0.0.1:8000")


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


SAME_ORIGIN = {"origin": "http://127.0.0.1:8000", "sec-fetch-site": "same-origin"}


def test_rejects_foreign_host_header(client):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends Host: evil.example."""
    assert client.get("/api/jobs", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/jobs", headers={"host": "localhost:8000"}).status_code == 200


def test_rejects_cross_site_post(client):
    r = client.post("/api/settings", json={"groq_api_key": "gsk_evil"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/settings", json={"groq_api_key": "gsk_evil"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert client.get("/api/settings").json() == {"has_key": False}


def test_same_origin_post_allowed(client):
    assert client.post("/api/settings", json={"groq_api_key": "gsk_ok"}, headers=SAME_ORIGIN).status_code == 200


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    for directive in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'", "object-src 'none'"):
        assert directive in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_page_loads_only_local_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    assert sources and all(src.startswith("/static/") for src in sources)
