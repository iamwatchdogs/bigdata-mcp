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
