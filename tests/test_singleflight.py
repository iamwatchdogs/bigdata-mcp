"""Tests for `SingleFlight`: coalescing, sharing failures, and retirement.

Split from `test_engine.py` because this is the only component whose contract is
about cancellation rather than about a cap. Its two subtlest failures are both
invisible to a caller that never loses interest in a call: the shared task outliving
the caller that started it, and a failure the caller never got to see. Keeping them
with the other single-flight tests is what makes that pairing obvious.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript), each
applied and observed failing before reverting; the E-series for the shared
fixtures is recorded in `test_engine.py`'s module docstring:

* E7 remove the single-flight coalescing ->
  `test_concurrent_identical_calls_share_one_backend_call`
* E8 stop sharing failures ->
  `test_a_failure_is_shared_rather_than_retried_by_every_waiter`
* E13 retire the in-flight entry when the *caller* stops waiting ->
  `test_a_cancelled_owner_does_not_let_a_second_call_start`
* E14 record the failure only where the owner can see it ->
  `test_a_failure_after_the_owner_is_cancelled_is_still_recorded`
* E15 drop the done callback altogether ->
  `test_a_cancelled_owner_does_not_let_a_second_call_start` and
  `test_a_finished_task_leaves_nothing_retrievable`
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import SharedFailure
from bigdata_mcp.engine import SingleFlight

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


async def test_a_cancelled_owner_does_not_let_a_second_call_start() -> None:
    """Cancelling the caller must not let a second `work()` in for the same key.

    `asyncio.shield` keeps the shared task running when its owner is cancelled, so
    a `finally` that removed the in-flight entry on the owner's way out dropped it
    while the work was still going. A caller arriving in that window found no entry
    and started a second call — the guarantee this class exists to provide, gone the
    moment the first caller lost interest.

    The owner is cancelled and a third caller arrives before the work finishes; the
    third must join the original task rather than start a new one.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())
    calls: list[int] = []
    release = asyncio.Event()

    async def work() -> str:
        calls.append(1)
        await release.wait()
        return "answer"

    owner = asyncio.ensure_future(flight.do("k", work))
    await asyncio.sleep(0)
    follower = asyncio.ensure_future(flight.do("k", work))
    await asyncio.sleep(0)

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    # The window the old `finally` opened: the task is alive, the entry is gone.
    latecomer = asyncio.ensure_future(flight.do("k", work))
    await asyncio.sleep(0)

    release.set()
    assert await follower == "answer"
    assert await latecomer == "answer"
    assert len(calls) == 1, f"work ran {len(calls)} times for one key"


async def test_a_failure_after_the_owner_is_cancelled_is_still_recorded() -> None:
    """A cancelled owner cannot be the one to notice the failure.

    The failure window exists so a broken key is not retried by every waiter in
    turn. With the bookkeeping tied to the owner's `except`, an owner that was
    cancelled recorded nothing and the failure went unremembered.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())
    started = asyncio.Event()

    async def work() -> str:
        started.set()
        await asyncio.sleep(0)
        boom = "boom"
        raise RuntimeError(boom)

    owner = asyncio.ensure_future(flight.do("k", work))
    await started.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    await asyncio.sleep(0)

    with pytest.raises(SharedFailure):
        await flight.do("k", work)


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


async def test_a_finished_task_leaves_nothing_retrievable() -> None:
    """`add_done_callback` holds a reference to the task after it completes.

    A done callback is not released when it fires — asyncio keeps it on the task,
    and the task is what a caller might still be holding. If the callback closed
    over the task itself, every completed single-flight would keep its result
    reachable for as long as anyone held the coroutine's owner. Asserted because
    the alternative is a slow leak that no functional test would ever notice.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())

    async def work() -> str:
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> None:
        assert await flight.do("k", work) == "answer"

    await scenario()
    gc.collect()
    # The map is what a long-lived engine keeps; anything left in it after the work
    # is a reference that outlived the call that created it.
    assert flight._in_flight == {}  # ruff: ignore[private-member-access] - the retirement rule under test


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
