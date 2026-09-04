import asyncio
import time

from app.ratelimit import HostLimiter, MinIntervalGate, SlidingWindowLimiter


def test_sliding_window_limiter():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    now = 1000.0
    assert limiter.check("a", now) == (True, 2, 0.0)
    assert limiter.check("a", now + 1) == (True, 1, 0.0)
    assert limiter.check("a", now + 2) == (True, 0, 0.0)
    allowed, remaining, retry = limiter.check("a", now + 3)
    assert not allowed and remaining == 0 and 56 < retry <= 57
    assert limiter.check("b", now + 3)[0], "other clients are independent"
    assert limiter.check("a", now + 61)[0], "window slides"


def test_min_interval_gate():
    gate = MinIntervalGate(60)
    assert gate.allow("q", 0.0) == (True, 0.0)
    ok, wait = gate.allow("q", 10.0)
    assert not ok and wait == 50.0
    assert gate.allow("q", 61.0)[0]


async def test_host_limiter_spaces_requests_and_caps_concurrency():
    limiter = HostLimiter(max_concurrency=2, min_interval=0.05)
    active = 0
    peak = 0
    stamps: list[float] = []

    async def worker() -> None:
        nonlocal active, peak
        async with limiter:
            active += 1
            peak = max(peak, active)
            stamps.append(time.monotonic())
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(4)))
    assert peak <= 2
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.045 for g in gaps)
