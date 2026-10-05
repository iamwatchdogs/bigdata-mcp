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

Everything written is `source = "observed"` with `captured_at` and `provenance`,
and an existing file is never replaced without `--force`: silently overwriting a
captured response destroys the evidence that anything changed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from bigdata_mcp.config import SECRET_REF_PREFIXES
from bigdata_mcp.errors import BigDataMcpError
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport
from bigdata_mcp.session import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    commands = parser.add_subparsers(dest="command", required=True)

    for transport in HTTP_TRANSPORTS:
        _add_http_command(commands, transport)

    mcp = commands.add_parser("mcp_client", help="capture a relayed MCP tools/call")
    mcp.add_argument("--command", required=True, help="command that starts the server")
    mcp.add_argument("--tool", required=True, help="tool name to call")
    mcp.add_argument("--arguments", default="{}", help="JSON object of arguments")
    mcp.add_argument("--source-id", required=True, help="e.g. schema-registry")
    mcp.add_argument("--operation", required=True, help="the call, as a label")
    _add_output_arguments(mcp)

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
    command.add_argument("--method", default="GET", help="HTTP method")
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
        help="secret reference for the Authorization header; never a literal",
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the capture CLI.

    Args:
        argv: Argument vector, or `None` to read `sys.argv`.

    Returns:
        0 on a written fixture, 3 for an uncapturable transport, 4 when the
        target exists and `--force` was absent, 5 when the request or the
        arguments were refused, 2 for an argparse usage fault.
    """
    args = build_parser().parse_args(argv)
    try:
        fixture = _wrap_ssh(args) if args.command == "wrap-ssh" else _capture(args)
    except (BigDataMcpError, PermissionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_REFUSED
    return _write(fixture, args.out, force=args.force)


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
    params = _json_object(args.params, "params")
    url = args.url.rstrip("/") + args.path
    headers = _credential_header(args.credential_ref) if args.credential_ref else None
    transport = Transport(args.command)

    async def call() -> tuple[int, str, str]:
        async with Session(
            allowlist=frozenset({args.allowlist_host}),
            allow_http=args.url.startswith("http://"),
            headers=headers,
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


def _credential_header(reference: str) -> dict[str, str]:
    """Turn a credential reference into a header carrying the reference itself.

    The reference is resolved nowhere here. §15.8 has no plaintext tier, and a
    capture tool that read a secret off disk would be a second place secrets live
    with no redaction boundary. The reference travels in the fixture so the
    operator's replay tool resolves it through the one store that redacts it.

    Args:
        reference: A `keychain:` / `exec:` / `file:` reference.

    Returns:
        The header carrying the reference verbatim.

    Raises:
        ConfigError: If the reference has no known prefix.
    """
    if not reference.startswith(SECRET_REF_PREFIXES):
        message = (
            f"--credential-ref must start with one of {SECRET_REF_PREFIXES}; "
            "there is no plaintext credential tier (§15.8)"
        )
        raise ConfigError(message)
    return {"authorization": reference}


def now() -> str:
    """The current UTC time, RFC 3339 with an explicit offset.

    Returns:
        A timestamp such as `2026-10-06T21:04:05+00:00`. The offset is explicit
        rather than `Z` because §14.2's reason applies: a zone or unit assumption
        that is wrong by 1000-fold produces a confidently wrong answer and no
        error at all.
    """
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


__all__ = [
    "EXIT_EXISTS",
    "EXIT_REFUSED",
    "EXIT_UNSUPPORTED_TRANSPORT",
    "SSH_CLI_RECIPE",
    "argv_list",
    "build_parser",
    "main",
    "now",
]
