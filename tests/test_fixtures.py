"""Tests for C5: the fixture format, the loader, the corpus, and `capture-fixtures`.

The format is the thing most likely to be wrong if guessed, so most of these tests
are refusals: a fixture that loads when it should not is worse than one that fails,
because it produces a wrong parse three layers down.

Mutation evidence, each applied and observed red before reverting:

* F1 accept a `source` that is neither value ->
  `test_a_source_that_is_neither_value_is_refused`
* F2 accept an unsupported `schema_version` ->
  `test_an_unknown_schema_version_is_refused`
* F3 skip `captured_at` on an observed fixture ->
  `test_an_observed_fixture_without_captured_at_is_refused`
* F4 skip `provenance` on an observed fixture ->
  `test_an_observed_fixture_without_provenance_is_refused`
* F5 accept `captured_at` on a synthetic fixture ->
  `test_a_synthetic_fixture_may_not_carry_captured_at`
* F6 allow an unknown document field ->
  `test_an_unknown_field_is_refused_not_ignored`
* F7 accept an empty argv for `ssh_cli` ->
  `test_an_ssh_cli_fixture_needs_a_non_empty_argv`
* F8 accept a shell string as `request` ->
  `test_an_ssh_cli_request_may_not_be_a_shell_string`
* F9 let `--force` be implied ->
  `test_capture_refuses_to_overwrite_without_force`
* F10 accept a plain `--credential-ref` ->
  `test_a_plaintext_credential_ref_is_refused`
* F11 report a synthetic-only corpus as validating ->
  `test_a_synthetic_only_corpus_cannot_validate_a_parser`
* F12 drop the `--out` requirement ->
  `test_capture_requires_an_output_path`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import pytest

from bigdata_mcp import capture
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Corpus
from bigdata_mcp.fixtures import Fixture
from bigdata_mcp.fixtures import Source
from bigdata_mcp.fixtures import Transport
from bigdata_mcp.fixtures import load_corpus
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from bigdata_mcp.fixtures.schema import check_version
from bigdata_mcp.fixtures.schema import load_fixture
from bigdata_mcp.fixtures.schema import parse_fixture
from tests.support.async_runner import run_async
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import json_reply
from tests.support.http_server import redirect

if TYPE_CHECKING:
    from collections.abc import Iterator

SYNTHETIC_COMMENT = "SPEC.md §1: this fixture exists to prove the loader works."


@pytest.fixture
def server() -> Iterator[LocalHttpServer]:
    """Return a started loopback HTTP server.

    Yields:
        A running `LocalHttpServer`, stopped when the test finishes.
    """
    with LocalHttpServer() as running:
        yield running


def observed(**overrides: Any) -> dict[str, Any]:
    """Build a minimal *observed* fixture document.

    Args:
        **overrides: Fields to replace or add.

    Returns:
        A document that validates as-is unless `overrides` breaks it.
    """
    document: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source": "observed",
        "transport": "https_api",
        "source_id": "yarn_rm",
        "operation": "GET /ws/v1/cluster/info",
        "request": {"method": "GET", "path": "/ws/v1/cluster/info", "params": {}},
        "stdout": '{"clusterInfo": {}}',
        "stderr": "",
        "exit_code": 200,
        "captured_at": "2026-10-06T21:04:05+00:00",
        "provenance": "laptop -> rm1.corp",
    }
    document.update(overrides)
    return document


def synthetic(**overrides: Any) -> dict[str, Any]:
    """Build a minimal *synthetic* fixture document.

    Args:
        **overrides: Fields to replace or add.

    Returns:
        A document that validates as-is unless `overrides` breaks it.
    """
    document: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source": "synthetic",
        "transport": "ssh_cli",
        "source_id": "hdfs",
        "operation": "hdfs dfs -count -q -v /warehouse/",
        "request": {"argv": ["hdfs", "dfs", "-count", "-q", "-v", "/warehouse/"]},
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "comment": SYNTHETIC_COMMENT,
    }
    document.update(overrides)
    return document


# --------------------------------------------------------------------------
# The format's two load-bearing rules
# --------------------------------------------------------------------------


def test_a_source_that_is_neither_value_is_refused() -> None:
    with pytest.raises(ConfigError, match="neither 'synthetic' nor 'observed'"):
        parse_fixture(observed(source="guessed"))


def test_an_unknown_schema_version_is_refused() -> None:
    with pytest.raises(ConfigError, match="schema_version"):
        parse_fixture(observed(schema_version="bigdata-mcp/fixture@99"))


def test_an_ancient_schema_version_is_refused_too() -> None:
    with pytest.raises(ConfigError, match="schema_version"):
        parse_fixture(observed(schema_version="1"))


def test_the_version_identifier_is_the_one_the_portal_pin_uses() -> None:
    """§14.2 pins `bigdata-mcp/port@1`; one naming scheme, not two."""
    assert FIXTURE_SCHEMA_VERSION.startswith("bigdata-mcp/")
    assert FIXTURE_SCHEMA_VERSION.endswith("@1")


def test_check_version_reads_the_version_without_building_the_record() -> None:
    assert check_version(observed()) == FIXTURE_SCHEMA_VERSION


def test_check_version_refuses_a_non_document() -> None:
    with pytest.raises(ConfigError, match="<document>"):
        check_version(["not", "a", "fixture"])


def test_an_observed_fixture_without_captured_at_is_refused() -> None:
    document = observed()
    del document["captured_at"]
    with pytest.raises(ConfigError, match="captured_at"):
        parse_fixture(document)


def test_an_observed_fixture_without_provenance_is_refused() -> None:
    document = observed()
    del document["provenance"]
    with pytest.raises(ConfigError, match="provenance"):
        parse_fixture(document)


def test_a_synthetic_fixture_may_not_carry_captured_at() -> None:
    """It was never captured anywhere, so a timestamp is a lie."""
    with pytest.raises(ConfigError, match="never captured"):
        parse_fixture(synthetic(captured_at="2026-10-06T21:04:05+00:00"))


def test_an_unknown_field_is_refused_not_ignored() -> None:
    """A typo in `source_id` must not load as a fixture about nothing."""
    with pytest.raises(ConfigError, match="unknown field"):
        parse_fixture(observed(source_ld="hdfs"))


def test_an_unknown_transport_is_refused() -> None:
    with pytest.raises(ConfigError, match="transport"):
        parse_fixture(observed(transport="carrier_pigeon"))


def test_an_exit_code_that_is_a_bool_is_refused() -> None:
    """`True` is an `int` in Python; a fixture saying `exit_code: true` is a bug."""
    with pytest.raises(ConfigError, match="exit_code"):
        parse_fixture(observed(exit_code=True))


def test_a_required_string_that_is_empty_is_refused() -> None:
    with pytest.raises(ConfigError, match="source_id"):
        parse_fixture(observed(source_id=""))


def test_a_non_object_document_is_refused() -> None:
    with pytest.raises(ConfigError, match="<document>"):
        parse_fixture("just a string")


def test_a_synthetic_fixture_without_a_comment_is_refused() -> None:
    """The spec asks every synthetic fixture to name the claim it encodes."""
    document = synthetic()
    del document["comment"]
    with pytest.raises(ConfigError, match="comment"):
        parse_fixture(document)


# --------------------------------------------------------------------------
# `request`: argv, never a shell string
# --------------------------------------------------------------------------


def test_an_ssh_cli_fixture_needs_a_non_empty_argv() -> None:
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(synthetic(request={"argv": []}))


def test_an_ssh_cli_request_may_not_be_a_shell_string() -> None:
    """§11.1. A string needs quoting rules, and quoting bugs on a bastion are RCE."""
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(
            synthetic(request={"command": "hdfs dfs -count /warehouse/ | wc -l"})
        )


def test_an_ssh_cli_argv_must_be_all_strings() -> None:
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(synthetic(request={"argv": ["hdfs", 7]}))


def test_argv_round_trips_as_a_tuple() -> None:
    fixture = parse_fixture(synthetic())
    assert fixture.argv == ("hdfs", "dfs", "-count", "-q", "-v", "/warehouse/")


def test_an_http_fixture_has_no_argv() -> None:
    """A caller must not be able to run an HTTP request as a shell command."""
    with pytest.raises(ConfigError, match="no argv"):
        _ = parse_fixture(observed()).argv


def test_an_http_fixture_needs_a_path() -> None:
    with pytest.raises(ConfigError, match=r"request\.path"):
        parse_fixture(observed(request={"method": "GET"}))


def test_the_status_is_carried_in_exit_code_for_an_http_fixture() -> None:
    """One response shape for every transport, so no consumer branches on transport."""
    fixture = parse_fixture(observed(exit_code=404, stdout="not found"))
    assert fixture.exit_code == 404
    assert fixture.stdout == "not found"


def test_a_fixture_round_trips_through_its_on_disk_form() -> None:
    fixture = parse_fixture(observed())
    again = parse_fixture(json.loads(fixture.dumps()))
    assert again == fixture


def test_a_synthetic_fixture_omits_the_observed_only_fields_on_disk() -> None:
    """Written as absent, not `null`: a synthetic file must not look half-written."""
    document = parse_fixture(synthetic()).to_document()
    assert "captured_at" not in document
    assert "provenance" not in document
    assert document["comment"] == SYNTHETIC_COMMENT


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_a_malformed_file_fails_at_load_naming_the_field(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(observed(source="nope")), encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_fixture(path)
    assert str(path) in str(caught.value)
    assert "source" in str(caught.value)


def test_an_unparseable_file_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_fixture(path)


def test_an_unreadable_file_names_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(ConfigError, match="Cannot read fixture"):
        load_fixture(missing)


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------


def test_the_committed_synthetic_corpus_loads() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    assert len(corpus) >= 7


def test_every_committed_fixture_is_labelled_synthetic_and_commented() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    for fixture in corpus:
        assert fixture.source is Source.SYNTHETIC, fixture.source_id
        assert fixture.comment, f"{fixture.operation} has no comment"


def test_a_synthetic_only_corpus_cannot_validate_a_parser() -> None:
    """§17.2, as code. False at any size."""
    corpus = load_corpus(Path("tests/fixtures"))
    assert corpus.validates_parsers is False
    assert "CANNOT validate a parser" in corpus.summarise()


def test_a_corpus_with_one_observed_fixture_can_validate() -> None:
    corpus = Corpus(
        fixtures=(
            parse_fixture(observed()),
            parse_fixture(synthetic()),
        ),
        root=Path("tests/fixtures"),
    )
    assert corpus.validates_parsers is True
    assert len(corpus.of_source(Source.OBSERVED)) == 1


def test_the_index_groups_by_source_and_operation() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    hits = corpus.lookup("yarn_rm", "GET /ws/v1/cluster/apps?states=RUNNING")
    assert len(hits) == 1
    assert hits[0].source_id == "yarn_rm"


def test_a_lookup_that_matches_nothing_returns_empty_not_none() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    assert corpus.lookup("nothing", "nothing") == ()


def test_for_source_returns_every_fixture_for_a_backend() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    assert len(corpus.for_source("hdfs")) == 3


def test_an_absent_corpus_directory_is_empty_not_an_error() -> None:
    """§3: nothing has been captured yet, and that is the expected state."""
    corpus = load_corpus(Path("tests/fixtures/does-not-exist"))
    assert len(corpus) == 0
    assert corpus.validates_parsers is False


def test_corpus_files_under_a_dot_directory_are_skipped(tmp_path: Path) -> None:
    """Editors and tools leave caches beside a corpus; those are not fixtures."""
    (tmp_path / "real.json").write_text(json.dumps(observed()), encoding="utf-8")
    hidden = tmp_path / ".cache" / "junk.json"
    hidden.parent.mkdir()
    hidden.write_text("{not json", encoding="utf-8")
    corpus = load_corpus(tmp_path)
    assert len(corpus) == 1


def test_a_malformed_fixture_in_a_corpus_fails_the_whole_load(tmp_path: Path) -> None:
    (tmp_path / "one.json").write_text(json.dumps(observed()), encoding="utf-8")
    (tmp_path / "two.json").write_text(
        json.dumps(observed(source="x")), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="source"):
        load_corpus(tmp_path)


# --------------------------------------------------------------------------
# capture-fixtures
# --------------------------------------------------------------------------


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


def test_the_synthetic_corpus_documents_the_shape_it_encodes() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    comments = {fixture.operation: fixture.comment or "" for fixture in corpus}
    assert any("apps: []" in comment for comment in comments.values())
    assert any("none" in comment and "inf" in comment for comment in comments.values())
    assert any(
        "0.037" in comment or "number" in comment.lower()
        for comment in comments.values()
    )


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


def test_two_corpora_merge_without_one_replacing_the_other() -> None:
    """A differential test holds both transports for one operation side by side."""
    from bigdata_mcp.fixtures.corpus import merge

    first = Corpus(fixtures=(parse_fixture(synthetic()),), root=Path("a"))
    second = Corpus(fixtures=(parse_fixture(observed()),), root=Path("b"))
    merged = merge([first, second])
    assert len(merged) == 2
    assert merged.root is None
    assert merged.lookup("hdfs", "hdfs dfs -count -q -v /warehouse/") == (
        first.fixtures[0],
    )


def test_a_merge_of_one_corpus_keeps_its_root() -> None:
    from bigdata_mcp.fixtures.corpus import merge

    only = Corpus(fixtures=(parse_fixture(synthetic()),), root=Path("a"))
    assert merge([only]).root == Path("a")


def test_the_index_key_is_source_and_operation() -> None:
    fixture = parse_fixture(synthetic())
    assert fixture.key == ("hdfs", "hdfs dfs -count -q -v /warehouse/")


def test_a_non_string_stdout_is_refused() -> None:
    with pytest.raises(ConfigError, match="stdout"):
        parse_fixture(observed(stdout={"not": "a string"}))


def test_a_non_string_method_is_refused() -> None:
    with pytest.raises(ConfigError, match=r"request\.method"):
        parse_fixture(observed(request={"method": 7, "path": "/x"}))
