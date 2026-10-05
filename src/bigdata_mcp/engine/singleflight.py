"""The TTL cache and the single-flight coalescer.

§10.1 names both, and they are here together because they answer the same question
from two directions: *an agent re-asking the same expensive call while it reasons*
is the dominant pattern, and the fix is either to answer from memory (the cache) or
to make the duplicate calls share one backend call (single-flight).

**The cache** is per-operation and keyed on a canonical key. TTLs are clamped to
5-300 s: below 5 s the cache rarely hits, and above 300 s an agent's second
question is answered from data the estate has already moved on from. A cache that
returns a stale answer confidently is worse than no cache at all.

**Single-flight** coalesces. N concurrent identical calls become one backend call,
and the outcome — success *or* failure — goes to all N. Sharing the failure matters
more than sharing the success: N waiters each retrying on their own after the same
refusal is a thundering herd aimed at a backend that has already said no once. A
failure is therefore shared for a short window, because a backend that failed a
moment ago will probably fail again immediately and there is nothing to gain by
asking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import TypeVar

from bigdata_mcp.engine.clock import Clock
from bigdata_mcp.engine.clock import SystemClock
from bigdata_mcp.errors import BigDataMcpError

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

T = TypeVar("T")

MIN_TTL_S = 5.0
MAX_TTL_S = 300.0

#: How long a *failure* is shared. Shorter than a success's TTL on purpose: a
#: backend that just failed will probably fail again, and caching that failure for
#: five minutes turns a transient blip into a five-minute outage.
FAILURE_TTL_S = 1.0


def clamp_ttl(seconds: float) -> float:
    """Clamp a TTL into §10.1's 5-300 s range.

    Args:
        seconds: The requested TTL.

    Returns:
        The clamped value. Both bounds are real: below 5 s the cache rarely hits,
        and above 300 s an agent's second question is answered from data the
        estate has already moved on from.
    """
    return max(MIN_TTL_S, min(MAX_TTL_S, seconds))


@dataclass(frozen=True, slots=True)
class Entry[T]:
    """One cached value.

    Attributes:
        value: What was cached.
        expires_at: The clock reading at which this stops being an answer.
    """

    value: T
    expires_at: float


class TtlCache[T]:
    """A per-operation cache with a clamped TTL.

    Attributes:
        ttl_s: The clamped TTL.
    """

    def __init__(self, ttl_s: float = MIN_TTL_S, *, clock: Clock | None = None) -> None:
        """Build a cache.

        Args:
            ttl_s: The requested TTL; clamped into range.
            clock: The time source. Defaults to the real monotonic clock.
        """
        self.ttl_s = clamp_ttl(ttl_s)
        self._clock: Clock = clock or SystemClock()
        self._entries: dict[str, Entry[T]] = {}
        self._hits = 0
        self._misses = 0

    @property
    def hits(self) -> int:
        """Lookups served from the cache."""
        return self._hits

    @property
    def misses(self) -> int:
        """Lookups that had to call through."""
        return self._misses

    def get(self, key: str) -> T | None:
        """Look a key up.

        Args:
            key: The canonical key.

        Returns:
            The cached value, or `None` on a miss or an expired entry. `None` is
            also a legitimate cached value, so a `None` return is not proof of a
            miss — `contains` is.
        """
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if entry.expires_at <= self._clock.now():
            del self._entries[key]
            self._misses += 1
            return None
        self._hits += 1
        return entry.value

    def contains(self, key: str) -> bool:
        """Whether a live entry exists for a key.

        Args:
            key: The canonical key.

        Returns:
            True when an unexpired entry is present. An expired one is dropped on
            the way out, so a stale entry cannot keep a key looking live.
        """
        self.get(key)
        return key in self._entries

    def put(self, key: str, value: T, *, ttl_s: float | None = None) -> None:
        """Store a value.

        Args:
            key: The canonical key.
            value: The value.
            ttl_s: An override TTL for this entry, clamped the same way. `None`
                uses the cache's own TTL.
        """
        ttl = self.ttl_s if ttl_s is None else clamp_ttl(ttl_s)
        self._entries[key] = Entry(value=value, expires_at=self._clock.now() + ttl)

    def invalidate(self, key: str) -> None:
        """Drop one key.

        Args:
            key: The canonical key. An absent key is ignored rather than an error,
                because invalidation is usually speculative.
        """
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop every entry."""
        self._entries.clear()

    def __len__(self) -> int:
        """How many entries are held, expired ones included.

        Returns:
            `len(self._entries)`.
        """
        return len(self._entries)


class SharedFailure(BigDataMcpError):
    """A recent failure for this key is still being shared.

    Raised instead of re-running work that already failed inside the sharing
    window, so N waiters do not become N simultaneous retries.

    Attributes:
        key: The canonical key whose failure was shared.
    """

    def __init__(self, key: str) -> None:
        """Record which key's failure is being shared.

        Args:
            key: The canonical key.
        """
        message = (
            f"{key!r} failed inside the last {FAILURE_TTL_S:.0f}s and is still "
            "being shared; retrying now would be a thundering herd"
        )
        super().__init__(message)
        self.key = key


class SingleFlight[T]:
    """Coalesces concurrent identical work onto one backend call.

    Attributes:
        coalesced: How many callers shared a call rather than starting their own.
            The number a test asserts on to prove coalescing happened, as opposed to
            happening to happen.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        """Build a coalescer.

        Args:
            clock: The time source. Defaults to the real monotonic clock.
        """
        self._in_flight: dict[str, asyncio.Task[T]] = {}
        self._failures: dict[str, float] = {}
        self._clock: Clock = clock or SystemClock()
        self.coalesced = 0

    @property
    def in_flight(self) -> int:
        """How many distinct keys are being served right now."""
        return len(self._in_flight)

    @property
    def shared_failures(self) -> int:
        """How many keys are currently inside their failure window."""
        return len(self._failures)

    async def do(self, key: str, work: Callable[[], Awaitable[T]]) -> T:
        """Run `work` once per key, sharing the outcome with every caller.

        Args:
            key: The canonical key. Two callers passing equal keys share a call;
                two passing different keys never do.
            work: A zero-argument callable returning the awaitable to run. Called
                at most once per concurrent group.

        Returns:
            The shared result, re-raised for every waiter when it is an exception.

        Raises:
            SharedFailure: If the same key failed inside the sharing window.
        """
        if self._within_window(key):
            raise SharedFailure(key)

        existing = self._in_flight.get(key)
        if existing is not None:
            self.coalesced += 1
            return await asyncio.shield(existing)

        task: asyncio.Task[T] = asyncio.ensure_future(work())
        self._in_flight[key] = task
        try:
            return await asyncio.shield(task)
        except Exception:
            self._failures[key] = self._clock.now() + FAILURE_TTL_S
            raise
        finally:
            if self._in_flight.get(key) is task:
                del self._in_flight[key]

    def _within_window(self, key: str) -> bool:
        """Whether a key failed recently enough to share.

        Args:
            key: The canonical key.

        Returns:
            True inside the window. An expired entry is dropped on the way out, so
            a stale failure cannot keep a key suppressed forever.
        """
        until = self._failures.get(key)
        if until is None:
            return False
        if until <= self._clock.now():
            del self._failures[key]
            return False
        return True

    def forget_failures(self) -> None:
        """Clear the shared-failure window."""
        self._failures.clear()


__all__ = [
    "FAILURE_TTL_S",
    "MAX_TTL_S",
    "MIN_TTL_S",
    "Entry",
    "SharedFailure",
    "SingleFlight",
    "TtlCache",
    "clamp_ttl",
]
