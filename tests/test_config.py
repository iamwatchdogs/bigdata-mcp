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

Each was applied, observed failing, and reverted. The red names are the exact
test names below, not a paraphrase of them.
"""

from __future__ import annotations

import tempfile
import textwrap
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

KNOWN_HOSTS = "~/.ssh/known_hosts"
REF_KEYCHAIN = "keychain:bigdata-edge"
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


def _fresh_dir() -> Path:
    """Return a unique empty directory.

    Returns:
        A fresh directory, so one test's config file cannot reach the next.
    """
    return Path(tempfile.mkdtemp(prefix="bigdata-mcp-config-"))


def write_config(tmp_path: Path, body: str, name: str = "config.toml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Posture
# --------------------------------------------------------------------------


def test_missing_mode_defaults_to_read_only() -> None:
    """An absent `mode` is `read_only`, not an error and not `read_write`."""
    config = load_config(write_config(_fresh_dir(), "[server]\nlog_level = 'info'\n"))
    assert config.posture is Posture.READ_ONLY


def test_both_posture_literals_are_accepted() -> None:
    for literal, expected in (
        ("read_only", Posture.READ_ONLY),
        ("read_write", Posture.READ_WRITE),
    ):
        config = load_config(
            write_config(_fresh_dir(), f'[server]\nmode = "{literal}"\n')
        )
        assert config.posture is expected


def test_unknown_posture_literal_is_refused() -> None:
    with pytest.raises(ConfigError, match="mode"):
        load_config(write_config(_fresh_dir(), '[server]\nmode = "readonly"\n'))


def test_posture_round_trips_through_its_config_literal() -> None:
    assert Posture.from_literal(Posture.READ_WRITE.literal) is Posture.READ_WRITE


def test_from_literal_lists_both_valid_values_in_the_message() -> None:
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


def test_read_only_refuses_a_write_shaped_tool_name() -> None:
    """A name is a surface too; `drop_partitions` is a write by its name."""
    with pytest.raises(PermissionError, match="write verb"):
        portal_schema(
            name="drop_partitions",
            paths={"drop": "drop"},
            posture=Posture.READ_ONLY,
        )


def test_portal_with_no_endpoints_is_refused() -> None:
    with pytest.raises(ValueError, match="no readable endpoints"):
        portal_schema(name="warehouse", paths={})


def test_portal_schema_is_closed_to_unexpected_properties() -> None:
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
    with pytest.raises(ConfigError, match="hdfs"):
        load_config(
            write_config(
                _fresh_dir(),
                '[server]\nmode = "read_only"\n\n[hdfs]\nenabled = true\nnope = 1\n',
            )
        )


def test_unparseable_toml_is_refused_by_path() -> None:
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
# Secrets are references only (§15.8)
# --------------------------------------------------------------------------


def test_literal_secret_is_refused() -> None:
    with pytest.raises(ConfigError, match="no plaintext credential tier"):
        load_config(
            write_config(
                _fresh_dir(),
                """
                [hdfs]
                enabled = true
                known_hosts = "~/.ssh/known_hosts"
                password_ref = "hunter2"
                """,
            )
        )


def test_keychain_reference_is_accepted() -> None:
    config = load_config(
        write_config(
            _fresh_dir(),
            """
            [hdfs]
            enabled = true
            known_hosts = "~/.ssh/known_hosts"
            password_ref = "keychain:bigdata-edge"
            """,
        )
    )
    assert config.hdfs is not None
    assert config.hdfs.password_ref == REF_KEYCHAIN


@pytest.mark.parametrize("prefix", ["keychain:", "exec:", "file:"])
def test_every_resolver_prefix_is_accepted(prefix: str) -> None:
    config = load_config(
        write_config(
            _fresh_dir(),
            f"[hdfs]\nenabled = true\nknown_hosts = {KNOWN_HOSTS!r}\n"
            f'password_ref = "{prefix}thing"\n',
        )
    )
    assert config.hdfs is not None


def test_env_prefix_is_not_a_credential_tier() -> None:
    """§15.8 deprioritises `env:`; the resolver order has no env member."""
    with pytest.raises(ConfigError):
        load_config(
            write_config(
                _fresh_dir(),
                f"[hdfs]\nenabled = true\nknown_hosts = {KNOWN_HOSTS!r}\n"
                'password_ref = "env:HOME"\n',
            )
        )


def test_literal_credential_hidden_in_a_map_is_refused() -> None:
    """The second load pass, for the keys the schema's `$ref`s do not cover.

    `auth.custom.extra_headers` is intentionally an open map, because a Tier 3
    IdP names its headers however it likes. That openness must not become a way
    to write a literal token into a file.
    """
    with pytest.raises(ConfigError, match="no plaintext credential tier"):
        load_config(
            write_config(
                _fresh_dir(),
                """
                [server]
                mode = "read_only"

                [auth]
                tier = "custom"

                [auth.custom]
                extra_headers = { token = "hunter2" }
                """,
            )
        )


def test_a_credential_reference_inside_an_open_map_is_accepted() -> None:
    """The pass refuses literals, not the practice of sending a header."""
    config = load_config(
        write_config(
            _fresh_dir(),
            """
            [server]
            mode = "read_only"

            [auth]
            tier = "custom"

            [auth.custom]
            extra_headers = { Authorization = "keychain:bigdata-mcp" }
            """,
        )
    )
    assert config.posture is Posture.READ_ONLY


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


def test_redirect_allowlist_is_empty_when_nothing_is_configured() -> None:
    """No configured source means no redirect is followed at all."""
    config = load_config(write_config(_fresh_dir(), VALID_TOML))
    assert config.redirect_allowlist == frozenset()


def test_config_path_flag_outranks_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "/from/env.toml")
    assert resolve_config_path("/from/flag.toml") == Path("/from/flag.toml")


def test_config_path_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "/from/env.toml")
    assert resolve_config_path(None) == Path("/from/env.toml")


def test_config_path_expands_a_home_relative_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", "~/cfg.toml")
    assert resolve_config_path(None) == Path.home() / "cfg.toml"


def test_config_path_absent_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIGDATA_MCP_CONFIG", raising=False)
    assert resolve_config_path(None) is None


def test_doctor_can_name_where_the_config_came_from() -> None:
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
    assert isinstance(error, errors.BigDataMcpError)
    assert "Traceback" not in str(error)


def test_malformed_json_is_a_protocol_error_not_an_envelope() -> None:
    """§8.2 routes this to JSON-RPC `-32602`, so it is not an `isError`."""
    error = errors.UnknownToolOrMalformedJson("no such tool: hdfs_ls")
    assert not isinstance(error, errors.QueueFull)
    assert "hdfs_ls" in str(error)


def test_credential_store_failure_never_suggests_a_plaintext_fallback() -> None:
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
    import bigdata_mcp.config as config_module

    scratch = Path(tempfile.mkdtemp(prefix="schema-"))
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
    path = write_config(_fresh_dir(), VALID_TOML)
    monkeypatch.setenv("BIGDATA_MCP_CONFIG", str(path))
    config = load_config()
    assert config.source_path == path
    assert describe_source(config, explicit=False) is ConfigSource.ENVIRONMENT
