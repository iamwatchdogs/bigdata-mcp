"""`bigdata-mcp capture-fixtures` — record real responses for the golden corpus.

The corpus is §4.3 mandate 5, and §3 records who has to fill it: the owner, on the
machine that can reach the estate. This module is the tool for that. It is not a
stub, because a stub leaves the format unproven against a live endpoint — which is
exactly the "guess the format, then discover it was wrong" failure the
offline-first spec exists to prevent.

What it can reach, and what it cannot:

- `https_api` and `web_session` go through `session.py`, so a capture inherits the
  same TLS trust, redirect policy, byte cap, and timeout as a live request. That is
  the point: a capture taken through a *different* HTTP path would not describe
  what the server actually does.
- `mcp_client` performs a real `tools/call` over stdio.
- `ssh_cli` **cannot** be captured here. It needs an SSH session this host may not
  have, and shelling out to `ssh` would put an SSH client on a capture tool's
  request path — the one place §11.1's allowlist cannot reach it. So `wrap-ssh`
  turns operator-captured output into a fixture instead, and `--help` says so
  rather than letting it be discovered at the first attempt.

Three things are refused rather than recorded, and all three are the same bug in
different clothes: a fixture whose recorded request differs from the request the
server actually received.

`--params` is encoded into the query string rather than filed alongside the request.
`Session.get` takes a URL and nothing else, so a `params` field that never reached
the wire describes a filtered query whose body is the unfiltered response. §8.1's
`resolved_params` exists to be what was *actually* sent, and a capture that
disagrees with its own recorded parameters poisons every replay and every
differential test built on it.

`--method` may only be GET. `Session` exposes only `get`, so a fixture recording a
POST against a body fetched by GET is the same confident wrongness one field out.

`--credential-ref` is refused outright. §15.8 has no plaintext tier and §2.3
mandate 3 says a reference is carried verbatim and never resolved here, so this
build has nothing that could turn `keychain:bigdata-edge` into a usable
`Authorization` value. Sending it anyway had two effects the operator could see
neither: every authenticated capture returned 401, which reads as an expired
credential on the estate; and the backend learned the secret-store *name*, which is
a map to wherever the real credential lives. A refusal is loud and correct; a 401
recorded as an observation is neither.

Everything written is `source = "observed"` with `captured_at` and `provenance`,
and an existing file is never replaced without `--force`: silently overwriting a
captured response destroys the evidence that anything changed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlencode

from bigdata_mcp import capture_mcp
from bigdata_mcp.errors import BigDataMcpError
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport
from bigdata_mcp.session import Session
from bigdata_mcp.timestamps import now

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The seam `capture_mcp` takes, re-exported so `main`'s signature can name it
#: without importing the SDK. A seam rather than a direct `Client` construction so a
#: test can connect an in-process server: the stdio path spawns a subprocess, and a
#: test suite that needs a subprocess to prove a two-line call is a test suite that
#: gets skipped in CI for being slow.
McpConnect = capture_mcp.McpConnect

#: What `add_subparsers` hands back. Named because the private
#: `argparse._SubParsersAction` is not something to annotate against in a module
#: whose whole job is public API.
Subparsers = Any

EXIT_REFUSED = 5
EXIT_EXISTS = 4
EXIT_UNSUPPORTED_TRANSPORT = 3

#: Transports whose capture is a single HTTP request through the session seam.
#: `MCP_CLIENT` is not here: it is a stdio `tools/call`, not an HTTP exchange, and
#: gets its own subcommand.
HTTP_TRANSPORTS: tuple[Transport, ...] = (Transport.HTTPS_API, Transport.WEB_SESSION)

#: Every subcommand that captures a transport. `ssh_cli` is absent because it is
#: deliberately uncapturable here (see `SSH_CLI_RECIPE`), so the set is the enum
#: minus that one member -- the invariant `test_every_transport_has_a_capture_path`
#: asserts, and the reason `_dispatch` can refuse an unknown command instead of
#: falling through to `_capture` and reading `args.url` off a namespace without one.
CAPTURE_COMMANDS: frozenset[str] = frozenset(
    transport.value for transport in HTTP_TRANSPORTS
) | {Transport.MCP_CLIENT.value}

#: The subcommand that wraps output an operator captured by hand.
WRAPPER_COMMAND = "wrap-ssh"

#: §11.1's recipe, reproduced so the operator can paste it unchanged. The exit
#: status is captured separately because `echo` clobbers `$?` otherwise, which is
#: the kind of detail that silently yields a corpus with no exit codes in it.
SSH_CLI_RECIPE = """\
Run this on the edge host, then wrap the two files as a fixture:

  hdfs dfs -count -q -v /warehouse/ 2>/tmp/err.txt >/tmp/out.txt ; echo "exit=$?"

  bigdata-mcp capture-fixtures wrap-ssh \\
    --operation 'hdfs dfs -count -q -v <p>' \\
    --argv '["hdfs","dfs","-count","-q","-v","/warehouse/"]' \\
    --stdout /tmp/out.txt --stderr /tmp/err.txt --exit-code 0 \\
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
    "  ssh_cli     NOT capturable from here: it needs an SSH session, and\n"
    "              shelling out would put an SSH client on a capture\n"
    "              tool's request path where §11.1's allowlist cannot\n"
    "              reach it. Use `wrap-ssh` on the operator's copy.\n\n"
    "  tls_verify  It is a configuration error to capture over https without\n"
    "              --ca-bundle; no default trust source will be invented.\n\n"
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
    command.add_argument(
        "--allowlist-host",
        required=True,
        help=(
            "host the redirect policy is told about. Required rather than "
            "inferred: an empty allowlist is a capture that cannot follow YARN HA"
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


def main(
    argv: Sequence[str] | None = None, *, connect: McpConnect | None = None
) -> int:
    """Run the capture CLI.

    Args:
        argv: Argument vector, or `None` to read `sys.argv`.
        connect: Builds the MCP client for `mcp_client`, or `None` for the real
            stdio client. A seam so a test can drive the whole CLI -- dispatch,
            validation, writing -- against an in-process server.

    Returns:
        0 on a written fixture, 3 for an uncapturable transport, 4 when the
        target exists and `--force` was absent, 5 when the request or the
        arguments were refused, 2 for an argparse usage fault.
    """
    args = build_parser().parse_args(argv)
    try:
        fixture = _dispatch(args, connect=connect)
    except (BigDataMcpError, PermissionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_REFUSED
    return _write(fixture, args.out, force=args.force)


def _dispatch(
    args: argparse.Namespace, *, connect: McpConnect | None = None
) -> Fixture:
    """Route a parsed namespace to the capture path for its transport.

    Args:
        args: The parsed namespace.
        connect: Forwarded to `_capture_mcp`; ignored by the other paths.

    Returns:
        The observed fixture.

    Raises:
        ConfigError: If the command names no capture path. Argparse already refuses
            an unknown command, so this catches a transport that reached the enum
            without reaching a branch here -- a bug that would otherwise read an
            attribute off a namespace that does not have it.
    """
    if args.transport == WRAPPER_COMMAND:
        return _wrap_ssh(args)
    if args.transport == Transport.MCP_CLIENT.value:
        return capture_mcp.capture(
            args.tool,
            _json_object(args.arguments, "arguments"),
            args.command,
            source_id=args.source_id,
            operation=args.operation,
            provenance=args.provenance,
            connect=connect,
        )
    if args.transport in CAPTURE_COMMANDS:
        return _capture(args)
    message = f"no capture path for command {args.transport!r}"
    raise ConfigError(message)


def _capture(args: argparse.Namespace) -> Fixture:
    """Capture one HTTP exchange through the session seam.

    Args:
        args: The parsed namespace.

    Returns:
        The observed fixture.

    Unusable parameters or a refused request surface as a `ConfigError`; a refused
    redirect surfaces as a `PermissionError` naming the rule. Both are caught in
    `main`, so a refusal is a message and an exit code rather than a traceback —
    an operator running this on the edge host should never have to read Python
    internals to find out why their capture did not happen.
    """
    _refuse_an_unsent_method(args.method)
    _refuse_an_unresolved_credential(args.credential_ref)
    params = _json_object(args.params, "params")
    url = args.url.rstrip("/") + args.path
    if params:
        url = f"{url}?{urlencode(params)}"
    transport = Transport(args.transport)

    async def call() -> tuple[int, str, str]:
        async with Session(
            allowlist=frozenset({args.allowlist_host}),
            allow_http=args.url.startswith("http://"),
            ca_bundle=str(args.ca_bundle) if args.ca_bundle else None,
        ) as session:
            response = await session.get(url)
            return response.status, response.body, response.url

    status, body, final_url = asyncio.run(call())
    request: dict[str, Any] = {
        "method": args.method,
        "path": args.path,
        "params": params,
        "url": final_url,
    }
    return Fixture(
        schema_version=FIXTURE_SCHEMA_VERSION,
        source=Source.OBSERVED,
        transport=transport,
        source_id=args.source_id,
        operation=args.operation,
        request=request,
        stdout=body,
        exit_code=status,
        captured_at=now(),
        provenance=args.provenance or f"captured via {url}",
    )


def _wrap_ssh(args: argparse.Namespace) -> Fixture:
    """Wrap operator-captured `ssh_cli` output as an observed fixture.

    Args:
        args: The parsed namespace.

    Returns:
        The observed fixture.
    """
    return Fixture(
        schema_version=FIXTURE_SCHEMA_VERSION,
        source=Source.OBSERVED,
        transport=Transport.SSH_CLI,
        source_id=args.source_id,
        operation=args.operation,
        request={"argv": argv_list(args.argv)},
        stdout=_read(args.stdout),
        stderr=_read(args.stderr),
        exit_code=args.exit_code,
        captured_at=now(),
        provenance=args.provenance,
    )


def _write(fixture: Fixture, out: Path, *, force: bool) -> int:
    """Write a fixture, refusing to clobber without `--force`.

    Args:
        fixture: The fixture to write.
        out: The destination path.
        force: Whether an existing file may be replaced.

    Returns:
        0 on success, 4 when the file exists and `force` is false.
    """
    if out.exists() and not force:
        sys.stderr.write(
            f"error: {out} already exists. A captured fixture is evidence; "
            "replacing one destroys the record of what changed. Pass --force if "
            "you meant it.\n"
        )
        return EXIT_EXISTS
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(fixture.dumps(), encoding="utf-8")
    sys.stdout.write(
        f"wrote {out} ({fixture.source.value}, {fixture.transport.value})\n"
    )
    return 0


def _read(path: Path | None) -> str:
    """Read a captured file verbatim.

    Args:
        path: The file, or `None` for stderr that was not captured.

    Returns:
        The file's text, or an empty string.

    Raises:
        ConfigError: If the file cannot be read.
    """
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"Cannot read captured file {path}: {exc}"
        raise ConfigError(message) from exc


def argv_list(raw: str) -> list[str]:
    """Parse an argv list from JSON text.

    Args:
        raw: A JSON array of strings.

    Returns:
        The argv list.

    Raises:
        ConfigError: If the text is not a non-empty JSON array of strings.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"--argv is not valid JSON: {exc}"
        raise ConfigError(message) from exc
    if not isinstance(parsed, list) or not parsed:
        message = "--argv must be a non-empty JSON array (argv, never a shell string)"
        raise ConfigError(message)
    if not all(isinstance(item, str) for item in parsed):
        message = "--argv must contain only strings"
        raise ConfigError(message)
    return [str(item) for item in parsed]


def _json_object(raw: str, field_name: str) -> dict[str, Any]:
    """Parse a JSON object from text.

    Args:
        raw: The JSON text.
        field_name: The field's name, for the error message.

    Returns:
        The parsed object.

    Raises:
        ConfigError: If the text is not a JSON object.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"--{field_name} is not valid JSON: {exc}"
        raise ConfigError(message) from exc
    if not isinstance(parsed, dict):
        message = f"--{field_name} must be a JSON object"
        raise ConfigError(message)
    return dict(parsed)


def _refuse_an_unsent_method(method: str) -> None:
    """Refuse a method `Session` cannot send.

    Args:
        method: The `--method` value.

    Raises:
        ConfigError: If it is anything but GET. `Session` exposes only `get`, and
            a fixture recording a POST against a body fetched by GET is a
            confidently wrong observation.
    """
    if method.upper() != "GET":
        message = (
            f"--method {method!r} is refused: Session speaks GET only. A fixture "
            "recording a verb the capture did not use is worse than no fixture"
        )
        raise ConfigError(message)


def _refuse_an_unresolved_credential(reference: str) -> None:
    """Refuse `--credential-ref` rather than put the reference on the wire.

    §15.8 has no plaintext tier and §2.3 mandate 3 says the loader carries a
    reference verbatim and never resolves it — so this build has nothing that could
    turn `keychain:bigdata-edge` into a usable `Authorization` value. Two things
    went wrong by sending it anyway, and both are worse than a refusal:

    * every authenticated capture returned 401, and the operator had no way to tell
      that from an expired credential on the estate;
    * the backend learned the secret-store *name*. Sending a reference to a server
      that cannot use it hands an attacker the map to wherever the real credential
      lives.

    Args:
        reference: The `--credential-ref` value, or an empty string.

    Raises:
        ConfigError: If a reference was supplied.
    """
    if not reference:
        return
    message = (
        "--credential-ref is refused: this build has no secret store, so the "
        "reference cannot be resolved into a credential. Sending it verbatim would "
        "put the secret-store name on the wire and return 401. Capture "
        "authenticated exchanges with `wrap-ssh`, which records operator output "
        "without touching the credential"
    )
    raise ConfigError(message)


__all__ = [
    "CAPTURE_COMMANDS",
    "EXIT_EXISTS",
    "EXIT_REFUSED",
    "EXIT_UNSUPPORTED_TRANSPORT",
    "SSH_CLI_RECIPE",
    "argv_list",
    "build_parser",
    "main",
    "now",
]


if __name__ == "__main__":
    raise SystemExit(main())
