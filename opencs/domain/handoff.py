"""Pure validation of the approved D2/D3 handoff snapshot and seven edges."""

from typing import Any

from .validation import exact_object, json_value, nonempty

STATES = frozenset({"requested", "accepted", "in_progress", "completed", "cancelled", "failed"})
ACTIVE_STATES = frozenset({"requested", "accepted", "in_progress"})
TRANSITIONS = frozenset(
    {
        ("requested", "accepted", "human_acceptance"),
        ("requested", "cancelled", "cancellation"),
        ("requested", "failed", "failure"),
        ("accepted", "in_progress", "human_service_action"),
        ("in_progress", "completed", "completion"),
        ("in_progress", "failed", "failure"),
        ("failed", "requested", "retry"),
    }
)
FIELDS = {
    "id",
    "tenant_id",
    "conversation_id",
    "state",
    "attempt",
    "version",
    "from",
    "to",
    "reason",
    "customer",
    "intent",
    "priority",
    "summary",
    "facts",
    "actions_completed",
    "actions_pending",
    "recommended_action",
    "evidence_refs",
}


def validate_handoff(snapshot: dict[str, Any]) -> None:
    exact_object(snapshot, FIELDS, "handoff")
    json_value(snapshot)
    for field in ("id", "tenant_id", "conversation_id", "state", "summary"):
        nonempty(snapshot[field], field)
    if snapshot["state"] not in STATES:
        raise ValueError("Unknown handoff state")
    for field in ("attempt", "version"):
        if type(snapshot[field]) is not int or snapshot[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    for field, keys in {
        "from": {"type", "id"},
        "to": {"type", "id"},
        "reason": {"code", "description"},
        "priority": {"level", "reason"},
    }.items():
        nested = exact_object(snapshot[field], keys, field)
        for key in keys:
            nonempty(nested[key], f"{field}.{key}")
    customer = exact_object(snapshot["customer"], {"id", "organization_id"}, "customer")
    nonempty(customer["id"], "customer.id")
    if customer["organization_id"] is not None:
        nonempty(customer["organization_id"], "customer.organization_id")
    intent = exact_object(snapshot["intent"], {"primary", "confidence"}, "intent")
    nonempty(intent["primary"], "intent.primary")
    confidence = intent["confidence"]
    if confidence is not None and (
        type(confidence) not in (int, float) or not 0 <= confidence <= 1
    ):
        raise ValueError("confidence must be null or between zero and one")
    action = exact_object(snapshot["recommended_action"], {"tool", "parameters"}, "action")
    nonempty(action["tool"], "recommended_action.tool")
    if not isinstance(action["parameters"], dict):
        raise ValueError("recommended_action.parameters must be an object")
    for field in ("facts", "actions_completed", "actions_pending", "evidence_refs"):
        if not isinstance(snapshot[field], list):
            raise ValueError(f"{field} must be an array")
        for value in snapshot[field]:
            nonempty(value, field)
    if (
        snapshot["state"] in {"accepted", "in_progress", "completed"}
        and not snapshot["evidence_refs"]
    ):
        raise ValueError("Responsibility transition requires evidence")


def transition_handoff(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    trigger: str,
    expected_version: int,
    active_handoff_id: str | None = None,
) -> None:
    validate_handoff(current)
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    if active_handoff_id is not None and active_handoff_id != current["id"]:
        raise ValueError("Another handoff is active")
    if previous is None:
        if (
            current["state"],
            current["attempt"],
            current["version"],
            expected_version,
            trigger,
        ) != ("requested", 1, 1, 0, "request"):
            raise ValueError("Invalid initial handoff")
        return
    validate_handoff(previous)
    for key in ("id", "tenant_id", "conversation_id"):
        if current[key] != previous[key]:
            raise ValueError(f"Handoff identity changed: {key}")
    if current["customer"]["id"] != previous["customer"]["id"]:
        raise ValueError("Handoff customer changed")
    if expected_version != previous["version"] or current["version"] != previous["version"] + 1:
        raise ValueError("Version conflict")
    if (previous["state"], current["state"], trigger) not in TRANSITIONS:
        raise ValueError("Invalid state edge or evidence trigger")
    retry = previous["state"] == "failed" and current["state"] == "requested"
    if current["attempt"] != previous["attempt"] + int(retry):
        raise ValueError("Stale or invalid attempt")
