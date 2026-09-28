"""Tests for the packaged ``bigdata-mcp`` console entry point.

Two properties, both of which failed in this repository at least once.

**1. The console-script target resolves to the function.** ``pyproject.toml``
binds ``bigdata-mcp`` to ``bigdata_mcp:main``, which the generated shim resolves
as ``from bigdata_mcp import main`` -- the name on the **package**, not the name
inside the module ``bigdata_mcp.main``.

While ``src/bigdata_mcp/__init__.py`` was empty, that import fell through to its
submodule fallback and bound the *module object* to ``main``, so the console
script died on its first statement with
``TypeError: 'module' object is not callable``. Nothing caught it: the build
never imports the target, and ``ruff`` and ``ty`` read source rather than the
bound attribute.

**2. The ``__main__`` guard fires.** PyInstaller freezes this file, and the
release pipeline's smoke test runs it. ``runpy`` returns a namespace containing a
callable ``main`` whether or not the guard exists, so a namespace-only check
passes with the guard deleted -- the module would define a function and exit
without running it.

These run in-process on purpose. A subprocess probe was the obvious way to
reproduce the real resolution order, and it was what caught bug 1 originally,
but ``subprocess.run`` with a non-literal argv is itself the thing static
analysers flag as a command-injection risk (Bandit ``B603``, and the
``dangerous-subprocess-use-audit`` rule). The argv here is three fixed elements
and ``shell`` is never used, so there is nothing to inject -- but a test suite
should not need that argument to be made. ``inspect.isfunction`` on the bound
package attribute distinguishes a function from a module just as sharply, with
no process spawn at all. Both properties are verified by mutation: emptying
``__init__.py`` fails the first, deleting the guard fails the second.
"""

from __future__ import annotations

import importlib
import inspect
import runpy
import sys
from pathlib import Path
from typing import Any

import bigdata_mcp

ENTRY_POINT_MODULE = "bigdata_mcp.main"
ENTRY_POINT_SCRIPT = Path("src/bigdata_mcp/main.py")


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    msg = f"no pyproject.toml found in any parent of {__file__}"
    raise RuntimeError(msg)


def test_console_script_target_resolves_to_the_function() -> None:
    """Assert ``bigdata_mcp.main`` is the entry-point function, not a module.

    The submodule is imported first so the two can be compared: with nothing
    re-exporting it, importing the submodule is precisely what left the module
    object bound to the name, so this assertion degrades into a clean failure
    rather than an ``AttributeError`` when the re-export is removed again.
    """
    module = importlib.import_module(ENTRY_POINT_MODULE)

    assert inspect.isfunction(bigdata_mcp.main), (
        f"bigdata_mcp.main resolved to {type(bigdata_mcp.main).__name__} "
        f"({bigdata_mcp.main!r}), not a function. The console script declared as "
        "'bigdata-mcp = \"bigdata_mcp:main\"' does `from bigdata_mcp import main` "
        "and then `sys.exit(main())`, so a module object crashes the installed "
        "entry point with \"TypeError: 'module' object is not callable\". This "
        f"means {ENTRY_POINT_MODULE} is not re-exported from the package."
    )

    assert bigdata_mcp.main is module.main, (
        f"bigdata_mcp.main is {bigdata_mcp.main!r} but {ENTRY_POINT_MODULE}.main "
        f"is {module.main!r}. The package attribute has drifted from the function "
        "the module defines, so the re-export is no longer the single source of "
        "truth for the entry point"
    )


def test_entry_point_module_executes_as_a_script() -> None:
    """Assert running ``main.py`` as ``__main__`` actually calls ``main``."""
    repo_root = _repo_root()
    script = repo_root / ENTRY_POINT_SCRIPT
    assert script.is_file(), f"entry-point script is missing: {script}"

    calls: list[tuple[str, int]] = []
    previous_tracer = sys.gettrace()

    # Parameter and return types are `Any` deliberately: typeshed models
    # TraceFunction as a recursive alias a narrower annotation cannot satisfy.
    def tracer(frame: Any, event: Any, arg: Any) -> Any:
        if event == "call" and frame.f_code.co_name == "main":
            calls.append((frame.f_code.co_filename, frame.f_code.co_firstlineno))
        if previous_tracer is not None:
            return previous_tracer(frame, event, arg)
        return None

    sys.settrace(tracer)
    try:
        namespace = runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.settrace(previous_tracer)

    # Assert the CALL, not the namespace: runpy yields a callable `main` either
    # way, so a namespace-only check passes with the guard deleted.
    observed = [where for where in calls if where[0] == str(script)]
    assert len(observed) == 1, (
        f"executing {script} under __name__ == {namespace.get('__name__')!r} "
        f"called a `main` defined in that file {len(observed)} times "
        f"({observed!r}); expected exactly 1. Without the guard "
        f'`if __name__ == "__main__": main()` the module would define a '
        "function and exit without running it, so the frozen binary would ship "
        "an entry point that does nothing"
    )
