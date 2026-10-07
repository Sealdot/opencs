"""Immutable normalized envelope; no provider SDK or schema runtime dependency."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .handoff import validate_handoff
from .validation import exact_object, json_value, nonempty

EVENT_TYPES = frozenset(
    {
        "conversation_started",
        "customer_message",
        "agent_message",
        "human_message",
        "tool_call",
        "tool_result",
        "task_started",
        "task_completed",
        "handoff_requested",
        "handoff_accepted",
        "handoff_in_progress",
        "handoff_failed",
        "handoff_completed",
        "handoff_cancelled",
        "conversation_resolved",
        "conversation_reopened",
        "evaluation_completed",
    }
)
_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _validate(payload: dict[str, Any]) -> None:
    exact_object(
        payload,
        {
            "event_id",
            "tenant_id",
            "conversation_id",
            "customer_id",
            "channel",
            "event",
            "occurred_at",
            "metadata",
        },
        "event envelope",
    )
    json_value(payload)
    for key in ("event_id", "tenant_id", "conversation_id", "customer_id"):
        nonempty(payload[key], key)
    channel = exact_object(payload["channel"], {"type", "provider"}, "channel")
    for key in channel:
        nonempty(channel[key], f"channel.{key}")
    event = exact_object(payload["event"], {"type", "content_type", "content"}, "event")
    nonempty(event["type"], "event.type")
    if event["type"] not in EVENT_TYPES:
        raise ValueError("Unknown event type")
    nonempty(event["content_type"], "event.content_type")
    if event["content"] is not None and not isinstance(event["content"], (str, dict)):
        raise ValueError("event.content must be a string, object or null")
    if not isinstance(payload["metadata"], dict):
        raise ValueError("metadata must be an object")
    timestamp = payload["occurred_at"]
    if not isinstance(timestamp, str) or not _DATE_TIME.fullmatch(timestamp):
        raise ValueError("occurred_at must be a timezone-aware RFC3339 timestamp")
    if timestamp[-1] not in "Zz" and int(timestamp[-2:]) > 59:
        raise ValueError("Invalid occurred_at timezone offset")
    try:
        parsed = datetime.fromisoformat(timestamp.upper().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid occurred_at") from exc
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must have a timezone")
    if event["type"].startswith("handoff_"):
        if event["content_type"] != "application/json" or not isinstance(event["content"], dict):
            raise ValueError("Handoff events require an application/json snapshot")
        snapshot = event["content"]
        validate_handoff(snapshot)
        if snapshot["state"] != event["type"].removeprefix("handoff_"):
            raise ValueError("Handoff state disagrees with event type")
        if any(snapshot[key] != payload[key] for key in ("tenant_id", "conversation_id")):
            raise ValueError("Handoff subject disagrees with envelope")
        if snapshot["customer"]["id"] != payload["customer_id"]:
            raise ValueError("Handoff customer disagrees with envelope")


@dataclass(frozen=True, init=False)
class Event:
    """Validated envelope owning a canonical JSON snapshot, safe across boundaries."""

    _json: str

    def __init__(self, payload: dict[str, Any]) -> None:
        _validate(payload)
        object.__setattr__(
            self,
            "_json",
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Event":
        return cls(payload)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self._json)
        return result

    @property
    def event_id(self) -> str:
        return str(self.to_dict()["event_id"])

    @property
    def tenant_id(self) -> str:
        return str(self.to_dict()["tenant_id"])

    @property
    def conversation_id(self) -> str:
        return str(self.to_dict()["conversation_id"])

    @property
    def customer_id(self) -> str:
        return str(self.to_dict()["customer_id"])

    @property
    def event_type(self) -> str:
        return str(self.to_dict()["event"]["type"])

    @property
    def content_type(self) -> str:
        return str(self.to_dict()["event"]["content_type"])

    @property
    def content(self) -> str | dict[str, Any] | None:
        result: str | dict[str, Any] | None = self.to_dict()["event"]["content"]
        return result

    @property
    def channel_type(self) -> str:
        return str(self.to_dict()["channel"]["type"])

    @property
    def provider(self) -> str:
        return str(self.to_dict()["channel"]["provider"])

    @property
    def occurred_at(self) -> str:
        return str(self.to_dict()["occurred_at"])

    @property
    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = self.to_dict()["metadata"]
        return result
