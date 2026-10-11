"""Tests for the fixture corpus: what loads, what is indexed, what it claims.

Split from `test_fixtures.py` for the reason this repository splits any test
file: that one crossed Codacy's 500-line limit, and the corpus tests are the half
that argues with `corpus.py` rather than with a single fixture's fields. Keeping
them in one place is what stops a corpus-level regression from being read as a
field-level one.

The loader is given an absolute path resolved from this file, never the working
directory — `load_corpus` treats an absent directory as an empty corpus with no
error, so a relative path here would make every test below pass while asserting
nothing whenever pytest is invoked from somewhere other than the repository root.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures import Source
from bigdata_mcp.fixtures.corpus import DEFAULT_CORPUS_DIR
from bigdata_mcp.fixtures.corpus import Corpus
from bigdata_mcp.fixtures.corpus import load_corpus
from bigdata_mcp.fixtures.fields import parse_fixture
from tests.support.fixture_docs import observed
from tests.support.fixture_docs import synthetic

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures"


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
    corpus = load_corpus(CORPUS_DIR)
    assert len(corpus) >= 7


def test_every_committed_fixture_is_labelled_synthetic_and_commented() -> None:
    """Every file has to say it is synthetic, and what claim it encodes.

    A committed synthetic fixture does not validate a parser, so the label is
    what stops a later reader treating one as evidence about the real endpoints,
    and the comment is the only place the claim it does encode is written down.
    Walking all of them is what stops one hand-edited file from quietly becoming
    unlabelled while the rest stay correct.
    """
    corpus = load_corpus(CORPUS_DIR)
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
    corpus = load_corpus(CORPUS_DIR)
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
    corpus = load_corpus(CORPUS_DIR)
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
    corpus = load_corpus(CORPUS_DIR)
    assert corpus.lookup("nothing", "nothing") == ()


def test_for_source_returns_every_fixture_for_a_backend() -> None:
    """`for_source` scans the whole corpus by `source_id`, ignoring the index.

    That makes it the "what does this backend have" accessor a differential test
    consults before deciding it holds both halves of a comparison. The count is
    exact rather than a floor, so a new HDFS fixture surfaces here as the visible
    edit it should be.
    """
    corpus = load_corpus(CORPUS_DIR)
    assert len(corpus.for_source("hdfs")) == 3


def test_the_default_corpus_fails_loudly_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one path that must exist is the one this checkout carries.

    An *explicitly named* directory that is not there stays an empty corpus,
    because "nothing captured yet" is the expected starting state. The default is
    different: it is this repository's own corpus, and an empty result with no
    error is indistinguishable from having loaded it — which is how a suite ends
    up asserting nothing for a whole run.

    The default is redirected at a directory that cannot exist, because the
    real one is asserted by the tests around it and this is about what happens
    when it does *not*. That the refusal *also* tells a caller what to do about
    it is asserted separately, since it is a different claim.
    """
    monkeypatch.setattr(
        "bigdata_mcp.fixtures.corpus.DEFAULT_CORPUS_DIR", CORPUS_DIR / "absent"
    )

    with pytest.raises(FileNotFoundError, match="default fixture corpus"):
        load_corpus()


def test_the_default_corpus_is_a_checkout_path_not_a_packaged_one() -> None:
    """The default resolves inside this checkout, which no wheel contains.

    `DEFAULT_CORPUS_DIR` is `Path(__file__)... / "tests" / "fixtures"`, and the
    wheel built from this project carries `src/` alone — verified: it holds
    `bigdata_mcp/fixtures/corpus.py` and zero `tests/` entries. So the default
    only ever resolves for someone running from a clone, and every other caller
    has to name a corpus.

    That is a deliberate default rather than a bug, which is why it is pinned
    here: the two ways to "fix" the loader are to package the corpus or to
    point the default somewhere else, and either one is a decision about
    distribution that belongs to a spec, not to a message that happens to be
    wrong. Turning the tail into a different path is what makes this fail.
    """
    assert DEFAULT_CORPUS_DIR.parts[-2:] == ("tests", "fixtures")


def test_the_default_corpus_refusal_says_what_to_do_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal names the cause and the remedy, not just the missing path.

    An absent default has exactly two explanations to a caller holding this
    error: the clone is incomplete, or there is no clone because the package
    was installed. The first is not actionable from here; the second is, and
    `directory=` is how. Reporting only the path leaves the second caller to
    guess that a default they never chose is the thing standing between them
    and a working loader.

    The message is matched rather than quoted because the remedy is the
    contract: the path it prints and the wording around it are free to change,
    the two clauses this asserts on are not.
    """
    monkeypatch.setattr(
        "bigdata_mcp.fixtures.corpus.DEFAULT_CORPUS_DIR", CORPUS_DIR / "absent"
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        load_corpus()

    message = str(excinfo.value)
    assert str(CORPUS_DIR / "absent") in message
    assert re.search(r"checkout|not (?:shipped|installed)|no clone", message)
    assert re.search(r"directory\s*=", message)


def test_a_named_directory_that_is_absent_is_still_an_empty_corpus() -> None:
    """§3: nothing captured yet is the expected starting state, not an error."""
    corpus = load_corpus(CORPUS_DIR / "does-not-exist")
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
