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

import pytest

from tests.support.http_server import LocalHttpServer

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _no_inherited_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear `$BIGDATA_MCP_CONFIG` so a suite never inherits the developer's.

    `load_config()` with no argument falls back to the environment, so a
    contributor who has the variable exported — which is what running this server
    in production looks like — would change what `test_no_config_path_returns_
    defaults` measures, and a config that fails to load would fail the suite for
    a reason that has nothing to do with the code.

    Cleared rather than set to a fixed path, because the default state is what
    these tests are about. Tests that *do* want the variable set it themselves,
    after this fixture has run, and that is the whole of the interaction.
    """
    monkeypatch.delenv("BIGDATA_MCP_CONFIG", raising=False)


@pytest.fixture
def server() -> Iterator[LocalHttpServer]:
    """Return a started loopback HTTP server.

    Lives here rather than in a test module because two suites need it and a
    fixture is the one thing a test module cannot import from another -- so the
    second copy was a rename away from serving a stale class.

    Yields:
        A running `LocalHttpServer`, stopped when the test finishes.
    """
    with LocalHttpServer() as running:
        yield running


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
