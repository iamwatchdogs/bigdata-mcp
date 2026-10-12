"""The `capture-fixtures` command line: what it parses, and what it refuses to.

Split out of `capture.py` when that module crossed Codacy's 500-line limit. The
split follows the seam the module already had: this file owns the shape of an
operator's request -- the subcommands, their flags, and the help text that names
which transports can be captured -- and `capture.py` owns what happens once a
namespace is parsed. A flag's help text is prose, and prose buried in a module
half of whose job is writing fixtures is prose nobody proofreads.

`build_parser` is re-exported from `capture.py`, because that is where callers and
tests already look for it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from bigdata_mcp import capture_mcp
from bigdata_mcp.fixtures.schema import Transport

#: What `add_subparsers` hands back; the private `argparse._SubParsersAction` is
#: not something to annotate against in a module whose job is public API.
Subparsers = Any

McpConnect = capture_mcp.McpConnect

EXIT_REFUSED = 5
EXIT_EXISTS = 4
EXIT_UNSUPPORTED_TRANSPORT = 3

#: Transports whose capture is one HTTP request through the session seam.
#: `MCP_CLIENT` is absent: it is a stdio `tools/call` with its own subcommand.
HTTP_TRANSPORTS: tuple[Transport, ...] = (Transport.HTTPS_API, Transport.WEB_SESSION)

#: Every subcommand that captures a transport; `ssh_cli` is deliberately absent
#: (uncapturable here) -- the invariant `test_every_transport_has_a_capture_path`
#: asserts, and the reason `_dispatch` can refuse an unknown command. The
#: `ssh_cli` subcommand itself only prints the manual recipe and exits 3.
CAPTURE_COMMANDS: frozenset[str] = frozenset(
    transport.value for transport in HTTP_TRANSPORTS
) | {Transport.MCP_CLIENT.value}

#: The subcommand that wraps output an operator captured by hand.
WRAPPER_COMMAND = "wrap-ssh"

#: §11.1's recipe, reproduced so the operator can paste it unchanged. The
#: separate `echo "exit=$?"` is load-bearing, and so is the instruction that
#: follows it: `--exit-code` must carry the status the recipe printed, not a
#: hardcoded 0. A recipe that records a failed command as a success fixture is a
#: confidently wrong observation — the exact thing capture exists to prevent.
SSH_CLI_RECIPE = """\
Run this on the edge host, then wrap the two files as a fixture:

  hdfs dfs -count -q -v /warehouse/ 2>/tmp/err.txt >/tmp/out.txt ; echo "exit=$?"

The status the echo printed is the real exit status. Use it verbatim for
`--exit-code` — passing 0 unconditionally would record a failed command as a
successful fixture:

  bigdata-mcp capture-fixtures wrap-ssh --operation 'hdfs dfs -count -q -v <p>' \\
    --argv '["hdfs","dfs","-count","-q","-v","/warehouse/"]' \\
    --exit-code <status that echo "exit=$?" printed> \\
    --stdout /tmp/out.txt --stderr /tmp/err.txt \\
    --provenance 'laptop -> edge-host-alias' \\
    --out tests/fixtures/observed/hdfs-count.json
"""


#: `--help` text. It lives in a constant rather than inline because it is prose, and
#: prose buried in a call is prose nobody proofreads when the transport list changes.
_TRANSPORT_EPILOG = (
    "TRANSPORT SUPPORT IS NOT UNIFORM, and that is deliberate:\n"
    "  https_api   captured here, through the same session seam a live\n"
    "              request uses, so TLS trust and redirect policy match.\n"
    "  web_session captured here, same seam.\n"
    "  mcp_client  captured here, over a real stdio tools/call.\n"
    "  ssh_cli     NOT capturable: it needs an SSH session, and shelling out\n"
    "              would put an SSH client on the request path where §11.1's\n"
    "              allowlist cannot reach it. `capture-fixtures ssh_cli`\n"
    "              prints the recipe below and exits 3; wrap the files with\n"
    "              `wrap-ssh`.\n\n"
    "  tls_verify  capturing over https without --ca-bundle is a config\n"
    "              error; no default trust source is invented.\n\n"
    f"{SSH_CLI_RECIPE}"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser.

    Returns:
        The parser. `--help` carries the transport-support asymmetry, because a
        capability that has to be discovered by failing is a capability nobody
        relies on.
    """
    parser = argparse.ArgumentParser(
        prog="bigdata-mcp capture-fixtures",
        description=(
            "Record real backend responses as fixtures for the golden corpus. "
            "The corpus is captured by the operator on the machine that reaches "
            "the estate; this server never reaches it (SPEC.md §3)."
        ),
        epilog=_TRANSPORT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # `dest="transport"`, not `dest="command"`: `mcp_client` has its own
    # `--command` option naming the server to launch, and argparse writes option
    # values after the subparser action, so `dest="command"` was silently
    # overwritten by the argv string. The subcommand name then read
    # "python -m registry" and dispatch refused its own subcommand.
    commands = parser.add_subparsers(dest="transport", required=True)

    for transport in HTTP_TRANSPORTS:
        _add_http_command(commands, transport)

    mcp = commands.add_parser(
        capture_mcp.COMMAND, help="capture a relayed MCP tools/call"
    )
    capture_mcp.add_command(mcp)

    wrap = commands.add_parser(
        "wrap-ssh",
        help="turn operator-captured ssh_cli output into a fixture",
    )
    wrap.add_argument("--argv", required=True, help="the command, as a JSON argv list")
    wrap.add_argument("--operation", required=True, help="the invocation, as a label")
    wrap.add_argument("--source-id", default="hdfs")
    wrap.add_argument("--stdout", required=True, type=Path, help="captured stdout file")
    wrap.add_argument("--stderr", default=None, type=Path, help="captured stderr file")
    wrap.add_argument(
        "--exit-code", required=True, type=int, help="captured exit status"
    )
    wrap.add_argument(
        "--provenance", required=True, help="machine and command, verbatim"
    )
    _add_output_arguments(wrap)
    commands.add_parser(
        Transport.SSH_CLI.value,
        help=(
            "not capturable from here; prints the exact manual recipe and "
            "exits 3 on purpose"
        ),
    )
    return parser


def _add_http_command(commands: Subparsers, transport: Transport) -> None:
    """Register the subcommand for one HTTP-ish transport.

    Args:
        commands: The subparser collection to add to.
        transport: Which transport this subcommand captures.
    """
    command = commands.add_parser(
        transport.value, help=f"capture a {transport.value} exchange"
    )
    command.add_argument("--url", required=True, help="endpoint base to call")
    command.add_argument("--source-id", required=True, help="e.g. yarn_rm, solr_query")
    command.add_argument("--operation", required=True, help="e.g. cluster/info")
    command.add_argument("--path", default="/", help="request path, appended to --url")
    command.add_argument(
        "--params", default="{}", help="JSON object of query parameters"
    )
    command.add_argument(
        "--method", default="GET", help="HTTP method; anything but GET is refused"
    )
    _add_allowlist_argument(command)


def _add_allowlist_argument(command: argparse.ArgumentParser) -> None:
    """Register `--allowlist-host`, which repeats for YARN HA's two RMs.

    Split out because the flag's reason is one paragraph and `_add_http_command`
    is already at the length this repository's gate allows. `append` is the fix:
    YARN's standby answers 307 pointing at the active peer, so a single host left
    the HA path uncapturable through the seam §4.2 designed for it — the owner
    could only point `--url` at the active RM, recording a different exchange
    from the one production makes.

    Args:
        command: The subparser to extend.
    """
    command.add_argument(
        "--allowlist-host",
        required=True,
        action="append",
        help=(
            "host the redirect policy is told about; repeat for every peer, "
            "because YARN HA is a 307 between two RMs and one host cannot "
            "follow it. Required rather than inferred: an empty allowlist cannot "
            "follow YARN HA at all"
        ),
    )
    command.add_argument(
        "--credential-ref",
        default="",
        help=(
            "REFUSED in this build: there is no secret store, so a reference "
            "cannot be resolved. Use wrap-ssh for authenticated captures"
        ),
    )
    command.add_argument(
        "--ca-bundle",
        default=None,
        type=Path,
        help=(
            "PEM bundle trusting the captured endpoint's CA. Required for "
            "https:// captures: the session refuses to find trust in a default "
            "source instead of erroring on a missing CA"
        ),
    )
    command.add_argument(
        "--provenance", default="", help="machine and command, verbatim"
    )
    _add_output_arguments(command)


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
