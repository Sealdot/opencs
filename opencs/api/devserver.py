"""Loopback-only HTTP bridge for local development with OpenCS's HTTP ASGI apps.

This is not a general ASGI server: no lifespan, streaming, WebSockets, TLS, worker
management or production hardening. Each request closes its HTTP connection.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote_to_bytes

from opencs.api.app import ASGIApp, ASGIMessage

_MAX_RESPONSE_BYTES = 1_048_576
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class _DevServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Socket-server tracebacks can contain application secrets. The listener
        # deliberately has no access/error logging; application audits are separate.
        pass


def create_dev_server(
    app: ASGIApp,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    max_body_bytes: int = 1_048_576,
    request_timeout_seconds: float = 10.0,
) -> ThreadingHTTPServer:
    """Bind an explicit local HTTP listener; callers own serving and shutdown.

    The timeout limits socket-read inactivity, not application execution time.
    Incoming bodies require Content-Length; chunked transfer is unsupported.
    The application must send one response start and one complete response body.
    """
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Development server host must be loopback")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    if type(max_body_bytes) is not int or max_body_bytes < 1:
        raise ValueError("max_body_bytes must be a positive integer")
    if (
        isinstance(request_timeout_seconds, bool)
        or not isinstance(request_timeout_seconds, (int, float))
        or not math.isfinite(request_timeout_seconds)
        or request_timeout_seconds <= 0
    ):
        raise ValueError("request_timeout_seconds must be finite and positive")

    class Handler(BaseHTTPRequestHandler):
        raw_requestline: bytes
        protocol_version = "HTTP/1.0"
        server_version = "OpenCSDev"
        sys_version = ""

        def setup(self) -> None:
            self.request.settimeout(request_timeout_seconds)
            super().setup()

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            self._error(code, "invalid_http_request")

        def _error(self, status: int, error: str) -> None:
            body = json.dumps({"error": error}).encode("ascii")
            self._write_response(status, [(b"content-type", b"application/json")], body)

        def _write_response(
            self, status: int, headers: list[tuple[bytes, bytes]], body: bytes
        ) -> None:
            self.close_connection = True
            try:
                self.send_response(status)
                for name, value in headers:
                    if name.lower() not in {b"content-length", b"connection"}:
                        self.send_header(name.decode("ascii"), value.decode("latin-1"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if getattr(self, "command", None) != "HEAD":
                    self.wfile.write(body)
            except OSError:
                # The peer can close while receiving either an error or a result.
                pass

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            target = self.raw_requestline.split()[1]
            # Validate the original target: BaseHTTPRequestHandler normalizes //.
            if (
                self.request_version not in {"HTTP/1.0", "HTTP/1.1"}
                or not target.startswith(b"/")
                or target.startswith(b"//")
                or b"#" in target
                or any(byte <= 32 or byte >= 127 for byte in target)
            ):
                self._error(400, "invalid_request_target")
                return False
            self.close_connection = True
            return True

        def _dispatch(self) -> None:
            if self.headers.get_all("Transfer-Encoding"):
                self._error(400, "transfer_encoding_unsupported")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or (
                lengths and (not lengths[0].isascii() or not lengths[0].isdigit())
            ):
                self._error(400, "invalid_content_length")
                return
            if not lengths and self.command == "POST":
                self._error(411, "content_length_required")
                return
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                self._error(400, "invalid_content_length")
                return
            if length > max_body_bytes:
                self._error(413, "body_too_large")
                return
            try:
                body = self.rfile.read(length)
            except TimeoutError:
                self._error(408, "request_timeout")
                return
            if len(body) != length:
                self._error(400, "incomplete_body")
                return
            raw_target = self.raw_requestline.split()[1]
            raw_path, _, query_string = raw_target.partition(b"?")
            scope: ASGIMessage = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": self.request_version.removeprefix("HTTP/"),
                "method": self.command,
                "scheme": "http",
                "path": unquote_to_bytes(raw_path).decode("utf-8", errors="replace"),
                "raw_path": raw_path,
                "query_string": query_string,
                "root_path": "",
                "headers": [
                    (name.lower().encode("ascii"), value.encode("latin-1"))
                    for name, value in self.headers.raw_items()
                ],
                "server": self.server.server_address,
                "client": self.client_address,
            }
            try:
                status, headers, response_body = asyncio.run(_call_app(app, scope, body))
            except Exception:
                self._error(500, "application_error")
                return
            self._write_response(status, headers, response_body)

        do_GET = _dispatch
        do_POST = _dispatch
        do_HEAD = _dispatch
        do_PUT = _dispatch
        do_PATCH = _dispatch
        do_DELETE = _dispatch
        do_OPTIONS = _dispatch

    return _DevServer(("127.0.0.1", port), Handler)


async def _call_app(
    app: ASGIApp, scope: ASGIMessage, body: bytes
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    received = False
    messages: list[ASGIMessage] = []

    async def receive() -> ASGIMessage:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: ASGIMessage) -> None:
        if len(messages) >= 2:
            raise ValueError("Development server does not support streaming")
        if (
            not isinstance(message.get("body", b""), bytes)
            or len(message.get("body", b"")) > _MAX_RESPONSE_BYTES
        ):
            raise ValueError("Invalid development server response body")
        messages.append(dict(message))

    await app(scope, receive, send)
    if (
        len(messages) != 2
        or messages[0].get("type") != "http.response.start"
        or messages[1].get("type") != "http.response.body"
        or messages[1].get("more_body", False)
    ):
        raise ValueError("Unsupported development server response")
    status = messages[0].get("status")
    if type(status) is not int or not 200 <= status <= 599:
        raise ValueError("Invalid development server response status")
    headers: list[tuple[bytes, bytes]] = []
    for name, value in messages[0].get("headers", []):
        if (
            not isinstance(name, bytes)
            or not isinstance(value, bytes)
            or not _HEADER_NAME.fullmatch(name)
            or any(byte < 32 or byte == 127 for byte in value)
            or name.lower() == b"transfer-encoding"
        ):
            raise ValueError("Invalid development server response header")
        headers.append((name, value))
    return status, headers, messages[1].get("body", b"")
