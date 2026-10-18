import subprocess

import imageio_ffmpeg
import pytest

from voice_to_doc.config import _FIELDS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not see the developer's real environment variables."""
    for key in _FIELDS:
        monkeypatch.delenv(key, raising=False)


class FakeClock:
    """Monotonic clock whose sleep() just advances time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def make_media(path, seconds, video=False):
    """Generate a real audio (sine) or video file with ffmpeg."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-shortest", "-c:v", "mpeg4", "-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path
