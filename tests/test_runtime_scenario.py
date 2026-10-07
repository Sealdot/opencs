"""Formal package end-to-end contracts over injected HTTP, with zero live I/O."""

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from opencs.examples.billing import FAULTS, resource, run_billing
from scripts.check_foundation import validate_contract


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline scenario must not open network sockets")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_formal_billing_context_policy_handoff_replay() -> None:
    report = run_billing()
    assert report["passed"] and report["replay_unchanged"]
    assert report["result"]["status"] == "pending_human"
    assert report["result"]["resolution"] == "pending_human"
    handoff = report["result"]["handoff"]
    assert handoff["state"] == "requested"
    assert handoff["reason"]["code"] == "refund_requires_approval"
    assert handoff["actions_completed"] == ["get_customer", "get_invoice"]
    assert len(handoff["facts"]) == len(handoff["evidence_refs"]) == 2
    validate_contract("handoff", handoff)
    validate_contract("evaluation-result", report["evaluation"])
    requests = report["channel_requests"]
    assert requests[0]["body"]["private"] is False
    assert requests[1]["body"]["private"] is True
    assert json.loads(requests[1]["body"]["content"]) == handoff
    assert requests[2]["body"] == {"team_id": 9}
    assert not any(row["kind"] == "handoff_accepted" for row in report["audit"])
    assert report["synthetic_http_requests"] == {"model": 4, "tools": 2, "channel": 3}
    assert report["provider_calls"] == 0 and report["model_inference"] is False
    metrics = report["evaluation"]["metrics"]
    for name in ("resolution", "correctness", "grounding"):
        assert metrics[name]["status"] == "not_evaluated"
    for name in ("policy_compliance", "tool_correctness", "handoff_quality"):
        assert metrics[name]["status"] == "pass"


@pytest.mark.parametrize("fault", FAULTS[1:])
def test_formal_billing_faults_are_detected_without_retry(fault: str) -> None:
    report = run_billing(fault=fault)
    assert not report["passed"] and report["replay_unchanged"]
    validate_contract("evaluation-result", report["evaluation"])
    assertions = {row["name"]: row["status"] for row in report["evaluation"]["assertions"]}
    assert report["synthetic_http_requests"]["tools"] == 2
    if fault == "forbidden-tool":
        assert assertions["forbidden_tool:issue_refund"] == "fail"
        assert any(row["kind"] == "tool_denied" for row in report["audit"])
    elif fault == "tool-failure":
        assert assertions["required_tool:get_invoice"] == "fail"
        assert report["result"]["handoff"]["actions_completed"] == ["get_customer"]
    else:
        assert report["result"]["status"] == "unknown"
        assert report["result"]["handoff"] is None
        assert report["synthetic_http_requests"]["channel"] == 1


def test_formal_billing_each_channel_subaction_has_prior_audit() -> None:
    report = run_billing()
    audit = report["audit"]
    prepared = [row for row in audit if row["kind"] == "channel_http_prepared"]
    assert len(prepared) == 3
    assert len({row["data"]["action_id"] for row in prepared}) == 3
    kinds = [row["kind"] for row in audit]
    assert (
        kinds.index("output_prepared")
        < kinds.index("channel_http_prepared")
        < kinds.index("output_result")
    )
    delivery = kinds.index("handoff_delivery_result")
    for row in prepared[1:]:
        assert kinds.index("handoff_requested") < audit.index(row) < delivery
    assert "synthetic-unused-key" not in json.dumps(report)
    assert "synthetic-unused-token" not in json.dumps(report)


def test_packaged_example_config_matches_approved_reference() -> None:
    assert resource("policy.json") == json.loads(
        Path("examples/saas-billing/policy.json").read_text()
    )
    assert resource("scenario.json") == json.loads(Path("tests/fixtures/scenario.json").read_text())


def test_unknown_fault_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown fault"):
        run_billing(fault="invented")
