"""Shared readers for the repository contract tests.

Split out of ``test_repo_contracts.py`` when that module crossed Codacy's
500-line limit. Three suites read the same documents with the same helpers — the
``SPEC.md`` suite, the coverage-floor suite, and the tooling suite — so the
helpers are a dependency of all three and belong to none of them.

A helper stays here only when it reads a file the policy is written in. One that
merely builds a fixture stays with the suite that uses it.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "SPEC.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CODECOV_PATH = REPO_ROOT / "codecov.yml"
MAKEFILE_PATH = REPO_ROOT / "Makefile"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"

#: The coverage floor this repository commits to, in percent. Three
#: configurations state it -- ``pyproject.toml`` (coverage.py's project total),
#: ``codecov.yml`` (Codecov's ``project`` status), and, by reading the first, the
#: per-file gate in ``scripts/coverage_gate.py``. Asserted equal below so that no
#: two can be edited apart.
COVERAGE_FLOOR_PERCENT = 95.0

#: Every top-level section of ``SPEC.md`` v1. The traceability table must map
#: each of these, or explicitly account for it, so none can vanish unremarked.
EXPECTED_V1_SECTIONS = frozenset(range(1, 21))

#: The marker that identifies the traceability table's header row. It is
#: searched as a literal because the table is a human-authored document, not a
#: generated artifact -- its shape is the contract.
TRACEABILITY_HEADER_MARKER = "v1 §"

#: The posture model v2 commits to. Both must appear: `read_only` alone is what
#: v1 asserted unconditionally, and is the exact claim being replaced.
POSTURE_LITERALS = ("read_only", "read_write")

#: The two capabilities that were **absent** from v1 and whose absence this
#: rewrite exists to fix. Each carries its own literal from the transport/config
#: vocabulary it introduces.
#:
#: ``custom-port`` is the family name and ``web_session`` the transport that
#: implements it; pinning both catches a v2 that writes the prose but drops the
#: capability from the config schema, which is the failure that actually matters.
NEW_CAPABILITY_LITERALS = ("custom-port", "web_session", "mcp_client")

#: Sub-sections of v1 holding measured or source-verified findings that cost
#: weeks to produce. Top-level coverage is not enough for these: the ``9`` row
#: survives happily while the ``9.2`` row -- 155 lines of ETA measurement -- is
#: dropped, and that loss is invisible to every other gate in this repository.
VERBATIM_SUBSECTIONS = frozenset({"4.2", "9.2", "11.1.1", "11.3", "13.1.2"})

#: The fixed meta-tool surface that bounds the always-loaded tool count. If the
#: surface regresses to one tool per portal endpoint, these names disappear.
META_TOOL_LITERALS = (
    "list_portals",
    "describe_portal_endpoint",
    "call_portal_endpoint",
    "list_mcp_servers",
    "describe_mcp_tool",
    "call_mcp_tool",
)

_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_LEADING_INTEGER = re.compile(r"^(\d+)")
_DOTTED_IDENTIFIER = re.compile(r"^(\d+(?:\.\d+)*)")


@cache
def _spec_text() -> str:
    """Return the contents of ``SPEC.md`` once per process.

    Cached because four tests read the same file and the four reads must observe
    the same bytes: a rewrite landing mid-run would otherwise let half the suite
    grade v1 and half grade v2.
    """
    assert SPEC_PATH.is_file(), (
        f"specification not found at {SPEC_PATH}. These contract tests assert "
        "properties of that document; without it they cannot be evaluated, and "
        "silently skipping would leave the scope unguarded"
    )
    return SPEC_PATH.read_text(encoding="utf-8")


def _table_cells(line: str) -> list[str]:
    """Return one markdown table row split into its trimmed cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_row(line: str) -> bool:
    """Report whether a line counts as a row of the table currently being scanned.

    Indentation is tolerated because ``SPEC.md`` is hand-authored and a table
    nested under a list item is still a table. More importantly, this predicate is
    what *ends* the scan in ``_traceability_table_cells`` — the first line that is
    not a row is taken to mean the table is over — so a stricter test here would
    truncate the table silently and report every row below the cutoff as absent,
    which reads like a content regression rather than a parsing one.

    Args:
        line: One line of the document.

    Returns:
        True if the line, once its leading whitespace is removed, opens with a
        pipe.
    """
    return line.lstrip().startswith("|")


def _is_separator_row(line: str) -> bool:
    """Report whether a row is markdown's ``|---|---:|`` divider rather than data.

    Matched cell by cell because the divider is the only row in a markdown table
    that carries no content, and it has to be skipped: it would otherwise be the
    one row reported as a traceability entry that does not begin with a section
    number. Requiring *every* cell to be dashes is what keeps a data row safe —
    a cell of prose never matches, however it is punctuated.

    Args:
        line: One line of the table.

    Returns:
        True if each cell is dashes with at most one colon, which is the whole of
        what ``_SEPARATOR_CELL`` accepts.
    """
    return all(_SEPARATOR_CELL.match(cell) for cell in _table_cells(line))


def _traceability_table_cells() -> list[str]:
    """Return the first cell of every data row in the traceability table.

    Identifies the table by its header row whose **first cell** is exactly
    ``v1 §`` rather than by position or by a bare substring match. Both weaker
    strategies were tried and both broke against real prose: a positional lookup
    silently shifts when a section is inserted above the table, and a substring
    match latches onto any incidental mention such as a scope table's
    ``carried from v1 §11`` cell, then reports twelve unparseable rows and looks
    like a content failure rather than a parsing one. Requiring the whole cell
    makes prose that happens to mention a v1 section harmless.
    """
    lines = _spec_text().splitlines()
    header = next(
        (
            index
            for index, line in enumerate(lines)
            if _is_table_row(line)
            and _table_cells(line)[:1] == [TRACEABILITY_HEADER_MARKER]
        ),
        None,
    )
    assert header is not None, (
        f"{SPEC_PATH.name} contains no markdown table whose header row includes "
        f"{TRACEABILITY_HEADER_MARKER!r}. v2 must carry an explicit v1-to-v2 "
        "traceability table, because the rewrite touches most sections and an "
        "amendment would leave one file describing two products. Without the "
        "table there is no record of what happened to each v1 section, which is "
        "the exact failure these tests exist to catch"
    )

    cells: list[str] = []
    for line in lines[header + 1 :]:
        if not _is_table_row(line):
            break
        if _is_separator_row(line):
            continue
        cells.append(_table_cells(line)[0])
    assert cells, (
        f"the traceability table in {SPEC_PATH.name} was found by its header but "
        "contains no data rows beneath it, so it accounts for nothing"
    )
    return cells


def _section_text(heading: str) -> str:
    """Return the text of the ``## heading`` section, up to the next ``##``.

    Several assertions below are about *where* a commitment appears, not merely
    whether it appears. A document that mentions ``read_write`` in eleven places
    of prose while its configuration schema declares only ``read_only`` has
    reverted the model and still satisfies a bare substring check -- which was
    observed happening, and is why this helper exists.
    """
    lines = _spec_text().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"## {heading}")),
        None,
    )
    assert start is not None, f"{SPEC_PATH.name} has no '## {heading}' section"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _assert_literals_present(literals: tuple[str, ...], reason: str) -> None:
    """Assert every literal appears somewhere in ``SPEC.md``."""
    text = _spec_text()
    missing = [literal for literal in literals if literal not in text]
    assert not missing, (
        f"{SPEC_PATH.name} does not contain {missing}. {reason} "
        f"Present in v1, absent from v2 is a regression, not a simplification"
    )


CODERABBIT_DOCSTRINGS_BLOCK = "docstrings:"


@cache
def _docstrings_threshold_percent(path: Path = PYPROJECT_PATH) -> float:
    """Return `[tool.docstrings].threshold` from `pyproject.toml`.

    Args:
        path: The `pyproject.toml` to read.

    Returns:
        The threshold as a float percentage.

    Raises:
        AssertionError: If the table or key is missing. Written as an assertion
            rather than an exception because a missing threshold is a state the
            gate turns into exit 2, and this test's job is to notice that first.
    """
    assert path.is_file(), f"pyproject.toml not found at {path}"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))["tool"]["docstrings"]
        threshold = value["threshold"]
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        message = (
            "pyproject.toml has no [tool.docstrings].threshold; "
            "scripts/docstring_gate.py would exit 2 on every run"
        )
        raise AssertionError(message) from exc
    return float(threshold)


@cache
def _coderabbit_text() -> str:
    """Return `.coderabbit.yaml` once per process, or fail loudly.

    Returns:
        The file's text.

    The missing-file case is asserted inline rather than listed in `Raises:`,
    because `DOC502` only accepts an exception the function raises itself and this
    one is raised by the `assert`. The reason it is an assertion at all is above:
    CodeRabbit's checks are what pair with the gate's, so a deleted config would
    leave the repository's strictness stated in exactly one place, undetected.
    """
    path = REPO_ROOT / ".coderabbit.yaml"
    assert path.is_file(), (
        ".coderabbit.yaml not found; the docstring threshold CodeRabbit enforces "
        "cannot be asserted without it, and skipping would leave this half of the "
        "contract unguarded"
    )
    return path.read_text(encoding="utf-8")


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


DOCSTRING_THRESHOLD_PERCENT = 100

#: The YAML block in `.coderabbit.yaml` whose `threshold` is the CodeRabbit half.
CODERABBIT_DOCSTRINGS_BLOCK = "docstrings:"


@cache
def _docstrings_threshold_percent(path: Path = PYPROJECT_PATH) -> float:
    """Return `[tool.docstrings].threshold` from `pyproject.toml`.

    Args:
        path: The `pyproject.toml` to read.

    Returns:
        The threshold as a float percentage.

    Raises:
        AssertionError: If the table or key is missing. Written as an assertion
            rather than an exception because a missing threshold is a state the
            gate turns into exit 2, and this test's job is to notice that first.
    """
    assert path.is_file(), f"pyproject.toml not found at {path}"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))["tool"]["docstrings"]
        threshold = value["threshold"]
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        message = (
            "pyproject.toml has no [tool.docstrings].threshold; "
            "scripts/docstring_gate.py would exit 2 on every run"
        )
        raise AssertionError(message) from exc
    return float(threshold)


@cache
def _coderabbit_text() -> str:
    """Return `.coderabbit.yaml` once per process, or fail loudly.

    Returns:
        The file's text.

    The missing-file case is asserted inline rather than listed in `Raises:`,
    because `DOC502` only accepts an exception the function raises itself and this
    one is raised by the `assert`. The reason it is an assertion at all is above:
    CodeRabbit's checks are what pair with the gate's, so a deleted config would
    leave the repository's strictness stated in exactly one place, undetected.
    """
    path = REPO_ROOT / ".coderabbit.yaml"
    assert path.is_file(), (
        ".coderabbit.yaml not found; the docstring threshold CodeRabbit enforces "
        "cannot be asserted without it, and skipping would leave this half of the "
        "contract unguarded"
    )
    return path.read_text(encoding="utf-8")


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
