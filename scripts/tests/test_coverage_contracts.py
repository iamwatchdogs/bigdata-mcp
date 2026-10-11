"""Contract tests: see the module docstring of the suite they belong to."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from repo_support import CI_PATH
from repo_support import CODECOV_PATH
from repo_support import CODERABBIT_DOCSTRINGS_BLOCK
from repo_support import CONTRIBUTING_PATH
from repo_support import COVERAGE_FLOOR_PERCENT
from repo_support import MAKEFILE_PATH
from repo_support import PYPROJECT_PATH
from repo_support import _coderabbit_text

# --------------------------------------------------------------------------
# The coverage floor: one number, three configurations
# --------------------------------------------------------------------------


@cache
def _fail_under_percent(path: Path = PYPROJECT_PATH) -> float:
    """Return ``pyproject.toml``'s ``[tool.coverage.report].fail_under``.

    One parser for two tests: the first asserts the value, the second compares it
    against Codecov's, and a pair of inlined searches is a pair of places for the
    pattern to drift apart.

    Parsed as TOML rather than searched as text. This was a whole-file regex
    returning the *first* ``fail_under`` line, which is a different question
    from the one being asked: a future table carrying its own ``fail_under`` —
    plausible in any tool's config block — would be asserted while
    ``scripts/coverage_gate.py``, which parses the real table, read another
    number. The two are one decision written twice, and a reader that can
    disagree with the gate is a reader that grades the wrong file.
    ``test_the_floor_is_read_from_the_report_table_not_the_first_match`` pins
    that distinction.

    Args:
        path: The ``pyproject.toml`` to read, so the parse can be exercised
            against a copy the repository does not have.

    Returns:
        The floor, as a percentage.

    Raises:
        AssertionError: If the file, table, or key is missing. Worth failing
        over rather than defaulting, since coverage.py would then run with no
        floor at all and nothing else here would necessarily notice.
    """
    assert path.is_file(), (
        f"pyproject.toml not found at {path}; the floor it carries cannot be "
        "asserted without it, and skipping would leave the number unguarded"
    )
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        report = config["tool"]["coverage"]["report"]
        value = report["fail_under"]
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        message = (
            "pyproject.toml has no [tool.coverage.report].fail_under; "
            "coverage.py would then run with no floor at all"
        )
        raise AssertionError(message) from exc
    return float(value)


@cache
def _codecov_text() -> str:
    """Return ``codecov.yml`` once per process, or fail loudly."""
    assert CODECOV_PATH.is_file(), (
        f"codecov.yml not found at {CODECOV_PATH}; without it Codecov applies "
        "its own defaults, which is the state this test exists to prevent"
    )
    return CODECOV_PATH.read_text(encoding="utf-8")


def _codecov_status_target(status: str) -> float:
    """Read one status block's ``target`` out of ``codecov.yml``.

    The repository has no YAML parser available to tests (no ``pyyaml`` in the
    lock, and adding a dependency to read ten lines of a file this repo owns is
    the wrong trade), so this walks indentation instead: find ``<status>:``, then
    the first ``target:`` nested more deeply than it. A regex over the whole file
    would match whichever block came first and silently assert the wrong one.

    Args:
        status: The status name, e.g. ``project`` or ``patch``.

    Returns:
        The target as a float percentage.

    Raises:
        AssertionError: If the status or its target cannot be located, which is
            a change to the file's shape rather than to its policy -- both need
            a human, and one of them only needs a human.
    """
    lines = _codecov_text().splitlines()
    header = re.compile(rf"^(\s*){re.escape(status)}:\s*$")
    target = re.compile(r"^\s*target:\s*([0-9]+(?:\.[0-9]+)?)%")
    for index, line in enumerate(lines):
        match = header.match(line)
        if match is None:
            continue
        indent = len(match.group(1))
        for nested in lines[index + 1 :]:
            if nested.strip() and len(nested) - len(nested.lstrip()) <= indent:
                break
            found = target.match(nested)
            if found:
                return float(found.group(1))
        message = f"codecov.yml has a `{status}` status with no `target` percentage"
        raise AssertionError(message)
    message = f"codecov.yml has no `{status}` status"
    raise AssertionError(message)


def test_the_project_coverage_floor_is_95_percent() -> None:
    """The decision, in the file coverage.py reads.

    Asserted against the literal rather than against ``> 0`` or a range: a floor
    that only has to be *some* number is not the floor this repository chose, and
    the point of the test is to make the next edit to this line an explicit one.
    """
    floor = _fail_under_percent()
    assert floor == COVERAGE_FLOOR_PERCENT, (
        f"fail_under is {floor:g}%, expected {COVERAGE_FLOOR_PERCENT:g}%; "
        "raise it back, or change COVERAGE_FLOOR_PERCENT and codecov.yml in the "
        "same commit -- never one alone"
    )


def test_the_floor_is_read_from_the_report_table_not_the_first_match(
    tmp_path: Path,
) -> None:
    """A ``fail_under`` in an *earlier* table must not answer this question.

    The regression this pins is a disagreement between the two readers that are
    meant to be one number: the gate parses ``[tool.coverage.report]`` with
    ``tomllib``, and until this test existed the contract helper searched the
    whole file and returned the first match. Add a decoy key in a table that
    sorts above the real one and the helper grades 95 while the gate enforces
    something else — both green, neither wrong about its own input.

    The decoy is deliberately ``95``, the number the other two tests want: the
    old regex returned it and passed, which is precisely how a wrong reader
    hides.
    """
    copy = tmp_path / "pyproject.toml"
    copy.write_text(
        "[tool.other]\nfail_under = 95\n\n[tool.coverage.report]\nfail_under = 12.5\n",
        encoding="utf-8",
    )

    assert _fail_under_percent(copy) == pytest.approx(12.5), (
        "the earlier table's fail_under won; the helper must answer for "
        "[tool.coverage.report] specifically"
    )


def test_codecov_enforces_the_same_floor_as_pyproject() -> None:
    """Both configurations state one number, and they must be *the same* one.

    Two independent assertions of the literal (above and here) would let the pair
    drift apart and both stay green. This one compares the parsed values, so a
    change to either side alone turns this red -- which is the whole reason
    `codecov.yml`'s comment names this test.
    """
    pyproject_floor = _fail_under_percent()
    codecov_target = _codecov_status_target("project")

    assert codecov_target == pyproject_floor, (
        f"codecov.yml project target is {codecov_target:g}% but pyproject "
        f"fail_under is {pyproject_floor:g}%; the two are meant to be one "
        "decision written twice"
    )
    assert codecov_target == COVERAGE_FLOOR_PERCENT

    patch_target = _codecov_status_target("patch")
    assert patch_target == COVERAGE_FLOOR_PERCENT, (
        f"codecov.yml patch target is {patch_target:g}%; PR #4 sat red on an "
        "implicit 100% here while every local gate passed"
    )


def test_the_per_file_gate_is_wired_where_the_suite_is_run() -> None:
    """A floor nobody runs is a comment.

    The gate reads `fail_under` itself, so what needs guarding is the wiring: it
    has to run after the suite produces the report, in both places that run the
    suite for real. `make testmon` deliberately does not -- it runs a subset with
    `--no-cov` and there is no report, which the gate treats as a failure rather
    than a pass.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "coverage-per-file" in makefile, (
        "the Makefile no longer defines or chains the per-file gate"
    )
    assert "@$(MAKE) -s coverage-per-file" in makefile, (
        "`make test` runs pytest and stops; the gate after it was removed, so "
        "the floor would only be checked by whoever remembered to run it"
    )

    workflow = CI_PATH.read_text(encoding="utf-8")
    assert "scripts/coverage_gate.py" in workflow, (
        "the CI coverage step no longer runs the per-file gate; a PR could drop "
        "one file below the floor and every check would stay green"
    )
    assert workflow.index("coverage_gate.py") > workflow.index("--cov-report=xml"), (
        "the gate runs before the report that feeds it"
    )


def test_contributing_states_the_same_coverage_floor() -> None:
    """The number a contributor reads must be the number the gates enforce.

    ``CONTRIBUTING.md`` documents ``make test`` in prose. The prose carried the
    pre-D9 floor (80%) for the whole branch that raised it to 95% -- found in
    review of PR #4 -- so a reader following the document was told a gate
    existed that the gates no longer run. This is the same one-number-many-places
    contract as ``codecov.yml`` above, but for the copy a human reads, and
    written as a *floor-consistency* assertion rather than a literal: changing
    the floor for real means editing ``COVERAGE_FLOOR_PERCENT``, which this test
    tracks automatically, instead of hunting a fourth copy.

    The regex is tolerant of the phrasing a floor change implies (``at least``,
    ``>=``, a new number) so a legitimate raise does not red this test while the
    other three copies change -- but any claim *below* the floor, or no claim at
    all, is a contradiction with the gates and must be an explicit edit.
    """
    text = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    stated = [
        float(match.group(1))
        for match in re.finditer(
            r"coverage[^\n]*?(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE
        )
    ]
    assert stated, (
        "CONTRIBUTING.md no longer states a coverage figure for `make test`; "
        "the floor contributors are held to must be readable from the "
        "contributing guide, or the document and the gates disagree by omission"
    )
    too_low = sorted({s for s in stated if s < COVERAGE_FLOOR_PERCENT})
    assert not too_low, (
        f"CONTRIBUTING.md states coverage figure(s) {too_low}% but the enforced "
        f"floor is {COVERAGE_FLOOR_PERCENT:g}% (pyproject.toml fail_under, "
        "codecov.yml, and the per-file gate). A contributor told the floor is "
        "lower than it is will treat a red suite as a mystery rather than as "
        "their missing test. Update the prose to match, or raise the floor in "
        "all four places in one commit"
    )


#: The docstring threshold this repository commits to, in percent. Two
#: configurations state it -- `pyproject.toml`, which the deterministic
#: `scripts/docstring_gate.py` reads, and `.coderabbit.yaml`, whose docstring
#: pre-merge check CodeRabbit runs on the diff. Asserted equal below, so neither
#: can be edited alone.
def _coderabbit_docstrings_threshold() -> float:
    """Read the `threshold` under `.coderabbit.yaml`'s `docstrings:` block.

    There is no YAML parser available to tests -- same reason
    `_codecov_status_target` gives -- so this walks indentation under the
    `docstrings:` header, which is also what stops it matching some *other*
    `threshold` key in a file that carries one per check.

    Returns:
        The threshold as a float percentage.

    Raises:
        AssertionError: If the block or its `threshold` cannot be located. That is
            a change to the config's shape rather than to its policy, and both
            need a human -- but only one of them needs one urgently.
    """
    lines = _coderabbit_text().splitlines()
    header = re.compile(r"^(\s*)docstrings:\s*$")
    threshold = re.compile(r"^\s*threshold:\s*([0-9]+(?:\.[0-9]+)?)")
    for index, line in enumerate(lines):
        match = header.match(line)
        if match is None:
            continue
        indent = len(match.group(1))
        for nested in lines[index + 1 :]:
            if nested.strip() and len(nested) - len(nested.lstrip()) <= indent:
                break
            found = threshold.match(nested)
            if found:
                return float(found.group(1))
        block = CODERABBIT_DOCSTRINGS_BLOCK
        message = f".coderabbit.yaml has an `{block}` block with no `threshold`"
        raise AssertionError(message)
    message = ".coderabbit.yaml has no `docstrings` pre-merge check"
    raise AssertionError(message)
