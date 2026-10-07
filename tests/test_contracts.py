"""Candidate format regression checks; no business runtime is implemented."""

from copy import deepcopy
from typing import Any

import pytest
from jsonschema import ValidationError

from scripts.check_foundation import CONTRACTS, ROOT, read_object, validator


def fixture(name: str) -> dict[str, Any]:
    return deepcopy(read_object(ROOT / f"tests/fixtures/{name}.json"))


@pytest.mark.parametrize(("schema_name", "fixture_name"), CONTRACTS)
def test_supplied_examples_validate(schema_name: str, fixture_name: str) -> None:
    validator(schema_name).validate(fixture(fixture_name))


@pytest.mark.parametrize("field", ["event_id", "tenant_id", "conversation_id", "customer_id"])
@pytest.mark.parametrize("invalid", [None, "", 123])
def test_invalid_event_identifiers_rejected(field: str, invalid: object) -> None:
    event = fixture("event")
    event[field] = invalid
    with pytest.raises(ValidationError):
        validator("event").validate(event)


@pytest.mark.parametrize("field", ["tenant_id", "channel", "event", "occurred_at", "metadata"])
def test_incomplete_envelope_rejected(field: str) -> None:
    event = fixture("event")
    del event[field]
    with pytest.raises(ValidationError):
        validator("event").validate(event)


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-09-05T01:00:00", "2026-02-30T01:00:00Z"])
def test_invalid_or_unzoned_time_rejected(timestamp: str) -> None:
    event = fixture("event")
    event["occurred_at"] = timestamp
    with pytest.raises(ValidationError):
        validator("event").validate(event)


def test_vendor_fields_belong_in_metadata() -> None:
    event = fixture("event")
    event["chatwoot_conversation_id"] = 10
    with pytest.raises(ValidationError):
        validator("event").validate(event)
    del event["chatwoot_conversation_id"]
    event["metadata"]["external_reference"] = {"provider": "chatwoot", "id": "10"}
    event["channel"] = {"type": "custom-channel", "provider": "community-provider"}
    validator("event").validate(event)


@pytest.mark.parametrize("event_type", ["message_created", "handoff_resumed", "unknown"])
def test_unapproved_event_types_are_not_silently_added(event_type: str) -> None:
    event = fixture("event")
    event["event"]["type"] = event_type
    with pytest.raises(ValidationError):
        validator("event").validate(event)


def test_required_handoff_needs_reason() -> None:
    scenario = fixture("scenario")
    del scenario["expect"]["handoff"]["reason"]
    with pytest.raises(ValidationError):
        validator("scenario").validate(scenario)
    scenario["expect"]["handoff"]["required"] = False
    validator("scenario").validate(scenario)


def test_duplicate_required_tool_rejected() -> None:
    scenario = fixture("scenario")
    scenario["expect"]["tools"]["required"] = ["get_invoice", "get_invoice"]
    with pytest.raises(ValidationError):
        validator("scenario").validate(scenario)


def test_metric_omission_is_not_a_pass() -> None:
    result = fixture("evaluation-result")
    del result["metrics"]["policy_compliance"]
    with pytest.raises(ValidationError):
        validator("evaluation-result").validate(result)


def test_unscored_metrics_are_explicit() -> None:
    result = fixture("evaluation-result")
    assert all(metric["status"] == "not_evaluated" for metric in result["metrics"].values())
    result["metrics"]["grounding"]["status"] = "probably-good"
    with pytest.raises(ValidationError):
        validator("evaluation-result").validate(result)
