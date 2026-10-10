"""Pytest configuration for this suite.

One hook: run `async def` tests. No plugin dependency.

The project deliberately takes no async pytest plugin. Every async test in this
suite drives an event loop with a handful of awaits, and a plugin would buy
auto-discovery at the cost of a mode-selection rule that can silently skip a test
when the mode is not configured. A test that silently skips is worse than an
explicit `run_async` call, so this hook is the whole mechanism and it has one job.

Returning `None` for a synchronous test hands control back to pytest's default
path, so this changes nothing about the 500-odd tests that are not async.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable


def pytest_pyfunc_call(
    pyfuncitem: Any,
) -> bool | None:
    """Run an async test on a fresh event loop.

    Args:
        pyfuncitem: The collected test item.

    Returns:
        True when this hook ran the test, so pytest must not run it again. `None`
        for a synchronous test, which hands control back to the default path.
    """
    test_function: Callable[..., Any] = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None
    argnames = pyfuncitem._fixtureinfo.argnames  # ruff: ignore[private-member-access] — the hook has no public accessor
    kwargs = {name: pyfuncitem.funcargs[name] for name in argnames}
    asyncio.run(test_function(**kwargs))
    return True
