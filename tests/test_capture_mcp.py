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

M6 stayed green on its first attempt, which is why that test exists at all. M8 also
stayed green, because the mutation renamed the option's `dest` too and so removed
the collision rather than reproducing it. Both are recorded because a mutation that
stays green for the wrong reason is worth as much as one that goes red for the
right one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from bigdata_mcp import capture
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from tests.support.fixture_docs import observed

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence
    from pathlib import Path


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
        if fails:
            message = "no such schema"
            raise RuntimeError(message)
        return json.dumps({"schema": name})

    server.tool(name="schema_get")(schema_get)
    return server


def _connector(server: MCPServer) -> Callable[[Sequence[str]], Client]:
    """Return a `connect` seam that talks to `server` in-process.

    Args:
        server: The server to connect to.

    Returns:
        A callable matching `McpConnect`, ignoring the argv it is handed.
    """

    def connect(_argv: Sequence[str]) -> Client:
        return Client(server)

    return connect


def _mcp_argv(out: Path, *, arguments: str = '{"name": "users"}') -> list[str]:
    """Return a complete `mcp_client` argv.

    Args:
        out: Where to write the fixture.
        arguments: The `--arguments` JSON.

    Returns:
        An argv list suitable for `capture.main`.
    """
    return [
        "mcp_client",
        "--command",
        UNUSED_ARGV,
        "--tool",
        "schema_get",
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
    out = tmp_path / "schema.json"
    capture.main(_mcp_argv(out), connect=_connector(_registry()))
    before = out.read_text(encoding="utf-8")

    code = capture.main(_mcp_argv(out), connect=_connector(_registry()))

    assert code == 4, f"expected EXIT_EXISTS (4), got {code}"
    assert out.read_text(encoding="utf-8") == before


def test_mcp_arguments_that_are_not_a_json_object_are_refused(tmp_path: Path) -> None:
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
    fixture = parse_fixture(_relayed())

    assert fixture.transport is Transport.MCP_CLIENT
    assert fixture.request["tool"] == "schema_get"


def test_a_relayed_call_without_a_tool_is_refused() -> None:
    with pytest.raises(ConfigError, match=r"request\.tool"):
        parse_fixture(observed(transport="mcp_client", request={"arguments": {}}))


def test_a_relayed_call_with_non_object_arguments_is_refused() -> None:
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
