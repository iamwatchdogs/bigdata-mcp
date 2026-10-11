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
"""

from __future__ import annotations

from repo_support import META_TOOL_LITERALS
from repo_support import NEW_CAPABILITY_LITERALS
from repo_support import _assert_literals_present


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
