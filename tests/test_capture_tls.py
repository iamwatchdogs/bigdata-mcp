"""TLS trust in the capture path: `--ca-bundle`, and no default trust source.

Split from `test_capture_cli.py` for two reasons, one of which is a hard one.
Lizard's per-file NLOC ceiling is 500 and `test_capture_cli.py` had reached it
before this module existed; the other is that these are the only two tests here
that stand a TLS server up, and mixing them into a module whose fixtures are all
plain HTTP made "which server is this test talking to" a question worth avoiding.

§3 of `SPEC.md` records that this estate's endpoints sit behind a corporate CA,
so the capture path carries the same obligation as the live seam it goes through:
an `https://` capture without `--ca-bundle` is refused, never quietly verified
against whatever roots the laptop happens to have. A capture that succeeded
against the public roots while the operator believed the corporate CA was in play
is a fixture that cannot be trusted later, and a fixture that cannot be trusted
later is worse than a capture that failed loudly.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript):

* Drop the `ca_bundle` plumbing in `capture.py::_capture` ->
  `test_a_tls_capture_trusts_the_configured_bundle` fails on the handshake
  against a self-signed certificate the system roots cannot vouch for.
* Remove the refusal from `session.py::_check_initial_scheme` ->
  `test_an_https_capture_without_a_ca_bundle_is_refused` fails: the request
  reaches the network and dies as `BackendUnreachable` instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bigdata_mcp import capture
from bigdata_mcp.fixtures.fields import load_fixture
from tests.support.http_server import LocalTlsServer
from tests.support.http_server import json_reply

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_an_https_capture_without_a_ca_bundle_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `--ca-bundle` means no capture over https, never a default fall-back.

    `127.0.0.1:1` is not reachable, which is deliberate: the point is that the
    refusal happens before the socket does, so this test needs no server and
    cannot be made green by a lucky connection.
    """
    out = tmp_path / "captured.json"
    code = capture.main([
        "https_api",
        "--url",
        "https://127.0.0.1:1",
        "--source-id",
        "yarn_rm",
        "--operation",
        "GET /x",
        "--allowlist-host",
        "127.0.0.1",
        "--out",
        str(out),
    ])
    assert code == capture.EXIT_REFUSED
    assert not out.exists(), "a refused capture must not leave a fixture behind"
    assert "CA bundle" in capsys.readouterr().err


def test_a_missing_ca_bundle_file_is_a_refusal_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `--ca-bundle` that does not exist must exit 5, not crash the CLI.

    The bundle is loaded when `Session` builds its TLS context, which happens
    inside the capture call. `ssl.create_default_context` raises
    `FileNotFoundError` there — not one of the families `capture.main` catches —
    so the operator saw a traceback instead of the documented refusal. Mutation
    evidence: removing the conversion in `build_ssl_context` turns this test red
    with the raw `FileNotFoundError` propagating out of `capture.main`.
    """
    out = tmp_path / "captured.json"
    code = capture.main([
        "https_api",
        "--url",
        "https://127.0.0.1:1",
        "--source-id",
        "yarn_rm",
        "--operation",
        "GET /x",
        "--allowlist-host",
        "127.0.0.1",
        "--ca-bundle",
        str(tmp_path / "missing.pem"),
        "--out",
        str(out),
    ])
    assert code == capture.EXIT_REFUSED
    assert not out.exists(), "a refused capture must not leave a fixture behind"
    assert "Cannot load CA bundle" in capsys.readouterr().err


def test_a_tls_capture_trusts_the_configured_bundle(tmp_path: Path) -> None:
    """`--ca-bundle` reaches the seam: a host the system roots do not know.

    `LocalTlsServer` presents a certificate generated per server, so this passes
    only if the capture's own context verified against the bundle it was given.
    A capture that verified against the laptop's roots could not reach this
    server at all.
    """
    server = LocalTlsServer()
    server.route("/ws/v1/cluster/info", lambda _p: json_reply({"state": "RUNNING"}))
    server.start()
    try:
        out = tmp_path / "yarn-info.json"
        code = capture.main([
            "https_api",
            "--url",
            server.url(""),
            "--path",
            "/ws/v1/cluster/info",
            "--source-id",
            "yarn_rm",
            "--operation",
            "GET /ws/v1/cluster/info",
            "--allowlist-host",
            "127.0.0.1",
            "--ca-bundle",
            str(server.certificate_path),
            "--provenance",
            "laptop -> rm1",
            "--out",
            str(out),
        ])
        assert code == 0
        fixture = load_fixture(out)
        assert fixture.exit_code == 200
        assert "RUNNING" in fixture.stdout
        assert fixture.transport is capture.Transport.HTTPS_API
    finally:
        server.stop()
