"""RateLimitManager gives tokens back with time, so no adapter call waits forever.

Before the fix, an adapter had 100 tokens per process that never came back:
the 101st rate-limited call looped on ``asyncio.sleep(0.1)`` for ever. These
tests run on a fake clock: the event loop's ``time()`` and ``asyncio.sleep``
both move a counter, so minutes of waiting take no real time, and a call that
would wait for ever is cut off by ``asyncio.wait_for`` after an hour of fake
time.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, RateLimitManager

EX = "binanceusdm"
CAPACITY = 100  # requests a process may make before any wait
REFILL_S = 60.0 / CAPACITY  # seconds per token as the bucket refills: 0.6 s
HOUR = 3600.0  # fake seconds before a stuck call is cut off


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []


class FakeClockLoop(asyncio.SelectorEventLoop):
    """An event loop whose time is the fake clock (wait_for's deadline uses it too)."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self._clock = clock

    def time(self) -> float:
        return self._clock.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, result=None):
        clock.slept.append(delay)
        clock.now += delay
        await real_sleep(0)  # yield, so a passed wait_for deadline can fire
        return result

    monkeypatch.setattr(ca.asyncio, "sleep", fake_sleep)
    return clock


def run(clock: Clock, coro, timeout: float = HOUR):
    """One event loop per call, as PerpDemoVenue's asyncio.run does, bounded by ``timeout`` fake seconds."""

    loop = FakeClockLoop(clock)
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout))
    finally:
        loop.close()


def burst(clock: Clock, rl: RateLimitManager, n: int) -> int:
    """Acquire ``n`` times with no time between; how many completed before a cut-off."""

    done = 0

    async def go():
        nonlocal done
        for _ in range(n):
            await rl.acquire(EX, weight=1)
            done += 1

    try:
        run(clock, go())
    except TimeoutError:
        pass
    return done


def test_requests_101_to_300_complete_and_each_waits_at_most_one_refill(clock):
    rl = RateLimitManager()
    start = clock.now

    done = burst(clock, rl, 300)

    assert done == 300, f"request {done + 1} still waiting after {HOUR:g} s"
    assert rl.get_state(EX).weight_used == 300
    # Requests 101..300 each wait for one token: 0.6 s, never more.
    assert max(clock.slept) <= REFILL_S + 1e-9
    assert clock.now - start == pytest.approx((300 - CAPACITY) * REFILL_S, abs=0.01)


def test_first_hundred_requests_never_wait(clock):
    rl = RateLimitManager()

    assert burst(clock, rl, CAPACITY) == CAPACITY
    assert clock.slept == []


def test_the_bucket_refills_while_idle(clock):
    rl = RateLimitManager()
    assert burst(clock, rl, 150) == 150

    clock.now += 60.0  # a minute without requests fills the bucket again
    clock.slept.clear()
    assert burst(clock, rl, CAPACITY) == CAPACITY
    assert clock.slept == []


def test_requests_at_the_refill_rate_never_wait(clock):
    rl = RateLimitManager()
    assert burst(clock, rl, CAPACITY) == CAPACITY  # empty the bucket
    clock.slept.clear()

    for _ in range(500):
        clock.now += REFILL_S + 1e-6
        assert burst(clock, rl, 1) == 1
    assert clock.slept == []


def test_adapter_calls_past_the_hundredth_complete_one_loop_each(clock):
    """The perp venue runs every adapter call in its own asyncio.run; call 101 used to hang there."""

    class Exchange:
        def fetch_positions(self, symbols=None):
            return []

    adapter = object.__new__(CCXTAdapter)
    adapter.config = SimpleNamespace(id=EX)
    adapter.exchange = Exchange()
    adapter._rate_limiter = RateLimitManager()

    finished = 0
    for _ in range(250):
        try:
            assert run(clock, adapter.fetch_positions()) == []
        except TimeoutError:
            break
        finished += 1

    stuck = f"adapter call {finished + 1} still waiting after {HOUR:g} s"
    assert finished == 250, stuck


def test_a_header_reset_still_fills_the_bucket(clock):
    rl = RateLimitManager()
    assert burst(clock, rl, 1) == 1
    reset_at = clock.now + 0.25  # sooner than one refill
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at * 1000)}
    rl.update_from_headers(EX, headers)

    assert burst(clock, rl, 1) == 1
    # It waited for the reset, not for a refill.
    assert reset_at <= clock.now < reset_at + REFILL_S / 2
    assert rl.get_state(EX).remaining == pytest.approx(CAPACITY - 1)


def test_a_header_reset_far_away_waits_only_for_the_refill(clock):
    """A reset time in wall-clock ms is far beyond the loop's clock; it must not hold the call."""

    rl = RateLimitManager()
    assert burst(clock, rl, 1) == 1
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000000"}
    rl.update_from_headers(EX, headers)
    start = clock.now

    assert burst(clock, rl, 3) == 3
    assert clock.now - start == pytest.approx(3 * REFILL_S, abs=0.01)


@pytest.mark.parametrize("left", [0, 5])
def test_a_header_count_gets_no_tokens_from_before_it(clock, left):
    """A header's count of tokens left is the count at that moment.

    The refill used to count from the request before the header, so 30 s idle
    before a header saying 0 left gave 50 tokens and the next request went
    without a wait.
    """

    rl = RateLimitManager()
    assert burst(clock, rl, 1) == 1
    clock.now += 30.0  # idle before the header
    reset_at = clock.now + 10.0  # later than one refill
    headers = {
        "x-ratelimit-remaining": str(left),
        "x-ratelimit-reset": str(reset_at * 1000),
    }
    rl.update_from_headers(EX, headers)

    assert burst(clock, rl, left + 1) == left + 1
    # The header's tokens go, then one request waits for one refill.
    assert sum(clock.slept) == pytest.approx(REFILL_S, abs=0.01)
    assert rl.get_state(EX).remaining == pytest.approx(0, abs=0.01)


def test_a_lock_contended_on_one_loop_serves_the_next_loop(clock):
    """Requests that queue on one loop must not break the next loop's requests.

    An asyncio.Lock binds to the loop it is first contended on. Requests past
    the bucket queue on the lock; a later queued request on a new loop, as the
    next asyncio.run makes, raised RuntimeError.
    """

    rl = RateLimitManager()

    async def together(n: int) -> None:
        await asyncio.gather(*(rl.acquire(EX, weight=1) for _ in range(n)))

    run(clock, together(CAPACITY + 2))  # two wait for a refill, one queues behind
    run(clock, together(3))  # the bucket is empty: two queue on a new loop

    assert rl.get_state(EX).weight_used == CAPACITY + 5
    assert sum(clock.slept) == pytest.approx(5 * REFILL_S, abs=0.01)


def test_bucket_size_and_refill_rate_are_as_documented():
    assert RateLimitManager.CAPACITY == CAPACITY
    assert 1 / RateLimitManager.REFILL_PER_S == pytest.approx(REFILL_S)
    assert ca.RateLimitState(exchange_id=EX).remaining == CAPACITY
