"""Tests for the gate: one `HostGate` per host, never negative, never leaked.

Split from `test_engine.py` for the same reason `test_queue.py` and
`test_singleflight.py` were: Codacy's Lizard gate fails a file over 500 NLOC, and
adding the three accessor tests pushed `test_engine.py` to 542. The gate is a
self-contained component with its own invariant — "permit count never negative" —
so it took the split better than a random fifth of that file would have.

**Mutation evidence**, each applied and observed red before reverting:

* E4 release the permit only on success ->
  `test_a_permit_is_released_when_the_body_raises`
* E5 leave the gate's own occupancy accounting stale ->
  `test_the_gate_bounds_concurrency_and_never_exceeds_its_cap`
* G1 drop the `max(0, ...)` clamp on `available` ->
  `test_a_gate_reports_how_many_permits_are_free`
* G2 report the cap regardless of occupancy -> the same test
* G3 make `len(gate)` report occupancy, the mistake the test exists to prevent ->
  `test_len_of_a_gate_is_its_configured_cap`
* G4 return gate objects from `hosts()` instead of host names ->
  `test_the_registry_reports_only_the_hosts_that_have_a_gate`

G1 to G4 came from the per-file coverage floor: `available`, `__len__` and
`hosts()` were the three lines in `semaphore.py` no test had ever called, and they
are all accessors rather than logic — which is exactly why they drift. `hosts()`
is the one with a real semantic trap: "every host with a gate *so far*" is not
"every configured source", and a reader who expects the latter will find an answer
that never grows to match the config. The absent host is asserted for that reason,
not just the two present ones.

One design bug was found by these tests rather than by review: the gate originally
*refused* rather than waited, so any configuration with `queue_depth` greater than
the derived cap — the normal one — raised `PermitUnavailable` at the caller. The
queue rejects and the semaphore bounds; making both refuse is the same control
twice, with the wrong precedence. That is why
`test_the_gate_bounds_concurrency_and_never_exceeds_its_cap` exists as written.
"""

from __future__ import annotations

import asyncio

import pytest

from bigdata_mcp.engine import HostGate
from bigdata_mcp.engine import HostGates
from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.executor import FakeExecutor

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
        """Hold one permit for 5 ms, then give it back.

        Args:
            index: Recorded in `order` on entry. No assertion reads that list;
                the two that matter are on occupancy, and it is there to keep the
                holders distinguishable in the source.

        Six of these against a gate of two is the shape under test: at the peak
        four of them are queued on the semaphore, which is exactly what a gate
        that refused instead of waiting would not produce.
        """
        async with gate.permit():
            order.append(index)
            await asyncio.sleep(0.005)

    await asyncio.gather(*(hold(index) for index in range(6)))
    assert gate.in_flight == 0
    assert gate.peak <= 2


def test_a_gate_below_one_is_refused() -> None:
    """A cap below one is refused at construction, with a message that says why.

    The engine cannot build one — `derive_concurrency`'s floor is 2 — but the
    class is public and takes whatever integer it is handed. A zero-limit gate
    would be an `asyncio.Semaphore(0)`, so every caller would block on a permit
    that can never be released: the failure would surface as a server that has
    stopped answering rather than as a configuration error. The `match` pins the
    phrase that names which number was wrong.
    """
    with pytest.raises(ValueError, match="at least 1"):
        _ = HostGate(0)


def test_a_gate_reports_how_many_permits_are_free() -> None:
    """`available` is the number a caller can take right now, and never negative.

    Three readings: idle, one held, released. The fourth asserts the clamp, which
    is the part a test cannot reach by normal use — `in_flight` cannot exceed
    `limit` without corruption — so it is pinned by construction instead: set the
    counter out of range and check the answer is `0` rather than a number no
    permit exists for. Dropping the `max` turns that into a negative, which is
    the value a naive `limit - in_flight` returns and the one the docstring
    promises never to report.
    """
    gate = HostGate(2)
    assert gate.available == 2, "an idle gate must report every permit free"

    async def scenario() -> int:
        """Read `available` from inside a held permit.

        Returns:
            The free count with one of two permits out — the only moment the
            subtraction is observable, since an idle gate and a released one
            both report the cap.
        """
        async with gate.permit():
            return gate.available

    assert asyncio.run(scenario()) == 1, "a held permit must be subtracted"
    assert gate.available == 2, "a released permit must come back"

    gate.in_flight = 99
    assert gate.available == 0, "an out-of-range count must clamp, not go negative"


def test_len_of_a_gate_is_its_configured_cap() -> None:
    """`len(gate)` is the cap, not the occupancy.

    Worth pinning because both are integers on the same object and a reader who
    expects occupancy gets a number that never changes — a static value where a
    live one was wanted looks fine until it does not.
    """
    gate = HostGate(3)

    async def scenario() -> None:
        """Read `len(gate)` while a permit is held.

        The occupied moment is the one that tells the two readings apart. A cap
        answers 3 whether or not anyone is inside; an occupancy would answer 2
        here and 3 a moment later. A check made only at rest could not say which
        of the two it was looking at.
        """
        async with gate.permit():
            assert len(gate) == 3, "the cap must not move with occupancy"

    asyncio.run(scenario())
    assert len(gate) == 3
    assert HostGate(1).limit == 1


def test_the_registry_reports_only_the_hosts_that_have_a_gate() -> None:
    """`hosts()` lists gates that exist, not hosts that are configured.

    The distinction is the whole point of `for_host`: gates are created lazily
    because §3 gives the edge host as a configured alias whose value is not known
    here. A reader who expects `hosts()` to enumerate configured sources would
    expect every configured host to appear, and it never will — so the absent
    host is asserted too, not just the two present ones.
    """
    gates = HostGates(edge_cores=2)
    assert gates.hosts() == (), "no gate exists before any host is used"

    first = gates.for_host("edge")
    second = gates.for_host("other")
    assert gates.hosts() == ("edge", "other")
    assert gates.for_host("edge") is first, "a second call must not fork the gate"
    assert gates.for_host("other") is second
    assert "third" not in gates.hosts(), "an unused host has no gate to report"


async def test_a_permit_is_released_when_the_body_raises() -> None:
    """A leaked permit degrades a server into a single-threaded one that looks fine."""

    async def scenario() -> int:
        """Fail inside a permit, then report what the gate believes is held.

        Returns:
            The occupancy once the exception has propagated out of the permit
            scope. `Permit.__aexit__` is the only code that decrements, so the
            raising path is the one way a permit goes missing for good.
        """
        gate = HostGate(1)
        with pytest.raises(RuntimeError):
            await _raise_inside(gate)
        return gate.in_flight

    assert await scenario() == 0


async def test_the_peak_is_recorded_so_a_cap_that_never_bites_is_visible() -> None:
    """The high-water mark is kept, and it outlives the occupancy.

    `peak` is raised in `__aenter__` and never lowered, so after one permit it
    reads 1 while `in_flight` is already back to 0. Asserting only the occupancy
    would pass against a gate that tracked nothing at all, because 0 is under
    every cap — the same reason the engine's stress test reads `peak` rather
    than `depth`.
    """

    async def scenario() -> tuple[int, int]:
        """Take and release one permit on a gate of four, then read both counts.

        Returns:
            The peak reached and the occupancy afterwards — 1 and 0. Read after
            the scope closes, which is what makes the pair a peak-and-rest
            reading rather than a live one.
        """
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
        """Run one command through the engine and hand back its stdout.

        Args:
            _i: Used only to build a distinct cache key, so none of the sixty
                callers coalesces and every one of them takes and gives back a
                permit of its own.

        Returns:
            The fake's `stdout`, which is empty for every one of these calls. The
            assertions are about permits, not about output, so nothing is
            compared against it.
        """
        return (await engine.run(f"k{_i}", ("true",))).stdout

    await asyncio.gather(*(one(index) for index in range(60)))

    # The counter cannot go negative and the assertion has to be able to notice if
    # it ever does, which `min(in_flight, peak) >= 0` cannot: both terms are
    # non-negative by construction, so it holds whatever the counters say. The
    # three assertions below are the properties that can each actually fail.
    gate = engine.gates.for_host("edge")
    assert gate.in_flight == 0, "permits were still held after every call returned"
    # A ceiling, not a target: `FakeExecutor` returns immediately, so calls overlap
    # only by chance and the peak is whatever the scheduler happened to produce.
    # Asserting `peak == concurrency` would be asserting that this particular run
    # saturated the gate, which is a property of the scheduler and not of the code.
    assert 1 <= gate.peak <= engine.concurrency, (
        f"peak in-flight was {gate.peak}, outside 1..{engine.concurrency}"
    )
    assert gate.in_flight <= gate.peak


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
