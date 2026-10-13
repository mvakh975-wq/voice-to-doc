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
