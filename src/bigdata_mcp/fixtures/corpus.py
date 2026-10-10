"""Loading, indexing, and accounting for a fixture corpus.

A corpus is a directory of `.json` fixtures. Two operations matter and they are
not symmetric:

- **Loading** is strict and eager. Every file is validated when the corpus loads,
  not when a fixture is first used, so a malformed corpus fails at startup instead
  of producing a wrong parse three layers down. §8.1 names a confidently wrong
  answer as the worst outcome, and a lazily-validated corpus is how you get one.
- **Reporting** is the other half. A corpus that mixes observed and synthetic
  fixtures must be able to say so in one line, because "this parser passes against
  the corpus" means something completely different when every fixture in it was
  synthesised here. `Corpus.validates_parsers` exists for exactly that sentence,
  and it is `False` for a synthetic-only corpus no matter how large it is.

Indexing is by `(source_id, operation)` because that is how a differential test
asks its question — "what did `hdfs dfs -count` return over SSH, and what did the
native path return?" — and answering it by scanning would mean the test itself
could be the slow thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING

from bigdata_mcp.fixtures.fields import load_fixture
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Iterator

DEFAULT_CORPUS_DIR = Path("tests/fixtures")


@dataclass(frozen=True, slots=True)
class Corpus:
    """A loaded set of fixtures, indexed for lookup.

    Attributes:
        fixtures: Every fixture, in a stable order — sorted by path — so two runs
            over the same directory agree.
        root: Where it was loaded from, for error messages and for `doctor`.
        by_key: The `(source_id, operation)` index.
    """

    fixtures: tuple[Fixture, ...] = ()
    root: Path | None = None
    by_key: dict[tuple[str, str], tuple[Fixture, ...]] = field(default_factory=dict)

    def __len__(self) -> int:
        """The number of fixtures.

        Returns:
            `len(self.fixtures)`.
        """
        return len(self.fixtures)

    def __iter__(self) -> Iterator[Fixture]:
        """Iterate the fixtures in order.

        Returns:
            An iterator over `fixtures`.
        """
        return iter(self.fixtures)

    def lookup(self, source_id: str, operation: str) -> tuple[Fixture, ...]:
        """Every fixture for one `(source_id, operation)` pair.

        Args:
            source_id: The logical backend, e.g. `hdfs`.
            operation: The invocation, e.g. `hdfs dfs -count -q -v`.

        Returns:
            The matching fixtures, possibly several — the same operation captured
            over two transports is exactly what a differential test needs. Never
            `None`, so a caller does not have to distinguish "no match" from
            "the index was never built".
        """
        return self.by_key.get((source_id, operation), ())

    def for_source(self, source_id: str) -> tuple[Fixture, ...]:
        """Every fixture for one backend.

        Args:
            source_id: The logical backend.

        Returns:
            The matching fixtures.
        """
        return tuple(item for item in self.fixtures if item.source_id == source_id)

    def of_source(self, source: Source) -> tuple[Fixture, ...]:
        """Every fixture with a given provenance class.

        Args:
            source: `OBSERVED` or `SYNTHETIC`.

        Returns:
            The matching fixtures.
        """
        return tuple(item for item in self.fixtures if item.source is source)

    @property
    def validates_parsers(self) -> bool:
        """Whether this corpus can validate a parser against the estate.

        `False` for a synthetic-only corpus, and it stays `False` at any size. §17.2
        is explicit that a parser proven only against synthetic data has not been
        proven, and this property is that sentence as code, so a test report can
        print it rather than assert it in prose.
        """
        return any(item.is_observed for item in self.fixtures)

    def summarise(self) -> str:
        """One line describing what this corpus can and cannot prove.

        Returns:
            A sentence naming both counts and the consequence. Written for a
            failure transcript, so it says the useful thing rather than the
            complete thing.
        """
        observed = len(self.of_source(Source.OBSERVED))
        synthetic = len(self.of_source(Source.SYNTHETIC))
        verdict = (
            "can validate a parser against the estate"
            if self.validates_parsers
            else "CANNOT validate a parser: every fixture is synthetic (§17.2)"
        )
        return (
            f"{len(self)} fixture(s) in {self.root}: "
            f"{observed} observed, {synthetic} synthetic — {verdict}"
        )


def load_corpus(directory: Path | None = None) -> Corpus:
    """Load every fixture in a directory.

    Args:
        directory: The corpus root. Defaults to `tests/fixtures`, which holds the
            committed synthetic corpus.

    Returns:
        The indexed corpus. An absent directory is an empty corpus rather than a
        failure, because a project that has captured nothing yet is the expected
        starting state (§3: the estate is unreachable from here).

    A malformed fixture refuses the load with a message naming the file and the
    field, because validating on load is the entire point of doing it here rather
    than at first use.
    """
    root = directory if directory is not None else DEFAULT_CORPUS_DIR
    if not root.is_dir():
        return Corpus(fixtures=(), root=root)

    fixtures = [
        load_fixture(path)
        for path in sorted(root.rglob("*.json"))
        if not _is_hidden(path, root)
    ]
    return Corpus(
        fixtures=tuple(fixtures),
        root=root,
        by_key=_index(fixtures),
    )


def merge(corpora: Iterable[Corpus]) -> Corpus:
    """Combine several corpora into one, preserving order.

    Args:
        corpora: The corpora to combine, in priority order. Later duplicates of
            the same `(source_id, operation)` key append rather than replace, so a
            differential test can hold both transports side by side.

    Returns:
        The merged, re-indexed corpus. `root` survives only when exactly one input
        had one, because a merged corpus has no single directory behind it.
    """
    fixtures = [item for corpus in corpora for item in corpus.fixtures]
    roots = [corpus.root for corpus in corpora if corpus.root is not None]
    return Corpus(
        fixtures=tuple(fixtures),
        root=roots[0] if len(roots) == 1 else None,
        by_key=_index(fixtures),
    )


def _index(fixtures: Iterable[Fixture]) -> dict[tuple[str, str], tuple[Fixture, ...]]:
    """Build the `(source_id, operation)` index.

    Args:
        fixtures: The fixtures to index.

    Returns:
        A mapping from key to every fixture carrying that key, in input order.
    """
    buckets: dict[tuple[str, str], list[Fixture]] = {}
    for fixture in fixtures:
        buckets.setdefault(fixture.key, []).append(fixture)
    return {key: tuple(items) for key, items in buckets.items()}


def _is_hidden(path: Path, root: Path) -> bool:
    """Whether a path sits under a dot-directory *below* the corpus root.

    Editors and `uv` leave caches beside a corpus; a fixture loader that read those
    would report a corpus failure caused by an unrelated tool. So only the
    components between the root and the file are inspected.

    The components of `root` itself are not. Checking those means a corpus anywhere
    under a dot-directory — `~/.local/…`, `.venv/…`, a checkout unpacked under
    `~/.cache` — has every one of its fixtures classified as hidden, and
    `load_corpus` returns an empty corpus with no error at all. A strict loader that
    silently loads nothing is worse than a permissive one, because the empty result
    is indistinguishable from a project that has captured nothing yet.

    Args:
        path: The candidate fixture path.
        root: The corpus root the path was found under.

    Returns:
        True when any part strictly below `root` starts with a dot.
    """
    return any(part.startswith(".") for part in path.relative_to(root).parts)
