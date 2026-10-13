"""Client-side pacing for Groq per-minute limits and the retry policy for transient errors."""

import time
from collections import deque

from voice_to_doc.groq_client import DailyLimitExceeded, RateLimited, Unavailable

WINDOW = 60.0
UNAVAILABLE_DELAYS = (2, 4, 8)


class RateLimiter:
    def __init__(self, rpm: int, tpm: int | None = None, clock=time.monotonic, sleep=time.sleep):
        self.rpm = rpm
        self.tpm = tpm
        self.clock = clock
        self.sleep = sleep
        self._events: deque[tuple[float, int]] = deque()

    def acquire(self, tokens: int = 0) -> None:
        if self.tpm:
            tokens = min(tokens, self.tpm)
        while True:
            now = self.clock()
            while self._events and now - self._events[0][0] >= WINDOW:
                self._events.popleft()
            used = sum(t for _, t in self._events)
            if len(self._events) < self.rpm and (not self.tpm or used + tokens <= self.tpm):
                self._events.append((now, tokens))
                return
            self.sleep(max(0.05, WINDOW - (now - self._events[0][0])))


def call_with_retries(fn, *, sleep=time.sleep, log=lambda message: None):
    failures = 0
    while True:
        try:
            return fn()
        except RateLimited as e:
            if e.daily:
                raise DailyLimitExceeded("Дневной лимит Groq исчерпан") from e
            log(f"Лимит Groq, жду {e.retry_after:.0f} с")
            sleep(e.retry_after)
        except Unavailable:
            if failures >= len(UNAVAILABLE_DELAYS):
                raise
            delay = UNAVAILABLE_DELAYS[failures]
            failures += 1
            log(f"Нет связи с Groq, повтор через {delay} с")
            sleep(delay)
