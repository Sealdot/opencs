"""Strict structured action boundary shared by fake and provider agents."""

import json
from copy import deepcopy
from typing import Any


def validate_decision(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"intent", "action"}:
        raise ValueError("Decision requires exactly intent and action")
    if not isinstance(value["intent"], str) or not value["intent"].strip():
        raise ValueError("Decision intent must be nonempty")
    action = value["action"]
    if not isinstance(action, dict):
        raise ValueError("Decision action must be an object")
    kind = action.get("type")
    fields = {
        "reply": {"type", "text"},
        "tool": {"type", "name", "arguments"},
        "handoff": {"type", "reason", "summary"},
        "done": {"type"},
    }
    if not isinstance(kind, str) or kind not in fields or set(action) != fields[kind]:
        raise ValueError("Unknown action or unexpected action fields")
    for key in ("text", "name", "reason", "summary"):
        if key in action and (not isinstance(action[key], str) or not action[key].strip()):
            raise ValueError(f"Action {key} must be nonempty")
    if kind == "tool" and not isinstance(action["arguments"], dict):
        raise ValueError("Tool arguments must be an object")
    json.dumps(value, allow_nan=False)
    return deepcopy(value)
