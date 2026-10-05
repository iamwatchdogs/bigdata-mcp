"""Tests for the packaged ``bigdata-mcp`` console entry point.

Two properties, both of which failed in this repository at least once.

**1. The console-script target resolves to the function.** ``pyproject.toml``
binds ``bigdata-mcp`` to ``bigdata_mcp:main``, and the generated shim resolves
that as ``from bigdata_mcp import main`` -- the name on the **package**. While
``__init__.py`` was empty that fell through to the submodule fallback and bound
the *module object* to ``main``, so the console script died on its first
statement with ``TypeError: 'module' object is not callable``. Nothing caught it:
the build never imports the target, and ruff and ty read source rather than the
bound attribute.

**2. The ``__main__`` guard fires.** PyInstaller freezes this file and the
release smoke test runs it. ``runpy`` returns a namespace containing a callable
``main`` whether or not the guard exists, so a namespace-only check passes with
it deleted -- and once the guard exists it also raises ``SystemExit``, because the
dispatch's return value has to reach the shell.

These run in-process on purpose. A subprocess probe would have reproduced the
real resolution order, and did catch bug 1 originally, but a non-literal argv is
itself what static analysers flag as a command-injection risk (Bandit ``B603``,
Opengrep's ``dangerous-subprocess-use-audit``), and a test suite should not need
that argument to be made. ``inspect.isfunction`` on the bound package attribute
is just as sharp. Both are mutation-verified: emptying ``__init__.py`` fails the
first, deleting the guard fails the second.
"""

from __future__ import annotations

import importlib
import inspect
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import bigdata_mcp
from bigdata_mcp.main import CAPTURE_FIXTURES
from bigdata_mcp.main import main

ENTRY_POINT_MODULE = "bigdata_mcp.main"
ENTRY_POINT_SCRIPT = Path("src/bigdata_mcp/main.py")

#: What `main` returns for a subcommand it does not know.
EXIT_UNKNOWN_SUBCOMMAND = 2


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    msg = f"no pyproject.toml found in any parent of {__file__}"
    raise RuntimeError(msg)


def test_console_script_target_resolves_to_the_function() -> None:
    """Assert ``bigdata_mcp.main`` is the entry-point function, not a module.

    The submodule is imported first so the two can be compared: with nothing
    re-exporting it, importing it is precisely what left the module object bound
    to the name, so this degrades into a clean failure rather than an
    ``AttributeError`` when the re-export is removed again.
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

    # `Any` deliberately: typeshed models TraceFunction as a recursive alias a
    # narrower annotation cannot satisfy.
    def tracer(frame: Any, event: Any, arg: Any) -> Any:
        if event == "call" and frame.f_code.co_name == "main":
            calls.append((frame.f_code.co_filename, frame.f_code.co_firstlineno))
        if previous_tracer is not None:
            return previous_tracer(frame, event, arg)
        return None

    # `sys.argv` is set for the duration because the guard now dispatches on it.
    # Left alone it inherits pytest's own argv, which makes this test fail on the
    # first flag pytest happens to pass -- the test then reports a packaging fault
    # for a change in argument handling.
    previous_argv = sys.argv
    sys.argv = ["bigdata-mcp"]
    sys.settrace(tracer)
    try:
        with pytest.raises(SystemExit):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.settrace(previous_tracer)
        sys.argv = previous_argv

    # Assert the CALL, not the namespace: runpy yields a callable `main` either
    # way, so a namespace-only check passes with the guard deleted.
    observed = [where for where in calls if where[0] == str(script)]
    assert len(observed) == 1, (
        f"executing {script} as __main__ called a `main` defined in that file "
        f"{len(observed)} times ({observed!r}); expected exactly 1. Without the "
        'guard `if __name__ == "__main__": raise SystemExit(main())` the module '
        "would define a function and exit without running it, so the frozen binary "
        "would ship an entry point that does nothing"
    )


def test_the_entry_point_propagates_the_subcommand_exit_code() -> None:
    """Assert a subcommand's non-zero code becomes the process's exit code.

    The console-script shim is `sys.exit(main())`, so a subcommand that returns 4
    for "the target exists and you did not pass --force" is only useful to a script
    if 4 is what the shell sees. This drives the real dispatch rather than
    `capture.main` so the wiring between the two is what is under test.
    """
    # `sys.argv` is set for the same reason as in the test above, and this one
    # failed under xdist for exactly that: pytest's own argv was the subcommand,
    # so the assertion was about whichever flag the runner happened to pass.
    previous_argv = sys.argv
    sys.argv = ["bigdata-mcp", "not-a-subcommand"]
    try:
        with pytest.raises(SystemExit) as caught:
            runpy.run_path(str(_repo_root() / ENTRY_POINT_SCRIPT), run_name="__main__")
    finally:
        sys.argv = previous_argv

    assert caught.value.code == EXIT_UNKNOWN_SUBCOMMAND, (
        f"running the entry point as a script exited with {caught.value.code!r}; "
        "expected it to propagate the dispatch's exit code, since the generated "
        "console script is `sys.exit(main())` and would otherwise report success "
        "for a refused capture"
    )


def test_capture_fixtures_reaches_the_capture_cli(tmp_path: Path) -> None:
    """Assert `bigdata-mcp capture-fixtures` is dispatched, not merely accepted.

    Driven through `wrap-ssh` because it needs no socket and no subprocess: the
    point is that argv starting with `capture-fixtures` reaches `capture.main` and
    writes a fixture. A dispatcher that parsed the argument and did nothing would
    pass every other test in this file, which is why this one asserts the artefact.
    """
    out = tmp_path / "count.json"
    stdout_file = tmp_path / "out.txt"
    stdout_file.write_text("  1234\n", encoding="utf-8")

    code = main([
        CAPTURE_FIXTURES,
        "wrap-ssh",
        "--operation",
        "hdfs dfs -count",
        "--argv",
        '["hdfs","dfs","-count"]',
        "--stdout",
        str(stdout_file),
        "--exit-code",
        "0",
        "--provenance",
        "laptop -> edge-host",
        "--out",
        str(out),
    ])

    assert code == 0, f"capture exited {code}"
    assert out.is_file(), (
        "`bigdata-mcp capture-fixtures` returned without writing a fixture. The "
        "subcommand was parsed and then dropped, which is the failure mode of an "
        "entry point that accepts arguments it does not act on"
    )


def test_an_unknown_subcommand_is_refused_with_its_exit_code() -> None:
    assert main(["not-a-subcommand"]) == EXIT_UNKNOWN_SUBCOMMAND


def test_bare_invocation_says_what_this_build_can_do(capsys: Any) -> None:
    """A build without the server says so, rather than exiting 0 in silence."""
    code = main([])

    out = capsys.readouterr().out
    assert code == 0, f"bare invocation exited {code}"
    assert CAPTURE_FIXTURES in out, (
        f"bare invocation printed {out!r}, which does not mention "
        f"{CAPTURE_FIXTURES!r}; a command that exits 0 having said nothing looks "
        "like it worked"
    )
