"""Fail-closed gate: every measured file must clear the per-file coverage floor.

Two floors exist and neither can do the other's job.

`pyproject.toml`'s `[tool.coverage.report] fail_under` is enforced by coverage.py
itself on every `pytest`, and it is a **project total** — coverage.py has no
per-file `fail_under` (verified against the 7.16 configuration reference; the
setting is a single float compared against the total). `codecov.yml` is enforced
on the PR, and Codecov's statuses are `project` and `patch`: again totals, one
over the package and one over the diff. A single file can therefore fall to 50%
while the other twenty-four hold the total at 98%, and every existing gate stays
green. That is the gap this script fills.

The floor is read from `[tool.coverage.report] fail_under` rather than repeated
here. One number, one source: `codecov.yml` states the same value for Codecov's
own check, and `scripts/tests/test_repo_contracts.py` asserts the two agree, so
there is no third copy to drift.

Three things this deliberately does not do.

**It does not compute coverage itself.** The report comes from `coverage json`,
so the per-file number is coverage.py's own `percent_covered` — the same
branch-aware figure `coverage report` prints. Reimplementing that arithmetic is
how a gate and a report start disagreeing about the same data.

**It does not pass when the report cannot be read.** A missing `.coverage`, an
unparseable JSON, an empty `files` map: each exits `2`, not `0`. This is the
failure `scripts/codacy_gate.py`'s docstring records from experience — a gate
that cannot find its data is indistinguishable from one that found nothing, and
"nothing" reads as clean. Exit codes follow `scripts/redirect_gate.py`:
`0` clean, `1` a file is below the floor, `2` the report could not be examined.

**It does not re-run the suite.** It reads the report left by the last run, so it
costs nothing and cannot disagree with what `make test` just measured. Its callers
are `make test` and the CI coverage step, both of which run it immediately after
the suite produces the data.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tomllib
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

EXIT_CLEAN = 0
EXIT_BELOW_FLOOR = 1
EXIT_NO_REPORT = 2


class NoReport(Exception):
    """The coverage report could not be obtained or understood."""


def _as_mapping(value: object) -> dict[str, object] | None:
    """Narrow a parsed JSON value to a string-keyed mapping.

    The ``cast`` is the same one ``scripts/codacy_gate.py`` documents: an
    ``isinstance`` check on a value parsed from JSON gives ``dict[Unknown,
    Unknown]``, whose ``get`` is a partially unknown member type. Narrowing first
    and casting after is what makes the declared shape honest rather than an
    annotation the checker never checks.

    Returns:
        The mapping, or ``None`` when the value is not one.
    """
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value)


def floor_from_pyproject(path: Path = PYPROJECT_PATH) -> float:
    """Read the coverage floor from pyproject's `[tool.coverage.report]`.

    Args:
        path: The `pyproject.toml` to read. A parameter rather than a hardcoded
            constant so a test can point it at a copy of the tree.

    Returns:
        The `fail_under` value as a float.

    Raises:
        NoReport: If the file, section, or key is missing. Raised rather than
            defaulted to some number: a floor the gate invented is a floor
            nobody configured, and silently skipping the check would be worse
            than not having it.
    """
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        value = config["tool"]["coverage"]["report"]["fail_under"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        message = f"cannot read [tool.coverage.report].fail_under from {path}"
        raise NoReport(message) from exc
    return float(value)


def offenders(report: dict[str, object], floor: float) -> list[tuple[str, float]]:
    """Return the measured files sitting under the floor, with their percentages.

    Args:
        report: A parsed `coverage json` report.
        floor: The per-file minimum, as a percentage.

    Returns:
        `(filename, percent_covered)` pairs, sorted by name.

    Raises:
        NoReport: If the report has no usable file map, or a file's summary is
            missing the numbers this compares. An absent field is not a pass —
            it is a report this gate cannot read.
    """
    files = _as_mapping(report.get("files"))
    if not files:
        message = "coverage report contains no files map"
        raise NoReport(message)

    results: list[tuple[str, float]] = []
    for name in sorted(files):
        summary = _as_mapping(files[name])
        summary = _as_mapping(summary.get("summary")) if summary else None
        if summary is None:
            message = f"coverage report entry for {name} has no summary"
            raise NoReport(message)

        percent = summary.get("percent_covered")
        if not isinstance(percent, (int, float)):
            message = f"coverage report entry for {name} has no percent_covered"
            raise NoReport(message)

        statements = summary.get("num_statements", 0)
        if not isinstance(statements, int):
            message = f"coverage report entry for {name} has no num_statements"
            raise NoReport(message)
        if statements == 0:
            # An empty file has nothing to cover, and holding it against the
            # floor would fail `__init__.py` files no test can affect.
            continue

        if float(percent) < floor:
            results.append((name, float(percent)))
    return results


def _report_from_coverage() -> dict[str, object]:
    """Ask coverage.py for the JSON report of the last run.

    Returns:
        The parsed report.

    Raises:
        NoReport: If `coverage json` exits non-zero (typically no `.coverage`
            data file exists) or its stdout is not JSON.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "-"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        message = f"coverage json failed: {completed.stderr.strip()[-2000:]}"
        raise NoReport(message)
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        message = "coverage json produced output that is not JSON"
        raise NoReport(message) from exc
    if not isinstance(parsed, dict):
        message = "coverage json produced a non-object document"
        raise NoReport(message)
    return cast("dict[str, object]", parsed)


def _report_from_path(path: str) -> dict[str, object]:
    """Read a stored JSON report from disk.

    The injection point a test uses, and the one an operator would use to check
    a report produced elsewhere.

    Args:
        path: Where to read from.

    Returns:
        The parsed report.

    Raises:
        NoReport: If the file is missing or is not JSON.
    """
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"cannot read coverage report from {path}"
        raise NoReport(message) from exc
    if not isinstance(parsed, dict):
        message = f"coverage report at {path} is not a JSON object"
        raise NoReport(message)
    return cast("dict[str, object]", parsed)


def main(argv: list[str] | None = None) -> int:
    """Run the gate.

    Args:
        argv: Arguments, or `None` to read `sys.argv`. `--json PATH` reads a
            stored report instead of asking coverage.py for one.

    Returns:
        `EXIT_CLEAN` when every measured file clears the floor,
        `EXIT_BELOW_FLOOR` when one does not, `EXIT_NO_REPORT` when the report
        could not be examined — which is a failure, never a pass.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json",
        metavar="PATH",
        default=None,
        help="read a stored coverage JSON report instead of running coverage",
    )
    args = parser.parse_args(argv)

    try:
        floor = floor_from_pyproject()
        report = _report_from_path(args.json) if args.json else _report_from_coverage()
        bad = offenders(report, floor)
    except (NoReport, ValueError) as exc:
        print(f"coverage gate: {exc}", file=sys.stderr)
        return EXIT_NO_REPORT

    if not bad:
        print(f"coverage gate: clean, every file at or above {floor:g}%")
        return EXIT_CLEAN

    print(f"coverage gate: {len(bad)} file(s) below {floor:g}%:", file=sys.stderr)
    for name, percent in bad:
        print(f"  {percent:6.2f}%  {name}", file=sys.stderr)
    print(
        "each file must clear the same floor the project total clears; "
        "add tests rather than lowering the floor",
        file=sys.stderr,
    )
    return EXIT_BELOW_FLOOR


if __name__ == "__main__":
    sys.exit(main())
