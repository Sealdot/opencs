"""Independent offline use-case checks over explicit domain/application ports."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event as ThreadEvent
from typing import Any

import pytest

from opencs.application.memory_store import MemoryRuntimeStore, ReplayConflict
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Conversation, Customer, Event, Tenant
from opencs.persistence.memory import MemoryRepository


def action(kind: str, **fields: Any) -> dict[str, Any]:
    return {"intent": "billing_dispute", "action": {"type": kind, **fields}}


@dataclass
class Agent:
    decisions: list[dict[str, Any]]
    contexts: list[dict[str, Any]] = field(default_factory=list)
    before: Callable[[], None] | None = None
    error: Exception | None = None

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(deepcopy(context))
        if self.before:
            self.before()
        if self.error:
            raise self.error
        return deepcopy(self.decisions.pop(0)) if self.decisions else action("done")


@dataclass
class Tools:
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    before: Callable[[], None] | None = None

    def execute(
        self, name: str, arguments: dict[str, Any], *, tenant_id: str, action_id: str
    ) -> dict[str, Any]:
        if self.before:
            self.before()
        self.calls.append(
            {"name": name, "arguments": arguments, "tenant_id": tenant_id, "action_id": action_id}
        )
        if self.error:
            raise self.error
        return {"invoice_id": "INV-3021", "duplicate": True}


@dataclass
class Channel:
    replies: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    delivery: str = "sent"
    response: dict[str, Any] | None = None
    before: Callable[[str], None] | None = None

    def send(
        self, *, tenant_id: str, conversation_id: str, text: str, action_id: str
    ) -> dict[str, Any]:
        if self.before:
            self.before("output_prepared")
        self.replies.append(
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "text": text,
                "action_id": action_id,
            }
        )
        if self.error:
            raise self.error
        return deepcopy(self.response) if self.response is not None else {"delivery": self.delivery}

    def handoff(
        self, *, tenant_id: str, conversation_id: str, snapshot: dict[str, Any], action_id: str
    ) -> dict[str, Any]:
        if self.before:
            self.before("handoff_delivery_prepared")
        self.handoffs.append(
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "snapshot": snapshot,
                "action_id": action_id,
            }
        )
        if self.error:
            raise self.error
        return deepcopy(self.response) if self.response is not None else {"delivery": self.delivery}


@dataclass
class Harness:
    runtime: Runtime
    repository: MemoryRepository
    store: MemoryRuntimeStore
    agent: Agent
    tools: Tools
    channel: Channel

    def kinds(self) -> list[str]:
        return [item["kind"] for item in self.store.list_audit("t1", "c1")]


def harness(*decisions: dict[str, Any], max_steps: int = 8) -> Harness:
    repo = MemoryRepository()
    for tenant in ("t1", "t2"):
        repo.add_tenant(Tenant(tenant))
        repo.add_customer(Customer(tenant, "u1"))
        repo.add_conversation(Conversation(tenant, "c1", "u1"))
    repo.add_customer(Customer("t1", "u2"))
    store, agent, tools, channel = MemoryRuntimeStore(), Agent(list(decisions)), Tools(), Channel()
    runtime = Runtime(
        repository=repo,
        store=store,
        agent=agent,
        tools=tools,
        channel=channel,
        policy=ToolPolicy(
            frozenset({"get_invoice", "get_customer", "issue_refund"}),
            frozenset({"issue_refund"}),
            frozenset({"t1"}),
        ),
        handoff_target={"type": "human_queue", "id": "billing"},
        handoff_reason="refund_requires_approval",
        pending_resolution="pending_human",
        max_steps=max_steps,
    )
    return Harness(runtime, repo, store, agent, tools, channel)


def message(
    id: str = "evt1", *, tenant: str = "t1", customer: str = "u1", text: str = "Duplicate charge"
) -> Event:
    return Event.from_dict(
        {
            "event_id": id,
            "tenant_id": tenant,
            "conversation_id": "c1",
            "customer_id": customer,
            "channel": {"type": "im", "provider": "synthetic"},
            "event": {"type": "customer_message", "content_type": "text", "content": text},
            "occurred_at": "2026-09-07T10:00:00Z",
            "metadata": {},
        }
    )


def request_snapshot() -> dict[str, Any]:
    return {
        "id": "ho1",
        "tenant_id": "t1",
        "conversation_id": "c1",
        "state": "requested",
        "attempt": 1,
        "version": 1,
        "from": {"type": "ai_agent", "id": "support"},
        "to": {"type": "human_queue", "id": "billing"},
        "reason": {"code": "refund_requires_approval", "description": "Refund approval required"},
        "customer": {"id": "u1", "organization_id": None},
        "intent": {"primary": "billing_dispute", "confidence": None},
        "priority": {"level": "normal", "reason": "configured"},
        "summary": "Customer reports duplicate charge",
        "facts": [],
        "actions_completed": [],
        "actions_pending": ["refund_requires_approval"],
        "recommended_action": {"tool": "human_review", "parameters": {}},
        "evidence_refs": ["synthetic:explicit-request"],
    }


def callback(
    snapshot: dict[str, Any], *, trigger: str, expected_version: int, id: str | None = None
) -> Event:
    payload = message(id or f"callback-{snapshot['version']}").to_dict()
    payload["event"] = {
        "type": f"handoff_{snapshot['state']}",
        "content_type": "application/json",
        "content": snapshot,
    }
    payload["metadata"] = {"trigger": trigger, "expected_version": expected_version}
    return Event.from_dict(payload)


def test_customer_tool_reply_and_handoff_record_successful_evidence_before_delivery() -> None:
    h = harness(
        action("tool", name="get_invoice", arguments={"invoice_id": "INV-3021"}),
        action("reply", text="I checked the invoice."),
        action("handoff", reason="refund_requires_approval", summary="Proposed summary"),
    )

    def check_prepared(kind: str) -> None:
        assert kind in h.kinds()

    h.tools.before = lambda: check_prepared("tool_prepared")
    h.channel.before = check_prepared
    result = h.runtime.ingest(message(), trusted_tenant="t1")
    assert result["status"] == "pending_human"
    assert result["resolution"] == "pending_human"
    assert result["handoff"]["state"] == "requested"
    assert result["handoff"]["actions_completed"] == ["get_invoice"]
    assert result["handoff"]["evidence_refs"]
    assert len(h.tools.calls) == len(h.channel.replies) == len(h.channel.handoffs) == 1
    assert h.agent.contexts[1]["tool_results"][0]["status"] == "succeeded"
    assert h.agent.contexts[0]["interactions"][0]["content"] == "Duplicate charge"
    assert h.repository.get_conversation("t1", "c1").ai_paused
    assert "handoff_accepted" not in h.kinds()
    assert [entry.kind for entry in h.repository.list_interactions("t1", "c1")] == [
        "customer_message",
        "tool_call",
        "tool_result",
        "agent_message",
        "handoff_requested",
    ]


def test_replay_returns_saved_result_and_conflict_is_audited_without_another_effect() -> None:
    h = harness(action("reply", text="Hello"))
    event = message()
    first = h.runtime.ingest(event, trusted_tenant="t1")
    reordered = event.to_dict()
    reordered = dict(reversed(list(reordered.items())))
    assert h.runtime.ingest(Event.from_dict(reordered), trusted_tenant="t1") == first
    assert len(h.channel.replies) == 1
    with pytest.raises(ReplayConflict):
        h.runtime.ingest(message(text="Different payload"), trusted_tenant="t1")
    assert h.kinds().count("replay_conflict") == 1
    first["status"] = "caller mutation"
    assert h.runtime.ingest(event, trusted_tenant="t1")["status"] == "completed"


def test_concurrent_duplicates_return_pending_and_execute_once() -> None:
    h = harness(action("reply", text="Hello"))
    started, release = ThreadEvent(), ThreadEvent()

    def delay() -> None:
        started.set()
        assert release.wait(5)

    h.agent.before = delay
    with ThreadPoolExecutor(max_workers=9) as pool:
        first = pool.submit(h.runtime.ingest, message(), trusted_tenant="t1")
        try:
            assert started.wait(5)
            duplicates = list(
                pool.map(lambda _: h.runtime.ingest(message(), trusted_tenant="t1"), range(16))
            )
            assert all(item["status"] == "pending" for item in duplicates)
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "completed"
    assert len(h.channel.replies) == 1
    assert (
        sum(
            entry.kind == "customer_message" for entry in h.repository.list_interactions("t1", "c1")
        )
        == 1
    )


def test_distinct_concurrent_messages_share_serialized_conversation_context() -> None:
    h = harness()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda n: h.runtime.ingest(message(f"evt{n}"), trusted_tenant="t1"), range(16))
        )
    assert all(item["status"] == "completed" for item in results)
    assert sorted(len(ctx["interactions"]) for ctx in h.agent.contexts) == list(range(1, 17))


def test_untrusted_tenant_and_cross_customer_cannot_claim_or_access_event() -> None:
    h = harness()
    with pytest.raises(PermissionError):
        h.runtime.ingest(message(), trusted_tenant="t2")
    with pytest.raises(ValueError, match="reference mismatch"):
        h.runtime.ingest(message(customer="u2"), trusted_tenant="t1")
    assert h.kinds() == []
    assert h.runtime.ingest(message(), trusted_tenant="t1")["status"] == "completed"
    assert h.runtime.ingest(message(tenant="t2"), trusted_tenant="t2")["status"] == "completed"
    assert len(h.store.list_audit("t2", "c1")) > 0
    assert len(h.repository.list_interactions("t2", "c1")) == 1


@pytest.mark.parametrize(
    "tool,tenant", [("issue_refund", "t1"), ("unlisted", "t1"), ("get_invoice", "t2")]
)
def test_tool_denied_before_execution_with_attempt_and_permission_audit(
    tool: str, tenant: str
) -> None:
    h = harness(action("tool", name=tool, arguments={}))
    assert h.runtime.ingest(message(tenant=tenant), trusted_tenant=tenant)["status"] == "completed"
    assert h.tools.calls == []
    kinds = [entry["kind"] for entry in h.store.list_audit(tenant, "c1")]
    assert "tool_attempt" in kinds and "tool_denied" in kinds
    assert "tool_prepared" not in kinds
    assert h.agent.contexts[-1]["tool_results"] == [{"name": tool, "status": "denied"}]


@pytest.mark.parametrize(
    "error,expected", [(TimeoutError("uncertain"), "unknown"), (ValueError("failed"), "completed")]
)
def test_tool_failure_and_timeout_do_not_fabricate_success_or_repeat(
    error: Exception, expected: str
) -> None:
    h = harness(action("tool", name="get_invoice", arguments={}))
    h.tools.error = error
    first = h.runtime.ingest(message(), trusted_tenant="t1")
    assert first["status"] == expected
    assert h.runtime.ingest(message(), trusted_tenant="t1") == first
    assert len(h.tools.calls) == 1
    assert "tool_succeeded" not in h.kinds()
    assert "tool_unknown" in h.kinds() if expected == "unknown" else "tool_failed" in h.kinds()
    assert all(item.kind != "tool_result" for item in h.repository.list_interactions("t1", "c1"))


@pytest.mark.parametrize("delivery", ["unknown", "failed", "invalid"])
def test_uncertain_or_failed_output_is_checkpointed_without_automatic_resend(delivery: str) -> None:
    h = harness(action("reply", text="Hello"))
    h.channel.delivery = delivery
    first = h.runtime.ingest(message(), trusted_tenant="t1")
    assert first["status"] == ("unknown" if delivery == "invalid" else delivery)
    assert h.runtime.ingest(message(), trusted_tenant="t1") == first
    assert len(h.channel.replies) == 1
    assert not any(
        item.kind == "agent_message" for item in h.repository.list_interactions("t1", "c1")
    )


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError()])
def test_channel_handoff_failure_preserves_requested_pause_and_pending_human(
    error: Exception,
) -> None:
    h = harness(action("handoff", reason="refund_requires_approval", summary="Needs approval"))
    h.channel.error = error
    first = h.runtime.ingest(message(), trusted_tenant="t1")
    assert first["status"] == "pending_human"
    assert first["handoff"]["state"] == "requested"
    assert h.repository.get_conversation("t1", "c1").ai_paused
    assert h.runtime.ingest(message("next"), trusted_tenant="t1")["status"] == "paused"
    assert len(h.channel.handoffs) == 1


def test_handoff_requires_explicit_grant_and_assignment_cannot_accept() -> None:
    h = harness()
    snapshot = request_snapshot()
    event = callback(snapshot, trigger="request", expected_version=0)
    with pytest.raises(PermissionError):
        h.runtime.ingest(event, trusted_tenant="t1")
    assert h.store.get_handoff("t1", "c1") is None
    assert (
        h.runtime.ingest(event, trusted_tenant="t1", can_transition=True)["status"]
        == "handoff_updated"
    )
    accepted = deepcopy(snapshot)
    accepted.update(state="accepted", version=2, evidence_refs=["synthetic:assignment"])
    result = h.runtime.ingest(
        callback(accepted, trigger="assignment", expected_version=1),
        trusted_tenant="t1",
        can_transition=True,
    )
    assert result["status"] == "rejected"
    assert h.store.get_handoff("t1", "c1") == snapshot


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "completed"])
def test_handoff_lifecycle_never_automatically_resumes_ai(terminal: str) -> None:
    h = harness()
    snapshot = request_snapshot()
    h.runtime.ingest(
        callback(snapshot, trigger="request", expected_version=0),
        trusted_tenant="t1",
        can_transition=True,
    )
    sequence = [(terminal, "failure" if terminal == "failed" else "cancellation")]
    if terminal == "completed":
        sequence = [
            ("accepted", "human_acceptance"),
            ("in_progress", "human_service_action"),
            ("completed", "completion"),
        ]
    for state, trigger in sequence:
        expected_version = snapshot["version"]
        snapshot.update(
            state=state, version=expected_version + 1, evidence_refs=[f"synthetic:{state}"]
        )
        result = h.runtime.ingest(
            callback(snapshot, trigger=trigger, expected_version=expected_version),
            trusted_tenant="t1",
            can_transition=True,
        )
        assert result["status"] == "handoff_updated"
    assert h.runtime.ingest(message(), trusted_tenant="t1")["status"] == "paused"
    assert h.agent.contexts == []
    assert h.channel.replies == []
    assert h.repository.get_conversation("t1", "c1").resolution == "pending_human"
    assert h.repository.list_interactions("t1", "c1")[-1].kind == "customer_message"


def test_handoff_retry_preserves_history_and_rejects_stale_attempt_and_rewritten_context() -> None:
    h = harness()
    snapshot = request_snapshot()
    h.runtime.ingest(
        callback(snapshot, trigger="request", expected_version=0),
        trusted_tenant="t1",
        can_transition=True,
    )
    failed = deepcopy(snapshot)
    failed.update(state="failed", version=2)
    h.runtime.ingest(
        callback(failed, trigger="failure", expected_version=1),
        trusted_tenant="t1",
        can_transition=True,
    )
    retry = deepcopy(failed)
    retry.update(state="requested", version=3, attempt=2)
    assert (
        h.runtime.ingest(
            callback(retry, trigger="retry", expected_version=2),
            trusted_tenant="t1",
            can_transition=True,
        )["status"]
        == "handoff_updated"
    )
    stale = deepcopy(retry)
    stale.update(state="accepted", version=4, attempt=1)
    assert (
        h.runtime.ingest(
            callback(stale, trigger="human_acceptance", expected_version=3, id="stale"),
            trusted_tenant="t1",
            can_transition=True,
        )["status"]
        == "rejected"
    )
    changed = deepcopy(retry)
    changed.update(state="accepted", version=4, summary="Unauthenticated rewritten facts")
    assert (
        h.runtime.ingest(
            callback(changed, trigger="human_acceptance", expected_version=3, id="changed"),
            trusted_tenant="t1",
            can_transition=True,
        )["status"]
        == "rejected"
    )
    assert h.store.get_handoff("t1", "c1") == retry
    snapshots = [row.content for row in h.repository.list_interactions("t1", "c1")]
    assert isinstance(snapshots[1], dict) and snapshots[1]["state"] == "failed"
    assert isinstance(snapshots[2], dict) and snapshots[2]["attempt"] == 2


def test_late_ai_reply_is_suppressed_when_handoff_arrives_during_agent_call() -> None:
    h = harness(action("reply", text="Must never be sent"))
    started, release = ThreadEvent(), ThreadEvent()

    def slow_agent() -> None:
        started.set()
        assert release.wait(5)

    h.agent.before = slow_agent
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(h.runtime.ingest, message(), trusted_tenant="t1")
        try:
            assert started.wait(5)
            result = h.runtime.ingest(
                callback(request_snapshot(), trigger="request", expected_version=0),
                trusted_tenant="t1",
                can_transition=True,
            )
            assert result["status"] == "handoff_updated"
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "paused"
    assert h.channel.replies == []
    assert "reply_suppressed" in h.kinds()


def test_invalid_agent_decision_and_step_budget_are_explicit_and_replay_safe() -> None:
    h = harness({"intent": "billing", "action": {"type": "refund_now"}})
    result = h.runtime.ingest(message(), trusted_tenant="t1")
    assert result["status"] == "failed"
    assert h.runtime.ingest(message(), trusted_tenant="t1") == result
    assert len(h.agent.contexts) == 1
    assert h.tools.calls == h.channel.replies == []
    limited = harness(action("tool", name="issue_refund", arguments={}), max_steps=1)
    assert limited.runtime.ingest(message(), trusted_tenant="t1")["status"] == "step_limit"
    assert limited.tools.calls == []


def test_runtime_store_copies_claim_finish_audit_and_handoff_snapshots() -> None:
    store = MemoryRuntimeStore()
    payload = message().to_dict()
    created, pending = store.claim(payload)
    assert created
    pending["status"] = "changed"
    assert store.claim(payload)[1]["status"] == "pending"
    result: dict[str, Any] = {"status": "completed", "nested": ["original"]}
    store.finish("t1", "evt1", result)
    result["nested"].clear()
    assert store.claim(payload)[1]["nested"] == ["original"]
    record = {"values": ["source"]}
    store.append_audit("t1", "c1", "example", record)
    record["values"].clear()
    rows = store.list_audit("t1", "c1")
    rows[0]["data"]["values"].clear()
    assert store.list_audit("t1", "c1")[0]["data"] == {"values": ["source"]}
    assert store.list_audit("t2", "c1") == []
    snapshot = request_snapshot()
    store.save_handoff(snapshot)
    snapshot["evidence_refs"].clear()
    retrieved = store.get_handoff("t1", "c1")
    assert retrieved is not None
    retrieved["evidence_refs"].clear()
    assert store.get_handoff("t1", "c1") == request_snapshot()


def test_handoff_initial_snapshot_cannot_forge_customer_organization() -> None:
    h = harness()
    snapshot = request_snapshot()
    snapshot["customer"]["organization_id"] = "other-tenant-or-unknown-organization"
    result = h.runtime.ingest(
        callback(snapshot, trigger="request", expected_version=0),
        trusted_tenant="t1",
        can_transition=True,
    )
    assert result["status"] == "rejected"
    assert h.store.get_handoff("t1", "c1") is None
    assert not h.repository.get_conversation("t1", "c1").ai_paused


def test_agent_timeout_is_checkpointed_and_cannot_reexecute_on_replay() -> None:
    h = harness()
    h.agent.error = TimeoutError("synthetic timeout")
    result = h.runtime.ingest(message(), trusted_tenant="t1")
    assert result["status"] == "failed"
    assert h.runtime.ingest(message(), trusted_tenant="t1") == result
    assert len(h.agent.contexts) == 1
    assert h.tools.calls == h.channel.replies == []


def test_channel_timeout_is_unknown_with_stable_action_and_no_blind_retry() -> None:
    h = harness(action("reply", text="Response lost after sending"))
    h.channel.error = TimeoutError("response lost")
    result = h.runtime.ingest(message(), trusted_tenant="t1")
    assert result["status"] == "unknown"
    assert h.runtime.ingest(message(), trusted_tenant="t1") == result
    assert len(h.channel.replies) == 1
    action_id = h.channel.replies[0]["action_id"]
    audits = h.store.list_audit("t1", "c1")
    assert [
        row["data"]["action_id"]
        for row in audits
        if row["kind"] in {"output_prepared", "output_result"}
    ] == [action_id, action_id]


def test_handoff_replay_is_idempotent_but_new_event_cannot_repeat_a_state_change() -> None:
    h = harness()
    snapshot = request_snapshot()
    initial = callback(snapshot, trigger="request", expected_version=0)
    result = h.runtime.ingest(initial, trusted_tenant="t1", can_transition=True)
    assert h.runtime.ingest(initial, trusted_tenant="t1", can_transition=True) == result
    duplicate = callback(snapshot, trigger="request", expected_version=0, id="new-replayed-request")
    assert (
        h.runtime.ingest(duplicate, trusted_tenant="t1", can_transition=True)["status"]
        == "rejected"
    )
    assert len(h.repository.list_interactions("t1", "c1")) == 1
    assert h.store.get_handoff("t1", "c1") == snapshot


def test_customer_event_id_cannot_collide_with_generated_handoff_interaction() -> None:
    h = harness()
    assert h.runtime.ingest(message("ho1:1"), trusted_tenant="t1")["status"] == "completed"
    snapshot = request_snapshot()
    result = h.runtime.ingest(
        callback(snapshot, trigger="request", expected_version=0),
        trusted_tenant="t1",
        can_transition=True,
    )
    assert result["status"] == "handoff_updated"
    assert h.store.get_handoff("t1", "c1") == snapshot
    history = h.repository.list_interactions("t1", "c1")
    assert [entry.kind for entry in history] == ["customer_message", "handoff_requested"]
    assert history[0].content == "Duplicate charge"
    assert history[1].content == snapshot
    assert history[0].id != history[1].id


def test_same_handoff_id_in_different_conversations_has_isolated_interactions() -> None:
    h = harness()
    h.repository.add_conversation(Conversation("t1", "c2", "u1"))
    for index, conversation_id in enumerate(("c1", "c2")):
        snapshot = request_snapshot()
        snapshot["conversation_id"] = conversation_id
        payload = message(f"request-{index}").to_dict()
        payload["conversation_id"] = conversation_id
        payload["event"] = {
            "type": "handoff_requested",
            "content_type": "application/json",
            "content": snapshot,
        }
        payload["metadata"] = {"trigger": "request", "expected_version": 0}
        result = h.runtime.ingest(
            Event.from_dict(payload), trusted_tenant="t1", can_transition=True
        )
        assert result["status"] == "handoff_updated"
        assert h.store.get_handoff("t1", conversation_id) == snapshot
        assert h.repository.list_interactions("t1", conversation_id)[0].content == snapshot
    first = h.repository.list_interactions("t1", "c1")[0]
    second = h.repository.list_interactions("t1", "c2")[0]
    assert first.id != second.id


class FirstReceiptAuditFailure(MemoryRuntimeStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def append_audit(
        self, tenant_id: str, conversation_id: str, kind: str, data: dict[str, Any]
    ) -> str:
        if kind == "event_received" and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("Synthetic first receipt audit failure")
        return super().append_audit(tenant_id, conversation_id, kind, data)


@pytest.mark.parametrize("is_handoff", [False, True])
def test_first_receipt_audit_failure_checkpoints_failure_instead_of_leaving_pending(
    is_handoff: bool,
) -> None:
    h = harness(action("reply", text="Must not be sent"))
    h.store = FirstReceiptAuditFailure()
    h.runtime.store = h.store
    event = (
        callback(request_snapshot(), trigger="request", expected_version=0)
        if is_handoff
        else message()
    )
    result = h.runtime.ingest(event, trusted_tenant="t1", can_transition=is_handoff)
    assert result["status"] == "failed"
    assert h.runtime.ingest(event, trusted_tenant="t1", can_transition=is_handoff) == result
    assert h.store.claim(event.to_dict())[1] == result
    assert h.agent.contexts == []
    assert h.tools.calls == h.channel.replies == h.channel.handoffs == []
    assert h.store.get_handoff("t1", "c1") is None
    assert h.repository.list_interactions("t1", "c1") == []


@pytest.mark.parametrize(
    "response",
    [
        {"delivery": []},
        {"delivery": {}},
        {"delivery": "sent", "invalid": float("nan")},
    ],
)
@pytest.mark.parametrize("is_handoff", [False, True])
def test_malformed_channel_delivery_is_unknown_and_never_blindly_retried(
    response: dict[str, Any], is_handoff: bool
) -> None:
    decision = (
        action("handoff", reason="refund_requires_approval", summary="Approval required")
        if is_handoff
        else action("reply", text="Hello")
    )
    h = harness(decision)
    h.channel.response = response
    first = h.runtime.ingest(message(), trusted_tenant="t1")
    if is_handoff:
        assert first["status"] == "pending_human"
        assert first["delivery"] == "unknown"
        assert first["handoff"]["state"] == "requested"
    else:
        assert first["status"] == "unknown"
    assert h.runtime.ingest(message(), trusted_tenant="t1") == first
    assert len(h.channel.handoffs if is_handoff else h.channel.replies) == 1
    assert not any(
        entry.kind == "agent_message" for entry in h.repository.list_interactions("t1", "c1")
    )
