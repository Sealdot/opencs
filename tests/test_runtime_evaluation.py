"""D6 assertions consume scoped execution evidence, never scripted answer text."""

from copy import deepcopy
from typing import Any

import pytest

from opencs.evaluation import evaluate_scenario
from scripts.check_foundation import ROOT, read_object, validator


def scenario() -> dict[str, Any]:
    return {
        "scenario": "generic_support",
        "given": {},
        "user": "Please investigate",
        "expect": {
            "intent": "account_question",
            "tools": {"required": ["lookup_account"], "forbidden": ["close_account"]},
            "handoff": {"required": True, "reason": "requires_review"},
            "resolution": "pending_human",
        },
    }


def record(ref: str, kind: str, **data: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "tenant_id": "tenant_acme",
        "conversation_id": "conv_01",
        "kind": kind,
        "data": data,
    }


def evidence() -> list[dict[str, Any]]:
    snapshot = read_object(ROOT / "tests/fixtures/handoff.json")
    snapshot["reason"]["code"] = "requires_review"
    snapshot["evidence_refs"] = ["event", "success"]
    return [
        record("event", "event_received", event_id="event_1"),
        record(
            "decision", "agent_decision", decision={"intent": "account_question", "action": "tools"}
        ),
        record("attempt", "tool_attempt", name="lookup_account", action_id="action1"),
        record("success", "tool_succeeded", name="lookup_account", action_id="action1"),
        record("request", "handoff_requested", snapshot=snapshot),
        record("resolution", "resolution_recorded", resolution="pending_human"),
    ]


def evaluate(
    audit: list[dict[str, Any]], definition: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = evaluate_scenario(
        definition or scenario(),
        audit,
        "pending_human",
        tenant_id="tenant_acme",
        conversation_id="conv_01",
    )
    validator("evaluation-result").validate(result["evaluation"])
    refs = {
        r["ref"]
        for r in audit
        if (r["tenant_id"], r["conversation_id"]) == ("tenant_acme", "conv_01")
    }
    for item in list(result["evaluation"]["metrics"].values()) + result["evaluation"]["assertions"]:
        assert set(item["evidence_refs"]) <= refs
        if item["status"] in {"pass", "fail"}:
            assert item["evidence_refs"]
    return result


def statuses(result: dict[str, Any]) -> dict[str, str]:
    return {a["name"]: a["status"] for a in result["evaluation"]["assertions"]}


def test_valid_generic_flow_passes_without_claiming_resolution_or_model_quality() -> None:
    result = evaluate(evidence())
    assert result["passed"] is True
    assert result["evidence_kind"] == "offline_contract"
    assert set(statuses(result).values()) == {"pass"}
    for name in ("resolution", "correctness", "grounding"):
        assert result["evaluation"]["metrics"][name]["status"] == "not_evaluated"
    for name in ("policy_compliance", "tool_correctness", "handoff_quality"):
        assert result["evaluation"]["metrics"][name]["status"] == "pass"


def test_no_execution_has_no_passing_assertions_or_metrics() -> None:
    result = evaluate([])
    assert result["passed"] is False
    assert set(statuses(result).values()) == {"not_evaluated"}
    assert all(m["status"] == "not_evaluated" for m in result["evaluation"]["metrics"].values())


@pytest.mark.parametrize("kind", ["tool_failed", "tool_denied", "tool_attempt"])
def test_required_tool_needs_success(kind: str) -> None:
    audit = evidence()
    audit[3]["kind"] = kind
    result = evaluate(audit)
    assert result["passed"] is False
    assert statuses(result)["required_tool:lookup_account"] == "fail"


def test_success_without_matching_attempt_or_after_failed_attempt_cannot_pass() -> None:
    for mode in ("missing", "wrong_action", "failure", "reversed"):
        audit = evidence()
        if mode == "missing":
            del audit[2]
        elif mode == "wrong_action":
            audit[2]["data"]["action_id"] = "other"
        elif mode == "failure":
            audit.insert(
                3, record("failed", "tool_failed", name="lookup_account", action_id="action1")
            )
        else:
            audit[2], audit[3] = audit[3], audit[2]
        assert statuses(evaluate(audit))["required_tool:lookup_account"] == "fail"


def test_denied_forbidden_attempt_fails_agent_policy_evaluation() -> None:
    audit = evidence()
    audit.extend(
        [
            record("forbidden", "tool_attempt", name="close_account"),
            record("denied", "tool_denied", name="close_account"),
        ]
    )
    result = evaluate(audit)
    assert result["passed"] is False
    assert statuses(result)["forbidden_tool:close_account"] == "fail"
    assert result["evaluation"]["metrics"]["policy_compliance"]["status"] == "fail"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_reason",
        "bad_structure",
        "foreign_scope",
        "dangling",
        "future",
        "foreign_ref",
        "wrong_state",
    ],
)
def test_handoff_needs_valid_request_and_scoped_prior_references(mutation: str) -> None:
    audit = evidence()
    snapshot = audit[4]["data"]["snapshot"]
    if mutation == "wrong_reason":
        snapshot["reason"]["code"] = "another_reason"
    elif mutation == "bad_structure":
        del snapshot["summary"]
    elif mutation == "foreign_scope":
        snapshot["tenant_id"] = "other"
    elif mutation == "dangling":
        snapshot["evidence_refs"] = ["invented"]
    elif mutation == "future":
        snapshot["evidence_refs"] = ["resolution"]
    elif mutation == "foreign_ref":
        foreign = record("foreign", "event_received")
        foreign["tenant_id"] = "other"
        audit.insert(0, foreign)
        snapshot["evidence_refs"] = ["foreign"]
    else:
        snapshot["state"] = "accepted"
    result = evaluate(audit)
    assert result["passed"] is False
    assert statuses(result)["required_handoff"] == "fail"


@pytest.mark.parametrize("foreign_field", ["tenant_id", "conversation_id"])
def test_foreign_success_does_not_satisfy_required_tool(foreign_field: str) -> None:
    audit = evidence()
    audit[3][foreign_field] = "other"
    assert statuses(evaluate(audit))["required_tool:lookup_account"] == "fail"


def test_duplicate_audit_refs_are_rejected_even_across_tenants() -> None:
    audit = evidence()
    foreign = deepcopy(audit[0])
    foreign["tenant_id"] = "other"
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(audit + [foreign])


def test_resolution_requires_record_and_matches_latest_business_result() -> None:
    audit = evidence()
    assert statuses(evaluate(audit[:-1]))["resolution_matches"] == "not_evaluated"
    audit[-1]["data"]["resolution"] = "resolved"
    assert statuses(evaluate(audit))["resolution_matches"] == "fail"
    definition = scenario()
    definition["expect"]["resolution"] = "resolved"
    result = evaluate_scenario(
        definition, audit, "resolved", tenant_id="tenant_acme", conversation_id="conv_01"
    )
    validator("evaluation-result").validate(result["evaluation"])
    assert result["passed"]
    assert result["evaluation"]["metrics"]["resolution"]["status"] == "pass"


def test_missing_start_and_wrong_intent_do_not_pass() -> None:
    assert not evaluate(evidence()[1:])["passed"]
    audit = evidence()
    audit[1]["data"]["decision"]["intent"] = "unrelated"
    assert statuses(evaluate(audit))["intent_matches"] == "fail"


def test_one_correct_intent_does_not_hide_conflicting_decision() -> None:
    audit = evidence()
    audit.append(record("contradiction", "agent_decision", decision={"intent": "unrelated"}))
    assert statuses(evaluate(audit))["intent_matches"] == "fail"


@pytest.mark.parametrize(
    "mutation",
    [
        "overlap",
        "duplicates",
        "empty",
        "extra",
        "bad_boolean",
        "missing_reason",
        "bad_given",
        "bad_tool",
    ],
)
def test_invalid_scenario_rejected(mutation: str) -> None:
    definition = scenario()
    expect = definition["expect"]
    if mutation == "overlap":
        expect["tools"]["required"] = ["close_account"]
    elif mutation == "duplicates":
        expect["tools"]["required"] *= 2
    elif mutation == "empty":
        definition["scenario"] = ""
    elif mutation == "extra":
        expect["invented"] = True
    elif mutation == "bad_boolean":
        expect["handoff"]["required"] = 1
    elif mutation == "missing_reason":
        del expect["handoff"]["reason"]
    elif mutation == "bad_given":
        definition["given"] = []
    else:
        expect["tools"]["required"] = [{}]
    with pytest.raises(ValueError):
        evaluate(evidence(), definition)


def test_no_handoff_required_does_not_invent_human_acceptance() -> None:
    definition = scenario()
    definition["expect"]["handoff"] = {"required": False}
    audit = [r for r in evidence() if r["kind"] != "handoff_requested"]
    result = evaluate(audit, definition)
    assert result["passed"]
    assert result["evaluation"]["metrics"]["handoff_quality"]["status"] == "not_evaluated"
