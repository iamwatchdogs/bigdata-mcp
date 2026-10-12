r"""Capturing a relayed MCP `tools/call` as a fixture.

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

A command that cannot be started, and a server that starts and then will not answer,
are translated by `_raise_typed_failure` into the two typed refusals — `ConfigError`
for what the operator typed, `BackendUnreachable` for what the estate did not
answer — because a raw library exception from a capture run is what `capture.main`
exists to keep off the operator's terminal.

`--command` is split with POSIX `shlex` rules, where an unquoted backslash escapes
the character after it. A Windows path has to be quoted before it reaches here:
`shlex.join` produces that spelling, and an operator who pastes a bare
`D:\repo\python.exe` gets `D:repopython.exe` back and a launch failure that
names no path they recognise.
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
from typing import NoReturn

from mcp import Client
from mcp import MCPError
from mcp import StdioServerParameters
from mcp.types import TextContent

if TYPE_CHECKING:
    import argparse

from bigdata_mcp.errors import BackendUnreachable
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
    r"""Call `tool` over stdio and return what the server answered.

    Args:
        tool: The tool name to call.
        arguments: The call's arguments, already a dict.
        command: The server command as a shell-quoted string.
        source_id: The upstream server this relay belongs to.
        operation: The call, as a label.
        provenance: Where this was captured, or `None` to describe the call itself.
        connect: The client seam; `None` builds the real stdio client.

    Returns:
        The observed fixture.

    A `--command` that is empty once the shell has had it is refused by
    `_prepare`, which is where that `ConfigError` leaves from.
    """
    connect, argv = _prepare(command, connect)
    try:
        text, structured, is_error = asyncio.run(
            _relay_call(connect, argv, tool, arguments)
        )
    except (OSError, MCPError, ExceptionGroup) as exc:
        _raise_typed_failure(exc, shlex.join(argv))
    return _recorded(
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


def _recorded(
    call: _Call,
    text: str,
    structured: str | None,
    *,
    is_error: bool,
) -> Fixture:
    """Build the fixture from what the relay answered.

    Split out of `capture` for the same reason `Session._request`'s hop loop was:
    the function does two things — it asks the server and it records the answer —
    and the recording half is a pure mapping from one shape to another. Naming it
    separately keeps `capture` short enough to read as one pass.

    Args:
        call: What was asked for.
        text: The result's text blocks, joined.
        structured: The rendered `structured_content`, or `None`.
        is_error: Whether the server marked the result an error.

    Returns:
        The observed fixture.
    """
    return _fixture(call, text, structured, is_error=is_error)


def _prepare(command: str, connect: McpConnect | None) -> tuple[McpConnect, list[str]]:
    """Split the command and resolve the client seam it names.

    Args:
        command: The server command as a shell-quoted string.
        connect: The caller's seam, or `None` for the real stdio client.

    Returns:
        The client seam to use and the shell-split argv.

    Raises:
        ConfigError: If `command` is empty once the shell has had it.
    """
    argv = shlex.split(command)
    if not argv:
        message = "--command was empty once the shell has had it"
        raise ConfigError(message)
    return (connect or _stdio_client), argv


async def _relay_call(
    connect: McpConnect,
    argv: Sequence[str],
    tool: str,
    arguments: dict[str, Any],
) -> tuple[str, str | None, bool]:
    r"""Invoke the tool and flatten the result into the three parts a fixture stores.

    Args:
        connect: Builds the client for `argv`.
        argv: The server command, already split.
        tool: The tool name to call.
        arguments: The call's arguments.

    Returns:
        The text content, the structured content as a JSON string or `None`, and
        whether the server flagged the call as an error. `is_error` is carried
        rather than raised: a tool that answers "here is why it failed" is an
        observation to record, not a transport fault.
    """
    async with connect(argv) as client:
        result = await client.call_tool(tool, arguments)
    return (
        _text_of(result.content),
        _structured_of(result.structured_content),
        result.is_error,
    )


def _raise_typed_failure(exc: BaseException, argv_text: str) -> NoReturn:
    """Raise the typed failure a launch or transport fault names.

    A command that could not be started is the operator's own mistake — a
    missing binary, a directory without `+x`, a path that is not a program — so
    it raises the `ConfigError` an empty `--command` raises, and `main` answers
    exit 5 where a raw `FileNotFoundError` used to escape as a traceback. A
    command that *started* and then would not answer raises `BackendUnreachable`,
    because §8.2 row 5 is the class for an endpoint that does not answer.

    `ExceptionGroup` is in the second family because a stdio child that exits
    during the handshake surfaces through anyio's TaskGroup rather than as an
    `MCPError`, and an unconverted library exception from a capture run is
    exactly what `capture.main` exists to keep off the operator's terminal.

    It raises rather than returning, so `capture`'s control flow ends in the
    `except` without the caller reading a value it has to re-raise — and so the
    `from exc` chain keeps the original fault for whoever debugs the capture
    itself.

    Args:
        exc: The fault the SDK raised.
        argv_text: The shell-quoted command, quoted for the message.

    Raises:
        ConfigError: If the command could not be started at all.
        BackendUnreachable: If the command started and then would not answer.
    """
    if isinstance(exc, OSError):
        message = f"{argv_text} could not be started: {exc}"
        raise ConfigError(message) from exc
    raise BackendUnreachable(
        endpoint=f"mcp_client {argv_text}",
        detail=_transport_detail(exc),
    ) from exc


def _transport_detail(exc: BaseException) -> str:
    """Name the fault behind a transport failure, including the members of a group.

    An `ExceptionGroup` from anyio's TaskGroup carries the real cause one level
    down -- `BrokenResourceError`, `ClosedResourceError`, `MCPError` -- and the
    group's own `__str__` reports "unhandled errors in a TaskGroup", which tells
    an operator nothing they can act on. Rendering each member's type is what
    makes the message name the actual failure.

    Args:
        exc: The launch or transport fault.

    Returns:
        The fault class names, joined when the fault is a group.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(
            f"{type(member).__name__}: {member}" for member in exc.exceptions
        )
    return f"{type(exc).__name__}: {exc}"


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
