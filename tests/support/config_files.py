"""Helpers for writing TOML configs under test.

Shared because two test modules write configs, and a helper duplicated between them
is a helper that will drift: the two would accept different documents while both
claiming to describe the same loader.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

#: A keychain reference. Deliberately not a credential: nothing here resolves it,
#: so a test can assert that it is carried verbatim without holding a real secret.
REF_KEYCHAIN = "keychain:bigdata-edge"

#: The SSH trust anchor every config in these tests must name, because §16 makes
#: `known_hosts` load-bearing rather than optional.
KNOWN_HOSTS = "~/.ssh/known_hosts"


def fresh_dir() -> Path:
    """Return a unique empty directory outside pytest's `tmp_path`.

    Returns:
        A fresh directory, so one test's config file cannot reach the next.
    """
    return Path(tempfile.mkdtemp(prefix="bigdata-mcp-config-"))


def write_config(tmp_path: Path, body: str, name: str = "config.toml") -> Path:
    """Write a dedented TOML document and return its path.

    Args:
        tmp_path: Directory to write into.
        body: The TOML, indented however the test finds readable.
        name: Filename within `tmp_path`.

    Returns:
        The path written.
    """
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path
