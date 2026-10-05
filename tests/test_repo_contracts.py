"""Contract tests over the repository-root ``SPEC.md``.

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
from functools import cache
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "SPEC.md"

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
    return line.lstrip().startswith("|")


def _is_separator_row(line: str) -> bool:
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
