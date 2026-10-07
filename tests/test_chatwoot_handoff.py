"""Offline AgentBot transport contracts; these do not prove human acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any, Literal

import pytest

from adapters import AdapterError, ChatwootAdapter, FakeTransport, HttpResponse
from opencs.application.memory_store import MemoryRuntimeStore
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Tenant
from opencs.persistence.memory import MemoryRepository


def success_responses() -> list[HttpResponse | TimeoutError]:
    return [
        HttpResponse(
            200,
            {
                "id": 501,
                "private": True,
                "conversation_id": 42,
                "inbox_id": 2,
                "message_type": 1,
                "content_type": "text",
            },
        ),
        HttpResponse(200, {"id": 9}),
        HttpResponse(
            200, {"payload": {"conversation_id": 42, "current_status": "open", "success": None}}
        ),
    ]


class Setup:
    def __init__(
        self,
        responses: list[HttpResponse | TimeoutError] | None = None,
        *,
        mode: Literal["assignment", "agent_bot"] = "agent_bot",
    ) -> None:
        self.repository = MemoryRepository()
        self.repository.add_tenant(Tenant("tenant_a"))
        self.transport = FakeTransport(success_responses() if responses is None else responses)
        self.adapter = ChatwootAdapter(
            base_url="https://chatwoot.invalid",
            account_id=1,
            inbox_id=2,
            tenant_id="tenant_a",
            token="synthetic-token",
            transport=self.transport,
            repository=self.repository,
            webhook_secret="synthetic-secret",
            team_id=9,
            handoff_mode=mode,
        )
        self.payload: dict[str, Any] = json.loads(
            Path("opencs/examples/data/chatwoot-message.json").read_text()
        )
        event = self.adapter.normalize(self.payload, credential="synthetic-secret")
        assert event is not None
        self.event = event
        self.snapshot: dict[str, Any] = {
            "tenant_id": event.tenant_id,
            "conversation_id": event.conversation_id,
            "facts": [{"description": "Duplicate charge 待核实"}],
        }

    def handoff(self, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.adapter.handoff(
            tenant_id=self.event.tenant_id,
            conversation_id=self.event.conversation_id,
            snapshot=self.snapshot if snapshot is None else snapshot,
            action_id="handoff_1",
        )


@pytest.mark.parametrize("success", [None, True])
def test_agent_bot_sequence_and_audit_before_every_request(success: bool | None) -> None:
    responses = success_responses()
    assert isinstance(responses[2], HttpResponse)
    responses[2].body["payload"]["success"] = success
    setup = Setup(responses)
    records: list[dict[str, Any]] = []

    def audit(record: dict[str, Any]) -> None:
        assert len(setup.transport.requests) == len(records)
        records.append(record)

    setup.adapter.before_request = audit
    result = setup.handoff()
    assert result["delivery"] == "sent"
    assert [r["action_id"] for r in records] == [
        "handoff_1:context",
        "handoff_1:assignment",
        "handoff_1:open",
    ]
    requests = setup.transport.requests
    assert [r.url.rsplit("/", 1)[-1] for r in requests] == [
        "messages",
        "assignments",
        "toggle_status",
    ]
    assert all(
        r.url.startswith("https://chatwoot.invalid/api/v1/accounts/1/conversations/42/")
        and r.method == "POST"
        for r in requests
    )
    assert requests[0].json_body == {
        "content": json.dumps(setup.snapshot, ensure_ascii=False, sort_keys=True),
        "message_type": "outgoing",
        "private": True,
        "content_type": "text",
    }
    assert requests[1].json_body == {"team_id": 9}
    assert requests[2].json_body == {"status": "open"}
    assert all(a["delivery"] == "sent" for a in result["actions"])
    assert all("body" not in a for a in result["actions"])
    assert "accepted" not in json.dumps(result)
    assert setup.handoff() == result
    assert len(requests) == len(records) == 3


@pytest.mark.parametrize("step", [0, 1, 2])
@pytest.mark.parametrize(
    "response,delivery",
    [
        (HttpResponse(401, {"error": "private provider detail"}), "failed"),
        (HttpResponse(403, {}), "failed"),
        (HttpResponse(429, {}), "failed"),
        (HttpResponse(500, {}), "failed"),
        (TimeoutError(), "unknown"),
    ],
)
def test_failure_stops_at_checkpoint_and_replay_never_retries(
    step: int, response: HttpResponse | TimeoutError, delivery: str
) -> None:
    responses = success_responses()
    responses[step] = response
    setup = Setup(responses)
    result = setup.handoff()
    assert result["delivery"] == delivery
    assert [a["delivery"] for a in result["actions"]] == ["sent"] * step + [delivery]
    assert setup.handoff() == result
    assert len(setup.transport.requests) == step + 1
    assert "private provider detail" not in json.dumps(result)


@pytest.mark.parametrize(
    "step,field,value",
    [
        (0, "id", 0),
        (0, "id", True),
        (0, "private", False),
        (0, "private", 1),
        (0, "conversation_id", 43),
        (0, "inbox_id", 3),
        (0, "message_type", "outgoing"),
        (0, "message_type", True),
        (0, "content_type", "image"),
        (1, "id", 10),
        (1, "id", "9"),
        (1, "id", None),
        (2, "conversation_id", 43),
        (2, "current_status", "pending"),
        (2, "current_status", None),
        (2, "success", False),
        (2, "success", 1),
    ],
)
def test_malformed_or_wrong_target_success_is_unknown(step: int, field: str, value: Any) -> None:
    responses = success_responses()
    response = responses[step]
    assert isinstance(response, HttpResponse)
    body = response.body["payload"] if step == 2 else response.body
    body[field] = value
    setup = Setup(responses)
    result = setup.handoff()
    assert result["delivery"] == "unknown"
    assert [a["delivery"] for a in result["actions"]] == ["sent"] * step + ["unknown"]
    assert setup.handoff() == result
    assert len(setup.transport.requests) == step + 1


@pytest.mark.parametrize("step", [0, 1, 2])
def test_missing_response_fields_stop_later_steps(step: int) -> None:
    responses = success_responses()
    responses[step] = HttpResponse(200, {})
    setup = Setup(responses)
    assert setup.handoff()["delivery"] == "unknown"
    assert len(setup.transport.requests) == step + 1


@pytest.mark.parametrize(
    "payload", [None, [], "open", {"conversation_id": 42, "current_status": "open"}]
)
def test_open_requires_payload_and_explicit_success_field(payload: Any) -> None:
    setup = Setup(success_responses()[:2] + [HttpResponse(200, {"payload": payload})])
    assert setup.handoff()["delivery"] == "unknown"
    assert len(setup.transport.requests) == 3


@pytest.mark.parametrize("step", [0, 1, 2])
def test_unexpected_transport_exception_retains_completed_steps(step: int) -> None:
    setup = Setup(success_responses()[:step])
    # Exhaustion raises RuntimeError instead of a transport TimeoutError.
    result = setup.handoff()
    assert result["delivery"] == "unknown"
    assert result["actions"][-1]["error"] == "request_error"
    assert [a["delivery"] for a in result["actions"]] == ["sent"] * step + ["unknown"]
    assert setup.handoff() == result
    assert len(setup.transport.requests) == step + 1


@pytest.mark.parametrize("step", [0, 1, 2])
def test_audit_hook_failure_sends_no_unaudited_request(step: int) -> None:
    setup = Setup()
    audited: list[str] = []

    def audit(record: dict[str, Any]) -> None:
        audited.append(record["operation"])
        if len(audited) == step + 1:
            raise RuntimeError("audit unavailable")

    setup.adapter.before_request = audit
    result = setup.handoff()
    assert result["delivery"] == "unknown"
    assert [a["delivery"] for a in result["actions"]] == ["sent"] * step + ["unknown"]
    assert setup.handoff() == result
    assert len(setup.transport.requests) == step
    assert len(audited) == step + 1


def test_reentrant_audit_callback_observes_claim_and_cannot_repeat_network() -> None:
    setup = Setup()
    observed: list[dict[str, Any]] = []

    def audit(record: dict[str, Any]) -> None:
        replay = setup.handoff()
        observed.append(deepcopy(replay))
        assert replay["delivery"] == "unknown"
        assert replay["actions"][-1] == {
            "action_id": record["action_id"],
            "delivery": "unknown",
            "error": "request_in_progress",
        }
        replay["actions"].clear()

    setup.adapter.before_request = audit
    assert setup.handoff()["delivery"] == "sent"
    assert [len(r["actions"]) for r in observed] == [1, 2, 3]
    assert len(setup.transport.requests) == 3


def test_concurrent_duplicate_calls_share_one_delivery() -> None:
    setup = Setup()
    entered, release, duplicate_started = ThreadEvent(), ThreadEvent(), ThreadEvent()

    def audit(record: dict[str, Any]) -> None:
        if record["operation"] == "messages":
            entered.set()
            assert release.wait(5)

    def duplicate() -> dict[str, Any]:
        duplicate_started.set()
        return setup.handoff()

    setup.adapter.before_request = audit
    with ThreadPoolExecutor(max_workers=2) as executor:
        original = executor.submit(setup.handoff)
        try:
            assert entered.wait(5)
            replay = executor.submit(duplicate)
            assert duplicate_started.wait(5)
        finally:
            release.set()
        assert original.result(timeout=5) == replay.result(timeout=5)
    assert len(setup.transport.requests) == 3


def test_snapshot_canonicalization_and_result_copy_isolation() -> None:
    setup = Setup()
    original_snapshot = deepcopy(setup.snapshot)
    result = setup.handoff()
    expected = deepcopy(result)
    result["delivery"] = "failed"
    result["actions"][0]["delivery"] = "failed"
    result["actions"].append({"delivery": "failed"})
    reordered = dict(reversed(list(original_snapshot.items())))
    replay = setup.handoff(snapshot=reordered)
    assert replay == expected
    replay["actions"].clear()
    setup.snapshot["facts"][0]["description"] = "mutated"
    with pytest.raises(AdapterError, match="reused"):
        setup.handoff()
    assert setup.handoff(snapshot=original_snapshot) == expected
    assert len(setup.transport.requests) == 3


@pytest.mark.parametrize("change", ["team", "mode", "target"])
def test_action_id_conflict_rejects_changed_delivery_binding(change: str) -> None:
    setup = Setup()
    setup.handoff()
    if change == "team":
        setup.adapter.team_id = 10
    elif change == "mode":
        setup.adapter.handoff_mode = "assignment"
    else:
        setup.adapter.base_url = "https://another-chatwoot.invalid"
    with pytest.raises(AdapterError, match="reused"):
        setup.handoff()
    assert len(setup.transport.requests) == 3


def test_legacy_assignment_mode_preserves_two_step_response_contract() -> None:
    setup = Setup([HttpResponse(200, {"id": 501}), HttpResponse(200, {})], mode="assignment")
    result = setup.handoff()
    assert result["delivery"] == "sent"
    assert [a["action_id"] for a in result["actions"]] == [
        "handoff_1:context",
        "handoff_1:assignment",
    ]
    assert setup.handoff() == result
    assert len(setup.transport.requests) == 2


class HandoffAgent:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {
            "intent": "billing_dispute",
            "action": {
                "type": "handoff",
                "reason": "refund_requires_approval",
                "summary": "Human refund review requested",
            },
        }


class NoTools:
    def execute(
        self, name: str, arguments: dict[str, Any], *, tenant_id: str, action_id: str
    ) -> dict[str, Any]:
        raise AssertionError("Unexpected tool")


@pytest.mark.parametrize("outcome", ["sent", "failed", "unknown"])
def test_runtime_never_confuses_agent_bot_delivery_with_human_acceptance(outcome: str) -> None:
    responses = success_responses()
    if outcome != "sent":
        responses[2] = HttpResponse(500, {}) if outcome == "failed" else TimeoutError()
    setup = Setup(responses)
    store, agent = MemoryRuntimeStore(), HandoffAgent()
    runtime = Runtime(
        repository=setup.repository,
        store=store,
        agent=agent,
        channel=setup.adapter,
        tools=NoTools(),
        policy=ToolPolicy(frozenset(), frozenset(), frozenset({"tenant_a"})),
        handoff_target={"type": "human_queue", "id": "billing"},
        handoff_reason="refund_requires_approval",
        pending_resolution="pending_human",
    )

    def audit(record: dict[str, Any]) -> None:
        assert store.get_handoff("tenant_a", setup.event.conversation_id) is not None
        assert setup.repository.get_conversation("tenant_a", setup.event.conversation_id).ai_paused
        store.append_audit("tenant_a", setup.event.conversation_id, "channel_http_prepared", record)

    setup.adapter.before_request = audit
    result = runtime.ingest(setup.event, trusted_tenant="tenant_a")
    assert result["status"] == result["resolution"] == "pending_human"
    assert result["handoff"]["state"] == "requested"
    assert runtime.ingest(setup.event, trusted_tenant="tenant_a") == result
    setup.payload["id"] += 1
    setup.payload["content"] = "Is anyone available?"
    followup = setup.adapter.normalize(setup.payload, credential="synthetic-secret")
    assert followup is not None
    assert runtime.ingest(followup, trusted_tenant="tenant_a")["status"] == "paused"
    conversation = setup.repository.get_conversation("tenant_a", setup.event.conversation_id)
    assert conversation.ai_paused
    assert conversation.resolution == "pending_human"
    assert store.get_handoff("tenant_a", conversation.id) == result["handoff"]
    audit_log = store.list_audit("tenant_a", conversation.id)
    delivery = [r for r in audit_log if r["kind"] == "handoff_delivery_result"]
    assert len(delivery) == 1
    assert delivery[0]["data"]["result"]["delivery"] == outcome
    assert not any(r["kind"] == "handoff_accepted" for r in audit_log)
    assert len(setup.transport.requests) == 3
    assert agent.calls == 1
