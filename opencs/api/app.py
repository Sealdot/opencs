"""Small ASGI ingress for offline runtime contracts; no server or framework dependency."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from opencs.application.memory_store import ReplayConflict
from opencs.domain import Event

ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIMessage, Receive, Send], Awaitable[None]]


class IngestionRuntime(Protocol):
    def ingest(
        self, event: Event, *, trusted_tenant: str, can_transition: bool = False
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AccessGrant:
    tenant_id: str
    can_transition: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("Access grant requires a tenant")
        if type(self.can_transition) is not bool:
            raise ValueError("Transition permission must be boolean")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


async def _respond(send: Send, status: int, value: dict[str, Any]) -> None:
    body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def create_app(
    runtime: IngestionRuntime,
    credentials: dict[str, AccessGrant],
    *,
    max_body_bytes: int = 1_048_576,
) -> ASGIApp:
    """Bind immutable credential grants; credentials are never read from request bodies."""
    if type(max_body_bytes) is not int or max_body_bytes < 1:
        raise ValueError("max_body_bytes must be a positive integer")
    grants = dict(credentials)
    if any(
        not isinstance(token, str)
        or not token
        or any(c.isspace() for c in token)
        or not isinstance(grant, AccessGrant)
        for token, grant in grants.items()
    ):
        raise ValueError("Credentials must map nonempty token strings to AccessGrant")

    async def app(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            raise ValueError("Only HTTP and lifespan ASGI scopes are supported")
        path, method = scope.get("path"), scope.get("method")
        if path in {"/health/live", "/health/ready"}:
            if method != "GET":
                await _respond(send, 405, {"error": "method_not_allowed"})
                return
            await _respond(
                send,
                200,
                {
                    "status": "alive" if path == "/health/live" else "ready_for_offline",
                    "mode": "offline",
                    "storage": "inmemory",
                    "durable": False,
                    "live_integrations": "not_run",
                },
            )
            return
        if path != "/v1/events":
            await _respond(send, 404, {"error": "not_found"})
            return
        if method != "POST":
            await _respond(send, 405, {"error": "method_not_allowed"})
            return
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        authorization = headers.get(b"authorization", [])
        grant = None
        if len(authorization) == 1:
            try:
                scheme, token = authorization[0].decode("ascii").split(" ", 1)
                if scheme.lower() == "bearer":
                    grant = grants.get(token)
            except (UnicodeDecodeError, ValueError):
                pass
        if grant is None:
            await _respond(send, 401, {"error": "unauthorized"})
            return
        content_types = headers.get(b"content-type", [])
        if (
            len(content_types) != 1
            or content_types[0].split(b";", 1)[0].strip().lower() != b"application/json"
        ):
            await _respond(send, 415, {"error": "json_content_type_required"})
            return
        lengths = headers.get(b"content-length", [])
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError("Invalid Content-Length")
                if int(lengths[0]) > max_body_bytes:
                    await _respond(send, 413, {"error": "body_too_large"})
                    return
            except ValueError:
                await _respond(send, 400, {"error": "invalid_content_length"})
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request" or not isinstance(message.get("body", b""), bytes):
                await _respond(send, 400, {"error": "invalid_http_request"})
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > max_body_bytes:
                await _respond(send, 413, {"error": "body_too_large"})
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if lengths and int(lengths[0]) != len(body):
            await _respond(send, 400, {"error": "content_length_mismatch"})
            return
        try:
            payload = json.loads(
                body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            await _respond(send, 400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            await _respond(send, 422, {"error": "invalid_event"})
            return
        if "tenant_id" in payload and payload["tenant_id"] != grant.tenant_id:
            await _respond(send, 403, {"error": "tenant_forbidden"})
            return
        try:
            event = Event.from_dict(payload)
        except (ValueError, TypeError, RecursionError):
            await _respond(send, 422, {"error": "invalid_event"})
            return
        try:
            result = await asyncio.to_thread(
                runtime.ingest,
                event,
                trusted_tenant=grant.tenant_id,
                can_transition=grant.can_transition,
            )
        except PermissionError:
            await _respond(send, 403, {"error": "event_forbidden"})
            return
        except ReplayConflict:
            await _respond(send, 409, {"error": "event_replay_conflict"})
            return
        except KeyError:
            await _respond(send, 404, {"error": "subject_not_found"})
            return
        except (ValueError, TypeError):
            await _respond(send, 422, {"error": "invalid_event"})
            return
        except Exception:
            await _respond(send, 500, {"error": "runtime_error"})
            return
        # Processing failures/unknown deliveries are recorded application outcomes,
        # not permission to repeat the external action through an HTTP retry.
        await _respond(send, 200, result)

    return app
