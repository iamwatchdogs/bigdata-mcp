"""The polite engine: the five components of §10.1, wired in order.

    admission → bounded queue → per-host semaphore → executor
    + TTL cache + single-flight + hard timeouts

The order is the design. Admission rejects before anything is allocated, so a
refused request costs one dict lookup. The queue bounds what is *waiting*. The
semaphore bounds what is *running on the edge host*. The cache answers without
reaching any of them. Single-flight means a duplicate does not reach the semaphore
at all — it joins the call already holding a permit.

That last point is why the cache is consulted first and single-flight second: the
cache answers the repeated question outright, and single-flight answers the
*simultaneous* one. Reversing them would put a permit behind a wait for every
duplicate, which is the opposite of the point.

Every backend call is bounded by a `Deadline` value (§4.3 mandate 2), and the
deadline is threaded — never read from a global, never ambient. That is what makes
"a permit is released on every exit path" a thing a test can assert rather than a
thing a reviewer has to trust.

`Engine` holds no state that outlives a call, so it is safe to share across the
event loop's tasks; it is not safe to share across threads, and it does not claim
to be.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import TypeVar

from bigdata_mcp.engine.clock import Clock
from bigdata_mcp.engine.clock import SystemClock
from bigdata_mcp.engine.queue import DEFAULT_QUEUE_DEPTH
from bigdata_mcp.engine.queue import BoundedQueue
from bigdata_mcp.engine.queue import Counters
from bigdata_mcp.engine.semaphore import HostGates
from bigdata_mcp.engine.semaphore import derive_concurrency
from bigdata_mcp.engine.singleflight import MIN_TTL_S
from bigdata_mcp.engine.singleflight import SingleFlight
from bigdata_mcp.engine.singleflight import TtlCache
from bigdata_mcp.executor import Deadline
from bigdata_mcp.executor import DeadlineExceeded

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable
    from collections.abc import Sequence

    from bigdata_mcp.executor import ExecResult
    from bigdata_mcp.executor import Executor

#: Only `gather_bounded` is generic, and it is generic because the stress tests use
#: it to return whatever their factory returns. Nothing else in the engine is.
T = TypeVar("T")

#: The §16 default. Must undercut the client's 60 s so a clean error is returned
#: rather than the connection being severed (§3).
DEFAULT_BUDGET_S = 20.0


@dataclass(frozen=True, slots=True)
class EngineStats:
    """What the engine has done, for `doctor` and for tests.

    Attributes:
        cache_hits: Calls answered without touching the executor.
        cache_misses: Calls that had to go through.
        coalesced: Calls that joined another call rather than starting one.
        admitted: Items that passed the queue's cap.
        refused: Items rejected at the cap.
        peak_in_flight: The highest number of permits held at once.
        concurrency: The derived per-host cap.
    """

    cache_hits: int
    cache_misses: int
    coalesced: int
    admitted: int
    refused: int
    peak_in_flight: int
    concurrency: int


class PoliteEngine:
    """The five components, in the order §5.1's diagram puts them.

    Args:
        executor: The command runner. A `FakeExecutor` here is how every adapter
            gets developed before an estate exists (§4.3 mandate 4).
        host: The edge host key, i.e. the configured alias.
        edge_cores: Cores on that host, or `None` when the observer could not read
            them.
        budget_s: Per-call budget. Must stay under the client's 60 s.
        cache_ttl_s: Cache TTL; clamped into §10.1's range.
        queue_cap: Queue depth cap.
        clock: The time source.

    Attributes:
        gates: The per-host concurrency gates.
        queue: The bounded admission queue.
        cache: The TTL cache.
        flight: The single-flight coalescer.
    """

    def __init__(
        self,
        executor: Executor,
        *,
        host: str = "edge",
        edge_cores: int | None = None,
        budget_s: float = DEFAULT_BUDGET_S,
        cache_ttl_s: float = MIN_TTL_S,
        queue_cap: int = DEFAULT_QUEUE_DEPTH,
        clock: Clock | None = None,
    ) -> None:
        """Build an engine. Nothing is running yet."""
        self.executor = executor
        self.host = host
        self.budget_s = budget_s
        self._clock: Clock = clock or SystemClock()
        self.gates = HostGates(edge_cores)
        self.queue: BoundedQueue[ExecResult] = BoundedQueue(queue_cap)
        self.cache: TtlCache[ExecResult] = TtlCache(cache_ttl_s, clock=self._clock)
        self.flight: SingleFlight[ExecResult] = SingleFlight(clock=self._clock)
        self._counters = Counters()

    @property
    def concurrency(self) -> int:
        """The derived per-host cap."""
        return self.gates.limit

    def stats(self) -> EngineStats:
        """Render the running totals.

        Returns:
            The counters, including the cache and single-flight figures.
        """
        gate = self.gates.for_host(self.host)
        return EngineStats(
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            coalesced=self.flight.coalesced,
            admitted=self._counters.admitted,
            refused=self._counters.refused,
            peak_in_flight=max(self._counters.peak_in_flight, gate.peak),
            concurrency=self.concurrency,
        )

    def derive_cap(self, edge_cores: int | None) -> int:
        """Recompute the cap for a new core reading.

        Args:
            edge_cores: A fresh core count, or `None` if the observer lost it.

        Returns:
            The cap that would apply. Gates already created keep their old limit,
            because shrinking a live gate below its current occupancy would strand
            the permits already held; a new host picks the new value up.
        """
        return derive_concurrency(edge_cores)

    async def run(
        self,
        key: str,
        argv: Sequence[str],
        *,
        use_cache: bool = True,
    ) -> ExecResult:
        """Run one command under the whole policy.

        Args:
            key: The canonical key. Two calls with equal keys and equal `use_cache`
                coalesce onto one backend call.
            argv: The command. Never a shell string (§11.1).
            use_cache: Whether the TTL cache may answer this call, and whether the
                result is written back to it.

        Returns:
            The result.

        A full queue raises `QueueFull` — nothing was allocated and the executor was
        never reached — and an exhausted budget raises `DeadlineExceeded`.

        Every caller takes a queue slot, including the ones that go on to coalesce
        onto another caller's work. That over-counts rather than under-counts, which
        is the right direction: the depth cap then bounds *callers*, and executor
        concurrency is bounded by the host gate as well as by this queue. Making the
        slot conditional on not coalescing would require knowing whether a
        concurrent call exists before admitting, which is the question admission
        control exists to answer.
        """
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        admission = self.queue.admit()
        if not admission.admitted:
            self._counters.refused += 1
            raise admission.refuse()
        self._counters.admitted += 1

        # `use_cache` is part of the coalescing key, not just of the caller's
        # intent. A caller that asked for the cache to be bypassed would otherwise
        # join a task whose caching was decided by whoever arrived first, and its
        # result would be written to the cache anyway — so the bypass would be
        # silently ignored, and a later caller would then be served the answer that
        # caller did not want cached. Two callers that disagree here do not
        # coalesce, which costs one extra backend call and keeps the flag honest.
        flight_key = key if use_cache else f"{key}\0nocache"
        try:
            return await self.flight.do(
                flight_key, lambda: self._guarded(key, argv, use_cache=use_cache)
            )
        finally:
            self.queue.release()

    async def _guarded(
        self,
        key: str,
        argv: Sequence[str],
        *,
        use_cache: bool,
    ) -> ExecResult:
        """Take a permit, honour the deadline, cache, and always release.

        Args:
            key: The canonical key, for the cache write.
            argv: The command.
            use_cache: Whether to write the result into the cache.

        Returns:
            The result.

        Raises:
            DeadlineExceeded: If the budget is already spent when the permit is
                taken. Checking *before* acquiring matters: holding a permit for
                work that cannot run is a permit held for nothing.
        """
        gate = self.gates.for_host(self.host)
        # The engine's clock, not `time.monotonic`. The cache and single-flight
        # both read `self._clock`, so a deadline reading the wall clock instead
        # means a `FakeClock` can expire a cache entry but never a deadline — and
        # the module docstring promises the engine runs entirely on the injected
        # clock. `Clock.now` and `Deadline` both come from `time.monotonic` by
        # default, so passing it changes nothing in production.
        deadline = Deadline.starting_now(self.budget_s, clock=self._clock.now)
        if deadline.expired:
            raise DeadlineExceeded(argv[0] if argv else key, self.budget_s)

        async with gate.permit():
            self._counters.in_flight += 1
            self._counters.peak_in_flight = max(
                self._counters.peak_in_flight,
                self._counters.in_flight,
            )
            try:
                result = await self.executor.exec(argv, deadline)
            finally:
                self._counters.in_flight -= 1

        if use_cache:
            self.cache.put(key, result)
        return result


class Probe:
    """A one-shot measurement of what the edge host can take.

    Separate from the engine so the observer (§10.2) can measure without running
    work, and so a measurement cannot itself consume a permit — a probe that
    queued behind the work it is measuring would report the queue, not the host.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        """Build a probe.

        Args:
            clock: The time source.
        """
        self._clock: Clock = clock or SystemClock()
        self.last_at: float | None = None

    def stale(self, max_age_s: float) -> bool:
        """Whether the last sample is older than the staleness bound.

        Args:
            max_age_s: `observer_staleness_s` from §16.

        Returns:
            True when there is no sample at all, or the sample is older than the
            bound. §10.2 is explicit that refresh is lazy: a stdio server may sit
            idle for hours and is killed without warning, so there is no background
            timer to keep a sample fresh.
        """
        if self.last_at is None:
            return True
        return (self._clock.now() - self.last_at) >= max_age_s

    def record(self) -> None:
        """Record that a sample was just taken."""
        self.last_at = self._clock.now()


# `T = TypeVar("T")` rather than PEP 695's `def gather_bounded[T]`: Codacy's SAST
# parser predates PEP 695, so the newer syntax is a file it cannot read at all.
# See engine/singleflight.py.
async def gather_bounded(  # ruff: ignore[non-pep695-generic-function] -- see engine/singleflight.py
    factory: Callable[[], Awaitable[T]],
    count: int,
) -> list[T]:
    """Run `count` callables concurrently, for stress tests.

    Args:
        factory: A zero-argument callable returning a fresh awaitable each time.
        count: How many to run.

    Returns:
        The results in submission order.

    Lives here because §17.2's stress harness needs a way to generate concurrency
    that does not itself be the thing under test, and `asyncio.gather` on a
    generator is not obviously that.
    """
    return list(await asyncio.gather(*(factory() for _ in range(count))))


__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_QUEUE_DEPTH",
    "EngineStats",
    "PoliteEngine",
    "Probe",
    "gather_bounded",
]
