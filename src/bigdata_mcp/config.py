"""Load, validate, and resolve the configuration.

`SPEC.md` §16 is TOML at a path given by `--config` or `$BIGDATA_MCP_CONFIG`, and
§14.3 requires the **parsed** document to be validated against
`schemas/config.schema.json`. The schema is loaded raw, never derived from a model
(§4.3 mandate 1), so it stays a language-neutral artefact that a future Go port can
read without inheriting this implementation.

Three properties this module exists to guarantee:

1. **Unknown keys are refused.** Every object in the schema sets a bounded
   `additionalProperties`. A typo in a config that half-applies is worse than one
   that refuses to load, because the operator believes a setting took effect.
2. **A literal secret is refused.** `password_ref = "hunter2"` is not a warning,
   it is a refusal: §15.8 has no plaintext tier, so a literal is a secret with no
   redaction boundary. The schema's `pattern` catches the keys it knows about and
   a second pass catches the rest, including keys nested in a deliberately open
   map.
3. **Posture is read here.** Every capability decision downstream reads
   `config.posture`, so this is the single place §2.1's two spellings meet.
"""

from __future__ import annotations

import enum
import json
import os
import tomllib
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture

CONFIG_ENV_VAR = "BIGDATA_MCP_CONFIG"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "config.schema.json"

#: The MCP client's own default request timeout (§3). Every backend call must
#: finish inside it or the connection is severed and no error can be returned.
CLIENT_TIMEOUT_S: float = 60.0

#: The only credential reference prefixes the resolver understands (§15.8). There
#: is deliberately no `env:` member: MCP stdio clients sanitise the environment
#: to about six variables, so env-based secrets are quietly unreliable here.
SECRET_REF_PREFIXES: tuple[str, ...] = ("keychain:", "exec:", "file:")

_LITERAL_CREDENTIAL_RULE = (
    "a secret must be a reference (keychain:, exec:, or file:) — there is no "
    "plaintext credential tier (§15.8)"
)

#: Leaf keys that name a credential. Checked on the leaf rather than on the
#: dotted path, because `auth.custom.extra_headers.token` names a credential and a
#: path-aware test would miss it precisely for being long.
_SECRET_KEY_NAMES: frozenset[str] = frozenset({"password", "secret", "token"})
_SECRET_KEY_SUFFIXES: tuple[str, ...] = ("_ref", "_password", "password")


@cache
def load_schema() -> dict[str, Any]:
    """Return the parsed config schema.

    Cached: the file ships inside the package and cannot change at runtime, and
    re-parsing it per load would dominate a config reload.

    Returns:
        The schema as plain JSON data, ready for `jsonschema`.

    Raises:
        ConfigError: If the schema file is missing or is not valid JSON. That is
            a packaging fault rather than an operator error, and must not be
            reported to an operator as one.
    """
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        message = f"Config schema is missing from the package: {SCHEMA_PATH}"
        raise ConfigError(message) from exc
    except json.JSONDecodeError as exc:
        message = f"Config schema is not valid JSON: {exc}"
        raise ConfigError(message) from exc


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the config path from the CLI flag, then the environment.

    Args:
        explicit: The `--config` value, if the caller was given one. It wins over
            the environment and is never second-guessed: a path on the command
            line is a decision, not a hint.

    Returns:
        The expanded path to load, or `None` when neither source named one.
        `None` is the legal "no config" state, distinct from a path that fails
        to load.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    return None


@dataclass(frozen=True, slots=True)
class HdfsConfig:
    """The `[hdfs]` table.

    Attributes:
        enabled: Whether the HDFS adapter is configured at all.
        entrypoint: Server-side SSH alias. Never caller-supplied (§11.1).
        namenode_uri: Recorded for `doctor`; the CLI path never uses it.
        allowed_prefixes: The path allowlist. An empty tuple permits nothing.
        known_hosts: Trust store path, or the literal `disabled`.
        bastion_known_hosts: Separate trust store for the bastion hop, because a
            bastion is a different machine with a different key.
        allow_insecure: The explicit opt-in that `known_hosts = "disabled"`
            requires.
        auth: `key` or `password`.
        key_path: Path to the private key, for `auth = "key"`.
        password_ref: Credential reference, for `auth = "password"`. Never a
            literal (§15.8).
    """

    enabled: bool
    entrypoint: str = ""
    namenode_uri: str = ""
    allowed_prefixes: tuple[str, ...] = ()
    known_hosts: str = ""
    bastion_known_hosts: str = ""
    allow_insecure: bool = False
    auth: str = "key"
    key_path: str = ""
    password_ref: str = ""


@dataclass(frozen=True, slots=True)
class YarnConfig:
    """The `[yarn]` table. `base_urls` is also the redirect allowlist (§4.2).

    Attributes:
        enabled: Whether the YARN adapter is configured.
        base_urls: Every configured RM. A 307 between two of these is followed; a
            307 to anything else is refused and names the host.
        credential_shape: How YARN authenticates (§15.6).
    """

    enabled: bool
    base_urls: tuple[str, ...] = ()
    credential_shape: str = "spnego"


@dataclass(frozen=True, slots=True)
class SolrConfig:
    """The `[solr]` table — a bundled instance of the custom-port family.

    Attributes:
        enabled: Whether Solr is configured.
        base_urls: Configured Solr base URLs.
        credential_shape: How Solr authenticates.
        fail_if_open: Refuse to run against an unauthenticated Solr. An open Solr
            on a network this server can reach is a data-exfiltration path, and
            silently serving from one is the wrong default.
    """

    enabled: bool
    base_urls: tuple[str, ...] = ()
    credential_shape: str = "bearer"
    fail_if_open: bool = True


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """The `[limits]` table.

    `edge_host_concurrency` is `"auto"` or an int, exactly as §16 declares it. The
    schema carries the `anyOf`, and widening it here to a float would admit a
    value the schema rejects — two rules, disagreeing.

    Attributes:
        edge_host_concurrency: `"auto"` to derive `clamp(cores/2, 2, 8)` from the
            edge host, or a pinned integer.
        queue_depth: Bound on queued work. The queue rejects rather than grows.
        max_rows: Row cap per response.
        max_output_bytes: Byte cap per response, 96 KiB by default.
    """

    edge_host_concurrency: str | int = "auto"
    queue_depth: int = 16
    max_rows: int = 500
    max_output_bytes: int = 98304


@dataclass(frozen=True, slots=True)
class TimeoutsConfig:
    """The `[timeouts]` table.

    Attributes:
        backend_call_s: Per-backend budget. Must undercut `CLIENT_TIMEOUT_S`.
        ssh_handshake_s: SSH connection budget.
        stall_probe_interval_s: How often the observer probes for a stalled host.
    """

    backend_call_s: float = 20.0
    ssh_handshake_s: float = 10.0
    stall_probe_interval_s: float = 20.0


@dataclass(frozen=True, slots=True)
class PortalsConfig:
    """The `[portals]` table.

    Attributes:
        portal_dir: Directory holding §14.2 portal spec files.
        enabled: Explicit allowlist of spec files. Never a glob, never a
            directory sweep: an allowlist that means "everything present" is not
            an allowlist.
    """

    portal_dir: str = ""
    enabled: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Config:
    """The loaded configuration.

    Frozen and slotted. It is read on every request path and nothing has a
    legitimate reason to mutate it in place; a reload replaces the whole value.

    Attributes:
        posture: The declared posture (§2.1). Every capability decision reads
            this and nothing else.
        log_level: stderr log level. stdout is reserved for the JSON envelope.
        observer_staleness_s: How stale an observer reading may be.
        adapters: The only third-party adapter names that may be resolved. Empty
            means none is ever loaded (§5.2).
        limits: Concurrency, queue, and output bounds.
        timeouts: Per-subsystem budgets.
        hdfs: The `[hdfs]` table, or `None` when it was absent.
        yarn: The `[yarn]` table, or `None` when it was absent.
        solr: The `[solr]` table, or `None` when it was absent.
        portals: The `[portals]` table.
        raw: The parsed document, kept so `doctor` can echo resolved config and
            so a caller can read a key this dataclass does not model yet.
        source_path: Where it was loaded from, or `None` for the no-config state.
    """

    posture: Posture = Posture.READ_ONLY
    log_level: str = "info"
    observer_staleness_s: int = 5
    adapters: tuple[str, ...] = ()
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    hdfs: HdfsConfig | None = None
    yarn: YarnConfig | None = None
    solr: SolrConfig | None = None
    portals: PortalsConfig = field(default_factory=PortalsConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    @property
    def redirect_allowlist(self) -> frozenset[str]:
        """Hosts a redirect may land on. §4.2 checks this on every hop.

        Built from every configured `base_urls` across the HTTPS sources, so a
        YARN HA peer declared in either table is reachable. An empty result means
        no redirect is followed at all, which is the correct default when
        nothing is configured: following no redirect cannot reach an unconfigured
        host.
        """
        hosts: set[str] = set()
        for section in (self.yarn, self.solr):
            if section is None:
                continue
            for url in section.base_urls:
                authority = url.split("://", 1)[-1].split("/", 1)[0]
                hosts.add(authority.rsplit("@", 1)[-1].split(":", 1)[0])
        return frozenset(host for host in hosts if host)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load, validate, and model the configuration.

    The order is load-bearing: the schema check runs before the model is built, so
    a bad key can never reach a dataclass and a good key can never be quietly
    dropped by one.

    Args:
        path: Explicit path. Falls back to `$BIGDATA_MCP_CONFIG`, then to the
            no-config defaults.

    Returns:
        The modelled config. With no path configured at all, the safe defaults
        are returned: `read_only`, no adapters, no sources.

    Raises:
        ConfigError: If the TOML is unparseable, fails the schema, carries a
            literal secret, or declares a backend timeout at or above the
            client's own.
    """
    resolved = resolve_config_path(path)
    if resolved is None:
        return Config()
    if not resolved.is_file():
        message = f"Config file not found: {resolved}"
        raise ConfigError(message)

    document = _parse_toml(resolved)
    validate(document)
    _reject_literal_secrets(document)
    config = _build(document, resolved)
    _check_client_timeout(config)
    return config


def validate(document: dict[str, Any]) -> None:
    """Validate a parsed document against `schemas/config.schema.json`.

    Args:
        document: The parsed TOML as plain dicts, lists, and scalars.

    Raises:
        ConfigError: If the document violates the schema. Every violation is
            reported at once rather than one per run, because a config with three
            typos should cost one round trip. A secret violation is rewritten to
            say so in the spec's terms instead of quoting a regex.
    """
    validator = jsonschema.Draft202012Validator(load_schema())
    violations = sorted(
        validator.iter_errors(document),
        key=lambda err: [str(part) for part in err.absolute_path],
    )
    if not violations:
        return
    problems = [
        f"{_json_path(err.absolute_path)}: {_describe(err)}" for err in violations
    ]
    raise ConfigError("Config failed validation:\n  - " + "\n  - ".join(problems))


def _describe(error: jsonschema.ValidationError) -> str:
    """Render one violation, translating a secret-pattern failure into its rule.

    Args:
        error: The violation.

    Returns:
        The spec's wording for a literal secret, or the validator's own message
        for everything else.
    """
    if _is_secret_ref_violation(error):
        return _LITERAL_CREDENTIAL_RULE
    return error.message


def _is_secret_ref_violation(error: jsonschema.ValidationError) -> bool:
    """Whether an error is a secret key carrying a literal instead of a reference.

    `jsonschema` resolves a `$ref` before raising, so the reported path names the
    *referring* key and the reported schema is the dereferenced node. That is why
    the discriminator is the pattern rather than the path: the path can never say
    which `$defs` entry it came from. The pattern is read back out of the schema
    file so there is one definition of it, not two that can drift.

    Args:
        error: The violation.

    Returns:
        True when the failure is the secret-reference pattern.
    """
    if error.validator != "pattern":
        return False
    failing_schema = error.schema
    if not isinstance(failing_schema, Mapping):
        return False
    secret_pattern = load_schema()["$defs"]["secretRef"]["pattern"]
    return bool(failing_schema.get("pattern") == secret_pattern)


def _reject_literal_secrets(document: dict[str, Any]) -> None:
    """Refuse a credential-looking leaf whose value is not a reference.

    The schema's `pattern` already rejects the keys it models with a `$ref`. This
    pass covers the rest — most importantly a credential-shaped key nested inside a
    deliberately open map such as `auth.custom.extra_headers`, where the keys come
    from another system and cannot be enumerated in a schema.

    Args:
        document: The parsed TOML.

    Raises:
        ConfigError: On the first literal secret found, naming the dotted path
            so the operator knows which line to edit.
    """
    for dotted, leaf_key, value in _walk(document):
        if not isinstance(value, str) or not _is_secret_key(leaf_key):
            continue
        if value.startswith(SECRET_REF_PREFIXES):
            continue
        message = f"{dotted}: {_LITERAL_CREDENTIAL_RULE} (got a literal value)"
        raise ConfigError(message)


def _is_secret_key(key: str) -> bool:
    """Whether a leaf key names a credential.

    Args:
        key: The leaf key name, never a dotted path.

    Returns:
        True for an exact credential name or a credential-shaped suffix.
    """
    lowered = key.lower()
    return lowered in _SECRET_KEY_NAMES or lowered.endswith(_SECRET_KEY_SUFFIXES)


def _walk(document: object, prefix: str = "") -> list[tuple[str, str, object]]:
    """Flatten a nested document to `(dotted.path, leaf_key, leaf_value)` triples.

    The leaf key is carried alongside the path because only the leaf names a
    credential, while only the path names the field to a human fixing the config.

    Args:
        document: Any JSON-shaped value.
        prefix: Path accumulated so far, used by the recursion.

    Returns:
        One triple per non-dict leaf. Lists recurse with an index in the path.
    """
    found: list[tuple[str, str, object]] = []
    if isinstance(document, dict):
        for key, value in document.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.extend(_walk_leaf(value, path, str(key)))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(_walk_leaf(value, f"{prefix}[{index}]", str(index)))
    return found


def _walk_leaf(value: object, path: str, key: str) -> list[tuple[str, str, object]]:
    """One walk step: recurse into a nested container, or emit a leaf.

    Args:
        value: The value at this position.
        path: The dotted path accumulated so far.
        key: The leaf key this position would be reported under.

    Returns:
        The triples for this subtree.
    """
    if isinstance(value, (dict, list)):
        return _walk(value, path)
    return [(path, key, value)]


def _parse_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file.

    Args:
        path: The file to read.

    Returns:
        The parsed document.

    Raises:
        ConfigError: If the file is not valid TOML. The parser's own message
            carries a line and column, which is what makes it fixable.
    """
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        message = f"Config file is not valid TOML: {exc}"
        raise ConfigError(message) from exc


def _build(document: dict[str, Any], source: Path) -> Config:
    """Model a validated document.

    Args:
        document: A document that has already passed `validate`.
        source: Where it came from, recorded on the result.

    Returns:
        The modelled config. Assumes validation passed, which is why every value
        is narrowed without re-checking it.
    """
    server = document.get("server", {})
    limits = document.get("limits", {})
    timeouts = document.get("timeouts", {})
    portals = document.get("portals", {})
    hdfs = document.get("hdfs")
    yarn = document.get("yarn")
    solr = document.get("solr")
    return Config(
        posture=Posture.from_literal(server.get("mode", "read_only")),
        log_level=server.get("log_level", "info"),
        observer_staleness_s=int(server.get("observer_staleness_s", 5)),
        adapters=tuple(document.get("adapters", [])),
        limits=LimitsConfig(
            edge_host_concurrency=limits.get("edge_host_concurrency", "auto"),
            queue_depth=int(limits.get("queue_depth", 16)),
            max_rows=int(limits.get("max_rows", 500)),
            max_output_bytes=int(limits.get("max_output_bytes", 98304)),
        ),
        timeouts=TimeoutsConfig(
            backend_call_s=float(timeouts.get("backend_call_s", 20.0)),
            ssh_handshake_s=float(timeouts.get("ssh_handshake_s", 10.0)),
            stall_probe_interval_s=float(timeouts.get("stall_probe_interval_s", 20.0)),
        ),
        hdfs=HdfsConfig(**_table(hdfs)) if isinstance(hdfs, dict) else None,
        yarn=YarnConfig(**_table(yarn)) if isinstance(yarn, dict) else None,
        solr=SolrConfig(**_table(solr)) if isinstance(solr, dict) else None,
        portals=PortalsConfig(
            portal_dir=portals.get("portal_dir", ""),
            enabled=tuple(portals.get("enabled", [])),
        ),
        raw=document,
        source_path=source,
    )


def _table(section: dict[str, Any]) -> dict[str, Any]:
    """Coerce TOML arrays to tuples, matching the dataclass field types.

    The schema already fixed every value's type, so this is a narrowing that
    cannot fail — which is the only reason it is safe to skip validation here.

    Args:
        section: One validated table.

    Returns:
        The same table with every list replaced by a tuple.
    """
    return {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in section.items()
    }


def _check_client_timeout(config: Config) -> None:
    """Refuse a backend budget the client could sever before we answer.

    §3 records a 60 s client-side default. A backend budget at or above it means
    the worst case is a severed connection and no message at all, which is
    strictly worse than an error. This check lives here rather than in the schema
    on purpose: one rule, one implementation, one message. A `maximum: 59` in the
    schema would produce a second, differently-worded enforcement of the same
    fact, and the two would eventually disagree about the boundary.

    Args:
        config: The modelled config.

    Raises:
        ConfigError: If `backend_call_s` is at or above `CLIENT_TIMEOUT_S`.
    """
    backend_s = config.timeouts.backend_call_s
    if backend_s >= CLIENT_TIMEOUT_S:
        message = (
            f"timeouts.backend_call_s={backend_s} must be under the client's "
            f"{CLIENT_TIMEOUT_S:.0f}s default, or the connection is severed "
            "before a clean error can be returned"
        )
        raise ConfigError(message)


def _json_path(parts: Sequence[object]) -> str:
    """Render a jsonschema error path as a dotted, index-bearing path.

    Args:
        parts: `error.absolute_path`, which mixes strings and integers.

    Returns:
        `a.b[0].c`, or `(document root)` for an empty path.
    """
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered or "(document root)"


class ConfigSource(enum.Enum):
    """Where a config path came from, so `doctor` can say so.

    Attributes:
        FLAG: Named on the command line with `--config`.
        ENVIRONMENT: Taken from `$BIGDATA_MCP_CONFIG`.
        NONE: No config was named and the defaults are in force.
    """

    FLAG = "flag"
    ENVIRONMENT = "environment"
    NONE = "none"


def describe_source(config: Config, *, explicit: bool) -> ConfigSource:
    """Classify where a loaded config's path came from.

    Args:
        config: The loaded config.
        explicit: Whether the caller was given a `--config` path.

    Returns:
        The source. `NONE` is reported when no path was named at all, which is
        different from a path that named a file containing only defaults.
    """
    if explicit:
        return ConfigSource.FLAG
    if config.source_path is not None:
        return ConfigSource.ENVIRONMENT
    return ConfigSource.NONE
