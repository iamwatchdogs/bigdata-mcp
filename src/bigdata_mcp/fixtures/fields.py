"""Field-level validation for a fixture document.

`schema.py` says what a fixture is. This module says whether a parsed JSON object
is allowed to be one, and every refusal here names the field that caused it.

The split earns its keep in `parse_fixture`, which is a short list of calls into
here: each rule is small enough to hold in your head, and the format stays
readable as rules accumulate instead of becoming the place where validation grew.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn

from bigdata_mcp.errors import ConfigError
from bigdata_mcp.fixtures.schema import KNOWN_FIELDS
from bigdata_mcp.fixtures.schema import SUPPORTED_SCHEMA_VERSIONS
from bigdata_mcp.fixtures.schema import Fixture
from bigdata_mcp.fixtures.schema import Source
from bigdata_mcp.fixtures.schema import Transport

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["check_version", "load_fixture", "parse_fixture"]


def parse_fixture(document: object, *, origin: str = "<memory>") -> Fixture:
    """Validate one parsed document and build a `Fixture`.

    Args:
        document: The parsed JSON.
        origin: Where it came from, for error messages. A path in every
            file-backed case.

    Returns:
        The validated fixture.

    Every violation raises `ConfigError` naming the field and the value, one field
    at a time: a fixture with three problems is three edits a human has to make,
    and they should be visible together rather than one commit at a time.
    """
    fields = _object(document, origin)
    _check_known_fields(fields, origin)
    _check_version(fields, origin)
    source = _source_of(fields, origin)
    transport = _transport_of(fields, origin)
    _required_str(fields, origin, "source_id")
    _required_str(fields, origin, "operation")
    request = _object(fields.get("request"), origin, "request")
    _check_request_shape(origin, transport, request)
    _check_optional_str(fields, origin, "stdout")
    _check_optional_str(fields, origin, "stderr")
    captured_at, provenance = _check_capture_fields(fields, origin, source)
    return Fixture(
        schema_version=fields["schema_version"],
        source=source,
        transport=transport,
        source_id=fields["source_id"],
        operation=fields["operation"],
        request=request,
        stdout=fields.get("stdout", ""),
        stderr=fields.get("stderr", ""),
        exit_code=_exit_code(fields, origin),
        captured_at=captured_at,
        provenance=provenance,
        comment=_comment_of(fields, origin, source),
    )


def load_fixture(path: Path) -> Fixture:
    """Load and validate one fixture file.

    Args:
        path: The `.json` file to read.

    Returns:
        The validated fixture.

    Raises:
        ConfigError: If the file is unreadable, is not valid JSON, or violates a
            fixture rule. The origin in the message is the file path.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"Cannot read fixture {path}: {exc}"
        raise ConfigError(message) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"Fixture {path} is not valid JSON: {exc}"
        raise ConfigError(message) from exc
    return parse_fixture(document, origin=str(path))


def check_version(document: object, *, origin: str = "<memory>") -> str:
    """Return the fixture's schema version, refusing an unknown one.

    A helper so a caller that only wants the version — a corpus loader deciding
    whether it understands a file — does not have to build the whole record to
    read one field.

    Args:
        document: The parsed JSON.
        origin: Where it came from, for the error message.

    Returns:
        The declared `schema_version`.

    A missing or unsupported version is refused through `_refuse`.
    """
    fields = _object(document, origin)
    _check_version(fields, origin)
    return str(fields["schema_version"])


def _object(
    value: object, origin: str, field_name: str = "<document>"
) -> dict[str, Any]:
    """Return `value` as a string-keyed dict, refusing anything else.

    Args:
        value: The value to check.
        origin: The fixture's origin, for the message.
        field_name: The field being checked.

    Returns:
        The dict itself, with keys coerced to `str`.

    A non-dict is refused through `_refuse`.
    """
    if not isinstance(value, dict):
        seen = type(value).__name__
        _refuse(origin, field_name, f"expected an object, got {seen}")
    return {str(key): item for key, item in value.items()}


def _check_known_fields(document: dict[str, Any], origin: str) -> None:
    """Refuse any key the format does not define.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.

    An unrecognised key is refused rather than ignored. A typo in `source_id` that
    loads silently turns a fixture about HDFS into one matching nothing, which is
    the same shape of failure as a wrong answer with no error — the outcome §8.1
    names as the worst thing this server can do.
    """
    unknown = sorted(set(document) - KNOWN_FIELDS)
    if unknown:
        _refuse(
            origin,
            "document",
            f"unknown field(s) {unknown}; known fields are {sorted(KNOWN_FIELDS)}",
        )


def _check_version(document: dict[str, Any], origin: str) -> None:
    """Require a supported `schema_version`.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.
    """
    _required_str(document, origin, "schema_version")
    version = document["schema_version"]
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = sorted(SUPPORTED_SCHEMA_VERSIONS)
        _refuse(
            origin,
            "schema_version",
            f"{version!r} is not supported; expected one of {supported}",
        )


def _source_of(document: dict[str, Any], origin: str) -> Source:
    """Require `source` to say whether the fixture was observed or synthesised.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.

    Returns:
        The declared source.

    This is the field a reader trusts to tell a real capture from a made-up one,
    so its message says exactly that rather than just listing the two options.
    """
    _required_str(document, origin, "source")
    raw = document["source"]
    try:
        return Source(raw)
    except ValueError:
        _refuse(
            origin,
            "source",
            f"{raw!r} is neither 'synthetic' nor 'observed'; a fixture must say "
            "whether it was observed, or it can be mistaken for one",
        )


def _transport_of(document: dict[str, Any], origin: str) -> Transport:
    """Require `transport` to name one of the §5.1 families.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.

    Returns:
        The declared transport.
    """
    _required_str(document, origin, "transport")
    raw = document["transport"]
    try:
        return Transport(raw)
    except ValueError:
        supported = ", ".join(sorted(member.value for member in Transport))
        _refuse(origin, "transport", f"{raw!r} is not one of {supported}")


def _exit_code(document: dict[str, Any], origin: str) -> int:
    """Require an integer `exit_code`.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.

    Returns:
        The exit code, defaulting to 0.

    A non-integer is refused through `_refuse`, and a `bool` counts as one here:
    `True` *is* an `int` in Python, so a fixture whose exit status is `true` would
    otherwise load without complaint.
    """
    value = document.get("exit_code", 0)
    if not isinstance(value, int) or isinstance(value, bool):
        _refuse(origin, "exit_code", f"expected an integer, got {value!r}")
    return int(value)


def _check_optional_str(document: dict[str, Any], origin: str, field_name: str) -> None:
    """Require a field to be a string when present.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.
        field_name: The field to check.
    """
    value = document.get(field_name, "")
    if not isinstance(value, str):
        _refuse(origin, field_name, f"expected a string, got {value!r}")


def _comment_of(document: dict[str, Any], origin: str, source: Source) -> str | None:
    """Require a `comment` on a synthetic fixture, and only there.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.
        source: The declared source.

    Returns:
        The comment, or `None` for an observed fixture that has none.

    A synthetic fixture with no comment is refused through `_refuse`: the spec asks
    every one to name the claim it encodes, and an unlabelled fixture is a trap for
    whoever reads it months later.
    """
    comment = document.get("comment")
    if source is Source.OBSERVED:
        return comment if isinstance(comment, str) else None
    if not isinstance(comment, str) or not comment:
        _refuse(
            origin,
            "comment",
            "required for a synthetic fixture: name the SPEC.md claim it encodes, "
            "so a reader knows what it is and is not for",
        )
    return str(comment)


def _check_capture_fields(
    document: dict[str, Any],
    origin: str,
    source: Source,
) -> tuple[str | None, str | None]:
    """Check `captured_at` and `provenance` against the declared source.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.
        source: The declared source.

    Returns:
        The `(captured_at, provenance)` pair, both `None` for a synthetic fixture.

    An observed fixture missing either field, or a synthetic one carrying a
    `captured_at`, is refused through `_refuse`.
    """
    captured_at = document.get("captured_at")
    provenance = document.get("provenance")
    if source is Source.SYNTHETIC:
        if captured_at is not None:
            _refuse(
                origin,
                "captured_at",
                "must be absent for a synthetic fixture — it was never captured",
            )
        return None, None
    if not isinstance(captured_at, str) or not captured_at:
        _refuse(
            origin,
            "captured_at",
            "required for an observed fixture, and must be an RFC 3339 timestamp",
        )
    if not isinstance(provenance, str) or not provenance:
        _refuse(
            origin,
            "provenance",
            "required for an observed fixture: name the machine and the command",
        )
    return str(captured_at), str(provenance)


def _check_request_shape(
    origin: str,
    transport: Transport,
    request: dict[str, Any],
) -> None:
    """Check that `request` matches its transport's shape.

    Args:
        origin: The fixture's origin, for the message.
        transport: The declared transport.
        request: The request object.

    Every mismatch raises `ConfigError` naming `request` and the field inside it.
    """
    if transport is Transport.SSH_CLI:
        _check_argv(origin, request)
        return
    _check_http_request(origin, transport, request)


def _check_argv(origin: str, request: dict[str, Any]) -> None:
    """Require a non-empty argv list of strings.

    Args:
        origin: The fixture's origin, for the message.
        request: The request object.

    A missing, empty, non-list, or non-string `argv` is refused through `_refuse`.
    """
    argv = request.get("argv")
    if not isinstance(argv, list) or not argv:
        _refuse(
            origin,
            "request.argv",
            "required and non-empty for ssh_cli; argv, never a shell string "
            "(§11.1: quoting bugs on a bastion are remote code execution)",
        )
    if not all(isinstance(item, str) for item in argv):
        _refuse(origin, "request.argv", "every argv element must be a string")


def _check_http_request(
    origin: str,
    transport: Transport,
    request: dict[str, Any],
) -> None:
    """Require a `path`, and a `method` when one is given.

    Args:
        origin: The fixture's origin, for the message.
        transport: The declared transport.
        request: The request object.

    A missing or empty `path`, or a non-string `method`, is refused through
    `_refuse`.
    """
    path = request.get("path")
    if not isinstance(path, str) or not path:
        _refuse(origin, "request.path", f"required and non-empty for {transport}")
    method = request.get("method", "GET")
    if not isinstance(method, str):
        _refuse(origin, "request.method", f"expected a string, got {method!r}")


def _required_str(document: dict[str, Any], origin: str, field_name: str) -> None:
    """Require a string field to be present and non-empty.

    Args:
        document: The parsed fixture.
        origin: The fixture's origin, for the message.
        field_name: The field to require.

    An absent, non-string, or empty field is refused through `_refuse`.
    """
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        _refuse(origin, field_name, f"required and non-empty; got {value!r}")


def _refuse(origin: str, field_name: str, detail: str) -> NoReturn:
    """Refuse a fixture, naming the file and the field.

    The one place a fixture `ConfigError` is constructed. Callers describe this in
    prose rather than in a `Raises:` section, because they raise it from here
    rather than from their own body, and a `Raises:` that names an exception the
    function does not raise is a docstring lie.

    Args:
        origin: The fixture's origin.
        field_name: The field at fault.
        detail: What is wrong with it.

    Raises:
        ConfigError: Always.
    """
    message = f"{origin}: {field_name}: {detail}"
    raise ConfigError(message)
