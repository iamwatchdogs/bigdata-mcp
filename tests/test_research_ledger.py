"""Contract tests over the decision ledger in ``docs/research/README.md``.

The ledger states its own rule:

    "If new research changes a decision, add a new ledger entry with the date and
    what changed. **Do not edit the old entry in place: the history of a decision
    changing is the part worth reading later.**"

Nothing enforces that rule. A future edit that "tidies up" D1's conclusion, or
restates D5's finding without its correction, would pass every gate in this
repository and would destroy the one artefact that records why the project looks
the way it does. That is the same class of failure
``tests/test_repo_contracts.py`` guards in ``SPEC.md``, applied to the ledger.

Each assertion is mutation-verified: change the thing, watch the named test go
red, restore it.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

LEDGER_PATH = Path(__file__).resolve().parent.parent / "docs" / "research" / "README.md"

#: Every decision entry the ledger must carry, newest last. D6 is the scope
#: realignment; D1-D5 predate it and must survive it unedited.
EXPECTED_ENTRIES = ("D1", "D2", "D3", "D4", "D5", "D6")

#: One verbatim phrase from each pre-existing entry. These are the sentences that
#: carry the entry's *conclusion* -- not its topic. D5's correction ("they were
#: not in the dependency tree") is included because that is the part a careless
#: edit would drop: it is the sentence that reversed the wrong hypothesis.
PRIOR_ENTRY_ANCHORS = {
    "D1": "the v2 native-HDFS argument is language-neutral",
    "D2": "Only a Tier-1 SDK implementing the `2026-07-28` spec is acceptable",
    "D3": "These cost roughly a day and are the highest-leverage work",
    "D4": "the gates are cheapest to add while there",
    "D5": "They are **not** in the dependency tree",
}

#: The commitments D6 must record. Each is a conclusion that was expensive to
#: reach and is invisible in any other file -- Stainless was rejected on CVE
#: evidence, and the refresh-token finding is a permanent-lockout bug.
SCOPE_REALIGNMENT_ANCHORS = (
    "CVE-2026-42551",
    "RFC 9700",
    "Stainless",
    "read_write",
    "web_session",
    "mcp_client",
    "fact domain",
)

_ENTRY_HEADING = re.compile(r"^### (D\d+)\b")


@cache
def _ledger_text() -> str:
    """Return the ledger contents once per process."""
    assert LEDGER_PATH.is_file(), (
        f"decision ledger not found at {LEDGER_PATH}. SPEC.md v2 references it as "
        "the record of why each decision was taken, and D6 documents the scope "
        "change; without it the rationale for SPEC.md v2 is unrecoverable"
    )
    return LEDGER_PATH.read_text(encoding="utf-8")


@cache
def _entry_headings() -> tuple[str, ...]:
    """Return the ledger's decision identifiers in document order."""
    found: list[str] = []
    for line in _ledger_text().splitlines():
        match = _ENTRY_HEADING.match(line)
        if match is None:
            continue
        # ``Match.group`` is typed ``str | Any`` because it returns ``None`` for
        # a group that does not exist. Group 1 is mandatory in this pattern, so
        # the value is always a string -- but narrowing it with an assert rather
        # than a ``str()`` cast means a future pattern that makes the group
        # optional fails loudly instead of yielding the string ``"None"`` as a
        # ledger entry.
        entry = match.group(1)
        assert isinstance(entry, str), (
            f"{LEDGER_PATH.name} has a '### Dn' heading that captured a non-string "
            f"identifier: {entry!r}"
        )
        found.append(entry)

    assert found, f"{LEDGER_PATH.name} contains no '### Dn' decision headings"
    return tuple(found)


def test_ledger_records_every_decision_entry() -> None:
    """Assert D1-D6 are all present, in order.

    Order matters as much as presence: the ledger's value is chronological, and
    an entry inserted before D1 would misrepresent when a decision was taken.
    """
    headings = _entry_headings()
    missing = [entry for entry in EXPECTED_ENTRIES if entry not in headings]
    assert not missing, (
        f"{LEDGER_PATH.name} is missing decision entr(ies) {missing}. The ledger "
        f"has {list(headings)}. Each entry records what a decision changed and "
        "why; a missing one makes the change unreconstructable"
    )

    first_index = [headings.index(entry) for entry in EXPECTED_ENTRIES]
    assert first_index == sorted(first_index), (
        f"decision entries are out of chronological order in {LEDGER_PATH.name}: "
        f"{list(headings)}. The ledger's own rule is that 'the history of a "
        "decision changing is the part worth reading later', which requires the "
        "sequence to be the real one"
    )


def test_prior_ledger_entries_were_not_edited_in_place() -> None:
    """Assert D1-D5 still carry their original conclusions.

    This is the ledger's own rule turned into a gate. Editing a prior entry to
    reflect the new decision would erase the record of the *old* one, which is
    the only reason a reader can tell that a decision changed rather than was
    always this way.
    """
    text = _ledger_text()
    missing = {
        entry: anchor
        for entry, anchor in PRIOR_ENTRY_ANCHORS.items()
        if anchor not in text
    }
    assert not missing, (
        f"{LEDGER_PATH.name} no longer contains the conclusion of {sorted(missing)}. "
        f"Missing: {list(missing.values())!r}. The ledger forbids editing prior "
        "entries in place -- add a new entry instead. If an entry is genuinely "
        "wrong, that is a new finding and belongs in D7, not a rewrite of the "
        "entry that recorded what was believed at the time"
    )


def test_scope_realignment_is_recorded_in_the_ledger() -> None:
    """Assert D6 exists and records the commitments only it can carry.

    The Stainless rejection and the refresh-token finding are conclusions from
    research that has no other home. They are not in SPEC.md's design sections --
    SPEC.md references them -- and the council log predates both. If D6 loses
    them, the reasoning is gone while the decision remains.
    """
    text = _ledger_text()
    missing = [anchor for anchor in SCOPE_REALIGNMENT_ANCHORS if anchor not in text]
    assert not missing, (
        f"{LEDGER_PATH.name} does not record {missing}. D6 must capture why the "
        "scope changed: the council's partitioning blind spot, the Stainless "
        "rejection with its CVE evidence, and the refresh-token lockout finding. "
        "Each is a conclusion with no other home in the repository"
    )


def test_ledger_marks_the_connector_research_as_superseded() -> None:
    """Assert the superseded state of the adapter research is recorded.

    ``polite-engine-and-adapters.md`` closed the adapter space three ways: a
    closed ``Connector.id`` union, a closed ``kind`` enum, and a filesystem-shaped
    ``Connector`` interface. Those are why a web portal could not be expressed.
    The research document is **not** edited to match -- editing it would destroy
    the record of what the research actually found -- so D6 has to say plainly
    that it is superseded on those points, or a future reader will treat a
    closed enum as a current constraint.
    """
    text = _ledger_text()
    for anchor in ("superseded", "polite-engine-and-adapters"):
        assert anchor in text, (
            f"{LEDGER_PATH.name} does not mention {anchor!r}. The connector "
            "research is superseded on three specific points and that has to be "
            "written down, because the document itself still reads as current"
        )
