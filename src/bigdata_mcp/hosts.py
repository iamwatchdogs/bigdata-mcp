"""Host extraction from a URL, in one place.

Two callers need the same answer and neither owns it: `config_models` builds the
redirect allowlist from configured `base_urls`, and `session` checks each redirect
target against it. When they disagreed about what a host is, the allowlist and the
check would disagree too — and the symptom would be a redirect refused for a reason
nobody could name.

The reason this is its own module and not a helper in either caller is that
importing it from `session` would make config depend on the HTTP seam, and
importing it from `config_models` would make the seam depend on config loading.
Neither direction is right, and the duplication that motivated moving it here was
the tell.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

__all__ = ["host_of"]


def host_of(url: str) -> str:
    """Return a URL's host, lowercased and without the port.

    `urlsplit(...).hostname` rather than splitting the netloc on `":"`, because an
    IPv6 literal is *all* colons: `https://[::1]:8088` split on the first colon
    yields `[`, which matches no configured host and no DNS name. The failure is
    safe — the redirect is refused — but it is refused for a reason that looks like
    a misconfiguration rather than a parser that cannot count colons, and an estate
    on IPv6 loses its HA redirects for no reason an operator could find.

    `hostname` also handles the userinfo case (`https://user:pw@host/`) that a
    manual split has to handle separately and therefore eventually gets wrong.

    An IPv6 host is normalised to its compressed form. `::1` and the long
    `0:0:0:0:0:0:0:1` name the same machine but are different strings, so an
    allowlist holding one spelling refuses a redirect written in the other — the
    identical failure as the colon split, one layer up, and one that survives any
    amount of correct parsing.

    Args:
        url: The URL to inspect. A bare host with no scheme is accepted, because
            `base_urls` in a config file may be written either way.

    Returns:
        The host component, lowercased, without userinfo or port. An empty string
        when the URL carries no host — which is not the same as a host that fails
        to match, and callers decide differently for each.
    """
    parts = urlsplit(url if "//" in url else f"//{url}")
    return _canonical(hostname=parts.hostname)


def _canonical(hostname: str | None) -> str:
    """Lowercase a host and compress it if it is an IPv6 address.

    Args:
        hostname: The host `urlsplit` reported, or `None` for a URL with no host.

    Returns:
        The normalised host, or an empty string when there was none.
    """
    if hostname is None:
        return ""
    host = hostname.lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host
