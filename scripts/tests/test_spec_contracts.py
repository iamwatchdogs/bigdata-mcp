"""Contract tests: see the module docstring of the suite they belong to."""

from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING

from repo_support import _DOTTED_IDENTIFIER
from repo_support import _LEADING_INTEGER
from repo_support import EXPECTED_V1_SECTIONS
from repo_support import POSTURE_LITERALS
from repo_support import PYPROJECT_PATH
from repo_support import SPEC_PATH
from repo_support import TRACEABILITY_HEADER_MARKER
from repo_support import VERBATIM_SUBSECTIONS
from repo_support import _assert_literals_present
from repo_support import _is_separator_row
from repo_support import _is_table_row
from repo_support import _section_text
from repo_support import _spec_text
from repo_support import _table_cells
from repo_support import _traceability_table_cells

if TYPE_CHECKING:
    import pytest


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
        "in v1. The traceability table accounts for v1's 20 top-level sections; a "
        "row pointing past 20 is either a typo or a pointer to something the "
        "parser is misreading"
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


def test_every_declared_dependency_matches_the_specs_own_table() -> None:
    """A dependency line and its §4 row are one decision written twice.

    `aiohttp` has its own test because its pin is a CVE. The rest of the table was
    unguarded, and it drifted: §4's implementation note said `dependencies = []`
    while three dependencies were declared, the `mcp` row pinned 2.2.0 against a
    requirement of `>=2.3.0`, and the checker row named `pyright` where §17.2 now
    names `ty`. The spec is the document a reader treats as authoritative, so a
    wrong row there is worse than a missing one.

    Asserted for every declared dependency, so adding a fourth to
    `pyproject.toml` without a row here is a failure rather than a gap.

    Mutation evidence: changing the `mcp` row back to `2.2.0` turns this red, as
    does bumping the requirement in `pyproject.toml` alone.
    """
    spec = _spec_text()
    rows = {
        cells[0].strip("`"): cells[1].strip()
        for cells in (
            _table_cells(line)
            for line in spec.splitlines()
            if line.strip().startswith("|")
            and len(_table_cells(line)) > 2
            and _table_cells(line)[0].strip("`").isidentifier()
        )
        if cells[1].startswith("≥")
    }

    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    declared = {
        requirement.split(">=")[0]: requirement.split(">=")[1]
        for requirement in pyproject["project"]["dependencies"]
        if ">=" in requirement
    }

    missing = sorted(set(declared) - set(rows))
    assert not missing, (
        f"{missing} declared in pyproject.toml but absent from §4's dependency "
        "table. A dependency with no row is one nobody wrote down a reason for"
    )
    for name, floor in sorted(declared.items()):
        row = rows[name].lstrip("≥")
        assert row == floor, (
            f"pyproject.toml requires {name}>={floor} while §4 pins {rows[name]}. "
            "The two are one decision; a reader who trusts the spec resolves the "
            "release the requirement no longer asks for"
        )


def test_the_aiohttp_floor_is_the_pin_the_spec_gives_for_it() -> None:
    """The CVE pin in §4 is the reason for the floor, so the two must agree.

    §4 pins `aiohttp` at ≥3.14.1 because CVE-2026-54280 leaks the response body
    on a mid-body disconnect, which is the streaming-abort path this server takes
    on every deadline. Nothing else in the tree compares that number to
    `pyproject.toml`'s: the dependency line sat at `>=3.13.3` while the spec,
    the sole record of *why*, said 3.14.1 — and `uv` happily resolves a
    3.13.x that carries the CVE.

    Mutation evidence: reverting the floor in `pyproject.toml` to `>=3.13.3`
    turns this red, as does loosening the pin in the spec's table — either way
    the two numbers disagree and the CVE reasoning is no longer enforced
    anywhere.
    """
    spec = _spec_text()
    pinned_row = next(
        line
        for line in spec.splitlines()
        if line.strip().startswith("|") and _table_cells(line)[:1] == ["`aiohttp`"]
    )
    pinned = _table_cells(pinned_row)[1]
    version = pinned.lstrip("≥>=").strip()
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", version), (
        f"the spec's §4 dependency table pins `aiohttp` as {pinned!r}, which this "
        "check cannot read as a version. A pin nobody can parse is a pin nobody "
        "enforces"
    )

    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    requirement = next(
        dependency
        for dependency in pyproject["project"]["dependencies"]
        if dependency.startswith("aiohttp")
    )
    assert requirement == f"aiohttp>={version}", (
        f"pyproject.toml declares {requirement!r} while §4 pins `{version}`. The "
        "spec's number exists because of CVE-2026-54280 on the streaming-abort "
        "path; a lower floor resolves a release that still carries it"
    )


def _traceability_rows() -> list[list[str]]:
    """Return every data row of the traceability table, cell by cell.

    `_traceability_table_cells` returns one cell per row, which is all the
    leading-integer check needs. This returns the whole row, because the
    disposition — *verbatim* or *amended* — is in the second cell and asserting
    on it is the only way to check what the table says *about* a section rather
    than merely that the section has a row.

    The table is found the same way its sibling finds it: by a header row whose
    first cell is exactly ``v1 §``. Two parsers of one table would let them
    disagree about where it ends.
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
        f"{TRACEABILITY_HEADER_MARKER!r}"
    )

    rows: list[list[str]] = []
    for line in lines[header + 1 :]:
        if not _is_table_row(line):
            break
        if _is_separator_row(line):
            continue
        rows.append(_table_cells(line))
    assert rows, f"the traceability table in {SPEC_PATH.name} has no data rows"
    return rows


def test_the_carryover_statement_agrees_with_the_traceability_table() -> None:
    """§1.1 says what carried over; §21 says how. They must not disagree.

    §1.1 listed §4.2 among the sections "carried over verbatim" while §21's own
    row for §4.2 reads "amended" — the blanket redirect ban replaced by the
    scoped policy, and two CVE paragraphs added. A document that calls a
    weakened section verbatim is a document nobody can check by reading one part
    of it, and §4.2 is the section whose change is the whole point of the
    rewrite.

    Both halves are asserted because they encode one decision twice: §1.1's
    claim, and §21's row. Reverting either has to fail.
    """
    spec = _spec_text()
    paragraph = spec[spec.index("**v2 keeps every verified research finding") :]
    paragraph = paragraph[: paragraph.index("\n\n")]
    sentence = next((each for each in paragraph.split(". ") if "verbatim" in each), "")
    assert sentence, (
        "the §1.1 carryover paragraph no longer says which sections moved "
        "unchanged. The distinction is the assertion's subject: a carryover "
        "with no verbatim claim is a carryover nobody can check"
    )
    called_verbatim = set(re.findall(r"\d+\.\d+(?:\.\d+)*", sentence))
    assert "4.2" not in called_verbatim, (
        f"§1.1 describes §4.2 as carried over verbatim (verbatim list: "
        f"{sorted(called_verbatim)}), but §21's traceability row for it says "
        "'amended' — the blanket redirect ban became the scoped policy and two "
        "CVE paragraphs were added"
    )
    assert "4.2" in paragraph, (
        "the carryover paragraph no longer names §4.2 at all. Its amendment is "
        "the single most consequential change in the rewrite and a paragraph "
        "that omits it reads as though the section carried over untouched"
    )

    amended_rows = [
        cells[0]
        for cells in _traceability_rows()
        if len(cells) > 1 and cells[1] == "amended"
    ]
    assert any(row.startswith("4.2 ") for row in amended_rows), (
        f"§21 no longer marks §4.2 as amended (rows marked amended: "
        f"{amended_rows}). The row is the record that the redirect ban was "
        "rescoped; a reader who trusts §1.1's 'verbatim' and finds this row "
        "missing has no way to learn the section changed"
    )


def test_the_sixteen_example_config_loads_through_the_shipped_schema(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """An example an operator copies must load, or the spec is a trap.

    §16's TOML block is the one thing in the document somebody is guaranteed to
    paste: `adapters = []` sat *after* the `[server]` keys, so TOML made it a key
    of that table, and the schema closes every table it does not declare — the
    schema answered that the property does not exist. `[kerberos]` set both
    `keytab_ref` and `keytab`, which the schema forbids as a pair. Both refusals
    are correct behaviour aimed at a config the document itself published.

    Asserted by *loading* it, not by reading lines: the loader is the thing that
    answers the operator's question, and a fixed example can drift again the
    moment either side changes.
    """
    body = _section_text("16. Configuration")
    match = re.search(r"```toml\n(.*?)```", body, re.DOTALL)
    assert match is not None, (
        "§16 no longer carries a ```toml block. The example is what an operator "
        "copies, so its absence is a regression this test must fail on"
    )
    temporary = tmp_path_factory.mktemp("spec-example")
    path = temporary / "example.toml"
    path.write_text(match.group(1), encoding="utf-8")

    # Imported here rather than at module scope: this is the one test in the file
    # that loads the *product*, and the import belongs next to the assertion it
    # serves rather than at the top of a module of document checks.
    from bigdata_mcp.config import load_config
    from bigdata_mcp.posture import Posture as PostureEnum

    config = load_config(path)

    assert config.posture is PostureEnum.READ_ONLY
    assert config.adapters == (), (
        "the example loads but `adapters` is not the empty allowlist §5.2 "
        "describes as the default"
    )
