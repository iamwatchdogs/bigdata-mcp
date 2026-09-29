"""Fail-closed Bandit gate for the commit stage.

Bandit's exit status cannot be the gate, for the same reason Codacy's cannot.
On 1.9.4 the CLI decides with `results_count(...) > 0` and nothing else, so a
file it never managed to read contributes no result and no failure. Measured
against this repository's own config: a tree whose only Python file has a syntax
error exits **0**; a file at mode `000` exits **0**, and so does the same file at
`644` once readable again; a non-UTF-8 file is skipped; a real
`subprocess.run(..., shell=True)` gives a B602 finding at `644` and nothing at
`000`. `-q` is not the cause -- without it Bandit prints `Files skipped (1):` at
INFO and still exits 0.

`check-ast` does not cover the gap: it sees only the files in the commit, while
this scan has `pass_filenames: false` and covers all three roots at once, so a
file broken by a merge -- syntax intact in every individual parent -- is exactly
the case it misses.

The skip list is not lost, only off the exit path. `-f json` writes every skipped
file to the top-level `errors` key, which `bandit/formatters/json.py` fills
directly from `manager.get_skipped()`; this reads it and fails on a non-empty
list. Hence two runs: the bare command, so the report a developer reads is
unchanged, and a second with `-f json` for the skip list and the finding count.
Bandit exits 1 for any nonzero finding count, so its status is not a count and
must never be printed as one. The pair costs about 0.25s.

Bandit is called as a bare name rather than `sys.executable -m bandit` because
Opengrep's `dangerous-subprocess-use-audit` rule exempts a literal argv and
reports anything else -- the `sys.executable` form included. pre-commit prepends
the hook's venv `bin` to `PATH` via its own `get_env_patch`, so the bare name and
the `shutil.which` above resolve to the same copy and cannot disagree about which
binary is meant. That is also why an absent `bandit` cannot raise here: `which`
reports it as a stated failure first, and a child's nonzero exit plus an empty
report is read as unknown.

**What this does not cover.** `errors` is initialised `[]` and filled in a
separate loop, so an upstream refactor that stopped populating it would look
exactly like a scan that skipped nothing. The missing-key case is rejected; the
starved-key case is indistinguishable from clean by any consumer of this format.
That is a property of Bandit's report, not a choice here, and it is recorded so
nobody reads a green line as stronger evidence than it is.

Refusing to run is a failure, not a pass: Bandit exiting 2, or JSON this cannot
read, means the skip list is unknown, and unknown is not clean.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from typing import Any
from typing import cast

# Explicit roots rather than a bare directory: that is Bandit's only exclusion
# lever, and every finding inside `.venv` is about a third-party package.
ROOTS = ["src", "scripts", "tests"]

# Read from the same file the hook lints, so the reviewed skip list in
# `[tool.bandit]` and this gate can never describe different configurations.
CONFIG = "pyproject.toml"

TIMEOUT_SECONDS = 300


def _fail(message: str) -> int:
    print(f"bandit: {message}", file=sys.stderr)
    return 1


def _as_array(value: object) -> list[Any] | None:
    """Narrow a value to a JSON array.

    This gate reads a document ``json.loads`` produced, so every value out of it
    is ``Any``, and ``isinstance(value, list)`` narrows ``Any`` to
    ``list[Unknown]`` -- an element read back out is unknown, and every use of it
    is then a ``reportUnknownVariableType``. The element type is therefore
    asserted rather than inferred: the report's arrays hold whatever the document
    held, and each caller checks the shape of the element it needs.

    Args:
        value: The value to narrow.

    Returns:
        ``value`` as an array, or ``None`` if it is not one.
    """
    if not isinstance(value, list):
        return None
    return cast("list[Any]", value)


def _as_object(value: object) -> dict[str, Any] | None:
    """Narrow a value to a JSON object.

    The same narrowing problem as :func:`_as_array`, and the same answer:
    ``isinstance(value, dict)`` on an unannotated value gives
    ``dict[Unknown, Unknown]``, whose ``get`` is a partially unknown member
    type. ``dict`` is invariant in its first parameter, so the key type cannot
    be widened to ``str`` by narrowing alone either -- the annotation is
    asserted, and :func:`_as_array` says why asserting is sound here.

    Args:
        value: The value to narrow.

    Returns:
        ``value`` as an object, or ``None`` if it is not one.
    """
    if not isinstance(value, dict):
        return None
    return cast("dict[str, Any]", value)


def _run(extra: tuple[str, ...]) -> subprocess.CompletedProcess[str] | None:
    """Run Bandit once through the copy this hook's environment installed.

    The literal argv is for Opengrep's `dangerous-subprocess-use-audit` rule,
    which exempts a literal argv and reports anything else -- the
    `sys.executable` form included. `shutil.which` above resolved the same name
    through the same PATH, so the check and the call cannot disagree.

    Args:
        extra: Additional arguments for this run, such as ``-f json``.

    Returns:
        The completed process, or ``None`` if it could not be run at all. The
            reason has already been printed; a caller only needs to know that the
            result is unknown.
    """
    try:
        return subprocess.run(
            ["bandit", *extra, "-r", *ROOTS, "-c", CONFIG],
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _fail(f"a scan exceeded {TIMEOUT_SECONDS}s")
    except OSError as error:
        _fail(f"bandit could not be started: {error}")
    return None


def _echo(completed: subprocess.CompletedProcess[str]) -> None:
    """Print a scan's own output, so a finding reads the way it always has.

    Args:
        completed: The process returned by :func:`_run`.
    """
    if completed.stdout.strip():
        print(completed.stdout, end="")
    if completed.stderr.strip():
        print(completed.stderr, end="", file=sys.stderr)


Scan = tuple[list[str], int] | None


def _document(
    machine: subprocess.CompletedProcess[str] | None,
) -> dict[str, Any] | None:
    """Read a JSON scan's report, or say why it cannot be read.

    Args:
        machine: The completed process from the ``-f json`` run, or ``None`` if
            that run could not be started.

    Returns:
        The parsed report, or ``None`` if there is no readable report. The reason
            has already been printed.
    """
    if machine is None or machine.returncode == 2 or not machine.stdout.strip():
        _fail("the JSON scan produced no readable report, so its result is unknown")
        return None
    try:
        document = json.loads(machine.stdout)
    except json.JSONDecodeError as error:
        _fail(f"the JSON report is not valid JSON: {error}")
        return None
    if not isinstance(document, dict):
        _fail(f"the JSON report holds a {type(document).__name__}, not an object")
        return None
    return cast("dict[str, Any]", document)


def _array(document: dict[str, Any], key: str, subject: str) -> list[Any] | None:
    """Read one of the report's arrays, rejecting anything that is not one.

    Args:
        document: The parsed JSON report.
        key: The array to read.
        subject: What the array is about, phrased to read after "so the".

    Returns:
        The array, or ``None`` if the key is absent or is not an array.
    """
    value = _as_array(document.get(key))
    if value is None:
        _fail(f"the JSON report has no `{key}` array, so {subject} is unknown")
        return None
    return value


def _lines(errors: list[Any]) -> list[str] | None:
    """Render the report's skip list, rejecting an entry that is not an object.

    Args:
        errors: The report's ``errors`` array.

    Returns:
        One ``  filename: reason`` line per skipped file, or ``None`` if an entry
            is not an object.
    """
    rendered: list[str] = []
    for entry in errors:
        record = _as_object(entry)
        if record is None:
            _fail("the JSON report's `errors` array holds a non-object entry")
            return None
        name = record.get("filename", "<unnamed>")
        reason = record.get("reason", "no reason given")
        rendered.append(f"  {name}: {reason}")
    return rendered


def _read(machine: subprocess.CompletedProcess[str] | None) -> Scan:
    """Read what a JSON scan skipped and how many findings it reported.

    The skip list is the analyser reporting its own blind spot, taken from
    ``errors`` (populated from ``manager.get_skipped()``). The count comes from
    ``results`` because Bandit's exit status is 1 for any nonzero number of
    findings, so printing the status as a count would be wrong on every run that
    found more than one.

    Args:
        machine: The completed process from the ``-f json`` run.

    Returns:
        The skipped files as ``filename: reason`` lines and the number of
            findings, or ``None`` if either could not be read.
    """
    document = _document(machine)
    if document is None:
        return None
    errors = _array(document, "errors", "the list of files this scan skipped")
    if errors is None:
        return None
    results = _array(document, "results", "the finding count")
    if results is None:
        return None
    skipped = _lines(errors)
    if skipped is None:
        return None
    return skipped, len(results)


def main() -> int:
    """Run the gate.

    Returns:
        ``0`` when Bandit read every file it was given and found nothing, and
            ``1`` in every other case -- a finding, a file the scan could not
            read, Bandit unable to run, or a report this cannot read. A non-zero
            result and an unknown result both fail the commit; the difference is
            in the message, not the exit code.
    """
    if shutil.which("bandit") is None:
        return _fail(
            "bandit is not on PATH, so this gate cannot run. Refusing to pass: a "
            "gate that silently does not run is indistinguishable from a gate "
            "that found nothing."
        )

    report = _run(("-q",))
    if report is None:
        return 1
    _echo(report)
    if report.returncode == 2:
        return _fail("bandit exited 2, which is how it reports that it cannot run")

    counted = _read(_run(("-q", "-f", "json")))
    if counted is None:
        return 1
    skipped, found = counted

    if skipped:
        print(
            f"bandit: {len(skipped)} file(s) could not be scanned, so this gate "
            "read less than it claims to:\n",
            file=sys.stderr,
        )
        print("\n".join(skipped), file=sys.stderr)
        print(
            "  A file Bandit cannot parse is a file Bandit did not analyse. Fix "
            "it, or the finding it would have produced is not in this report.",
            file=sys.stderr,
        )
        return 1

    if found == 0:
        print("bandit: clean, 0 findings, 0 files skipped")
        return 0

    return _fail(f"{found} finding(s); every finding fails this gate")


if __name__ == "__main__":
    sys.exit(main())
