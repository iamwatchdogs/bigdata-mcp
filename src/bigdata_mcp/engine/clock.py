"""A clock the engine can be tested against without sleeping.

§10.1's TTL cache and §10.2's staleness check are both time-dependent, and a test
that sleeps to prove them is a slow test that will be skipped under load. The
clock is therefore an injected value, not a call to `time.monotonic` scattered
through the engine — which is also what §4.3 mandate 2's "explicit values, not
ambient context" implies one layer down.

`time-machine` is available and verified working on this interpreter (§18.2), and
is used where the test needs the *real* clock to move. `FakeClock` is used where it
does not, because it needs to move in both directions at once: forward to expire a
TTL entry, backward to test that a future-dated entry is still fresh.
"""

from __future__ import annotations

import time
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A monotonic time source.

    Monotonic, not wall clock, on purpose: a TTL that a clock adjustment could
    extend is a TTL that can be made to live forever.
    """

    def now(self) -> float:
        """Return the current reading.

        Returns:
            Seconds from an unspecified but fixed origin. Only differences are
            meaningful.
        """
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> float:
        """Return `time.monotonic()`.

        Returns:
            Seconds from an unspecified fixed origin.
        """
        return time.monotonic()


class FakeClock:
    """A clock that moves only when a test moves it.

    Attributes:
        _seconds: The current reading.

    Movable in both directions on purpose: a cache entry stamped in the future
    must still be fresh, and no amount of sleeping will produce that state
    without making the test take as long as the future offset.
    """

    def __init__(self, start: float = 0.0) -> None:
        """Start the clock at a chosen reading.

        Args:
            start: The initial reading. `0.0` makes the arithmetic in a test read
                as plain elapsed seconds.
        """
        self._seconds = start

    def now(self) -> float:
        """Return the current reading.

        Returns:
            The reading this clock is holding.
        """
        return self._seconds

    def advance(self, seconds: float) -> None:
        """Move the clock forward.

        Args:
            seconds: How far to move. May be negative to move backwards.
        """
        self._seconds += seconds


__all__ = ["Clock", "FakeClock", "SystemClock"]
