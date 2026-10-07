"""Chatwoot IM contracts using a trusted integration binding and injected HTTP."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from opencs.domain import Conversation, Event

from .chatwoot_signing import ChatwootWebhookVerifier
from .http import AdapterError, HttpTransport, request_once


class MappingRepository(Protocol):
    def resolve_external(
        self, tenant: str, provider: str, external_conversation_id: str, external_contact_id: str
    ) -> Conversation: ...


def positive_id(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise AdapterError("Expected positive provider id")
    return int(value)


def checked_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise AdapterError("Expected configured HTTP(S) URL without credentials/query/fragment")
    return url.rstrip("/")


class ChatwootAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        account_id: int,
        inbox_id: int,
        tenant_id: str,
        token: str,
        transport: HttpTransport,
        repository: MappingRepository,
        webhook_secret: str,
        team_id: int | None = None,
        before_request: Callable[[dict[str, Any]], None] | None = None,
        handoff_mode: Literal["assignment", "agent_bot"] = "assignment",
    ) -> None:
        self.base_url = checked_url(base_url)
        self.account_id = positive_id(account_id)
        self.inbox_id = positive_id(inbox_id)
        if not all(
            isinstance(value, str) and value.strip() for value in (tenant_id, token, webhook_secret)
        ):
            raise AdapterError("Tenant, token and integration credential are required")
        self.tenant_id = tenant_id
        self._token = token
        self._webhook_secret = webhook_secret
        self.transport = transport
        self.repository = repository
        self.team_id = positive_id(team_id) if team_id is not None else None
        self._targets: dict[str, int] = {}
        self.before_request = before_request
        if not isinstance(handoff_mode, str) or handoff_mode not in {"assignment", "agent_bot"}:
            raise AdapterError("Unsupported Chatwoot handoff mode")
        self.handoff_mode = handoff_mode
        # Process-local replay protection, not a durable outbox or recovery worker.
        self._handoff_lock = RLock()
        self._handoff_deliveries: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}

    def normalize(self, payload: dict[str, Any], *, credential: str) -> Event | None:
        # Credential represents an authenticated gateway/integration secret. This
        # is not a claim that Chatwoot v4.17.1 signs native webhooks this way.
        if not isinstance(credential, str) or not hmac.compare_digest(
            credential.encode(), self._webhook_secret.encode()
        ):
            raise AdapterError("Invalid integration credential")
        return self._normalize(payload)

    def normalize_signed(
        self,
        body: bytes,
        *,
        timestamp: str,
        signature: str,
        verifier: ChatwootWebhookVerifier,
    ) -> Event | None:
        """Authenticate original bytes before parsing or resolving any identities."""
        verifier.verify(body, timestamp=timestamp, signature=signature)

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result

        payload = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        # Reject non-finite numbers, including overflowed exponents, anywhere.
        json.dumps(payload, allow_nan=False)
        if not isinstance(payload, dict):
            raise AdapterError("Expected a webhook object")
        return self._normalize(payload)

    def _normalize(self, payload: dict[str, Any]) -> Event | None:
        try:
            if positive_id(payload["account"]["id"]) != self.account_id:
                raise AdapterError("Wrong account binding")
            if positive_id(payload["inbox"]["id"]) != self.inbox_id:
                raise AdapterError("Wrong inbox binding")
            if payload.get("event") != "message_created":
                return None
            if payload.get("message_type") != "incoming" or payload.get("private") is not False:
                return None
            if payload.get("content_type") != "text" or payload.get("attachments"):
                return None
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip():
                return None
            external_id = positive_id(payload["conversation"]["id"])
            contact_id = positive_id(payload["sender"]["id"])
            message_id = positive_id(payload["id"])
            occurred_at = datetime.fromisoformat(payload["created_at"])
            if occurred_at.tzinfo is None:
                raise AdapterError("Provider timestamp requires timezone")
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError("Malformed or incorrectly bound Chatwoot message") from exc
        scope = [self.base_url, self.account_id]
        conversation = self.repository.resolve_external(
            self.tenant_id,
            "chatwoot",
            json.dumps([*scope, external_id]),
            json.dumps([*scope, contact_id]),
        )
        source = json.dumps([self.tenant_id, *scope, message_id, "message_created"])
        event = Event.from_dict(
            {
                "event_id": f"evt_{uuid5(NAMESPACE_URL, source)}",
                "tenant_id": self.tenant_id,
                "conversation_id": conversation.id,
                "customer_id": conversation.customer_id,
                "channel": {"type": "im", "provider": "chatwoot"},
                "event": {"type": "customer_message", "content_type": "text", "content": content},
                "occurred_at": occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "metadata": {
                    "external_reference": {
                        "instance_id": self.base_url,
                        "account_id": self.account_id,
                        "inbox_id": self.inbox_id,
                        "external_conversation_id": external_id,
                        "contact_id": contact_id,
                        "message_id": message_id,
                    }
                },
            }
        )
        self._targets[conversation.id] = external_id
        return event

    def _target(self, tenant_id: str, conversation_id: str) -> str:
        if tenant_id != self.tenant_id or conversation_id not in self._targets:
            raise AdapterError("No trusted outbound conversation binding")
        return (
            f"{self.base_url}/api/v1/accounts/{self.account_id}/conversations/"
            f"{self._targets[conversation_id]}"
        )

    def _post(
        self,
        url: str,
        body: dict[str, Any],
        action_id: str,
        *,
        conversation_id: str,
        response_check: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.before_request is not None:
            self.before_request(
                {
                    "action_id": action_id,
                    "tenant_id": self.tenant_id,
                    "conversation_id": conversation_id,
                    "operation": url.rsplit("/", 1)[-1],
                    "url": url,
                }
            )
        result = request_once(
            self.transport,
            "POST",
            url,
            {"api_access_token": self._token, "Content-Type": "application/json"},
            body,
            action_id,
        )
        if result["delivery"] == "sent" and url.endswith("/messages"):
            try:
                positive_id(result["body"].get("id"))
            except (TypeError, ValueError):
                result.update(delivery="unknown", error="invalid_message_response")
        if result["delivery"] == "sent" and response_check is not None:
            try:
                response_check(result["body"])
            except (ValueError, TypeError, KeyError):
                result.update(delivery="unknown", error="invalid_handoff_response")
        # Keep provider body out of the public audit result (may contain PII).
        result.pop("body", None)
        return result

    def send(
        self, *, tenant_id: str, conversation_id: str, text: str, action_id: str
    ) -> dict[str, Any]:
        target = self._target(tenant_id, conversation_id)
        if not isinstance(text, str) or not text.strip():
            raise AdapterError("Reply text is required")
        return self._post(
            f"{target}/messages",
            {
                "content": text,
                "message_type": "outgoing",
                "private": False,
                "content_type": "text",
            },
            action_id,
            conversation_id=conversation_id,
        )

    def handoff(
        self, *, tenant_id: str, conversation_id: str, snapshot: dict[str, Any], action_id: str
    ) -> dict[str, Any]:
        target = self._target(tenant_id, conversation_id)
        if self.team_id is None:
            raise AdapterError("A trusted handoff team must be configured")
        if (
            snapshot.get("tenant_id") != tenant_id
            or snapshot.get("conversation_id") != conversation_id
        ):
            raise AdapterError("Handoff context does not match binding")
        if not isinstance(action_id, str) or not action_id.strip():
            raise AdapterError("action_id is required")
        mode, team_id = self.handoff_mode, self.team_id
        external_id, inbox_id = self._targets[conversation_id], self.inbox_id
        context = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
        fingerprint = json.dumps([target, team_id, mode, context], separators=(",", ":"))
        key = (tenant_id, action_id)
        with self._handoff_lock:
            previous = self._handoff_deliveries.get(key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise AdapterError("Handoff action id reused with different context or target")
                return deepcopy(previous[1])
            result: dict[str, Any] = {
                "action_id": action_id,
                "delivery": "unknown",
                "actions": [],
            }
            self._handoff_deliveries[key] = (fingerprint, result)
            steps: list[tuple[str, str, dict[str, Any]]] = [
                (
                    "context",
                    "messages",
                    {
                        "content": context,
                        "message_type": "outgoing",
                        "private": True,
                        "content_type": "text",
                    },
                ),
                ("assignment", "assignments", {"team_id": team_id}),
            ]
            if mode == "agent_bot":
                steps.append(("open", "toggle_status", {"status": "open"}))
            for operation, endpoint, body in steps:
                child_id = f"{action_id}:{operation}"
                # Claim before even invoking the optional pre-request audit hook.
                # Reentrant calls and interrupted executions cannot repeat this step.
                result["actions"].append(
                    {"action_id": child_id, "delivery": "unknown", "error": "request_in_progress"}
                )

                def check(response: dict[str, Any], step: str = operation) -> None:
                    if step == "context":
                        if (
                            response.get("private") is not True
                            or positive_id(response.get("conversation_id")) != external_id
                            or positive_id(response.get("inbox_id")) != inbox_id
                            or type(response.get("message_type")) is not int
                            or response["message_type"] != 1
                            or response.get("content_type") != "text"
                        ):
                            raise AdapterError("Wrong private context response")
                    elif step == "assignment":
                        if positive_id(response.get("id")) != team_id:
                            raise AdapterError("Wrong assigned team response")
                    else:
                        payload = response["payload"]
                        if not isinstance(payload, dict) or (
                            positive_id(payload.get("conversation_id")) != external_id
                            or payload.get("current_status") != "open"
                            or (payload["success"] is not None and payload["success"] is not True)
                        ):
                            raise AdapterError("Conversation was not confirmed open")

                try:
                    outcome = self._post(
                        f"{target}/{endpoint}",
                        body,
                        child_id,
                        conversation_id=conversation_id,
                        response_check=check if mode == "agent_bot" else None,
                    )
                except Exception:
                    # A transport may raise after sending. Retain prior successes
                    # and stop; an exception is not evidence that retry is safe.
                    outcome = {
                        "action_id": child_id,
                        "delivery": "unknown",
                        "error": "request_error",
                    }
                result["actions"][-1] = outcome
                if outcome["delivery"] != "sent":
                    result["delivery"] = outcome["delivery"]
                    return deepcopy(result)
            result["delivery"] = "sent"
            return deepcopy(result)
