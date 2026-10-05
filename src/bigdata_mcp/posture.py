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
    express a write. Supplying `headers` under `READ_ONLY` is refused for the same
    reason §14.2 refuses it — a credential smuggled through a per-call field
    bypasses the store that redacts it.

    The portal's base URL is deliberately *not* a parameter. A base URL is session
    state belonging to `session.py`, and a schema that carried one would invite a
    caller to point the request somewhere the redirect allowlist never saw.

    Args:
        name: Tool name. Under `READ_ONLY` it must not itself read as a write,
            since the name is part of the advertised surface.
        paths: Read endpoints mapped to a one-line description each. Only these
            are exposed, which is what keeps the surface finite.
        posture: Declared posture. Defaults to `READ_ONLY`.
        headers: Static headers to embed. `READ_ONLY` only, because a per-call
            `headers` table is a credential bypass (§14.2).

    Returns:
        A JSON-Schema object. Under `READ_ONLY` it contains neither `method` nor
        `headers`.

    Raises:
        PermissionError: If `headers` is supplied, or `name` reads as a write
            verb, while `posture` is `READ_ONLY`.
        ValueError: If `paths` is empty, which would publish a tool with no
            readable endpoints at all.
    """
    if not paths:
        message = f"portal {name!r} declares no readable endpoints"
        raise ValueError(message)
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

    schema: dict[str, Any] = {
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


def _names_a_write(name: str) -> bool:
    """Whether a tool name reads as a write verb.

    Args:
        name: The tool name.

    Returns:
        True when any of `WRITE_VERB_WORDS` appears in the lowercased name.
    """
    lowered = name.lower()
    return any(word in lowered for word in WRITE_VERB_WORDS)
