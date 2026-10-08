"""Tests for `BoundedQueue`: admission, refusal, and the depth cap.

Split from `test_engine.py` because the queue is the one component with a cap of
its own, and its tests are the only ones that can hang the file: under the
unbounded mutation the queue tests never return, which is the clearest possible
demonstration of what the cap is for but a poor way to discover it.

The queue's `peak_depth` is asserted in `test_engine.py` rather than here, because
the stress harness that exercises it lives there.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript), each
applied and observed failing before reverting; the E-series for the shared
fixtures is recorded in `test_engine.py`'s module docstring:

* G1 admit at any depth -> `test_the_queue_refuses_at_its_cap`,
  `test_a_refusal_does_not_run_the_work`, and
  `test_a_refusal_releases_the_slot_it_was_about_to_take`
* G2 release the slot a refusal never took ->
  `test_a_refusal_releases_the_slot_it_was_about_to_take`
* G3 return the work's result even when it was refused ->
  `test_a_refusal_does_not_run_the_work`
* G4 accept a cap below 1 -> `test_a_queue_below_one_is_refused`
* G5 release the slot only on the success path ->
  `test_the_queue_depth_returns_to_zero_after_work`
"""

from __future__ import annotations

import asyncio

import pytest

from bigdata_mcp.engine import BoundedQueue
from bigdata_mcp.errors import QueueFull
from tests.test_engine import _answer
from tests.test_engine import _never

# --------------------------------------------------------------------------
# The bounded queue
# --------------------------------------------------------------------------


def test_the_queue_refuses_at_its_cap() -> None:
    """A refusal reports the occupancy, not the occupancy it would have become.

    `admit` counts the item being admitted, so a cap of 2 refuses the third with
    `depth == 2`: the two already in flight or waiting. §8.2 row 4 asks the
    message to carry "current depth, cap, retry-after", and a depth that counted
    the refused item would have the caller quote back a queue one busier than it
    is, as a fact.
    """
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
        """Read the depth from inside the refusal, before the outer test lets go.

        Returns:
            The queue's depth while the refusal is being handled.

        The outer assertions then hold one slot and release it exactly once, so
        a depth of 0 here would mean the refusal gave back a slot it never took
        and the two halves of `run` disagree about who is holding what.
        """
        with pytest.raises(QueueFull):
            await queue.run(_never)
        return queue.depth

    assert await scenario() == 1
    queue.release()
    assert queue.depth == 0


async def test_the_queue_depth_returns_to_zero_after_work() -> None:
    """A finished item gives its slot back, or the cap leaks one call at a time.

    `run` releases in a `finally`, so this is the success path of the same
    guarantee the refusal path covers. One leaked slot is invisible in one call
    — a queue of 4 still answers three more — and only surfaces later as a queue
    refusing at depth 3 of cap 4 with nothing in the transcript to explain it.
    Recorded as G5 in the module docstring.
    """
    queue: BoundedQueue[int] = BoundedQueue(4)

    async def scenario() -> int:
        """Run one item through the queue and report the depth afterwards.

        Returns:
            The queue's depth once `run` has returned.
        """
        await queue.run(lambda: _answer(7))
        return queue.depth

    assert await scenario() == 0


def test_a_queue_below_one_is_refused() -> None:
    """A cap of zero is a server that answers nothing, so it is refused at build.

    §10.1's cap is a promise about how many items may be outstanding. A queue
    built with 0 would refuse every call with "depth 0 of cap 0", which reads as a
    busy host rather than as the misconfiguration it is. Refusing in `__init__`
    puts the mistake where it was made.
    """
    with pytest.raises(ValueError, match="at least 1"):
        _ = BoundedQueue(0)


async def test_a_refusal_does_not_run_the_work() -> None:
    """The work callable is not called at all when admission fails.

    `run` admits before it calls `work`, and that order is the guarantee: a
    refused item that still executed would charge the caller for a call it was
    told would not run, and would make `QueueFull` a statement about scheduling
    rather than about whether the work happened.
    """
    queue: BoundedQueue[int] = BoundedQueue(1)
    ran: list[int] = []

    async def scenario() -> None:
        """Fill the queue, then offer one more item through `run`.

        The queue is admitted first so the refusal is genuine; without that the
        work would run and the assertion below would pass for the wrong reason.
        A closure only so that the `await` and the `pytest.raises` that catches
        it share one scope.
        """
        queue.admit()

        async def work() -> int:
            """A callable whose only effect is to record that it was reached.

            Returns:
                A value nothing reads, because reaching the return is the failure.

            The `sleep(0)` makes it a coroutine that genuinely suspends, so a
            `run` that skipped the admission check would reach the append rather
            than failing some earlier and less obvious way.
            """
            await asyncio.sleep(0)
            ran.append(1)
            return 1

        with pytest.raises(QueueFull):
            await queue.run(work)

    await scenario()
    assert ran == []
