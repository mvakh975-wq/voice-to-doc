"""Speech-to-text for prepared chunks; resumable chunk by chunk."""

import json
import time
from pathlib import Path

from voice_to_doc.fsutil import write_text_atomic
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
            write_text_atomic(part, json.dumps(shift_segments(raw, chunk["start"]), ensure_ascii=False))
            log(f"Распознан кусок {i + 1}/{len(chunks)}")
        segments.extend(json.loads(part.read_text(encoding="utf-8")))
        on_progress(i + 1, len(chunks))
    write_text_atomic(job_dir / "transcript.json", json.dumps(segments, ensure_ascii=False))
    return segments
