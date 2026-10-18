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
    chars_per_token: float = 3.5  # measured ≈3.8 for Russian on gpt-oss; kept slightly conservative
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
    kwargs = {}
    for key, (attr, convert) in _FIELDS.items():
        if raw.get(key, "") == "":
            continue
        try:
            kwargs[attr] = convert(raw[key])
        except ValueError:
            raise ValueError(f"Неверное значение {key} в .env: {raw[key]!r}") from None
    return Settings(env_path=env_path, **kwargs)


def save_api_key(key: str, env_path: Path = ENV_PATH) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Ключ не должен быть пустым или содержать пробелы")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [line for line in lines if not line.startswith("GROQ_API_KEY=")]
    lines.append(f"GROQ_API_KEY={key}")
    # The file holds a secret: create it owner-only and tighten an existing one.
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
