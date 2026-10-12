"""§10.2's lazy probe: it measures the host without taking a permit for itself.

Split from `test_engine.py` when that file crossed Codacy's 500-line limit. The
subject is one property — a probe that queued behind the work it measures would
report the queue, not the host — and the tests around it are the mutation evidence
for how that property broke and was found.
"""

from __future__ import annotations

import asyncio
import contextlib

from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.engine import Probe
from bigdata_mcp.executor import FakeExecutor

# --------------------------------------------------------------------------
# The lazy probe (§10.2)
# --------------------------------------------------------------------------


def test_a_probe_with_no_sample_is_stale() -> None:
    """With nothing recorded, "stale" is the only honest answer.

    The question behind `stale` is whether the last reading may still be trusted,
    and before the first one there is nothing to trust. §10.2 refreshes lazily —
    no background timer, because a stdio server may idle for hours — so this
    check is what decides whether a re-probe happens at all. The opposite default
    would be the quiet failure: a cap driver reported as measured when nothing
    was ever read, and no reader of the answer able to tell.
    """
    assert Probe(clock=FakeClock()).stale(5.0) is True


def test_a_fresh_sample_is_not_stale() -> None:
    """The other half of `record`: a sample taken now has not aged out yet.

    `stale` compares `now - last_at` against the bound, and `record` is the only
    thing that sets `last_at`. Without this, an implementation that never recorded
    anything would still pass the two tests around it — both expect True — and
    only a probe that can also say "fresh" shows the recording is what changed
    the answer.
    """
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


async def test_the_probe_does_not_need_a_permit_to_measure() -> None:
    """A probe that queued behind the work it measures would report the queue.

    The observer's reading is taken through the same gate the work goes through,
    and a probe that needed a permit would wait for the very backlog it is
    measuring — or never run at all when the backlog is at the cap, which is
    exactly when the staleness answer matters most.

    Every permit is held before either call, and both are bounded by a timeout,
    which is the load-bearing half: a `record`/`stale` that queued for a permit
    would hang here rather than answering. The previous version asserted on an
    untouched gate's occupancy, which no probe behaviour could move — it passed
    with the probe deleted.

    The staleness answer is asserted too, so the calls have to *work* behind the
    held permits and not merely avoid blocking.
    """
    engine = PoliteEngine(FakeExecutor(), edge_cores=4, queue_cap=1)
    gate = engine.gates.for_host("edge")
    clock = FakeClock()
    probe = Probe(clock=clock)

    async def scenario() -> bool:
        """Hold every permit, then record and read staleness behind them.

        Returns:
            `stale(...)` for a sample six seconds old, which is the only answer
                that proves the probe measured at all.
        """
        stack = contextlib.AsyncExitStack()
        permits = [
            await stack.enter_async_context(gate.permit()) for _ in range(len(gate))
        ]
        assert len(permits) == len(gate), (
            "the test could not hold every permit, so nothing was proven about "
            "measuring behind them"
        )
        try:
            probe.record()
            assert probe.stale(5.0) is False, "a fresh sample must not read stale"
            clock.advance(6)
            return probe.stale(5.0)
        finally:
            await stack.aclose()

    assert await asyncio.wait_for(scenario(), timeout=5.0) is True, (
        "the probe could not measure while every permit was held"
    )
    assert gate.in_flight == 0, "a permit the test took was never released"
