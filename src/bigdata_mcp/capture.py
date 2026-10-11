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

Four things are refused rather than recorded, and all four are the same bug in
different clothes: a fixture whose recorded exchange differs from the exchange the
server actually made. The fourth is a **truncated body** — `Session` replaces
anything past the byte cap with a notice, so a capture of a larger response would
record the notice as `stdout` beside `exit_code = 200` and `source = "observed"`.
A parser built on that fixture learns a response the estate never sent, which is
the failure this whole tool exists to prevent, so `_capture` refuses it.

Everything written is `source = "observed"` with `captured_at` and `provenance`,
and an existing file is never replaced without `--force`: silently overwriting a
captured response destroys the evidence that anything changed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import cast
from urllib.parse import urlencode

from bigdata_mcp import capture_mcp
from bigdata_mcp.capture_cli import CAPTURE_COMMANDS
from bigdata_mcp.capture_cli import EXIT_EXISTS
from bigdata_mcp.capture_cli import EXIT_REFUSED
from bigdata_mcp.capture_cli import EXIT_UNSUPPORTED_TRANSPORT
from bigdata_mcp.capture_cli import SSH_CLI_RECIPE
from bigdata_mcp.capture_cli import WRAPPER_COMMAND
from bigdata_mcp.capture_cli import build_parser
from bigdata_mcp.errors import BigDataMcpError
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport
from bigdata_mcp.session import Session
from bigdata_mcp.timestamps import now

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence
    from pathlib import Path

#: The seam `capture_mcp` takes, re-exported so `main`'s signature can name it
#: without importing the SDK. A seam rather than a direct `Client` construction so a
#: test can connect an in-process server: the stdio path spawns a subprocess, and a
#: test suite that needs a subprocess to prove a two-line call is a test suite that
#: gets skipped in CI for being slow.
McpConnect = capture_mcp.McpConnect

#: What `add_subparsers` hands back; the private `argparse._SubParsersAction` is
#: not something to annotate against in a module whose job is public API.
Subparsers = Any


def main(
    argv: Sequence[str] | None = None, *, connect: McpConnect | None = None
) -> int:
    """Run the capture CLI.

    Args:
        argv: Argument vector, or `None` to read `sys.argv`.
        connect: Builds the MCP client for `mcp_client`, or `None` for the real
            stdio client — the seam a test uses to drive the whole CLI
            in-process.

    Returns:
        0 on a written fixture, 3 for `ssh_cli` (the recipe is the output), 4
        when the target exists without `--force`, 5 when the request or the
        arguments were refused, 2 for an argparse usage fault.
    """
    args = build_parser().parse_args(argv)
    if args.transport == Transport.SSH_CLI.value:
        sys.stdout.write(SSH_CLI_RECIPE)
        return EXIT_UNSUPPORTED_TRANSPORT
    # Before the request, not after it. The refusal has nothing to do with what
    # the cluster would answer, and paying for the round trip to learn that last
    # week's file is still there is a trip the operator never asked for. `_write`
    # keeps the same check: this one can lose a race it cannot see, and a second
    # guard costs a `stat`.
    if args.out.exists() and not args.force:
        sys.stderr.write(_existing_target_message(args.out))
        return EXIT_EXISTS
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
        ConfigError: If the command names no capture path — a bug that would
            otherwise read `args.url` off a namespace that does not have it.
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
        The observed fixture. Refusals surface as `ConfigError` or
        `PermissionError`, both caught in `main` and turned into a message and
        an exit code rather than a traceback on the operator's edge host.
    """
    _refuse_an_unsent_method(args.method)
    _refuse_an_unresolved_credential(args.credential_ref)
    params = _json_object(args.params, "params")
    # A leading `/` is supplied when the operator left it off rather than
    # assumed. `url.rstrip("/") + path` joins with no separator, so
    # `--path ws/v1/cluster/info` produces `https://rm1:8088ws/v1/...` — the port
    # swallows the path, the authority stops existing, and the refusal names a
    # host the operator never wrote.
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    url = args.url.rstrip("/") + path
    if params:
        url = f"{url}?{urlencode(params)}"
    transport = Transport(args.transport)

    async def call() -> tuple[int, str, str]:
        """Fetch the URL through the session seam.

        A closure rather than a module function because the session policy is
        built entirely from `args`; hoisting it would mean passing six arguments
        to express what is already in scope.

        Returns:
            The status, the decoded body, and the URL the body actually came
            from. The last is the *resolved* URL after redirects, because §8.1's
            `resolved_params` exists for a confidently wrong answer.

        Raises:
            ConfigError: If the byte cap cut the body. `Session` writes a notice
                in place of the missing bytes, so a fixture built from a cut body
                would record that notice beside a 200 and call the result an
                observation — the confident-wrongness this module refuses.
        """
        async with Session(
            allowlist=frozenset(args.allowlist_host),
            allow_http=args.url.startswith("http://"),
            ca_bundle=str(args.ca_bundle) if args.ca_bundle else None,
        ) as session:
            response = await session.get(url)
            if response.truncated:
                message = (
                    f"response from {response.url} exceeds the byte cap; the body "
                    "was cut, and a cut body is not an observation"
                )
                raise ConfigError(message)
            return response.status, response.body, response.url

    status, body, final_url = asyncio.run(call())
    request: dict[str, Any] = {
        "method": args.method,
        # The normalised path, not `args.path`. The URL above was built from the
        # normalised one, so recording the raw spelling would file a request the
        # server never received -- the same confident wrongness in reverse.
        "path": path,
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


def _existing_target_message(out: Path) -> str:
    """The refusal printed when a destination holds evidence already.

    One message in one place, because the check now happens twice: before the
    request is sent, and again at the write. Two copies of "already exists"
    would let them drift, and the operator's only clue about why a capture did
    nothing is the sentence that came back.

    Args:
        out: The destination path.

    Returns:
        The message, newline-terminated like every other CLI diagnostic here.
    """
    return (
        f"error: {out} already exists. A captured fixture is evidence; "
        "replacing one destroys the record of what changed. Pass --force if "
        "you meant it.\n"
    )


def _write(fixture: Fixture, out: Path, *, force: bool) -> int:
    """Write a fixture, refusing to clobber without `--force`.

    The existence check is a second guard rather than the only one: `main`
    asks the same question before it sends a request, so an operator who points
    a capture at last week's fixture learns so without waiting for the cluster
    to answer.

    Args:
        fixture: The fixture to write.
        out: The destination path.
        force: Whether an existing file may be replaced.

    Returns:
        0 on success, 4 when the file exists and `force` is false.
    """
    if out.exists() and not force:
        sys.stderr.write(_existing_target_message(out))
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


def _parse_json(raw: str, field_name: str) -> object:
    """Parse JSON text, naming `field_name` in the refusal if it is malformed.

    One well-formedness check shared by every JSON-carrying capture flag.

    Args:
        raw: The JSON text, exactly as the operator passed it.
        field_name: The flag the text came from, for the error message.

    Returns:
        The parsed value. Narrowing the type is the caller's job.

    Raises:
        ConfigError: If the text is not valid JSON, with the parser's line and
            column so the fault is fixable.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"--{field_name} is not valid JSON: {exc}"
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
    parsed = _parse_json(raw, "argv")
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
    parsed = _parse_json(raw, field_name)
    if not isinstance(parsed, dict):
        message = f"--{field_name} must be a JSON object"
        raise ConfigError(message)
    return cast("dict[str, Any]", parsed)


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

    The module docstring carries the full reasoning. In short: §15.8 has no
    plaintext tier and §2.3 mandate 3 says a reference is carried verbatim and
    never resolved here, so sending one puts the secret-store name on the wire
    and returns an indistinguishable 401 — both strictly worse than a refusal.

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
