"""The v2 commitments that are only commitments because the spec says so.

The custom-port family and MCP-server aggregation are the two capabilities v1
never had. Each is asserted here against the words in ``SPEC.md`` rather than
against the code, because the code is downstream: a capability that disappears
from the document is the regression, and it is silent by construction — every
gate that reads source stays green.

The traceability and coverage halves of the original suite moved to
`test_spec_contracts.py` and `test_coverage_contracts.py` when this module
crossed Codacy's 500-line limit, and the readers they shared moved to
`repo_support.py`.

It also holds the repository's file-layout contracts — the rules about how the
tree is written that no single module has a reason to assert about itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repo_support import META_TOOL_LITERALS
from repo_support import NEW_CAPABILITY_LITERALS
from repo_support import REPO_ROOT
from repo_support import _assert_literals_present

if TYPE_CHECKING:
    from pathlib import Path


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


def test_no_doc_comment_is_stranded_above_an_import() -> None:
    """Assert every ``#:`` in ``tests/`` documents a definition, not an import.

    ``#:`` is the Sphinx attribute-docstring marker: it attaches to the statement
    *below* it. Placed above an ``import`` it documents the import, and the name
    it was written for — further down the same import block — is left uncommented.
    The file still parses and still passes, so the mistake is invisible to every
    gate except this one. It happened once, where a copy of the ``_LITERAL``
    comment was left above ``from tests.test_config import VALID_TOML``.
    """
    stranded = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith("#:"):
                stranded.append(_stranded_marker(path, lines, index))
    hits = [hit for hit in stranded if hit]
    assert hits == [], "marker not attached to a definition: " + ", ".join(hits)


def _stranded_marker(path: Path, lines: list[str], index: int) -> str:
    """Return ``path:line`` when the ``#:`` marker at *index* sits over an import.

    Args:
        path: The file the marker lives in.
        lines: The file's lines, already split.
        index: Position of the ``#:`` line.

    Returns:
        A ``relative/path:lineno`` locator, or an empty string when the marker
        is followed by the definition it documents.
    """
    location = f"{path.relative_to(REPO_ROOT)}:{index + 1}"
    for following in lines[index + 1 :]:
        stripped = following.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        if stripped.startswith(("import ", "from ")):
            return location
        return ""
    return ""


#: The definitions in ``repo_support.py`` that have been pasted twice by
#: accident. Each name is the *reader* for one half of a two-configuration
#: contract, so a duplicate does not fail anything — Python keeps the second
#: definition and every import keeps working.
_SINGLE_COPY_DEFINITIONS = (
    "CODERABBIT_DOCSTRINGS_BLOCK =",
    "def _docstrings_threshold_percent(",
    "def _coderabbit_text(",
    "def _coderabbit_docstrings_threshold(",
)


def test_repo_support_defines_each_reader_once() -> None:
    """Assert ``repo_support.py`` holds one copy of each shared reader.

    The readers that pair ``pyproject.toml`` with ``.coderabbit.yaml`` were
    pasted twice into this module, and the first copy was dead in the plainest
    way a Python duplicate can be: the second definition silently shadowed it,
    so both copies stayed byte-identical, both compiled, and the suite stayed
    green. Nothing reads a name from the middle of a file, so the only thing
    that could notice was a count.

    The risk is not the waste but the edit. Changing the refusal message, the
    regex, or the assertion in one copy and not the other leaves two readers
    that disagree about what the configuration says, and which one wins is
    decided by position in the file rather than by anyone's intent — a
    difference no gate would report, because both copies satisfy their callers.

    A third reader of the same name in a *different* module is allowed: the
    consumer in ``test_tooling_contracts.py`` imports these by name, and a
    module-local copy elsewhere is a separate decision. This asserts only that
    the shared module is not carrying two.
    """
    source = (REPO_ROOT / "scripts" / "tests" / "repo_support.py").read_text(
        encoding="utf-8"
    )
    counts = {name: source.count(name) for name in _SINGLE_COPY_DEFINITIONS}
    duplicated = {name: count for name, count in counts.items() if count != 1}
    assert duplicated == {}, (
        "repo_support.py defines these more than once, so the first is dead and "
        f"the second silently wins: {duplicated}. Delete every copy but the last; "
        "the readers pair two configurations that must agree, and two copies can "
        "drift into disagreeing without any gate noticing"
    )
