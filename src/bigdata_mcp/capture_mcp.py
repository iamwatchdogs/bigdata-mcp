"""Capturing a relayed MCP `tools/call` as a fixture.

§7.3 makes `mcp_client` a first-class transport, so the corpus has to be able to
hold one, and the only honest way to fill it is to actually make the call. This
lives apart from `capture.py` because it is the one capture path with no HTTP in
it: a relay has no URL, no status line, and no session, and folding it into the
module that is mostly about sessions would make both harder to read.

`connect` is a seam, like `Session`, `FakeExecutor`, and the observer's injected
readers: the default launches the command over stdio, and a test connects an
`MCPServer` in-process. Proving a two-line call by spawning an interpreter is a
test suite that gets skipped for being slow.

Three decisions about how the answer is recorded, each with a test that fails if it
is reversed:

An `isError` result becomes `exit_code = 1`. A result carries a protocol flag
rather than a process status, and collapsing the two would make the distinction
unrecoverable later -- filing a failing call as a passing one is the same
fabricated success the observer refuses to invent with an unreadable load average,
and far harder to notice.

`structured_content` is kept, in `stderr`, behind a status prefix. Overloading a
diagnostics field is ugly and is still right: §6.2's structured-output rule means a
tool returning a Pydantic model puts its whole answer there, and dropping it records
the tool as having returned nothing. The prefix is JSON so a reader can tell the
status line from output the tool produced.

Non-text content blocks are dropped with a count rather than silently.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from mcp import Client
from mcp import StdioServerParameters
from mcp.types import TextContent

if TYPE_CHECKING:
    import argparse

from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport
from bigdata_mcp.timestamps import now

#: Builds an MCP client for a server argv. A seam rather than a direct `Client`
#: construction so a test can connect an in-process server: the stdio path spawns a
#: subprocess, and a test suite that needs a subprocess to prove a two-line call is
#: a test suite that will be skipped in CI for being slow.
McpConnect = Callable[[Sequence[str]], Client]

#: The subcommand that captures a relayed call.
COMMAND = Transport.MCP_CLIENT.value

__all__ = ["COMMAND", "McpConnect", "add_command", "capture"]


def _add_output_arguments(command: argparse.ArgumentParser) -> None:
    """Register the output arguments every capture subcommand shares.

    Args:
        command: The subparser to extend.
    """
    command.add_argument(
        "--out", required=True, type=Path, help="fixture file to write"
    )
    command.add_argument(
        "--force",
        action="store_true",
        help="replace an existing fixture; without it an existing file is refused",
    )


def add_command(command: argparse.ArgumentParser) -> None:
    """Register the arguments a relayed `tools/call` capture needs.

    `--command` is a whole argv *string* rather than repeated flags because an MCP
    server is frequently launched through an interpreter with flags before the
    module (`python -m schema_registry`, `npx -y @upstream/mcp-server`), and a
    flag-per-argument interface cannot express that without asking the operator to
    quote a list the shell will also try to expand.

    Args:
        command: The `mcp_client` subparser to populate.
    """
    command.add_argument(
        "--command",
        required=True,
        help="command that starts the server, as a shell-quoted argv string",
    )
    command.add_argument("--tool", required=True, help="tool name to call")
    command.add_argument("--arguments", default="{}", help="JSON object of arguments")
    command.add_argument("--source-id", required=True, help="e.g. schema-registry")
    command.add_argument("--operation", required=True, help="the call, as a label")
    command.add_argument(
        "--provenance",
        default=None,
        help="where this was captured; defaults to the command and tool name",
    )
    _add_output_arguments(command)


def capture(
    tool: str,
    arguments: dict[str, Any],
    command: str,
    *,
    source_id: str,
    operation: str,
    provenance: str | None = None,
    connect: McpConnect | None = None,
) -> Fixture:
    """Call `tool` over stdio and return what the server answered.

    Args:
        tool: The tool name to call.
        arguments: The call's arguments, already a dict.
        command: The server command as a shell-quoted string.
        source_id: The upstream server this relay belongs to.
        operation: The call, as a label.
        provenance: Where this was captured, or `None` to describe the call itself.
        connect: Builds the client, or `None` for the real stdio client. Injected so
            a test can connect an in-process server rather than spawning one.

    Returns:
        The observed fixture.

    Raises:
        ConfigError: If `command` is empty once the shell has had it.
    """
    if connect is None:
        connect = _stdio_client
    argv = shlex.split(command)
    if not argv:
        message = "--command was empty once the shell had it"
        raise ConfigError(message)

    async def call() -> tuple[str, str | None, bool]:
        async with connect(argv) as client:
            result = await client.call_tool(tool, arguments)
        return (
            _text_of(result.content),
            _structured_of(result.structured_content),
            result.is_error,
        )

    text, structured, is_error = asyncio.run(call())
    return _fixture(
        _Call(
            tool=tool,
            arguments=arguments,
            argv=argv,
            source_id=source_id,
            operation=operation,
            provenance=provenance,
        ),
        text,
        structured,
        is_error=is_error,
    )


@dataclass(frozen=True, slots=True)
class _Call:
    """What was asked for, as distinct from what came back.

    A dataclass rather than seven parameters because the split is real: the request
    is what the operator typed, the result is what the server said, and conflating
    them is how a capture ends up describing a request it never made.
    """

    tool: str
    arguments: dict[str, Any]
    argv: Sequence[str]
    source_id: str
    operation: str
    provenance: str | None


def _fixture(
    call: _Call, text: str, structured: str | None, *, is_error: bool
) -> Fixture:
    """Build the fixture from a relayed call and its result.

    Args:
        call: What was asked for.
        text: The result's text blocks, joined.
        structured: The rendered `structured_content`, or `None`.
        is_error: Whether the server marked the result an error.

    Returns:
        The observed fixture.
    """
    return Fixture(
        schema_version=FIXTURE_SCHEMA_VERSION,
        source=Source.OBSERVED,
        transport=Transport.MCP_CLIENT,
        source_id=call.source_id,
        operation=call.operation,
        request={
            "tool": call.tool,
            "arguments": call.arguments,
            "argv": list(call.argv),
        },
        stdout=text,
        stderr=_with_status(structured or "", "isError" if is_error else "ok"),
        exit_code=1 if is_error else 0,
        captured_at=now(),
        provenance=call.provenance or f"mcp_client {shlex.join(call.argv)} {call.tool}",
    )


def _stdio_client(argv: Sequence[str]) -> Client:
    """Return a client that launches `argv` as a stdio MCP server.

    Args:
        argv: The server command, already split.

    Returns:
        An `mcp.Client` configured for stdio. The subprocess is not started until
        the context manager is entered.
    """
    return Client(StdioServerParameters(command=argv[0], args=list(argv[1:])))


def _text_of(blocks: Sequence[Any]) -> str:
    """Join a result's content blocks into the text a caller would see.

    Only `TextContent` is read. An image or a resource block in a relayed result is
    not something a text corpus can hold, so it is dropped rather than rendered --
    and dropped silently, because the count that follows records that it happened.

    Args:
        blocks: The `CallToolResult.content` list.

    Returns:
        The text of every `TextContent` block, separated by newlines.
    """
    texts: list[str] = [
        block.text for block in blocks if isinstance(block, TextContent)
    ]
    other = len(blocks) - len(texts)
    if other:
        texts.append(f"[{other} non-text content block(s) omitted]")
    return "\n".join(texts)


def _structured_of(structured: object) -> str | None:
    """Render a result's `structured_content` for the fixture.

    Args:
        structured: The `CallToolResult.structured_content`, or `None`.

    Returns:
        Pretty JSON, or `None` when the tool returned no structured content.
    """
    if structured is None:
        return None
    return json.dumps(structured, indent=2, sort_keys=True)


def _with_status(rendered: str, status: str) -> str:
    """Prefix the fixture's `stderr` with the protocol status.

    The prefix is a JSON object rather than prose so that a reader can tell a status
    line from output the tool itself produced, which is the whole reason this is not
    simply `stdout`.

    Args:
        rendered: The rendered `structured_content`, possibly empty.
        status: `"ok"` or `"isError"`.

    Returns:
        The `stderr` value to store.
    """
    prefix = json.dumps({"status": status}, sort_keys=True)
    return f"{prefix}\n{rendered}" if rendered else prefix
