"""Behaviour tests for the packaged ``bigdata-mcp`` console entry point.

``pyproject.toml`` binds the console script to the target
``bigdata_mcp:main``. That target is resolved by the generated entry-point shim
as ``from bigdata_mcp import main``, so it binds the name ``main`` on the
**package**, not the name ``main`` inside the module ``bigdata_mcp.main``.

While ``src/bigdata_mcp/__init__.py`` was a 0-byte file the package exported
nothing, so the ``from ... import`` fell through to its submodule fallback:
Python imported ``bigdata_mcp.main``, bound the *module object* to the name
``main`` on the package, and the console script died on its own first
statement with::

    TypeError: 'module' object is not callable

Nothing else in the repository noticed. The build succeeded, because packaging
never imports the target. ``ruff`` and ``ty`` stayed silent, because both read
the source rather than the bound attribute. The existing test suite imported
``main`` through ``from bigdata_mcp.main import main``, which reaches the
function by a different route than the console script takes, so an in-process
assertion of that form would still have passed with the bug in place.

The regression is therefore only observable at two boundaries, and both are
asserted here:

- the in-process attribute contract, in
  ``test_console_script_target_resolves_to_a_callable``;
- the subprocess boundary the real console script crosses, in
  ``test_installed_console_script_survives_a_subprocess``.

The remaining tests pin the contract that surrounds that target: the declared
public surface, the ``None`` return, the typed signature, and the
``__main__`` guard that both the PyInstaller freeze and the release pipeline's
smoke test execute.
"""

from __future__ import annotations

import importlib
import inspect
import runpy

# This module has to launch a real interpreter, so `subprocess` is unavoidable.
# Every call below uses a fixed argv, `shell=False`, and no interpolated input.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from typing import Any
from typing import get_type_hints

import pytest

import bigdata_mcp

ENTRY_POINT_MODULE = "bigdata_mcp.main"
ENTRY_POINT_SCRIPT = Path("src/bigdata_mcp/main.py")
EXPECTED_PUBLIC_API = ["main"]
EXPECTED_RETURN_ANNOTATION = type(None)
# Every accepted spelling of "this returns None" that `inspect.signature` can
# hand back, because `src/bigdata_mcp/main.py` does NOT import
# `from __future__ import annotations`. With that import absent the annotation
# is evaluated at definition time and arrives as the real `NoneType`, whereas a
# stringified `-> "None"` or an explicit `-> type(None)` arrives as written.
ACCEPTED_RETURN_ANNOTATIONS = (None, "None", EXPECTED_RETURN_ANNOTATION)
# The two substrings the pre-fix console script printed on stderr. Asserting
# they are absent is weaker than asserting the return code alone, because it
# pins the specific regression rather than "something went wrong".
BROKEN_ENTRY_POINT_MARKERS = ("TypeError", "object is not callable")
# Byte-for-byte the body of the console script that ``[project.scripts]``
# generates, minus the `sys.exit()` wrapper and the `.exe`/`.pyw` argv fixups,
# which cannot affect which object the name ``main`` resolves to. The naive
# `import bigdata_mcp; bigdata_mcp.main()` spelling is deliberately NOT used
# here: against the 0-byte `__init__.py` it raises `AttributeError`, because a
# package never resolves a not-yet-imported submodule by attribute access, so
# it would not reproduce the bug it is meant to catch.
CONSOLE_SCRIPT_PROBE = "from bigdata_mcp import main; main()"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Locate the repository root by walking up from this test file.

    Returns:
        The first ancestor directory of this file that holds a
        ``pyproject.toml``, which is this repository's root.

    Raises:
        RuntimeError: If no ancestor directory holds a ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    msg = f"no pyproject.toml found in any parent of {__file__}"
    raise RuntimeError(msg)


def test_console_script_target_resolves_to_a_callable() -> None:
    """Assert ``bigdata_mcp.main`` is the entry-point function, not a module.

    This is the exact contract ``[project.scripts]`` depends on, and the exact
    one the 0-byte ``__init__.py`` broke. The module is imported first, both to
    compare the two against each other and to reproduce the pre-fix state
    faithfully: with nothing re-exporting, importing the submodule is precisely
    what left the module object bound to the name, so this assertion degrades
    into a clean failure rather than an ``AttributeError`` when the re-export is
    removed again.
    """
    module = importlib.import_module(ENTRY_POINT_MODULE)

    assert callable(bigdata_mcp.main), (
        f"bigdata_mcp.main resolved to {type(bigdata_mcp.main).__name__} "
        f"({bigdata_mcp.main!r}), which is not callable. The console script "
        "declared as 'bigdata-mcp = \"bigdata_mcp:main\"' does "
        "`from bigdata_mcp import main` and then `sys.exit(main())`, so a "
        "non-callable binding crashes the installed entry point with "
        f"{BROKEN_ENTRY_POINT_MARKERS[0]}: '{BROKEN_ENTRY_POINT_MARKERS[1]}'"
    )

    assert inspect.isfunction(bigdata_mcp.main), (
        f"bigdata_mcp.main is callable but is a "
        f"{type(bigdata_mcp.main).__name__}, not a plain function, so the "
        "console script would call a wrapper whose behaviour is not the one "
        f"declared in {ENTRY_POINT_MODULE}"
    )

    assert bigdata_mcp.main is module.main, (
        f"bigdata_mcp.main is {bigdata_mcp.main!r} but "
        f"{ENTRY_POINT_MODULE}.main is {module.main!r}. The package attribute "
        "has drifted away from the function the module defines, so the "
        "re-export is no longer the single source of truth for the entry point"
    )


def test_package_declares_main_as_its_only_public_symbol() -> None:
    """Assert ``__all__`` is exactly ``["main"]``.

    The re-export is a deliberate published surface, not an incidental import.
    A second entry in ``__all__`` widens what ``from bigdata_mcp import *``
    exposes, and dropping ``main`` undeclares the console-script target, so
    either drift has to break here rather than at a consumer's ``ImportError``.
    """
    assert bigdata_mcp.__all__ == EXPECTED_PUBLIC_API, (
        f"bigdata_mcp.__all__ is {bigdata_mcp.__all__!r}, expected "
        f"{EXPECTED_PUBLIC_API!r}. Widening it changes what "
        "`from bigdata_mcp import *` publishes; dropping 'main' undeclares the "
        f"name bound by the console-script target {ENTRY_POINT_MODULE!r}"
    )


def test_entry_point_is_callable_and_returns_none() -> None:
    """Assert calling the re-exported entry point returns ``None``.

    ``sys.exit(main())`` turns the return value into the process exit status, so
    a non-``None`` return would silently turn a healthy run into a failing one.
    """
    # The Bandit suppression marker below is deliberately ID-less. Codacy reports
    # this line for "assigning the result of a function that has no return", but
    # that check does not exist in the Bandit this repository runs (1.9.4 has no
    # such plugin), and naming an ID Bandit does not know prints a warning on
    # every single run. An ID-less marker is version-independent, and the line
    # does exactly one thing, so blanket suppression on it carries little risk.
    #
    # The check is a false positive regardless: it reads main()'s `-> None`
    # annotation as a guarantee, and an annotation is not enforced at runtime.
    # This is the only check that catches main() starting to return a value while
    # the console script still wraps it in sys.exit(). Proved it can fail:
    # injecting `return 7` into main() fails this test.
    #
    # Prose in a comment above a marker must not spell the marker out, or Bandit
    # parses the rest of the sentence as a list of check names and warns about
    # every word in it.
    result = bigdata_mcp.main()  # nosec

    assert result is None, (
        f"bigdata_mcp.main() returned {result!r} "
        f"({type(result).__name__}), expected None. The console script wraps "
        "this call in sys.exit(), so any non-None value becomes the process "
        "exit status and a successful start would be reported as a failure"
    )


def test_entry_point_signature_is_annotated_as_returning_none() -> None:
    """Assert ``main`` is annotated ``-> None`` and takes no parameters.

    The module carries no ``from __future__ import annotations``, so the
    annotation is resolved at definition time and the raw value
    ``inspect.signature`` reports is the real ``NoneType``. The raw value is
    still checked against every accepted spelling, because a stringified
    annotation is legal and would otherwise be silently accepted by
    ``get_type_hints`` alone.

    The empty parameter list is asserted for the coverage contract, not for
    taste: the ``if __name__ == \"__main__\": main()`` guard is the only
    statement the suite never executes, so the module must not also contain
    unexecuted parameter handling.
    """
    signature = inspect.signature(bigdata_mcp.main)

    assert signature.return_annotation in ACCEPTED_RETURN_ANNOTATIONS, (
        f"bigdata_mcp.main has return annotation "
        f"{signature.return_annotation!r}, which resolves to neither None nor "
        "the string 'None'. The entry point is documented and typed as "
        "returning nothing, and sys.exit() propagates this value as the "
        "process exit status"
    )

    resolved = get_type_hints(bigdata_mcp.main)
    assert "return" in resolved, (
        f"get_type_hints(bigdata_mcp.main) resolved to {resolved!r} with no "
        "'return' entry, so the declared return type carries no information"
    )
    assert resolved["return"] is EXPECTED_RETURN_ANNOTATION, (
        f"get_type_hints resolved the return annotation of bigdata_mcp.main to "
        f"{resolved['return']!r}, expected "
        f"{EXPECTED_RETURN_ANNOTATION!r} -- the annotations module cannot be "
        "reconciled with the declared signature"
    )

    assert not signature.parameters, (
        f"bigdata_mcp.main takes {list(signature.parameters)!r}, expected no "
        "parameters. The console script calls main() with no arguments, so any "
        "required parameter would break the entry point, and any optional one "
        "would add a branch this suite cannot execute and cover"
    )


def test_entry_point_module_executes_as_a_script(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert ``main.py`` runs to completion under ``run_name=\"__main__\"``.

    ``runpy.run_path`` with ``run_name=\"__main__\"`` is the same shape of
    execution PyInstaller performs when it freezes this file, and the same
    shape the release pipeline's smoke test runs. Reaching the ``runpy`` return
    value at all is the assertion: a ``TypeError`` from a shadowed ``main``, a
    ``SystemExit`` with a non-zero status, or any import-time error would abort
    the call before it returns. ``__name__`` is then checked to be exactly
    ``\"__main__\"`` because that is the single condition under which the guard
    fires -- ``runpy`` otherwise executes the same file under a synthetic
    module name and the guard would never run.
    """
    monkeypatch.chdir(repo_root)
    script = repo_root / ENTRY_POINT_SCRIPT
    assert script.is_file(), f"entry-point script is missing: {script}"

    calls: list[tuple[str, int]] = []
    previous_tracer = sys.gettrace()

    # Parameter and return types are `Any` deliberately: typeshed models
    # TraceFunction as a recursive alias, which a narrower annotation here
    # cannot satisfy without casts that would obscure what the tracer does.
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

    observed = [where for where in calls if where[0] == str(script)]
    assert observed, (
        f"executing {script} under __name__ == "
        f"{namespace.get('__name__')!r} never called a function named `main` "
        f'defined in that file. The guard `if __name__ == "__main__": '
        "main()` is missing, or does not invoke the entry point, so the module "
        "and the frozen binary would define a function and exit without ever "
        "running it -- the console script would exit 0 having done nothing. "
        "This asserts the CALL rather than the returned namespace: runpy "
        "returns __name__ and a callable main whether or not the guard exists, "
        f"so a namespace-only check passes with the guard deleted ({calls!r})"
    )
    assert len(observed) == 1, (
        f"the guard called main() {len(observed)} times ({observed!r}); a "
        "script entry point must call it exactly once"
    )

    assert namespace["__name__"] == "__main__", (
        f"the entry point executed with __name__ "
        f"{namespace['__name__']!r}, expected '__main__'. runpy was asked for "
        f"run_name='__main__', so anything else means the "
        f'`if __name__ == "__main__": main()` guard in {script} never fired'
    )

    assert callable(namespace["main"]), (
        f"executing {script} left namespace['main'] bound to "
        f"{type(namespace['main']).__name__} "
        f"({namespace['main']!r}), which the guard cannot call"
    )

    assert namespace["main"] is not bigdata_mcp.main, (
        f"the executed {script} produced the same function object as the "
        "already-imported package attribute, so the file body did not actually "
        "run and the namespace proves nothing"
    )


def test_installed_console_script_survives_a_subprocess(
    repo_root: Path,
) -> None:
    """Assert the console-script binding runs cleanly in a fresh interpreter.

    This is the only assertion here that crosses the same boundary the real bug
    did. An in-process ``from bigdata_mcp.main import main`` reaches the
    function by submodule lookup and stayed green while the installed console
    script crashed, because ``from bigdata_mcp import main`` is a different
    resolution: it reads the package attribute and only falls back to the
    submodule when the package exports nothing. Running a fresh interpreter
    with the console script's own body is what makes the smoke assertion in the
    release pipeline mean something.

    Args:
        repo_root: The repository root, used as the subprocess working
            directory so the import resolves against the same editable
            install the test session is using.
    """
    # The argv is three fixed elements: the interpreter, a literal "-c", and a
    # module-level constant holding the probe. `cwd` is a repository-root path
    # the test resolves itself; it is the working directory rather than part of
    # the command, and it never reaches a shell because `shell` is not used.
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]  # nosec B603
        [sys.executable, "-c", CONSOLE_SCRIPT_PROBE],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, (
        f"the console script body {CONSOLE_SCRIPT_PROBE!r} exited "
        f"{completed.returncode} in a fresh interpreter instead of 0. "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )

    for marker in BROKEN_ENTRY_POINT_MARKERS:
        assert marker not in completed.stderr, (
            f"stderr of the console script body contains {marker!r}, which is "
            "the signature of a 'bigdata_mcp:main' target that resolved to the "
            f"module object rather than the function. Full stderr: "
            f"{completed.stderr!r}"
        )
