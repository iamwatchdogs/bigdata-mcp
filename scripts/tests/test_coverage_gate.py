"""Tests for `scripts/coverage_gate.py`, the per-file coverage floor.

The gate exists because neither existing floor can see a single file.
`fail_under` in `pyproject.toml` is coverage.py's project total — there is no
per-file `fail_under` in the configuration reference — and Codecov's statuses
are `project` and `patch`, also totals. One file can therefore fall to 50% while
the other twenty-four hold the total at 98% and every gate stays green.

**Mutation evidence**, each applied and observed red before reverting:

* C1 make `offenders` return nothing regardless of the floor ->
  `test_a_file_below_the_floor_is_reported_with_its_percentage`
* C2 report clean when the report cannot be read, rather than exit 2 ->
  `test_a_missing_report_is_a_failure_never_a_pass`, all four cases
* C3 fail a file sitting exactly at the floor (mutate `<` to `<=`) ->
  `test_a_file_exactly_at_the_floor_passes`
* C4 skip empty files -> `test_an_empty_file_is_not_held_against_the_floor`
* C5 accept a report entry with no `percent_covered` as clean ->
  `test_a_summary_without_a_percentage_is_unreadable_not_passing`
* C6 tolerate a non-int `num_statements` ->
  `test_a_summary_without_a_percentage_is_unreadable_not_passing`, the
  `null_count` case. The branch had no test until C6 went green, which is how
  that surfaced
* C7 remove the `shutil.which` guard ->
  `test_a_missing_coverage_binary_is_unreadable_not_passing`. Written after the
  call was changed to a bare `coverage` name to satisfy opengrep's
  `dangerous-subprocess-use-audit` rule (see `scripts/codacy_gate.py` for the same
  reasoning): with a literal program, an absent binary must still fail closed
  rather than escape as `FileNotFoundError`

The tests drive `main()` through `--json` and read the floor from the repository's
own `pyproject.toml`, which is what the gate does in production. They are written
so they hold at either the current floor or 95: every synthetic file is either 100%
or 0%, so the assertions are about the gate's behaviour and not about the number
of the day. The number itself is pinned by `scripts/tests/test_repo_contracts.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coverage_gate import EXIT_BELOW_FLOOR
from coverage_gate import EXIT_CLEAN
from coverage_gate import EXIT_NO_REPORT
from coverage_gate import NoReport
from coverage_gate import floor_from_pyproject
from coverage_gate import main
from coverage_gate import offenders

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PYPROJECT = REPO_ROOT / "pyproject.toml"


def _report(files: dict[str, float], *, statements: int = 10) -> dict[str, object]:
    """Build a `coverage json` report from filename to percentage.

    Args:
        files: Filename to `percent_covered`.
        statements: Statements to claim for each file, so the gate's empty-file
            skip can be switched off by passing a non-zero value.

    Returns:
        A report shaped like coverage.py's own output.
    """
    return {
        "files": {
            name: {
                "summary": {
                    "num_statements": statements,
                    "percent_covered": percent,
                    "missing_lines": 0 if percent == 100 else 1,
                },
            }
            for name, percent in files.items()
        },
        "totals": {"percent_covered": 100.0},
    }


def _write(tmp_path: Path, report: object) -> Path:
    """Write a report to disk as JSON.

    Args:
        tmp_path: Where to write.
        report: The document to serialise.

    Returns:
        The path written.
    """
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")
    return target


def test_the_floor_comes_from_the_file_it_is_asked_for(tmp_path: Path) -> None:
    """One number, one source: the gate reads `fail_under` rather than owning one.

    A floor written twice is a floor that drifts, so the gate has none of its own.
    Asserted against a temporary `pyproject.toml` carrying a value the repository
    does not have — if the gate hardcoded a percentage anywhere, it would fail
    this test, which is the only way to tell "reads the config" from "happens to
    agree with it today".
    """
    fake = tmp_path / "pyproject.toml"
    fake.write_text("[tool.coverage.report]\nfail_under = 12.5\n", encoding="utf-8")

    assert floor_from_pyproject(fake) == pytest.approx(12.5)


def test_a_floor_that_cannot_be_read_is_an_error_not_a_default() -> None:
    """A gate that invents its own threshold is a gate nobody configured."""
    with pytest.raises(NoReport, match="fail_under"):
        floor_from_pyproject(Path("/nonexistent/pyproject.toml"))


def test_a_file_below_the_floor_is_reported_with_its_percentage() -> None:
    """The whole point: a weak file must be named, not averaged away."""
    report = _report({"good.py": 100.0, "bad.py": 0.0}, statements=100)

    found = offenders(report, floor=95.0)

    assert found == [("bad.py", 0.0)]


def test_a_file_exactly_at_the_floor_passes() -> None:
    """`fail_under` semantics: at the floor is at the floor.

    Pinned as its own case because the boundary is where `<` and `<=` differ, and
    a gate that fails a file sitting exactly on its target demands 100% while
    claiming not to.
    """
    assert offenders(_report({"edge.py": 95.0}), floor=95.0) == []
    assert offenders(_report({"under.py": 94.9}), floor=95.0) == [("under.py", 94.9)]


def test_an_empty_file_is_not_held_against_the_floor() -> None:
    """`__init__.py` files with no statements of their own cannot be covered.

    Holding them against the floor would make the gate fail files that no test
    can affect, which trains a reader to distrust the whole check.
    """
    report = _report({"pkg/__init__.py": 0.0}, statements=0)

    assert offenders(report, floor=95.0) == []


def test_a_summary_without_a_percentage_is_unreadable_not_passing() -> None:
    """Missing fields fail closed; an empty `or` default here is the classic bug.

    Two shapes, because the gate refuses both and they arrive differently: a
    whole summary that is absent, and a summary whose percentage is absent. The
    second is the one an `or []`-style default would swallow — a report that
    cannot be counted is not a clean report, and `null` is the JSON a tool that
    failed to start actually emits.
    """
    without_summary: dict[str, object] = {}
    entry: dict[str, object] = {
        "summary": {"num_statements": 10, "percent_covered": 100.0},
    }

    with pytest.raises(NoReport, match="summary"):
        offenders({"files": {"x.py": without_summary}}, floor=95.0)

    del entry["summary"]
    with pytest.raises(NoReport, match="summary"):
        offenders({"files": {"x.py": entry}}, floor=95.0)

    partial: dict[str, object] = {"summary": {"num_statements": 10}}
    with pytest.raises(NoReport, match="percent_covered"):
        offenders({"files": {"x.py": partial}}, floor=95.0)

    null_count: dict[str, object] = {
        "summary": {"percent_covered": 100.0, "num_statements": None},
    }
    with pytest.raises(NoReport, match="num_statements"):
        offenders({"files": {"x.py": null_count}}, floor=95.0)


def test_an_empty_files_map_is_unreadable_not_passing() -> None:
    """A report with no files describes no analysis, not a clean one."""
    with pytest.raises(NoReport, match="no files map"):
        offenders({"files": {}}, floor=95.0)
    with pytest.raises(NoReport, match="no files map"):
        offenders({}, floor=95.0)


def test_a_missing_coverage_binary_is_unreadable_not_passing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `coverage` on PATH is exit 2, not a traceback.

    The bare program name this gate now uses is what opengrep's
    `dangerous-subprocess-use-audit` rule exempts — `codacy_gate.py` documents
    the same trade — and it buys exactly this failure mode: `FileNotFoundError`
    from a gate that is meant to classify failures rather than raise them. The
    `which` guard is the difference, and this test is the only thing that would
    notice its removal.
    """
    monkeypatch.setattr("coverage_gate.shutil.which", lambda _name: None)

    assert main([]) == EXIT_NO_REPORT


def test_a_clean_report_exits_zero(tmp_path: Path) -> None:
    """Every file at 100%: the gate says so and gets out of the way."""
    path = _write(tmp_path, _report({"a.py": 100.0, "b.py": 100.0}))

    assert main(["--json", str(path)]) == EXIT_CLEAN


def test_a_file_below_the_floor_exits_one(tmp_path: Path) -> None:
    """Exit 1 is the difference this gate exists to make, distinct from 2."""
    path = _write(tmp_path, _report({"a.py": 100.0, "weak.py": 0.0}, statements=50))

    assert main(["--json", str(path)]) == EXIT_BELOW_FLOOR


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json at all",
        json.dumps({"files": {}}),
        json.dumps(["not", "an", "object"]),
    ],
    ids=["missing", "garbage", "empty-files", "non-object"],
)
def test_a_missing_report_is_a_failure_never_a_pass(
    tmp_path: Path, content: str
) -> None:
    """Exit 2 for every way the report can fail to exist.

    This is the behaviour the Codacy gate's docstring records from experience:
    a gate that cannot find its data reports clean and exits 0, and "clean" and
    "never ran" are the two things an operator most needs to tell apart.
    """
    path = tmp_path / "report.json"
    if content:
        path.write_text(content, encoding="utf-8")

    assert main(["--json", str(path)]) == EXIT_NO_REPORT


def test_a_coverage_failure_is_reported_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`coverage json` exiting non-zero — no data from the last run — fails closed.

    The subprocess result is canned rather than produced for real, because the
    point is the contract (`non-zero exit -> NoReport`), not the tool's behaviour
    under a condition this suite cannot arrange deterministically.
    """

    def fail(*_args: object, **_kwargs: object) -> object:
        class Result:
            returncode = 1
            stdout = ""
            stderr = "no data to report"

        return Result()

    monkeypatch.setattr("coverage_gate.subprocess.run", fail)

    assert main([]) == EXIT_NO_REPORT


def test_a_stdout_that_is_not_json_is_reported_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coverage.py's `analyze` always exits 0 on a clean tree; parse failures are
    the other half of the same lesson. A non-JSON stdout must not become an empty
    report."""

    def junk(*_args: object, **_kwargs: object) -> object:
        class Result:
            returncode = 0
            stdout = "this is not json"
            stderr = ""

        return Result()

    monkeypatch.setattr("coverage_gate.subprocess.run", junk)

    assert main([]) == EXIT_NO_REPORT
