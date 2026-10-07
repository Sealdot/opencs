"""Synthetic HTTP contract tests, not live provider integration evidence."""

import json
from typing import Any

import pytest

from adapters import (
    AdapterError,
    ChatwootAdapter,
    FakeTransport,
    GenericHttpToolAdapter,
    HttpResponse,
    OpenAIResponsesAdapter,
    ToolDefinition,
)
from opencs.domain import Tenant
from opencs.examples.billing import resource
from opencs.persistence.memory import MemoryRepository


@pytest.fixture
def payload() -> dict[str, Any]:
    return resource("chatwoot-message.json")


def chatwoot(transport: FakeTransport, **overrides: Any) -> ChatwootAdapter:
    repository = MemoryRepository()
    repository.add_tenant(Tenant("tenant_a"))
    config: dict[str, Any] = dict(
        base_url="https://chatwoot.invalid",
        account_id=1,
        inbox_id=2,
        tenant_id="tenant_a",
        token="synthetic-token",
        transport=transport,
        repository=repository,
        webhook_secret="synthetic-secret",
        team_id=9,
    )
    config.update(overrides)
    return ChatwootAdapter(**config)


def test_chatwoot_binding_stable_event_and_send(payload: dict[str, Any]) -> None:
    transport = FakeTransport([HttpResponse(200, {"id": 501})])
    adapter = chatwoot(transport)
    payload["tenant_id"] = "attacker"
    payload["metadata"] = {"external_conversation_id": 666}
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    assert event.tenant_id == "tenant_a"
    assert adapter.normalize(payload, credential="synthetic-secret") == event
    result = adapter.send(
        tenant_id=event.tenant_id,
        conversation_id=event.conversation_id,
        text="Checked invoice",
        action_id="action_1",
    )
    assert result["delivery"] == "sent"
    assert transport.requests[0].url.endswith("/conversations/42/messages")
    assert transport.requests[0].json_body == {
        "content": "Checked invoice",
        "message_type": "outgoing",
        "private": False,
        "content_type": "text",
    }
    assert "synthetic-token" not in repr(transport.requests[0])
    with pytest.raises(AdapterError):
        adapter.send(
            tenant_id="attacker",
            conversation_id=event.conversation_id,
            text="bad",
            action_id="action_2",
        )
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("private", True),
        ("message_type", "outgoing"),
        ("message_type", 0),
        ("content_type", "image"),
        ("attachments", [{"id": 1}]),
        ("content", ""),
        ("event", "conversation_updated"),
    ],
)
def test_chatwoot_filters(payload: dict[str, Any], field: str, value: Any) -> None:
    payload[field] = value
    transport = FakeTransport()
    assert chatwoot(transport).normalize(payload, credential="synthetic-secret") is None
    assert not transport.requests


@pytest.mark.parametrize("change", ["account", "inbox", "id", "timestamp", "missing"])
def test_chatwoot_rejects_changed_schema(payload: dict[str, Any], change: str) -> None:
    if change in {"account", "inbox"}:
        payload[change]["id"] = 999
    elif change == "timestamp":
        payload["created_at"] = "2026-09-07T08:00:00"
    elif change == "missing":
        del payload["sender"]
    else:
        payload["id"] = "101"
    with pytest.raises(AdapterError):
        chatwoot(FakeTransport()).normalize(payload, credential="synthetic-secret")


def test_chatwoot_auth_before_mapping(payload: dict[str, Any]) -> None:
    adapter = chatwoot(FakeTransport())
    with pytest.raises(AdapterError):
        adapter.normalize(payload, credential="wrong")
    with pytest.raises(AdapterError):
        adapter.send(tenant_id="tenant_a", conversation_id="42", text="x", action_id="a")


@pytest.mark.parametrize(
    "response,delivery",
    [
        (HttpResponse(401, {}), "failed"),
        (HttpResponse(403, {}), "failed"),
        (HttpResponse(429, {}), "failed"),
        (HttpResponse(500, {}), "failed"),
        (TimeoutError(), "unknown"),
        (HttpResponse(200, {"renamed_id": 5}), "unknown"),
    ],
)
def test_chatwoot_never_retries(
    payload: dict[str, Any], response: HttpResponse | TimeoutError, delivery: str
) -> None:
    transport = FakeTransport([response])
    adapter = chatwoot(transport)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    result = adapter.send(
        tenant_id="tenant_a", conversation_id=event.conversation_id, text="hello", action_id="a"
    )
    assert result["delivery"] == delivery
    assert len(transport.requests) == 1


def test_handoff_note_assignment_separate_and_audited(payload: dict[str, Any]) -> None:
    transport = FakeTransport([HttpResponse(200, {"id": 5}), HttpResponse(200, {})])
    records: list[dict[str, Any]] = []

    def before(record: dict[str, Any]) -> None:
        assert len(transport.requests) == len(records)
        records.append(record)

    adapter = chatwoot(transport, before_request=before)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    result = adapter.handoff(
        tenant_id="tenant_a",
        conversation_id=event.conversation_id,
        snapshot={
            "tenant_id": "tenant_a",
            "conversation_id": event.conversation_id,
            "reason": "refund_requires_approval",
        },
        action_id="handoff_1",
    )
    assert result["delivery"] == "sent"
    assert "accepted" not in json.dumps(result).lower()
    assert [item["action_id"] for item in records] == ["handoff_1:context", "handoff_1:assignment"]
    assert transport.requests[0].json_body is not None
    assert transport.requests[0].json_body["private"] is True
    assert transport.requests[1].json_body == {"team_id": 9}


def test_handoff_context_timeout_stops_assignment(payload: dict[str, Any]) -> None:
    transport = FakeTransport([TimeoutError()])
    adapter = chatwoot(transport)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    result = adapter.handoff(
        tenant_id="tenant_a",
        conversation_id=event.conversation_id,
        snapshot={"tenant_id": "tenant_a", "conversation_id": event.conversation_id},
        action_id="h",
    )
    assert result["delivery"] == "unknown"
    assert len(transport.requests) == 1


def tool_adapter(transport: FakeTransport) -> GenericHttpToolAdapter:
    return GenericHttpToolAdapter(
        transport=transport,
        registry={
            "get_invoice": ToolDefinition(
                "https://billing.invalid/invoice", "POST", frozenset({"tenant_a"})
            ),
        },
    )


def test_tool_static_binding_and_result() -> None:
    transport = FakeTransport([HttpResponse(200, {"invoice": "inv_1"})])
    result = tool_adapter(transport).execute(
        "get_invoice", {"url": "https://evil.invalid"}, tenant_id="tenant_a", action_id="a"
    )
    assert result == {"invoice": "inv_1"}
    assert transport.requests[0].url == "https://billing.invalid/invoice"


@pytest.mark.parametrize("name,tenant", [("issue_refund", "tenant_a"), ("get_invoice", "tenant_b")])
def test_tool_unknown_or_other_tenant_never_sent(name: str, tenant: str) -> None:
    transport = FakeTransport()
    with pytest.raises(AdapterError):
        tool_adapter(transport).execute(name, {}, tenant_id=tenant, action_id="a")
    assert not transport.requests


@pytest.mark.parametrize(
    "response", [HttpResponse(status, {}) for status in (401, 403, 429, 500)] + [TimeoutError()]
)
def test_tool_failure_no_retry(response: HttpResponse | TimeoutError) -> None:
    transport = FakeTransport([response])
    with pytest.raises((AdapterError, TimeoutError)):
        tool_adapter(transport).execute("get_invoice", {}, tenant_id="tenant_a", action_id="a")
    assert len(transport.requests) == 1


def llm_response(text: str) -> HttpResponse:
    return HttpResponse(
        200,
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        },
    )


def openai(transport: FakeTransport) -> OpenAIResponsesAdapter:
    return OpenAIResponsesAdapter(
        model="synthetic-model", api_key="synthetic-key", transport=transport
    )


@pytest.mark.parametrize(
    "action",
    [
        {"type": "reply", "text": "hello"},
        {"type": "done"},
        {"type": "handoff", "reason": "refund_requires_approval", "summary": "Duplicate charge"},
        {"type": "tool", "name": "get_invoice", "arguments_json": '{"id":"inv_1"}'},
    ],
)
def test_openai_mapping(action: dict[str, Any]) -> None:
    transport = FakeTransport([llm_response(json.dumps({"intent": "billing", "action": action}))])
    result = openai(transport).decide({"message": "Investigate charges"})
    assert result["intent"] == "billing"
    assert result["action"]["type"] == action["type"]
    if action["type"] == "tool":
        assert result["action"]["arguments"] == {"id": "inv_1"}
    request = transport.requests[0]
    assert request.url == "https://api.openai.com/v1/responses"
    assert request.json_body is not None
    assert request.json_body["text"]["format"]["strict"] is True
    assert "tools" not in request.json_body
    assert request.json_body["store"] is False


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        "{}",
        '{"intent":"billing","action":{"type":"refund"}}',
        '{"intent":"billing","action":{"type":"tool","name":"get_invoice","arguments_json":"[]"}}',
        '{"intent":"billing","action":{"type":"reply","text":"hi","url":"evil"}}',
    ],
)
def test_openai_invalid_decision(text: str) -> None:
    transport = FakeTransport([llm_response(text)])
    with pytest.raises(AdapterError):
        openai(transport).decide({})
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "response",
    [HttpResponse(code, {}) for code in (401, 403, 429, 500)]
    + [
        TimeoutError(),
        HttpResponse(200, {"status": "incomplete", "output": []}),
        HttpResponse(
            200,
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "Cannot answer"}],
                    }
                ],
            },
        ),
        HttpResponse(200, {"status": "completed", "output_text": "{}"}),
    ],
)
def test_openai_failure_or_refusal_no_retry(response: HttpResponse | TimeoutError) -> None:
    transport = FakeTransport([response])
    with pytest.raises((AdapterError, TimeoutError)):
        openai(transport).decide({})
    assert len(transport.requests) == 1


def test_chatwoot_provider_scope_and_explicit_customer_mapping(payload: dict[str, Any]) -> None:
    repository = MemoryRepository()
    repository.add_tenant(Tenant("tenant_a"))
    first = chatwoot(FakeTransport(), repository=repository)
    second = chatwoot(FakeTransport(), repository=repository, base_url="https://other.invalid")
    a = first.normalize(payload, credential="synthetic-secret")
    b = second.normalize(payload, credential="synthetic-secret")
    assert a is not None and b is not None
    assert a.conversation_id != b.conversation_id
    assert a.customer_id != b.customer_id
    assert a.event_id != b.event_id
    payload["conversation"]["id"] = 43
    c = first.normalize(payload, credential="synthetic-secret")
    assert c is not None
    assert c.conversation_id != a.conversation_id
    assert c.customer_id == a.customer_id


def test_chatwoot_audit_failure_prevents_any_request(payload: dict[str, Any]) -> None:
    transport = FakeTransport()

    def failed_audit(record: dict[str, Any]) -> None:
        raise RuntimeError("Audit unavailable")

    adapter = chatwoot(transport, before_request=failed_audit)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        adapter.send(
            tenant_id="tenant_a", conversation_id=event.conversation_id, text="hello", action_id="a"
        )
    assert not transport.requests


def test_handoff_assignment_timeout_keeps_both_results(payload: dict[str, Any]) -> None:
    transport = FakeTransport([HttpResponse(200, {"id": 5}), TimeoutError()])
    adapter = chatwoot(transport)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    result = adapter.handoff(
        tenant_id="tenant_a",
        conversation_id=event.conversation_id,
        snapshot={"tenant_id": "tenant_a", "conversation_id": event.conversation_id},
        action_id="h",
    )
    assert result["delivery"] == "unknown"
    assert [action["delivery"] for action in result["actions"]] == ["sent", "unknown"]
    assert len(transport.requests) == 2


@pytest.mark.parametrize("team_id,snapshot_tenant", [(None, "tenant_a"), (9, "other")])
def test_handoff_binding_and_team_validation(
    payload: dict[str, Any], team_id: int | None, snapshot_tenant: str
) -> None:
    transport = FakeTransport()
    adapter = chatwoot(transport, team_id=team_id)
    event = adapter.normalize(payload, credential="synthetic-secret")
    assert event is not None
    with pytest.raises(AdapterError):
        adapter.handoff(
            tenant_id="tenant_a",
            conversation_id=event.conversation_id,
            snapshot={"tenant_id": snapshot_tenant, "conversation_id": event.conversation_id},
            action_id="h",
        )
    assert not transport.requests


def test_fake_transport_copies_request_and_has_no_fallback() -> None:
    transport = FakeTransport([HttpResponse(200, {})])
    body = {"arguments": {"id": "original"}}
    transport.request("POST", "https://fake.invalid", {}, body)
    body["arguments"]["id"] = "mutated"
    assert transport.requests[0].json_body == {"arguments": {"id": "original"}}
    with pytest.raises(RuntimeError, match="no network fallback"):
        transport.request("POST", "https://fake.invalid", {}, {})
