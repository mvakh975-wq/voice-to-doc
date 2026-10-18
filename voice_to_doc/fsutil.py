"""Crash-safe file writes: a killed process never leaves a half-written step result."""

from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
