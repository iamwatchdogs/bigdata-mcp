"""Posture, and the capability vocabulary it generates.

`SPEC.md` §2.1 separates two things v1 conflated: **posture** is one declared
config value, and the **capability vocabulary** is a function of it. Under
`read_only` the generated schema for a portal has no verb field at all, so a write
is not a rule that could be forgotten — it is a shape the document cannot take.
§14.2 says the same about the portal spec format, for the same reason.

The mapping between the config literals (`"read_only"` / `"read_write"`) and this
enum is the one place those two spellings meet, and both are load-bearing: §16
carries the lowercase literals and `tests/test_repo_contracts.py` asserts them
there.
"""

from __future__ import annotations

import enum
from typing import Any
from typing import Literal

READ_ONLY: Literal["read_only"] = "read_only"
READ_WRITE: Literal["read_write"] = "read_write"
CONFIG_POSTURE_LITERALS: tuple[str, ...] = (READ_ONLY, READ_WRITE)

#: Words that make a tool name read as a write. Checked against the name because a
#: name is part of the surface: a tool called `drop_partitions` advertises a write
#: in the tool list, before any schema is read.
WRITE_VERB_WORDS: tuple[str, ...] = (
    "write",
    "mutate",
    "delete",
    "drop",
    "insert",
    "put",
)

_JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class Posture(enum.Enum):
    """The one declared value that decides what the tool surface can express.

    Defaults to `READ_ONLY`. §2.1's argument is that read-only must be structural,
    and a default that requires opting in is the only version of that which
    survives a config that fails to load.

    Attributes:
        READ_ONLY: No generated schema exposes a verb field.
        READ_WRITE: Verb fields are generated.
    """

    READ_ONLY = READ_ONLY
    READ_WRITE = READ_WRITE

    @classmethod
    def from_literal(cls, value: str) -> Posture:
        """Parse a config literal.

        Case-sensitive on purpose: a config is not prose, and a config that
        silently accepts `READ_ONLY` is one whose posture nobody can audit by
        reading the file.

        Args:
            value: Exactly `read_only` or `read_write`.

        Returns:
            The matching member.

        Raises:
            ValueError: If `value` is not one of the two literals. The message
                names both, so the fix does not require opening `SPEC.md`.
        """
        try:
            return cls(value)
        except ValueError:
            valid = " or ".join(CONFIG_POSTURE_LITERALS)
            message = f"Unknown posture {value!r}; expected {valid}"
            raise ValueError(message) from None

    @property
    def literal(self) -> str:
        """The config spelling of this posture."""
        return self.value

    @property
    def allows_writes(self) -> bool:
        """Whether a write verb may appear in a generated schema."""
        return self is Posture.READ_WRITE

    def describe(self) -> str:
        """One line for `doctor` and the advertised `instructions`.

        Returns:
            A sentence naming the posture and what it means for the surface.
        """
        if self is Posture.READ_ONLY:
            return "read_only: no generated schema exposes a write verb"
        return "read_write: write verbs are present in the generated schema"


def _refuse_if_unusable(
    name: str, posture: Posture, headers: dict[str, str] | None
) -> None:
    """Refuse a portal declaration the posture cannot express.

    The three refusals live together because they are one policy: a read-only
    portal is refused anything that would let a caller express a write, however
    it arrives. A literal verb in the name, a per-call header that overrides the
    store, or an endpoint list that is empty -- each is a way the same thing slips
    through, so each is checked at the same place.

    Args:
        name: The tool name being declared.
        posture: The posture it was declared under.
        headers: Per-call headers, or `None` when none were supplied.

    Raises:
        PermissionError: If the name reads as a write verb, or if per-call headers
            were supplied, under a posture that allows no writes.
    """
    if not posture.allows_writes and headers:
        message = (
            f"portal {name!r}: a per-call headers table is not expressible under "
            "read_only; put static headers in the portal spec file instead"
        )
        raise PermissionError(message)
    if not posture.allows_writes and _names_a_write(name):
        message = (
            f"portal {name!r}: the name reads as a write verb, which read_only "
            "does not expose"
        )
        raise PermissionError(message)


def portal_schema(
    *,
    name: str,
    paths: dict[str, str],
    posture: Posture = Posture.READ_ONLY,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generate a portal tool's input schema for `posture`.

    This is §2.1's capability vocabulary and the reason the module exists: under
    `READ_ONLY` the returned schema has **no `method` key**, so no caller value can
    express a write, and per-call `headers` are refused for the same reason §14.2
    refuses them -- a credential smuggled through a per-call field bypasses the
    store that redacts it. The base URL is deliberately not a parameter: it is
    session state belonging to `session.py`, and a schema carrying one would invite
    a caller to point the request somewhere the redirect allowlist never saw.

    Args:
        name: Tool name. Under `READ_ONLY` it must not itself read as a write.
        paths: Endpoint name to description. Becomes the `endpoint` enum.
        posture: `READ_ONLY` or `WRITE_ENABLED` (§2.1).
        headers: Static headers for the portal. Refused under `READ_ONLY`.

    Returns:
        A JSON Schema object. `additionalProperties` is false so a typo in a
        parameter is refused by the client rather than silently dropped.

    Raises:
        ValueError: If `paths` is empty, or if `posture` is `READ_ONLY` and
            `headers` were supplied -- including the `PermissionError` that second
            case raises, which this docstring names here to keep the contract of
            the public entry point in one place.
    """
    if not paths:
        message = f"portal {name!r} declares no readable endpoints"
        raise ValueError(message)
    _refuse_if_unusable(name, posture, headers)

    schema = _base_schema(name, paths)
    if posture.allows_writes:
        schema["properties"]["method"] = {
            "type": "string",
            "enum": ["GET", "POST"],
            "description": "HTTP verb; absent entirely under read_only (§2.1)",
        }
        schema["properties"]["headers"] = {
            "type": "object",
            "description": "Extra headers; forbidden under read_only (§14.2)",
        }
    return schema


def _base_schema(name: str, paths: dict[str, str]) -> dict[str, Any]:
    """Build the posture-independent part of a portal schema.

    `params` is a free-form object rather than a per-endpoint property bag on
    purpose: a portal declares one tool with an `endpoint` enum, not one tool per
    endpoint, and enumerating every parameter here would put §7's query surface in
    this file instead of in the portal spec that describes it.

    Args:
        name: Tool name, used as the schema title.
        paths: Endpoint name to description. Becomes the `endpoint` enum.

    Returns:
        A JSON Schema object with `endpoint` and `params` and no verb.
    """
    return {
        "$schema": _JSON_SCHEMA_DIALECT,
        "title": f"bigdata-mcp/{name}",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "endpoint": {
                "type": "string",
                "enum": sorted(paths),
                "description": "; ".join(paths[key] for key in sorted(paths)),
            },
            "params": {
                "type": "object",
                "description": (
                    "Query parameters, sent verbatim and echoed in resolved_params "
                    "so a wrong unit or timezone is visible rather than confident"
                ),
            },
        },
        "required": ["endpoint"],
    }


def _names_a_write(name: str) -> bool:
    """Whether a tool name reads as a write verb.

    Args:
        name: The tool name.

    Returns:
        True when any of `WRITE_VERB_WORDS` appears in the lowercased name.
    """
    lowered = name.lower()
    return any(word in lowered for word in WRITE_VERB_WORDS)
