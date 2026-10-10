"""Contract tests over the documents that decide what the code is *for*.

Two things are guarded here: ``SPEC.md``, and the coverage policy split across
``pyproject.toml`` and ``codecov.yml``.

These guard a failure mode no other gate in this repository can see: a section
of the specification disappearing during an edit. Everything else here --
ruff, ``ty``, the complexity gate, the test suite -- reads source code. None of
it reads the document that decides what the source code is *for*, so a rewrite
can drop 200 researched lines and every gate stays green.

``SPEC.md`` is being rewritten from v1 to v2 (see
``docs/specs/scope-realignment-mcp-server-v2_20261005_124933.spec.md`` §5 and §7,
change C1). v2 realigns the product's scope: it declares a configurable posture
instead of asserting read-only unconditionally, and it adds the two capabilities
v1 never had -- the custom-port family and MCP-server aggregation. A rewrite
that loses any of that is a silent regression, so each commitment below has a
test.

They are **contract tests, not behaviour tests**. They read a file. The
repository has already recorded why that is normally the wrong trade: *"a test
that reads a file is a contract test, and a contract test in ``tests/`` couples
the product suite to CI wiring"* (``docs/research/README.md`` D5). It is
justified here because the thing being protected is a document, and the failure
is silent by construction.

Every assertion is mutation-verified. Break the feature in ``SPEC.md``, watch
the named test go red, restore it. A test here that cannot fail is worse than
no test, because it occupies the slot where a real check would go.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

import pytest
from docstring_gate import SCAN_ROOTS

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


def test_every_v1_section_is_accounted_for_in_the_traceability_table() -> None:
    """Assert the traceability table maps every top-level section of v1.

    Rows may be sub-sections (``9.2 ETA analysis``); only the leading integer is
    collected, because the guarantee is that no top-level section of v1 is
    dropped from the document without appearing in the table at all.
    """
    cells = _traceability_table_cells()
    mapped: dict[int, str] = {}
    unparsed: list[str] = []
    for cell in cells:
        match = _LEADING_INTEGER.match(cell)
        if match is None:
            unparsed.append(cell)
        else:
            mapped.setdefault(int(match.group(1)), cell)

    assert not unparsed, (
        f"{len(unparsed)} traceability row(s) do not begin with a section "
        f"number, e.g. {unparsed[:3]!r}. A row the parser cannot read is a row "
        "nobody is asserting anything about, which is precisely how a section "
        "goes missing"
    )

    missing = sorted(EXPECTED_V1_SECTIONS - mapped.keys())
    extra = sorted(mapped.keys() - EXPECTED_V1_SECTIONS)
    assert not missing, (
        f"v1 section(s) {missing} do not appear in the traceability table in "
        f"{SPEC_PATH.name}. Each must be mapped, or explicitly marked deleted, "
        "so that dropping a section during the rewrite is a visible event rather "
        "than a silent deletion"
    )
    assert not extra, (
        f"traceability row(s) {extra} name a section number that does not exist "
        f"in v1. {SPEC_PATH.name} v2 had 20 top-level sections; a row pointing "
        "past 20 is either a typo or a pointer to something the parser is "
        "misreading"
    )


def test_research_bearing_subsections_survive_the_rewrite() -> None:
    """Assert the expensive v1 findings each get their own traceability row.

    ``test_every_v1_section_is_accounted_for`` only checks leading integers, so a
    rewrite could keep the ``9 State`` row and drop ``9.2 ETA analysis`` -- 155
    lines of measurement about the RM retention wall, the ``n`` guard and
    censoring disclosure -- and pass. These are the sections that would be most
    expensive to reproduce and least possible to notice missing, so each is
    named individually.
    """
    identifiers = {
        match.group(1)
        for cell in _traceability_table_cells()
        if (match := _DOTTED_IDENTIFIER.match(cell)) is not None
    }
    missing = sorted(VERBATIM_SUBSECTIONS - identifiers)
    assert not missing, (
        f"traceability table has no row for v1 sub-section(s) {missing}. Each "
        "holds measured or source-verified findings -- exit codes that are "
        "indistinguishable, `-stat` eating a `%` path as its format string, "
        "`-ls` columns shifting at 100 entries, the retention wall that makes a "
        "longer ETA lookback impossible. Dropping the row is how they disappear"
    )


def test_spec_declares_both_posture_modes() -> None:
    """Assert the posture model is declared as configurable, not asserted.

    v1 §2.1 claimed "strictly read-only" as an architectural property. v2
    replaces that with a configured ``mode``. Recording only ``read_only`` would
    reproduce v1's unconditional claim in a form that looks configurable.

    Checked in **two** places, because a bare substring check was observed to
    pass while the model was reverted. ``read_write`` appears in eleven places of
    §1-§20 prose; changing only the configuration schema -- the one place a
    deployer would actually edit -- left the substring assertion green. So the
    configuration schema is asserted directly.
    """
    _assert_literals_present(
        POSTURE_LITERALS,
        "v2 declares posture as config: 'read_only' (the default) and "
        "'read_write', with the capability vocabulary generated from it.",
    )

    config = _section_text("16. Configuration")
    assert 'mode = "read_only"' in config, (
        'the configuration schema in §16 no longer declares mode = "read_only" '
        "as the default. Posture has to be settable by whoever edits the config, "
        "not only described in prose, or the model is decorative"
    )
    assert '"read_write"' in config, (
        'the configuration schema in §16 does not list "read_write" as a legal '
        "value of mode. §2.1 promises both modes; if the schema cannot express "
        "the second one, the promise is not implemented"
    )


def test_spec_commits_to_custom_port_family_and_mcp_aggregation() -> None:
    """Assert both capabilities absent from v1 are committed to in writing.

    v1 mentions ``web_session`` once, as a ``Literal`` type, and mentions other
    MCP servers not at all. Neither was rejected or deferred; neither was
    considered. If v2 narrows back to HDFS/YARN/Solr, these literals go.
    """
    _assert_literals_present(
        NEW_CAPABILITY_LITERALS,
        "v1 scope is the custom-port family (arbitrary web portals, "
        "credentials or OIDC/OAuth, portal APIs as resource tooling, manually "
        "configured) plus aggregation of other MCP servers via mcp_client.",
    )


def test_spec_bounds_the_always_loaded_tool_surface() -> None:
    """Assert the fixed meta-tool surface is named in the spec.

    The property under test is the *bound*: ~20 always-loaded tools whatever the
    configuration, because ~21 already degrades tool selection for the agent.
    Naming the six meta-tools is what makes the bound checkable rather than
    aspirational.
    """
    _assert_literals_present(
        META_TOOL_LITERALS,
        "v2 commits to a bounded always-loaded surface: 13 curated built-ins "
        "plus six fixed meta-tools, so the count does not grow with the number "
        "of configured portals or proxied MCP servers.",
    )


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


def test_the_docstring_threshold_is_100_percent() -> None:
    """The decision, in the file the gate reads.

    Asserted against the literal rather than against `> 0` or a range: a floor
    that only has to be *some* number is not the floor this repository chose, and
    the point of the test is to make the next edit to this line an explicit one.
    100 rather than the usual 80 is the same decision `[tool.coverage.report]`
    records: a partial threshold is an invitation to leave the remainder
    undocumented.
    """
    assert _docstrings_threshold_percent() == pytest.approx(
        DOCSTRING_THRESHOLD_PERCENT
    ), (
        f"pyproject [tool.docstrings].threshold is "
        f"{_docstrings_threshold_percent():g}%, expected "
        f"{DOCSTRING_THRESHOLD_PERCENT:g}%; contract tests are also written in "
        "prose, so a partial threshold would let that drift"
    )


def test_coderabbit_enforces_the_same_docstring_threshold() -> None:
    """Both configurations state one number, and they must be *the same* one.

    Two independent assertions of the literal would let the pair drift apart and
    both stay green. This one compares the parsed values, so a change to either
    side alone turns it red.

    The two are not duplicates. CodeRabbit's reviews a *diff*, so a definition
    that loses its docstring stops being reviewed once it ages out of a pull
    request's range; the gate here reads the whole tree on every commit. That is
    the gap, and the pair agreeing is what keeps the stricter of the two from
    silently becoming the only one.
    """
    threshold = _coderabbit_docstrings_threshold()
    assert threshold == pytest.approx(DOCSTRING_THRESHOLD_PERCENT)
    assert threshold == pytest.approx(_docstrings_threshold_percent())


def test_the_docstring_gate_is_wired_into_the_commit_stage() -> None:
    """A threshold nobody runs locally is a comment.

    The gate is the deterministic half, so it has to run at the point where the
    change is one keystroke from being undone -- not only on the pull request,
    which is precisely the layer that missed 71% coverage of `tests/`.

    Asserted against the wiring rather than the exit code alone: the script
    already works, and what has regressed before in this repository is the hook
    being removed from the stage rather than the script being broken.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "docstrings:" in makefile, (
        "the Makefile no longer defines the docstring target"
    )
    assert "scripts/docstring_gate.py" in makefile, (
        "the Makefile's docstring target no longer runs the gate; the threshold "
        "would be enforced nowhere"
    )

    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "docstring-gate" in config, (
        "the docstring gate is no longer in the pre-commit stage; a contributor "
        "would only meet it on the pull request, which is the layer whose diff "
        "scope is what left the tree at 71%"
    )
    hook = config.index("docstring-gate")
    assert "make docstrings" in config, (
        "the hook no longer routes through `make docstrings`, so the hook and the "
        "Makefile can drift -- the same failure `pytest-testmon` documents"
    )
    assert hook < config.index("pytest-testmon")


def test_the_docstring_gate_covers_the_same_roots_as_the_linters() -> None:
    """The gate reads `src`, `tests` and `scripts`, and never a narrower set.

    A root dropped from the gate is a root silently removed from the rule, which
    is the failure mode this whole gate exists to close: a check that reports
    clean because it stopped looking is worse than one that fails loudly.

    Mutation evidence: D2 above, applied to the gate. Here it is the
    configuration that is mutated -- deleting `"scripts"` from `SCAN_ROOTS` --
    which keeps every other test green because the gate would still have read
    two roots of the three.
    """
    assert set(SCAN_ROOTS) == {"src", "tests", "scripts"}
