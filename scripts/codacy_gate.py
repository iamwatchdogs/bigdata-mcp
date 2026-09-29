"""Fail-closed Codacy static-analysis gate for the pre-push stage.

Why this is a wrapper rather than a hook entry of ``codacy-cli analyze``
directly, and why the analysis is staged into a temporary directory. Both are
consequences of measured behaviour, not preference.

**`analyze` always exits 0.** It reported 0 on a clean tree, 0 on a tree with a
confirmed `subprocess(..., shell=True)` finding, 0 on a missing config file, and
0 on a malformed config. Its exit status carries no information, so a hook whose
entry is that command reports green no matter what it finds. The finding count
is read from SARIF here instead, and a missing or unparseable SARIF file is
treated as a failure rather than as "no findings".

**A report that cannot be counted is not a clean report.** `analyze` does not
currently fail in a way that produces a malformed report -- every one of its own
failure modes writes no file at all, which is already handled. The gap this
closes is one malformed-content change away: a comprehension that iterated
`run.get("results", []) or []` returned `[]` for `"results": {"error": "analyzer
failed"}`, for `"results": "boom"`, and for `"results": null`, and `main()`
exited successfully on all three. Section 3.14.23 of the specification reserves a
`null` `results` for a tool that *failed to start*, so that `or []` mapped the
standard's own failure sentinel onto "clean". Appendix I names the three signals
of an incomplete result set, and all three are now checked: a false
`executionSuccessful`, an error-level notification, and a `null` `results`.
Absent `invocations` is not one of them, and two of the three runs this analyser
emits have none.

**`analyze` has no exclusion mechanism and no path argument.** It walks the
current directory, and `exclude_paths` in `.codacy/codacy.yaml` is silently
ignored: adding it left a run that was still analysing `.venv` when it was
killed at six minutes forty. Measured on this repository, the same config
produced 860 findings in 9m11s, and all 860 were inside `.venv`; the identical
config over `src/` and `tests/` alone produced 0 findings in 1m12s. So the
workable lever is the working directory, and staging the analysable trees into
a temporary one both excludes `.venv` and makes the gate fast enough to sit in
a pre-push hook at all. A 9-minute gate does not get run.

Refusing to run is a failure, not a pass. A hook that cannot find its tools
would otherwise be indistinguishable from a hook that found nothing, which is
the same green line that means two different things.

Findings are reported with file, line, rule and message. Every finding fails the
gate: this repository has no baseline to diff against, and a suppression list
is how the previous attempt at silencing this tool went wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
from pathlib import Path
from typing import Any
from typing import cast

# The trees that ship, mapped to the name each is staged under. `.venv` is
# deliberately absent: it is the only thing the analysis would otherwise spend
# its time on, and every finding it produces there is about a third-party
# package rather than about this repository.
#
# `tests` is staged as `_tests`. The analyser skips a directory literally named
# `tests`, and it does so silently: a `subprocess(..., shell=True)` planted in
# `tests/` produced no finding while the identical file in `scripts/` was
# reported, and renaming `tests/` to `teststuff/` made the same file appear
# again. Nothing in the CLI's output says a tree was skipped. Since `tests/` is
# half the first-party Python in this repository, staging under a neutral name
# is what keeps it analysed at all.
STAGED_TREES = {"src": "src", "tests": "_tests", "scripts": "scripts"}

CONFIG_SOURCE = Path(".codacy/codacy.yaml")
SARIF_NAME = "codacy.sarif"
ANALYSE_TIMEOUT_SECONDS = 600


def _fail(message: str) -> int:
    print(f"codacy: {message}", file=sys.stderr)
    return 1


def _reject(message: str) -> None:
    """Print why a report cannot be counted.

    Args:
        message: What is wrong with the report, phrased as a reason rather than
            an instruction.
    """
    print(f"codacy: {message}", file=sys.stderr)


def _stage(destination: Path) -> Path | None:
    """Build a working directory holding only the trees worth analysing.

    Returns:
        The staged ``.codacy`` config directory, or ``None`` if a tree named in
            ``STAGED_TREES`` is missing, which is a repository change this
            gate should not silently absorb.
    """
    for tree in STAGED_TREES:
        if not Path(tree).is_dir():
            print(f"codacy: no {tree}/ directory to analyse", file=sys.stderr)
            return None

    config_dir = destination / ".codacy"
    config_dir.mkdir(parents=True)
    shutil.copy2(CONFIG_SOURCE, config_dir / "codacy.yaml")

    for tree, staged_name in STAGED_TREES.items():
        shutil.copytree(
            tree,
            destination / staged_name,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    return config_dir


def _run_analysis(executable: str, config_dir: Path, sarif_path: Path) -> int:
    """Run the analysis over the staged tree.

    Args:
        executable: The absolute path to ``codacy-cli``, resolved by ``main``.
            Passed in rather than resolved here so the process is not started
            from a bare command name, which ``PATH`` could satisfy with a
            different binary than the one whose presence was checked.
        config_dir: The staged ``.codacy`` directory, used to locate the tree.
        sarif_path: Where the analyser should write its SARIF report.

    Returns:
        The analyser's exit status. Recorded but not trusted: it is 0 for
            findings and for config errors alike, and is only consulted to
            tell a crash from a clean run.
    """
    completed = subprocess.run(  # nosec B603  # ruff: ignore[subprocess-without-shell-equals-true]
        [executable, "analyze", "--format", "sarif", "--output", str(sarif_path)],
        cwd=config_dir.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=ANALYSE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        print(completed.stderr[-2000:], file=sys.stderr)
    return completed.returncode


def _notification_error(invocation: dict[str, Any], run_index: int) -> str | None:
    """Report an error-level notification on one invocation.

    Args:
        invocation: A single ``invocation`` object from ``run.invocations``.
        run_index: The run's position in the ``runs`` array, used only to name
            it.

    Returns:
        A human-readable reason, or ``None`` if no notification reports an
            error and the arrays are well-formed.
    """
    for field in ("toolExecutionNotifications", "toolConfigurationNotifications"):
        notifications = invocation.get(field, [])
        if not isinstance(notifications, list):
            print(
                f"codacy: runs[{run_index}].invocations[].{field} is not an array",
                file=sys.stderr,
            )
            return f"{field} is not an array"
        for notification in notifications:
            if not isinstance(notification, dict):
                print(
                    f"codacy: runs[{run_index}].invocations[].{field} holds a "
                    "non-object",
                    file=sys.stderr,
                )
                return f"{field} holds a non-object"
            if notification.get("level") == "error":
                return f"{field} reports an error"
    return None


def _incomplete(run: dict[str, Any], run_index: int) -> str | None:
    """Report why a run says its own result set cannot be trusted.

    Appendix I of the specification gathers the conditions that tell a consumer
    the tool failed to produce a comprehensive set of results, and states that
    they apply separately to each run. They are: an invocation reporting
    ``executionSuccessful`` false; a notification at level ``error``; and a
    ``results`` property whose value is ``null``. All three are checked, because
    a well-formed report that says the analysis was incomplete is not a report of
    zero findings.

    ``invocations`` and ``results`` are both optional properties, so a run that
    omits one makes no such claim and is not rejected for omitting it. That is
    not hypothetical: of the three runs this analyser emits, two carry no
    ``invocations`` at all.

    Args:
        run: A single ``run`` object from the ``runs`` array.
        run_index: The run's position in that array, used only to name it.

    Returns:
        A human-readable reason the run is untrustworthy, or ``None`` if the run
            reports a complete analysis.
    """
    invocations = run.get("invocations", [])
    if not isinstance(invocations, list):
        print(f"codacy: runs[{run_index}].invocations is not an array", file=sys.stderr)
        return "invocations is not an array"

    for invocation in invocations:
        if not isinstance(invocation, dict):
            print(
                f"codacy: runs[{run_index}].invocations[] is not an object",
                file=sys.stderr,
            )
            return "invocations[] is not an object"
        if invocation.get("executionSuccessful") is not True:
            return "invocations[].executionSuccessful is not true"
        reason = _notification_error(invocation, run_index)
        if reason is not None:
            return reason

    if "results" in run and run["results"] is None:
        # Redundant for the verdict -- `_results_of` rejects a `null` results on
        # its own, and a mutation that deletes this branch changes no probe's
        # outcome. It is kept because it is the condition the specification
        # names, and because the message it produces says what happened ("did
        # not complete its analysis") rather than what the shape was.
        return "results is null"
    return None


def _results_of(run: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract one run's result objects, rejecting anything that is not one.

    A ``result`` is an object carrying a ``message``: the schema marks
    ``message`` as required and ``locations`` as optional, so a result with no
    location is valid and must still be counted. A missing ``results`` property
    is an empty list -- the property is optional -- but a ``results`` that is
    present and is not an array of result objects means no finding count can be
    read at all.

    Args:
        run: A single ``run`` object, already accepted by :func:`_incomplete`.

    Returns:
        The run's results, or ``None`` if ``results`` is present but malformed.
    """
    if "results" not in run:
        return []
    results = run["results"]
    if not isinstance(results, list):
        return None
    if not all(isinstance(result, dict) and "message" in result for result in results):
        return None
    return results


def _log(sarif_path: Path) -> dict[str, Any] | None:
    """Read a SARIF file and confirm it is a log object at all.

    Args:
        sarif_path: The report the analyser was asked to write.

    Returns:
        The parsed log object, or ``None`` if the file is absent, is not valid
            JSON, or does not hold an object.
    """
    if not sarif_path.is_file():
        return None
    try:
        document = json.loads(sarif_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _reject(f"{sarif_path} is not valid JSON: {error}")
        return None
    if not isinstance(document, dict):
        _reject(
            f"{sarif_path} holds a JSON {type(document).__name__}, "
            "not a SARIF log object"
        )
        return None
    return cast("dict[str, Any]", document)


def _run_results(run: dict[str, Any], run_index: int) -> list[dict[str, Any]] | None:
    """Validate one run and hand back its results.

    Args:
        run: A single ``run`` object from the ``runs`` array.
        run_index: The run's position in that array, used only to name it.

    Returns:
        The run's results, or ``None`` if this run cannot be counted. A single
            uncountable run poisons the whole log, because a partial count read
            as a total is a number nobody checked.
    """
    reason = _incomplete(run, run_index)
    if reason is not None:
        _reject(
            f"run {run_index} did not complete its analysis ({reason}), so its "
            "results are not a finding count. Reading that as zero findings "
            "would pass a run that never happened."
        )
        return None
    results = _results_of(run)
    if results is None:
        _reject(
            f"runs[{run_index}].results is not an array of SARIF result objects, "
            "so the finding count is unknown"
        )
        return None
    return results


def _findings(sarif_path: Path) -> list[dict[str, Any]] | None:
    """Read every result out of a SARIF file.

    Returns:
        The results, or ``None`` if the file is absent, is not valid JSON, is not
            a SARIF log object, or is a report whose finding count cannot be
            trusted. That last case is why a malformed report is rejected rather
            than skipped: a consumer that silently ignores what it cannot parse
            reports a failed analysis as a clean one, which is the failure this
            function exists to prevent.
    """
    document = _log(sarif_path)
    if document is None:
        return None

    runs = document.get("runs")
    if not isinstance(runs, list):
        _reject(f"{sarif_path} has no `runs` array")
        return None

    findings: list[dict[str, Any]] = []
    for run_index, raw_run in enumerate(runs):
        if not isinstance(raw_run, dict):
            _reject(f"{sarif_path} runs[{run_index}] is not an object")
            return None
        # `isinstance` narrows to `dict[Unknown, Unknown]`, and `dict` is
        # invariant in its first parameter, so the annotation has to be asserted
        # rather than narrowed.
        results = _run_results(cast("dict[str, Any]", raw_run), run_index)
        if results is None:
            return None
        findings.extend(results)
    return findings


def _field(value: object, *names: str) -> object:
    """Read a nested field, treating anything that is not an object as absent.

    Args:
        value: The value to read from.
        *names: The field names to descend through, outermost first.

    Returns:
        The value at the end of the path, or ``None`` if any step on the way is
            not an object or the final key is missing.
    """
    for name in names:
        if not isinstance(value, dict):
            return None
        value = value.get(name)
    return value


def _describe(result: dict[str, Any]) -> str:
    """Render a finding with the three facts needed to act on it.

    Args:
        result: A single SARIF ``result`` object.

    Returns:
        One line naming the rule and the file:line, and one carrying the message.
    """
    location = ""
    for entry in result.get("locations", []) or []:
        physical = _field(entry, "physicalLocation")
        uri = _field(physical, "artifactLocation", "uri")
        if not uri:
            continue
        line = _field(physical, "region", "startLine")
        location = f"{uri}:{line}" if line else str(uri)
        break

    rule = str(result.get("ruleId", "unknown-rule")).split(".")[-1]
    message = str(_field(result, "message", "text") or "").strip()
    return f"  [{rule}] {location}\n      {message}"


def main() -> int:
    """Run the gate.

    Returns:
        ``0`` when the analysis ran and reported no findings, and ``1`` on any
            finding or on any condition that left the result unknown -- a
            missing tool, a missing config, a missing tree, an unreadable
            report. Unknown is not the same as clean, so both are ``1``.
    """
    executable = shutil.which("codacy-cli")
    if executable is None:
        return _fail(
            "codacy-cli is not on PATH, so this gate cannot run. Install it with "
            "`brew install codacy-cli` and run `make codacy-install` once to fetch "
            "the analysis tools. Refusing to pass: a gate that silently does not "
            "run is indistinguishable from a gate that found nothing."
        )

    if not CONFIG_SOURCE.is_file():
        return _fail(f"{CONFIG_SOURCE} is missing, so there is nothing to analyse")

    with tempfile.TemporaryDirectory(prefix="codacy-gate-") as workspace:
        destination = Path(workspace)
        if _stage(destination) is None:
            return 1

        sarif_path = destination / SARIF_NAME
        try:
            status = _run_analysis(executable, destination / ".codacy", sarif_path)
        except subprocess.TimeoutExpired:
            return _fail(
                f"analysis exceeded {ANALYSE_TIMEOUT_SECONDS}s. Raise "
                "ANALYSE_TIMEOUT_SECONDS or narrow STAGED_TREES."
            )

        results = _findings(sarif_path)
        if results is None:
            return _fail(
                "the analysis produced no readable SARIF report, so its result is "
                "unknown. Not treating that as zero findings."
            )

    if not results:
        print(f"codacy: clean, 0 findings (analyze exit {status})")
        return 0

    print(
        f"codacy: {len(results)} finding(s). Every finding fails this gate:\n",
        file=sys.stderr,
    )
    for result in results:
        print(_describe(result), file=sys.stderr)
    return 1


if __name__ == "__main__":
    # `python3 scripts/codacy_gate.py` and `python scripts/codacy_gate.py` are
    # both valid invocations, so the shebang is not load-bearing here. Kept for
    # consistency with the other script in this directory.
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
