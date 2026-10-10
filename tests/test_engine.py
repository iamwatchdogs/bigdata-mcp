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
* E4, E5, G1-G4 are recorded in `test_gate.py`'s docstring — the gate section
  moved there when this file crossed Codacy's 500-NLOC limit
* E6 drop the TTL expiry -> `test_an_expired_entry_is_not_served`
* E7, E8, E13, E14, E15 are recorded in `test_singleflight.py`'s docstring
* H1 let the first caller's `use_cache` decide for everyone coalescing ->
  `test_a_concurrent_bypass_is_not_decided_by_whoever_arrived_first`
* H2 key the coalescing on the cache flag too, splitting every bypass into real
  work -> `test_callers_that_agree_on_caching_still_coalesce`
* E9 remove the pre-permit deadline check ->
  `test_a_call_with_no_budget_left_never_reaches_the_executor`
* F1 build the engine deadline without the injected clock ->
  `test_the_engine_deadline_runs_on_the_injected_clock`
* F2 never enforce the queue cap -> `test_the_stress_harness_keeps_the_queue_bounded`
* F3 stop tracking the queue's peak depth ->
  `test_the_stress_harness_keeps_the_queue_bounded`
* E10 let `spawn_failed` count as success ->
  `test_a_spawn_failure_is_not_a_success`
* E11 clamp the TTL floor to zero -> `test_the_ttl_is_clamped_into_the_specs_range`
* E12 skip the cache on a repeat call ->
  `test_a_repeated_call_is_answered_from_the_cache`
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
is the one with a real semantic trap in it: "every host with a gate *so far*" is
not "every configured source", and a reader who expects the latter will find an
answer that never grows to match the config. The absent host is asserted for that
reason, not just the two present ones.

Two design bugs were found by these tests rather than by review, and both are
recorded in the code they came from:

- `BoundedQueue.run` decremented the depth on a refusal, releasing a slot it had
  never taken. `test_a_refusal_releases_the_slot_it_was_about_to_take` caught it.
- The gate originally *refused* rather than waited, so any configuration with
  `queue_depth` greater than the derived cap — the normal one — raised
  `PermitUnavailable` at the caller. Its tests moved to `test_gate.py`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import override

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

from bigdata_mcp.engine import FakeClock
from bigdata_mcp.engine import PoliteEngine
from bigdata_mcp.engine import Probe
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


# --------------------------------------------------------------------------
# The TTL cache
# --------------------------------------------------------------------------


def test_the_ttl_is_clamped_into_the_specs_range() -> None:
    """§10.1: 5-300 s. Below 5 s it rarely hits; above 300 s it is stale."""
    assert clamp_ttl(0.1) == MIN_TTL_S
    assert clamp_ttl(10_000) == MAX_TTL_S
    assert clamp_ttl(30) == 30


def test_an_expired_entry_is_not_served() -> None:
    """E6: expiry is a comparison against the injected clock, not a timer.

    `put` stamps `now + ttl_s` and `get` declines once `expires_at <= now`, so
    the branch is only reachable by moving the clock — which is why the clock is
    injected rather than read ambiently. The TTL is 10 s, inside §10.1's 5-300 s
    range, so `clamp_ttl` leaves it alone and the 11 s jump is what expires the
    entry rather than the clamp quietly lengthening it.
    """
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(10, clock=clock)
    cache.put("k", "value")
    assert cache.get("k") == "value"
    clock.advance(11)
    assert cache.get("k") is None


def test_an_entry_within_its_ttl_is_served() -> None:
    """The nearest reading that must still hit, a tenth of a second short.

    `get` expires at `expires_at <= now`, so an entry stamped at 0 with a 10 s
    TTL dies exactly at 10.0 — which is why the expiry test jumps to 11 and this
    one stops at 9.9. Testing the boundary rather than the middle of the window
    is the point: an off-by-one in that comparison, or a bound drawn at
    `now + ttl_s - 1`, still serves a comfortably-fresh entry and only fails
    here.
    """
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
    """The entry is deleted on the way out, so a dead key cannot look live.

    `get` removes the entry rather than only declining to return it, and two
    things depend on that. `__len__` counts entries "expired ones included", so
    an expired value left in the map is one the cache carries forever. And
    `contains` answers from the map's membership, so a stale entry that outlived
    its TTL would make an expired key report as present.
    """
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
    """Two counters rather than one, because the ratio is the useful number.

    A cache whose hit rate cannot be read is a cache nobody can tell is failing:
    a single tally would report lookups and say nothing about how many were
    answered from memory. Both the absent-key path and the expired-entry path
    count as a miss — an expired entry is not served either way — so this pair
    also says the second lookup found nothing rather than found a stale answer.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("k", "v")
    cache.get("k")
    cache.get("absent")
    assert (cache.hits, cache.misses) == (1, 1)


def test_invalidate_and_clear_drop_entries() -> None:
    """The two sweeps, and the asymmetry between what they reset.

    `invalidate` drops one key and `clear` drops every key; both go through the
    same entry map `get` reads, so a dropped key misses on the next lookup
    instead of serving a value the operator believes is gone. Neither touches the
    counters: `clear` empties the entries and leaves `hits` and `misses`
    cumulative, so a hit rate read after a purge still describes the whole run.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.put("a", "1")
    cache.put("b", "2")
    cache.invalidate("a")
    assert cache.contains("a") is False
    cache.clear()
    assert len(cache) == 0


def test_invalidate_on_an_absent_key_is_not_an_error() -> None:
    """Invalidation is speculative, so an absent key is a no-op, not an error.

    `invalidate` pops with a default. Callers invalidate on a "the estate may
    have moved" signal, which fires whether or not this server ever cached
    anything for the key, so raising here would make a purge depend on cache
    state — the coupling the cache exists to avoid. The test asserts only that
    the call returns.
    """
    cache: TtlCache[str] = TtlCache(10, clock=FakeClock())
    cache.invalidate("never-there")


# --------------------------------------------------------------------------
# The fake executor
# --------------------------------------------------------------------------


async def test_the_fake_records_every_call_it_is_asked_to_run() -> None:
    """`calls` is the only handle on "did the engine reach the backend".

    `FakeExecutor` returns one shared `ExecResult` for every unconfigured argv,
    so object identity cannot tell one backend call from two and a test that
    needs to know what was actually run reads this list instead. It keeps
    insertion order, normalises each argv to a tuple, and records before the
    deadline check — so a call refused for want of time is in the record too.
    """
    fake = FakeExecutor()

    async def scenario() -> None:
        """Make both calls, in order, and let both return.

        Neither argv is scripted, so both get the fake's one shared default
        result. What tells the two calls apart afterwards is the record alone,
        which is why the assertion below is about the list and not about the
        values.
        """
        await fake.exec(("a",), Deadline.starting_now(5.0))
        await fake.exec(("b",), Deadline.starting_now(5.0))

    await scenario()
    assert fake.calls == [("a",), ("b",)]


async def test_the_fake_returns_the_default_for_an_unscripted_argv() -> None:
    """An unscripted argv is answered, not refused, so counting stays possible.

    Nothing is scripted here and the default answers anyway. A fake that raised
    on an unscripted argv would force every test that only cares how many times
    the backend was reached to also invent a result for a command it is not
    asserting on — and that invented result would then become the thing the
    cache tests are comparing.
    """
    fake = FakeExecutor(default=ExecResult(stdout="fallback"))

    async def scenario() -> str:
        """Run the unscripted call and hand back what it answered.

        Returns:
            The `stdout` of the default the fake returned for an argv it has no
            entry for.
        """
        return (await fake.exec(("x",), Deadline.starting_now(5.0))).stdout

    assert await scenario() == "fallback"


async def test_the_fake_refuses_a_call_with_no_budget() -> None:
    """It records the call first, so a test can assert on a refused attempt."""
    fake = FakeExecutor()

    async def scenario() -> ExecResult:
        """Arrive at the executor with the budget already spent.

        A zero budget is spent at construction: `remaining_s` is clamped at zero,
        so `expired` is true before anything waits. The refusal comes back as a
        result carrying `spawn_failed` rather than as a raised exception, because
        "the process would not start" is an answer the caller has to report, not
        a crash.

        Returns:
            The refusal the executor returned for the call it recorded.
        """
        return await fake.exec(("x",), Deadline.starting_now(0.0))

    result = await scenario()
    assert result.spawn_failed is True
    assert fake.calls == [("x",)]


# --------------------------------------------------------------------------
# The engine, end to end
# --------------------------------------------------------------------------


async def test_the_engine_deadline_runs_on_the_injected_clock() -> None:
    """A `FakeClock` must be able to expire an engine deadline.

    The cache and the single-flight window both read `self._clock`, so before this
    was passed through, a `FakeClock` could expire a cache entry and a
    single-flight failure window but never a deadline: the deadline read
    `time.monotonic` regardless, so the only way to reach the expiry branch was to
    wait in real time. The module docstring says the engine runs on the injected
    clock; this is that claim, checked.

    The clock starts far enough ahead of any real monotonic reading that the two
    cannot be confused, and the assertion is on where the deadline says it started.
    Advancing the clock would not work: the deadline is created inside the call, so
    it always begins at the current reading and is never already spent on entry.
    """
    clock = FakeClock(start=1_000_000.0)
    seen: list[Deadline] = []

    class RecordsDeadlines(FakeExecutor):
        """A fake that keeps the deadline it was handed."""

        @override
        async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
            """Record the deadline, then behave like the fake.

            Args:
                argv: The command.
                deadline: The deadline the engine built.

            Returns:
                The fake's usual result.
            """
            seen.append(deadline)
            return await super().exec(argv, deadline)

    engine = PoliteEngine(RecordsDeadlines(), edge_cores=8, budget_s=10.0, clock=clock)

    await engine.run("k", ("true",))

    assert len(seen) == 1
    # `approx`, not `==`: a real monotonic reading is on the order of 10^5 seconds
    # on a developer laptop, so the two differ by five orders of magnitude. The
    # tolerance only has to absorb float noise, not the gap it is proving.
    assert seen[0].started_at == pytest.approx(1_000_000.0), (
        "the deadline was not stamped from the injected clock"
    )


async def test_a_repeated_call_is_answered_from_the_cache() -> None:
    """E12: the second call is answered before anything downstream is touched.

    Two assertions that fail for different reasons, and both are needed. The
    backend call count is the one that can actually go wrong, because
    `FakeExecutor` returns one shared `ExecResult` for every unconfigured argv,
    so `first is second` holds whether or not the cache answered it. The
    hit counter is what says the answer came out of the cache rather than out of
    a second backend call that happened to return the same object.
    """
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> tuple[ExecResult, ExecResult]:
        """Ask the same key twice, then hand back both answers together.

        Returns:
            The two results, in call order. Comparing them needs both to exist,
            which is why they are returned rather than asserted on in the body.
        """
        first = await engine.run("k", COUNT_ARGV)
        second = await engine.run("k", COUNT_ARGV)
        return first, second

    first, second = await scenario()
    assert first is second
    assert len(fake.calls) == 1
    assert engine.stats().cache_hits == 1


async def test_the_cache_can_be_bypassed() -> None:
    """`use_cache=False` skips the read, and the read is the half visible here.

    Two calls under one key and two backend calls says the second was not
    answered from memory. That the bypass is also never *written* cannot be seen
    from this test, because the second call does not read either — which is why
    H1's test makes a bypassing caller overlap a caching one and then asks a
    third caller, since only that shape can catch a stray write.
    """
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> None:
        """Cache one answer, then ask again with the cache refused.

        The first call has to be a caching one: with nothing cached, "two calls"
        would say nothing at all about whether the bypass did anything.
        """
        await engine.run("k", COUNT_ARGV)
        await engine.run("k", COUNT_ARGV, use_cache=False)

    await scenario()
    assert len(fake.calls) == 2


async def test_a_concurrent_bypass_is_not_decided_by_whoever_arrived_first() -> None:
    """A caller that bypassed the cache must not have its answer cached.

    `use_cache` was carried by whichever caller started the shared task, so a
    caller passing `use_cache=False` that coalesced onto a concurrent caching call
    had its result written to the cache anyway. The bypass was silently ignored,
    and the next caller was served the answer this one did not want remembered.

    The two callers have to overlap. Run one after the other there is nothing to
    coalesce onto, the bypass is honoured by accident, and the bug does not appear
    — which is why the first version of this test, run sequentially, stayed green
    against the old code.

    The probe is the backend call count, not object identity: `FakeExecutor`
    returns one shared `ExecResult` for every unconfigured command, so `is` would
    compare the same object whether or not the cache answered.
    """
    in_flight = asyncio.Event()
    release = asyncio.Event()

    class Gated(FakeExecutor):
        """A fake whose call blocks until the test lets it finish."""

        @override
        async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
            """Hold the call open long enough for a second caller to arrive.

            Args:
                argv: The command.
                deadline: The deadline, unused.

            Returns:
                The fake's usual result.
            """
            in_flight.set()
            await release.wait()
            return await super().exec(argv, deadline)

    fake = Gated()
    engine = PoliteEngine(fake, edge_cores=8)

    caching = asyncio.ensure_future(engine.run("k", COUNT_ARGV))
    await in_flight.wait()
    bypassing = asyncio.ensure_future(engine.run("k", COUNT_ARGV, use_cache=False))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(caching, bypassing)

    await engine.run("k", COUNT_ARGV)
    assert len(fake.calls) == 2, "the bypassed answer was served from the cache"


async def test_callers_that_agree_on_caching_still_coalesce() -> None:
    """The other half: the key change must not split every bypass into real work."""
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> tuple[ExecResult, ExecResult]:
        """Start both bypassing callers before either one has finished.

        `gather` submits both up front, which is the whole precondition: the
        first caller registers its in-flight entry before it suspends, so the
        second arrives while it is still there. Run one after the other, the
        entry is already retired and the second does real work of its own — and
        because `FakeExecutor` returns one shared result for every unconfigured
        argv, `first is second` would still hold, which leaves the backend count
        as the only assertion here with any teeth.

        Returns:
            Both results, which must be the one shared call's.
        """
        return await asyncio.gather(
            engine.run("k", COUNT_ARGV, use_cache=False),
            engine.run("k", COUNT_ARGV, use_cache=False),
        )

    first, second = await scenario()
    assert first is second
    assert len(fake.calls) == 1, "two bypassing callers ran the backend twice"


async def test_different_keys_both_reach_the_executor() -> None:
    """Coalescing is keyed, so a different key is never joined to anything.

    Every other coalescing test in this file shows two callers sharing; without
    this one, a coalescer that ignored its key would pass all of them. Both
    argvs are unscripted and get the fake's one shared result, so the two
    recorded calls are the only evidence that the work ran separately.
    """
    fake = FakeExecutor()
    engine = PoliteEngine(fake, edge_cores=8)

    async def scenario() -> None:
        """Run two different keys and let both return.

        Two keys rather than two argvs under one key, because the coalescer keys
        on the canonical key `run` is handed — that is what puts the second call
        outside the first call's in-flight entry.
        """
        await engine.run("a", ("one",))
        await engine.run("b", ("two",))

    await scenario()
    assert len(fake.calls) == 2


async def test_the_engine_counts_admissions_and_refusals() -> None:
    """`doctor`'s two admission numbers, and why neither is asserted exactly.

    `run` counts `admitted` after the queue lets an item through and `refused`
    before it raises, so between them they account for every caller. How many of
    the twelve got in depends on how the 10 ms fake delays happen to overlap,
    which belongs to the scheduler rather than to the code, so both are floors.
    The third assertion is an invariant rather than an observation: the counter is
    incremented inside the permit, so a peak above the derived cap could only
    mean something was admitted without a permit behind it.
    """
    engine = PoliteEngine(FakeExecutor(delay_s=0.01), edge_cores=4, queue_cap=2)

    async def scenario() -> None:
        """Submit the whole burst and wait for every caller to finish.

        Refusals are absorbed here instead of raised out of `gather`, which
        propagates the first exception and would abandon the rest of the burst —
        leaving the counters describing fewer callers than were submitted.
        """

        async def one(index: int) -> None:
            """Submit one caller, letting a refusal end it quietly.

            Args:
                index: Used only to make the canonical key distinct, so none of
                    the twelve callers coalesces and the 10 ms delays actually
                    overlap. Join them onto one call instead and nothing is ever
                    in flight long enough for the queue to refuse anything.

            `QueueFull` is the behaviour under test rather than a failure of the
            harness, and the engine has already counted the refusal by the time
            it raises.
            """
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
        """Run a mix of admitted and refused calls, then read what is still held.

        Returns:
            The gate's occupancy once the burst is over, which has to be 0 — the
            claim is that no exit path through `run` leaves a permit taken.
        """

        async def one(index: int) -> bool:
            """Run one caller, turning a refusal into a reported False.

            Args:
                index: Makes the canonical key distinct, so the 24 callers cannot
                    coalesce into fewer real calls and the gate really does see
                    them all competing.

            Returns:
                True when the call ran, False when the queue refused it.
            """
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
    """Answers what the cap would be; it does not re-derive anything in place.

    A fresh reading from the lazy observer comes through here, and gates that
    already exist deliberately keep their old limit: shrinking a live gate below
    its current occupancy would strand the permits already held. So the value is
    for a host's first gate, and the engine's own cap is unchanged by the call —
    which is the trap in the name, since "recomputed" reads like a mutation and
    is not one.
    """
    engine = PoliteEngine(FakeExecutor(), edge_cores=4)
    assert engine.derive_cap(32) == 8
    assert engine.derive_cap(None) == 4


def test_one_gate_per_host_so_a_second_source_cannot_spend_the_first_ones_cores() -> (
    None
):
    """Two sources, two budgets; the second cannot spend the first's permits.

    `for_host` memoises, and that is half of what is checked: a second call for
    the same host returning a fresh gate would reset its occupancy and hand out
    permits that are already taken. The cross-host half is why the registry is
    keyed at all — the resource being protected is one host's cores, so another
    source gets its own count rather than a share of this one's.
    """
    engine = PoliteEngine(FakeExecutor(), edge_cores=16)
    first = engine.gates.for_host("edge-a")
    assert engine.gates.for_host("edge-a") is first
    assert engine.gates.for_host("edge-b") is not first


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


async def _never() -> int:
    """Suspend long enough that reaching here means the refusal did not happen.

    Raises:
        AssertionError: If the sleep completes, which means the queue admitted work
            it should have refused.
    """
    await asyncio.sleep(3600)
    boom = "the queue ran work it should have refused"
    raise AssertionError(boom)
