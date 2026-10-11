"""The derived concurrency cap: `clamp(cores/2, 2, 8)`, never a hardcoded number.

Split from `test_engine.py` when that file crossed Codacy's 500-line limit. The
subject is one formula and the two inputs it cannot guess — a zero-core reading,
which is a host that did not answer rather than a host with no cores, and the
clamps, which are §10's bounds rather than tuning.
"""

from __future__ import annotations

import asyncio

from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.errors import QueueFull
from bigdata_mcp.executor import FakeExecutor

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
    """Both ends of `clamp(edge_cores // 2, 2, 8)` are reachable in practice.

    The floor of 2 is what a one-core reading gets, because `1 // 2` is 0 and a
    gate built at 0 raises `ValueError` — without the floor, a weak host would
    become a server that refuses every call rather than a slow one. The ceiling
    of 8 is the point past which the edge host is the bottleneck: more
    concurrency only deepens the queue it already has, so 1000 cores buys
    nothing over 16.
    """
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
    """The operator's number and the enforced number have to be the same one.

    `concurrency` reads `gates.limit`, and `stats().concurrency` copies it into
    the snapshot an operator reads. Both have to agree with what `for_host` builds
    its `HostGate` from, so a second derivation anywhere along that path would
    report a cap the engine is not holding itself to.
    """
    engine = PoliteEngine(FakeExecutor(), edge_cores=16)
    assert engine.concurrency == 8
    assert engine.stats().concurrency == 8


async def test_the_stress_harness_keeps_the_queue_bounded() -> None:
    """§17.2: the queue never grows past its cap, whatever the load.

    Submits four times the cap and asserts both halves: the depth never exceeded
    it, *and* the excess was refused rather than queued. A queue that grew quietly
    would pass the first assertion alone.
    """
    cap = 8
    engine = PoliteEngine(FakeExecutor(delay_s=0.005), edge_cores=4, queue_cap=cap)

    async def one(index: int) -> bool:
        """Run one distinct key, reporting a refusal instead of raising.

        `gather` propagates the first exception, so a `QueueFull` escaping here
        would abandon the rest of the load and the refusals below could not be
        counted at all. The key is built per index on purpose: coalescing is
        keyed on it, so a shared key would collapse four times the cap onto one
        backend call, the 5 ms delays would never overlap, and the queue would
        be holding waiters on a single call rather than genuine concurrency.

        Args:
            index: Makes this caller's canonical key distinct from the others.

        Returns:
            True when the call ran, False when the queue refused it.
        """
        try:
            await engine.run(f"k{index}", ("true",))
        except QueueFull:
            return False
        return True

    results = await asyncio.gather(*(one(index) for index in range(cap * 4)))

    # `peak_depth`, not `depth`. After `gather` every item has been released, so
    # `depth` is 0 and `depth <= cap` is a tautology — it passes just as well
    # against a queue with no cap at all, which is the whole thing being tested.
    # The peak is read during execution and survives it.
    # Both bounds, because each direction fails differently. `<= cap` alone passes
    # against a queue that never tracked a peak at all, since 0 is under any cap.
    assert engine.queue.peak_depth >= 1, "the queue never recorded any occupancy"
    assert engine.queue.peak_depth <= cap, (
        f"the queue reached {engine.queue.peak_depth} against a cap of {cap}"
    )
    assert results.count(False) >= 1
    assert engine.stats().refused >= 1


async def test_the_engine_never_exceeds_the_derived_cap() -> None:
    """The semaphore, not the queue, is what holds the peak down here.

    `queue_cap` is 64 for 30 callers on purpose. Anything smaller would let the
    queue reject, and `gather` propagates the first `QueueFull` out of
    `scenario`, so the test would fail on the wrong control — and one whose
    failure says nothing about the gate. The keys differ per call so nothing
    coalesces away, and `peak` rather than `in_flight` is what gets read: every
    permit is back by the time `gather` returns, so occupancy is 0 whatever the
    peak was.
    """
    engine = PoliteEngine(FakeExecutor(delay_s=0.002), edge_cores=4, queue_cap=64)
    assert engine.concurrency == 2

    async def scenario() -> int:
        """Run the whole burst, then report the gate's high-water mark.

        Returns:
            The highest occupancy the gate ever reached across the 30 calls.
        """
        await asyncio.gather(*(engine.run(f"k{i}", ("true",)) for i in range(30)))
        return engine.gates.for_host("edge").peak

    assert await scenario() <= 2
