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
