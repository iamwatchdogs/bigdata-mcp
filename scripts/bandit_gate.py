"""Fail-closed Bandit gate for the commit stage.

Bandit's exit status cannot be the gate, for the same reason Codacy's cannot.
In 1.9.4 the CLI decides with `results_count(...) > 0` and nothing else, and a
file it never managed to read contributes no result and no failure. Measured
against this repository's own configuration, on 1.9.4:

- a tree whose only Python file has a syntax error exits **0**
- a file made unreadable with `chmod 000` exits **0**, and so does the same file
  at mode `644` when it is made readable again -- the verdict flips on the
  permission bits, not on the content
- a file that is not valid UTF-8 is skipped as a syntax error rather than read
- a file holding a real `subprocess.run(..., shell=True)` produces a B602
  finding at mode `644` and nothing at all at mode `000`

So the file is not scanned, and the finding it would have produced is invisible
to the gate. `check-ast` does not cover the gap: it is a stock `pre-commit-hooks`
hook that sees only the files in the commit, while this scan has
`pass_filenames: false` and covers all three roots at once -- so a file broken by
a merge, with its syntax intact in every individual parent, is exactly the case
it misses.

The `-q` in the command line is not the cause and is worth being precise about.
Without it, Bandit prints `Files skipped (1):` at INFO -- and still exits 0. The
exit status is 0 either way; `-q` only removes the one notice that said a file had
gone unread. The status is decided by `results_count`.

The skip list is not lost, it is just not on the exit path. `-f json` writes every
skipped file to the top-level `errors` key, and `bandit/formatters/json.py` fills
that key directly from `manager.get_skipped()`. This reads it and fails on a
non-empty list.

Two runs, not one. The first is the bare command the hook used before, so the
report a developer reads on a finding is unchanged. The second adds `-f json`,
which is where both the skip list and the real finding count come from: Bandit
exits 1 for any nonzero number of findings, so its exit status is not a count and
must never be printed as one. The scan takes about 0.1s over `src scripts tests`
here, so the pair costs about 0.25s.

Bandit is invoked as a bare name rather than as `sys.executable -m bandit`. That
is not a style preference: Opengrep's `dangerous-subprocess-use-audit` rule
exempts a literal argv and reports anything else, and the `sys.executable` form
is not exempt either. The hook's venv `bin` is prepended to `PATH` by
pre-commit's own `get_env_patch`, so the bare name and the `shutil.which` above
resolve to the same copy of bandit that `additional_dependencies` installed, and
the check and the call cannot disagree about which binary is meant.

That is also why an absent `bandit` cannot raise here: `which` reports it as a
stated failure first, and if it were somehow reached anyway the failure is a
child's nonzero exit and an empty report, which is read as unknown. `PATH` is
still checked so the most common cause is named rather than inferred.

**What this does not cover.** `errors` is initialised as `[]` and filled in a
separate loop, so an upstream refactor that stopped populating it would look
exactly like a scan that skipped nothing. The missing-key case is rejected, the
starved-key case is indistinguishable from clean by any consumer of this format.
That is a property of Bandit's report, not a choice here, and it is recorded so
nobody reads a green line as stronger evidence than it is.

Refusing to run is a failure, not a pass. Bandit exiting 2 -- an unreadable
config, an empty profile -- or emitting JSON this cannot read means the skip list
is unknown, and unknown is not the same as clean.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from typing import Any

# The trees this repository owns. `.venv` is deliberately absent: `-r` is given
# explicit roots rather than a bare directory, which is the only exclusion lever
# Bandit has, and every finding inside `.venv` is about a third-party package.
ROOTS = ["src", "scripts", "tests"]

# Read from the same file the hook already lints, so the reviewed skip list in
# `[tool.bandit]` and the gate can never describe different configurations.
CONFIG = "pyproject.toml"

TIMEOUT_SECONDS = 300


def _fail(message: str) -> int:
    print(f"bandit: {message}", file=sys.stderr)
    return 1


def _run(extra: tuple[str, ...]) -> subprocess.CompletedProcess[str] | None:
    """Run Bandit once through the copy this hook's environment installed.

    The argv's first element is a bare literal for the same reason
    `scripts/coderabbit_advisory.py` spells its own out: Opengrep's
    `dangerous-subprocess-use-audit` rule exempts a literal argv and reports
    anything else, and a `sys.executable`-relative one is not exempt either --
    measured, both shapes are reported. `shutil.which` above resolved the same
    name through the same PATH, since the hook's venv `bin` is prepended to it,
    so the check and the call cannot disagree about which binary is meant.

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
    return document


def _array(document: dict[str, Any], key: str, subject: str) -> list[Any] | None:
    """Read one of the report's arrays, rejecting anything that is not one.

    Args:
        document: The parsed JSON report.
        key: The array to read.
        subject: What the array is about, phrased to read after "so the".

    Returns:
        The array, or ``None`` if the key is absent or is not an array.
    """
    value = document.get(key)
    if not isinstance(value, list):
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
        if not isinstance(entry, dict):
            _fail("the JSON report's `errors` array holds a non-object entry")
            return None
        name = entry.get("filename", "<unnamed>")
        reason = entry.get("reason", "no reason given")
        rendered.append(f"  {name}: {reason}")
    return rendered


def _read(machine: subprocess.CompletedProcess[str] | None) -> Scan:
    """Read what a JSON scan skipped and how many findings it reported.

    The ``errors`` key is populated from ``manager.get_skipped()`` by the JSON
    formatter, so the skip list is the analyser reporting its own blind spot
    rather than this script inferring one. The finding count comes from
    ``results`` for the same reason: Bandit's exit status is 1 for any nonzero
    number of findings, so printing the status as a count would be wrong on
    every run that found more than one.

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
            read, Bandit unable to run, or a report this cannot read. There is no
            third status: a non-zero result and an unknown result both fail the
            commit, and the difference is in the message, not the exit code.
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
    # `python3 scripts/bandit_gate.py` and `python scripts/bandit_gate.py` are
    # both valid invocations, so the shebang is not load-bearing here. Kept for
    # consistency with the other scripts in this directory.
    sys.exit(main())
