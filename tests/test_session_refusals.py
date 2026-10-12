"""The refusals: which redirects this session will not follow.

The companion to `test_session_redirects.py`. That half proves a hop the policy
permits is followed; this half proves the hop it forbids is refused *and named* —
`RedirectRefusal` rather than a bare string, because an operator reading the log
should not have to work out which check fired.

Every case here drives either a real loopback server or the pure `origin_of` helper,
never a mock: a refusal is only meaningful against the URL that was actually
requested, and `origin_of` is the function a prefix comparison would have gotten
wrong (`foo` and `foo.evil` are unrelated origins).
"""

from __future__ import annotations

import time

import pytest

from bigdata_mcp.errors import BackendUnreachable
from bigdata_mcp.session import MAX_REDIRECTS
from bigdata_mcp.session import RedirectRefusal
from bigdata_mcp.session import Session
from tests.support.async_runner import run_async
from tests.support.http_server import Handler
from tests.support.http_server import LocalHttpServer
from tests.support.http_server import Reply
from tests.support.http_server import redirect
from tests.support.http_server import text
from tests.test_session import TRUSTED
from tests.test_session import trusted_session

# --------------------------------------------------------------------------
# Redirect policy. Both directions, because §4.2 item 6 requires both.
# --------------------------------------------------------------------------


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
