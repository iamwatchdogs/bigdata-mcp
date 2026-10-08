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
* F12 classify fixtures by every component of the path, root included ->
  `test_a_corpus_under_a_dot_directory_still_loads`
* F12 drop the `--out` requirement ->
  `test_capture_requires_an_output_path`
* F13 compare only the version's `@N` suffix ->
  `test_an_ancient_schema_version_is_refused_too`
* F14 drop `provenance` from the on-disk form ->
  `test_a_fixture_round_trips_through_its_on_disk_form`
* F15 return `None` from a `lookup` that missed ->
  `test_a_lookup_that_matches_nothing_returns_empty_not_none`
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
    """Refuse rather than default, because this is the field a reader trusts.

    `source` says whether the bytes came off a real endpoint or were written
    here, and §17.2 makes that the difference between a corpus that can validate
    a parser and one that cannot. A default would answer that question on the
    author's behalf, so the message names both legal values and says what the
    field is for.
    """
    with pytest.raises(ConfigError, match="neither 'synthetic' nor 'observed'"):
        parse_fixture(observed(source="guessed"))


def test_an_unknown_schema_version_is_refused() -> None:
    """An unrecognised version is refused, never parsed on a best effort.

    The match is against an exact string, the same discipline §14.2 pins for the
    portal spec's `schema = "bigdata-mcp/port@1"`. A `@2` that adds a field, or
    changes what an existing one means, would otherwise be read with `@1`'s
    rules and produce a parse that looks right and is not.
    """
    with pytest.raises(ConfigError, match="schema_version"):
        parse_fixture(observed(schema_version="bigdata-mcp/fixture@99"))


def test_an_ancient_schema_version_is_refused_too() -> None:
    """No part of the version is interpreted: not the namespace, not the number.

    "1" has no `@2` suffix to compare against and no prefix to strip, so it is
    the value that catches a loader which helpfully parses the version instead of
    matching it — such a loader would accept a bare number from some other
    format's file and call it one of ours. F13 in the module docstring.
    """
    with pytest.raises(ConfigError, match="schema_version"):
        parse_fixture(observed(schema_version="1"))


def test_the_version_identifier_is_the_one_the_portal_pin_uses() -> None:
    """§14.2 pins `bigdata-mcp/port@1`; one naming scheme, not two."""
    assert FIXTURE_SCHEMA_VERSION.startswith("bigdata-mcp/")
    assert FIXTURE_SCHEMA_VERSION.endswith("@1")


def test_check_version_reads_the_version_without_building_the_record() -> None:
    """The cheap path answers one question: is this a version I understand.

    `check_version` exists so a loader deciding whether it can read a file does
    not have to build a whole `Fixture` to read one field, so it validates the
    version and nothing else. A document whose `source` is nonsense passes here
    and is refused by `parse_fixture`, and that asymmetry is the point: the two
    entry points answer two different questions.
    """
    assert check_version(observed()) == FIXTURE_SCHEMA_VERSION


def test_check_version_refuses_a_non_document() -> None:
    """The shape gate runs before any field is looked up, so it names the value.

    The label is `<document>` rather than a field because there is no field to
    blame — the whole value is the wrong shape — and a message naming a field
    would send the reader to fix something that was never wrong.
    """
    with pytest.raises(ConfigError, match="<document>"):
        check_version(["not", "a", "fixture"])


def test_an_observed_fixture_without_captured_at_is_refused() -> None:
    """When an observed fixture was captured is what makes it auditable later.

    `captured_at` is required for `observed` and forbidden for `synthetic`,
    because a fixture nobody captured has no timestamp to carry. What is checked
    is presence and non-emptiness; the RFC 3339 shape the message asks for is
    the operator's to honour.
    """
    document = observed()
    del document["captured_at"]
    with pytest.raises(ConfigError, match="captured_at"):
        parse_fixture(document)


def test_an_observed_fixture_without_provenance_is_refused() -> None:
    """Provenance says who ran it and from where, which a timestamp cannot.

    It is what lets someone who was not on the machine re-take the capture months
    later and see whether the estate has moved under the fixture. An observed
    fixture without it is a claim with no way left to check it.
    """
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
    """The transport decides the shape of `request`, so it cannot be open-ended.

    `ssh_cli` wants an argv list, the HTTP family wants a `path`, and
    `mcp_client` wants neither — a relayed `tools/call` has no URL to record. An
    unrecognised transport would leave the request with no shape to check against,
    so the refusal lists the four families rather than shrugging.
    """
    with pytest.raises(ConfigError, match="transport"):
        parse_fixture(observed(transport="carrier_pigeon"))


def test_an_exit_code_that_is_a_bool_is_refused() -> None:
    """`True` is an `int` in Python; a fixture saying `exit_code: true` is a bug."""
    with pytest.raises(ConfigError, match="exit_code"):
        parse_fixture(observed(exit_code=True))


def test_a_required_string_that_is_empty_is_refused() -> None:
    """Empty is refused separately from non-string, and `source_id` is half the key.

    `source_id` is one half of the `(source_id, operation)` index key, so an
    empty one yields a fixture that loads cleanly and is then unreachable through
    `lookup` — a fixture about nothing, which is what refusing the unknown-field
    typo above exists to prevent. Neither is worth guessing at.
    """
    with pytest.raises(ConfigError, match="source_id"):
        parse_fixture(observed(source_id=""))


def test_a_non_object_document_is_refused() -> None:
    """Nothing downstream reaches into a mapping, so the shape is settled once.

    A committed `.json` file can perfectly well hold a list or a string, and
    every field check after this one indexes or `.get`s a dict. `_object` is the
    single place that establishes the shape, which is why the refusal is
    `<document>` again: there is no field to name.
    """
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
    """An empty argv describes a command that never ran.

    `Fixture.argv` is what a consumer builds an executor call from, so `[]`
    would spawn nothing and report success — a fixture that proves nothing while
    looking exactly like a capture. The refusal repeats §11.1's reason for argv
    rather than a shell string, because this is the other half of that rule.
    """
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(synthetic(request={"argv": []}))


def test_an_ssh_cli_request_may_not_be_a_shell_string() -> None:
    """§11.1. A string needs quoting rules, and quoting bugs on a bastion are RCE."""
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(
            synthetic(request={"command": "hdfs dfs -count /warehouse/ | wc -l"})
        )


def test_an_ssh_cli_argv_must_be_all_strings() -> None:
    """argv elements are refused here, before anything downstream can render them.

    An executor joins argv into a command line, and `str(7)` would become the
    argument `7` rather than a number that was never meant to be a command. A
    JSON document is one copy-paste away from producing exactly that, and
    `Fixture.argv` coerces every element with `str()` on its way out — so this
    refusal is the only place the mistake is visible.
    """
    with pytest.raises(ConfigError, match=r"request\.argv"):
        parse_fixture(synthetic(request={"argv": ["hdfs", 7]}))


def test_argv_round_trips_as_a_tuple() -> None:
    """`argv` is the request's list, not the `operation` string beside it.

    In the committed fixtures the two carry the same text, which is precisely why
    a consumer has to be able to tell them apart: the index is keyed on the
    string and the executor call is built from the list. It comes back as a tuple
    so a caller cannot reach through it into the frozen record.
    """
    fixture = parse_fixture(synthetic())
    assert fixture.argv == ("hdfs", "dfs", "-count", "-q", "-v", "/warehouse/")


def test_an_http_fixture_has_no_argv() -> None:
    """A caller must not be able to run an HTTP request as a shell command."""
    with pytest.raises(ConfigError, match="no argv"):
        _ = parse_fixture(observed()).argv


def test_an_http_fixture_needs_a_path() -> None:
    """`path` is required and `method` is not, because only one has a default.

    §14.2 treats the relative path as the SSRF boundary, so it is the field that
    identifies which endpoint answered and the one a replay needs. A missing
    method has one obvious meaning, `GET`, while a missing path has none — so one
    is checked for presence and the other only for type.
    """
    with pytest.raises(ConfigError, match=r"request\.path"):
        parse_fixture(observed(request={"method": "GET"}))


def test_the_status_is_carried_in_exit_code_for_an_http_fixture() -> None:
    """One response shape for every transport, so no consumer branches on transport."""
    fixture = parse_fixture(observed(exit_code=404, stdout="not found"))
    assert fixture.exit_code == 404
    assert fixture.stdout == "not found"


def test_a_fixture_round_trips_through_its_on_disk_form() -> None:
    """What `capture-fixtures` writes must read back as the record it came from.

    The corpus is written by `dumps()` and read by `load_fixture`, so a field the
    writer drops is a field the corpus can never carry, and the loss is silent
    until somebody needs it. Equality is over the whole frozen record, enums
    included. F14 in the module docstring dropped `provenance` from the output and
    turned this red on the refusal for the field that went missing.
    """
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
    """Both halves of the message are load-bearing: which file, and which field.

    A corpus is a directory of many files, so a refusal naming `source` alone
    sends the reader hunting through all of them, and one naming the path alone
    tells them nothing about what to change. `load_fixture` threads the path
    through as the origin so a single message carries both.
    """
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(observed(source="nope")), encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_fixture(path)
    assert str(path) in str(caught.value)
    assert "source" in str(caught.value)


def test_an_unparseable_file_names_the_file(tmp_path: Path) -> None:
    """A JSON syntax error carries its own line and column; the filename is ours.

    `json.loads` reports where it stopped and nothing about which of the committed
    fixtures was open. What a maintainer sees is "the corpus did not load", and
    this is the one message in the chain that can say where to look.
    """
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_fixture(path)


def test_an_unreadable_file_names_the_file(tmp_path: Path) -> None:
    """An `OSError` is wrapped, because §8.2 forbids a traceback reaching a caller.

    `rglob` can hand the loader a path removed between listing and reading, or one
    the process cannot open, and an unwrapped `OSError` out of a corpus load is
    exactly the traceback the spec says must never surface. The path goes in the
    message so the operator knows which capture to re-take.
    """
    missing = tmp_path / "absent.json"
    with pytest.raises(ConfigError, match="Cannot read fixture"):
        load_fixture(missing)


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------


def test_the_committed_synthetic_corpus_loads() -> None:
    """The committed corpus is validated by being loaded, on every run.

    §17.2 forbids any test from reaching the estate, so these files are the only
    fixture evidence CI has and a malformed one has to fail here or nowhere. The
    bound is a floor, so capturing another fixture is not also a test edit — what
    each file claims is the next test's business.
    """
    corpus = load_corpus(Path("tests/fixtures"))
    assert len(corpus) >= 7


def test_every_committed_fixture_is_labelled_synthetic_and_commented() -> None:
    """Every file has to say it is synthetic, and what claim it encodes.

    A committed synthetic fixture does not validate a parser, so the label is
    what stops a later reader treating one as evidence about the real endpoints,
    and the comment is the only place the claim it does encode is written down.
    Walking all of them is what stops one hand-edited file from quietly becoming
    unlabelled while the rest stay correct.
    """
    corpus = load_corpus(Path("tests/fixtures"))
    for fixture in corpus:
        assert fixture.source is Source.SYNTHETIC, fixture.source_id
        assert fixture.comment, f"{fixture.operation} has no comment"


def test_a_corpus_under_a_dot_directory_still_loads(tmp_path: Path) -> None:
    """The root's own components are not the root's contents.

    `_is_hidden` walked `path.parts`, which includes the components of the corpus
    root itself. A corpus anywhere under a dot-directory — `~/.local/…`, `.venv/…`,
    a checkout unpacked under `~/.cache` — therefore had every fixture classified
    as hidden and `load_corpus` returned an empty corpus with no error.

    The failure is silent and it is the worst kind: an empty corpus is exactly what
    a project that has captured nothing yet returns, so a corpus that was present
    and loaded fine would be indistinguishable from one that was never there.

    The hidden directory below the root is still skipped, which is the half of the
    behaviour that was already right and is why the bug hid.
    """
    hidden_root = tmp_path / ".cache" / "corpus"
    (hidden_root / ".ipynb_checkpoints").mkdir(parents=True)
    (hidden_root / "visible.json").write_text(json.dumps(synthetic()), encoding="utf-8")
    (hidden_root / ".ipynb_checkpoints" / "copy.json").write_text(
        json.dumps(synthetic()), encoding="utf-8"
    )

    corpus = load_corpus(hidden_root)

    assert len(corpus) == 1, "the corpus under a dot-directory loaded as empty"
    assert corpus.root == hidden_root


def test_a_synthetic_only_corpus_cannot_validate_a_parser() -> None:
    """§17.2, as code. False at any size."""
    corpus = load_corpus(Path("tests/fixtures"))
    assert corpus.validates_parsers is False
    assert "CANNOT validate a parser" in corpus.summarise()


def test_a_corpus_with_one_observed_fixture_can_validate() -> None:
    """One observed fixture is enough; zero never is, at any size.

    `validates_parsers` is `any`, so the flag answers "is anything here from a
    real endpoint" and says nothing about how much. That is deliberate but
    coarse, which is why `summarise()` prints both counts next to it — the nuance
    belongs in the line a failure transcript shows, not in the boolean. Pinning
    the direction is what stops the flag quietly becoming a majority rule.
    """
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
    """The query string lives in `operation`, so one endpoint can hold two keys.

    `yarn-apps-empty` and `yarn-apps-progress-is-numeric` both call
    `/ws/v1/cluster/apps`, and only the query in `operation` separates them.
    Keying on the path would merge them, and a differential test over the merged
    pair would compare two different calls and report the gap as a parser
    disagreement.
    """
    corpus = load_corpus(Path("tests/fixtures"))
    hits = corpus.lookup("yarn_rm", "GET /ws/v1/cluster/apps?states=RUNNING")
    assert len(hits) == 1
    assert hits[0].source_id == "yarn_rm"


def test_a_lookup_that_matches_nothing_returns_empty_not_none() -> None:
    """A miss is an answer, so it is an empty tuple and not `None`.

    The two are different claims: an empty tuple says no fixture records this
    call, and `None` says the caller cannot tell — which is also exactly what a
    missing index looks like. The empty tuple iterates and compares as it stands,
    so no caller needs a guard that would also hide the broken-index case.
    """
    corpus = load_corpus(Path("tests/fixtures"))
    assert corpus.lookup("nothing", "nothing") == ()


def test_for_source_returns_every_fixture_for_a_backend() -> None:
    """`for_source` scans the whole corpus by `source_id`, ignoring the index.

    That makes it the "what does this backend have" accessor a differential test
    consults before deciding it holds both halves of a comparison. The count is
    exact rather than a floor, so a new HDFS fixture surfaces here as the visible
    edit it should be.
    """
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
    """One bad file fails the load rather than quietly leaving the corpus short.

    Skipping it would let a differential test compare one real capture against
    nothing and report the missing half as a parser disagreement — the silent
    wrong answer §17.2 calls the worst outcome. The refusal names the offending
    field and, through the origin, the file.
    """
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
    """The comments are the corpus's documentation, so they are asserted on too.

    The format requires a comment on every synthetic fixture precisely because
    the file is the only place the claim survives, and this pins the three shapes
    the corpus exists for: an empty list where `null` is the reflex, the `none`
    and `inf` literals `-count` emits for an empty prefix, and a fractional
    `progress` a percent parser gets wrong. What it catches is a comment
    rewritten until it stops naming the claim it was written to name.
    """
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
    """`root` survives only when one input had one, and one input is not a merge.

    `summarise()` prints the root, so carrying a single input's directory into a
    merged corpus would put a path into a failure transcript that does not
    describe where those fixtures came from. Merging one corpus is the no-op
    case and should look like one.
    """
    only = Corpus(fixtures=(parse_fixture(synthetic()),), root=Path("a"))
    assert merge([only]).root == Path("a")


def test_the_index_key_is_source_and_operation() -> None:
    """The key is the pair, as a tuple, and never a joined string.

    Backend and invocation are the two halves a differential test asks about, and
    neither is the transport: one operation recorded over SSH and over HTTPS is
    meant to land under a single key so that both come back. A tuple also keeps
    `("a b", "c")` from colliding with `("a", "b c")`, which a joined string
    with any separator between them would not.
    """
    fixture = parse_fixture(synthetic())
    assert fixture.key == ("hdfs", "hdfs dfs -count -q -v /warehouse/")


def test_a_non_string_stdout_is_refused() -> None:
    """The recorded body is the only thing a parser is ever tested against.

    Loaded as an object, every consumer downstream receives a mapping where it
    expects text, so the failure surfaces as a parser error three layers away
    instead of as a bad fixture at load. `stderr` goes through the same check and
    has no test of its own, because that is one call serving two fields.
    """
    with pytest.raises(ConfigError, match="stdout"):
        parse_fixture(observed(stdout={"not": "a string"}))


def test_a_non_string_method_is_refused() -> None:
    """The method is checked for shape only, never against an allowlist.

    §14.2 constrains `method` inside the portal *spec*, and only under
    `mode = "read_write"`, which is what makes read-only a property of the schema
    rather than a runtime check. A fixture is a record of what happened, so
    refusing `POST` here would make a read-write portal's calls unrecordable, and
    that is not this loader's decision to make.
    """
    with pytest.raises(ConfigError, match=r"request\.method"):
        parse_fixture(observed(request={"method": 7, "path": "/x"}))
