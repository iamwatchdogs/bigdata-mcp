"""Contract tests for the offline-first foundation's load-bearing premises.

Two decisions are recorded here because both are the kind that decay silently.
Neither is a test of behaviour; each one guards a *document* against an edit that
would leave the code and the document disagreeing.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript):

* Delete the §3 constraint row ->
  `test_spec_records_the_development_environment_constraint` fails on the
  missing-literal assertion.
* Add a scheme-prefixed URL for `yarn.corp:8088` to any module under `tests/` ->
  `test_no_test_targets_a_non_local_host` fails, naming the file. (The
  scheme is spelled out in prose here rather than written literally, because this
  very module is subject to the rule.)
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Both roots. This module scans the suite for non-local URLs, and the suite now
#: spans two directories — scanning only `tests/` would leave every file under
#: `scripts/tests/` unchecked while still reporting the rule as enforced.
TEST_DIRS = (REPO_ROOT / "tests", REPO_ROOT / "scripts" / "tests")
SPEC_PATH = REPO_ROOT / "SPEC.md"

ESTATE_ACCESS_SENTINEL = "Development has no access to the target estate"
FIXTURE_OWNERSHIP_SENTINEL = "No test may require estate access"
LOCAL_HOST_TOKENS = frozenset({
    "localhost",
    "127.0.0.1",
    # Both spellings of the IPv6 loopback. `urlsplit().hostname` reports the
    # compressed form, so a test asserting on the long form would otherwise be
    # flagged as reaching a remote host.
    "::1",
    "0:0:0:0:0:0:0:1",
    "[::1]",
    "host.docker.internal",
})
RESERVED_TLDS = frozenset({".invalid", ".test", ".example", ".localhost"})


@cache
def _spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


@cache
def _environment_section() -> str:
    """Return §3 only, so a row cannot satisfy an assertion about §17.2."""
    lines = _spec_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 3. "))
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


@cache
def _testing_section() -> str:
    lines = _spec_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 17. "))
    end = next(
        (
            i
            for i, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_spec_records_the_development_environment_constraint() -> None:
    """§3 carries the constraint, and §17.2 carries the rule it produces."""
    assert ESTATE_ACCESS_SENTINEL in _environment_section(), (
        "SPEC.md §3 must record that development has no access to the target estate; "
        "every §18 open item depends on that being stated rather than remembered"
    )
    assert FIXTURE_OWNERSHIP_SENTINEL in _testing_section(), (
        "SPEC.md §17.2 must state that no test may require estate access, and that "
        "fixtures are owner-captured"
    )


def test_fixture_ownership_is_split_between_implementer_and_owner() -> None:
    """The format is ours; the captured bytes are the owner's."""
    section = _testing_section()
    assert "owner-captured" in section
    assert "does not validate a parser" in section


def test_the_testing_table_rows_record_who_captures_and_what_stands_in() -> None:
    """§3.1 asks §17.2 for a row and a paragraph; assert the row, not the prose.

    The paragraph is the memorable part, and a table row is the part an edit
    leaves alone because nothing quotes it. Without this assertion the row can
    go and the section still reads as if it were recorded.
    """
    section = _testing_section()
    table_lines = [
        line
        for line in section.splitlines()
        if line.startswith("|") and "---" not in line
    ]
    assert any("capture-fixtures" in line for line in table_lines), (
        "§17.2's table must name capture-fixtures as how the corpus is produced"
    )
    assert any("localhost" in line for line in table_lines), (
        "§17.2's table must name the loopback server as the estate's substitute"
    )


@pytest.mark.parametrize(
    "path",
    sorted(
        p for directory in TEST_DIRS for p in directory.rglob("*.py") if p.is_file()
    ),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_test_targets_a_non_local_host(path: Path) -> None:
    """No test may reach a non-local host.

    A convention with teeth. Every URL literal in the suite must be `localhost`
    or an RFC 2606 reserved name, so an integration test pointed at a real
    cluster fails here instead of hanging or, worse, mutating one.
    """
    text = path.read_text(encoding="utf-8")
    # `]` is inside the character class on purpose. Excluding it — which the
    # pattern used to do, to avoid swallowing a markdown link's closing bracket —
    # truncated every IPv6 literal at the closing bracket of the address, so the
    # bracket handling in `_is_local_or_reserved` could never fire and any test
    # naming an IPv6 loopback was reported as reaching a remote host. A hermetic
    # test had no way to say otherwise, which is the opposite of what this is for.
    scheme_spans = re.finditer(r"\bhttps?://[^\s'\"`)<>,;}]+", text)
    offenders = [
        match.group(0)
        for match in scheme_spans
        if not _is_local_or_reserved(match.group(0))
    ]
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} references non-local hosts {offenders}; "
        "tests must be hermetic (SPEC.md §17.2)"
    )


def _is_local_or_reserved(url: str) -> bool:
    authority = url.split("://", 1)[1].split("/", 1)[0]
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    else:
        host = host.split(":", 1)[0]
    lowered = host.lower()
    if lowered in LOCAL_HOST_TOKENS:
        return True
    return any(lowered.endswith(tld) for tld in RESERVED_TLDS)
