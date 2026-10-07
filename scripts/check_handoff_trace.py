"""Executable specification for single handoff transitions, not a runtime store.

Evidence authenticity, durable concurrency and event idempotency must be tested
in the later runtime. Here evidence triggers are trusted synthetic test inputs.
"""

from typing import Any

from scripts.check_foundation import ROOT, read_object, validate_contract


def check_transition(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    trigger: str,
    expected_version: int,
    active_handoff_id: str | None = None,
) -> None:
    validate_contract("handoff", current)
    lifecycle = read_object(ROOT / "docs/specs/handoff-lifecycle.json")
    if active_handoff_id is not None and active_handoff_id != current["id"]:
        raise ValueError("Another handoff is active")
    if previous is None:
        if (
            current["state"] != lifecycle["initial_state"]
            or current["attempt"] != 1
            or current["version"] != 1
            or expected_version != 0
            or trigger != "request"
        ):
            raise ValueError("Invalid initial handoff")
        return
    validate_contract("handoff", previous)
    for field in ("id", "tenant_id", "conversation_id"):
        if previous[field] != current[field]:
            raise ValueError(f"Handoff identity changed: {field}")
    if previous["customer"]["id"] != current["customer"]["id"]:
        raise ValueError("Handoff customer changed")
    if expected_version != previous["version"] or current["version"] != previous["version"] + 1:
        raise ValueError("Version conflict")
    edge = {"from": previous["state"], "to": current["state"], "trigger": trigger}
    if edge not in lifecycle["transitions"]:
        raise ValueError("Invalid state edge or evidence trigger")
    retry = previous["state"] == "failed" and current["state"] == "requested"
    if current["attempt"] != previous["attempt"] + int(retry):
        raise ValueError("Stale or invalid attempt")
