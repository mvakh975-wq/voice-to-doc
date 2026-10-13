"""Re-encode any audio/video into small mono chunks for speech recognition."""

import csv
import io
import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import imageio_ffmpeg

from voice_to_doc.fsutil import write_text_atomic

READ_ERROR = "Не удалось прочитать файл — это точно аудио или видео?"
MAX_SECONDS = 6 * 3600
FFMPEG_TIMEOUT = 30 * 60
# Only real media containers: playlists (hls, concat) could make ffmpeg read other local files.
DEMUXERS = "mp3,mov,wav,w64,ogg,flac,aac,matroska,aiff,caf,asf,avi"


class AudioError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def parse_segment_list(csv_text: str) -> list[dict]:
    rows = csv.reader(io.StringIO(csv_text))
    return [{"file": r[0], "start": float(r[1]), "end": float(r[2])} for r in rows if r]


def prepare_audio(src: Path, job_dir: Path, chunk_seconds: int, *,
                  max_seconds: float = MAX_SECONDS, timeout: float = FFMPEG_TIMEOUT) -> dict:
    chunks_dir = job_dir / "chunks"
    shutil.rmtree(chunks_dir, ignore_errors=True)
    chunks_dir.mkdir(parents=True)
    list_path = chunks_dir / "list.csv"
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file", "-format_whitelist", DEMUXERS,
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-segment_list", str(list_path), "-segment_list_type", "csv",
        "-reset_timestamps", "1", str(chunks_dir / "%03d.ogg"),
    ]
    # Safe: argv list without a shell; paths are ours (source<.ext>), never the user's filename.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # nosec B603
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(chunks_dir, ignore_errors=True)
        raise AudioError("Обработка файла заняла слишком долго — возможно, файл повреждён", str(e)) from e
    chunks = parse_segment_list(list_path.read_text()) if list_path.exists() else []
    if proc.returncode != 0 or not chunks:
        raise AudioError(READ_ERROR, proc.stderr.strip())
    if chunks[-1]["end"] > max_seconds:
        shutil.rmtree(chunks_dir, ignore_errors=True)
        raise AudioError(f"Запись длиннее {max_seconds / 3600:g} ч — разделите её на части",
                         f"duration {chunks[-1]['end']:.0f} s")
    manifest = {"duration_s": chunks[-1]["end"], "chunks": chunks}
    write_text_atomic(job_dir / "chunks.json", json.dumps(manifest))
    return manifest
