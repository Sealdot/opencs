"""Deterministic D6 evaluation of trusted, ordered offline runtime audit records.

The caller supplies audit records from its authorized RuntimeStore. Reference
validation prevents mixing scopes or dangling evidence; it does not authenticate
an arbitrary caller's invented audit log or measure live/model quality.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from opencs.domain import validate_handoff
from opencs.domain.validation import exact_object, json_value, nonempty

METRICS = (
    "resolution",
    "correctness",
    "grounding",
    "policy_compliance",
    "tool_correctness",
    "handoff_quality",
)


def _validate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    exact_object(scenario, {"scenario", "given", "user", "expect"}, "scenario")
    json_value(scenario)
    nonempty(scenario["scenario"], "scenario")
    nonempty(scenario["user"], "user")
    if not isinstance(scenario["given"], dict):
        raise ValueError("given must be an object")
    expect = exact_object(
        scenario["expect"], {"intent", "tools", "handoff", "resolution"}, "expect"
    )
    for field in ("intent", "resolution"):
        nonempty(expect[field], field)
    tools = exact_object(expect["tools"], {"required", "forbidden"}, "tools")
    for field in ("required", "forbidden"):
        if not isinstance(tools[field], list):
            raise ValueError(f"tools.{field} must be an array")
        for name in tools[field]:
            nonempty(name, f"tools.{field}")
        if len(set(tools[field])) != len(tools[field]):
            raise ValueError("Tool names must be unique")
    if set(tools["required"]) & set(tools["forbidden"]):
        raise ValueError("Scenario requires a forbidden tool")
    handoff = expect["handoff"]
    if not isinstance(handoff, dict) or set(handoff) not in ({"required"}, {"required", "reason"}):
        raise ValueError("Invalid handoff expectation")
    if type(handoff["required"]) is not bool:
        raise ValueError("handoff.required must be boolean")
    if handoff["required"] or "reason" in handoff:
        nonempty(handoff.get("reason"), "handoff.reason")
    return expect


def evaluate_scenario(
    scenario: dict[str, Any],
    audit: list[dict[str, Any]],
    resolution: str | None,
    *,
    tenant_id: str,
    conversation_id: str,
    run_id: str = "offline",
) -> dict[str, Any]:
    """Evaluate one tenant/conversation; missing execution evidence never passes.

    Tool successes require an earlier unconsumed attempt of the same name (and
    action_id when supplied). A completed resolution requires resolution_recorded
    audit evidence. All pass/fail references resolve within the selected scope.
    """
    expect = _validate_scenario(scenario)
    for name, value in (
        ("tenant_id", tenant_id),
        ("conversation_id", conversation_id),
        ("run_id", run_id),
    ):
        nonempty(value, name)
    if resolution is not None:
        nonempty(resolution, "resolution")
    if not isinstance(audit, list):
        raise ValueError("audit must be an ordered array")
    json_value(audit)
    seen: set[str] = set()
    scoped: list[dict[str, Any]] = []
    for record in audit:
        if not isinstance(record, dict):
            raise ValueError("Invalid audit record")
        for field in ("ref", "tenant_id", "conversation_id", "kind"):
            nonempty(record.get(field), field)
        if not isinstance(record.get("data"), dict):
            raise ValueError("audit.data must be an object")
        if record["ref"] in seen:
            raise ValueError("Ambiguous duplicate audit reference")
        seen.add(record["ref"])
        if (record["tenant_id"], record["conversation_id"]) == (tenant_id, conversation_id):
            scoped.append(record)
    starts = [r["ref"] for r in scoped if r["kind"] == "event_received"]
    boundary = starts[:1]
    assertions: list[dict[str, Any]] = []

    def assertion(name: str, passed: bool, refs: list[str]) -> dict[str, Any]:
        evidence = list(dict.fromkeys(refs or boundary))
        status = ("pass" if passed else "fail") if boundary and evidence else "not_evaluated"
        result = {"name": name, "status": status, "evidence_refs": evidence}
        assertions.append(result)
        return result

    decisions = [r for r in scoped if r["kind"] == "agent_decision"]
    assertion(
        "intent_matches",
        bool(decisions)
        and all(
            isinstance(r["data"].get("decision"), dict)
            and r["data"]["decision"].get("intent") == expect["intent"]
            for r in decisions
        ),
        [r["ref"] for r in decisions],
    )

    attempts: dict[str, list[dict[str, Any]]] = {}
    successes: dict[str, list[str]] = {}
    tool_records: list[dict[str, Any]] = []
    for record in scoped:
        if record["kind"] not in {"tool_attempt", "tool_succeeded", "tool_failed", "tool_denied"}:
            continue
        tool_records.append(record)
        name = record["data"].get("name")
        nonempty(name, "tool.name")
        pending = attempts.setdefault(name, [])
        if record["kind"] == "tool_attempt":
            pending.append(record)
            continue
        action_id = record["data"].get("action_id")
        matching = next((x for x in pending if x["data"].get("action_id") == action_id), None)
        if matching is not None:
            pending.remove(matching)
            if record["kind"] == "tool_succeeded":
                successes.setdefault(name, []).extend([matching["ref"], record["ref"]])
    required_assertions = [
        assertion(
            f"required_tool:{name}",
            bool(successes.get(name)),
            successes.get(name, [r["ref"] for r in tool_records if r["data"]["name"] == name]),
        )
        for name in expect["tools"]["required"]
    ]
    forbidden_assertions = []
    for name in expect["tools"]["forbidden"]:
        violations = [r["ref"] for r in tool_records if r["data"]["name"] == name]
        forbidden_assertions.append(assertion(f"forbidden_tool:{name}", not violations, violations))

    requests = [r for r in scoped if r["kind"] == "handoff_requested"]
    authorized_refs: set[str] = set()
    valid_requests: list[str] = []
    for record in scoped:
        if record["kind"] == "handoff_requested":
            snapshot = record["data"].get("snapshot")
            try:
                validate_handoff(snapshot)
                valid = (
                    snapshot["state"] == "requested"
                    and snapshot["tenant_id"] == tenant_id
                    and snapshot["conversation_id"] == conversation_id
                    and snapshot["reason"]["code"] == expect["handoff"].get("reason")
                    and all(ref in authorized_refs for ref in snapshot["evidence_refs"])
                )
            except (ValueError, TypeError, KeyError):
                valid = False
            if valid:
                valid_requests.append(record["ref"])
        authorized_refs.add(record["ref"])
    handoff_assertion = assertion(
        "required_handoff",
        not expect["handoff"]["required"] or bool(valid_requests),
        valid_requests or [r["ref"] for r in requests],
    )
    resolutions = [r for r in scoped if r["kind"] == "resolution_recorded"]
    actual = resolutions[-1]["data"].get("resolution") if resolutions else None
    resolution_assertion = assertion(
        "resolution_matches",
        resolution == actual == expect["resolution"],
        [resolutions[-1]["ref"]] if resolutions else [],
    )
    if not resolutions:
        resolution_assertion["status"] = "not_evaluated"

    metrics: dict[str, Any] = {
        name: {
            "status": "not_evaluated",
            "evidence_refs": [],
            "explanation": "No reliable evidence or scoring criterion for this metric.",
        }
        for name in METRICS
    }

    def metric(name: str, checks: list[dict[str, Any]], explanation: str) -> None:
        if not checks or not boundary:
            return
        refs = list(dict.fromkeys(ref for check in checks for ref in check["evidence_refs"]))
        status = (
            "fail"
            if any(c["status"] == "fail" for c in checks)
            else ("pass" if all(c["status"] == "pass" for c in checks) else "not_evaluated")
        )
        metrics[name] = {"status": status, "evidence_refs": refs, "explanation": explanation}

    metric(
        "policy_compliance",
        forbidden_assertions,
        "Only the scenario's forbidden tool attempts were assessed, including denied attempts.",
    )
    metric(
        "tool_correctness",
        required_assertions,
        "Only required tools with paired successful execution evidence were assessed.",
    )
    if expect["handoff"]["required"]:
        metric(
            "handoff_quality",
            [handoff_assertion],
            "Validated request structure, scope, evidence and reason; "
            "human acceptance was not tested.",
        )
    if actual == "resolved" and resolution == actual:
        metric(
            "resolution", [resolution_assertion], "Explicit resolved business result was recorded."
        )
    elif actual == "pending_human":
        metrics["resolution"]["explanation"] = (
            "The expected human handoff is pending, not resolved."
        )
    evaluation = {
        "scenario": scenario["scenario"],
        "run_id": run_id,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "assertions": assertions,
    }
    return {
        "passed": all(a["status"] == "pass" for a in assertions),
        "evaluation": evaluation,
        "evidence_kind": "offline_contract",
    }
