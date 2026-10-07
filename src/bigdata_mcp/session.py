"""The one and only module allowed to speak to `aiohttp`.

`SPEC.md` §4.2 and §5.1 make this file the reason the redirect policy is
enforceable rather than aspirational. Four properties are structural here, and each
is worth more than a review comment:

1. **It is the only module that passes `allow_redirects`.** `aiohttp` follows
   redirects *by default* — measured, not assumed: it followed a 302 and returned
   200. So the raw session is never exposed, and the rule is pinned by
   `scripts/redirect_gate.py` plus a test rather than by review.
2. **Every request and every hop is validated against the configured allowlist.**
   The check runs on the initial URL *and* on each redirect target, because both
   ends of the chain are policy: a first-hop-only check lets a caller-supplied URL
   reach any host, and a target-only check would let the initial URL bypass a
   posture that promises "empty permits nothing". YARN HA needs this: the standby
   answers 307 pointing at the active peer, and refusing that would make the
   estate unreachable (§3).
3. **HTTPS → HTTP is always refused.** A redirect that downgrades transport
   cannot be inside a policy that exists to protect transport.
4. **No `ssl.SSLContext` is ever built from a default trust source.** Corporate
   endpoints use an internal CA (§3), so one context per configured CA bundle is
   built once, with `check_hostname=True` and `verify_mode=CERT_REQUIRED`, and
   passed to the connector. There is no path here that returns a permissive
   context, and no path that builds one and then does not use it.
5. **A credential does not outlive its origin.** `Authorization`,
   `Proxy-Authorization` and `Cookie` are dropped the moment a hop leaves the
   `(scheme, host, port)` the request started on. See the note on manual
   redirect following below for why this is not aiohttp's job here.

`TCPConnector(limit_per_host=2)` is set explicitly because aiohttp's `0` means
*unlimited*, which would make every politeness guarantee in §10 decorative.

Redirects are followed **manually**, one hop at a time, rather than by handing a
whole chain to aiohttp. That is what makes per-hop allowlist checking possible at
all: an automatic follower resolves the entire chain before returning, so there is
no point at which an individual hop can be refused.

It also gives up one thing aiohttp does for free, and property 5 exists to get it
back: its follower strips `Authorization` when a hop leaves the origin. Following
hops by hand means sending what you were given, so a YARN RM that redirects to an
allowlisted Solr host would hand that host the YARN credential. Being allowlisted
is not a reason to keep a credential — the allowlist is keyed on *host* precisely
so HA peers on different hosts stay reachable, which is the same reason it is too
wide to authenticate to.
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
from bigdata_mcp.errors import MissingCABundleError
from bigdata_mcp.hosts import host_of

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

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
    """
    if not ca_bundle:
        message = "An https session needs an explicit ca_bundle: no PEM path given"
        raise ValueError(message)
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
        allowlist: Hosts the initial request and each of its redirects may reach.
            Empty permits nothing, so a misconfigured session cannot reach out.
        ca_bundle: PEM bundle for the TLS context. When `None`, no context is
            built and every `https://` request raises `MissingCABundleError`:
            this seam never substitutes a default trust source.
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
        """Store the policy. Nothing is opened until `__aenter__`.

        Allowlist entries are normalised through `host_of` so both sides of the
        membership check compare canonical hosts: the request side is always
        `host_of(url)`, so an entry written as `host:port` would otherwise never
        match anything — silently, which for a first-hop check is the difference
        between a policy and a decoration.
        """
        self._allowlist = frozenset(
            host_of(entry) or entry.lower() for entry in allowlist
        )
        self._headers: dict[str, str] = dict(headers or {})
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        self._allow_http = allow_http
        self._ssl_context = (
            build_ssl_context(ca_bundle) if ca_bundle is not None else None
        )
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
            limit_per_host=LIMIT_PER_HOST,
            **({"ssl": self._ssl_context} if self._ssl_context is not None else {}),
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
                library exception reaching an adapter. The session's own timeout
                budget is included: its builtin `TimeoutError` is not an
                `aiohttp.ClientError`, so it is converted here too.

        A `PermissionError` also escapes, from `_check_initial_scheme`,
        `_check_host_allowed`, or `_next_hop`, but only ever with a message naming
        the rule that fired.
        """
        current = url
        first_origin = _origin_of(current)
        self._check_initial_scheme(current)
        self._check_host_allowed(host_of(current), current)
        hops = 0
        while True:
            try:
                merged = self._headers_for(current, first_origin, headers)
                async with self._raw().get(
                    current, headers=merged, allow_redirects=False
                ) as raw:
                    if raw.status not in REDIRECT_STATUSES:
                        return await self._read(raw, current)
                    if hops >= MAX_REDIRECTS:
                        self._refuse(RedirectRefusal.TOO_MANY_HOPS, current)
                    current = self._next_hop(raw.headers.get("Location"), current)
                    hops += 1
            except TimeoutError as exc:
                # The session's own `ClientTimeout(total=...)` raises the builtin
                # `TimeoutError`, which is *not* an `aiohttp.ClientError`. Left
                # uncaught it would escape as a bare library exception — exactly
                # what §5.2 forbids — so it joins the same typed error.
                raise BackendUnreachable(
                    endpoint=current,
                    detail=f"TimeoutError after {self._timeout_s:.1f}s budget",
                ) from exc
            except aiohttp.ClientError as exc:
                raise BackendUnreachable(
                    endpoint=current, detail=type(exc).__name__
                ) from exc

    def _headers_for(
        self,
        current: str,
        first_origin: str,
        per_request: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Return the headers to send to `current`.

        The session's own headers and the caller's per-request headers are merged
        as usual, then the credential-bearing names are dropped as soon as the hop
        leaves the origin the request started on.

        This is the one thing aiohttp's automatic follower would have done for
        free and that following redirects by hand gives up. Its `ClientSession`
        strips `Authorization` on a cross-origin redirect; this loop sends what it
        is given, so without this a YARN RM that redirects to an allowlisted Solr
        host hands that host the YARN `Authorization` header. The allowlist is
        deliberately wider than one origin — HA peers are on different hosts — so
        "allowlisted" is not a reason to keep a credential.

        Args:
            current: The URL about to be fetched.
            first_origin: The `(scheme, host, port)` the request started on.
            per_request: The caller's headers, or `None`.

        Returns:
            The headers for this hop, without credentials if the origin changed.
        """
        merged: dict[str, str] = {**self._headers, **(per_request or {})}
        if _origin_of(current) != first_origin:
            return {
                name: value
                for name, value in merged.items()
                if name.lower() not in CREDENTIAL_HEADERS
            }
        return merged

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
            MissingCABundleError: If an `https://` URL arrives with no CA bundle
                configured, instead of a default trust source being substituted.
        """
        scheme = urlsplit(url).scheme.lower()
        if scheme == Scheme.HTTPS or (scheme == Scheme.HTTP and self._allow_http):
            if scheme == Scheme.HTTPS and self._ssl_context is None:
                message = (
                    f"Cannot start an https request to {url}: no CA bundle is "
                    "configured for this session, and a default trust source is "
                    "not constructed. Pass ca_bundle to Session, or --ca-bundle "
                    "to capture-fixtures."
                )
                raise MissingCABundleError(message)
            return
        permitted = "http and https" if self._allow_http else "https only"
        shown = scheme or "(none)"
        message = f"Refusing scheme {shown!r} in {url}; permitted: {permitted}"
        raise PermissionError(message)

    def _next_hop(self, location: str | None, current: str) -> str:
        """Validate one redirect and return the URL to follow.

        This is the rule §4.2 item 4 exists for. The allowlist is checked against
        the *target*, because the initial URL has already had the identical check
        in `get` — see `_check_host_allowed`, which both hops share.

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
        was_https = urlsplit(current).scheme.lower() == Scheme.HTTPS
        if was_https and urlsplit(target).scheme.lower() == Scheme.HTTP:
            self._refuse(RedirectRefusal.SCHEME_DOWNGRADE, f"{current} -> {target}")
        self._check_host_allowed(host_of(target), f"{current} -> {target}")
        return target

    def _check_host_allowed(self, host: str, detail: str) -> None:
        """Refuse a host that is not on the allowlist.

        Shared by the first request and every redirect hop. Hoisted out of
        `_next_hop` because the first request needs the identical check: the
        allowlist is what a caller configures instead of trusting itself, and an
        initial request that skips it makes `Session(allowlist=frozenset())` — the
        documented "permits nothing" posture — reachable by any URL the caller
        happens to pass. Raises through `_refuse`, which names the rule and the
        URL.

        Args:
            host: The hostname the request would reach.
            detail: URL context for the refusal message.
        """
        if host not in self._allowlist:
            allowed = ", ".join(sorted(self._allowlist)) or "(nothing)"
            self._refuse(
                RedirectRefusal.NOT_ALLOWLISTED,
                f"{host}, from {detail}. Allowed: {allowed}",
            )

    async def _read(self, raw: aiohttp.ClientResponse, url: str) -> Response:
        """Read a non-redirect response, enforcing the byte cap.

        `truncated` is set by the cap actually firing, not by comparing the bytes
        received against a `Content-Length`. A chunked response has no declared
        length, so the comparison reads `received < received` and reports False on
        a body that was cut in half — which is the one case where a caller most
        needs to be told. A declared length is still checked as well, because a
        server that under-declares should not be believed either.

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
        cut = False
        async for chunk in raw.content.iter_chunked(8192):
            remaining = self._max_output_bytes - received
            if remaining <= 0:
                cut = True
                break
            if len(chunk) > remaining:
                cut = True
            chunks.append(chunk[:remaining])
            received += len(chunks[-1])
        return Response(
            status=raw.status,
            body=b"".join(chunks).decode("utf-8", errors="replace"),
            url=url,
            headers=_lower(raw.headers),
            truncated=cut or (declared is not None and received < declared),
        )


def _origin_of(url: str) -> str:
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


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    """Lowercase response header names.

    Args:
        headers: An `aiohttp` header mapping.

    Returns:
        A plain dict with lowercased keys, so a lookup cannot miss on case.
    """
    return {key.lower(): value for key, value in headers.items()}


__all__ = [
    "CREDENTIAL_HEADERS",
    "LIMIT_PER_HOST",
    "MAX_REDIRECTS",
    "REDIRECT_STATUSES",
    "BackendUnreachable",
    "MissingCABundleError",
    "RedirectRefusal",
    "Response",
    "Scheme",
    "Session",
    "build_ssl_context",
]
