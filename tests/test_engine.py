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
from bigdata_mcp.errors import QueueFull
from bigdata_mcp.executor import Deadline
from bigdata_mcp.executor import ExecResult
from bigdata_mcp.executor import FakeExecutor
from tests.support.argv import COUNT_ARGV

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


async def test_the_retry_hint_reports_the_hold_the_queue_actually_had() -> None:
    """§8.2 row 4 promises a retry-after, and it used to always promise 0.0.

    `run` released its slot without the hold duration, so the queue's `_wait_s`
    stayed at zero and `QueueFull.retry_after_s` — the number in the message a
    refused caller reads — was `0.0` for every refusal. "Come back now" is the
    one answer guaranteed to be refused, which is worse than saying nothing.

    Three calls, because the hint is a *mean over completed holds*: the first
    has to finish holding before any refusal can quote it, and a refusal needs a
    slot someone is still holding. Two of the three are the test's own
    sequencing and one is the event under test, which is why the fake gates its
    second call open rather than racing the scheduler.

    The clock is the engine's own and is advanced between the two: the hold is
    measured with it, so a `FakeClock` is what makes the number deterministic
    rather than wall-clock-dependent.
    """
    clock = FakeClock(0.0)
    gates = [asyncio.Event(), asyncio.Event()]
    arrived = [asyncio.Event(), asyncio.Event()]
    calls: list[str] = []

    class Gated(FakeExecutor):
        """A fake whose calls hold their queue slot until the test lets them go."""

        @override
        async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
            """Announce the call, then wait on its own gate.

            Two events per call rather than one because the test has to know the
            call *arrived* before it can advance the clock behind it, and a
            single event set by the fake is indistinguishable from the one the
            test is still waiting on.

            Args:
                argv: The command, unused — the calls are told apart by order,
                    because every one of them runs the same program.
                deadline: The engine's deadline, unused: the gate ends this call,
                    not the budget.

            Returns:
                The fake's usual result.
            """
            index = len(calls)
            calls.append(str(argv))
            arrived[index].set()
            await gates[index].wait()
            return await super().exec(argv, deadline)

    engine = PoliteEngine(
        Gated(), edge_cores=8, budget_s=100.0, queue_cap=1, clock=clock
    )

    async def scenario() -> str | None:
        """Complete one held call, hold a second, and refuse a third.

        Returns:
            The `QueueFull` message the refused caller was handed, or `None` if
                the queue admitted a caller its cap should have refused — a
                result the caller turns into a failed assertion rather than an
                exception raised from inside the `finally`.
        """
        first = asyncio.ensure_future(engine.run("first", COUNT_ARGV))
        await arrived[0].wait()
        clock.advance(7.0)
        gates[0].set()
        await first

        second = asyncio.ensure_future(engine.run("second", COUNT_ARGV))
        await arrived[1].wait()

        third = asyncio.ensure_future(engine.run("third", COUNT_ARGV))
        try:
            await third
        except QueueFull as exc:
            return str(exc)
        finally:
            gates[1].set()
            await second
        return None

    message = await scenario()

    assert message is not None, "a third caller was admitted to a queue capped at 1"
    assert "Retry after 7.0s" in message, (
        f"the refusal quoted a retry-after that was not the measured hold: {message}"
    )


async def test_a_spawn_failure_is_not_cached_for_the_full_ttl() -> None:
    """A caller that could not start is not an answer to serve for five minutes.

    `singleflight` shares a failure for `FAILURE_TTL_S = 1.0` because a backend
    that just failed to start will probably fail again, and the cache path was
    caching that same failure for the clamped 5-300 s -- so one missing binary on
    an edge host became an outage that outlived the host it failed on.

    A non-zero *exit code* is a real answer and is still cached, which is the
    other half of the assertion: the fix has to be about how the command ended,
    not about every result that is not a clean success.
    """
    failing_calls = 0
    refused_calls = 0

    class Failing(FakeExecutor):
        """A fake whose command never starts."""

        @override
        async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
            """Count the call and report a spawn failure.

            Args:
                argv: The command.
                deadline: The deadline, unused.

            Returns:
                A result carrying `spawn_failed`, the shape a missing binary
                    produces.
            """
            nonlocal failing_calls
            failing_calls += 1
            return ExecResult(exit_code=-1, spawn_failed=True)

    class SayingNo(FakeExecutor):
        """A fake whose command runs and answers with a non-zero status."""

        @override
        async def exec(self, argv: Sequence[str], deadline: Deadline) -> ExecResult:
            """Count the call and answer with exit 1.

            Args:
                argv: The command.
                deadline: The deadline, unused.

            Returns:
                A result carrying a non-zero exit code from a started process.
            """
            nonlocal refused_calls
            refused_calls += 1
            return ExecResult(exit_code=1)

    async def scenario() -> None:
        """Ask each engine twice, with both callers agreeing to use the cache."""
        failing_engine = PoliteEngine(Failing(), edge_cores=8)
        await failing_engine.run("k", COUNT_ARGV)
        await failing_engine.run("k", COUNT_ARGV)

        refused_engine = PoliteEngine(SayingNo(), edge_cores=8)
        await refused_engine.run("k", COUNT_ARGV)
        await refused_engine.run("k", COUNT_ARGV)

    await scenario()

    assert failing_calls == 2, (
        "a spawn failure was served from the cache to the second caller"
    )
    assert refused_calls == 1, (
        "a command that ran and answered with a non-zero status was not cached; "
        "that answer is real and must not be re-fetched"
    )


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
