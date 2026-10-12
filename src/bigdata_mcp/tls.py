"""The one `SSLContext` this process uses for a CA bundle.

Split out of `session.py` when that module crossed Codacy's 500-line limit. Never
built from a default trust source: §3's environment is corporate, and a library
default would either fail on every internal host or — worse, if someone "fixed"
that by turning verification off — pass silently. Nothing here returns a permissive
context, and a bundle that cannot be read is reported as a config fault so the
capture CLI answers the documented exit 5 rather than a traceback.
"""

from __future__ import annotations

import ssl

from bigdata_mcp.errors import ConfigError


def build_ssl_context(ca_bundle: str) -> ssl.SSLContext:
    """Build the one `SSLContext` this process uses for a CA bundle.

    Never built from a default trust source: §3's environment is corporate, and
    a library default would either fail on every internal host or — worse, if
    someone "fixed" that by turning verification off — pass silently. Nothing
    here returns a permissive context.

    Args:
        ca_bundle: Path to a PEM bundle. Required; `create_default_context`
            with no path loads the library's default trust roots, which is the
            path §3 forbids.

    Returns:
        A verifying context with hostname checking on.

    Raises:
        ValueError: If `ca_bundle` is empty.
        ConfigError: If the bundle cannot be read or parsed — a missing path or
            a file that is not PEM. Surfaced as a config fault so callers that
            treat `BigDataMcpError` as a refusal (the capture CLI's exit 5) do
            not emit a traceback instead.
    """
    if not ca_bundle:
        message = "An https session needs an explicit ca_bundle: no PEM path given"
        raise ValueError(message)
    try:
        context = ssl.create_default_context(cafile=ca_bundle)
    except OSError as exc:
        # A missing file raises `FileNotFoundError` and an unreadable or non-PEM
        # one raises `ssl.SSLError`; both are `OSError` subclasses. Left as-is
        # they surface as a traceback from the CLI's capture path instead of the
        # documented refusal — a configuration fault is not a crash.
        message = f"Cannot load CA bundle {ca_bundle}: {exc}"
        raise ConfigError(message) from exc
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context
