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
