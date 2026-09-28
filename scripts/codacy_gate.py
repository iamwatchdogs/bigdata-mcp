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


def _findings(sarif_path: Path) -> list[dict[str, Any]] | None:
    """Read every result out of a SARIF file.

    Returns:
        The results, or ``None`` if the file is absent or not valid SARIF. That
            distinction is the point: an unreadable report means the analysis
            did not happen, which is not the same as an analysis that found
            nothing.
    """
    if not sarif_path.is_file():
        return None
    try:
        document = json.loads(sarif_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"codacy: {sarif_path} is not valid JSON: {error}", file=sys.stderr)
        return None

    runs = document.get("runs")
    if not isinstance(runs, list):
        print(f"codacy: {sarif_path} has no `runs` array", file=sys.stderr)
        return None

    return [
        result
        for run in runs
        if isinstance(run, dict)
        for result in run.get("results", []) or []
        if isinstance(result, dict)
    ]


def _describe(result: dict[str, Any]) -> str:
    location = ""
    for entry in result.get("locations", []) or []:
        physical = entry.get("physicalLocation", {}) if isinstance(entry, dict) else {}
        uri = physical.get("artifactLocation", {}).get("uri", "")
        line = physical.get("region", {}).get("startLine")
        location = f"{uri}:{line}" if line else str(uri)
        if location:
            break

    rule = str(result.get("ruleId", "unknown-rule")).split(".")[-1]
    message = str(result.get("message", {}).get("text", "")).strip()
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
