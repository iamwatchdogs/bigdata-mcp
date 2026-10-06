"""The one and only module allowed to speak to `aiohttp`.

`SPEC.md` §4.2 and §5.1 make this file the reason the redirect policy is
enforceable rather than aspirational. Four properties are structural here, and each
is worth more than a review comment:

1. **It is the only module that passes `allow_redirects`.** `aiohttp` follows
   redirects *by default* — measured, not assumed: it followed a 302 and returned
   200. So the raw session is never exposed, and the rule is pinned by
   `scripts/redirect_gate.py` plus a test rather than by review.
2. **Every hop is re-validated against the configured allowlist.** The check runs
   on the redirect *target*, not only on the initial URL, because a first-hop-only
   check is precisely the one that lets a trusted host redirect to
   `169.254.169.254`. YARN HA needs this: the standby answers 307 pointing at the
   active peer, and refusing that would make the estate unreachable (§3).
3. **HTTPS → HTTP is always refused.** A redirect that downgrades transport
   cannot be inside a policy that exists to protect transport.
4. **No `ssl.SSLContext` is ever built from a default trust source.** Corporate
   endpoints use an internal CA (§3), so one context per configured CA bundle is
   built once, with `check_hostname=True` and `verify_mode=CERT_REQUIRED`, and
   passed explicitly. There is no path here that returns a permissive context.

`TCPConnector(limit_per_host=2)` is set explicitly because aiohttp's `0` means
*unlimited*, which would make every politeness guarantee in §10 decorative.

Redirects are followed **manually**, one hop at a time, rather than by handing a
whole chain to aiohttp. That is what makes per-hop allowlist checking possible at
all: an automatic follower resolves the entire chain before returning, so there is
no point at which an individual hop can be refused.
"""

from __future__ import annotations

import enum
import ssl
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Self
from urllib.parse import urljoin
from urllib.parse import urlsplit

import aiohttp

from bigdata_mcp.errors import BackendUnreachable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

#: §4.2 item 3. A chain longer than this is an error, not a retry.
MAX_REDIRECTS: int = 5

#: §4.2 item 7. aiohttp's own default of 0 means *unlimited*.
LIMIT_PER_HOST: int = 2

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


class Scheme(enum.Enum):
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
        SCHEME_DOWNGRADE: The hop target drops from HTTPS to HTTP.
        TOO_MANY_HOPS: The chain exceeded `MAX_REDIRECTS`.
    """

    NO_LOCATION = "redirect carried no Location header"
    NOT_ALLOWLISTED = "redirect target is not an allowed host"
    SCHEME_DOWNGRADE = "redirect would downgrade HTTPS to HTTP"
    TOO_MANY_HOPS = "redirect chain exceeded the hop limit"


@dataclass(frozen=True, slots=True)
class Response:
    """What a caller gets back. Deliberately not an `aiohttp` type.

    Returning the library's own response would leak its lifetime requirements — the
    session must outlive the body — into every adapter, and an object whose
    correctness depends on `__aexit__` running is not something to hand to code
    three layers away.

    Attributes:
        status: HTTP status code.
        body: Decoded body text, already truncated to the byte cap.
        url: Where the body actually came from, after any redirects. The final
            URL, not the requested one — §8.1's `resolved_params` exists because
            a confidently wrong answer is the worst outcome.
        headers: Response headers with lowercased keys.
        truncated: Whether the byte cap cut the body.
    """

    status: int
    body: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


def build_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    """Build the one `SSLContext` this process uses for a CA bundle.

    Never built from a default trust source. §3 records that corporate endpoints
    use an internal CA, and a library default would either fail on every internal
    host or — worse, if someone "fixed" that by turning verification off — pass
    silently. Nothing here returns a permissive context.

    Args:
        ca_bundle: Path to a PEM bundle, or `None` to load the system roots
            explicitly. Either way the returned context verifies.

    Returns:
        A verifying context with hostname checking on.
    """
    context = ssl.create_default_context(cafile=ca_bundle)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class Session:
    """The wrapper every HTTP source goes through.

    Owns the connection pool, the TLS context, the timeout, the byte cap, and the
    scoped redirect policy. Nothing else in the package constructs an
    `aiohttp.ClientSession`, and `scripts/redirect_gate.py` enforces that.

    Args:
        allowlist: Hosts a request and each of its redirects may reach. Empty
            permits nothing, so a misconfigured session cannot reach out.
        ca_bundle: PEM bundle for the TLS context, or `None` for system roots.
        timeout_s: Per-request budget. Must undercut the client's own (§3).
        max_output_bytes: Hard cap on a response body.
        allow_http: Whether an initial `http://` request is permitted. Off by
            default: §4.2 refuses a scheme downgrade unconditionally, and a
            plain-HTTP initial request is the same weakness without the redirect.
    """

    def __init__(
        self,
        *,
        allowlist: frozenset[str] = frozenset(),
        ca_bundle: str | None = None,
        timeout_s: float = 20.0,
        max_output_bytes: int = 98304,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Store the policy. Nothing is opened until `__aenter__`."""
        self._allowlist = allowlist
        self._headers = dict(headers or {})
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        self._allow_http = allow_http
        self._ssl_context = build_ssl_context(ca_bundle)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Self:
        """Open the underlying session with an explicit connector limit.

        The configured `SSLContext` goes on the connector, which is the only place
        it can take effect. Building it and then handing aiohttp its own default is
        the shape of bug where §3's internal CA silently stops mattering: every
        request still succeeds against a publicly-signed host and fails against
        the one you configured this for, and the failure reads as a broken CA file
        rather than a context that was never attached.

        Returns:
            This instance, so `async with Session(...) as s:` works.
        """
        connector = aiohttp.TCPConnector(
            limit_per_host=LIMIT_PER_HOST, ssl=self._ssl_context
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            cookie_jar=aiohttp.CookieJar(unsafe=False),
            trust_env=False,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying session.

        Args:
            exc_type: The in-flight exception's type, if any.
            exc: The in-flight exception, if any.
            traceback: The in-flight traceback, if any.
        """
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def connector_limit_per_host(self) -> int:
        """The per-host connection limit currently in force.

        `doctor` reports this, because "unlimited" is aiohttp's own default and
        silently unbounded concurrency is exactly what §10 exists to prevent.

        Returns:
            `LIMIT_PER_HOST` while a session is open. Before `__aenter__` there
            is no connector yet, so the configured constant is reported rather
            than a live reading.
        """
        session = self._session
        if session is None or session.connector is None:
            return LIMIT_PER_HOST
        return int(session.connector.limit_per_host)

    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Response:
        """GET `url`, following redirects only within the policy.

        Args:
            url: The absolute URL to fetch.
            headers: Per-request headers, merged over the session defaults.

        Returns:
            The response, with `url` set to where the body actually came from.

        Raises:
            BackendUnreachable: If the endpoint could not be reached, naming the
                URL attempted and the transport fault class. `aiohttp` types never
                escape this method — §5.2 requires a clean tool error, never a
                library exception reaching an adapter.

        A `PermissionError` also escapes, from `_check_initial_scheme` or
        `_next_hop`, but only ever with a message naming the rule that fired.
        """
        current = url
        hops = 0
        while True:
            self._check_initial_scheme(current)
            try:
                merged = {**self._headers, **(headers or {})}
                async with self._raw().get(
                    current, headers=merged, allow_redirects=False
                ) as raw:
                    if raw.status not in REDIRECT_STATUSES:
                        return await self._read(raw, current)
                    if hops >= MAX_REDIRECTS:
                        self._refuse(RedirectRefusal.TOO_MANY_HOPS, current)
                    current = self._next_hop(raw.headers.get("Location"), current)
                    hops += 1
            except aiohttp.ClientError as exc:
                raise BackendUnreachable(
                    endpoint=current, detail=type(exc).__name__
                ) from exc

    @staticmethod
    def _refuse(reason: RedirectRefusal, detail: str) -> None:
        """Refuse a redirect, naming both the rule and the URL.

        Args:
            reason: Which of the four rules fired.
            detail: The URL the rule fired at, plus whatever context helps.

        Raises:
            PermissionError: Always. A named rule beats a bare string: an operator
                reading the log should not have to work out which check refused,
                and a model should not have to retry to discover it.
        """
        message = f"{reason.value}: {detail} (hop limit {MAX_REDIRECTS})"
        raise PermissionError(message)

    def _raw(self) -> aiohttp.ClientSession:
        """Return the live underlying session.

        Returns:
            The `aiohttp` session.

        Raises:
            RuntimeError: If used outside `async with`, which would otherwise leak
                a session nobody closes and emit a warning nobody reads.
        """
        session = self._session
        if session is None:
            message = "Session used outside `async with`; no session is open"
            raise RuntimeError(message)
        return session

    def _check_initial_scheme(self, url: str) -> None:
        """Refuse a URL whose scheme is not permitted.

        Any scheme other than a permitted one is refused outright: `file://` and
        `ftp://` are not schemes this server speaks, and a permissive default here
        is how they would get in.

        Args:
            url: The URL about to be fetched.

        Raises:
            PermissionError: If the scheme is not one this session permits.
        """
        scheme = urlsplit(url).scheme.lower()
        if scheme == Scheme.HTTPS.value or (
            scheme == Scheme.HTTP.value and self._allow_http
        ):
            return
        permitted = "http and https" if self._allow_http else "https only"
        shown = scheme or "(none)"
        message = f"Refusing scheme {shown!r} in {url}; permitted: {permitted}"
        raise PermissionError(message)

    def _next_hop(self, location: str | None, current: str) -> str:
        """Validate one redirect and return the URL to follow.

        This is the rule §4.2 item 4 exists for. The allowlist is checked against
        the *target*, because the initial URL was already trusted and the threat is
        a trusted host pointing somewhere else.

        Takes the `Location` value rather than the response, deliberately: the rule
        needs one header and one URL, and a signature that admits an
        `aiohttp.ClientResponse` would make it impossible to test without either a
        live socket or a fake pretending to be one.

        Args:
            location: The `Location` header value, or `None` when absent.
            current: The URL that produced the redirect, for the error messages.

        Returns:
            The absolute URL of the next hop.

        Every refusal here raises `PermissionError` through `_refuse`, so the
        message names both the rule and the host: an absent `Location`, an
        HTTPS-to-HTTP downgrade, or a target host that is not allowlisted.
        """
        if not location:
            self._refuse(RedirectRefusal.NO_LOCATION, current)
        target = urljoin(current, location)
        was_https = urlsplit(current).scheme.lower() == Scheme.HTTPS.value
        if was_https and urlsplit(target).scheme.lower() == Scheme.HTTP.value:
            self._refuse(RedirectRefusal.SCHEME_DOWNGRADE, f"{current} -> {target}")
        host = _host_of(target)
        if host not in self._allowlist:
            allowed = ", ".join(sorted(self._allowlist)) or "(nothing)"
            self._refuse(
                RedirectRefusal.NOT_ALLOWLISTED,
                f"{host}, from {current} -> {target}. Allowed: {allowed}",
            )
        return target

    async def _read(self, raw: aiohttp.ClientResponse, url: str) -> Response:
        """Read a non-redirect response, enforcing the byte cap.

        Args:
            raw: The response to drain.
            url: The URL the body came from.

        Returns:
            The response, with `truncated` set when the cap cut the body.
        """
        declared = raw.content_length
        if declared is not None and declared > self._max_output_bytes:
            notice = f"[{declared} bytes exceeds the {self._max_output_bytes} byte cap]"
            return Response(
                status=raw.status,
                body=notice,
                url=url,
                headers=_lower(raw.headers),
                truncated=True,
            )
        chunks: list[bytes] = []
        received = 0
        async for chunk in raw.content.iter_chunked(8192):
            remaining = self._max_output_bytes - received
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            received += len(chunks[-1])
        return Response(
            status=raw.status,
            body=b"".join(chunks).decode("utf-8", errors="replace"),
            url=url,
            headers=_lower(raw.headers),
            truncated=received < (declared or received),
        )


def _host_of(url: str) -> str:
    """Return a URL's host, lowercased and without the port.

    Args:
        url: The URL to inspect.

    Returns:
        The host component, or an empty string when the URL carries none.
    """
    netloc = urlsplit(url).netloc
    # Strip userinfo before splitting the port; a URL may carry both.
    authority = netloc.rsplit("@", 1)[-1]
    return authority.split(":", 1)[0].lower()


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    """Lowercase response header names.

    Args:
        headers: An `aiohttp` header mapping.

    Returns:
        A plain dict with lowercased keys, so a lookup cannot miss on case.
    """
    return {key.lower(): value for key, value in headers.items()}


__all__ = [
    "LIMIT_PER_HOST",
    "MAX_REDIRECTS",
    "REDIRECT_STATUSES",
    "BackendUnreachable",
    "RedirectRefusal",
    "Response",
    "Scheme",
    "Session",
    "build_ssl_context",
]
