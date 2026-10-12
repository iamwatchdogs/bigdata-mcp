"""Document builders for fixture tests.

`observed()` and `synthetic()` are the base documents every fixture test starts
from, and the pair carries the rule worth stating once: an `observed` document
must have `captured_at` and `provenance`, a `synthetic` one must have neither and
must have a `comment` instead. Encoding that in one builder is what lets a test
override a single field and be confident it is exercising that field.

They live in `support` rather than in either test module because the loader tests
and the capture tests both need them, and two copies would be two opinions about
what a valid document is.
"""

from __future__ import annotations

from typing import Any

from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION

SYNTHETIC_COMMENT = "SPEC.md §1: this fixture exists to prove the loader works."


def observed(**overrides: Any) -> dict[str, Any]:
    """Build a minimal *observed* fixture document.

    Args:
        **overrides: Fields to replace or add.

    Returns:
        A document that validates as-is unless `overrides` breaks it.
    """
    document: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source": "observed",
        "transport": "https_api",
        "source_id": "yarn_rm",
        "operation": "GET /ws/v1/cluster/info",
        "request": {"method": "GET", "path": "/ws/v1/cluster/info", "params": {}},
        "stdout": '{"clusterInfo": {}}',
        "stderr": "",
        "exit_code": 200,
        "captured_at": "2026-10-06T21:04:05+00:00",
        "provenance": "laptop -> rm1.corp",
    }
    document.update(overrides)
    return document


def synthetic(**overrides: Any) -> dict[str, Any]:
    """Build a minimal *synthetic* fixture document.

    Args:
        **overrides: Fields to replace or add.

    Returns:
        A document that validates as-is unless `overrides` breaks it.
    """
    document: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source": "synthetic",
        "transport": "ssh_cli",
        "source_id": "hdfs",
        "operation": "hdfs dfs -count -q -v /warehouse/",
        "request": {"argv": ["hdfs", "dfs", "-count", "-q", "-v", "/warehouse/"]},
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "comment": SYNTHETIC_COMMENT,
    }
    document.update(overrides)
    return document
