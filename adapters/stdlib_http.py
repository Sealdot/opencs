"""Explicit, one-shot HTTP transport using the Python standard library.

HTTPS uses system trust and hostname verification. ``allow_http`` is an explicit
opt-in for controlled test or private endpoints; it does not disable TLS checks.
Timeouts bound individual socket operations, not DNS resolution or total elapsed
time. There are no retries, redirects, environment proxies, or implicit auth.
"""

import http.client
import json
import math
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

from .http import AdapterError, HttpResponse, TransportError

_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_OWNED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _check_json(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _check_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_json(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")


class StdlibHttpTransport:
    """Return strict JSON objects for 2xx; discard all non-2xx response bodies.

    A timeout or transport failure may occur after the server accepted an action.
    Callers must preserve an unknown outcome and must not blindly retry.
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_048_576,
        *,
        allow_http: bool = False,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise AdapterError("timeout_seconds must be finite and positive")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise AdapterError("max_response_bytes must be a positive integer")
        if type(allow_http) is not bool:
            raise AdapterError("allow_http must be a boolean")
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.allow_http = allow_http

    def request(
        self, method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
    ) -> HttpResponse:
        if not isinstance(method, str) or not _TOKEN.fullmatch(method):
            raise AdapterError("Invalid HTTP method")
        if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise AdapterError("Invalid HTTP URL")
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
            if (
                parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and not self.allow_http)
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
                or "#" in url
                or (port is not None and port == 0)
            ):
                raise ValueError
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            target.encode("ascii")
        except (ValueError, UnicodeError):
            raise AdapterError("Invalid or disallowed HTTP URL") from None
        request_headers = self._headers(headers)
        payload: bytes | None = None
        if json_body is not None:
            try:
                if not isinstance(json_body, dict):
                    raise ValueError
                _check_json(json_body)
                payload = json.dumps(json_body, allow_nan=False, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise AdapterError("Request body must be a finite JSON object") from None
            if not any(name.lower() == "content-type" for name in request_headers):
                request_headers["Content-Type"] = "application/json"
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            if parsed.scheme == "https":
                connection = http.client.HTTPSConnection(
                    hostname,
                    port,
                    timeout=self.timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                connection = http.client.HTTPConnection(
                    hostname, port, timeout=self.timeout_seconds
                )
            request_headers["Connection"] = "close"
            connection.request(method, target, body=payload, headers=request_headers)
            response = connection.getresponse()
            if not 100 <= response.status < 600:
                raise ValueError
            if not 200 <= response.status < 300:
                return HttpResponse(response.status, {})
            expected_length = response.length
            if expected_length is not None and expected_length > self.max_response_bytes:
                raise ValueError
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes or (
                expected_length is not None and len(data) != expected_length
            ):
                raise ValueError
            body = json.loads(
                data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_reject_constant
            )
            if not isinstance(body, dict):
                raise ValueError
            _check_json(body)
            return HttpResponse(response.status, body)
        except TimeoutError:
            raise TimeoutError("HTTP request timed out; delivery may be unknown") from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError):
            raise TransportError("HTTP transport failed; delivery may be unknown") from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    @staticmethod
    def _headers(headers: dict[str, str]) -> dict[str, str]:
        if not isinstance(headers, dict):
            raise AdapterError("Invalid HTTP headers")
        result: dict[str, str] = {}
        seen: set[str] = set()
        for name, value in headers.items():
            if (
                not isinstance(name, str)
                or not _TOKEN.fullmatch(name)
                or name.lower() in seen | _OWNED_HEADERS
                or not isinstance(value, str)
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise AdapterError("Invalid or transport-owned HTTP header")
            try:
                value.encode("latin-1")
            except UnicodeError:
                raise AdapterError("Invalid HTTP header encoding") from None
            result[name] = value
            seen.add(name.lower())
        return result
