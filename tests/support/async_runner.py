"""Run an async callable from a synchronous test.

The project deliberately does not take an async pytest plugin. Every async test in
this suite drives a real socket with a handful of awaits, so a plugin would buy
auto-discovery and cost a dependency plus a mode-selection rule that can silently
skip a test. A test that silently skips is worse than a test that is three lines
longer.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

T = TypeVar("T")


def run_async(  # ruff: ignore[non-pep695-generic-function] -- see engine/singleflight.py
    coro: Coroutine[Any, Any, T],
) -> T:
    """Run a coroutine to completion on a fresh event loop.

    Args:
        coro: The coroutine to run. Must not already be scheduled.

    Returns:
        Whatever the coroutine returned.
    """
    return asyncio.run(coro)
