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
    """The core promise: one backend call, the same answer to all eight callers.

    Coalescing has two halves and either can fail alone. `len(calls) == 1` catches
    a coalescer that let duplicates through; `coalesced == 7` catches one that
    served the right answer from a path that never registered the seven followers
    — correct output, silently no coalescing. `FakeClock` is irrelevant here
    (nothing expires); it is present so the seam under test is configured the same
    way as its neighbours.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())
    calls: list[int] = []

    async def work() -> str:
        """Count invocations, yielding once so the eight callers genuinely overlap.

        The `sleep(0)` is what makes this a concurrency test: without a suspension
        point the first caller would finish before the second was ever scheduled,
        and a coalescer that coalesced nothing would still pass on `calls == 1`.

        Returns:
            `"answer"`, after one yield. Appends to `calls` first, so the counter
            is incremented even for a run that is later abandoned.
        """
        calls.append(1)
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> list[str]:
        """Fire eight identical calls at once and return what every one got.

        `gather` schedules all eight before any of them runs, so they arrive while
        the first is still in flight rather than serialising into eight separate
        groups of one.

        Returns:
            The eight results, in call order. Asserted equal, so a coalescer that
            returned the answer to only some waiters cannot pass.
        """
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
        """Count invocations and block until the test releases them.

        `release` rather than a sleep: the test needs the work to still be running
        at a *chosen* moment (after the owner is cancelled, with the latecomer
        already in flight), not merely slow. A `work` that finished on its own
        would close that window before the interesting caller arrived.

        Returns:
            `"answer"`, once `release` is set.
        """
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
        """Announce that it has started, yield once, then fail.

        The single `sleep(0)` is what makes the failure land *after* the owner has
        been cancelled rather than before: `started` tells the test the work is
        running, the test cancels, and the coroutine resumes only on the next
        event-loop pass — by which point the owner is gone. Without the yield the
        raise could land first and the test would prove nothing about cancellation.

        Raises:
            RuntimeError: Always, after the yield.
        """
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
    """Coalescing is keyed, not global.

    The failure this rules out is a coalescer that keys on anything coarser than
    the canonical key — a single global entry, or a key truncated to the operation
    while the arguments differ. Both would hand two *different* questions the same
    task's result, which is silent data corruption rather than an error: the caller
    receives a plausible answer to a question nobody ran. So the assertions are
    about bookkeeping, not about the returned value: `coalesced == 0` says neither
    caller joined the other, and `in_flight == 0` says both entries were retired
    rather than one leaking behind the other.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())

    async def work() -> str:
        """Answer identically, so only the key can tell the two calls apart.

        The same `work` for both keys is deliberate: if it returned something
        key-dependent, a wrongly-coalesced pair might still produce two different
        outputs by accident and pass. One answer makes any shared task visible.

        Returns:
            `"answer"`, after one yield.
        """
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> None:
        """Run the two different keys concurrently and wait for both.

        Concurrent rather than sequential so the two are in flight at the same
        time — a coalescer keyed too coarsely would have no second chance to be
        correct if the first call had already finished and been retired.
        """
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
        """Count attempts, then refuse — the backend that has already said no.

        The counter lives here rather than around the `do` calls so it counts what
        the backend was actually asked to do. A caller-side assertion could not
        distinguish "the second caller never called the backend" from "the second
        caller did, and the failure was shared afterwards".

        Raises:
            RuntimeError: Always, after one yield.
        """
        await asyncio.sleep(0)
        attempts.append(1)
        boom = "backend said no"
        raise RuntimeError(boom)

    async def scenario() -> None:
        """Make two identical calls back to back and record what each raised.

        The two expectations are deliberately different exception types. The first
        caller gets the backend's own error, because nobody had yet recorded that
        this key was broken. The second gets `SharedFailure`, which is the whole
        point: the refusal was remembered, so the caller was told "do not retry"
        rather than being handed the same error a second time as if a fresh attempt
        had failed. Both are caught here rather than asserted inline so that the
        difference between them is one comparison the test body makes, not a
        behaviour of the enclosing function.
        """
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
        """Count attempts and refuse again, so a re-run is visible in the tally.

        The same closure for both calls is the point of the test: an identical
        second refusal must still reach the backend once the window has passed.
        If the failure were cached as a value rather than as a short-lived marker,
        this would return a remembered error without ever asking.

        Raises:
            RuntimeError: Always, after one yield.
        """
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
        """Answer once and yield first, so the task completes on a later pass.

        Identical to the `work` in the coalescing tests on purpose: retirement must
        not depend on how the work finishes, only on the fact that it did.

        Returns:
            `"answer"`, after one yield.
        """
        await asyncio.sleep(0)
        return "answer"

    async def scenario() -> None:
        """Make the one call whose task this test then forgets about.

        Scoping the call to a helper that then returns matters: the caller must not
        hold the coroutine, the task, or the result, or the retirement assertion
        would be checking a map that a live reference elsewhere could still
        legitimately populate. Only `flight` survives the frame. The `assert` lives
        here for the same reason — a returned value would be bound in the caller and
        held for the rest of the test.
        """
        assert await flight.do("k", work) == "answer"

    await scenario()
    gc.collect()
    # The map is what a long-lived engine keeps; anything left in it after the work
    # is a reference that outlived the call that created it.
    assert flight._in_flight == {}  # ruff: ignore[private-member-access] - the retirement rule under test


async def test_forget_failures_clears_the_window() -> None:
    """The escape hatch for a key the operator knows has recovered.

    A failure window is a bet that the backend will fail again shortly. When it
    does not — the credential was rotated, the node was replaced — the only way
    back is an explicit reset, and that reset has to reach the failure map
    specifically. Asserting `shared_failures == 0` rather than "a later call
    succeeds" is what makes this a test of `forget_failures`: a method that cleared
    the map and then failed to suppress anything would still be observably useless
    in exactly the way this catches, and a caller's success would depend on the
    backend rather than on the reset.
    """
    flight: SingleFlight[str] = SingleFlight(clock=FakeClock())

    async def failing() -> str:
        """Refuse unconditionally, so the only variable is the window.

        No `attempts` counter here: this test is about whether the window is still
        open, not about how many times the backend was reached. The refusal makes
        the window deterministic without needing a clock.

        Raises:
            RuntimeError: Always, after one yield.
        """
        await asyncio.sleep(0)
        boom = "backend said no"
        raise RuntimeError(boom)

    async def scenario() -> None:
        """Open the window by failing, then clear it explicitly.

        The ordering is the test: the reset happens while the window is known to
        be open, so a clear that only worked on an empty map would pass vacuously.
        The refusal is caught rather than allowed to escape so that the reset is
        reached on a key that actually failed.
        """
        with pytest.raises(RuntimeError):
            await flight.do("k", failing)
        flight.forget_failures()

    await scenario()
    assert flight.shared_failures == 0
