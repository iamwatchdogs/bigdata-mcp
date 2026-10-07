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
import threading
from typing import TYPE_CHECKING

import pytest

from bigdata_mcp.errors import BackendUnreachable
from bigdata_mcp.errors import ConfigError
from bigdata_mcp.errors import MissingCABundleError
from bigdata_mcp.session import LIMIT_PER_HOST
from bigdata_mcp.session import MAX_REDIRECTS
from bigdata_mcp.session import RedirectRefusal
from bigdata_mcp.session import Response
from bigdata_mcp.session import Session
from bigdata_mcp.session import build_ssl_context
from tests.support.async_runner import run_async
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import LocalTlsServer
from tests.support.http_server import Reply
from tests.support.http_server import chunked
from tests.support.http_server import json_reply
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
    from collections.abc import Iterator


@pytest.fixture
def server() -> Iterator[LocalHttpServer]:
    """Return a started loopback HTTP server.

    Yields:
        A running `LocalHttpServer`, stopped when the test finishes.
    """
    with LocalHttpServer() as running:
        yield running


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
# Redirect policy. Both directions, because §4.2 item 6 requires both.
# --------------------------------------------------------------------------


def test_an_initial_request_to_an_unallowlisted_host_is_refused(
    server: LocalHttpServer,
) -> None:
    """The allowlist gates the first request, not only the redirects.

    Mutation evidence: hoisting the check into `get` before the loop is what this
    test pins — with the check only in `_next_hop`, `Session.get` fetched a host
    outside the allowlist and returned its body (observed: status 200 against a
    loopback server with `allowlist={"not-this-host.invalid"}`). The `Session`
    docstring promises "empty permits nothing", and only a check on the initial
    URL makes that true.
    """
    server.route("/x", lambda _p: text("reached"))

    async def scenario() -> None:
        async with Session(
            allowlist=frozenset({"not-this-host.invalid"}),
            allow_http=True,
        ) as session:
            await session.get(server.url("/x"))

    with pytest.raises(PermissionError) as caught:
        run_async(scenario())
    message = str(caught.value)
    assert RedirectRefusal.NOT_ALLOWLISTED.value in message
    assert "not-this-host.invalid" in message
    assert not any("/x" in path for path in server.recorded), (
        "the unallowlisted host was fetched before the refusal"
    )


def test_an_empty_allowlist_permits_nothing(server: LocalHttpServer) -> None:
    """The documented default posture: an empty allowlist cannot reach out.

    This is the misconfiguration guard the class docstring promises. A caller that
    forgets to configure hosts must get a named refusal, not a successful fetch.
    """
    server.route("/y", lambda _p: text("reached"))

    async def scenario() -> None:
        async with Session(allowlist=frozenset(), allow_http=True) as session:
            await session.get(server.url("/y"))

    with pytest.raises(PermissionError) as caught:
        run_async(scenario())
    assert RedirectRefusal.NOT_ALLOWLISTED.value in str(caught.value)
    assert "(nothing)" in str(caught.value)


def test_a_same_host_initial_request_still_passes_the_first_hop_check(
    server: LocalHttpServer,
) -> None:
    """The added check must not break the loopback case every other test uses."""
    server.route("/ok", lambda _p: text("fine"))

    async def scenario() -> tuple[int, str]:
        async with trusted_session() as session:
            response = await session.get(server.url("/ok"))
            return response.status, response.body

    status, body = run_async(scenario())
    assert (status, body) == (200, "fine")


def test_the_session_timeout_raises_a_typed_error_not_a_bare_timeout(
    server: LocalHttpServer,
) -> None:
    """§5.2: aiohttp types never escape `get`, including the session's own budget.

    The connector timeout surfaces as `aiohttp.ServerTimeoutError`, which *is* an
    `aiohttp.ClientError` and was already converted. The session's own
    `ClientTimeout(total=...)` raises the builtin `TimeoutError`, which is not a
    `ClientError` — so it escaped `get` untyped. Mutation evidence: removing the
    `except TimeoutError` arm turns this test red with a bare `TimeoutError`.
    """
    started = threading.Event()
    release = threading.Event()

    def slow(_path: str) -> Reply:
        started.set()
        release.wait(10)
        return text("too late")

    server.route("/slow", slow)

    async def scenario() -> None:
        async with Session(
            allowlist=frozenset({TRUSTED}),
            allow_http=True,
            timeout_s=0.2,
        ) as session:
            await session.get(server.url("/slow"))

    try:
        with pytest.raises(BackendUnreachable) as caught:
            run_async(scenario())
        assert "TimeoutError" in str(caught.value.detail)
    finally:
        release.set()


def test_a_plain_get_returns_the_body(server: LocalHttpServer) -> None:
    server.route("/cluster/info", lambda _p: text("standalone"))

    async def scenario() -> tuple[int, str, bool]:
        async with trusted_session() as session:
            response = await session.get(server.url("/cluster/info"))
            return response.status, response.body, response.truncated

    status, body, truncated = run_async(scenario())
    assert (status, body, truncated) == (200, "standalone", False)


def test_a_307_to_a_configured_peer_rm_is_followed(server: LocalHttpServer) -> None:
    """The estate's own behaviour: a standby answers 307 pointing at the active RM.

    Refusing this would make YARN unreachable, which is why v1's blanket redirect
    ban was an over-correction (§4.2).
    """
    server.route("/ws/v1/cluster/info", lambda _p: redirect(server.url("/live/info")))
    server.route("/live/info", lambda _p: json_reply({"state": "RUNNING"}))

    async def scenario() -> tuple[int, str, str]:
        async with trusted_session() as session:
            response = await session.get(server.url("/ws/v1/cluster/info"))
            return response.status, response.body, response.url

    status, body, final_url = run_async(scenario())
    assert status == 200
    assert "RUNNING" in body
    assert final_url.endswith("/live/info")


def test_a_307_to_an_unconfigured_host_is_refused_and_names_it(
    server: LocalHttpServer,
) -> None:
    """§4.2 item 4: the check runs on the target, and names the offending host."""
    server.route("/away", lambda _p: redirect("http://elsewhere.invalid/elsewhere"))

    async def scenario() -> None:
        async with trusted_session() as session:
            await session.get(server.url("/away"))

    with pytest.raises(PermissionError) as caught:
        run_async(scenario())
    message = str(caught.value)
    assert RedirectRefusal.NOT_ALLOWLISTED.value in message
    assert UNTRUSTED_HOST in message


def test_the_allowlist_is_checked_on_the_target_not_only_the_first_url(
    server: LocalHttpServer,
) -> None:
    """The first URL is trusted by construction; the threat is the *hop*.

    This test asserts the destination was never fetched, which is the part a
    first-hop-only check gets wrong.
    """
    server.route("/peer-hop", lambda _p: redirect("http://elsewhere.invalid/metadata"))

    async def scenario() -> None:
        async with trusted_session() as session:
            await session.get(server.url("/peer-hop"))

    with pytest.raises(PermissionError):
        run_async(scenario())
    assert not any("metadata" in path for path in server.recorded)


def test_an_ipv6_literal_target_is_matched_against_the_allowlist() -> None:
    """§4.2's allowlist has to be able to name an IPv6 host at all.

    The allowlist is a set of host strings and this module derives the host of a
    redirect target with the same helper the config side uses. An IPv6 literal is
    all colons, so a first-colon split yields `[` — which matches no configured
    host, so an IPv6 estate loses every HA redirect. Refused rather than followed,
    so it is safe; but it is refused for a reason that reads as a misconfiguration,
    and nothing in the message points at the parser.
    """
    from bigdata_mcp.hosts import host_of

    assert host_of("https://[::1]:8088/ws") == "::1"
    assert host_of("https://[0:0:0:0:0:0:0:1]:8088") == "::1", (
        "the long form of loopback must match the short form in an allowlist"
    )

    async def scenario() -> None:
        async with Session(
            allowlist=frozenset({"::1"}),
        ) as session:
            # `_next_hop` is what the allowlist check lives in; driving it directly
            # keeps the loopback server, which can only bind IPv4, out of the test.
            session._next_hop(  # ruff: ignore[private-member-access] - the rule under test is private
                "https://[::1]:8088/next", "https://[::1]:8088/here"
            )

    run_async(scenario())


def test_a_redirect_to_another_origin_does_not_carry_the_credential() -> None:
    """A YARN `Authorization` header must not reach a different origin.

    The allowlist is keyed on host, so two loopback servers on different ports are
    both allowlisted and the hop is permitted — §3's YARN HA needs exactly that.
    Being allowlisted is therefore not a reason to keep sending a credential: this
    is the redirect aiohttp's automatic follower would have stripped for us, lost
    by following hops by hand.

    The second server records what it received, because "did the other host see the
    credential" is not answerable from the response.
    """
    with LocalHttpServer() as peer:
        peer.route("/landing", lambda _p: text("peer's page"))
        peer.route("/landed", lambda _p: text("peer's page"))
        with LocalHttpServer() as origin:
            origin.route("/away", lambda _p: redirect(peer.url("/landed")))

            async def scenario() -> Response:
                async with Session(
                    allowlist=frozenset({TRUSTED}),
                    allow_http=True,
                    headers={"Authorization": FAKE_BEARER},
                ) as session:
                    return await session.get(origin.url("/away"))

            response = run_async(scenario())

    assert response.body.strip() == "peer's page"
    assert peer.recorded == ["/landed"], "the hop was not followed at all"
    landed_headers = peer.received_headers[0]
    assert "authorization" not in landed_headers, (
        f"the credential crossed origins: {landed_headers.get('authorization')!r}"
    )


def test_a_redirect_on_the_same_origin_keeps_the_credential() -> None:
    """The other half: stripping everywhere would break HA, not just leak.

    §3's YARN standby answers 307 pointing at the active peer. Dropping the
    header on that hop would turn a working cluster into an estate that answers
    every request with 401, so the rule has to be about the origin changing and not
    about the redirect itself.
    """
    with LocalHttpServer() as origin:
        origin.route("/away", lambda _p: redirect(origin.url("/landed")))
        origin.route("/landed", lambda _p: text("same origin"))

        async def scenario() -> Response:
            async with Session(
                allowlist=frozenset({TRUSTED}),
                allow_http=True,
                headers={"Authorization": FAKE_BEARER},
            ) as session:
                return await session.get(origin.url("/away"))

        response = run_async(scenario())

    assert response.body.strip() == "same origin"
    assert origin.recorded == ["/away", "/landed"], (
        "the second hop never reached the origin server"
    )
    assert origin.received_headers[1].get("authorization") == FAKE_BEARER


def test_https_to_http_downgrade_is_refused() -> None:
    """§4.2 item 5, unconditional. A redirect cannot downgrade transport.

    Exercised against the hop rule directly rather than over the loopback socket:
    the loopback server speaks plain HTTP, so an `https://` request to it fails the
    TLS handshake before the redirect rule is reached. What is under test is the
    rule, not the handshake, and the allowlist rule *is* covered end to end above
    over a real socket.
    """
    session = trusted_session(allow_http=False)
    with pytest.raises(PermissionError) as caught:
        session._next_hop("http://127.0.0.1:8088/live", "https://127.0.0.1:8088/ws")  # ruff: ignore[private-member-access]
    assert RedirectRefusal.SCHEME_DOWNGRADE.value in str(caught.value)


def test_an_https_to_https_hop_is_not_treated_as_a_downgrade() -> None:
    """The counterpart, so the rule is a scheme check and not a blanket refusal."""
    session = trusted_session(allow_http=False)
    followed = session._next_hop(  # ruff: ignore[private-member-access]
        "https://127.0.0.1:8088/live", "https://127.0.0.1:8088/ws"
    )
    assert followed == "https://127.0.0.1:8088/live"


def test_a_missing_location_is_refused_by_the_hop_rule() -> None:
    """The absent-`Location` arm, without needing a socket that sends one."""
    session = trusted_session(allow_http=False)
    with pytest.raises(PermissionError) as caught:
        session._next_hop(None, "https://127.0.0.1:8088/ws")  # ruff: ignore[private-member-access]
    assert RedirectRefusal.NO_LOCATION.value in str(caught.value)


def test_a_redirect_chain_longer_than_the_limit_is_an_error(
    server: LocalHttpServer,
) -> None:
    """§4.2 item 3: a chain longer than the bound is an error, not a retry."""
    limit = MAX_REDIRECTS
    for index in range(limit + 1):
        server.route(
            f"/hop{index}", lambda _p, i=index: redirect(server.url(f"/hop{i + 1}"))
        )
    server.route(f"/hop{limit + 1}", lambda _p: text("never reached"))

    async def scenario() -> None:
        async with trusted_session() as session:
            await session.get(server.url("/hop0"))

    with pytest.raises(PermissionError) as caught:
        run_async(scenario())
    assert RedirectRefusal.TOO_MANY_HOPS.value in str(caught.value)
    assert f"/hop{limit + 1}" not in server.recorded


def test_a_chain_exactly_at_the_limit_is_followed(server: LocalHttpServer) -> None:
    """The bound is inclusive: `MAX_REDIRECTS` hops are legal, one more is not."""
    last = MAX_REDIRECTS
    for index in range(last):
        server.route(
            f"/hop{index}", lambda _p, i=index: redirect(server.url(f"/hop{i + 1}"))
        )
    server.route(f"/hop{last}", lambda _p: text("arrived"))

    async def scenario() -> str:
        async with trusted_session() as session:
            response = await session.get(server.url("/hop0"))
            return response.body

    assert run_async(scenario()) == "arrived"


def test_a_307_with_no_location_is_refused(server: LocalHttpServer) -> None:
    """A 3xx with no `Location` is a broken server, and still not followed."""
    server.route("/bare", lambda _p: Reply(status=307, body=b""))

    async def scenario() -> None:
        async with trusted_session() as session:
            await session.get(server.url("/bare"))

    with pytest.raises(PermissionError) as caught:
        run_async(scenario())
    assert RedirectRefusal.NO_LOCATION.value in str(caught.value)


def chain_routes(server: LocalHttpServer, hops: int) -> None:
    """Register `/hop0` .. `/hopN`, each redirecting to the next.

    Args:
        server: The loopback server.
        hops: How many redirecting hops to register.
    """
    for index in range(hops):
        target = server.url(f"/hop{index + 1}")
        server.route(f"/hop{index}", lambda _p, url=target: redirect(url))


def test_a_relative_location_is_resolved(server: LocalHttpServer) -> None:
    """RFC 9110 permits a relative `Location`, so one has to work."""
    server.route("/rel", lambda _p: redirect("/landed"))
    server.route("/landed", lambda _p: text("here"))

    async def scenario() -> str:
        async with trusted_session() as session:
            response = await session.get(server.url("/rel"))
            return response.body

    assert run_async(scenario()) == "here"


# --------------------------------------------------------------------------
# Scheme policy
# --------------------------------------------------------------------------


def test_plain_http_is_refused_unless_explicitly_permitted() -> None:
    """A policy that permits plain HTTP at the start is not a downgrade policy."""

    async def scenario() -> None:
        async with Session(allowlist=frozenset({TRUSTED})) as session:
            await session.get("http://127.0.0.1:1/thing")

    with pytest.raises(PermissionError, match="https only"):
        run_async(scenario())


@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data"])
def test_a_scheme_this_server_does_not_speak_is_refused(scheme: str) -> None:
    async def scenario() -> None:
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
        async with Session(allowlist=frozenset({"127.0.0.1"})) as session:
            await session.get("https://127.0.0.1:1/x")

    with pytest.raises(MissingCABundleError, match="CA bundle"):
        run_async(scenario())


def test_an_unloadable_ca_bundle_is_a_config_error_not_a_crash() -> None:
    """A missing or non-PEM bundle is a config fault, not an unhandled error.

    `ssl.create_default_context(cafile=...)` raises `FileNotFoundError` for a
    path that does not exist and `ssl.SSLError` for a file that is not PEM. Both
    are `OSError` subclasses that `capture.main` does not catch, so a capture CLI
    run with a bad `--ca-bundle` used to die with a traceback instead of the
    documented exit 5. Mutation evidence: removing the try/except in
    `build_ssl_context` turns this test red with the raw `FileNotFoundError`.
    """
    with pytest.raises(ConfigError, match="Cannot load CA bundle"):
        build_ssl_context("/nonexistent/ca-bundle.pem")


def test_the_connector_limit_is_explicit_not_aiohttps_unlimited_default() -> None:
    """aiohttp's `limit_per_host=0` means *unlimited*, which would void §10."""

    async def scenario() -> int:
        async with trusted_session() as session:
            return session.connector_limit_per_host

    assert run_async(scenario()) == LIMIT_PER_HOST
    assert LIMIT_PER_HOST != 0


def test_a_body_over_the_cap_is_refused_before_it_is_read(
    server: LocalHttpServer,
) -> None:
    server.route("/big", lambda _p: text("x" * 5000))

    async def scenario() -> tuple[str, bool]:
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
        async with trusted_session(max_output_bytes=100) as session:
            response = await session.get(server.url("/stream"))
            return response.body, response.truncated

    body, truncated = run_async(scenario())
    assert (body, truncated) == ("x" * 30 + "y" * 30, False)


def test_a_body_under_the_cap_arrives_whole(server: LocalHttpServer) -> None:
    server.route("/small", lambda _p: text("x" * 50))

    async def scenario() -> tuple[str, bool]:
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
        async with trusted_session() as session:
            await session.get("http://127.0.0.1:1/nothing-listening")

    with pytest.raises(BackendUnreachable) as caught:
        run_async(scenario())
    assert "127.0.0.1:1" in str(caught.value)


def test_using_the_session_outside_its_context_manager_is_refused() -> None:
    """Otherwise a session is opened that nobody closes."""
    session = trusted_session()

    async def scenario() -> None:
        await session.get("http://127.0.0.1:1/x")

    with pytest.raises(RuntimeError, match="outside `async with`"):
        run_async(scenario())
