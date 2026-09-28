"""Tests for the Codacy pre-push gate in ``scripts/codacy_gate.py``.

The gate exists because ``codacy-cli analyze`` exits 0 in every situation that
matters -- clean tree, a confirmed finding, a missing config, a malformed one.
Its exit status carries no information, so the gate reads findings out of SARIF
instead. These tests cover that decision, plus the staging rules, and they are
written against the measured behaviour rather than the intended behaviour: each
one below was a real way this gate was observed to be wrong.

The analysis itself is not exercised here. It needs the ``codacy-cli`` binary,
its tool cache, and roughly ninety seconds, so a test that ran it would make
``make test`` slow and network- or cache-dependent. What is tested is the
decision logic around it: that a finding count maps to the right exit status,
and that the staged tree is assembled the way the analyser requires.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

GATE_SCRIPT = Path("scripts/codacy_gate.py")


def _load_gate() -> ModuleType:
    """Import the gate as a module, since it lives in ``scripts/`` not a package.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("codacy_gate", GATE_SCRIPT)
    assert spec is not None, f"cannot build a spec for {GATE_SCRIPT}"
    assert spec.loader is not None, f"{GATE_SCRIPT} has no loader: {spec}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    return _load_gate()


def test_tests_tree_is_staged_under_a_name_the_analyser_scans(
    gate: ModuleType,
) -> None:
    """Assert ``tests/`` is not staged under its own name.

    The analyser skips a directory named ``tests`` silently: a planted
    ``subprocess(..., shell=True)`` in ``tests/`` produced no finding while the
    identical file in ``scripts/`` was reported, and renaming the directory to
    ``teststuff/`` made the same file appear again. Nothing in the CLI's output
    announces the skip, so a gate that staged it faithfully would report clean
    while never reading half the first-party Python.
    """
    staged = gate.STAGED_TREES
    assert isinstance(staged, dict), (
        f"STAGED_TREES is a {type(staged).__name__}, so the staging map cannot be "
        "read as a mapping of tree name to staged name"
    )

    assert "tests" in staged, (
        "tests/ is no longer analysed at all, so the gate cannot see findings in "
        "the suite that exercises the package"
    )
    assert staged["tests"] != "tests", (
        "tests/ is staged under its own name, which the analyser skips without "
        "reporting. Stage it under a neutral name instead"
    )


def test_every_staged_tree_exists_on_disk(gate: ModuleType) -> None:
    """Assert every tree the gate claims to analyse is really present.

    A missing tree means the gate is reading less than it reports, which is the
    same class of defect as the skip above: the gate passes because it looked
    at nothing.
    """
    missing = [tree for tree in gate.STAGED_TREES if not Path(tree).is_dir()]

    assert not missing, (
        f"the gate analyses {sorted(gate.STAGED_TREES)} but {missing} "
        f"do not exist in the repository, so it would stage an incomplete tree"
    )


def test_findings_map_to_a_nonzero_result(gate: ModuleType, tmp_path: Path) -> None:
    """Assert a non-empty SARIF result list is reported as findings.

    Returns ``None`` and not ``[]`` for a missing or malformed report, and the
    distinction is the whole reason this function exists: an unreadable report
    means the analysis did not run, which is not the same as an analysis that
    found nothing.
    """
    report = tmp_path / "out.sarif"
    document = {
        "runs": [
            {
                "results": [
                    {"ruleId": "a.b.subprocess-shell-true", "message": {"text": "x"}},
                ],
            },
        ],
    }
    report.write_text(json.dumps(document), encoding="utf-8")

    results = gate._findings(report)

    assert results is not None, "a well-formed SARIF report was read as unreadable"
    assert len(results) == 1, f"expected 1 result, read {len(results)}"


@pytest.mark.parametrize(
    "name",
    ["absent.sarif", "truncated.sarif", "no_runs.sarif"],
)
def test_unreadable_report_is_distinguishable_from_no_findings(
    gate: ModuleType,
    tmp_path: Path,
    name: str,
) -> None:
    """Assert a report that cannot be read yields ``None``, never an empty list.

    Returning ``[]`` for a report that was never produced would make a crashed
    analysis indistinguishable from a clean one, which is the failure this gate
    was written to prevent.
    """
    report = tmp_path / name
    if name == "truncated.sarif":
        report.write_text('{"runs": [', encoding="utf-8")
    elif name == "no_runs.sarif":
        report.write_text(json.dumps({"version": "2.1.0"}), encoding="utf-8")

    assert gate._findings(report) is None, (
        f"{name} was read as a report with zero findings rather than as an "
        "unreadable report, so a failed analysis would pass this gate"
    )


def test_describe_extracts_rule_location_and_message(gate: ModuleType) -> None:
    """Assert a finding is rendered with the three facts needed to act on it."""
    rendered = gate._describe({
        "ruleId": (
            "codacy.tools-configs.python.lang.security.audit.subprocess-shell-true"
        ),
        "message": {"text": "Found 'subprocess' function 'run' with 'shell=True'."},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "src/bigdata_mcp/main.py"},
                    "region": {"startLine": 12},
                },
            },
        ],
    })

    assert "subprocess-shell-true" in rendered, (
        f"the rendered finding names no rule, so it cannot be looked up: {rendered!r}"
    )
    assert "src/bigdata_mcp/main.py:12" in rendered, (
        f"the rendered finding carries no file:line, so it cannot be opened: "
        f"{rendered!r}"
    )
    assert "shell=True" in rendered, (
        f"the rendered finding omits the rule message, so it says what is wrong "
        f"but not why: {rendered!r}"
    )


def test_missing_tool_fails_rather_than_passing(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert the gate exits nonzero when ``codacy-cli`` is absent.

    Skipping instead would make a machine without the tool report the same
    green line as a machine where the analysis ran and found nothing.
    """
    monkeypatch.setattr(gate.shutil, "which", lambda _name: None)

    status = gate.main()

    assert status == 1, (
        f"the gate returned {status} with codacy-cli missing. It must fail: a "
        "gate that cannot run must never be indistinguishable from one that "
        "found nothing"
    )
