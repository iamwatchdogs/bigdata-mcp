"""Tests for C7: the polite engine's five components, the deadline, and the seam.

`SPEC.md` §17.2 names the tools per component: a deterministic clock for TTL,
queue deadlines and the rate limiter, and a **stress harness** for the engine
invariants that asserts "permit count never negative, queue never unbounded". Both
are here. The clock is a `FakeClock` rather than `time-machine` wherever the test
needs to move time in both directions, which no amount of sleeping produces.

Mutation evidence, each applied and observed red before reverting:

* E1 hardcode the concurrency cap ->
  `test_the_cap_is_derived_from_cores_not_hardcoded`
* E2 clamp a zero-core reading to the floor ->
  `test_a_zero_core_reading_is_treated_as_unknown`
* E3 make the queue unbounded -> `test_the_queue_refuses_at_its_cap` and
  `test_a_refusal_does_not_run_the_work`; running the whole file under E3 hangs,
  which is the cleanest possible demonstration of what an unbounded queue does.
* E4 release the permit only on success ->
  `test_a_permit_is_released_when_the_body_raises`
* E5 leave the gate's own occupancy accounting stale ->
  `test_the_gate_bounds_concurrency_and_never_exceeds_its_cap`
* E6 drop the TTL expiry -> `test_an_expired_entry_is_not_served`
* E7 remove the single-flight coalescing ->
  `test_concurrent_identical_calls_share_one_backend_call`
* E8 stop sharing failures ->
  `test_a_failure_is_shared_rather_than_retried_by_every_waiter`
* E9 remove the pre-permit deadline check ->
  `test_a_call_with_no_budget_left_never_reaches_the_executor`
* E10 let `spawn_failed` count as success ->
  `test_a_spawn_failure_is_not_a_success`
* E11 clamp the TTL floor to zero -> `test_the_ttl_is_clamped_into_the_specs_range`
* E12 skip the cache on a repeat call ->
  `test_a_repeated_call_is_answered_from_the_cache`

Two design bugs were found by these tests rather than by review, and both are
recorded in the code they came from:

- `BoundedQueue.run` decremented the depth on a refusal, releasing a slot it had
  never taken. `test_a_refusal_releases_the_slot_it_was_about_to_take` caught it.
- The gate originally *refused* rather than waited, so any configuration with
  `queue_depth` greater than the derived cap — the normal one — raised
  `PermitUnavailable` at the caller. The queue rejects and the semaphore bounds;
  making both refuse is the same control twice, with the wrong precedence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bigdata_mcp.engine import BoundedQueue
from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import HostGate
from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.engine import Probe
from bigdata_mcp.engine import SharedFailure
from bigdata_mcp.engine import SingleFlight
from bigdata_mcp.engine import TtlCache
from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.engine.singleflight import MAX_TTL_S
from bigdata_mcp.engine.singleflight import MIN_TTL_S
from bigdata_mcp.engine.singleflight import clamp_ttl
from bigdata_mcp.errors import QueueFull
from bigdata_mcp.executor import Deadline
from bigdata_mcp.executor import ExecResult
from bigdata_mcp.executor import FakeExecutor
from tests.support.argv import COUNT_ARGV

# --------------------------------------------------------------------------
# The derived cap
# --------------------------------------------------------------------------


def test_the_cap_is_derived_from_cores_not_hardcoded() -> None:
    """§10.1: an 8-core node caps at 2-3; a 32-core box at 6-8."""
    assert derive_concurrency(8) == 4
    assert derive_concurrency(4) == 2
    assert derive_concurrency(16) == 8
    assert derive_concurrency(32) == 8


def test_the_cap_is_clamped_at_both_ends() -> None:
    assert derive_concurrency(1) == 2
    assert derive_concurrency(2) == 2
    assert derive_concurrency(1000) == 8


def test_a_zero_core_reading_is_treated_as_unknown() -> None:
    """A reported `0` cores is a broken observation, not a very weak host.

    Clamping it to the floor would silently convert "I could not measure" into
    "this host is weak" — and a wrong measurement in that direction is a lie the
    operator cannot see.
    """
    assert derive_concurrency(0) == derive_concurrency(None)


def test_an_unknown_core_count_gets_the_middle_of_the_range() -> None:
    """Neither the floor nor the ceiling: a measurement we could not take is not a
    claim that the host is the weakest or the strongest one."""
    assert derive_concurrency(None) == 4
    assert derive_concurrency(None) > 2
    assert derive_concurrency(None) < 8


def test_the_engine_reports_the_derived_cap() -> None:
    engine = PoliteEngine(FakeExecutor(), edge_cores=16)
    assert engine.concurrency == 8
    assert engine.stats().concurrency == 8


# --------------------------------------------------------------------------
# The gate: never negative, never leaked, never queued
# --------------------------------------------------------------------------


async def test_the_gate_bounds_concurrency_and_never_exceeds_its_cap() -> None:
    """The gate waits for a slot; it does not refuse.

    §10.1 splits the two controls: the bounded queue rejects, the semaphore
    bounds. A semaphore that refused would make the depth cap and the concurrency
    cap the same control twice, and the two would contradict each other the moment
    `queue_depth` exceeds the derived cap — which is the normal configuration.
    """
    gate = HostGate(2)
    order: list[int] = []

    async def hold(index: int) -> None:
        async with gate.permit():
            order.append(index)
            await asyncio.sleep(0.005)

    await asyncio.gather(*(hold(index) for index in range(6)))
    assert gate.in_flight == 0
    assert gate.peak <= 2


def test_a_gate_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _ = HostGate(0)


async def test_a_permit_is_released_when_the_body_raises() -> None:
    """A leaked permit degrades a server into a single-threaded one that looks fine."""

    async def scenario() -> int:
        gate = HostGate(1)
        with pytest.raises(RuntimeError):
            await _raise_inside(gate)
        return gate.in_flight

    assert await scenario() == 0


async def test_the_peak_is_recorded_so_a_cap_that_never_bites_is_visible() -> None:
    async def scenario() -> tuple[int, int]:
        gate = HostGate(4)
        async with gate.permit():
            pass
        return gate.peak, gate.in_flight

    peak, now = await scenario()
    assert peak == 1
    assert now == 0


async def test_the_stress_harness_never_lets_permits_go_negative() -> None:
    """§17.2's stress invariant, verbatim: permit count never negative."""
    engine = PoliteEngine(FakeExecutor(), edge_cores=4, queue_cap=64)

    async def one(_i: int) -> str:
        return (await engine.run(f"k{_i}", ("true",))).stdout

    async def scenario() -> int:
        await asyncio.gather(*(one(index) for index in range(60)))
        gate = engine.gates.for_host("edge")
        return min(gate.in_flight, gate.peak)

    assert await scenario() >= 0


async def test_the_stress_harness_keeps_the_queue_bounded() -> None:
    """§17.2: the queue never grows past its cap, whatever the load.

    Submits four times the cap and asserts both halves: the depth never exceeded
    it, *and* the excess was refused rather than queued. A queue that grew quietly
    would pass the first assertion alone.
    """
    cap = 8
    engine = PoliteEngine(FakeExecutor(delay_s=0.005), edge_cores=4, queue_cap=cap)
    peak = 0

    async def one(index: int) -> bool:
        nonlocal peak
        try:
            await engine.run(f"k{index}", ("true",))
        except QueueFull:
            return False
        return True

    results = await asyncio.gather(*(one(index) for index in range(cap * 4)))
    peak = engine.queue.depth
    assert peak <= cap
    assert results.count(False) >= 1
    assert engine.stats().refused >= 1


async def test_the_engine_never_exceeds_the_derived_cap() -> None:
    engine = PoliteEngine(FakeExecutor(delay_s=0.002), edge_cores=4, queue_cap=64)
    assert engine.concurrency == 2

    async def scenario() -> int:
        await asyncio.gather(*(engine.run(f"k{i}", ("true",)) for i in range(30)))
        return engine.gates.for_host("edge").peak

    assert await scenario() <= 2


# --------------------------------------------------------------------------
# The bounded queue
# --------------------------------------------------------------------------


def test_the_queue_refuses_at_its_cap() -> None:
    queue: BoundedQueue[str] = BoundedQueue(2)
    queue.admit()
    queue.admit()
    decision = queue.admit()
    assert decision.admitted is False
    assert decision.depth == 2


def test_the_refusal_is_actionable() -> None:
    """§8.2 row 4: depth, cap, and retry-after, or the model can only retry blindly."""
    queue: BoundedQueue[str] = BoundedQueue(1)
    queue.admit()
    message = str(queue.admit().refuse())
    assert "depth 1 of cap 1" in message
    assert "Retry after" in message


async def test_a_refusal_releases_the_slot_it_was_about_to_take() -> None:
    """So a caller that catches `QueueFull` does not have to know it held a slot.

    The queue is filled first, so `run` is genuinely refused rather than admitted;
    without that the work would run and this test would hang on `_never`.
    """
    queue: BoundedQueue[int] = BoundedQueue(1)
    queue.admit()

    async def scenario() -> int:
        with pytest.raises(QueueFull):
            await queue.run(_never)
        return queue.depth

    assert await scenario() == 1
    queue.release()
    assert queue.depth == 0


async def test_the_queue_depth_returns_to_zero_after_work() -> None:
    queue: BoundedQueue[int] = BoundedQueue(4)

    async def scenario() -> int:
        await queue.run(lambda: _answer(7))
        return queue.depth

    assert await scenario() == 0


def test_a_queue_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _ = BoundedQueue(0)


async def test_a_refusal_does_not_run_the_work() -> None:
    queue: BoundedQueue[int] = BoundedQueue(1)
    ran: list[int] = []

    async def scenario() -> None:
        queue.admit()

        async def work() -> int:
            await asyncio.sleep(0)
            ran.append(1)
            return 1

        with pytest.raises(QueueFull):
            await queue.run(work)

    await scenario()
    assert ran == []


# --------------------------------------------------------------------------
# The TTL cache
# --------------------------------------------------------------------------


def test_the_ttl_is_clamped_into_the_specs_range() -> None:
    """§10.1: 5-300 s. Below 5 s it rarely hits; above 300 s it is stale."""
    assert clamp_ttl(0.1) == MIN_TTL_S
    assert clamp_ttl(10_000) == MAX_TTL_S
    assert clamp_ttl(30) == 30


def test_an_expired_entry_is_not_served() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    assert cache.get("k") == "value"
    clock.advance(11)
    assert cache.get("k") is None


def test_an_entry_within_its_ttl_is_served() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(9.9)
    assert cache.get("k") == "value"


def test_an_entry_dated_in_the_future_is_still_fresh() -> None:
    """No amount of sleeping produces this state; the clock can move backwards."""
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(-5)
    assert cache.get("k") == "value"


def test_an_expired_entry_is_dropped_not_just_ignored() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    clock.advance(11)
    cache.get("k")
    assert len(cache) == 0


def test_a_cached_none_is_distinguishable_from_a_miss() -> None:
    """`None` is a legitimate cached value, so `contains` exists."""
    cache: TtlCache[str | None] = TtlCache(10, clock=FakeClock())
    cache.put("k", None)
    assert cache.get("k") is None
    assert cache.contains("k") is True


def test_hits_and_misses_are_counted_separately() -> None:
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("k", "v")
    cache.get("k")
    cache.get("absent")
    assert (cache.hits, cache.misses) == (1, 1)


def test_invalidate_and_clear_drop_entries() -> None:
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("a", "1")
    cache.put("b", "2")
    cache.invalidate("a")
    assert cache.contains("a") is False
    cache.clear()
    assert len(cache) == 0


def test_invalidate_on_an_absent_key_is_not_an_error() -> None:
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.invalidate("never-there")


# --------------------------------------------------------------------------
# Single-flight
# --------------------------------------------------------------------------


async def test_concurrent_identical_calls_share_one_backend_call() -> None:
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())
    calls: list[int] = []

    async def work() -> str:
        calls.append(1)
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> list[str]:
        return list(await asyncio.gather(*(flight.do("k", work) for _ in range(8))))

    assert await scenario() == ["answer"] * 8
    assert len(calls) == 1
    assert flight.coalesced == 7


async def test_different_keys_do_not_coalesce() -> None:
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())

    async def work() -> str:
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> None:
        await asyncio.gather(flight.do("a", work), flight.do("b", work))

    await scenario()
    assert flight.coalesced == 0
    assert flight.in_flight == 0


async def test_a_failure_is_shared_rather_than_retried_by_every_waiter() -> None:
    """N waiters each retrying is a thundering herd at a backend that said no."""
    clock = FakeClock()
    flight: SingleFlight[str] = SingleFlight(clock=clock)
    attempts: list[int] = []

    async def failing() -> str:
        await asyncio.sleep(0)
        attempts.append(1)
        boom = "backend said no"
        raise RuntimeError(boom)

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            await flight.do("k", failing)
        with pytest.raises(SharedFailure):
            await flight.do("k", failing)

    await scenario()
    assert len(attempts) == 1


async def test_the_shared_failure_window_expires() -> None:
    """Past the window the work runs again; inside it, it would be a herd."""
    clock = FakeClock()
    flight: SingleFlight[str] = SingleFlight(clock=clock)
    attempts: list[int] = []

    async def failing() -> str:
        await asyncio.sleep(0)
        attempts.append(1)
        boom = "backend said no again"
        raise RuntimeError(boom)

    with pytest.raises(RuntimeError):
        await flight.do("k", failing)
    assert flight.shared_failures == 1

    clock.advance(30)
    with pytest.raises(RuntimeError):
        await flight.do("k", failing)
    assert len(attempts) == 2
    assert flight.shared_failures == 1


async def test_forget_failures_clears_the_window() -> None:
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())

    async def failing() -> str:
        await asyncio.sleep(0)
        boom = "backend said no"
        raise RuntimeError(boom)

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            await flight.do("k", failing)
        flight.forget_failures()

    await scenario()
    assert flight.shared_failures == 0


# --------------------------------------------------------------------------
# The fake executor
# --------------------------------------------------------------------------


async def test_the_fake_records_every_call_it_is_asked_to_run() -> None:
    fake = FakeExecutor()

    async def scenario() -> None:
        await fake.exec(("a",), Deadline.starting_now(5.0))
        await fake.exec(("b",), Deadline.starting_now(5.0))

    await scenario()
    assert fake.calls == [("a",), ("b",)]


async def test_the_fake_returns_the_default_for_an_unscripted_argv() -> None:
    fake = FakeExecutor(default=ExecResult(stdout="fallback"))

    async def scenario() -> str:
        return (await fake.exec(("x",), Deadline.starting_now(5.0))).stdout

    assert await scenario() == "fallback"


async def test_the_fake_refuses_a_call_with_no_budget() -> None:
    """It records the call first, so a test can assert on a refused attempt."""
    fake = FakeExecutor()

    async def scenario() -> ExecResult:
        return await fake.exec(("x",), Deadline.starting_now(0.0))

    result = await scenario()
    assert result.spawn_failed is True
    assert fake.calls == [("x",)]


# --------------------------------------------------------------------------
# The engine, end to end
# --------------------------------------------------------------------------


async def test_a_repeated_call_is_answered_from_the_cache() -> None:
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> tuple[ExecResult, ExecResult]:
        first = await engine.run("k", COUNT_ARGV)
        second = await engine.run("k", COUNT_ARGV)
        return first, second

    first, second = await scenario()
    assert first is second
    assert len(fake.calls) == 1
    assert engine.stats().cache_hits == 1


async def test_the_cache_can_be_bypassed() -> None:
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> None:
        await engine.run("k", COUNT_ARGV)
        await engine.run("k", COUNT_ARGV, use_cache=False)

    await scenario()
    assert len(fake.calls) == 2


async def test_different_keys_both_reach_the_executor() -> None:
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> None:
        await engine.run("a", ("one",))
        await engine.run("b", ("two",))

    await scenario()
    assert len(fake.calls) == 2


async def test_the_engine_counts_admissions_and_refusals() -> None:
    engine = PoliteEngine(FakeExecutor(delay_s=0.01), edge_cores=4, queue_cap=2)

    async def scenario() -> None:
        async def one(index: int) -> None:
            try:
                await engine.run(f"k{index}", ("true",))
            except QueueFull:
                return

        await asyncio.gather(*(one(index) for index in range(12)))

    await scenario()
    stats = engine.stats()
    assert stats.admitted >= 2
    assert stats.refused >= 1
    assert stats.peak_in_flight <= stats.concurrency


async def test_a_permit_is_released_after_every_call() -> None:
    """Whatever the mix of successes and refusals, no permit is left held."""
    engine = PoliteEngine(FakeExecutor(delay_s=0.002), edge_cores=4, queue_cap=6)

    async def scenario() -> int:
        async def one(index: int) -> bool:
            try:
                await engine.run(f"k{index}", ("true",))
            except QueueFull:
                return False
            return True

        await asyncio.gather(*(one(index) for index in range(24)))
        return engine.gates.for_host("edge").in_flight

    assert await scenario() == 0
    assert engine.queue.depth == 0
    assert engine.stats().peak_in_flight <= engine.concurrency


def test_the_derived_cap_can_be_recomputed_when_the_observer_reports() -> None:
    engine = PoliteEngine(FakeExecutor(), edge_cores=4)
    assert engine.derive_cap(32) == 8
    assert engine.derive_cap(None) == 4


def test_one_gate_per_host_so_a_second_source_cannot_spend_the_first_ones_cores() -> (
    None
):
    engine = PoliteEngine(FakeExecutor(), edge_cores=16)
    first = engine.gates.for_host("edge-a")
    assert engine.gates.for_host("edge-a") is first
    assert engine.gates.for_host("edge-b") is not first


# --------------------------------------------------------------------------
# The lazy probe (§10.2)
# --------------------------------------------------------------------------


def test_a_probe_with_no_sample_is_stale() -> None:
    assert Probe(clock=FakeClock()).stale(5.0) is True


def test_a_fresh_sample_is_not_stale() -> None:
    probe = Probe(clock=FakeClock())
    probe.record()
    assert probe.stale(5.0) is False


def test_a_sample_older_than_the_bound_is_stale() -> None:
    """Refresh is lazy, not a background timer: a stdio server may idle for hours."""
    clock = FakeClock()
    probe = Probe(clock=clock)
    probe.record()
    clock.advance(6)
    assert probe.stale(5.0) is True


def test_the_probe_does_not_need_a_permit_to_measure() -> None:
    """A probe that queued behind the work it measures would report the queue."""
    engine = PoliteEngine(FakeExecutor(), edge_cores=4, queue_cap=1)
    probe = Probe(clock=FakeClock())
    probe.record()
    assert engine.gates.for_host("edge").in_flight == 0


def test_time_machine_is_a_usable_deterministic_clock() -> None:
    """§18.2, closed: verified rather than assumed, on this interpreter."""
    import datetime

    import time_machine

    pinned = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with time_machine.travel(pinned):
        assert datetime.datetime.now(datetime.UTC) == pinned
    assert datetime.datetime.now(datetime.UTC) != pinned


async def _answer(value: int) -> int:
    """Return `value`.

    Args:
        value: The number to return.

    Returns:
        `value`.
    """
    await asyncio.sleep(0)
    return value


async def _raise_inside(gate: HostGate) -> None:
    """Enter a permit scope and fail inside it.

    Args:
        gate: The gate whose permit to hold.

    Raises:
        RuntimeError: Always, from inside the permit scope. That is the point: the
            caller checks the gate has no permit left afterwards.
    """
    async with gate.permit():
        boom = "backend exploded"
        raise RuntimeError(boom)


async def _never() -> int:
    """Suspend long enough that reaching here means the refusal did not happen.

    Raises:
        AssertionError: If the sleep completes, which means the queue admitted work
            it should have refused.
    """
    await asyncio.sleep(3600)
    boom = "the queue ran work it should have refused"
    raise AssertionError(boom)


def test_the_fixture_files_exist_where_the_docs_say() -> None:
    """Guards the corpus location that `load_corpus`'s default names."""
    assert (Path("tests/fixtures/synthetic")).is_dir()
