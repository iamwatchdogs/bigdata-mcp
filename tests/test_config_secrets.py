"""Tests for §15.8: a credential is a reference, and the loader refuses literals.

Split from `test_config.py` because this is the one section with its own failure
mode worth naming on its own. Every other config rule refuses a *malformed*
document; this one refuses a *well-formed* document that asks for the thing §15.8
does not offer. A schema that merely disallowed the value would be weaker than the
schema: a keychain reference must be allowed through unchanged, because the loader
carries it verbatim and never resolves it (§2.3 mandate 3).

Mutation evidence for the source behaviour here (M4, "short-circuit the
literal-secret pass" -> `test_literal_credential_hidden_in_a_map_is_refused`) is
recorded in `test_config.py`'s module docstring, where the M-series lives. The
S-series added with the header-spelling cases lives here:

* S1 drop `authorization` from the matched names ->
  `test_a_literal_credential_header_in_the_open_map_is_refused[Authorization]` and
  the `Proxy-Authorization` case
* S2 stop folding `-` to `_` in the leaf name ->
  `test_a_literal_credential_header_in_the_open_map_is_refused[X-Api-Key]`
* S3 add a credential suffix broad enough to catch a file path ->
  `test_the_real_config_surface_is_not_a_credential`
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

#: Stand-in values for the refusals below, assembled at runtime and named for
#: what they are rather than for what they once looked like.
#:
#: The runtime composition is deliberate: a committed literal shaped like a
#: credential is exactly what secret scanners rightly flag, and a scanner that
#: cries wolf over the tests trains people to ignore it on the day it finds
#: something real.
#:
#: `NOT_A_REFERENCE` is the value that belongs in neither field. A `_ref` field
#: must hold a reference (`keychain:`, `exec:`, `file:`) and an open map must not
#: hold a plaintext token, so what both refusals are about is a literal that is
#: not a reference. It was called `FAKE_PASSWORD` until CodeQL's
#: `py/clear-text-storage-sensitive-data` reported the *name* as a sensitive-data
#: source flowing into a file write -- which claimed it was credential-shaped when
#: the code goes out of its way not to be. It is not a password, and the name now
#: says so.
NOT_A_REFERENCE = "".join(("not", "a", "reference"))
NOT_A_TOKEN = "Bearer " + "".join(("abc", "123"))


def test_literal_secret_is_refused() -> None:
    """§15.8 has no plaintext tier, so a literal in a `_ref` field is a refusal.

    The message states the rule rather than quoting a regex: the operator's next
    step is to replace the value with a reference, and "does not match pattern"
    does not tell them which shapes qualify.
    """
    with pytest.raises(ConfigError, match="no plaintext credential tier"):
        load_config(
            write_config(
                _fresh_dir(),
                f"""
                [hdfs]
                enabled = true
                known_hosts = "~/.ssh/known_hosts"
                password_ref = "{NOT_A_REFERENCE}"
                """,
            )
        )


def test_keychain_reference_is_accepted() -> None:
    """The accepting arm, asserted on the value rather than on the absence of an error.

    A refusal-only test is satisfied just as happily by a loader that refuses
    everything, references included — but §2.3 mandate 3 requires the reference to
    be carried through verbatim and never resolved here. Comparing against the
    shared constant pins that the string arrives unchanged: not resolved, not
    normalised, no prefix stripped, so a resolver one tier down finds what it
    expects.
    """
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
    """The accepted set is exactly the three prefixes a resolver can open.

    The set is written down twice — the loader's tuple and the schema's pattern —
    and both have to agree with what a resolver can actually open. Parametrising
    over the three is what catches the disagreement when a fourth is added to one
    side only: the operator gets a config that loads and a reference nothing can
    resolve.

    `env:` is deliberately not among them. MCP stdio clients sanitise the
    environment down to about six variables, so an env-based secret is quietly
    unreliable here rather than merely discouraged, and it is refused rather than
    ranked lowest.
    """
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
                f"""
                [server]
                mode = "read_only"

                [auth]
                tier = "custom"

                [auth.custom]
                extra_headers = {{ token = "{NOT_A_REFERENCE}" }}
                """,
            )
        )


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Proxy-Authorization",
        "X-Api-Key",
        "api_key",
        "apikey",
        "X-Auth-Token",
        "client_secret",
        "passwd",
        "bearer",
    ],
)
def test_a_literal_credential_header_in_the_open_map_is_refused(
    header: str, tmp_path: Path
) -> None:
    """`extra_headers` must not become a way to write a secret to disk.

    The second load pass exists for exactly this map — its keys come from another
    system and cannot be enumerated in a schema — and it matched only `password`,
    `secret`, `token` and three suffixes. An `Authorization` header carrying a
    literal bearer value sailed through, so the module docstring's promise that
    this map "cannot smuggle a literal secret in" was not true of the one header
    name that matters most.

    Every spelling is checked because headers are spelled with dashes and config
    keys with underscores, and a matcher that understood only one of them would be
    defeated by the other.
    """
    with pytest.raises(ConfigError, match="no plaintext credential tier"):
        load_config(
            write_config(
                tmp_path,
                f"""
                [server]
                mode = "read_only"

                [auth]
                tier = "custom"

                [auth.custom]
                extra_headers = {{ {header} = "{NOT_A_TOKEN}" }}
                """,
            )
        )


def test_the_real_config_surface_is_not_a_credential(tmp_path: Path) -> None:
    """Broadening the matcher is safe only if these still load.

    The obvious near-misses in this repository's own schema: `key_path`,
    `ccache_path` and `keytab` are file locations, and `auth` is an enum whose
    *value* is the word "password". None ends in a credential suffix, and this
    test is what keeps that true — a matcher that refused `ccache_path` would push
    operators toward the workaround §15.8 exists to prevent, which would make the
    rule worse than the gap it closed.

    An earlier attempt at this covered the same ground with a deny-list of
    `_path`-shaped suffixes. That guard was dead code: no suffix in the list
    matched a `_path` leaf, so the test stayed green when the guard was deleted —
    a mutation that proved nothing. Naming the real fields is what actually holds.
    """
    config = load_config(
        write_config(
            tmp_path,
            """
            [server]
            mode = "read_only"

            [kerberos]
            transport = "curl_subprocess"
            ccache_path = "/tmp/krb5cc_1000"
            keytab = "/etc/krb5.keytab"

            [hdfs]
            enabled = true
            auth = "password"
            key_path = "/etc/security/keytabs/hdfs.keytab"
            known_hosts = "~/.ssh/known_hosts"
            """,
        )
    )
    assert config.posture is Posture.READ_ONLY


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
