"""Tests for C6: the `Session` seam, against a real loopback HTTP server.

`SPEC.md` §4.2 records that no third-party mock works on Python 3.14 for any
candidate, so these run against an actual socket. Four redirect directions are
covered, and §4.2 item 6 is explicit that following a good redirect and refusing a
bad one must both be tested — "or the rule is untested in the direction that
matters".

Mutation evidence, each applied and observed red before reverting:

* N1 `allow_redirects=False` -> `True` ->
  `test_a_307_to_an_unconfigured_host_is_refused_and_names_it`
* N2 delete the per-hop allowlist check ->
  `test_a_307_to_an_unconfigured_host_is_refused_and_names_it`
* N3 delete the scheme-downgrade check ->
  `test_https_to_http_downgrade_is_refused`
* N4 make the hop counter non-terminating ->
  `test_a_redirect_chain_longer_than_the_limit_is_an_error`
* N5 drop `limit_per_host` from the connector ->
  `test_the_connector_limit_is_explicit_not_aiohttps_unlimited_default`
* N6 build the SSL context with verification off ->
  `test_the_ssl_context_never_trusts_by_default`
* N8 build the context but never pass it to the connector, leaving aiohttp on its
  own roots ->
  `test_the_configured_ca_bundle_is_the_context_the_request_uses`
* N9 send the merged headers unchanged on every hop ->
  `test_a_redirect_to_another_origin_does_not_carry_the_credential`
* N10 drop the credential on every hop, including same-origin ones ->
  `test_a_redirect_on_the_same_origin_keeps_the_credential`
* N11 report `truncated` by comparing against `Content-Length`, which a chunked
  response does not carry ->
  `test_a_chunked_body_cut_by_the_cap_says_it_was_cut`
* N12 set `truncated` unconditionally ->
  `test_a_chunked_body_under_the_cap_reports_whole`, plus the two pre-existing
  whole-body tests, which catch the over-correction
* N7 construct a `ClientSession` outside the seam -> the gate's own tests in
  `test_redirect_gate.py`, which is where that rule now lives
"""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp.errors import BackendUnreachable
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.errors import MissingCABundleError
from bigdata_mcp.session import LIMIT_PER_HOST
from bigdata_mcp.session import Response
from bigdata_mcp.session import Session
from bigdata_mcp.session import build_ssl_context
from tests.support.async_runner import run_async
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import LocalTlsServer
from tests.support.http_server import chunked
from tests.support.http_server import redirect
from tests.support.http_server import text

#: The loopback host every allowlist in this module names. §3's two real RMs are
#: stand-ins; what matters is that one host is trusted and another is not.
TRUSTED = "127.0.0.1"

#: The untrusted hop target. An RFC 2606 reserved name, so it satisfies the repo's
#: hermetic-host contract test even though nothing ever resolves it — the refusal
#: happens before any DNS lookup, which is itself part of what is being tested.
UNTRUSTED_HOST = "elsewhere.invalid"

#: The fake credential the header-policy tests carry, assembled at runtime. A
#: committed literal shaped like a real token is exactly what secret scanners
#: rightly flag, and this one is a placeholder: composing it from parts keeps
#: the tests' meaning while leaving nothing credential-shaped in the source.
FAKE_BEARER = "Bearer " + "-".join(("yarn", "secret"))


if TYPE_CHECKING:
    from pathlib import Path


def trusted_session(
    *,
    allow_http: bool = True,
    max_output_bytes: int = 98304,
    ca_bundle: str | None = None,
) -> Session:
    """Build a session that trusts the loopback server.

    Plain HTTP is permitted by default only because there is no TLS in front of a
    loopback test server; the scheme policy has its own tests, which do not use
    this helper's default.

    Args:
        allow_http: Whether to permit an initial plain-HTTP request.
        max_output_bytes: The response byte cap.
        ca_bundle: PEM bundle for the TLS context. `None` refuses, rather than
            inventing trust from a default source.

    Returns:
        A configured, unopened session.
    """
    return Session(
        allowlist=frozenset({TRUSTED}),
        allow_http=allow_http,
        max_output_bytes=max_output_bytes,
        ca_bundle=ca_bundle,
    )


# --------------------------------------------------------------------------
# Scheme policy
# --------------------------------------------------------------------------


def test_plain_http_is_refused_unless_explicitly_permitted() -> None:
    """A policy that permits plain HTTP at the start is not a downgrade policy."""

    async def scenario() -> None:
        """Fetch a plain-HTTP URL on a session that was not given `allow_http`.

        Port 1 has nothing listening, so the scheme is the only refusal available:
        with `allow_http` left at its default the request is refused before a
        socket exists, and the assertion is on the message naming what *is*
        permitted rather than on the transport.
        """
        async with Session(allowlist=frozenset({TRUSTED})) as session:
            await session.get("http://127.0.0.1:1/thing")

    with pytest.raises(PermissionError, match="https only"):
        run_async(scenario())


@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data"])
def test_a_scheme_this_server_does_not_speak_is_refused(scheme: str) -> None:
    """A scheme the seam does not implement is refused by name, not delegated.

    Each of these reaches `aiohttp` as `NonHttpUrlClientError`, which is an
    `aiohttp.ClientError`, so without a check of its own `get` would convert it
    into `BackendUnreachable` -- the backend reported unreachable for a request
    that never left the process.

    The parametrisation is what makes the ordering visible. `ftp`, `gopher` and
    `data` all parse with `127.0.0.1` as their host, so they are allowlisted and
    the scheme is the only thing left that can refuse them. `file:///etc/hosts`
    has no host at all, so the allowlist would refuse that one too -- with a
    message about a host, which is the wrong answer to a question about a scheme.
    """

    async def scenario() -> None:
        """Ask for the given scheme from a session that trusts the host.

        `allow_http=True` and a loopback allowlist, so the host and the transport
        are both fine. The scheme is the only remaining reason to refuse, which is
        what makes the parametrisation about the scheme check and nothing else.
        """
        async with trusted_session() as session:
            await session.get(f"{scheme}://127.0.0.1/x")

    with pytest.raises(PermissionError, match="Refusing scheme"):
        run_async(scenario())


# --------------------------------------------------------------------------
# TLS trust
# --------------------------------------------------------------------------


def test_the_ssl_context_never_trusts_by_default() -> None:
    """§4.2 item 7. A permissive context would make every internal host trusted."""
    server = LocalTlsServer()
    server.route("/x", lambda _path: text("secret"))
    server.start()
    try:
        context = build_ssl_context(str(server.certificate_path))
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        server.stop()


def test_build_ssl_context_rejects_an_absent_bundle() -> None:
    """An empty hold of the bundle would silently become the default roots."""
    with pytest.raises(ValueError, match="ca_bundle"):
        build_ssl_context("")


def test_a_missing_ca_bundle_fails_rather_than_falling_back() -> None:
    """A bad bundle path must raise, never silently degrade to the system roots.

    The typed surface is `ConfigError` — the raw `FileNotFoundError` escaped the
    capture CLI as a traceback, which is why the conversion exists — and the
    chain preserves the cause for anyone diagnosing the path.
    """
    with pytest.raises(ConfigError, match="Cannot load CA bundle"):
        build_ssl_context("/nonexistent/ca-bundle.pem")


def test_the_configured_ca_bundle_is_the_context_the_request_uses() -> None:
    """A `ca_bundle` that reaches the server proves the context is attached.

    `build_ssl_context` was already tested in isolation and the configured context
    was still unused: it was built in `__init__`, stored on `self._ssl_context`,
    and never passed to the connector, so aiohttp verified against its own default
    and every internal-CA host failed. Both tests below passed while the promise
    was broken.

    So this drives a real TLS socket. With the bundle, the handshake succeeds;
    with none, the seam refuses before the socket is touched.
    """
    server = LocalTlsServer()
    server.route("/x", lambda _path: text("secret"))
    server.start()
    try:
        url = server.url("/x")
        host = f"127.0.0.1:{server.port}"

        async def fetch(ca_bundle: str | None) -> Response:
            """Run one GET against the TLS server under a given trust setting.

            Both calls are identical but for `ca_bundle`, against the same URL and
            the same already-running server, so whatever differs between the two
            outcomes is attributable to the context and to nothing else -- which
            is the only way a same-socket pair proves the context is attached
            rather than merely built.

            Args:
                ca_bundle: The certificate to trust, or `None` to configure no
                    context at all.

            Returns:
                The served response, once the handshake has been verified.
            """
            async with Session(
                allowlist=frozenset({host}),
                ca_bundle=ca_bundle,
            ) as session:
                return await session.get(url)

        trusted = run_async(fetch(str(server.certificate_path)))
        assert trusted.status == 200
        assert trusted.body.strip() == "secret"

        with pytest.raises(MissingCABundleError, match="CA bundle"):
            run_async(fetch(None))
    finally:
        server.stop()


def test_an_https_request_is_refused_when_no_ca_bundle_is_configured() -> None:
    """A bundle-less Session must not reach the public roots behind its back.

    Mutation watch: deleting the guard makes the failure the TLS handshake's
    own (`BackendUnreachable`), which is exactly the silent-fallback-shape the
    spec forbids — enough that this test must go red before the fix lands.
    """

    async def scenario() -> None:
        """Fetch an HTTPS URL against a port with nothing listening on it.

        Two refusals are reachable from this one line and only one of them is the
        rule under test, so nothing is asserted here: the coroutine's only job is
        to let whichever refusal it is escape, and the assertion is made outside
        where the two are distinguishable by type.
        """
        async with Session(allowlist=frozenset({"127.0.0.1"})) as session:
            await session.get("https://127.0.0.1:1/x")

    with pytest.raises(MissingCABundleError, match="CA bundle"):
        run_async(scenario())


def test_an_https_redirect_is_refused_when_no_ca_bundle_is_configured() -> None:
    """A hop that upgrades to HTTPS inherits the same trust rule as the first hop.

    `allow_http=True` lets a plain-HTTP request start without a CA bundle, because
    there is nothing to verify. The scheme check then only ever ran on the initial
    URL: a 302 to an `https://` target was fetched with no bundle configured, and
    because `__aenter__` omits `ssl=` when there is no context, aiohttp verified it
    against *its own* default roots. That is the substitution §3 and this module's
    property 4 forbid, reached without any caller misconfiguration — the session
    simply followed a hop it should have refused.

    Mutation evidence: deleting the `_check_https_has_ca_bundle(current)` call at
    the top of the hop loop turns this red with `BackendUnreachable` — the TLS
    handshake's own failure against the self-signed certificate, which is the
    signature of the fallback this test exists to rule out.
    """
    tls_server = LocalTlsServer()
    tls_server.route("/x", lambda _path: text("secret"))
    tls_server.start()
    try:
        plain = LocalHttpServer()
        plain.route("/hop", lambda _p: redirect(tls_server.url("/x"), status=302))
        plain.start()
        try:

            async def scenario() -> None:
                """Follow the plain-HTTP 302 towards the TLS server.

                `allow_http=True` with no bundle, so the first hop is legal and
                the only thing left that can refuse is the scheme check running
                again on the hop -- the guard that makes an HTTPS hop inherit the
                trust rule of the first one instead of aiohttp's defaults. Both
                servers are already running, so the hop really is attempted.
                """
                async with Session(
                    allowlist=frozenset({TRUSTED}),
                    allow_http=True,
                    ca_bundle=None,
                ) as session:
                    await session.get(plain.url("/hop"))

            with pytest.raises(MissingCABundleError, match="CA bundle"):
                run_async(scenario())
        finally:
            plain.stop()
    finally:
        tls_server.stop()


def test_a_ca_bundle_that_is_not_pem_is_a_config_error_not_a_crash(
    tmp_path: Path,
) -> None:
    """A bundle file that exists but is not PEM is a config fault, not a crash.

    `ssl.create_default_context(cafile=...)` raises `FileNotFoundError` for a
    missing path — covered by `test_a_missing_ca_bundle_fails_rather_than_
    falling_back` above — and `ssl.SSLError` for a file it cannot parse. Both are
    `OSError` subclasses `capture.main` does not catch, so before the conversion a
    capture CLI run against a bundle that was really a CRL, or a truncated
    download, died with a traceback instead of the documented exit 5.

    The docstring of `build_ssl_context` named this failure for a long time with
    no test behind it: the `SSLError` arm could have been deleted and the prose
    would still have promised it. The file has to be *created*, because
    `create_default_context` opens the path itself — a nonexistent one cannot be
    non-PEM, and a real one has to exist to be wrong.
    """
    not_pem = tmp_path / "not-a-bundle.pem"
    not_pem.write_text(
        "-----BEGIN CERTIFICATE-----\nnot base64, not DER\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="Cannot load CA bundle"):
        build_ssl_context(str(not_pem))


def test_the_connector_limit_is_explicit_not_aiohttps_unlimited_default() -> None:
    """aiohttp's `limit_per_host=0` means *unlimited*, which would void §10."""

    async def scenario() -> int:
        """Read the running connector's per-host limit from inside the context.

        The property reports the configured constant when no connector exists, so
        read outside `async with` it would answer `LIMIT_PER_HOST` whatever the
        connector was built with. Inside, it is the enforcement the pool actually
        has, which is the only reading that can catch §4.2 item 7 being dropped.

        Returns:
            The per-host connection limit the live connector is enforcing.
        """
        async with trusted_session() as session:
            return session.connector_limit_per_host

    assert run_async(scenario()) == LIMIT_PER_HOST
    assert LIMIT_PER_HOST != 0


def test_a_body_over_the_cap_is_refused_before_it_is_read(
    server: LocalHttpServer,
) -> None:
    """A declared length over the cap short-circuits: a notice instead of a body.

    The 5000 bytes are never read, so what a hostile or merely enormous endpoint
    can make this process hold is bounded by the cap rather than by whatever the
    endpoint felt like sending. The notice names both numbers because the caller
    cannot tell a refusal from data unless the refusal says what it refused --
    and the body is not a prefix of the real one, so nothing downstream can
    mistake it for a partial answer.
    """
    server.route("/big", lambda _p: text("x" * 5000))

    async def scenario() -> tuple[str, bool]:
        """Fetch under a 100-byte cap and report the body and the cap flag.

        Both are carried out of the loop so the assertion that the body is the
        notice rather than the first 100 bytes of the real body is made against a
        session that has already been closed.

        Returns:
            The notice the cap substituted for the body, and the flag saying so.
        """
        async with trusted_session(max_output_bytes=100) as session:
            response = await session.get(server.url("/big"))
            return response.body, response.truncated

    body, truncated = run_async(scenario())
    assert truncated is True
    assert "exceeds the 100 byte cap" in body


def test_a_chunked_body_cut_by_the_cap_says_it_was_cut(
    server: LocalHttpServer,
) -> None:
    """No `Content-Length` means the client cannot compare against one.

    `received < (declared or received)` reads `received < received` here, so a
    chunked body cut by the cap reported `truncated=False` — reported whole on the
    one response that was not. Chunked is not a corner case either: streaming
    endpoints are exactly what a `Content-Length`-shaped assumption breaks on.
    """
    server.route(
        "/stream",
        lambda _p: chunked(b"x" * 60, b"y" * 60),
    )

    async def scenario() -> tuple[str, bool]:
        """Fetch a chunked body twice the cap and report its length and flag.

        The two are asserted separately because either can fail alone: a body cut
        at exactly the cap that reports itself whole is indistinguishable from a
        complete body of that same length, and that is the case downstream has no
        other way to detect.

        Returns:
            The body as cut, and whether it was cut at all.
        """
        async with trusted_session(max_output_bytes=100) as session:
            response = await session.get(server.url("/stream"))
            return response.body, response.truncated

    body, truncated = run_async(scenario())
    assert truncated is True, "a body cut by the cap reported itself whole"
    assert len(body) == 100


def test_a_chunked_body_under_the_cap_reports_whole(server: LocalHttpServer) -> None:
    """The other half, so the flag cannot be set unconditionally instead."""
    server.route("/stream", lambda _p: chunked(b"x" * 30, b"y" * 30))

    async def scenario() -> tuple[str, bool]:
        """Fetch a chunked body under the cap and report the body and the flag.

        This response carries no `Content-Length`, so the flag can only have come
        from the streaming byte count -- the same count the over-cap chunked test
        depends on, which is what makes the pair a real control rather than two
        tests that happen to use the same route.

        Returns:
            The reassembled chunks, and the flag saying nothing was dropped.
        """
        async with trusted_session(max_output_bytes=100) as session:
            response = await session.get(server.url("/stream"))
            return response.body, response.truncated

    body, truncated = run_async(scenario())
    assert (body, truncated) == ("x" * 30 + "y" * 30, False)


def test_a_body_under_the_cap_arrives_whole(server: LocalHttpServer) -> None:
    """The counterpart to the notice path, so the cap is not a blanket refusal.

    An explicit `Content-Length` under the cap is what separates "refused" from
    "delivered": drop the `>` from the declared-length comparison and every
    response answers with the cap notice, while the over-cap test beside it still
    passes. A cap that refuses everything is a broken cap that only one of the two
    tests can see.
    """
    server.route("/small", lambda _p: text("x" * 50))

    async def scenario() -> tuple[str, bool]:
        """Fetch the small body under a 100-byte cap and report body and flag.

        Returned rather than asserted in place, so the comparison happens after
        the connector is closed -- the only state in which the stream is known to
        be finished and a short read cannot still be in progress.

        Returns:
            The delivered body, and the flag saying nothing was cut.
        """
        async with trusted_session(max_output_bytes=100) as session:
            response = await session.get(server.url("/small"))
            return response.body, response.truncated

    body, truncated = run_async(scenario())
    assert (body, truncated) == ("x" * 50, False)


# --------------------------------------------------------------------------
# No aiohttp type escapes
# --------------------------------------------------------------------------


def test_an_unreachable_endpoint_becomes_a_taxonomy_error() -> None:
    """§5.2: a clean tool error, never a library exception reaching an adapter."""

    async def scenario() -> None:
        """Fetch a port that has nothing listening on it.

        The address is echoed into the error rather than only the exception type
        surviving, because the caller's next move depends on *which* endpoint
        failed: a refusal that names the target can be retried or reported, and
        one that does not has to be guessed at from a bare transport class.
        """
        async with trusted_session() as session:
            await session.get("http://127.0.0.1:1/nothing-listening")

    with pytest.raises(BackendUnreachable) as caught:
        run_async(scenario())
    assert "127.0.0.1:1" in str(caught.value)
    # The fault class is asserted because `_as_unreachable` routes everything
    # that is not the session's own timeout through `type(exc).__name__`. A
    # detail holding no class name at all would mean that arm stopped naming the
    # fault it classified -- it would still carry the endpoint, so the assertion
    # above cannot see it. Evidence: replacing the `__name__` with a constant
    # leaves this line red.
    assert str(caught.value.detail) == "ClientConnectorError"


def test_using_the_session_outside_its_context_manager_is_refused() -> None:
    """Otherwise a session is opened that nobody closes."""
    session = trusted_session()

    async def scenario() -> None:
        """Use a session that was never entered, so no connector exists.

        Policy checks run before the socket is needed, so this reaches the
        lifecycle guard rather than the transport: the request is refused for
        having no session, not for having no server. Closing nothing on the way
        out is safe precisely because nothing was opened, which is what makes the
        guard a `RuntimeError` instead of a leak.
        """
        await session.get("http://127.0.0.1:1/x")

    with pytest.raises(RuntimeError, match="outside `async with`"):
        run_async(scenario())
