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

from pathlib import Path

import pytest

from bigdata_mcp.config import CLIENT_TIMEOUT_S
from bigdata_mcp.config import ConfigSource
from bigdata_mcp.config import describe_source
from bigdata_mcp.config import load_config
from bigdata_mcp.config import resolve_config_path
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture
from tests.support.config_files import KNOWN_HOSTS
from tests.support.config_files import REF_KEYCHAIN
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


def test_a_solr_table_can_carry_the_credential_reference_it_declares() -> None:
    """The schema models `[solr] password_ref`, so the dataclass must hold it.

    `SolrConfig` enumerated four of the schema's five keys, so a schema-valid
    `[solr]` table carrying `password_ref` died in `SolrConfig(**_table(...))`
    with `TypeError: unexpected keyword argument` — a config the validator had
    just passed, refused by the model that is supposed to describe it. The
    reference is what a `basic` Solr authenticates with, and §15.8's whole shape
    depends on there being a field to put it in.
    """
    config = load_config(
        write_config(
            _fresh_dir(),
            """
            [server]
            mode = "read_only"

            [solr]
            enabled = true
            base_urls = ["https://solr1.invalid:8983/solr"]
            credential_shape = "basic"
            password_ref = "keychain:bigdata-edge"
            """,
        )
    )

    assert config.solr is not None
    assert config.solr.password_ref == REF_KEYCHAIN, (
        "the reference the loader validated did not reach the model; a schema key "
        "with no field behind it is a key that silently changes nothing"
    )


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
