"""Tests for a relay that will not start, or starts and will not answer.

Split from `test_capture_mcp.py` for the reason this repository splits any test
file: that one crossed Codacy's 500-line limit, and the two cases here are the
ones that *cannot* inject the client — the failure is inside the SDK's own spawn
or handshake, so a test that substituted a client would be testing nothing.

Both therefore spawn a real subprocess. That is the point: the missing binary and
the child that exits during the handshake are the two ways a stdio server fails
before a `tools/call` is ever answered, and each used to escape as a raw library
exception out of `capture.main`.
"""

from __future__ import annotations

import shlex
import sys
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp import capture
from bigdata_mcp import capture_mcp
from bigdata_mcp.errors import BackendUnreachable
from bigdata_mcp.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


#: Whatever `--command` says for the direct call, because it never reaches a
#: spawn that could be confused with the one under test.
UNUSED_ARGV = "unused-when-injected"


def _argv(out: Path, *, command: str = UNUSED_ARGV) -> list[str]:
    """Return a complete `mcp_client` argv pointing at `command`.

    Args:
        out: Where to write the fixture.
        command: The server command to attempt, as a shell-quoted string.

    Returns:
        An argv list suitable for `capture.main`.
    """
    return [
        "mcp_client",
        "--command",
        command,
        "--tool",
        "schema_get",
        "--arguments",
        '{"name": "users"}',
        "--source-id",
        "schema-registry",
        "--operation",
        "schema",
        "--out",
        str(out),
    ]


def test_a_command_that_cannot_start_is_a_refusal_not_a_traceback(
    tmp_path: Path,
) -> None:
    """A missing binary is the operator's mistake, so it answers as exit 5.

    The seam here deliberately does *not* inject: `connect=None` takes the real
    stdio client, and the command is a path that cannot exist. Before the
    translation, the `FileNotFoundError` from the SDK's spawn escaped `main`,
    which catches `BigDataMcpError` and `PermissionError` only — so a mistyped
    `--command` printed a traceback instead of the refusal every other bad
    argument gets. This is the one capture path where a real subprocess is the
    only way to produce the failure.
    """
    out = tmp_path / "schema.json"

    with pytest.raises(ConfigError, match="could not be started"):
        capture_mcp.capture(
            "schema_get",
            {},
            "/no/such/registry-server",
            source_id="schema-registry",
            operation="schema",
        )

    code = capture.main(_argv(out, command="/no/such/registry-server"))

    assert code == 5, f"expected EXIT_REFUSED (5), got {code}"
    assert not out.exists(), "a launch that never happened wrote a fixture"


def test_a_server_that_dies_in_the_handshake_is_unreachable_not_a_crash(
    tmp_path: Path,
) -> None:
    """A command that starts and exits is `BackendUnreachable`, with a reason.

    Also un-injected, because the failure is inside anyio's TaskGroup: a stdio
    child that exits during the handshake surfaces as an `ExceptionGroup`, not
    as the `MCPError` the protocol errors arrive as. Rendering the members is
    what makes the message name the actual fault, and the exit code is what
    keeps the operator from reading a traceback as a bug in the capture tool.

    The command is built with `shlex.join`, not an f-string. `capture` splits it
    back with POSIX `shlex`, where an unquoted `\\` escapes the next character —
    so on the `windows-latest` cell `D:\\a\\repo\\.venv\\Scripts\\python.exe`
    would come back as `D:arepo.venvScriptspython.exe` and the test would fail
    for a reason that has nothing to do with what it is proving.
    """
    out = tmp_path / "schema.json"
    dead = shlex.join([sys.executable, "-c", "import sys; sys.exit(3)"])

    with pytest.raises(BackendUnreachable, match="mcp_client"):
        capture_mcp.capture(
            "schema_get",
            {},
            dead,
            source_id="schema-registry",
            operation="schema",
        )

    code = capture.main(_argv(out, command=dead))

    assert code == 5, f"expected EXIT_REFUSED (5), got {code}"
    assert not out.exists(), "a capture whose server never answered wrote a fixture"
