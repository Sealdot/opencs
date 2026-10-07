"""Real loopback socket evidence for the opt-in standard-library transport."""

import http.client
import json
import socket
import socketserver
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from adapters.http import AdapterError, HttpResponse, TransportError
from adapters.stdlib_http import StdlibHttpTransport


@dataclass
class CapturedRequest:
    line: bytes
    headers: dict[str, str]
    body: bytes


@contextmanager
def endpoint(response: bytes, *, delay: float = 0) -> Iterator[tuple[str, list[CapturedRequest]]]:
    requests: list[CapturedRequest] = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(2)
            line = self.rfile.readline()
            headers: dict[str, str] = {}
            while raw := self.rfile.readline():
                if raw == b"\r\n":
                    break
                name, value = raw.decode("latin-1").split(":", 1)
                headers[name.lower()] = value.strip()
            body = self.rfile.read(int(headers.get("content-length", "0")))
            requests.append(CapturedRequest(line, headers, body))
            if delay:
                time.sleep(delay)
            try:
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", requests
        finally:
            server.shutdown()
            thread.join(timeout=2)


def reply(body: bytes = b'{"id": 1}', *, status: int = 200, length: int | None = None) -> bytes:
    size = len(body) if length is None else length
    return f"HTTP/1.1 {status} Test\r\nContent-Length: {size}\r\n\r\n".encode() + body


def test_real_request_json_framing_and_response() -> None:
    transport = StdlibHttpTransport(allow_http=True)
    with endpoint(reply('{"answer":"你好"}'.encode())) as (url, requests):
        result = transport.request(
            "POST", url + "/api/messages?limit=1", {"api_access_token": "secret"}, {"text": "您好"}
        )
    assert result == HttpResponse(200, {"answer": "你好"})
    assert len(requests) == 1
    request = requests[0]
    assert request.line == b"POST /api/messages?limit=1 HTTP/1.1\r\n"
    assert request.headers["api_access_token"] == "secret"
    assert request.headers["connection"] == "close"
    assert request.headers["content-type"] == "application/json"
    assert int(request.headers["content-length"]) == len(request.body)
    assert json.loads(request.body) == {"text": "您好"}


def test_get_without_body_and_independent_connections() -> None:
    transport = StdlibHttpTransport(allow_http=True)
    with endpoint(reply()) as (url, requests):
        for _ in range(2):
            assert transport.request("GET", url, {}, None) == HttpResponse(200, {"id": 1})
    assert len(requests) == 2
    assert all(request.body == b"" for request in requests)
    assert all(request.line == b"GET / HTTP/1.1\r\n" for request in requests)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 429, 500, 503])
def test_non_success_keeps_status_discards_provider_body_and_never_retries(status: int) -> None:
    response = (
        f"HTTP/1.1 {status} Test\r\nLocation: /redirect\r\nContent-Length: 9999999\r\n\r\n"
        "provider-private-error"
    ).encode()
    with endpoint(response) as (url, requests):
        result = StdlibHttpTransport(allow_http=True).request("POST", url, {}, {})
    assert result == HttpResponse(status, {})
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json-provider-secret",
        b"[]",
        b"null",
        b'{"id":1,"id":2}',
        b'{"nested":{"id":1,"id":2}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e999}',
        b'{"value":"\xff"}',
    ],
)
def test_malformed_success_is_sanitized_unknown(body: bytes) -> None:
    with endpoint(reply(body)) as (url, requests):
        with pytest.raises(TransportError) as error:
            StdlibHttpTransport(allow_http=True).request("POST", url, {}, {})
    assert str(error.value) == "HTTP transport failed; delivery may be unknown"
    assert error.value.__suppress_context__
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        reply(b'{"id":1}', length=50),
        reply(b'{"id":1}', length=9999999),
        reply(b'{"value": "long"}'),
        b'HTTP/1.1 200 OK\r\n\r\n{"value": "long"}',
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n11\r\n"
        b'{"value": "long"}\r\n0\r\n\r\n',
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n99\r\n{}",
        b"provider-private-malformed-status\r\n\r\n",
    ],
)
def test_bounded_malformed_and_truncated_responses(response: bytes) -> None:
    with endpoint(response) as (url, requests):
        with pytest.raises(TransportError):
            StdlibHttpTransport(allow_http=True, max_response_bytes=8).request("POST", url, {}, {})
    assert len(requests) == 1


def test_response_at_exact_size_limit() -> None:
    with endpoint(reply(b'{"id":1}')) as (url, _):
        assert StdlibHttpTransport(allow_http=True, max_response_bytes=8).request(
            "GET", url, {}, None
        ) == HttpResponse(200, {"id": 1})


def test_socket_timeout_is_sanitized_without_retry() -> None:
    with endpoint(reply(), delay=0.1) as (url, requests):
        with pytest.raises(TimeoutError, match="HTTP request timed out; delivery may be unknown"):
            StdlibHttpTransport(allow_http=True, timeout_seconds=0.03).request("POST", url, {}, {})
    assert len(requests) == 1


def test_connection_refused_is_sanitized() -> None:
    # Release an unused loopback port so the kernel refuses the connection.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{reserved.getsockname()[1]}/private-path?secret=token"
    with pytest.raises(TransportError) as error:
        StdlibHttpTransport(allow_http=True, timeout_seconds=0.1).request(
            "POST", url, {"Authorization": "secret"}, {}
        )
    assert str(error.value) == "HTTP transport failed; delivery may be unknown"


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": True},
        {"max_response_bytes": 0},
        {"max_response_bytes": 1.5},
        {"max_response_bytes": True},
        {"allow_http": "yes"},
    ],
)
def test_invalid_configuration(options: dict[str, Any]) -> None:
    with pytest.raises(AdapterError):
        StdlibHttpTransport(**options)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://example.com",
        "https:///path",
        "https://user:secret@example.com",
        "https://example.com#secret",
        "https://example.com#",
        "https://example.com:abc",
        "https://example.com:999999",
        "https://example.com:0",
        "https://[invalid-ipv6]",
        " https://example.com",
        "https://example.com/\r\nInjected: value",
        "https://example.com/你好",
    ],
)
def test_disallowed_urls_fail_before_network(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("URL validation must precede connection creation")

    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    monkeypatch.setattr(http.client, "HTTPConnection", forbidden)
    with pytest.raises(AdapterError):
        StdlibHttpTransport().request("POST", url, {}, {})


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "other-host"},
        {"content-length": "3"},
        {"Transfer-Encoding": "chunked"},
        {"Connection": "keep-alive"},
        {"Authorization": "secret", "authorization": "other"},
        {"api_access_token": "secret\r\nInjected: value"},
        {"invalid name": "value"},
        {"invalid:name": "value"},
        {"X-Test": "\x00"},
        {"X-Test": "你好"},
    ],
)
def test_untrusted_headers_rejected_before_network(headers: dict[str, str]) -> None:
    with pytest.raises(AdapterError):
        StdlibHttpTransport().request("POST", "https://example.invalid", headers, {})


@pytest.mark.parametrize("body", [{"x": float("nan")}, {"x": float("inf")}, {1: "x"}, []])
def test_invalid_json_request_rejected_before_network(body: Any) -> None:
    with pytest.raises(AdapterError):
        StdlibHttpTransport().request("POST", "https://example.invalid", {}, body)


def test_circular_json_request_rejected() -> None:
    body: dict[str, Any] = {}
    body["self"] = body
    with pytest.raises(AdapterError):
        StdlibHttpTransport().request("POST", "https://example.invalid", {}, body)


@pytest.mark.parametrize("method", ["", "POST\r\nInjected: value", "GET /path"])
def test_invalid_http_method(method: str) -> None:
    with pytest.raises(AdapterError):
        StdlibHttpTransport().request(method, "https://example.invalid", {}, {})


def test_https_uses_default_verified_context_and_sanitizes_tls_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[ssl.SSLContext] = []

    def fail_tls(*args: Any, **kwargs: Any) -> Any:
        contexts.append(kwargs["context"])
        raise ssl.SSLCertVerificationError("private-host certificate detail")

    monkeypatch.setattr(http.client, "HTTPSConnection", fail_tls)
    with pytest.raises(TransportError) as error:
        StdlibHttpTransport(allow_http=True).request("GET", "https://example.invalid", {}, None)
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname
    assert "private-host" not in str(error.value)


def test_environment_proxy_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    with endpoint(reply()) as (url, _):
        assert StdlibHttpTransport(allow_http=True).request("GET", url, {}, None).status == 200
