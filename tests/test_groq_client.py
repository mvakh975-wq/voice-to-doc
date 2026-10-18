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
