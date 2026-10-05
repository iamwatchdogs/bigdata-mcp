"""A bounded queue with a depth cap and reject-fast admission.

§10.1's second component, and the one with the most obvious wrong answers.

The rule is *reject-fast*: when the queue is full, the caller is refused with an
actionable error rather than the work being queued. The alternative — an unbounded
queue — converts "this host is busy" into "this process grows until it is killed",
and it does so while every individual call still looks like it is progressing.

Refusing also has to be *actionable*, which is why `QueueFull` (§8.2 row 4) carries
the depth, the cap, and a retry-after: a model that gets "queue full" with no
numbers cannot do anything except retry immediately, which is precisely the
behaviour that keeps a queue full.

Depth is counted including the item being admitted, so a cap of 16 means "sixteen
items in flight or waiting", not "sixteen waiting plus one running". The off-by-one
that gets this wrong lets the queue hold one more than it promised.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import TypeVar

from bigdata_mcp.errors import QueueFull

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

T = TypeVar("T")

DEFAULT_QUEUE_DEPTH = 16


@dataclass(slots=True)
class Admission:
    """What the queue decided about one item.

    Attributes:
        admitted: Whether the item was accepted.
        depth: Depth at the moment of the decision, including this item. Reported
            on refusal too, so the error can say what it is competing with.
        cap: The configured cap.
        retry_after_s: A hint. Derived from the observed service rate rather than
            a constant, because a constant is wrong on every host except the one
            it was guessed on.
    """

    admitted: bool
    depth: int
    cap: int
    retry_after_s: float = 0.0

    def refuse(self) -> QueueFull:
        """Build the refusal this admission implies.

        Returns:
            A `QueueFull` carrying the depth, cap, and retry-after.
        """
        return QueueFull(
            current_depth=self.depth,
            cap=self.cap,
            retry_after_s=self.retry_after_s,
        )


class BoundedQueue[T]:
    """Admission control with a hard depth cap.

    Attributes:
        cap: The maximum depth, counting both queued and running items.
        _depth: Current depth.
        _wait_s: Total seconds spent holding items, used to derive the retry hint.
        _completed: How many items have finished, the other half of that rate.
    """

    def __init__(self, cap: int = DEFAULT_QUEUE_DEPTH) -> None:
        """Build a queue.

        Args:
            cap: Maximum depth. Must be at least 1, because a queue that admits
                nothing is a server that answers nothing.

        Raises:
            ValueError: If `cap` is below 1.
        """
        if cap < 1:
            message = f"queue cap must be at least 1, got {cap}"
            raise ValueError(message)
        self.cap = cap
        self._depth = 0
        self._wait_s = 0.0
        self._completed = 0
        self._release = asyncio.Event()
        self._release.set()

    @property
    def depth(self) -> int:
        """Current depth."""
        return self._depth

    @property
    def available(self) -> int:
        """How many more items could be admitted right now."""
        return max(0, self.cap - self._depth)

    def _retry_after_s(self) -> float:
        """Estimate how long a caller should wait.

        Returns:
            Mean observed service time, or 0.0 when nothing has completed yet.
            Mean rather than minimum: the minimum is the best case observed so far
            and would tell a caller to come back sooner than the host can actually
            serve them.
        """
        if self._completed == 0 or self._wait_s <= 0.0:
            return 0.0
        return round(self._wait_s / self._completed, 3)

    def admit(self) -> Admission:
        """Try to admit one item.

        Returns:
            The decision. `admitted` is False when the queue is at its cap; nothing
            was queued in that case.
        """
        if self._depth >= self.cap:
            return Admission(
                admitted=False,
                depth=self._depth,
                cap=self.cap,
                retry_after_s=self._retry_after_s(),
            )
        self._depth += 1
        self._release.clear()
        return Admission(admitted=True, depth=self._depth, cap=self.cap)

    def release(self, *, waited_s: float = 0.0) -> None:
        """Release one item's slot.

        Args:
            waited_s: How long the item held its slot, for the retry hint. Ignored
                if negative, since a clock that went backwards would otherwise
                produce a negative retry-after — a *negative* wait is an
                instruction to come back sooner than the previous attempt, which
                is the opposite of what a full queue should say.
        """
        self._depth = max(0, self._depth - 1)
        self._completed += 1
        if waited_s > 0.0:
            self._wait_s += waited_s
        if self._depth == 0:
            self._release.set()

    async def run(self, work: Callable[[], Awaitable[T]]) -> T:
        """Admit one item, run `work`, and release the slot on every path.

        Args:
            work: A zero-argument callable returning the awaitable to run. Called
                only after admission succeeds, so a refused item never executes.

        Returns:
            Whatever `work` returned.

        A full queue raises `QueueFull` without having taken a slot, so a caller
        that catches this does not have to know it was holding one.
        """
        admission = self.admit()
        if not admission.admitted:
            raise admission.refuse()
        started = asyncio.get_running_loop().time()
        try:
            return await work()
        finally:
            held = asyncio.get_running_loop().time() - started
            self.release(waited_s=held)


@dataclass(slots=True)
class Counters:
    """The engine's running totals.

    Attributes:
        admitted: Items that passed admission.
        refused: Items refused at the cap.
        in_flight: Items currently executing.
        peak_in_flight: The highest `in_flight` seen. Kept because "never exceeded
            the cap" and "the cap was ever reached" are different claims and only
            one of them means the cap is load-bearing.
    """

    admitted: int = 0
    refused: int = 0
    in_flight: int = 0
    peak_in_flight: int = 0
    extras: dict[str, float] = field(default_factory=dict)

    def snapshot(self) -> dict[str, float]:
        """Render the counters as a flat mapping.

        Returns:
            The counters plus any extras, for an envelope's `meta` or a test.
        """
        return {
            "admitted": self.admitted,
            "refused": self.refused,
            "in_flight": self.in_flight,
            "peak_in_flight": self.peak_in_flight,
            **self.extras,
        }


__all__ = ["DEFAULT_QUEUE_DEPTH", "Admission", "BoundedQueue", "Counters"]
