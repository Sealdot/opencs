"""Explicitly injected HTTP contract; this module never opens network sockets."""

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict[str, Any]


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(repr=False)
    json_body: dict[str, Any] | None = None


class HttpTransport(Protocol):
    def request(
        self, method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
    ) -> HttpResponse: ...


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | TimeoutError] | None = None) -> None:
        self.responses = deque(responses or [])
        self.requests: list[HttpRequest] = []

    def request(
        self, method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
    ) -> HttpResponse:
        self.requests.append(HttpRequest(method, url, deepcopy(headers), deepcopy(json_body)))
        if not self.responses:
            raise RuntimeError("FakeTransport has no queued response; no network fallback")
        response = self.responses.popleft()
        if isinstance(response, TimeoutError):
            raise response
        return deepcopy(response)


class AdapterError(ValueError):
    """Invalid configuration, untrusted binding or malformed provider response."""


class TransportError(AdapterError):
    """Uncertain network/response failure; delivery may already have occurred."""


def request_once(
    transport: HttpTransport,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    action_id: str,
) -> dict[str, Any]:
    """Return safe audit facts; do not expose credentials or raw provider errors."""
    if not isinstance(action_id, str) or not action_id.strip():
        raise AdapterError("action_id is required")
    try:
        response = transport.request(method, url, headers, body)
    except TimeoutError:
        return {"action_id": action_id, "delivery": "unknown", "error": "timeout"}
    except TransportError:
        return {"action_id": action_id, "delivery": "unknown", "error": "transport_error"}
    if not isinstance(response, HttpResponse) or not isinstance(response.body, dict):
        return {"action_id": action_id, "delivery": "unknown", "error": "invalid_response"}
    if type(response.status) is not int or not 100 <= response.status < 600:
        return {"action_id": action_id, "delivery": "unknown", "error": "invalid_status"}
    return {
        "action_id": action_id,
        "delivery": "sent" if 200 <= response.status < 300 else "failed",
        "status": response.status,
        "body": deepcopy(response.body),
    }
