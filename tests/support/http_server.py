"""A real local HTTP server for the session tests.

`SPEC.md` §4.2's own note: no third-party mock works on Python 3.14 for any
candidate — `respx` is broken for `httpx2`, `aioresponses` is broken for
`aiohttp`. So the substitute for the estate is a real socket on loopback.

Loopback only, and the repo's hermetic-host contract test is what enforces it:
this server cannot be aimed anywhere else without that test failing.

The server is deliberately dumb and scriptable. Each route is a callable that
receives the parsed path and returns a `Reply`, so a test can express "307 to a
peer RM" or "a body larger than the cap" without a web framework.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING
from typing import Any
from typing import Self
from typing import cast
from typing import override
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from types import TracebackType

Handler = Callable[[str], "Reply"]


@dataclass(slots=True)
class Reply:
    """What a route returns.

    Attributes:
        status: HTTP status to send. A 3xx here is how a test exercises the
            redirect policy.
        body: Response body as bytes.
        headers: Extra headers. `Location` is how a redirect names its target.
    """

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def text(
    body: str, *, status: int = 200, headers: dict[str, str] | None = None
) -> Reply:
    """Build a plain-text reply.

    Args:
        body: The body text.
        status: HTTP status.
        headers: Extra headers.

    Returns:
        A `Reply` with an explicit content type.
    """
    return Reply(
        status=status,
        body=body.encode(),
        headers={"content-type": "text/plain", **(headers or {})},
    )


def json_reply(payload: object, *, status: int = 200) -> Reply:
    """Build a JSON reply.

    Args:
        payload: Anything `json.dumps` accepts.
        status: HTTP status.

    Returns:
        A `Reply` carrying the serialised payload.
    """
    return Reply(
        status=status,
        body=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def redirect(location: str, *, status: int = 307) -> Reply:
    """Build a redirect reply.

    Args:
        location: The `Location` target, absolute or relative.
        status: The redirect status. 307 is what YARN HA actually returns.

    Returns:
        A `Reply` with a `Location` header and an empty body.
    """
    return Reply(status=status, body=b"", headers={"location": location})


class _Server(ThreadingHTTPServer):
    """`ThreadingHTTPServer` carrying this test's route table and access log.

    Subclassing rather than monkeypatching attributes onto the instance: the
    stdlib's `BaseServer` has no such attributes, so every read of them would
    need a `type: ignore`, and a suppressed type error is exactly how a rename
    would slip through.

    Attributes:
        routes: Path-to-handler table, shared with the owning `LocalHttpServer`.
        recorded: Every path served, in order.
    """

    routes: dict[str, Handler]
    recorded: list[str]

    def __init__(self, address: tuple[str, int]) -> None:
        """Bind without serving, so `start` controls when serving begins.

        Args:
            address: A `(host, port)` pair; port 0 asks the OS for one.
        """
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    """Routes each request through the owning server's route table.

    Attributes:
        protocol_version: HTTP/1.1 so a 307 keeps the method, matching what YARN
            HA does.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve a GET. A response is always written; an unknown path becomes 404."""
        self._serve()

    def do_POST(self) -> None:
        """Serve a POST from the route table.

        Reads the body first so a client that sent one does not see a reset, then
        dispatches exactly as `do_GET` does.
        """
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        self._serve()

    def _serve(self) -> None:
        """Dispatch one request, recording it and writing the reply."""
        server = cast("_Server", self.server)
        path = urlsplit(self.path).path
        server.recorded.append(self.path)
        route = server.routes.get(path)
        reply = (
            route(path)
            if route is not None
            else Reply(status=404, body=b"no such path")
        )
        self.send_response(reply.status)
        declared = next(
            (
                value
                for name, value in reply.headers.items()
                if name.lower() == "content-length"
            ),
            None,
        )
        for name, value in reply.headers.items():
            if name.lower() != "content-length":
                self.send_header(name, value)
        length = declared if declared is not None else str(len(reply.body))
        self.send_header("content-length", length)
        self.end_headers()
        self.wfile.write(reply.body)

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log.

        The tests assert on responses, and a wall of access logs makes a failure
        transcript unreadable.

        Args:
            format: The stdlib's format string.
            *args: Its arguments.
        """


class LocalHttpServer:
    """A loopback HTTP server for tests. Started and stopped explicitly.

    Attributes:
        routes: Path-to-handler table, mutated before the first request.
        recorded: Every path this server has served, in order.
    """

    def __init__(self) -> None:
        """Start with an empty route table and an empty access log."""
        self.routes: dict[str, Handler] = {}
        self.recorded: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def route(self, path: str, handler: Handler) -> Self:
        """Register a route.

        Args:
            path: Exact path, query string excluded.
            handler: Callable receiving the path and returning a `Reply`.

        Returns:
            This instance, so registrations chain.
        """
        self.routes[path] = handler
        return self

    def start(self) -> Self:
        """Bind to loopback on an ephemeral port and serve in a background thread.

        Returns:
            This instance.

        Raises:
            RuntimeError: If already started.
        """
        if self._server is not None:
            message = "LocalHttpServer is already running"
            raise RuntimeError(message)
        self._server = _Server(("127.0.0.1", 0))
        self._server.routes = self.routes
        self._server.recorded = self.recorded
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and join the thread."""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> Self:
        """Start the server.

        Returns:
            This instance.
        """
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the server.

        Args:
            exc_type: The in-flight exception's type, if any.
            exc: The in-flight exception, if any.
            traceback: The in-flight traceback, if any.
        """
        self.stop()

    @property
    def port(self) -> int:
        """The bound port.

        Returns:
            The ephemeral port the OS assigned.

        Raises:
            RuntimeError: If the server is not running.
        """
        if self._server is None:
            message = "LocalHttpServer is not running"
            raise RuntimeError(message)
        return int(self._server.server_address[1])

    def url(self, path: str = "/") -> str:
        """Build an absolute URL against this server.

        Args:
            path: The path, with any query string.

        Returns:
            An `http://127.0.0.1:<port><path>` URL. Loopback by construction, so
            it satisfies the repo's hermetic-host contract test.
        """
        return f"http://127.0.0.1:{self.port}{path}"
