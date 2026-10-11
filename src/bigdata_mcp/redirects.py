"""§4.2's redirect vocabulary: the schemes, the named refusals, and the helpers.

Split out of `session.py` when that module crossed Codacy's 500-line limit. What
moved is what a caller needs to *name* rather than what it needs to *use* — the
two enums, the hop bound, the credential-bearing header names, and the two
functions that decide where a hop goes and what a request may carry across one.

Every refusal here raises `PermissionError` through `refuse_redirect`, which names
the rule that fired. A named rule beats a bare string: an operator reading the log
should not have to work out which check refused, and a model should not have to
retry to discover it.

Both names are re-exported from `bigdata_mcp.session`, because that is where
callers and tests already look for them.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING
from typing import NoReturn
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

#: §4.2 item 3. A chain longer than this is an error, not a retry.
MAX_REDIRECTS: int = 5

#: §4.2 item 7. aiohttp's own default of 0 means *unlimited*.
LIMIT_PER_HOST: int = 2

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

#: Header names that carry a credential, dropped when a hop leaves the origin the
#: request started on. §4.2 item 2's per-hop allowlist is what makes cross-origin
#: redirects possible at all, so the credential has to be what is re-checked.
CREDENTIAL_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
})


class Scheme(enum.StrEnum):
    """The two schemes this seam speaks.

    Attributes:
        HTTP: Refused as a redirect target from an HTTPS request, and refused
            entirely as an initial scheme unless explicitly permitted.
        HTTPS: The only scheme a request may use when TLS is required.
    """

    HTTP = "http"
    HTTPS = "https"


class RedirectRefusal(enum.Enum):
    """Why a redirect was not followed.

    An enum rather than loose message strings, so the reason is a value a test can
    assert on and a caller can branch on, and so the wording lives in one place.

    Attributes:
        NO_LOCATION: A 3xx with no `Location`. A broken server rather than a
            policy call, but still not something to follow.
        NOT_ALLOWLISTED: The hop target is not a configured host.
        MALFORMED_TARGET: The hop target is not a URL this session can parse.
        SCHEME_DOWNGRADE: The hop target drops from HTTPS to HTTP.
        TOO_MANY_HOPS: The chain exceeded `MAX_REDIRECTS`.
    """

    NO_LOCATION = "redirect carried no Location header"
    NOT_ALLOWLISTED = "redirect target is not an allowed host"
    MALFORMED_TARGET = "redirect target is not a URL this session can check"
    SCHEME_DOWNGRADE = "redirect would downgrade HTTPS to HTTP"
    TOO_MANY_HOPS = "redirect chain exceeded the hop limit"


def refuse_redirect(reason: RedirectRefusal, detail: str) -> NoReturn:
    """Refuse a redirect, naming both the rule and the URL.

    Args:
        reason: Which of the rules fired.
        detail: The URL the rule fired at, plus whatever context helps.

    Raises:
        PermissionError: Always. A named rule beats a bare string: an operator
            reading the log should not have to work out which check refused,
            and a model should not have to retry to discover it.
    """
    message = f"{reason.value}: {detail} (hop limit {MAX_REDIRECTS})"
    raise PermissionError(message)


def origin_of(url: str) -> str:
    """Return a URL's origin as `(scheme, host, port)`, lowercased.

    Compared as a triple rather than as a string prefix: two URLs differing only
    by a default port, or by the *case* of the host, are the same origin, and a
    prefix comparison would treat `http://rm1` and `http://rm1.evil` as related
    while treating `http://rm1:80` and `http://rm1` as unrelated.

    Args:
        url: The URL to inspect.

    Returns:
        The origin, or an empty string when the URL carries no host.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    default = 443 if parts.scheme.lower() == Scheme.HTTPS else 80
    return f"{parts.scheme.lower()}://{host}:{port or default}"


def lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Lowercase response header names.

    `origin_of` parses a `Location`, which is attacker input; this is the other
    half — a header lookup that cannot miss on case.

    Args:
        headers: An `aiohttp` header mapping.

    Returns:
        A plain dict with lowercased keys, so a lookup cannot miss on case.
    """
    return {key.lower(): value for key, value in headers.items()}
