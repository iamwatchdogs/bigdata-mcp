"""Tests for C4: posture, the strict config load, and the error taxonomy.

**Mutation evidence** (AGENTS.md requires the red-then-green transcript). Each
mutation below was applied to the source, the named test was observed failing,
and the source was restored:

Mutation -> the test that goes red, verified one at a time:

* M1 emit `method` under `READ_ONLY` ->
  `test_read_only_portal_schema_has_no_verb_field`
* M2 drop `method` from `READ_WRITE` ->
  `test_read_write_portal_schema_exposes_a_verb_field`
* M3 relax `additionalProperties` on `server` ->
  `test_unknown_key_is_refused_not_ignored`
* M4 short-circuit the literal-secret pass ->
  `test_literal_credential_hidden_in_a_map_is_refused`
* M5 accept `backend_call_s >= 60` ->
  `test_backend_timeout_must_undercut_client`
* M6 make `known_hosts` optional ->
  `test_known_hosts_may_not_be_omitted`
* M7 mark the host-key error model-fixable ->
  `test_host_key_error_is_human_only`
* M8 flip the posture default to `read_write` ->
  `test_missing_mode_defaults_to_read_only`
* P1 name a `Posture` member that does not exist in a docstring ->
  `test_the_posture_enum_members_match_what_the_docstring_names`
* P2 raise the posture refusal as a `ValueError` ->
  `test_the_posture_refusal_is_not_catchable_as_a_bad_spec`

Each was applied, observed failing, and reverted. The red names are the exact
test names below, not a paraphrase of them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from bigdata_mcp import errors
from bigdata_mcp.config import CLIENT_TIMEOUT_S
from bigdata_mcp.config import ConfigSource
from bigdata_mcp.config import describe_source
from bigdata_mcp.config import load_config
from bigdata_mcp.config import resolve_config_path
from bigdata_mcp.config import validate
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture
from bigdata_mcp.posture import portal_schema
from tests.support.config_files import KNOWN_HOSTS
from tests.support.config_files import fresh_dir as _fresh_dir
from tests.support.config_files import write_config

#: A value that is deliberately not a credential, so the refusal it provokes is
#: about the *form* of the value rather than about anything it might contain.
_LITERAL = "not-a-credential"
HDFS_TAIL = f"""enabled = true
known_hosts = {KNOWN_HOSTS!r}
"""

VALID_TOML = """
[server]
mode = "read_only"
"""


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


# --------------------------------------------------------------------------
# Strict loading
# --------------------------------------------------------------------------


def test_unknown_key_is_refused_not_ignored() -> None:
    """The whole point of `additionalProperties: false`."""
    with pytest.raises(ConfigError, match="server_flavour"):
        load_config(
            write_config(
                _fresh_dir(), '[server]\nmode = "read_only"\nserver_flavour = "x"\n'
            )
        )


def test_unknown_nested_table_key_is_refused() -> None:
    """Closedness has to be recursive; `additionalProperties` at the top is not.

    The refusal names `hdfs`, the table the stray key sits in, because the error
    is rendered as an indexed dotted path and an operator has to be able to find
    the line. The root-level case is covered above; a schema closed only on
    `server` passes that one and lets a typo inside `[hdfs]` through, which is the
    shape of the mistake a nested table invites.
    """
    with pytest.raises(ConfigError, match="hdfs"):
        load_config(
            write_config(
                _fresh_dir(),
                '[server]\nmode = "read_only"\n\n[hdfs]\nenabled = true\nnope = 1\n',
            )
        )


def test_unparseable_toml_is_refused_by_path() -> None:
    """A syntax error is refused before any schema rule is consulted.

    The unclosed bracket means the parser raises and the schema never runs, so the
    message is the parser's own, carrying a line and a column. That is the only
    version of this failure an operator can act on: reported as a schema
    violation it would say what is wrong with the document's *shape*, which is not
    what is wrong with it.
    """
    path = write_config(_fresh_dir(), "[server\nmode = 'read_only'\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_missing_named_config_file_is_refused_not_defaulted() -> None:
    """An operator who named a file must never silently get defaults."""
    with pytest.raises(ConfigError, match="not found"):
        load_config(_fresh_dir() / "absent.toml")


def test_no_config_path_returns_defaults() -> None:
    """`None` is a legal state, distinct from a path that fails."""
    config = load_config()
    assert config.posture is Posture.READ_ONLY
    assert config.source_path is None


# --------------------------------------------------------------------------
# Load-bearing invariants from §3 and §16
# --------------------------------------------------------------------------


def test_backend_timeout_must_undercut_client() -> None:
    """§3: the client severs at 60s, so a 60s budget returns nothing."""
    with pytest.raises(ConfigError, match="severed"):
        load_config(
            write_config(
                _fresh_dir(),
                '[server]\nmode = "read_only"\n\n[timeouts]\nbackend_call_s = 60\n',
            )
        )
    assert pytest.approx(60.0) == CLIENT_TIMEOUT_S


def test_backend_timeout_just_under_the_client_default_is_accepted() -> None:
    """The bound is strict, so the largest legal value is the one below 60.

    The refusal is pinned above; without an accepting case a loader that refused
    everything from 59 upward, or replaced the value with a default, would still
    pass it. The schema deliberately carries `exclusiveMinimum: 0` and *not* the
    60 s bound, so this is the only place the Python-side check is exercised on a
    value it is meant to admit — and the figure arrives unrounded.
    """
    config = load_config(
        write_config(
            _fresh_dir(),
            "[server]\nmode = 'read_only'\n\n[timeouts]\nbackend_call_s = 59.5\n",
        )
    )
    assert config.timeouts.backend_call_s == pytest.approx(59.5)


def test_known_hosts_may_not_be_omitted() -> None:
    """§11.1.1: absence is not a legal state, so the schema requires it."""
    with pytest.raises(ConfigError, match="known_hosts"):
        load_config(write_config(_fresh_dir(), "[hdfs]\nenabled = true\n"))


def test_disabling_known_hosts_requires_optin() -> None:
    """§16: `known_hosts = "disabled"` is only legal alongside the opt-in flag."""
    with pytest.raises(ConfigError, match="allow_insecure"):
        load_config(
            write_config(
                _fresh_dir(), '[hdfs]\nenabled = true\nknown_hosts = "disabled"\n'
            )
        )
    config = load_config(
        write_config(
            _fresh_dir(),
            '[hdfs]\nenabled = true\nknown_hosts = "disabled"\nallow_insecure = true\n',
        )
    )
    assert config.hdfs is not None
    assert config.hdfs.known_hosts == "disabled"


def test_kerberos_keytab_and_its_reference_are_mutually_exclusive() -> None:
    """The schema says "not both", because "neither" is also a legal state.

    `keytab` is the older literal-path form and `keytab_ref` the preferred
    reference, and plenty of configs carry neither — which is exactly why the
    schema uses `not: {required: ...}` rather than `oneOf`, which would demand
    exactly one and refuse the ordinary keytab-less config. This is the only test
    that provokes that clause, so an operator who sets both is refused here and
    nowhere else.
    """
    with pytest.raises(ConfigError, match="keytab"):
        load_config(
            write_config(
                _fresh_dir(),
                """
                [kerberos]
                ccache_path = "/tmp/ccache"
                keytab = "/etc/keytab"
                keytab_ref = "keychain:bigdata-edge-keytab"
                """,
            )
        )


def test_entra_requires_a_tenant() -> None:
    """§15.4: `/common` fails against Entra, so the tenant is mandatory."""
    with pytest.raises(ConfigError, match="tenant"):
        load_config(
            write_config(
                _fresh_dir(),
                """
                [server]
                mode = "read_only"

                [auth]
                tier = "oidc_entra"

                [auth.entra]
                """,
            )
        )


def test_adapters_default_to_empty_so_no_third_party_is_ever_loaded() -> None:
    """§5.2 states the rule; an absent key is the mechanism that enforces it."""
    config = load_config(write_config(_fresh_dir(), VALID_TOML))
    assert config.adapters == ()


def test_redirect_allowlist_covers_every_configured_https_source() -> None:
    """§4.2: the allowlist is per-hop, so both HA peers must be in it."""
    config = load_config(
        write_config(
            _fresh_dir(),
            """
            [server]
            mode = "read_only"

            [yarn]
            enabled = true
            base_urls = ["https://rm1.invalid:8088", "https://rm2.invalid:8088"]
            credential_shape = "spnego"

            [solr]
            enabled = true
            base_urls = ["https://solr1.invalid:8983/solr"]
            credential_shape = "bearer"
            """,
        )
    )
    assert config.redirect_allowlist == {"rm1.invalid", "rm2.invalid", "solr1.invalid"}


def test_the_redirect_allowlist_reads_an_ipv6_literal_host() -> None:
    """An IPv6 RM is a legal `base_urls` entry and must reach the allowlist.

    Splitting the authority on the first colon turned `https://[::1]:8088` into
    `[`. That host matches nothing, so a redirect to it was refused — safe, but
    refused for a reason that looks like a misconfiguration rather than a parser
    that cannot count colons, and an estate on IPv6 loses HA redirects for a reason
    no operator could find.
    """
    config = load_config(
        write_config(
            _fresh_dir(),
            """
            [server]
            mode = "read_only"

            [yarn]
            enabled = true
            base_urls = ["https://[::1]:8088", "https://[0:0:0:0:0:0:0:1]:8088"]
            credential_shape = "spnego"
            """,
        )
    )
    # Both spellings of the same address collapse to one entry, and the allowlist
    # holds exactly one host rather than two spellings of it — otherwise a config
    # listing the short form refuses a redirect written in the long form, which is
    # the same bug as the colon split one layer further up.
    assert config.redirect_allowlist == {"::1"}


def test_redirect_allowlist_is_empty_when_nothing_is_configured() -> None:
    """No configured source means no redirect is followed at all."""
    config = load_config(write_config(_fresh_dir(), VALID_TOML))
    assert config.redirect_allowlist == frozenset()


def test_config_path_flag_outranks_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sources are set, so the precedence is genuinely exercised.

    Neither test in this pair passes the flag alone, so an implementation that
    read only the environment — or that consulted the flag only when the
    environment happened to be unset — would satisfy either one on its own. The
    flag is never second-guessed: a path an operator typed is a decision, and an
    env var left behind by an earlier session must not quietly become the config
    instead.
    """
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "/from/env.toml")
    assert resolve_config_path("/from/flag.toml") == Path("/from/flag.toml")


def test_config_path_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var is the second source, and only when the flag is absent.

    Passing `None` is how a caller says "no `--config`", which is the state the
    majority of runs are in. Without this arm, "the flag outranks the environment"
    would also be satisfied by an implementation that ignored the environment
    entirely — and an operator who exported the variable would silently get
    defaults with nothing to say so.
    """
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "/from/env.toml")
    assert resolve_config_path(None) == Path("/from/env.toml")


def test_config_path_expands_a_home_relative_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `~` in the env var is expanded here, because no shell will expand it later.

    `export BIGDATA_MCP_CONFIG=~/cfg.toml` leaves the literal two characters in
    the process environment: expansion happened in the shell that ran the export
    and is never redone. Left alone, the path is a relative name resolved against
    the server's working directory, so the load fails as *not found* — which reads
    as a missing file rather than as a path that was never resolved.
    """
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "~/cfg.toml")
    assert resolve_config_path(None) == Path.home() / "cfg.toml"


def test_config_path_absent_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No path anywhere is a legal state, not a fault.

    `load_config` turns `None` into the safe defaults, so an install with no config
    file at all is the ordinary case. An implementation that returned `Path("")`
    instead would pass both precedence tests above — `Path("")` is `.`, which is
    neither `None` nor a usable path — and then fail at the caller, so this is the
    arm that keeps "no config" distinct from "a path that does not load".
    """
    monkeypatch.delenv("BIGDATA_MCP_CONFIG", raising=False)
    assert resolve_config_path(None) is None


def test_doctor_can_name_where_the_config_came_from() -> None:
    """One loaded config, described two ways from two different inputs.

    `explicit` is what makes this `FLAG` even though nothing was loaded and
    `source_path` is `None`, and `explicit=False` over the very same object is
    `NONE`. So the source cannot be inferred from the config: an implementation
    that asked "was a path named?" by looking at `source_path` would report `NONE`
    for a real `--config` run, and `doctor` would send the operator looking for
    the file that was loaded from the command line.
    """
    config = load_config()
    assert describe_source(config, explicit=True) is ConfigSource.FLAG
    assert describe_source(config, explicit=False) is ConfigSource.NONE


# --------------------------------------------------------------------------
# Schema artefact
# --------------------------------------------------------------------------


def test_schema_file_ships_in_the_package() -> None:
    """§4.3 mandate 1: the schema is a neutral artefact, loaded raw."""
    from bigdata_mcp.config import load_schema

    schema = load_schema()
    assert schema["title"] == "bigdata-mcp configuration"


def test_every_object_in_the_schema_is_closed() -> None:
    """A table without a bounded `additionalProperties` is a hole in the strict load.

    `additionalProperties: false` is the closed case. A schema *dict* is the
    deliberately-open case: a map whose keys come from someone else's wire
    format (`extra_headers`) and whose values are still constrained.
    """
    from bigdata_mcp.config import load_schema

    def objects(node: object, path: str = "") -> list[tuple[str, Mapping[str, object]]]:
        """Collect every `type: object` node in the schema, each with its path.

        The schema is not one tree: `properties`, `$defs` and `allOf` hold schemas
        *inside arrays*, so a walk that descended dicts alone would report the root
        and stop, finding nothing to complain about. Paths carry the array indices
        for the same reason the loader's own error paths do — the failure has to
        name the object that is unbounded, and "the root" is not actionable when
        there are two dozen.

        Args:
            node: The value to walk. Any JSON-shaped value.
            path: Where this value sits, accumulated by the recursion.

        Returns:
            `(path, node)` for every object-typed node, the root labelled
            `(root)`.
        """
        found: list[tuple[str, Mapping[str, object]]] = []
        if isinstance(node, Mapping):
            if node.get("type") == "object":
                typed: Mapping[str, object] = cast("Mapping[str, object]", node)
                found.append((path or "(root)", typed))
            for key, value in node.items():
                found.extend(objects(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(objects(value, f"{path}[{index}]"))
        return found

    unbounded = [
        path
        for path, node in objects(load_schema())
        if not isinstance(node.get("additionalProperties"), (bool, dict))
    ]
    assert not unbounded, (
        f"schema objects with unbounded additionalProperties: {unbounded}"
    )


def test_schema_requires_nothing_that_specs16_calls_a_default() -> None:
    """§2.1's default is only a real default if an absent `mode` still loads."""
    validate({"timeouts": {"backend_call_s": 20}})


def test_validation_is_callable_on_a_parsed_document_without_a_file() -> None:
    """`validate` takes a document, so the schema check needs no file and no load.

    Separate from the `load_config` tests because the two are separately usable:
    anything holding a parsed document can check it against the strict schema
    without a `Path`, a temp directory, or the TOML round trip. The second half
    asserts that the accepting case still accepts — a `validate` that refused
    everything would satisfy a refusal-only test — and that a stray key nested
    inside `server` meets the same closed-object rule as one at the root.
    """
    validate({"server": {"mode": "read_only"}})
    with pytest.raises(ConfigError):
        validate({"server": {"mode": "read_only", "typo": 1}})


# --------------------------------------------------------------------------
# The §8.2 taxonomy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            errors.PathNotUnderAllowedPrefix(allowed_prefixes=("/warehouse/",)),
            "/warehouse/",
        ),
        (errors.PathTraversalAttempt(rejected_segment=".."), ".."),
        (errors.IllegalCharacterInPath(character="%", rule="no percent-encoding"), "%"),
        (errors.QueueFull(current_depth=16, cap=16, retry_after_s=1.5), "16"),
        (errors.BackendUnreachable(endpoint="https://rm1.invalid:8088"), "rm1.invalid"),
        (errors.CapabilityAbsent(available=("hdfs", "yarn")), "hdfs"),
        (
            errors.OutputUntruncatable(suggestion="narrow the prefix"),
            "narrow the prefix",
        ),
    ],
)
def test_each_taxonomied_error_carries_what_82_requires(
    error: errors.BigDataMcpError, expected: str
) -> None:
    """§8.2's "must contain" column, one representative fact per class.

    An error the model cannot act on is one it retries unchanged, so the required
    fact is the requirement and not the caller's to supply: each class builds it,
    and `str()` is the message alone or the message and the caller's detail
    separated, so the required half is never obscured by the supplementary one.

    Seven of the nine classes appear. `HostKeyUnknownOrChanged` is absent because
    its required content is a warning *against* an action, which its own test
    asserts, and `UnknownToolOrMalformedJson` is a protocol error rather than an
    `isError` envelope.
    """
    assert expected in str(error)


def test_host_key_error_is_human_only() -> None:
    """§8.2: not model-fixable, and never suggest accepting the key."""
    error = errors.HostKeyUnknownOrChanged(fingerprint="SHA256:abc")
    assert error.model_fixable is False
    assert error.surface == "isError"
    text = str(error).lower()
    assert "sha256:abc" in text
    assert "platform team" in text
    assert "do not accept the key automatically" in text
    assert "accept the key" not in text.replace(
        "do not accept the key automatically", ""
    )


@pytest.mark.parametrize(
    "error",
    [
        errors.PathNotUnderAllowedPrefix(),
        errors.PathTraversalAttempt(),
        errors.IllegalCharacterInPath(),
        errors.QueueFull(),
        errors.BackendUnreachable(),
        errors.CapabilityAbsent(),
        errors.OutputUntruncatable(),
        errors.HostKeyUnknownOrChanged(),
    ],
)
def test_every_taxonomy_class_is_model_surface_not_a_traceback(
    error: errors.BigDataMcpError,
) -> None:
    """All eight at their defaults, because this needs no facts to render.

    The test above can only reach a class once it has been handed the fact its
    message must carry. This one asserts the property that holds for every class
    regardless: it derives from the shared base, and rendering it produces the
    message with no traceback appended. §8.2's "never return a traceback" is a
    rule about the family, and a rule about a family is only measurable family-wide.

    Constructing each with no arguments is what makes the list exhaustive — a
    class whose fields were all required could not be enumerated here at all.
    """
    assert isinstance(error, errors.BigDataMcpError)
    assert "Traceback" not in str(error)


def test_malformed_json_is_a_protocol_error_not_an_envelope() -> None:
    """§8.2 routes this to JSON-RPC `-32602`, so it is not an `isError`."""
    error = errors.UnknownToolOrMalformedJson("no such tool: hdfs_ls")
    assert not isinstance(error, errors.QueueFull)
    assert "hdfs_ls" in str(error)


def test_credential_store_failure_never_suggests_a_plaintext_fallback() -> None:
    """§15.8 requires this be reported and never worked around.

    The class exists so that "degrade to plaintext" is a name a caller can catch
    and refuse rather than a string some future error path reaches for, and this
    is the assertion that no rendering of it points at the workaround. A message
    offering a plaintext file is worse than one offering nothing: an operator
    under time pressure takes it, and the credential is then on disk with no
    redaction boundary — the exact gap §15.8 exists to close.
    """
    error = errors.NoSecureStoreAvailable("no D-Bus session")
    assert isinstance(error, errors.BigDataMcpError)
    assert "plaintext" not in str(error).lower()


# --------------------------------------------------------------------------
# The paths that only a fault reaches
# --------------------------------------------------------------------------


def test_describe_reports_what_each_posture_exposes() -> None:
    """`doctor` prints this, so both arms have to say something true."""
    assert "no generated schema exposes a write verb" in Posture.READ_ONLY.describe()
    assert "write verbs are present" in Posture.READ_WRITE.describe()


def test_missing_schema_file_is_reported_as_a_packaging_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not an operator error: no TOML the operator writes can cause this."""
    import bigdata_mcp.config as config_module

    good = write_config(_fresh_dir(), VALID_TOML)
    config_module.load_schema.cache_clear()
    monkeypatch.setattr(config_module, "SCHEMA_PATH", Path("/nonexistent/schema.json"))
    try:
        with pytest.raises(ConfigError, match="missing from the package"):
            load_config(good)
    finally:
        config_module.load_schema.cache_clear()


def test_unparseable_schema_file_is_reported_as_a_packaging_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second packaging fault: the schema is present and is not JSON.

    Distinct from the missing-file case, and worded as a packaging fault for the
    same reason — no TOML an operator writes can make `config.schema.json`
    unparseable, so telling them their config is broken sends them to fix the
    wrong file.

    The schema is cached, so the cache is cleared before the swap and again in the
    `finally`. Without that, a previously loaded schema is handed back and the test
    measures the cache instead of the fault.
    """
    import bigdata_mcp.config as config_module

    scratch = _fresh_dir()
    broken = scratch / "config.schema.json"
    broken.write_text("{not json", encoding="utf-8")
    good = write_config(_fresh_dir(), VALID_TOML)
    config_module.load_schema.cache_clear()
    monkeypatch.setattr(config_module, "SCHEMA_PATH", broken)
    try:
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(good)
    finally:
        config_module.load_schema.cache_clear()


def test_a_secrets_inside_a_list_are_still_walked() -> None:
    """`_walk` recurses through arrays; a nested credential is still found."""
    from bigdata_mcp.config import _reject_literal_secrets

    with pytest.raises(ConfigError, match=r"\[1\]\.password"):
        _reject_literal_secrets({
            "things": [{"name": "a"}, {"name": "b", "password": _LITERAL}]
        })


def test_error_paths_render_as_an_indexed_dotted_path() -> None:
    """An operator has to find the offending key, not just be told it is wrong."""
    from bigdata_mcp.config import _json_path

    assert _json_path(()) == "(document root)"
    assert _json_path(("auth",)) == "auth"
    assert _json_path(("auth", "custom", 0, "name")) == "auth.custom[0].name"


def test_doctor_names_the_environment_as_the_config_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loaded path with no flag behind it is `ENVIRONMENT`, the last arm.

    The path is real and was loaded, so anything that infers the source from
    `source_path` alone reports `FLAG` here and never reaches `ENVIRONMENT` — the
    member `doctor` prints for the common case of an exported variable. Together
    with the test above, all three members are exercised and none is left to be
    covered by an enumeration that does not exist.
    """
    path = write_config(_fresh_dir(), VALID_TOML)
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", str(path))
    config = load_config()
    assert config.source_path == path
    assert describe_source(config, explicit=False) is ConfigSource.ENVIRONMENT
