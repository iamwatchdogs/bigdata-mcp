"""Tests for §15.8: a credential is a reference, and the loader refuses literals.

Split from `test_config.py` because this is the one section with its own failure
mode worth naming on its own. Every other config rule refuses a *malformed*
document; this one refuses a *well-formed* document that asks for the thing §15.8
does not offer. A schema that merely disallowed the value would be weaker than the
schema: a keychain reference must be allowed through unchanged, because the loader
carries it verbatim and never resolves it (§2.3 mandate 3).

Mutation evidence for the source behaviour here (M4, "short-circuit the
literal-secret pass" -> `test_literal_credential_hidden_in_a_map_is_refused`) is
recorded in `test_config.py`'s module docstring, where the M-series lives.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from typing import Any

import pytest

from bigdata_mcp.config import load_config
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.posture import Posture
from tests.support.config_files import KNOWN_HOSTS
from tests.support.config_files import REF_KEYCHAIN
from tests.support.config_files import fresh_dir as _fresh_dir
from tests.support.config_files import write_config

if TYPE_CHECKING:
    from pathlib import Path


def _load(body: str, tmp_path: Path, **overrides: Any) -> Any:
    """Write a config and load it, with `portals.auth` extended by `overrides`.

    Args:
        body: The TOML body.
        tmp_path: Where to write it.
        **overrides: Fields merged into the `portals.auth` table.

    Returns:
        The loaded `Config`.
    """
    table = "\n".join(f"{key} = {value!r}" for key, value in overrides.items())
    merged = body + "\n" + table + "\n"
    return load_config(write_config(tmp_path, textwrap.dedent(merged)))


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
