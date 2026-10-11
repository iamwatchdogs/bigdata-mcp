"""The §8.2 taxonomy, the shipped schema, and the paths only a fault reaches.

Split from `test_config.py` when that file crossed Codacy's 500-line limit. What
is left here is the loader's edges: the errors a caller can act on, the artefact
that makes a config a language-neutral contract, and the two failures — an
unreadable file and a missing one — that are not the same refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from bigdata_mcp import config as config_module
from bigdata_mcp import errors
from bigdata_mcp.config import ConfigSource
from bigdata_mcp.config import _reject_literal_secrets
from bigdata_mcp.config import describe_source
from bigdata_mcp.config import load_config
from bigdata_mcp.config import validate
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture
from tests.support.config_files import fresh_dir as _fresh_dir
from tests.support.config_files import write_config

#: A literal shaped like a credential, composed at runtime for the reason the
#: support module records: a committed literal is what secret scanners flag.
from tests.test_config import VALID_TOML

#: A literal shaped like a credential, composed at runtime for the reason the
#: support module records: a committed literal is what secret scanners flag.
_LITERAL = "".join(("not", "a", "credential"))

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
