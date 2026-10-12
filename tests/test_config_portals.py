"""§2.1 posture and §14.2 portals: the schema's shape follows the declaration.

Split from `test_config.py` when that file crossed Codacy's 500-line limit. The
subject is one rule with two halves: `read_only` is structural rather than
validated — the generated schema has no verb field at all, so a write is not a
value that could be rejected — and a portal declaration the posture cannot
express is a `PermissionError` rather than a `ValueError`, so a caller catching
one does not swallow the other.
"""

from __future__ import annotations

import re

import pytest

from bigdata_mcp.config import load_config
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture
from bigdata_mcp.posture import portal_schema
from tests.support.config_files import fresh_dir as _fresh_dir
from tests.support.config_files import write_config

# --------------------------------------------------------------------------
# Posture
# --------------------------------------------------------------------------


def test_missing_mode_defaults_to_read_only() -> None:
    """An absent `mode` is `read_only`, not an error and not `read_write`."""
    config = load_config(write_config(_fresh_dir(), "[server]\nlog_level = 'info'\n"))
    assert config.posture is Posture.READ_ONLY


def test_both_posture_literals_are_accepted() -> None:
    """Both arms of the enum, in the spelling an operator actually types.

    The round-trip test below walks a literal back to its member; this walks the
    other way, through a TOML file and the schema's `mode` enum, so a spelling
    that validates but never reaches `Posture.from_literal` cannot pass unnoticed.
    `read_write` is the arm worth carrying: it is the only way a verb ever gets
    into a schema, and a loader that quietly mapped it to `read_only` would fail
    safe rather than loudly.
    """
    for literal, expected in (
        ("read_only", Posture.READ_ONLY),
        ("read_write", Posture.READ_WRITE),
    ):
        config = load_config(
            write_config(_fresh_dir(), f'[server]\nmode = "{literal}"\n')
        )
        assert config.posture is expected


def test_unknown_posture_literal_is_refused() -> None:
    """A misspelled posture is a refusal, not a fall back to the default.

    Two layers can refuse one — the `mode` enum in the schema and
    `Posture.from_literal` — and this asserts only that a `ConfigError` names
    `mode`, because §16 does not say which layer reports it and the operator's
    fix is the same either way. The dangerous version of this code is the one
    that catches the refusal, maps an unknown spelling onto `read_only`, and
    carries on: it fails safe, so nothing reports it.
    """
    with pytest.raises(ConfigError, match="mode"):
        load_config(write_config(_fresh_dir(), '[server]\nmode = "readonly"\n'))


def test_posture_round_trips_through_its_config_literal() -> None:
    """`literal` and the member's value are one string, and `READ_WRITE` proves it.

    Both members stand in the same relation, so one is enough — and `READ_WRITE`
    is the one to check because `_build` hands the *string* `"read_only"` straight
    to `from_literal` for an absent `mode`. `READ_ONLY`'s value is therefore
    exercised by every default config already, and `READ_WRITE` is the member that
    reaches the enum only through this property.
    """
    assert Posture.from_literal(Posture.READ_WRITE.literal) is Posture.READ_WRITE


def test_from_literal_lists_both_valid_values_in_the_message() -> None:
    """The refusal enumerates the accepted pair.

    A message naming only the offending value leaves the operator to open
    `SPEC.md` to discover there are two postures at all, which is an expensive
    round trip on the file they are trying to fix. Asserting the joined form
    (`read_only or read_write`) also pins that both spellings are rendered from
    one tuple, so a third literal cannot produce a message naming two of three.
    """
    with pytest.raises(ValueError, match="read_only or read_write"):
        Posture.from_literal("nope")


# --------------------------------------------------------------------------
# The structural property: read_only has no verb field
# --------------------------------------------------------------------------


def test_read_only_portal_schema_has_no_verb_field() -> None:
    """§2.1: under `read_only` there is no verb field to set.

    The property is structural. There is no `method` key at all, so no caller
    value can express a write — there is nothing to validate against.
    """
    schema = portal_schema(
        name="warehouse",
        paths={"list": "list partitions", "count": "count rows"},
        posture=Posture.READ_ONLY,
    )
    assert "method" not in schema["properties"]
    assert "headers" not in schema["properties"]


def test_read_write_portal_schema_exposes_a_verb_field() -> None:
    """`read_write` is not merely permissive: the verb appears."""
    schema = portal_schema(
        name="warehouse",
        paths={"list": "list partitions"},
        posture=Posture.READ_WRITE,
    )
    assert schema["properties"]["method"]["enum"] == ["GET", "POST"]


def test_no_portal_schema_has_a_headers_table() -> None:
    """§14.2 and §4.2: the portal spec has no headers table under *either* posture.

    `read_write` used to grow an untyped `headers` object, and the reason it must
    not is CVE-2026-42551: `X-HTTP-Method-Override` is honoured on safe verbs, so a
    free-form header table lets a config turn a `GET` into a `DELETE` — and it can
    carry a caller-chosen `Authorization` past the session's credential handling
    while it does. A read-only guarantee that only holds while the posture is
    read-only is not a guarantee.
    """
    for posture in (Posture.READ_ONLY, Posture.READ_WRITE):
        schema = portal_schema(
            name="warehouse",
            paths={"list": "list partitions"},
            posture=posture,
        )
        assert "headers" not in schema["properties"], (
            f"{posture.name} grew a headers property, which the portal spec "
            "format does not have"
        )


def test_a_per_call_headers_table_is_refused_under_read_write_too() -> None:
    """The schema has no headers property, so a spec carrying one is refused.

    Previously the refusal was keyed on `not posture.allows_writes`, which made
    the read-write path the one place a headers table could still be declared —
    the posture with the verb that matters most to override.
    """
    with pytest.raises(PermissionError, match="headers"):
        portal_schema(
            name="warehouse",
            paths={"list": "list"},
            posture=Posture.READ_WRITE,
            headers={"X-HTTP-Method-Override": "DELETE"},
        )


def test_read_only_refuses_a_per_call_headers_table() -> None:
    """§14.2: a per-call `headers` table is a credential bypass."""
    with pytest.raises(PermissionError, match="headers"):
        portal_schema(
            name="warehouse",
            paths={"list": "list"},
            posture=Posture.READ_ONLY,
            headers={"Authorization": "Bearer x"},
        )


def test_the_posture_refusal_is_not_catchable_as_a_bad_spec() -> None:
    """`PermissionError` is not a `ValueError`, and that is deliberate.

    `portal_schema`'s docstring listed only `ValueError`, so a caller following it
    wrote `except ValueError` around the call — and the posture refusals, which are
    raised by `_refuse_if_unusable`, sailed past. A caller that treated a policy
    decision as a typo would carry on and generate the schema it was just refused.

    Asserted rather than left to the docstring, because a docstring is not enforced
    and this is exactly the kind of contract that decays into a wrong exception type
    nobody notices until a call is refused for the wrong reason.
    """
    assert not issubclass(PermissionError, ValueError)

    with pytest.raises(PermissionError):
        portal_schema(
            name="warehouse",
            paths={"list": "list"},
            posture=Posture.READ_ONLY,
            headers={"Authorization": "Bearer x"},
        )

    with pytest.raises(ValueError, match="no readable endpoints") as caught:
        portal_schema(name="warehouse", paths={}, posture=Posture.READ_ONLY)
    assert not isinstance(caught.value, PermissionError), (
        "an empty spec must not be reported as a permission problem"
    )


def test_the_posture_enum_members_match_what_the_docstring_names() -> None:
    """Every enum member the docstring names must exist, and vice versa.

    `portal_schema`'s docstring said `READ_ONLY` or `WRITE_ENABLED`; the second
    member is `READ_WRITE`. A docstring naming a member that does not exist is worse
    than one naming none, because the reader has to work out whether the member was
    renamed or the prose is stale, and the only way to find out is to open the enum.

    Checked both directions. "Every named member exists" alone would pass a
    docstring that named neither; "every member is named" alone would pass one that
    named an extra. What matters is that the two lists agree, because a caller
    choosing a posture reads this and not the enum.
    """
    members = {member.name for member in Posture}
    assert members == {"READ_ONLY", "READ_WRITE"}

    doc = portal_schema.__doc__ or ""
    named = set(re.findall(r"Posture\.([A-Z_]+)", doc))
    assert named == members, (
        f"the docstring names {sorted(named)}, the enum has {sorted(members)}"
    )


def test_read_only_refuses_a_write_shaped_tool_name() -> None:
    """A name is a surface too; `drop_partitions` is a write by its name."""
    with pytest.raises(PermissionError, match="write verb"):
        portal_schema(
            name="drop_partitions",
            paths={"drop": "drop"},
            posture=Posture.READ_ONLY,
        )


def test_a_read_only_portal_named_after_its_reads_is_accepted() -> None:
    """`list_outputs` is a read, and the whole name is the surface there is.

    Matched as a substring, `put` is inside "outputs" — so a portal named for
    the read it serves was refused under the posture that forbids nothing it
    does. `sprouting` is the same collision from the other direction: a word
    that happens to contain `put` as an accident of English. A refusal that is
    wrong is a refusal an operator deletes the rule over, so the matcher reads
    whole `_`- and `-`-separated tokens and nothing else.

    The word `drop_partitions` above still has to be refused, which is what
    keeps this from being read as permission to loosen the rule.
    """
    schema = portal_schema(
        name="list_outputs",
        paths={"list": "list partitions"},
        posture=Posture.READ_ONLY,
    )
    assert "method" not in schema["properties"], (
        "a read-only portal schema grew a verb field"
    )

    accepted = portal_schema(
        name="sprouting_jobs",
        paths={"status": "read the sprout state"},
        posture=Posture.READ_ONLY,
    )
    assert "method" not in accepted["properties"]


def test_a_hyphenated_write_token_is_still_a_write() -> None:
    """Tokens split on `-` as well as `_`: `put-partitions` says put.

    Whole-token matching narrows the rule, so the narrowing has to be pinned at
    its edge: the same word arrives hyphenated from a portal file written by
    someone else's convention and must still be refused.
    """
    with pytest.raises(PermissionError, match="write verb"):
        portal_schema(
            name="put-partitions",
            paths={"put": "put"},
            posture=Posture.READ_ONLY,
        )


def test_portal_with_no_endpoints_is_refused() -> None:
    """An empty endpoint list is a bad spec, so it is a `ValueError`.

    Not a `PermissionError`: nothing about the posture was violated, the
    declaration simply has nothing to read, and the operator fixes it by editing
    the spec file. That separation is load-bearing —
    `test_the_posture_refusal_is_not_catchable_as_a_bad_spec` asserts that a
    caller catching `ValueError` does not swallow policy refusals, which only
    holds while the two failures stay in different types.

    No `posture` is passed, which also fixes the ordering: the emptiness check
    runs before the posture is consulted, so the refusal is the same `ValueError`
    under either posture.
    """
    with pytest.raises(ValueError, match="no readable endpoints"):
        portal_schema(name="warehouse", paths={})


def test_portal_schema_is_closed_to_unexpected_properties() -> None:
    """Closed, and closed in the posture-independent part of the builder.

    `additionalProperties: false` is set by the base schema both postures share, so
    a typo in a query parameter is refused by the client rather than silently
    dropped — the same rule the config schema enforces on a config file, applied to
    the document a model fills in. Nothing about the closedness may depend on the
    caller remembering to pass a posture, and none is passed.
    """
    schema = portal_schema(
        name="warehouse",
        paths={"list": "list"},
    )
    assert schema["additionalProperties"] is False
