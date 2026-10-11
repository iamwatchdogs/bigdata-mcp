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
   `(scheme, host, port)` the request started on.

`TCPConnector(limit_per_host=2)` is set explicitly because aiohttp's `0` means
*unlimited*, which would make every politeness guarantee in §10 decorative. The
names that decide a hop live in `bigdata_mcp.redirects`, and the one `SSLContext`
this process builds lives in `bigdata_mcp.tls`.

Redirects are followed **manually**, one hop at a time, rather than by handing a
whole chain to aiohttp. That is what makes per-hop allowlist checking possible at
all: an automatic follower resolves the entire chain before returning, so there is
no point at which an individual hop can be refused.

It also gives up one thing aiohttp does for free, and property 5 exists to get it
back: its follower strips `Authorization` when a hop leaves the origin. Following
hops by hand means sending what you were given, so a YARN RM that redirects to an
allowlisted Solr host would hand that host the YARN credential. Being allowlisted
is keyed on *host*, precisely so HA peers stay reachable — too wide to
authenticate to.
"""

from __future__ import annotations

import asyncio
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
from bigdata_mcp.redirects import CREDENTIAL_HEADERS
from bigdata_mcp.redirects import LIMIT_PER_HOST
from bigdata_mcp.redirects import MAX_REDIRECTS
from bigdata_mcp.redirects import REDIRECT_STATUSES
from bigdata_mcp.redirects import RedirectRefusal
from bigdata_mcp.redirects import Scheme
from bigdata_mcp.redirects import lower_headers as _lower
from bigdata_mcp.redirects import origin_of as _origin_of
from bigdata_mcp.redirects import refuse_redirect
from bigdata_mcp.tls import build_ssl_context

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType


@dataclass(frozen=True, slots=True)
class Response:
    """One fetched page: status, body, final URL, headers, truncation flag."""

    status: int
    body: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


class Session:
    """The wrapper every HTTP source goes through.

    Owns the connection pool, the TLS context, the timeout, and the byte cap.
    Nothing else in the package constructs an `aiohttp.ClientSession`, and
    `scripts/redirect_gate.py` enforces that.

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

        A `BackendUnreachable` escapes when the endpoint cannot be reached —
        including a per-hop or whole-chain timeout, whose builtin `TimeoutError` is
        not an `aiohttp.ClientError`. No `aiohttp` type leaves this method (§5.2),
        and both faults go through `_as_unreachable`, which names the endpoint and
        the fault class. A `PermissionError` also escapes from the three policy
        checks, always with the rule that fired. Both are named in prose rather
        than under `Raises:` because they are raised by callees, not by this frame.
        """
        current = url
        self._check_initial(current)
        first_origin = _origin_of(current)
        hops = 0
        # One budget for the whole chain, not one per hop: aiohttp applies the
        # session's `ClientTimeout` to each `.get()`, so six hops at 20 s was 120 s
        # against §3's 60 s client budget -- the HA-standby path the estate is slow
        # on already. Its expiry raises the same `TimeoutError` the arms convert.
        try:
            async with asyncio.timeout(self._timeout_s):
                while True:
                    try:
                        self._check_https_has_ca_bundle(current)
                        merged = self._headers_for(current, first_origin, headers)
                        async with self._raw().get(
                            current, headers=merged, allow_redirects=False
                        ) as raw:
                            if raw.status not in REDIRECT_STATUSES:
                                return await self._read(raw, current)
                            if hops >= MAX_REDIRECTS:
                                refuse_redirect(RedirectRefusal.TOO_MANY_HOPS, current)
                            current = self._next_hop(
                                raw.headers.get("Location"), current
                            )
                            hops += 1
                    except (TimeoutError, aiohttp.ClientError) as exc:
                        raise self._as_unreachable(current, exc) from exc
        except TimeoutError as exc:
            # The chain budget spent. Escaping the loop's own arm uncaught, a bare
            # `asyncio` exception would leave this method -- §5.2 admits no library
            # type past it, and a whole chain over budget is a `BackendUnreachable`
            # with the same message shape a per-hop one carries.
            raise self._as_unreachable(current, exc) from exc

    def _check_initial(self, url: str) -> str:
        """Classify the URL the caller asked for, before any hop is taken.

        Hoisted out of `get` for the same reason the hop loop was: that function
        had grown past the length this repository's gate allows. `host_of` and
        `_origin_of` both parse, and `urlsplit` raises `ValueError` on a malformed
        IPv6 literal — an opening bracket with no closing one — so
        `capture-fixtures --url` with such a URL would print a traceback rather
        than exit 5. Same classification `_next_hop` gives a redirect target.

        Args:
            url: The absolute URL the caller asked for.

        Returns:
            The host of the initial URL, for the allowlist check.

        A `PermissionError` escapes through `_refuse` when the URL does not parse
        — a bare library exception reaching a caller is the failure §5.2 exists to
        prevent. Named in prose rather than under `Raises:` because it is raised
        by that callee, not by this frame.
        """
        try:
            initial_host = host_of(url)
        except ValueError:
            refuse_redirect(RedirectRefusal.MALFORMED_TARGET, url)
        self._check_initial_scheme(url)
        self._check_host_allowed(initial_host, url)
        return initial_host

    def _as_unreachable(self, current: str, exc: Exception) -> BackendUnreachable:
        """Convert a transport fault into the typed error §5.2 requires.

        Deliberately not per-`except`-arm inline construction: the hop loop had
        already grown past the length this repository's complexity gate allows,
        and the two arms differed only in the `detail` they built. Combining them
        also means a third transport fault joins one dispatch instead of adding a
        fourth arm to a `while True`.

        `TimeoutError` is in the union because the session's own
        `ClientTimeout(total=...)` raises the builtin, which is *not* an
        `aiohttp.ClientError` — left unconverted it would escape as a bare library
        exception, exactly what §5.2 forbids.

        Args:
            current: The URL being attempted, named in the error.
            exc: The fault the hop loop caught.

        Returns:
            A `BackendUnreachable` naming the endpoint and the fault class.
        """
        if isinstance(exc, TimeoutError):
            return BackendUnreachable(
                endpoint=current,
                detail=f"TimeoutError after {self._timeout_s:.1f}s budget",
            )
        return BackendUnreachable(endpoint=current, detail=type(exc).__name__)

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

    def _check_https_has_ca_bundle(self, url: str) -> None:
        """Check that HTTPS URLs have a CA bundle configured.

        Args:
            url: The URL to check.

        Raises:
            MissingCABundleError: If an `https://` URL arrives with no CA bundle
                configured.
        """
        scheme = urlsplit(url).scheme.lower()
        if scheme == Scheme.HTTPS and self._ssl_context is None:
            message = (
                f"Cannot start an https request to {url}: no CA bundle is "
                "configured for this session, and a default trust source is "
                "not constructed. Pass ca_bundle to Session, or --ca-bundle "
                "to capture-fixtures."
            )
            raise MissingCABundleError(message)

    def _check_initial_scheme(self, url: str) -> None:
        """Refuse a URL whose scheme is not permitted.

        Any scheme other than a permitted one is refused outright: `file://` and
        `ftp://` are not schemes this server speaks, and a permissive default here
        is how they would get in.

        Args:
            url: The URL about to be fetched.

        Raises:
            PermissionError: If the scheme is not one this session permits.

        A `MissingCABundleError` also escapes, from `_check_https_has_ca_bundle`,
        so the CA-bundle rule is stated once and enforced on every hop rather than
        only on the first URL. Naming it in prose rather than under `Raises:` is
        also what `DOC502` wants: it is raised by the callee, not by this method.
        """
        scheme = urlsplit(url).scheme.lower()
        if scheme == Scheme.HTTPS or (scheme == Scheme.HTTP and self._allow_http):
            self._check_https_has_ca_bundle(url)
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
        HTTPS-to-HTTP downgrade, a target that does not parse as a URL, or a host
        that is not allowlisted. Anything a caller's own handler can produce is a
        `Location` from another system, so "unparseable" is a normal outcome to
        classify and not an exception to propagate — §5.2 admits no library type
        past this point.
        """
        if not location:
            refuse_redirect(RedirectRefusal.NO_LOCATION, current)
        try:
            target = urljoin(current, location)
            was_https = urlsplit(current).scheme.lower() == Scheme.HTTPS
            is_http = urlsplit(target).scheme.lower() == Scheme.HTTP
            allowed_host = host_of(target)
        except ValueError:
            # `urljoin` and `host_of` both raise `ValueError` on a malformed
            # IPv6 literal -- a `[` with no `]`, or a stray `]`. Such a `Location`
            # is written by another system, so it is a normal outcome to classify
            # and not an exception to propagate (§5.2 admits no library type past
            # this point).
            refuse_redirect(
                RedirectRefusal.MALFORMED_TARGET, f"{current} -> {location}"
            )
        if was_https and is_http:
            refuse_redirect(RedirectRefusal.SCHEME_DOWNGRADE, f"{current} -> {target}")
        self._check_host_allowed(allowed_host, f"{current} -> {target}")
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
            refuse_redirect(
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
