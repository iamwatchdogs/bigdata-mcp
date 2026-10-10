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

import datetime
import ipaddress
import json
import ssl
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Self
from typing import cast
from typing import override
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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
    chunked: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        """Derive the transfer-encoding header when a chunked reply is built.

        Attributes:
            chunked: Chunk payloads. Empty means a normal `Content-Length` reply.
                Non-empty sends `Transfer-Encoding: chunked` and no length at all,
                which is the only way to reach the client code path where the
                response size is not known in advance.
        """
        if self.chunked:
            self.headers["transfer-encoding"] = "chunked"


def chunked(*payloads: bytes, status: int = 200) -> Reply:
    """Build a reply with no `Content-Length`.

    Args:
        *payloads: The chunks, written in order.
        status: HTTP status.

    Returns:
        A chunked `Reply`. `aiohttp` reports `content_length` as `None` for one
        of these, which is the case the byte-cap flag had to be written for.
    """
    return Reply(status=status, chunked=tuple(payloads))


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
    received_headers: list[dict[str, str]]

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
        server.received_headers.append({k.lower(): v for k, v in self.headers.items()})
        route = server.routes.get(path)
        reply = (
            route(path)
            if route is not None
            else Reply(status=404, body=b"no such path")
        )
        self.send_response(reply.status)
        self._write_headers(reply)
        self._write_body(reply)

    def _write_headers(self, reply: Reply) -> None:
        """Write the reply's headers, with the framing decided by `chunked`.

        A chunked reply carries no `Content-Length` at all — that is the point of
        it, and sending one anyway would make the client believe it knows the size.

        Args:
            reply: The reply to write headers for.
        """
        for name, value in reply.headers.items():
            if name.lower() != "content-length":
                self.send_header(name, value)
        if reply.chunked:
            self.end_headers()
            return
        declared = next(
            (
                value
                for name, value in reply.headers.items()
                if name.lower() == "content-length"
            ),
            None,
        )
        self.send_header("content-length", declared or str(len(reply.body)))
        self.end_headers()

    def _write_body(self, reply: Reply) -> None:
        """Write the reply's body, chunk-framed when `chunked` is set.

        Args:
            reply: The reply to write the body of.
        """
        if not reply.chunked:
            self.wfile.write(reply.body)
            return
        for payload in reply.chunked:
            self.wfile.write(f"{len(payload):x}\r\n".encode())
            self.wfile.write(payload)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    @override
    # The parameter keeps the stdlib's name because `ty` checks this override
    # against `BaseHTTPRequestHandler.log_message`, so renaming it would be an LSP
    # violation even though the base class only ever passes it positionally. That
    # leaves Codacy's `redefined-builtin` with no honest way to be satisfied by the
    # code, so the finding is suppressed here rather than by weakening the gate.
    # pylint: disable=redefined-builtin
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
        received_headers: The request headers of every served request, in order,
            lowercased keys. Recorded because "did the second host receive the
            credential" cannot be answered from the response.
    """

    def __init__(self) -> None:
        """Start with an empty route table and an empty access log."""
        self.routes: dict[str, Handler] = {}
        self.recorded: list[str] = []
        self.received_headers: list[dict[str, str]] = []
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
        self._server.received_headers = self.received_headers
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


class LocalTlsServer(LocalHttpServer):
    """A loopback **HTTPS** server, for proving the configured CA bundle is used.

    A separate class rather than a flag on `LocalHttpServer` because the whole
    point is that a client which does not trust this certificate must fail: a
    flag would make it easy to write the negative test against the same object as
    the positive one and quietly prove nothing.

    The certificate is self-signed for `localhost`, generated per server, and
    written out so a test can pass it as `ca_bundle`. §3 is about an internal CA,
    and this is the smallest honest stand-in for one on a machine with no estate.
    """

    def __init__(self) -> None:
        """Generate a self-signed certificate before starting."""
        super().__init__()
        self.certificate_path, self.key_path = _self_signed("localhost")

    @override
    def start(self) -> Self:
        """Bind to loopback and serve TLS with this server's certificate.

        Returns:
            This instance.

        Raises:
            RuntimeError: If already started.
        """
        if self._server is not None:
            message = "LocalTlsServer is already running"
            raise RuntimeError(message)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Pin the floor rather than inheriting OpenSSL's default, which is TLS 1.0
        # on some builds. CodeQL flags this call for exactly that reason, and it is
        # right to: a TLS 1.0-capable server is a real weakness even when the only
        # client is `LocalTlsServer` in the next test over. TLS 1.2 has been the
        # floor since 2021, so nothing this suite speaks to needs 1.0.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.certificate_path, self.key_path)
        self._server = _Server(("127.0.0.1", 0))
        self._server.routes = self.routes
        self._server.recorded = self.recorded
        self._server.received_headers = self.received_headers
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @override
    def url(self, path: str = "/") -> str:
        """Build an absolute `https://` URL against this server.

        An override rather than a new property: the base class's `url` is a
        method, and shadowing it with a property would be an LSP violation that
        `ty` is right to reject — and worse, it would read as "this server has no
        URL" to any caller holding a `LocalHttpServer`.

        Args:
            path: The path, with any query string.

        Returns:
            An `https://127.0.0.1:<port><path>` URL. Raises `RuntimeError` if the
            server is not running, via `port`.
        """
        return f"https://127.0.0.1:{self.port}{path}"


#: PEM encodings, named because the triple reads worse than the constants do.
_PEM_CERT = serialization.Encoding.PEM
_PEM_KEY = (
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
)


#: PEM encodings, named because the key's triple reads worse inline.
_PEM = serialization.Encoding.PEM
_KEY_ENCODING = (
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
)


def _self_signed(common_name: str) -> tuple[Path, Path]:
    """Write a self-signed certificate and key for `common_name` to a temp dir.

    Generated at runtime rather than committed, because a committed key in a test
    fixture is a key in a repository forever, and `gitleaks` will eventually be
    right to complain about it.

    Args:
        common_name: The certificate's CN and first SAN.

    Returns:
        The certificate path and the key path.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    directory = Path(tempfile.mkdtemp(prefix="bigdata-mcp-tls-"))
    certificate_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    _write(certificate_path, _certificate(common_name, key).public_bytes(_PEM))
    _write(key_path, key.private_bytes(*_KEY_ENCODING))
    return certificate_path, key_path


def _certificate(common_name: str, key: rsa.RSAPrivateKey) -> x509.Certificate:
    """Build a self-signed certificate for `common_name`.

    The SAN list carries both the name and `127.0.0.1`. The server binds to
    loopback, so the client dials an address, and a certificate naming only
    `localhost` fails hostname verification with a
    `ClientConnectorCertificateError` that has nothing to do with what the test is
    checking. An `IPAddress` SAN is required for an address; a `DNSName` is not a
    substitute for one.

    Args:
        common_name: The CN and first SAN.
        key: The key to certify.

    Returns:
        The signed certificate.
    """
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509
        .CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(common_name),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )


def _write(path: Path, payload: bytes) -> None:
    """Write PEM bytes to `path`.

    Args:
        path: Where to write.
        payload: The encoded bytes.
    """
    path.write_bytes(payload)
