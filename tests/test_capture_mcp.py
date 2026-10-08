"""Tests for capturing a relayed MCP `tools/call`.

Split from `test_capture_cli.py` because the rest of that module proves the HTTP
path, which speaks through `session.py`, and a relay speaks through none of it.
Keeping the two apart means "the capture tests fail" says which transport broke.

The client is injected, so the server here is in-process: what is under test is the
call and the fixture it produces, and proving those by spawning an interpreter is a
test suite that gets skipped for being slow.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript), each
applied and observed failing before reverting:

* M1 route `mcp_client` to the HTTP capture -> the four `test_an_mcp_capture_*`
  tests and `test_structured_content_*`
* M2 record an `isError` call with exit code 0 ->
  `test_a_tool_that_reports_an_error_is_captured_as_a_failure`
* M3 drop `structured_content` ->
  `test_structured_content_is_kept_because_it_may_be_the_whole_answer`
* M4 stop refusing a `path` on a relayed call ->
  `test_a_relayed_call_that_also_carries_a_path_is_refused` (in `test_fixtures.py`)
* M5 stop requiring a tool name ->
  `test_a_relayed_call_without_a_tool_is_refused` (in `test_fixtures.py`)
* M6 stop dispatching from the console entry point ->
  `test_capture_fixtures_reaches_the_capture_cli` (in `test_main.py`)
* M7 return 0 for an unknown subcommand ->
  `test_the_entry_point_propagates_the_subcommand_exit_code` (in `test_main.py`)
* M8 put the subparser `dest` back to `command`, colliding with `--command` ->
  the four `test_an_mcp_capture_*` tests
* M9 drop the count from the non-text summary line ->
  `test_a_non_text_content_block_is_recorded_as_omitted_not_dropped`
* M10 ignore non-text blocks silently rather than counting them -> the same test
* M11 hand the whole argv to `command` and nothing to `args` ->
  `test_the_real_stdio_client_splits_program_from_its_arguments`
* M12 remove the `connect is None` fallback -> the same module, in
  `test_the_capture_falls_back_to_the_real_stdio_client_when_none_is_injected`

M9 to M12 exist because of the per-file coverage floor, and each one was a branch
no test had reached. M11 and M12 are the pair worth noting: they are the production
path, and the whole module injected around them, so a real capture failing to launch
a server was invisible to a suite that was otherwise thorough about this file.

M6 stayed green on its first attempt, which is why that test exists at all. M8 also
stayed green, because the mutation renamed the option's `dest` too and so removed
the collision rather than reproducing it. Both are recorded because a mutation that
stays green for the wrong reason is worth as much as one that goes red for the
right one.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING
from typing import Any
from typing import Self

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import ImageContent
from mcp.types import TextContent

from bigdata_mcp import capture
from bigdata_mcp import capture_mcp
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from tests.support.fixture_docs import observed

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence
    from pathlib import Path

    from mcp.client.stdio import StdioServerParameters


class _FakeClient:
    """An `McpConnect` result that answers with one text block."""

    async def __aenter__(self) -> Self:
        """Enter the `async with` the capture wraps around `call_tool`.

        Returns:
            This client. The real `McpConnect` result is itself a context manager
            that owns the stdio connection, so the fake has to match that shape
            rather than being a bare object the capture would need a second path
            for.
        """
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the context the capture opened.

        A no-op: there is no stdio connection to tear down in-process, so this
        exists only to match the real `McpConnect` result's shape and let the
        capture's `async with` work against either. Ruff's `RET501` is what keeps
        the body empty — an explicit `return None` here is a no-op statement, and
        adding one to make room for this docstring is the trade ruff is pointing
        at.

        Args:
            *_exc: The in-flight exception's type, value and traceback. Swallowed
                deliberately: the fake is standing in for a connection, not for
                the capture's error handling.
        """

    async def call_tool(self, _tool: str, _arguments: dict[str, Any]) -> Any:
        """Answer any relayed call with one text block and no error.

        Underscore-prefixed because the value is irrelevant: the fixture this
        produces is the same whatever the operator asked for, and pinning the
        arguments here would make this look like a test of the request.

        Returns:
            A stand-in for `CallToolResult` with `structured_content` `None` and
            `is_error` `False`, built by `type()` so it needs no SDK import that
            the SDK's own version might rename.
        """
        return type(
            "R",
            (),
            {
                "content": [TextContent(type="text", text="ok")],
                "structured_content": None,
                "is_error": False,
            },
        )()


#: Whatever `--command` says, because the injected client never spawns it. Keeping a
#: plausible-looking argv means a test that forgets to inject fails loudly instead
#: of quietly skipping the protocol entirely.
UNUSED_ARGV = "unused-when-injected"


def _registry(*, fails: bool = False) -> MCPServer:
    """Return an in-process MCP server carrying one `schema_get` tool.

    In-process rather than a subprocess on purpose. What is under test is the call
    and the fixture it produces, and a test that proves those by spawning an
    interpreter is a test that gets skipped for being slow.

    Args:
        fails: Make the tool raise, which the SDK turns into an `isError` result.

    Returns:
        A server whose `schema_get` echoes its argument as JSON.
    """
    server = MCPServer(name="registry")

    def schema_get(name: str) -> str:
        """Echo the argument back as JSON, so the fixture carries a known answer.

        Args:
            name: Whatever `{"name": ...}` the argv supplied.

        Returns:
            `{"schema": "<name>"}`, so the assertion downstream can name the exact
            string rather than matching for a substring of it.

        Raises:
            RuntimeError: When the registry was built with `fails=True`. The SDK
                turns this into an `isError` result rather than propagating it,
                which is what the failure-capture test needs a tool to produce.
        """
        if fails:
            message = "no such schema"
            raise RuntimeError(message)
        return json.dumps({"schema": name})

    server.tool(name="schema_get")(schema_get)
    return server


def _mixed_content_registry() -> MCPServer:
    """Return a server whose tool answers with text *and* an image block.

    Added for the per-file coverage floor: `_text_of`'s non-text branch had never
    been reached, because a tool returning a string never produces one.

    Returns:
        A server whose `mixed` tool returns two content blocks of different types.
    """
    server = MCPServer(name="registry")

    def mixed(name: str) -> list[Any]:
        """Answer with two blocks of different types from one tool.

        Args:
            name: Ignored, because the point is the block *types* and a tool that
                ignored its argument would still be a legitimate tool.

        Returns:
            A text block and a real image block, so the capture has to join the
            first and count the second.
        """
        del name  # the tool takes an argument because every tool in this module does
        # 8 bytes of a real PNG signature so the SDK accepts it as image data
        # rather than rejecting the block and turning this into a test of nothing.
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 8).decode()
        return [
            TextContent(type="text", text="hello"),
            ImageContent(type="image", data=png, mime_type="image/png"),
        ]

    server.tool(name="mixed")(mixed)
    return server


def _connector(server: MCPServer) -> Callable[[Sequence[str]], Client]:
    """Return a `connect` seam that talks to `server` in-process.

    Args:
        server: The server to connect to.

    Returns:
        A callable matching `McpConnect`, ignoring the argv it is handed.
    """

    def connect(_argv: Sequence[str]) -> Client:
        """Build a client bound to `server`, discarding the argv it is handed.

        Args:
            _argv: The shell-split server command. Discarded because the server is
                already in this process — but the parameter stays, because its
                absence would make this satisfy `McpConnect` for the wrong reason.

        Returns:
            A client over the in-process server.
        """
        return Client(server)

    return connect


def _mcp_argv(
    out: Path, *, arguments: str = '{"name": "users"}', tool: str = "schema_get"
) -> list[str]:
    """Return a complete `mcp_client` argv.

    Args:
        out: Where to write the fixture.
        arguments: The `--arguments` JSON.
        tool: The tool to call.

    Returns:
        An argv list suitable for `capture.main`.
    """
    return [
        "mcp_client",
        "--command",
        UNUSED_ARGV,
        "--tool",
        tool,
        "--arguments",
        arguments,
        "--source-id",
        "schema-registry",
        "--operation",
        "schema_get users",
        "--out",
        str(out),
    ]


def test_an_mcp_capture_records_the_answer_the_tool_gave(tmp_path: Path) -> None:
    """The tool's answer lands in `stdout`, and nothing else does.

    §6.4's stdout-hygiene rule is what makes this field trustworthy: stdout is the
    JSON-RPC channel and anything else written there corrupts a live session, so
    the status prefix and the serialised `structured_content` both belong in
    `stderr`. Putting the tool's text anywhere else would leave a corpus that
    cannot be fed back into a test without stripping it first.
    """
    out = tmp_path / "schema.json"

    code = capture.main(_mcp_argv(out), connect=_connector(_registry()))

    assert code == 0, f"capture exited {code}"
    fixture = load_fixture(out)
    assert fixture.transport is Transport.MCP_CLIENT
    assert fixture.source_id == "schema-registry"
    assert fixture.operation == "schema_get users"
    assert fixture.stdout == '{"schema": "users"}'
    assert fixture.exit_code == 0


def test_an_mcp_capture_records_the_call_it_made(tmp_path: Path) -> None:
    """The request is enough to replay the call without the shell history."""
    out = tmp_path / "schema.json"

    capture.main(_mcp_argv(out), connect=_connector(_registry()))

    assert load_fixture(out).request == {
        "tool": "schema_get",
        "arguments": {"name": "users"},
        "argv": ["unused-when-injected"],
    }


def test_a_tool_that_reports_an_error_is_captured_as_a_failure(tmp_path: Path) -> None:
    """An `isError` result must not be recorded as exit_code 0.

    A corpus that filed a failing call as a passing one is the same
    fabricated-success mistake the observer refuses to make with an unreadable load
    average, and it would be much harder to notice here.
    """
    out = tmp_path / "schema.json"

    capture.main(_mcp_argv(out), connect=_connector(_registry(fails=True)))

    fixture = load_fixture(out)
    assert fixture.exit_code == 1, f"stderr was {fixture.stderr!r}"
    assert '"status": "isError"' in fixture.stderr


def test_structured_content_is_kept_because_it_may_be_the_whole_answer(
    tmp_path: Path,
) -> None:
    """§6.2's structured output is preserved rather than dropped."""
    out = tmp_path / "schema.json"

    capture.main(_mcp_argv(out), connect=_connector(_registry()))

    assert '"result"' in load_fixture(out).stderr, (
        "structured_content was dropped from the fixture. A tool returning a "
        "Pydantic model puts its whole answer there, so dropping it records the "
        "tool as having returned nothing at all"
    )


def test_an_mcp_capture_never_overwrites_without_force(tmp_path: Path) -> None:
    """A relay that answers differently the second time cannot silently replace.

    An MCP server behind a deploy can change its schema between two captures, and a
    corpus that keeps only the newest answer has lost the record that it changed.
    The byte-for-byte comparison is the point: returning 4 while still writing
    would satisfy the exit-code assertion alone.
    """
    out = tmp_path / "schema.json"
    capture.main(_mcp_argv(out), connect=_connector(_registry()))
    before = out.read_text(encoding="utf-8")

    code = capture.main(_mcp_argv(out), connect=_connector(_registry()))

    assert code == 4, f"expected EXIT_EXISTS (4), got {code}"
    assert out.read_text(encoding="utf-8") == before


def test_mcp_arguments_that_are_not_a_json_object_are_refused(tmp_path: Path) -> None:
    """`--arguments` is parsed before the call, not handed to the server to reject.

    A JSON array is well-formed text, so an operator's typo gets as far as the
    fixture unless something refuses it here. The absence of the output file is the
    second half: a refusal that still wrote a fixture would leave a half-answer on
    disk that `load_fixture` would happily accept.
    """
    out = tmp_path / "schema.json"

    code = capture.main(
        _mcp_argv(out, arguments='["not", "an", "object"]'),
        connect=_connector(_registry()),
    )

    assert code == 5, f"expected EXIT_REFUSED (5), got {code}"
    assert not out.exists()


def test_an_empty_mcp_command_is_refused_rather_than_spawning_nothing(
    tmp_path: Path,
) -> None:
    """A whitespace-only `--command` is refused before anything is spawned.

    `shlex.split` turns it into an empty list, so an unchecked argv would reach
    `StdioServerParameters(command=argv[0])` and raise `IndexError` — a traceback
    from a mistyped flag, where the only useful answer is "that command was
    empty". Only the exit code is asserted, which is all there is to assert: the
    refusal is raised inside `_dispatch`, so there is no later point at which a
    file could be written.
    """
    argv = _mcp_argv(tmp_path / "schema.json")
    argv[argv.index("--command") + 1] = "   "

    code = capture.main(argv, connect=_connector(_registry()))

    assert code == 5, f"expected EXIT_REFUSED (5), got {code}"


def test_a_quoted_command_is_split_like_a_shell_would(tmp_path: Path) -> None:
    """`--command` is one shell-quoted string, so quoting is honoured."""
    out = tmp_path / "schema.json"
    argv = _mcp_argv(out)
    argv[argv.index("--command") + 1] = "python -m registry"

    capture.main(argv, connect=_connector(_registry()))

    assert load_fixture(out).request["argv"] == ["python", "-m", "registry"]


def test_every_transport_has_a_capture_path() -> None:
    """Every `Transport` is reachable from the CLI, or deliberately is not.

    `_dispatch` branches on the subcommand string, so a transport added to the enum
    without a branch would reach `_capture` and read `args.url` off a namespace that
    has none. This is the assertion that keeps the enum and the dispatcher from
    drifting apart, and it names `ssh_cli` as the one deliberate omission rather
    than leaving a reader to infer it.
    """
    expected = {t.value for t in Transport if t is not Transport.SSH_CLI}

    assert expected == capture.CAPTURE_COMMANDS, (
        f"CAPTURE_COMMANDS is {sorted(capture.CAPTURE_COMMANDS)!r}, expected "
        f"{sorted(expected)!r}. ssh_cli is excluded because it needs an SSH "
        "session this host may not have; everything else must be capturable"
    )
    for command in capture.CAPTURE_COMMANDS:
        with pytest.raises(SystemExit) as parsed:
            capture.build_parser().parse_args([command, "--help"])
        assert parsed.value.code == 0, (
            f"{command!r} is in CAPTURE_COMMANDS but has no subcommand registered"
        )


# --------------------------------------------------------------------------
# A relayed call: `mcp_client` has no URL, and the loader says so
# --------------------------------------------------------------------------


def _relayed(**request: Any) -> dict[str, Any]:
    """Return an `observed` document describing a relayed `tools/call`.

    Args:
        **request: Fields merged into the `request` object.

    Returns:
        A document that validates unless `request` breaks it.
    """
    return observed(
        transport="mcp_client",
        source_id="schema-registry",
        operation="schema_get users",
        request={"tool": "schema_get", "arguments": {"name": "users"}, **request},
    )


def test_a_relayed_call_is_recorded_by_tool_and_arguments() -> None:
    """The `request` object is what makes a relayed fixture replayable.

    A relay has no URL and no method, so the tool name and its arguments are the
    entire record of what was asked. Asserting the *parsed* fixture rather than the
    document is what proves the loader carried them through instead of the builder
    having written them in.
    """
    fixture = parse_fixture(_relayed())

    assert fixture.transport is Transport.MCP_CLIENT
    assert fixture.request["tool"] == "schema_get"


def test_a_relayed_call_without_a_tool_is_refused() -> None:
    """No tool name means nothing identifies the call, so it cannot be recorded.

    Unlike the HTTP shape, `request.tool` has no fallback: the URL that identifies
    an HTTPS call is meaningless on a relay, which is why `_check_mcp_request`
    requires the tool explicitly rather than defaulting it. A fixture missing it
    would replay as "some tool was called" — an answer nobody can act on.
    """
    with pytest.raises(ConfigError, match=r"request\.tool"):
        parse_fixture(observed(transport="mcp_client", request={"arguments": {}}))


def test_a_relayed_call_with_non_object_arguments_is_refused() -> None:
    """`arguments` may be absent, but it may not be an array or a scalar.

    A `tools/call` carries its arguments as a name-to-value object, so an array
    describes a call the relay could not have made. The loader cannot tell "this
    tool takes no arguments" from "the caller passed the wrong shape" — both arrive
    with `arguments` missing or present — which is why the refusal names the field
    by path instead of reporting a generic parse error.
    """
    with pytest.raises(ConfigError, match=r"request\.arguments"):
        parse_fixture(_relayed(arguments=["not", "an", "object"]))


def test_a_relayed_call_may_omit_argv_the_operator_never_had() -> None:
    """`argv` is optional: a relay may be reached over a transport we cannot spawn."""
    document = _relayed()

    assert parse_fixture(document).transport is Transport.MCP_CLIENT


def test_a_relayed_call_that_also_carries_a_path_is_refused() -> None:
    """A URL field on a relayed call describes a request that never happened."""
    with pytest.raises(ConfigError, match=r"request\.path"):
        parse_fixture(_relayed(path="/schema/users"))


# --------------------------------------------------------------------------
# Two paths only the real CLI reaches
# --------------------------------------------------------------------------


def test_a_non_text_content_block_is_recorded_as_omitted_not_dropped(
    tmp_path: Path,
) -> None:
    """A tool that answers with an image must not produce an empty stdout.

    `_text_of` joins the text blocks and counts the rest. The count matters: a
    capture that silently dropped a non-text block would record a tool that
    returned *nothing*, which replays as a tool that returned nothing — a
    fabricated answer, the one outcome the observer's rules exist to prevent.

    The count is asserted rather than just the text because a summary line that
    omits the count ("[non-text content omitted]") still reads as if something
    were there, and would be indistinguishable from a server that sent prose.
    """
    out = tmp_path / "mixed.json"
    server = _mixed_content_registry()

    code = capture.main(_mcp_argv(out, tool="mixed"), connect=_connector(server))

    assert code == 0, f"capture exited {code}"
    stdout = load_fixture(out).stdout
    assert "hello" in stdout, f"the text block was lost: {stdout!r}"
    assert "1 non-text content block(s) omitted" in stdout, (
        f"the non-text block was neither kept nor counted: {stdout!r}"
    )


def test_the_real_stdio_client_splits_program_from_its_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_stdio_client` hands `argv[0]` to `command` and the rest to `args`.

    The production default, unexercised because every other test here injects
    `connect`. Getting this wrong is silent and total: pass the whole argv as
    `command` and no real capture can launch a server, and drop `argv[0]` as the
    program and the server is an empty command. Neither raises here, and no test
    that injects a connector would notice.

    `Client` is substituted so the parameters are readable — the SDK exposes no
    getter for them, and reading a private attribute would pin this test to an
    implementation detail of a dependency instead of to this module's behaviour.
    """
    seen: list[StdioServerParameters] = []

    def record(params: StdioServerParameters) -> None:
        """Stand in for `Client`, keeping the parameters the real one would get.

        Recording rather than inspecting inside a mock is what lets the assertions
        below read the SDK's own field names instead of a call signature.

        Args:
            params: What `_stdio_client` built.
        """
        seen.append(params)

    monkeypatch.setattr(capture_mcp, "Client", record)

    # ruff: ignore[private-member-access] - the production default is the rule under test
    capture_mcp._stdio_client(["python", "-m", "registry"])

    assert len(seen) == 1, f"expected one client, built {len(seen)}"
    params = seen[0]
    assert params.command == "python", f"program was {params.command!r}"
    assert params.args == ["-m", "registry"], f"arguments were {params.args!r}"


def test_the_capture_falls_back_to_the_real_stdio_client_when_none_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`connect=None` must reach `_stdio_client` rather than raise on `None`.

    The default branch, which no test reached until the per-file coverage floor:
    every caller of `capture.capture` in this suite passes a connector, so a
    change that dropped the fallback would leave the suite green and make every
    real capture fail with `TypeError: 'NoneType' object is not callable`.

    The real client is substituted rather than spawned, so the assertion is about
    the branch being taken and not about stdio transport.
    """
    seen: list[Sequence[str]] = []
    monkeypatch.setattr(
        capture_mcp, "_stdio_client", lambda argv: seen.append(argv) or _FakeClient()
    )

    capture_mcp.capture(
        "schema_get",
        {"name": "users"},
        "python -m registry --flag",
        source_id="schema-registry",
        operation="schema_get users",
    )

    assert seen == [["python", "-m", "registry", "--flag"]], (
        f"the fallback received {seen!r}; it must be handed the shell-split argv "
        "and must be the seam used when no connector is injected"
    )
