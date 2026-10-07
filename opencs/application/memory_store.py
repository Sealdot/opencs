"""Thread-safe, non-durable RuntimeStore test implementation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import Any


class ReplayConflict(ValueError):
    """An event key was reused for a different normalized payload."""


class MemoryRuntimeStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._processing: dict[tuple[str, str], RLock] = {}
        self._events: dict[tuple[str, str], dict[str, Any]] = {}
        self._audits: list[dict[str, Any]] = []
        self._handoffs: dict[tuple[str, str], dict[str, Any]] = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    @contextmanager
    def processing(self, tenant_id: str, conversation_id: str) -> Iterator[None]:
        with self._lock:
            lock = self._processing.setdefault((tenant_id, conversation_id), RLock())
        with lock:
            yield

    def claim(self, event: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        key = (event["tenant_id"], event["event_id"])
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock:
            if key in self._events:
                record = self._events[key]
                if record["payload"] != payload:
                    self.append_audit(
                        event["tenant_id"],
                        event["conversation_id"],
                        "replay_conflict",
                        {"event_id": event["event_id"]},
                    )
                    raise ReplayConflict("Event id reused with different payload")
                return False, deepcopy(record["result"])
            result = {"status": "pending", "event_id": event["event_id"]}
            self._events[key] = {"payload": payload, "result": result}
            return True, deepcopy(result)

    def finish(self, tenant_id: str, event_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._events[tenant_id, event_id]["result"] = deepcopy(result)

    def append_audit(
        self, tenant_id: str, conversation_id: str, kind: str, data: dict[str, Any]
    ) -> str:
        with self._lock:
            ref = f"audit:{len(self._audits) + 1}"
            self._audits.append(
                {
                    "ref": ref,
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "kind": kind,
                    "data": deepcopy(data),
                }
            )
            return ref

    def list_audit(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(
                [
                    x
                    for x in self._audits
                    if x["tenant_id"] == tenant_id and x["conversation_id"] == conversation_id
                ]
            )

    def get_handoff(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._handoffs.get((tenant_id, conversation_id)))

    def save_handoff(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._handoffs[snapshot["tenant_id"], snapshot["conversation_id"]] = deepcopy(snapshot)
