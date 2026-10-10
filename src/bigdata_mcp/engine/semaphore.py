"""Per-host concurrency: the derived cap, and the semaphore that enforces it.

§10.1 is explicit that the cap is **derived from the edge host, not hardcoded**, and
the reasoning is worth keeping: an 8-core shared node caps at 2-3 while a 32-core
dev box reaches 6-8. A single constant would be wrong for one of them and would not
know which.

The derivation is a clamp, and the clamp bounds are themselves the point:

- a floor of 2, because a cap of 1 makes the server strictly serial and turns
  latency into throughput loss for no politeness gain;
- a ceiling of 8, because past that the edge host is the bottleneck and more
  concurrency only deepens the queue it already has;
- `cores / 2`, because the resource being protected is the *edge host's* cores
  while the consumer is the JVM each `hdfs dfs` spawns on it.

The semaphore **waits**; it does not refuse. That is not a detail: §10.1 splits
refusal and bound across two components — the bounded queue rejects, the semaphore
bounds. A semaphore that refused would make `queue_depth` and the concurrency cap
the same control twice, and with the wrong precedence: the queue is the coarser
bound and must be able to admit more than the semaphore admits, or the two
contradict each other the moment `queue_depth` exceeds the derived cap, which is
the normal configuration.

Waiting here is still bounded, because the queue in front of it is bounded.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import TracebackType

MIN_CONCURRENCY = 2
MAX_CONCURRENCY = 8

#: The per-host cap used when the edge host's core count is not known. 4 is the
#: middle of the clamp's range, and it is deliberately the *middle* rather than the
#: floor: an unknown host should not be treated as the weakest possible one, and it
#: must not be treated as the strongest either.
FALLBACK_CONCURRENCY = 4


def derive_concurrency(
    edge_cores: int | None,
    *,
    minimum: int = MIN_CONCURRENCY,
    maximum: int = MAX_CONCURRENCY,
) -> int:
    """Derive the per-host concurrency cap from the edge host's cores.

    Args:
        edge_cores: Cores on the edge host, or `None` when the observer could not
            read them. `None` is the observer's `unsupported` case arriving here.
        minimum: The floor. A cap of 1 would serialise the server entirely.
        maximum: The ceiling. Past this, more concurrency only deepens a queue the
            edge host already has.

    Returns:
        `clamp(edge_cores // 2, minimum, maximum)`, or `FALLBACK_CONCURRENCY` when
        the core count is unknown.

    A core count below one is treated as unknown rather than clamped, because a
    reported `0` cores is a broken observation and clamping it to the floor would
    silently convert "I could not measure" into "this host is weak".
    """
    if edge_cores is None or edge_cores < 1:
        return FALLBACK_CONCURRENCY
    return max(minimum, min(maximum, edge_cores // 2))


class HostGate:
    """A counting gate for one edge host.

    Attributes:
        limit: The derived cap.
        in_flight: Permits currently held. Exposed so a test can assert the
            invariant rather than inferring it from timing.
        peak: The highest occupancy seen. A cap that is never reached is a cap that
            is not doing anything, and a test that only checks "never exceeded"
            cannot tell the two apart.
    """

    def __init__(self, limit: int) -> None:
        """Build a gate.

        Args:
            limit: The maximum concurrent permits.

        Raises:
            ValueError: If `limit` is below 1. A gate nobody can pass is a denial
                of service against ourselves.
        """
        if limit < 1:
            message = f"HostGate limit must be at least 1, got {limit}"
            raise ValueError(message)
        self.limit = limit
        self.in_flight = 0
        self.peak = 0
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def available(self) -> int:
        """Permits still free."""
        return max(0, self.limit - self.in_flight)

    def __len__(self) -> int:
        """The configured cap.

        Returns:
            `limit`.
        """
        return self.limit

    def permit(self) -> Permit:
        """Return a permit scope that waits for a slot.

        Returns:
            A permit scope. Acquiring may wait; the wait is bounded because the
            queue in front of this gate is bounded.
        """
        return Permit(self)


class Permit:
    """One host permit, released exactly once whatever happens inside it.

    A leaked permit is how a server degrades into a single-threaded one that still
    looks healthy, so the release lives in `__aexit__` and nothing else in the
    engine has to remember it.

    Attributes:
        _gate: The gate this permit belongs to.
    """

    def __init__(self, gate: HostGate) -> None:
        """Wrap a gate's semaphore.

        Args:
            gate: The gate. The permit is acquired on `__aenter__`, not here, so
                constructing one cannot leak a slot.
        """
        self._gate = gate

    async def __aenter__(self) -> None:
        """Wait for a permit and record the occupancy.

        The permit is held when this returns.
        """
        await self._gate._semaphore.acquire()  # ruff: ignore[private-member-access] — a Permit owns its gate
        self._gate.in_flight += 1
        self._gate.peak = max(self._gate.peak, self._gate.in_flight)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the permit.

        Args:
            exc_type: The in-flight exception's type, if any.
            exc: The in-flight exception, if any.
            traceback: The in-flight traceback, if any.
        """
        self._gate.in_flight -= 1
        self._gate._semaphore.release()  # ruff: ignore[private-member-access] — a Permit owns its gate


class HostGates:
    """One gate per host, created on first use.

    The edge host is not known at construction — §3 has it as a configured alias —
    so the gates are keyed and created lazily. A per-host map rather than one
    global gate, because §10.2 protects *one* host's cores and a second source
    must not be able to spend them.

    Attributes:
        limit: The cap every gate here uses.
    """

    def __init__(self, edge_cores: int | None) -> None:
        """Build the registry.

        Args:
            edge_cores: Cores on the edge host, or `None` when unknown.
        """
        self.limit = derive_concurrency(edge_cores)
        self._gates: dict[str, HostGate] = {}

    def for_host(self, host: str) -> HostGate:
        """Return the gate for a host, creating it if this is the first use.

        Args:
            host: The host key, e.g. the configured edge alias.

        Returns:
            The host's gate.
        """
        existing = self._gates.get(host)
        if existing is not None:
            return existing
        created = HostGate(self.limit)
        self._gates[host] = created
        return created

    def hosts(self) -> Iterable[str]:
        """Every host with a gate so far.

        Returns:
            The host keys.
        """
        return tuple(self._gates)


__all__ = [
    "FALLBACK_CONCURRENCY",
    "MAX_CONCURRENCY",
    "MIN_CONCURRENCY",
    "HostGate",
    "HostGates",
    "Permit",
    "derive_concurrency",
]
