"""The fixture format: what one recorded exchange is.

`SPEC.md` §5.1 gives the shape, §5.2 the version, and §5.3 the `observed` /
`synthetic` distinction that this repository depends on more than any other part of
the format -- every fixture in `tests/fixtures/synthetic/` is `synthetic`, because
the estate is not reachable from the machine that builds this (§3), and a synthetic
fixture can exercise the loader but must never be read as evidence about the real
endpoints.

This module holds the record and nothing else. Whether a parsed JSON document is
*allowed* to become a `Fixture` lives in `fields`, which is where every rule that
refuses also names the field it refused.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Any

from bigdata_mcp.errors import ConfigError

#: The format's version identifier. Mirrors §14.2's portal pin
#: (`schema = "bigdata-mcp/port@1"`) rather than inventing a second convention: one
#: naming scheme across the repo, and an unknown value is refused by exact string


FIXTURE_SCHEMA_VERSION = "bigdata-mcp/fixture@1"

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({FIXTURE_SCHEMA_VERSION})

#: Every key a fixture document may carry. Anything else is a refusal, not a
#: shrug: a typo in `source_id` that loads silently turns a fixture about HDFS into
#: one that matches nothing, which is the same shape of failure as a wrong answer
#: with no error. The strictness matches §16's config load, for the same reason.
KNOWN_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "source",
    "transport",
    "source_id",
    "operation",
    "request",
    "stdout",
    "stderr",
    "exit_code",
    "captured_at",
    "provenance",
    "comment",
})


class Source(enum.Enum):
    """Whether a fixture was observed on an estate or synthesised.

    Attributes:
        SYNTHETIC: Built here from a documented response shape. Proves the loader
            and the parser agree with the shape; proves nothing about the estate.
        OBSERVED: Captured from a real endpoint by the operator. The only kind
            that can validate a parser.
    """

    SYNTHETIC = "synthetic"
    OBSERVED = "observed"


class Transport(enum.Enum):
    """The §5.1 transport families a fixture can record.

    Attributes:
        SSH_CLI: A command over SSH. `request` is an argv list.
        HTTPS_API: A direct HTTPS request. `exit_code` is the HTTP status.
        WEB_SESSION: A request through a browser-derived session.
        MCP_CLIENT: A relayed MCP `tools/call`.
    """

    SSH_CLI = "ssh_cli"
    HTTPS_API = "https_api"
    WEB_SESSION = "web_session"
    MCP_CLIENT = "mcp_client"


@dataclass(frozen=True, slots=True)
class Fixture:
    """One recorded exchange.

    Attributes:
        schema_version: The format version. Always present; see the module note.
        source: `synthetic` or `observed`.
        transport: Which §5.1 family produced it.
        source_id: The logical backend, e.g. `hdfs` or `yarn_rm`.
        operation: The specific invocation, e.g. `hdfs dfs -count -q -v <p>`.
        request: The request, as structured data: an argv list for `ssh_cli`, or
            `{"method": ..., "path": ..., "params": {...}}` for the HTTP
            transports. Never a shell string.
        stdout: Verbatim stdout, or the response body for an HTTP transport.
        stderr: Verbatim stderr. Always empty for an HTTP transport.
        exit_code: The process exit status, or the HTTP status for an HTTP
            transport.
        captured_at: RFC 3339 timestamp. Absent for synthetic, required for
            observed — a synthetic fixture was never captured anywhere.
        provenance: Free text naming the machine and the command, for observed
            fixtures only. This is what makes an observed fixture auditable months
            later by someone who was not there.
        comment: What this fixture is for. **Required** for a synthetic fixture:
            the spec asks each one to name the claim it encodes, and an unlabelled
            synthetic fixture is a trap for whoever reads it later. Optional for an
            observed one, where `provenance` already says where it came from.
    """

    schema_version: str
    source: Source
    transport: Transport
    source_id: str
    operation: str
    request: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    captured_at: str | None = None
    provenance: str | None = None
    comment: str | None = None

    @property
    def is_observed(self) -> bool:
        """Whether this fixture came from a real endpoint."""
        return self.source is Source.OBSERVED

    @property
    def key(self) -> tuple[str, str]:
        """The `(source_id, operation)` pair this fixture is indexed under."""
        return (self.source_id, self.operation)

    @property
    def argv(self) -> tuple[str, ...]:
        """The request as an argv tuple.

        Returns:
            The argv list, for an `ssh_cli` fixture.

        Raises:
            ConfigError: If the fixture is not an `ssh_cli` one, so a caller
                cannot silently run an HTTP request as a shell command.
        """
        if self.transport is not Transport.SSH_CLI:
            message = (
                f"fixture for {self.source_id!r} is {self.transport.value}, not "
                "ssh_cli; it has no argv"
            )
            raise ConfigError(message)
        return tuple(_argv_of(self.request))

    def to_document(self) -> dict[str, Any]:
        """Render this fixture back to its on-disk form.

        Returns:
            A JSON-ready dict. `captured_at` and `provenance` are omitted for a
            synthetic fixture rather than written as `null`, so a synthetic file
            cannot be mistaken for a half-written observed one.
        """
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source": self.source.value,
            "transport": self.transport.value,
            "source_id": self.source_id,
            "operation": self.operation,
            "request": self.request,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }
        if self.captured_at is not None:
            document["captured_at"] = self.captured_at
        if self.provenance is not None:
            document["provenance"] = self.provenance
        if self.comment is not None:
            document["comment"] = self.comment
        return document

    def dumps(self) -> str:
        """Render this fixture as the JSON text stored on disk.

        Returns:
            Pretty-printed JSON with a trailing newline, so the committed corpus
            diffs cleanly and every file ends with one.
        """
        return json.dumps(self.to_document(), indent=2, sort_keys=True) + "\n"


def _argv_of(request: dict[str, Any]) -> list[str]:
    """Return the argv list from a validated `ssh_cli` request.

    Args:
        request: A validated request object.

    Returns:
        The argv list, or an empty list when absent.
    """
    argv = request.get("argv")
    return [str(item) for item in argv] if isinstance(argv, list) else []


__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "KNOWN_FIELDS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Fixture",
    "Source",
    "Transport",
]
