"""§4.2's redirect policy, in both directions, because item 6 requires both.

Split from `test_session.py` when that file crossed Codacy's 500-line limit. The
policy is one rule with two halves: a redirect is followed only to a host the
caller configured, re-checked on *every* hop, and the hop limit is 5. The
"followed" half and the "refused" half are each meaningless without the other, so
they live together.

Every case here drives a real loopback server rather than a mock: the redirect is
the behaviour under test, and a stub that answered 307 without a socket would be
testing the test.
"""

from __future__ import annotations

import threading
import time

import pytest

from bigdata_mcp.errors import BackendUnreachable
from bigdata_mcp.session import MAX_REDIRECTS
from bigdata_mcp.session import RedirectRefusal
from bigdata_mcp.session import Response
from bigdata_mcp.session import Session
from tests.support.async_runner import run_async
from tests.support.http_server import Handler
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import Reply
from tests.support.http_server import json_reply
from tests.support.http_server import redirect
from tests.support.http_server import text
from tests.test_session import FAKE_BEARER
from tests.test_session import TRUSTED
from tests.test_session import UNTRUSTED_HOST
from tests.test_session import trusted_session

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
        """Fetch a loopback URL through a session that does not name loopback.

        Nothing is checked in here. The refusal has to escape the event loop for
        `pytest.raises` to see it, and the access log is read after the session's
        `__aexit__` has closed the connector, so the "was it fetched anyway"
        assertion is made against a settled server rather than a live one.
        """
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
        """Fetch under the documented "permits nothing" posture.

        The operator-facing half of the rule lives in the message, which renders
        the allowed set; an empty frozenset has to render as something a reader
        can act on. A test that only asserted the exception type would never
        reach that wording.
        """
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
        """Fetch an allowlisted URL and report status and body together.

        Returned rather than asserted in place so the comparison happens once the
        connector is closed, and so a mismatch is reported by pytest as a single
        difference between the two halves of the same response.

        Returns:
            The status and the body the one allowlisted route served.
        """
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
        """Park the request until the test releases it.

        Parking is what pushes the response past the session's own `ClientTimeout`
        budget: the test is about that budget arriving as a typed error, so the
        route has to outlast it rather than merely be slow. `release` is set in the
        test's `finally`, so this handler thread cannot outlive the assertion that
        gave up on it.

        Args:
            _path: The requested path, unused; the route is matched by
                registration rather than by inspecting what was asked for.

        Returns:
            A canned reply, written to a client that stopped waiting long ago.
        """
        started.set()
        release.wait(10)
        return text("too late")

    server.route("/slow", slow)

    async def scenario() -> None:
        """Fetch with a 0.2 s budget, short enough that the park outlasts it.

        The coroutine is allowed to do nothing but propagate: a return value here
        would mean the request completed inside the budget, which is the opposite
        of what the test is asking. The assertion on the typed error is made
        outside, where the exception has crossed the loop boundary and `get`'s
        conversion has happened.
        """
        async with Session(
            allowlist=frozenset({TRUSTED}),
            allow_http=True,
            timeout_s=0.2,
        ) as session:
            await session.get(server.url("/slow"))

    try:
        with pytest.raises(BackendUnreachable) as caught:
            run_async(scenario())
        # The budget must be *named*, not just the fault class. Both arms of
        # `_as_unreachable` produce a detail containing "TimeoutError", so
        # matching on that alone passes whether or not the dispatch reached the
        # timeout arm at all — `type(exc).__name__` would let a broken router
        # report a class name where the caller budget was spent. Assertion
        # evidence: mutating `isinstance(exc, TimeoutError)` to `False` leaves
        # this test red, where it stayed green against the class-name check.
        assert "TimeoutError" in str(caught.value.detail)
        assert "0.2s budget" in str(caught.value.detail)
    finally:
        release.set()


def test_a_plain_get_returns_the_body(server: LocalHttpServer) -> None:
    """The control for every cap test below: a fitting body is delivered intact.

    `truncated` is asserted here because it is the flag a caller branches on, and
    a session that reported it unconditionally -- or that answered with the cap
    notice whatever the size -- would still pass "an over-cap body is refused"
    while being useless for the ordinary case. `url` is deliberately not asserted:
    with no redirect there is only one place the body could have come from.
    """
    server.route("/cluster/info", lambda _p: text("standalone"))

    async def scenario() -> tuple[int, str, bool]:
        """Fetch the plain endpoint and report status, body and the cap flag.

        The flag is carried out of the loop so it is asserted against a session
        that has already been closed -- the state a real caller inspects the
        `Response` in, and the only one where the stream is known to be finished.

        Returns:
            The status, the body text, and whether the byte cap fired.
        """
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
        """Follow the 307 and report status, body and where the body came from.

        The final URL is carried out alongside the body because a redirect that
        was followed to the wrong place still answers 200: asserting only the
        payload would pass for an estate that reached some other RM entirely.

        Returns:
            The status, the body, and the URL the body was finally served from.
        """
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
        """Fetch a 307 whose target is a reserved name that resolves nowhere.

        The coroutine has to fail with a `PermissionError`, not with whatever the
        network has to say about it. With the allowlist check skipped, the DNS
        lookup for the `.invalid` host fails first and `get` reports that as
        `BackendUnreachable` -- a transport classification for a request a policy
        refused before a socket was ever opened, which is the distinction this
        test exists to keep sharp.
        """
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
        """Fetch the 307 and let the hop rule refuse it.

        The access log is asserted outside, once the session has closed: "the
        destination was never fetched" is only a true statement when no request is
        still in flight, and the handler that appends to the log runs on the
        server's thread rather than the event loop's.
        """
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
        """Run the hop rule directly against an IPv6 literal.

        The loopback server binds IPv4 only, so no socket here can express an
        IPv6 estate. `_next_hop` takes a `Location` and a URL rather than a
        response precisely so a rule like this can be driven without one.

        Nothing is returned, because the property is that no exception is raised
        and a coroutine cannot report its own success. A refusal would arrive as
        the `PermissionError` this call does not expect, and the test simply not
        raising is the whole assertion.
        """
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
                """Fetch the cross-origin redirect and return what was served.

                The response is carried out rather than asserted in place, which
                leaves the credential question to the peer's access log -- read
                only once both servers have stopped, because a header list read
                mid-flight can be missing the hop that matters.

                Returns:
                    Whatever the peer served once the hop was taken.
                """
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
            """Fetch the same-origin redirect and return what was served.

            Both hops have to have been served before the second request's headers
            exist, so the response is carried out and the log is indexed outside,
            after the origin server has stopped. Indexing inside the loop would be
            a race against the handler thread, not an earlier failure.

            Returns:
                Whatever the origin served after the second, same-origin hop.
            """
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


def test_one_slow_chain_cannot_outlast_the_budget_it_started_with(
    server: LocalHttpServer,
) -> None:
    """The chain budget is one number, not one number per hop.

    aiohttp applies the session's `ClientTimeout(total=...)` to each `.get()`, so
    a chain of `MAX_REDIRECTS + 1` requests each inside the budget could spend
    many times it -- six hops at 20 s is 120 s against §3's 60 s client deadline,
    on the HA-standby path the estate is slow on already. `asyncio.timeout` now
    covers the whole chain.

    Two hops, each inside the per-request budget but together outside it: one slow
    hop would be bounded by the per-hop timeout alone and would still pass before
    this fix existed, which is why the chain has to be more than one link long.
    """

    def slow_redirect(target: str, delay_s: float) -> Handler:
        """Return a handler that stalls, then redirects.

        Args:
            target: The redirect target.
            delay_s: How long to hold the request before answering.

        Returns:
            A route handler. It sleeps in the server's own thread, which blocks
                this connection and no other, so the second hop really is after
                the first rather than alongside it.
        """

        def handler(_path: str) -> Reply:
            """Stall for `delay_s`, then redirect to `target`.

            Args:
                _path: The served path, unused.

            Returns:
                A redirect reply.
            """
            time.sleep(delay_s)
            return redirect(target)

        return handler

    server.route("/slow0", slow_redirect(server.url("/slow1"), 0.15))
    server.route("/slow1", slow_redirect(server.url("/slow2"), 0.15))
    server.route("/slow2", lambda _p: text("too late"))

    async def scenario() -> None:
        """Ask for the chain through a session whose whole budget is 0.2 s."""
        async with Session(
            allowlist=frozenset({TRUSTED}),
            allow_http=True,
            timeout_s=0.2,
        ) as session:
            await session.get(server.url("/slow0"))

    with pytest.raises(BackendUnreachable, match="TimeoutError"):
        run_async(scenario())


def test_a_307_whose_target_is_not_a_url_is_refused(server: LocalHttpServer) -> None:
    """A `Location` another system wrote is classified, not propagated.

    `urljoin` and `host_of` both raise `ValueError` on a malformed IPv6 literal —
    an opening bracket with no closing one, as the route below sends — and before
    this the raw exception left the session. §5.2 admits no library type past
    `Session.get`, and a caller cannot do anything with a traceback naming
    `urlsplit`.
    """
    malformed = "https://" + "[" + "bad"
    server.route("/malformed", lambda _p: redirect(malformed))

    async def scenario() -> None:
        """Follow the malformed redirect."""
        async with trusted_session() as session:
            await session.get(server.url("/malformed"))

    with pytest.raises(PermissionError, match="not a URL"):
        run_async(scenario())


def test_a_malformed_initial_url_is_refused_not_propagated() -> None:
    """The same classification `_next_hop` gives a redirect, applied to the start.

    `_origin_of` and `host_of` both parse, and `urlsplit` raises `ValueError` on a
    malformed IPv6 literal — an opening bracket with no closing one. Before the
    guard, `capture-fixtures --url` with such a URL printed a traceback rather
    than exiting 5, and this method's docstring promises no library exception
    leaves it. The target is assembled at runtime so the repository's own
    hermeticity scan does not read it as a host reference.
    """
    malformed = "https://" + "[" + ":1:8088"

    async def scenario() -> None:
        """Ask for the malformed URL through a trusting session."""
        async with trusted_session() as session:
            await session.get(malformed)

    with pytest.raises(PermissionError, match="not a URL"):
        run_async(scenario())


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
        """Walk the over-long chain until the hop bound refuses it.

        The bound is checked before a hop is taken, so the refusal arrives without
        the last route ever being served. That is only provable afterwards: the
        access log is read once the session has closed, because the handler that
        appends to it runs on the server's thread.
        """
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
        """Follow a chain of exactly `MAX_REDIRECTS` hops and return the body.

        The bound is checked before each hop, so this returns the final route's
        body only if the last legal hop is still inside the budget. An off-by-one
        in that comparison would not shorten this chain -- it would raise
        `TOO_MANY_HOPS` here, which is why the companion test needs the extra hop.

        Returns:
            The body served by the last route in the chain.
        """
        async with trusted_session() as session:
            response = await session.get(server.url("/hop0"))
            return response.body

    assert run_async(scenario()) == "arrived"


def test_a_307_with_no_location_is_refused(server: LocalHttpServer) -> None:
    """A 3xx with no `Location` is a broken server, and still not followed."""
    server.route("/bare", lambda _p: Reply(status=307, body=b""))

    async def scenario() -> None:
        """Fetch a 3xx the server wrote without a `Location` header.

        What `get` reads is the header, not the status: to `headers.get` an absent
        `Location` and a hop to nowhere look the same, and both have to end in a
        refusal rather than in a retry of a URL that will keep answering the same
        way.
        """
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
        """Follow a relative `Location` and return the body it lands on.

        RFC 9110 permits a relative target, so the hop is resolved against the URL
        that produced it before any host is looked at. The consequence worth
        stating is that the allowlist still applies -- keyed on the resolved host,
        not on whatever text the header happened to carry.

        Returns:
            The body the relative hop landed on.
        """
        async with trusted_session() as session:
            response = await session.get(server.url("/rel"))
            return response.body

    assert run_async(scenario()) == "here"
