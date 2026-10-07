"""Dependency-free validation shared by the 0.0 domain contracts."""

import math
from typing import Any


def nonempty(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")


def json_value(value: Any) -> None:
    """Reject Python-only values, non-string keys, cycles and non-finite numbers."""

    def visit(item: Any, ancestors: set[int]) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return
        if isinstance(item, (dict, list)):
            if id(item) in ancestors:
                raise ValueError("JSON cannot contain cycles")
            lineage = ancestors | {id(item)}
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("JSON object keys must be strings")
                    visit(child, lineage)
            else:
                for child in item:
                    visit(child, lineage)
            return
        raise ValueError("Unsupported JSON value")

    try:
        visit(value, set())
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def exact_object(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"Invalid {field} fields")
    return value
