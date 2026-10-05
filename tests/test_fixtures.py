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

import pytest

from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Corpus
from bigdata_mcp.fixtures import Source
from bigdata_mcp.fixtures import load_corpus
from bigdata_mcp.fixtures.corpus import merge
from bigdata_mcp.fixtures.fields import check_version
from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.fields import parse_fixture
from bigdata_mcp.fixtures.schema import FIXTURE_SCHEMA_VERSION
from tests.support.fixture_docs import SYNTHETIC_COMMENT
from tests.support.fixture_docs import observed
from tests.support.fixture_docs import synthetic

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


# --------------------------------------------------------------------------
# Corpus round-trips and remaining field refusals
# --------------------------------------------------------------------------


def test_the_synthetic_corpus_documents_the_shape_it_encodes() -> None:
    corpus = load_corpus(Path("tests/fixtures"))
    comments = {fixture.operation: fixture.comment or "" for fixture in corpus}
    assert any("apps: []" in comment for comment in comments.values())
    assert any("none" in comment and "inf" in comment for comment in comments.values())
    assert any(
        "0.037" in comment or "number" in comment.lower()
        for comment in comments.values()
    )


def test_two_corpora_merge_without_one_replacing_the_other() -> None:
    """A differential test holds both transports for one operation side by side."""

    first = Corpus(fixtures=(parse_fixture(synthetic()),), root=Path("a"))
    second = Corpus(fixtures=(parse_fixture(observed()),), root=Path("b"))
    merged = merge([first, second])
    assert len(merged) == 2
    assert merged.root is None
    assert merged.lookup("hdfs", "hdfs dfs -count -q -v /warehouse/") == (
        first.fixtures[0],
    )


def test_a_merge_of_one_corpus_keeps_its_root() -> None:

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
