"""Fail-closed Codacy static-analysis gate for the pre-push stage.

A wrapper rather than a bare `codacy-cli analyze` hook entry, staged into a
temporary directory, for three measured reasons.

**`analyze` always exits 0** -- on a clean tree, on a confirmed
`subprocess(..., shell=True)`, on a missing config, on a malformed one. Its exit
status carries no information, so the finding count is read from SARIF instead,
and a missing or unparseable SARIF file is a failure rather than "no findings".

**A report that cannot be counted is not a clean report.** An `or []` default on
`results` returns `[]` for a dict, for a string and for a `null`, and exits
successfully on all three -- and SARIF 3.14.23 reserves a `null` `results` for a
tool that *failed to start*, so `or []` maps the standard's own failure sentinel
onto "clean". Appendix I names the three signals of an incomplete result set and
all three are checked: a false `executionSuccessful`, an error-level
notification, a `null` `results`. Absent `invocations` is not one of them, and
two of the three runs this analyser emits have none.

**No exclusion mechanism and no path argument.** It walks the current directory
and `exclude_paths` in `.codacy/codacy.yaml` is silently ignored. Staging is
therefore the only lever that drops `.venv`, and it is what makes the gate fast
enough to sit in a pre-push hook: 9m11s over the working tree versus 1m12s
staged. See the failure ledger in AGENTS.md for the measurements.

Refusing to run is a failure, not a pass: a hook that cannot find its tools would
be indistinguishable from one that found nothing.

Findings are reported with file, line, rule and message. Every finding fails the
gate -- there is no baseline to diff against, and a suppression list is how the
previous attempt at silencing this tool went wrong.
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
# absent because it is what the analysis would otherwise spend its time on, and
# every finding it produces there is about a third-party package.
#
# `tests` is staged as `_tests`: the analyser silently skips a directory named
# `tests`, and `tests/` is half the first-party Python here, so a neutral name is
# what keeps it analysed at all.
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


def _run_analysis(config_dir: Path, sarif_path: Path) -> int:
    """Run the analysis over the staged tree.

    Args:
        config_dir: The staged ``.codacy`` directory, used to locate the tree.
        sarif_path: Where the analyser should write its SARIF report.

    Returns:
        The analyser's exit status. Recorded but not trusted: it is 0 for
            findings and for config errors alike, and is only consulted to
            tell a crash from a clean run.
    """
    # The bare name is load-bearing, not tidiness. Opengrep's
    # `dangerous-subprocess-use-audit` rule exempts a literal argv and reports
    # anything else, and a `shutil.which` result is not a literal. `main` has
    # already checked `codacy-cli` is on PATH, so the two cannot disagree.
    completed = subprocess.run(
        ["codacy-cli", "analyze", "--format", "sarif", "--output", str(sarif_path)],
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

    Appendix I's three signals, all checked because a well-formed report saying
    the analysis was incomplete is not a report of zero findings. ``invocations``
    and ``results`` are both optional, so a run omitting one claims nothing and
    is not rejected for it -- not hypothetical: two of the three runs this
    analyser emits carry no ``invocations``.

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
        # Redundant for the verdict -- `_results_of` rejects a `null` on its own
        # -- but kept because it is the condition the specification names, and
        # because its message says what happened, not what the shape was.
        return "results is null"
    return None


def _results_of(run: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract one run's result objects, rejecting anything that is not one.

    A ``result`` is an object carrying a ``message``: the schema marks
    ``message`` as required and ``locations`` optional, so a locationless result
    is valid and must still be counted. A missing ``results`` is an empty list
    (the property is optional), but a present one that is not an array of result
    objects means no finding count can be read at all.

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
            reports a failed analysis as a clean one.
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
        # invariant in its first parameter, so the annotation is asserted, not
        # narrowed.
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
    if shutil.which("codacy-cli") is None:
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
            status = _run_analysis(destination / ".codacy", sarif_path)
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
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
