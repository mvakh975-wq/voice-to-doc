import pytest

from voice_to_doc.groq_client import DailyLimitExceeded, RateLimited, Unavailable
from voice_to_doc.ratelimit import RateLimiter, call_with_retries


def test_requests_per_minute(clock):
    rl = RateLimiter(rpm=2, clock=clock, sleep=clock.sleep)
    rl.acquire(); rl.acquire()
    assert clock.now == 0
    rl.acquire()
    assert clock.now == pytest.approx(60)


def test_tokens_per_minute(clock):
    rl = RateLimiter(rpm=100, tpm=8000, clock=clock, sleep=clock.sleep)
    rl.acquire(5000)
    clock.now = 10
    rl.acquire(5000)  # 10000 > 8000 → wait until first expires at t=60
    assert clock.now == pytest.approx(60)


def test_request_bigger_than_tpm_is_clamped(clock):
    rl = RateLimiter(rpm=100, tpm=8000, clock=clock, sleep=clock.sleep)
    rl.acquire(20000)  # must not hang
    assert clock.now == 0


def test_daily_limit_raises(clock):
    def fn():
        raise RateLimited("per day", retry_after=10, daily=True)
    with pytest.raises(DailyLimitExceeded):
        call_with_retries(fn, sleep=clock.sleep)


def test_minute_limit_waits_and_retries(clock):
    calls = iter([RateLimited("per minute", retry_after=7, daily=False), "ok"])
    def fn():
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item
    assert call_with_retries(fn, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [7]


def test_unavailable_backoff_then_success(clock):
    attempts = []
    def fn():
        attempts.append(1)
        if len(attempts) <= 3:
            raise Unavailable("down")
        return "ok"
    assert call_with_retries(fn, sleep=clock.sleep) == "ok"
    assert clock.sleeps == [2, 4, 8]


def test_unavailable_gives_up_after_three_retries(clock):
    def fn():
        raise Unavailable("down")
    with pytest.raises(Unavailable):
        call_with_retries(fn, sleep=clock.sleep)
    assert clock.sleeps == [2, 4, 8]
