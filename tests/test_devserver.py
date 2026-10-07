"""Exercise the development HTTP bridge through real loopback sockets."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from opencs.api.app import ASGIApp, ASGIMessage, Receive, Send
from opencs.api.devserver import create_dev_server


@contextmanager
def running(app: ASGIApp, **kwargs: Any) -> Iterator[ThreadingHTTPServer]:
    server = create_dev_server(app, port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def request(server: ThreadingHTTPServer, raw: bytes, *, eof: bool = False) -> bytes:
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as client:
        client.sendall(raw)
        if eof:
            client.shutdown(socket.SHUT_WR)
        result = bytearray()
        while chunk := client.recv(65536):
            result.extend(chunk)
        return bytes(result)


async def echo(scope: ASGIMessage, receive: Receive, send: Send) -> None:
    message = await receive()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"x-test", b"yes")]})
    await send({"type": "http.response.body", "body": message["body"]})


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1", "example.com", "127.0.0.2"])
def test_rejects_nonapproved_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        create_dev_server(echo, host=host, port=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": -1},
        {"port": 65536},
        {"port": True},
        {"max_body_bytes": 0},
        {"max_body_bytes": True},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"request_timeout_seconds": float("inf")},
        {"request_timeout_seconds": True},
    ],
)
def test_validates_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        create_dev_server(echo, **kwargs)


def test_preserves_signed_body_and_duplicate_headers() -> None:
    observed: list[ASGIMessage] = []
    body = '{ "content": "退款", "n": 1 }\r\n'.encode()

    async def capture(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        observed.append(scope)
        await echo(scope, receive, send)
        assert (await receive())["type"] == "http.disconnect"

    with running(capture, host="localhost") as server:
        assert server.server_address[0] == "127.0.0.1"
        assert server.daemon_threads
        raw = request(
            server,
            b"POST /webhooks/chatwoot/%E9%80%80?token=secret HTTP/1.1\r\n"
            b"Host: localhost\r\nX-Chatwoot-Signature: first\r\n"
            b"X-Chatwoot-Signature: second\r\nConnection: keep-alive\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body,
        )
    assert raw.startswith(b"HTTP/1.0 200")
    assert b"Connection: close\r\n" in raw
    assert raw.split(b"\r\n\r\n", 1)[1] == body
    assert observed[0]["path"] == "/webhooks/chatwoot/退"
    assert observed[0]["raw_path"] == b"/webhooks/chatwoot/%E9%80%80"
    assert observed[0]["query_string"] == b"token=secret"
    signatures = [v for k, v in observed[0]["headers"] if k == b"x-chatwoot-signature"]
    assert signatures == [b"first", b"second"]


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (b"POST / HTTP/1.1\r\nHost: local\r\n\r\n", 411),
        (b"POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx", 400),
        (b"POST / HTTP/1.1\r\nContent-Length: -1\r\n\r\n", 400),
        (b"POST / HTTP/1.1\r\nContent-Length: 1, 1\r\n\r\nx", 400),
        (b"POST / HTTP/1.1\r\nContent-Length: 9\r\n\r\n", 413),
        (b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", 400),
        (b"GET / HTTP/1.1\r\nTransfer-Encoding: identity\r\n\r\n", 400),
        (b"GET http://host/ HTTP/1.1\r\n\r\n", 400),
        (b"GET //host/path HTTP/1.1\r\n\r\n", 400),
        (b"GET /path#fragment HTTP/1.1\r\n\r\n", 400),
        (b"GET /\xff HTTP/1.1\r\n\r\n", 400),
        (b"BOGUS /?secret=value HTTP/1.1\r\n\r\n", 501),
    ],
)
def test_rejects_invalid_frames_without_app_call(raw: bytes, status: int) -> None:
    calls: list[bool] = []

    async def unused(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        calls.append(True)
        await echo(scope, receive, send)

    with running(unused, max_body_bytes=8) as server:
        response = request(server, raw)
    assert response.startswith(f"HTTP/1.0 {status}".encode())
    assert json.loads(response.split(b"\r\n\r\n", 1)[1])["error"]
    assert not calls


def test_truncated_body_and_read_timeout_never_call_app() -> None:
    calls: list[bool] = []

    async def unused(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        calls.append(True)
        await echo(scope, receive, send)

    with running(unused, request_timeout_seconds=0.05) as server:
        raw = b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nx"
        assert request(server, raw, eof=True).startswith(b"HTTP/1.0 400")
        assert request(server, raw).startswith(b"HTTP/1.0 408")
        assert request(server, b"GET / HTTP/1.1\r\nX-Secret: partial") == b""
    assert not calls


def test_routes_methods_and_head_body() -> None:
    async def routes(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        status = 404 if scope["path"] != "/health/live" else 200
        if scope["method"] != "GET":
            status = 405
        await send({"type": "http.response.start", "status": status})
        await send({"type": "http.response.body", "body": b"{}"})

    with running(routes) as server:
        for method, path, expected in [
            ("GET", "/health/live", 200),
            ("GET", "/missing", 404),
            ("PATCH", "/health/live", 405),
            ("HEAD", "/health/live", 405),
        ]:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request(method, path)
            response = connection.getresponse()
            assert response.status == expected
            assert response.read() == (b"" if method == "HEAD" else b"{}")
            assert response.will_close
            connection.close()


@pytest.mark.parametrize("behavior", ["exception", "stream", "oversize", "header"])
def test_application_errors_are_generic_and_not_logged(
    behavior: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        if behavior == "exception":
            raise RuntimeError("token=secret-from-app")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-test", b"secret\r\ninvalid")] if behavior == "header" else [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"x" * 1_048_577 if behavior == "oversize" else b"secret",
                "more_body": behavior == "stream",
            }
        )

    with running(broken) as server:
        response = request(server, b"GET /?token=secret HTTP/1.1\r\nAuthorization: secret\r\n\r\n")
        assert response.startswith(b"HTTP/1.0 500")
        assert b"secret" not in response
        malformed = request(server, b"BOGUS /?secret HTTP/1.1\r\n\r\n")
        assert b"secret" not in malformed
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
