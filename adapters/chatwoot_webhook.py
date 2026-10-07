"""Signed native webhook ASGI ingress; no server, queue or persistence supplied."""

import asyncio
import json
from typing import Any

from opencs.api.app import ASGIApp, ASGIMessage, IngestionRuntime, Receive, Send
from opencs.application.memory_store import ReplayConflict

from .chatwoot import ChatwootAdapter
from .chatwoot_signing import ChatwootWebhookVerifier, WebhookAuthenticationError


async def _respond(send: Send, status: int, value: dict[str, Any]) -> None:
    body = json.dumps(value, allow_nan=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_chatwoot_app(
    adapter: ChatwootAdapter,
    runtime: IngestionRuntime,
    verifier: ChatwootWebhookVerifier,
    *,
    path: str = "/webhooks/chatwoot",
    max_body_bytes: int = 1_048_576,
) -> ASGIApp:
    """One configured instance/account/inbox/tenant binding per mounted app.

    Supply the same repository to the adapter and runtime. Delivery headers are
    not authenticated by Chatwoot's body signature, so never use them as identity
    or as a substitute for Runtime's stable, normalized event deduplication.
    """
    if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("Expected an absolute webhook path")
    if type(max_body_bytes) is not int or max_body_bytes < 1:
        raise ValueError("max_body_bytes must be positive")
    tenant_id = adapter.tenant_id

    def ingest(body: bytes, timestamp: str, signature: str) -> dict[str, Any]:
        event = adapter.normalize_signed(
            body, timestamp=timestamp, signature=signature, verifier=verifier
        )
        if event is None:
            return {"status": "ignored"}
        return runtime.ingest(event, trusted_tenant=tenant_id, can_transition=False)

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
            raise ValueError("Only HTTP and lifespan scopes are supported")
        if scope.get("path") != path:
            await _respond(send, 404, {"error": "not_found"})
            return
        if scope.get("method") != "POST":
            await _respond(send, 405, {"error": "method_not_allowed"})
            return
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        try:
            timestamps = headers.get(b"x-chatwoot-timestamp", [])
            signatures = headers.get(b"x-chatwoot-signature", [])
            if len(timestamps) != 1 or len(signatures) != 1:
                raise ValueError("Ambiguous or missing authentication")
            timestamp, signature = timestamps[0].decode("ascii"), signatures[0].decode("ascii")
        except (ValueError, UnicodeDecodeError):
            await _respond(send, 401, {"error": "webhook_unauthorized"})
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
            if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 20:
                await _respond(send, 400, {"error": "invalid_content_length"})
                return
            if int(lengths[0]) > max_body_bytes:
                await _respond(send, 413, {"error": "body_too_large"})
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if message["type"] != "http.request" or not isinstance(chunk, bytes):
                await _respond(send, 400, {"error": "invalid_http_request"})
                return
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
            result = await asyncio.to_thread(ingest, bytes(body), timestamp, signature)
        except WebhookAuthenticationError:
            await _respond(send, 401, {"error": "webhook_unauthorized"})
            return
        except ReplayConflict:
            await _respond(send, 409, {"error": "event_replay_conflict"})
            return
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            await _respond(send, 400, {"error": "invalid_json"})
            return
        except PermissionError:
            await _respond(send, 403, {"error": "event_forbidden"})
            return
        except (ValueError, TypeError, KeyError):
            await _respond(send, 422, {"error": "invalid_webhook"})
            return
        except Exception:
            await _respond(send, 500, {"error": "runtime_error"})
            return
        # No asynchronous acceptance without a durable queue. Stored failed or
        # unknown outcomes remain 200, not permission to repeat external actions.
        await _respond(send, 200, result)

    return app
