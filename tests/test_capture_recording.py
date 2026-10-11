"""What a capture records, and what it refuses to record.

The companion to `test_capture_cli.py`, which holds the CLI surface: the parser,
the exit codes, and the pre-flight refusals. This half is what ends up on disk —
status, body, a redirect that was refused, an `HA-Redirect-Peers` cluster, and
the two guards that stop a job writing what it should not.

The write guard is tested by bypassing its own preflight on purpose. Asserting
the refusal only through the CLI would prove the preflight fired, not that the
write is actually refused; calling the private writer directly closes that hole.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bigdata_mcp import capture
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from tests.support.fixture_docs import observed
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import json_reply
from tests.support.http_server import redirect
from tests.support.http_server import text


def test_an_http_capture_records_the_real_status_and_body(
    server: LocalHttpServer,
    tmp_path: Path,
) -> None:
    """The capture goes through the same seam a live request uses."""
    server.route("/ws/v1/cluster/info", lambda _p: json_reply({"state": "STANDBY"}))
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
        "--provenance",
        "laptop -> rm1",
        "--out",
        str(out),
    ])
    assert code == 0
    fixture = load_fixture(out)
    assert fixture.exit_code == 200
    assert "STANDBY" in fixture.stdout
    assert fixture.transport is Transport.HTTPS_API
    assert fixture.request["path"] == "/ws/v1/cluster/info"


def test_a_capture_records_a_refused_redirect_as_a_failure(
    server: LocalHttpServer,
    tmp_path: Path,
) -> None:
    """A 307 somewhere the policy does not allow must not become a fixture.

    The exit code is pinned rather than checked non-zero, because the CLI has
    three failure codes and only one of them means "the policy said no". A
    caller that branches on the reason -- and `test_capture_cli.py` documents
    which is which -- would be satisfied by an assertion this loose, so the test
    would keep passing if a refusal started being reported as, say, a missing
    file.

    `recorded` is the other half: it holds every path the server was asked for,
    so `["/ws"]` is what separates "the request went out and the *redirect* was
    refused" from "nothing was sent at all". The second would make this test
    pass against a pre-flight that fires first, and the redirect policy would
    then be covered by nothing.
    """
    server.route("/ws", lambda _p: redirect("http://elsewhere.invalid/x"))
    code = capture.main([
        "https_api",
        "--url",
        server.url(""),
        "--path",
        "/ws",
        "--source-id",
        "yarn_rm",
        "--operation",
        "GET /ws",
        "--allowlist-host",
        "127.0.0.1",
        "--out",
        str(tmp_path / "never.json"),
    ])
    assert code == capture.EXIT_REFUSED
    assert server.recorded == ["/ws"]
    assert not (tmp_path / "never.json").exists()


def test_a_capture_can_be_given_every_host_an_ha_redirect_needs(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """§3's HA standby answer: one flag per peer, or the 307 is refused.

    YARN's standby RM answers 307 pointing at the active peer, and the redirect
    allowlist is per-host. A single `--allowlist-host` therefore made the HA path
    uncapturable through the seam §4.2 designed for it — the owner could only
    point `--url` at the active RM, which records a different exchange from the
    one production makes.

    Both peers are the same loopback server here, because what is under test is
    that the flag *repeat*s and every value reaches the allowlist, not that two
    hosts exist.
    """
    server.route("/apps", lambda _p: json_reply({"apps": {"app": []}}))
    out = tmp_path / "ha.json"

    code = capture.main([
        "https_api",
        "--url",
        server.url(),
        "--source-id",
        "yarn_rm",
        "--operation",
        "apps",
        "--allowlist-host",
        "127.0.0.1",
        "--allowlist-host",
        "127.0.0.1",
        "--path",
        "/apps",
        "--out",
        str(out),
    ])

    assert code == 0, "a repeated --allowlist-host was refused"
    assert server.recorded == ["/apps"]


def test_an_error_status_is_captured_rather_than_treated_as_a_failure(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """A 503 is a real observation; refusing to record it loses the evidence.

    Served from the loopback server rather than assembled with `observed()`: a
    fixture built in-process proves `parse_fixture` reads a status nobody put
    there in the first place, and a `_capture` that started refusing non-2xx
    statuses would leave this test green.
    """
    server.route("/down", lambda _p: text("service unavailable", status=503))
    out = tmp_path / "down.json"

    code = capture.main([
        "https_api",
        "--url",
        server.url(),
        "--source-id",
        "yarn_rm",
        "--operation",
        "apps",
        "--allowlist-host",
        "127.0.0.1",
        "--path",
        "/down",
        "--out",
        str(out),
    ])

    assert code == 0, "an error status was treated as a failed capture"
    fixture = load_fixture(out)
    assert fixture.exit_code == 503
    assert fixture.stdout == "service unavailable"


def test_the_write_guard_still_refuses_when_the_preflight_is_bypassed(
    tmp_path: Path,
) -> None:
    """`_write` keeps its own check, because `main`'s can lose a race.

    The pre-flight in `main` is what an operator normally meets; `_write` is the
    second guard behind it, and a file that appears between the two `stat` calls
    is exactly the case it is for. Exercised by calling `_write` directly, since
    nothing on the CLI path can reach it any more.
    """
    from bigdata_mcp.capture import _write

    out = tmp_path / "already-there.json"
    out.write_text("{}\n", encoding="utf-8")

    assert _write(parse_fixture(observed()), out, force=False) == capture.EXIT_EXISTS
    assert out.read_text(encoding="utf-8") == "{}\n"


def test_dispatch_refuses_a_command_with_no_capture_path(tmp_path: Path) -> None:
    """`_dispatch` is the last line between a parsed namespace and a path.

    The parser restricts the choices, so this branch is unreachable from the CLI
    — which is why it is called directly: a transport added to the enum without a
    branch here would otherwise read `args.url` off a namespace that does not have
    it, and the refusal is the only thing standing in the way.
    """
    from bigdata_mcp.capture import _dispatch

    namespace = capture.build_parser().parse_args([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "apps",
        "--allowlist-host",
        "rm1.invalid",
        "--path",
        "/ws/v1/cluster/apps",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    namespace.transport = "no-such-transport"

    with pytest.raises(ConfigError, match="no capture path"):
        _dispatch(namespace)


def test_capture_module_executes_as_a_script() -> None:
    """`python -m bigdata_mcp.capture` reaches `main`, as the guard promises.

    Follows `tests/test_main.py`'s pattern rather than inventing a second one:
    the call is asserted through `sys.settrace` because `runpy` yields a callable
    `main` whether or not the guard exists, so a namespace check would pass with
    the guard deleted. `sys.argv` is set for the duration because the guard now
    dispatches on it, and left alone the test fails on whatever flag pytest
    happens to pass.
    """
    import runpy
    import sys
    from typing import Any

    script = Path(__file__).resolve().parents[1] / "src" / "bigdata_mcp" / "capture.py"
    assert script.is_file(), f"capture script is missing: {script}"

    calls: list[tuple[str, int]] = []
    previous_tracer = sys.gettrace()

    def tracer(frame: Any, event: Any, arg: Any) -> Any:
        """Record every `call` to a function named `main`, with its file.

        Args:
            frame: The frame the event fired for.
            event: The event name. Only `call` is of interest here.
            arg: The event's payload, unused.

        Returns:
            Whatever the previously installed trace function returns, so coverage
                measurement keeps running: a test that silently disabled it to
                prove a packaging fact would be a poor trade.
        """
        if event == "call" and frame.f_code.co_name == "main":
            calls.append((frame.f_code.co_filename, frame.f_code.co_firstlineno))
        if previous_tracer is not None:
            return previous_tracer(frame, event, arg)
        return None

    previous_argv = sys.argv
    sys.argv = ["capture-fixtures", "ssh_cli"]
    sys.settrace(tracer)
    try:
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.settrace(previous_tracer)
        sys.argv = previous_argv

    here = [where for where in calls if where[0] == str(script)]
    assert len(here) == 1, (
        f"executing {script.name} as __main__ called a `main` defined in that "
        f"file {len(here)} times ({here!r}); expected exactly 1. Without the "
        'guard `if __name__ == "__main__": raise SystemExit(main())` the module '
        "defines an entry point that never runs"
    )
    # `ssh_cli` prints the recipe and exits 3, which is the cheapest path through
    # `main` that needs no server and no fixture.
    assert raised.value.code == capture.EXIT_UNSUPPORTED_TRANSPORT
