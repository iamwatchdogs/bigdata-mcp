"""Load, validate, and resolve the configuration.

`SPEC.md` §16 is TOML at a path given by `--config` or `$BIGDATA_MCP_CONFIG`, and
§14.3 requires the **parsed** document to be validated against
`schemas/config.schema.json`. The schema is loaded raw, never derived from a model
(§4.3 mandate 1), so it stays a language-neutral artefact that a future Go port can
read without inheriting this implementation.

The typed result lives in `config_models`: a dataclass does no I/O, and keeping the
two apart means the model can be imported without touching the filesystem.

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
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from bigdata_mcp.config_models import Config
from bigdata_mcp.config_models import HdfsConfig
from bigdata_mcp.config_models import LimitsConfig
from bigdata_mcp.config_models import PortalsConfig
from bigdata_mcp.config_models import SolrConfig
from bigdata_mcp.config_models import TimeoutsConfig
from bigdata_mcp.config_models import YarnConfig
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

#: Leaf keys that name a credential exactly. Checked on the leaf rather than on
#: the dotted path, because `auth.custom.extra_headers.token` names a credential and
#: a path-aware test would miss it precisely for being long.
_SECRET_KEY_NAMES: frozenset[str] = frozenset({
    "apikey",
    "api_key",
    "bearer",
    "credential",
    "credentials",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
})

#: Leaf key suffixes that name a credential. `authorization` is here rather than
#: in the exact set because every header spelling of it ends that way —
#: `Authorization`, `Proxy-Authorization`, `X-Authorization` — and that is the one
#: name this list was missing.
_SECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "_authorization",
    "_credential",
    "_credentials",
    "_key",
    "_password",
    "_ref",
    "_secret",
    "_token",
    "authorization",
    "password",
)


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

    Dashes are folded to underscores first, because this list exists mainly for
    `auth.custom.extra_headers` and every header name in it is spelled with dashes:
    `X-Api-Key` and `api_key` are the same credential, and a matcher that only
    understood one spelling of a header would be defeated by the other.

    Nothing here matches a config field that is not a credential, which is the
    property that makes broadening this list safe rather than a tightening. The
    near-misses are named in `test_the_real_config_surface_is_not_a_credential`:
    `key_path`, `ccache_path` and `keytab` are file locations, and `auth` is an
    enum whose *value* is the word "password".

    Args:
        key: The leaf key name, never a dotted path.

    Returns:
        True for an exact credential name or a credential-shaped suffix.
    """
    lowered = key.lower().replace("-", "_")
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
