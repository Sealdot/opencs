"""Validate the immutable kit and Foundation contracts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = (
    ("event", "event"),
    ("scenario", "scenario"),
    ("evaluation-result", "evaluation-result"),
    ("handoff", "handoff"),
    ("event", "handoff-event"),
)


def read_object(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def check_kit(root: Path = ROOT) -> int:
    manifest = read_object(root / "docs/bootstrap/kit-checksums.json")
    expected = set(manifest)
    actual = {
        path.relative_to(root / "project-kit").as_posix()
        for path in (root / "project-kit").rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise ValueError(
            f"Kit inventory mismatch: missing={expected - actual}, extra={actual - expected}"
        )
    for relative, expected_hash in manifest.items():
        for prefix in (root, root / "project-kit"):
            path = prefix / relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"Kit checksum mismatch: {path}")
    return len(manifest)


def validator(name: str, root: Path = ROOT) -> Draft202012Validator:
    schema = read_object(root / f"docs/specs/schemas/{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    registry: Registry[Any] = Registry()
    for path in (root / "docs/specs/schemas").glob("*.schema.json"):
        document = read_object(path)
        Draft202012Validator.check_schema(document)
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    return Draft202012Validator(schema, format_checker=FormatChecker(), registry=registry)


def validate_contract(name: str, value: dict[str, Any]) -> None:
    """Apply structural checks and documented cross-field invariants."""
    validator(name).validate(value)
    if name == "event" and value["event"]["type"].startswith("handoff_"):
        handoff = value["event"]["content"]
        for field in ("tenant_id", "conversation_id"):
            if value[field] != handoff[field]:
                raise ValueError(f"Handoff envelope mismatch: {field}")
        if value["customer_id"] != handoff["customer"]["id"]:
            raise ValueError("Handoff envelope mismatch: customer_id")
    if name == "scenario":
        tools = value["expect"]["tools"]
        if set(tools["required"]) & set(tools["forbidden"]):
            raise ValueError("Scenario requires a forbidden tool")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-only", action="store_true", help="Public source omits the kit")
    args = parser.parse_args()
    count = None if args.contracts_only else check_kit()
    for schema_name, fixture_name in CONTRACTS:
        validate_contract(schema_name, read_object(ROOT / f"tests/fixtures/{fixture_name}.json"))
    if count is not None:
        print(f"PASS: {count} kit files preserved; {len(CONTRACTS)} contract/fixture pairs valid.")
    else:
        print(f"PASS: {len(CONTRACTS)} contract/fixture pairs valid; kit check explicitly omitted.")
    print(
        "This is Foundation validation, not runtime, domain approval or live integration evidence."
    )


if __name__ == "__main__":
    main()
