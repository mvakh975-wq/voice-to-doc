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
