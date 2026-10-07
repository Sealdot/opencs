"""D1–D6 specification regression; no live provider or database claims."""

from copy import deepcopy
from itertools import product
from typing import Any

import pytest
from jsonschema import ValidationError

from scripts.check_foundation import ROOT, read_object, validate_contract
from scripts.check_handoff_trace import check_transition

STATES = ("requested", "accepted", "in_progress", "completed", "cancelled", "failed")
EDGES = {
    ("requested", "accepted"): "human_acceptance",
    ("requested", "cancelled"): "cancellation",
    ("requested", "failed"): "failure",
    ("accepted", "in_progress"): "human_service_action",
    ("in_progress", "completed"): "completion",
    ("in_progress", "failed"): "failure",
    ("failed", "requested"): "retry",
}


def snapshot(state: str = "requested", version: int = 1, attempt: int = 1) -> dict[str, Any]:
    value = read_object(ROOT / "tests/fixtures/handoff.json")
    value.update(state=state, version=version, attempt=attempt)
    return value


@pytest.mark.parametrize(("before", "after"), list(product(STATES, repeat=2)))
def test_all_state_edges(before: str, after: str) -> None:
    current = snapshot(after, 2, 2 if (before, after) == ("failed", "requested") else 1)
    if (before, after) in EDGES:
        check_transition(
            snapshot(before), current, trigger=EDGES[before, after], expected_version=1
        )
    else:
        with pytest.raises(ValueError):
            check_transition(snapshot(before), current, trigger="arbitrary", expected_version=1)


def test_initial_creation_and_single_active_handoff() -> None:
    check_transition(None, snapshot(), trigger="request", expected_version=0)
    with pytest.raises(ValueError, match="Another handoff"):
        check_transition(
            None, snapshot(), trigger="request", expected_version=0, active_handoff_id="other"
        )


def test_assignment_is_not_acceptance() -> None:
    with pytest.raises(ValueError, match="evidence trigger"):
        check_transition(
            snapshot(), snapshot("accepted", 2), trigger="provider_assignment", expected_version=1
        )


def test_retry_and_old_attempt_callback() -> None:
    failed = snapshot("failed", 2)
    retried = snapshot("requested", 3, 2)
    check_transition(failed, retried, trigger="retry", expected_version=2)
    with pytest.raises(ValueError, match="attempt"):
        check_transition(
            retried, snapshot("accepted", 4, 1), trigger="human_acceptance", expected_version=3
        )
    with pytest.raises(ValueError, match="Version conflict"):
        check_transition(
            retried, snapshot("accepted", 4, 2), trigger="human_acceptance", expected_version=1
        )


@pytest.mark.parametrize("field", ["id", "tenant_id", "conversation_id"])
def test_identity_cannot_change_during_handoff(field: str) -> None:
    current = snapshot("accepted", 2)
    current[field] = "unrelated"
    with pytest.raises(ValueError, match="identity changed"):
        check_transition(snapshot(), current, trigger="human_acceptance", expected_version=1)


@pytest.mark.parametrize("state", STATES)
def test_handoff_event_matches_snapshot_state(state: str) -> None:
    event = read_object(ROOT / "tests/fixtures/handoff-event.json")
    event["event"]["type"] = "handoff_" + state
    event["event"]["content"] = snapshot(state)
    validate_contract("event", event)
    event["event"]["content"]["state"] = "failed" if state != "failed" else "requested"
    with pytest.raises(ValidationError):
        validate_contract("event", event)


@pytest.mark.parametrize("field", ["tenant_id", "conversation_id", "customer_id"])
def test_event_snapshot_identity_mismatch(field: str) -> None:
    event = read_object(ROOT / "tests/fixtures/handoff-event.json")
    event[field] = "unrelated"
    with pytest.raises(ValueError, match="mismatch"):
        validate_contract("event", event)


@pytest.mark.parametrize("state", ["accepted", "in_progress", "completed"])
def test_human_state_needs_evidence(state: str) -> None:
    current = snapshot(state)
    current["evidence_refs"] = []
    with pytest.raises(ValidationError):
        validate_contract("handoff", current)


def test_scenario_cannot_require_forbidden_tool() -> None:
    scenario = read_object(ROOT / "tests/fixtures/scenario.json")
    scenario["expect"]["tools"]["required"].append("issue_refund")
    with pytest.raises(ValueError, match="forbidden tool"):
        validate_contract("scenario", scenario)


@pytest.mark.parametrize("status", ["pass", "fail"])
def test_evaluation_claims_need_evidence(status: str) -> None:
    result = read_object(ROOT / "tests/fixtures/evaluation-result.json")
    result["metrics"]["policy_compliance"]["status"] = status
    with pytest.raises(ValidationError):
        validate_contract("evaluation-result", result)
    result["metrics"]["policy_compliance"]["evidence_refs"] = ["fixture:tool-trace-1"]
    validate_contract("evaluation-result", result)


def test_billing_scenario_agrees_with_approved_example_policy() -> None:
    policy = read_object(ROOT / "examples/saas-billing/policy.json")
    expected = read_object(ROOT / "tests/fixtures/scenario.json")["expect"]
    assert set(expected["tools"]["required"]) == set(policy["allowed_tools"])
    assert expected["tools"]["forbidden"] == policy["forbidden_tools"] == ["issue_refund"]
    assert expected["handoff"]["reason"] == policy["handoff_reason"] == "refund_requires_approval"
    assert expected["resolution"] == policy["expected_resolution"] == "pending_human"


def test_reference_checks_do_not_mutate_history() -> None:
    previous = snapshot("failed", 2)
    history = deepcopy(previous)
    check_transition(previous, snapshot("requested", 3, 2), trigger="retry", expected_version=2)
    assert previous == history
