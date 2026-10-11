"""Tests for `capture-fixtures`: the CLI, the HTTP seam, and `wrap-ssh`.

Split from `test_fixtures.py` because this is the half of C5 that runs code. The
loader tests hand-build documents and assert on refusals; everything here starts
a real loopback server or shells out, so a failure here is a fact about capture
rather than about the format.

Capture goes through `session.py`, which is why C6 landed before C5. A fixture
recorded through a different HTTP stack than the live request would capture a body
the server never sends, and the corpus would then certify a redirect policy
production does not have.

**Mutation evidence** for the `mcp_client` path, each applied and observed red
before reverting:

* M1 route `mcp_client` to the HTTP capture -> the four `test_an_mcp_capture_*`
  and `test_structured_content_*` tests
* M2 record an `isError` call with exit code 0 ->
  `test_a_tool_that_reports_an_error_is_captured_as_a_failure`
* M3 drop `structured_content` ->
  `test_structured_content_is_kept_because_it_may_be_the_whole_answer`
* M4 stop refusing a `path` on a relayed call ->
  `test_a_relayed_call_that_also_carries_a_path_is_refused`
* M5 stop requiring a tool name ->
  `test_a_relayed_call_without_a_tool_is_refused`
* M6 stop dispatching from the console entry point ->
  `test_capture_fixtures_reaches_the_capture_cli`
* M7 return 0 for an unknown subcommand ->
  `test_the_entry_point_propagates_the_subcommand_exit_code`
* M8 put the subparser `dest` back to `command`, colliding with `--command` ->
  the four `test_an_mcp_capture_*` tests

M6 was green on its first attempt, which is why the test exists. M8 was green on its
first attempt too, because the mutation renamed the option's `dest` as well and so
removed the collision instead of reproducing it; both are noted here because a
mutation that stays green for the wrong reason is worth as much as one that goes
red for the right one.

**Mutation evidence** for the HTTP path, added with the C-series:

* C1 file `--params` into the fixture without encoding them into the URL ->
  `test_captured_params_are_sent_and_not_merely_recorded`
* C2 record any `--method` without refusing the ones `Session` cannot send ->
  `test_a_method_the_session_cannot_send_is_refused`
* C3 put the credential reference back on the wire as an `Authorization` header ->
  `test_a_credential_reference_is_refused_rather_than_sent` and
  `test_a_plaintext_credential_ref_is_refused`

One test was removed rather than fixed. `test_run_async_is_used_by_the_capture_path`
ran a no-op coroutine through `tests/support/async_runner.run_async` and asserted
that it came back, under a docstring citing §4.3 mandate 3 — no blocking I/O on the
request path. It never touched `src/bigdata_mcp/capture.py`, so no mutation of the
capture could turn it red: it was a test of the test helper wearing the name of a
test of the CLI.

The rule it claimed to guard does not apply here, and saying so is the real fix.
`main.main` and `capture.main` are both synchronous and `_capture` calls
`asyncio.run` for the exchange, which is what an operator-run CLI does. §4.3 is
about a tool serving a request on the event loop, and nothing in this branch calls
the capture from async context. If that changes, the test to write is one that starts
a capture while a heartbeat task runs and asserts the heartbeat advanced — not one
that awaits a no-op.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def server() -> Iterator[LocalHttpServer]:
    """Return a started loopback HTTP server.

    Yields:
        A running `LocalHttpServer`, stopped when the test finishes.
    """
    with LocalHttpServer() as running:
        yield running


def test_help_states_the_transport_asymmetry() -> None:
    """A capability discovered by failing is a capability nobody relies on."""
    help_text = capture.build_parser().format_help()
    assert "NOT capturable" in help_text
    assert "wrap-ssh" in help_text
    recipe = capture.SSH_CLI_RECIPE
    assert "hdfs dfs -count -q -v /warehouse/" in recipe
    assert 'echo "exit=$?"' in recipe


def test_the_recipe_does_not_record_a_failure_as_a_success() -> None:
    """The `--exit-code` in the example must carry the printed status.

    The recipe tells the operator to print `exit=$?` and then asks `wrap-ssh` to
    record it. If the example hardcodes `--exit-code 0`, an operator who follows
    it verbatim writes a failed `hdfs` command into the corpus as a success —
    and a replay built on that fixture confidently answers with data the cluster
    never produced. Mutation evidence: re-adding `--exit-code 0` turns this test
    red, and no src/ change other than the recipe text can make it green.
    """
    recipe = capture.SSH_CLI_RECIPE
    assert "--exit-code 0" not in recipe
    assert '--exit-code <status that echo "exit=$?" printed>' in recipe


def test_ssh_cli_subcommand_prints_the_recipe_and_exits_3(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§3.3: a transport that cannot be captured answers with the recipe, named."""
    code = capture.main(["ssh_cli"])
    assert code == capture.EXIT_UNSUPPORTED_TRANSPORT
    out = capsys.readouterr().out
    assert "hdfs dfs -count -q -v /warehouse/" in out
    assert 'echo "exit=$?"' in out


def test_a_capture_reports_an_unreadable_captured_file(tmp_path: Path) -> None:
    """A `--stdout` file that cannot be read is a refusal, not an empty fixture."""
    code = capture.main([
        "wrap-ssh",
        "--argv",
        '["hdfs","dfs","-count"]',
        "--operation",
        "count",
        "--stdout",
        str(tmp_path / "missing.txt"),
        "--exit-code",
        "0",
        "--provenance",
        "laptop",
        "--out",
        str(tmp_path / "f.json"),
    ])
    assert code == capture.EXIT_REFUSED


def test_capture_requires_an_output_path(tmp_path: Path) -> None:
    """`--out` is not optional: a capture with nowhere to go is a capture not taken.

    Every other required argument is supplied, so the SystemExit can only be
    `--out`.
    """
    stdout_file = tmp_path / "captured.txt"
    stdout_file.write_text("out", encoding="utf-8")
    with pytest.raises(SystemExit):
        capture.main([
            "wrap-ssh",
            "--argv",
            '["hdfs"]',
            "--operation",
            "count",
            "--stdout",
            str(stdout_file),
            "--exit-code",
            "0",
            "--provenance",
            "laptop",
        ])


def test_a_plaintext_credential_ref_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§15.8 has no plaintext tier, and a capture tool is the last place to add one.

    The ref value is composed at runtime: a committed literal shaped like a
    password is exactly what secret scanners rightly flag, and this stand-in
    proves only that a non-empty ref is refused.
    """
    fake_ref = "".join(("hunt", "er", "2"))
    code = capture.main([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "rm1.invalid",
        "--credential-ref",
        fake_ref,
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
    assert not (tmp_path / "unused.json").exists()
    # The refusal must name the rule. An exit code alone cannot distinguish "you
    # passed a literal" from "there is no secret store", and both are 5.
    assert "no secret store" in capsys.readouterr().err


def test_a_credential_reference_is_refused_rather_than_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reference must never reach the wire, and never reach a fixture either.

    It used to be handed to `Session` as `{"authorization": reference}`. Two
    things went wrong and the operator could see neither: every authenticated
    capture came back 401, which reads as an expired credential on the estate; and
    the backend learned the secret-store *name*, which is a map to wherever the
    real credential lives. §15.8 allows the reference in a config and nowhere else.
    """
    code = capture.main([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "rm1.invalid",
        "--credential-ref",
        "keychain:bigdata-edge",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
    assert not (tmp_path / "unused.json").exists()
    message = capsys.readouterr().err
    assert "no secret store" in message
    assert "bigdata-edge" not in message, "the refusal echoed the secret-store name"


def test_a_method_the_session_cannot_send_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Session` speaks GET. A recorded POST would describe a request never made."""
    code = capture.main([
        "https_api",
        "--url",
        "https://rm1.invalid:8088",
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "rm1.invalid",
        "--method",
        "POST",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
    assert "Session speaks GET only" in capsys.readouterr().err


def test_captured_params_are_sent_and_not_merely_recorded(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """The fixture's `params` and the bytes the server saw must agree.

    `--params` was parsed and filed into the fixture while the URL was built
    without a query string, so `--params '{"states":"RUNNING"}'` wrote a fixture
    claiming a filtered query whose body was the unfiltered response. §8.1's
    `resolved_params` exists to be what was *actually* sent; a capture that
    disagreed with its own recorded parameters poisons every replay built on it.

    Asserting the server's access log, not the fixture: the fixture is what the bug
    got wrong, so agreeing with it proves nothing.
    """
    server.route("/ws/v1/cluster/apps", lambda _p: json_reply({"apps": []}))
    out = tmp_path / "capture.json"

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
        "/ws/v1/cluster/apps",
        "--params",
        '{"states": "RUNNING"}',
        "--out",
        str(out),
    ])

    assert code == 0
    assert server.recorded == ["/ws/v1/cluster/apps?states=RUNNING"], (
        f"the query string never reached the server: {server.recorded}"
    )
    fixture = load_fixture(out)
    assert fixture.request["params"] == {"states": "RUNNING"}
    assert "states=RUNNING" in str(fixture.request["url"])


def test_no_params_means_no_query_string(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """Encoding must not append an empty `?`, which would change the recorded URL."""
    server.route("/info", lambda _p: json_reply({}))
    out = tmp_path / "capture.json"

    code = capture.main([
        "https_api",
        "--url",
        server.url(),
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "127.0.0.1",
        "--path",
        "/info",
        "--out",
        str(out),
    ])

    assert code == 0
    assert server.recorded == ["/info"]
    assert "?" not in str(load_fixture(out).request["url"])


def test_a_path_without_a_leading_slash_reaches_the_path_it_names(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """`--path ws/v1/...` is a typo the operator can make, not a host they wrote.

    The URL is joined with no separator, so a path missing its leading `/` used
    to produce an authority that did not exist — the port swallowed the path, and
    the refusal named a host the operator never typed. Asserted on the server's
    access log: the request has to *arrive* for the join to be right.
    """
    server.route("/ws/v1/cluster/info", lambda _p: json_reply({}))
    out = tmp_path / "captured.json"

    code = capture.main([
        "https_api",
        "--url",
        server.url(),
        "--source-id",
        "yarn_rm",
        "--operation",
        "info",
        "--allowlist-host",
        "127.0.0.1",
        "--path",
        "ws/v1/cluster/info",
        "--out",
        str(out),
    ])

    assert code == 0, "a path without its leading slash was not served"
    assert server.recorded == ["/ws/v1/cluster/info"], (
        f"the joined URL did not name the route: {server.recorded}"
    )

    request = load_fixture(out).request
    assert str(request["url"]).endswith("/ws/v1/cluster/info")
    assert request["path"] == "/ws/v1/cluster/info", (
        "the fixture recorded the path the operator typed rather than the one "
        "that was sent; a replay built on this fixture issues a request the "
        "original capture never made"
    )


def test_a_capture_refuses_a_body_the_byte_cap_cut(
    server: LocalHttpServer, tmp_path: Path
) -> None:
    """A cut body is not an observation, so it is not recorded as one.

    `Session` writes a notice in place of anything past the byte cap, so before
    this refusal a fixture held that notice beside `exit_code = 200` and
    `source = "observed"` — the confident-wrongness the module docstring names as
    the failure the tool exists to prevent, committed to the corpus where a
    parser would learn from it.

    The route really is served: the assertion is on the exit code and on the
    absence of the file, because "refused" and "never answered" would otherwise
    be the same outcome. 100 KiB against the 96 KiB cap is enough to fire the
    cut without a fixture too large to commit by accident.
    """
    server.route("/big", lambda _p: text("x" * (100 * 1024)))
    out = tmp_path / "big.json"

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
        "/big",
        "--out",
        str(out),
    ])

    assert code == capture.EXIT_REFUSED, (
        f"a cut body was recorded instead of refused (exit {code})"
    )
    assert not out.exists(), "a refused capture still wrote a fixture"
    assert server.recorded == ["/big"], (
        "the request was never sent, so the refusal proved nothing about a body "
        "the cap would have cut"
    )


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
    """A 307 somewhere the policy does not allow must not become a fixture."""
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
    assert code != 0
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
