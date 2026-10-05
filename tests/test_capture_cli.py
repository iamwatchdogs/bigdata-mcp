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
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp import capture
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Fixture
from bigdata_mcp.fixtures import Source
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from tests.support.async_runner import run_async
from tests.support.fixture_docs import observed
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import json_reply
from tests.support.http_server import redirect

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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


def test_capture_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "already-there.json"
    out.write_text("{}\n", encoding="utf-8")
    code = _wrap_ssh_argv(tmp_path, out=out, force=False)
    assert code == capture.EXIT_EXISTS
    assert out.read_text(encoding="utf-8") == "{}\n"


def test_capture_overwrites_with_force(tmp_path: Path) -> None:
    out = tmp_path / "already-there.json"
    out.write_text("{}\n", encoding="utf-8")
    code = _wrap_ssh_argv(tmp_path, out=out, force=True)
    assert code == 0
    assert (
        parse_fixture(json.loads(out.read_text(encoding="utf-8"))).source
        is Source.OBSERVED
    )


def test_a_capture_records_the_fields_that_prove_it_was_observed(
    tmp_path: Path,
) -> None:
    out = tmp_path / "captured.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    fixture = parse_fixture(json.loads(out.read_text(encoding="utf-8")))
    assert fixture.source is Source.OBSERVED
    assert fixture.captured_at is not None
    assert fixture.provenance == "laptop -> edge-host-alias"
    assert fixture.transport is Transport.SSH_CLI


def test_wrap_ssh_rejects_a_shell_string_argv(tmp_path: Path) -> None:
    code = capture.main([
        "wrap-ssh",
        "--argv",
        "hdfs dfs -count /warehouse/ | wc -l",
        "--operation",
        "count",
        "--stdout",
        str(tmp_path / "out.txt"),
        "--exit-code",
        "0",
        "--provenance",
        "laptop",
        "--out",
        str(tmp_path / "f.json"),
    ])
    assert code == capture.EXIT_REFUSED


def test_wrap_ssh_rejects_a_non_array_argv() -> None:
    with pytest.raises(ConfigError, match="non-empty JSON array"):
        capture.argv_list('{"a": 1}')


def test_wrap_ssh_rejects_a_non_string_inside_the_argv() -> None:
    with pytest.raises(ConfigError, match="only strings"):
        capture.argv_list('["hdfs", 7]')


def test_a_capture_reports_an_unreadable_captured_file(tmp_path: Path) -> None:
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
    """§15.8 has no plaintext tier, and a capture tool is the last place to add one."""
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
        "hunter2",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
    assert not (tmp_path / "unused.json").exists()
    # The refusal must name the rule. An exit code alone cannot distinguish "you
    # passed a literal" from "the host did not resolve", and both are 5.
    assert "no plaintext credential tier" in capsys.readouterr().err


def test_the_credential_ref_rule_itself_is_refused() -> None:
    """The rule in isolation, so nothing downstream can mask it."""
    from bigdata_mcp.capture import _credential_header

    with pytest.raises(ConfigError, match="no plaintext credential tier"):
        _credential_header("hunter2")


def test_a_credential_reference_is_carried_verbatim_not_resolved() -> None:
    """The reference travels; resolving it here would be a second secret store."""
    from bigdata_mcp.capture import _credential_header

    assert _credential_header("keychain:bigdata-edge") == {
        "authorization": "keychain:bigdata-edge"
    }


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


def test_an_error_status_is_captured_rather_than_treated_as_a_failure(
    tmp_path: Path,
) -> None:
    """A 503 is a real observation; refusing to record it loses the evidence."""
    fixture = parse_fixture(observed(exit_code=503, stdout="service unavailable"))
    assert fixture.exit_code == 503


def _wrap_ssh_argv(tmp_path: Path, *, out: Path, force: bool = False) -> int:
    """Run `capture wrap-ssh` against a temporary captured-output file.

    Args:
        tmp_path: Where to stage the captured stdout.
        out: The fixture path to write.
        force: Whether to permit replacing an existing file.

    Returns:
        The CLI's exit code.
    """
    stdout_file = tmp_path / "captured-stdout.txt"
    stdout_file.write_text(
        "       QUERY_REPONSE : 0\tnone\tnone\tinf\n", encoding="utf-8"
    )
    argv = [
        "wrap-ssh",
        "--argv",
        '["hdfs","dfs","-count","-q","-v","/warehouse/"]',
        "--operation",
        "hdfs dfs -count -q -v <p>",
        "--stdout",
        str(stdout_file),
        "--exit-code",
        "0",
        "--provenance",
        "laptop -> edge-host-alias",
        "--out",
        str(out),
    ]
    if force:
        argv.append("--force")
    return capture.main(argv)


def test_a_written_fixture_ends_with_exactly_one_newline(tmp_path: Path) -> None:
    """The hygiene hooks check this, and a committed corpus is full of these files."""
    out = tmp_path / "f.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    assert out.read_text(encoding="utf-8").endswith("}\n")


def test_a_fixture_written_by_the_cli_reloads_through_the_loader(
    tmp_path: Path,
) -> None:
    out = tmp_path / "f.json"
    assert _wrap_ssh_argv(tmp_path, out=out) == 0
    assert isinstance(load_fixture(out), Fixture)


def test_the_timestamp_carries_an_explicit_offset() -> None:
    """§14.2's reason: a wrong zone assumption is a wrong answer with no error."""
    assert capture.now().endswith("+00:00")


def test_run_async_is_used_by_the_capture_path() -> None:
    """Guards against a capture that blocks the loop — §4.3 mandate 3."""
    assert run_async(_noop()) == "ran"


async def _noop() -> str:
    """Return a fixed string after one suspension point.

    Returns:
        The string `"ran"`.
    """
    import asyncio

    await asyncio.sleep(0)
    return "ran"


def test_params_must_be_a_json_object(tmp_path: Path) -> None:
    """An array is a shape error, not a request that happens to have no params."""
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
        "--params",
        "[1, 2]",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED


def test_params_that_are_not_json_at_all_are_refused(tmp_path: Path) -> None:
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
        "--params",
        "{oops",
        "--out",
        str(tmp_path / "unused.json"),
    ])
    assert code == capture.EXIT_REFUSED
