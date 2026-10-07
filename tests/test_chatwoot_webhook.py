"""Signed bytes -> ASGI -> Chatwoot mapping -> real offline Runtime integration."""

import asyncio
import hashlib
import hmac
import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from adapters import (
    ChatwootAdapter,
    ChatwootWebhookVerifier,
    FakeTransport,
    HttpResponse,
    WebhookAuthenticationError,
)
from adapters.chatwoot_webhook import create_chatwoot_app
from opencs.api import ASGIApp
from opencs.application.memory_store import MemoryRuntimeStore
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Event, Tenant
from opencs.persistence.memory import MemoryRepository

NOW = 1_790_000_000
SECRET = "synthetic-native-signing-secret"


def signature(body: bytes, timestamp: str = str(NOW), secret: str = SECRET) -> str:
    return (
        "sha256="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


def headers(body: bytes, timestamp: str = str(NOW)) -> list[tuple[bytes, bytes]]:
    return [
        (b"content-type", b"application/json"),
        (b"x-chatwoot-timestamp", timestamp.encode()),
        (b"x-chatwoot-signature", signature(body, timestamp).encode()),
    ]


def request(
    app: ASGIApp,
    body: bytes,
    *,
    request_headers: list[tuple[bytes, bytes]] | None = None,
    chunks: list[dict[str, Any]] | None = None,
    path: str = "/webhooks/chatwoot",
    method: str = "POST",
) -> tuple[int, dict[str, Any]]:
    async def run() -> tuple[int, dict[str, Any]]:
        incoming = deque(chunks or [{"type": "http.request", "body": body}])
        outgoing: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return incoming.popleft()

        async def send(message: dict[str, Any]) -> None:
            outgoing.append(message)

        await app(
            {
                "type": "http",
                "path": path,
                "method": method,
                "headers": request_headers if request_headers is not None else headers(body),
            },
            receive,
            send,
        )
        if not outgoing:
            return 0, {}
        assert len(outgoing) == 2
        assert (b"cache-control", b"no-store") in outgoing[0]["headers"]
        return outgoing[0]["status"], json.loads(outgoing[1]["body"])

    return asyncio.run(run())


class ReplyAgent:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if context["step"]:
            return {"intent": "billing", "action": {"type": "done"}}
        return {"intent": "billing", "action": {"type": "reply", "text": "Checking your invoice"}}


class NoTools:
    def execute(
        self, name: str, arguments: dict[str, Any], *, tenant_id: str, action_id: str
    ) -> dict[str, Any]:
        raise AssertionError("Unexpected tool")


class RecordingRepository(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.mapping_calls = 0

    def resolve_external(
        self, tenant: str, provider: str, external_conversation_id: str, external_contact_id: str
    ) -> Any:
        self.mapping_calls += 1
        return super().resolve_external(
            tenant, provider, external_conversation_id, external_contact_id
        )


class Setup:
    def __init__(self, *, max_body_bytes: int = 1_048_576) -> None:
        self.repository = RecordingRepository()
        self.repository.add_tenant(Tenant("tenant_a"))
        self.transport = FakeTransport([HttpResponse(200, {"id": 123})])
        self.adapter = ChatwootAdapter(
            base_url="https://chatwoot.invalid",
            account_id=1,
            inbox_id=2,
            tenant_id="tenant_a",
            token="synthetic-api-token",
            transport=self.transport,
            repository=self.repository,
            webhook_secret="different-legacy-gateway-credential",
        )
        self.agent = ReplyAgent()
        self.store = MemoryRuntimeStore()
        self.runtime = Runtime(
            repository=self.repository,
            store=self.store,
            agent=self.agent,
            channel=self.adapter,
            tools=NoTools(),
            policy=ToolPolicy(frozenset(), frozenset(), frozenset({"tenant_a"})),
            handoff_target={"type": "human_queue", "id": "billing"},
            handoff_reason="refund_requires_approval",
            pending_resolution="pending_human",
        )
        self.verifier = ChatwootWebhookVerifier(SECRET, clock=lambda: NOW)
        self.app = create_chatwoot_app(
            self.adapter, self.runtime, self.verifier, max_body_bytes=max_body_bytes
        )


@pytest.fixture
def payload() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        Path("opencs/examples/data/chatwoot-message.json").read_text()
    )
    return value


@pytest.mark.parametrize("delta", [-30, 0, 300])
def test_verifier_time_boundaries(delta: int) -> None:
    body = ' {"content":"账单"}\n'.encode()
    timestamp = str(NOW - delta)
    ChatwootWebhookVerifier(SECRET, clock=lambda: NOW).verify(
        body, timestamp=timestamp, signature=signature(body, timestamp)
    )


@pytest.mark.parametrize("delta", [-31, 301])
def test_verifier_rejects_stale_or_future(delta: int) -> None:
    timestamp = str(NOW - delta)
    with pytest.raises(WebhookAuthenticationError):
        ChatwootWebhookVerifier(SECRET, clock=lambda: NOW).verify(
            b"{}", timestamp=timestamp, signature=signature(b"{}", timestamp)
        )


@pytest.mark.parametrize("timestamp", ["", "-1", "+1", "1.0", " 1", "١", "1" * 21])
def test_verifier_malformed_timestamp(timestamp: str) -> None:
    with pytest.raises(WebhookAuthenticationError):
        ChatwootWebhookVerifier(SECRET).verify(
            b"{}", timestamp=timestamp, signature="sha256=" + "0" * 64
        )


@pytest.mark.parametrize("value", ["", "sha256=abc", "sha1=" + "0" * 64, "sha256=" + "G" * 64])
def test_verifier_malformed_signature(value: str) -> None:
    with pytest.raises(WebhookAuthenticationError):
        ChatwootWebhookVerifier(SECRET, clock=lambda: NOW).verify(
            b"{}", timestamp=str(NOW), signature=value
        )


@pytest.mark.parametrize(
    "config", [{"secret": ""}, {"max_age_seconds": -1}, {"future_skew_seconds": True}]
)
def test_verifier_configuration(config: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ChatwootWebhookVerifier(**({"secret": SECRET} | config))
    assert SECRET not in repr(ChatwootWebhookVerifier(SECRET))


def test_invalid_clock_fails_closed() -> None:
    with pytest.raises(WebhookAuthenticationError):
        ChatwootWebhookVerifier(SECRET, clock=lambda: float("nan")).verify(
            b"{}", timestamp=str(NOW), signature=signature(b"{}")
        )


def test_ruby_openssl_signature_vector() -> None:
    # Generated with Ruby OpenSSL using Chatwoot v4.17.1's signing expression.
    # UTF-8 and a trailing newline must survive unchanged across languages.
    body = '{"content":"账单"}\n'.encode()
    digest = "sha256=e259771ff351c252e78fda3efca8b3f0367dc60608fe0c3ea762fe11804314fa"
    verifier = ChatwootWebhookVerifier(SECRET, clock=lambda: NOW)
    verifier.verify(body, timestamp=str(NOW), signature=digest)
    with pytest.raises(WebhookAuthenticationError):
        verifier.verify(body.rstrip(), timestamp=str(NOW), signature=digest)


def test_signed_ingress_reply_replay_and_conflict(payload: dict[str, Any]) -> None:
    setup = Setup()
    payload["tenant_id"] = "attacker"
    payload["metadata"] = {"can_transition": True}
    body = json.dumps(payload, ensure_ascii=False).encode()
    first = request(setup.app, body)
    assert first[0] == 200 and first[1]["status"] == "completed"
    assert setup.agent.calls == 2 and len(setup.transport.requests) == 1
    assert setup.transport.requests[0].url.endswith("/conversations/42/messages")
    # Retries may have a fresh timestamp, delivery id and JSON serialization.
    reordered = json.dumps(payload, sort_keys=True).encode()
    retry_headers = headers(reordered, str(NOW + 1)) + [(b"x-chatwoot-delivery", b"other-delivery")]
    assert request(setup.app, reordered, request_headers=retry_headers) == first
    assert setup.agent.calls == 2 and len(setup.transport.requests) == 1
    event = setup.adapter.normalize_signed(
        body, timestamp=str(NOW), signature=signature(body), verifier=setup.verifier
    )
    assert event is not None and event.tenant_id == "tenant_a"
    audit = setup.store.list_audit(event.tenant_id, event.conversation_id)
    assert any(x["kind"] == "event_received" for x in audit)
    payload["content"] = "Changed customer message"
    assert request(setup.app, json.dumps(payload).encode())[0] == 409
    assert len(setup.transport.requests) == 1


@pytest.mark.parametrize(
    "change", ["body", "secret", "timestamp", "signature", "duplicate", "missing", "unicode"]
)
def test_auth_before_parse_mapping_and_agent(payload: dict[str, Any], change: str) -> None:
    setup = Setup()
    body = json.dumps(payload).encode()
    values = headers(body)
    if change == "body":
        body += b" "
    elif change == "secret":
        values[2] = (values[2][0], signature(body, secret="wrong").encode())
    elif change == "timestamp":
        values[1] = (values[1][0], str(NOW + 1).encode())
    elif change == "signature":
        values[2] = (values[2][0], b"sha256=" + b"0" * 64)
    elif change == "duplicate":
        values.append((b"X-Chatwoot-Signature", values[2][1]))
    elif change == "missing":
        values.pop()
    else:
        values[1] = (values[1][0], b"\xff")
    assert request(setup.app, body, request_headers=values)[0] == 401
    assert setup.repository.mapping_calls == setup.agent.calls == 0
    assert not setup.transport.requests


def test_invalid_signature_precedes_json_validation() -> None:
    setup = Setup()
    assert request(setup.app, b"{", request_headers=headers(b"{}"))[0] == 401
    assert setup.repository.mapping_calls == 0


@pytest.mark.parametrize(
    "field,value",
    [("account", {"id": 999}), ("inbox", {"id": 999}), ("sender", []), ("created_at", "bad")],
)
def test_signed_wrong_binding_or_shape(payload: dict[str, Any], field: str, value: Any) -> None:
    setup = Setup()
    payload[field] = value
    assert request(setup.app, json.dumps(payload).encode())[0] == 422
    assert setup.repository.mapping_calls == setup.agent.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("message_type", "outgoing"),
        ("private", True),
        ("attachments", [{"id": 1}]),
        ("event", "handoff_accepted"),
    ],
)
def test_signed_filtered_events_cannot_trigger_actions(
    payload: dict[str, Any], field: str, value: Any
) -> None:
    setup = Setup()
    payload[field] = value
    assert request(setup.app, json.dumps(payload).encode()) == (200, {"status": "ignored"})
    assert setup.repository.mapping_calls == setup.agent.calls == 0


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]", b"null"])
def test_ambiguous_or_invalid_signed_json(body: bytes) -> None:
    setup = Setup()
    assert request(setup.app, body)[0] == 422
    assert setup.repository.mapping_calls == 0


@pytest.mark.parametrize("body", [b"{", b"\xff", b"[" * 2000])
def test_broken_signed_json(body: bytes) -> None:
    setup = Setup()
    assert request(setup.app, body)[0] == 400
    assert setup.repository.mapping_calls == 0


def test_chunking_limits_disconnect_and_routes(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    setup = Setup(max_body_bytes=len(body))
    assert (
        request(
            setup.app,
            body,
            chunks=[
                {"type": "http.request", "body": body[:5], "more_body": True},
                {"type": "http.request", "body": body[5:]},
            ],
        )[0]
        == 200
    )
    assert request(setup.app, body + b" ")[0] == 413
    assert request(setup.app, body, chunks=[{"type": "http.disconnect"}])[0] == 0
    assert request(setup.app, body, path="/v1/events")[0] == 404
    assert request(setup.app, body, method="GET")[0] == 405
    assert setup.agent.calls == 2


@pytest.mark.parametrize("length", [b"bad", b"-1", b"0", b"1" * 21])
def test_invalid_body_length(length: bytes) -> None:
    setup = Setup()
    assert (
        request(setup.app, b"{}", request_headers=headers(b"{}") + [(b"content-length", length)])[0]
        == 400
    )
    assert setup.repository.mapping_calls == 0


def test_duplicate_headers_and_content_type() -> None:
    setup = Setup()
    values = headers(b"{}")
    assert request(setup.app, b"{}", request_headers=values + [values[1]])[0] == 401
    assert request(setup.app, b"{}", request_headers=values + [values[0]])[0] == 415
    assert request(setup.app, b"{}", request_headers=values[1:])[0] == 415
    assert (
        request(setup.app, b"{}", request_headers=values + [(b"content-length", b"2")] * 2)[0]
        == 400
    )


def test_delivery_unknown_is_not_reexecuted(payload: dict[str, Any]) -> None:
    setup = Setup()
    setup.transport.responses.clear()
    setup.transport.responses.append(TimeoutError())
    body = json.dumps(payload).encode()
    first = request(setup.app, body)
    assert first[0] == 200 and first[1]["status"] == "unknown"
    assert request(setup.app, body) == first
    assert len(setup.transport.requests) == 1


def test_lifespan() -> None:
    async def run() -> None:
        messages = deque([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        output: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return messages.popleft()

        async def send(value: dict[str, Any]) -> None:
            output.append(value)

        await Setup().app({"type": "lifespan"}, receive, send)
        assert [x["type"] for x in output] == [
            "lifespan.startup.complete",
            "lifespan.shutdown.complete",
        ]

    asyncio.run(run())


def test_native_ingress_has_no_handoff_transition_grant(payload: dict[str, Any]) -> None:
    class RecordingRuntime:
        def ingest(
            self, event: Event, *, trusted_tenant: str, can_transition: bool = False
        ) -> dict[str, Any]:
            assert trusted_tenant == "tenant_a" and can_transition is False
            return {"status": "recorded"}

    setup = Setup()
    app = create_chatwoot_app(setup.adapter, RecordingRuntime(), setup.verifier)
    assert request(app, json.dumps(payload).encode()) == (200, {"status": "recorded"})


def test_unexpected_runtime_failure_is_sanitized(payload: dict[str, Any]) -> None:
    class BrokenRuntime:
        def ingest(
            self, event: Event, *, trusted_tenant: str, can_transition: bool = False
        ) -> dict[str, Any]:
            raise RuntimeError("Internal credential must never reach response")

    setup = Setup()
    app = create_chatwoot_app(setup.adapter, BrokenRuntime(), setup.verifier)
    assert request(app, json.dumps(payload).encode()) == (500, {"error": "runtime_error"})


@pytest.mark.parametrize(
    "config", [{"path": "relative"}, {"max_body_bytes": 0}, {"max_body_bytes": True}]
)
def test_invalid_ingress_configuration(config: dict[str, Any]) -> None:
    setup = Setup()
    with pytest.raises(ValueError):
        create_chatwoot_app(setup.adapter, setup.runtime, setup.verifier, **config)


@pytest.mark.parametrize("failed_step", [None, 0, 1, 2])
def test_signed_ingress_agentbot_handoff_retains_responsibility(
    payload: dict[str, Any], failed_step: int | None
) -> None:
    class HandoffAgent:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, context: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {
                "intent": "billing",
                "action": {
                    "type": "handoff",
                    "reason": "refund_requires_approval",
                    "summary": "Customer billing request requires human review",
                },
            }

    setup = Setup()
    setup.adapter.handoff_mode = "agent_bot"
    setup.adapter.team_id = 9
    agent = HandoffAgent()
    setup.runtime.agent = agent
    responses: list[HttpResponse | TimeoutError] = [
        HttpResponse(
            200,
            {
                "id": 501,
                "conversation_id": 42,
                "inbox_id": 2,
                "private": True,
                "message_type": 1,
                "content_type": "text",
            },
        ),
        HttpResponse(200, {"id": 9}),
        HttpResponse(
            200,
            {
                "payload": {
                    "conversation_id": 42,
                    "current_status": "open",
                    "success": None,
                }
            },
        ),
    ]
    if failed_step is not None:
        responses[failed_step] = TimeoutError()
    setup.transport.responses = deque(responses)
    body = json.dumps(payload).encode()
    first = request(setup.app, body)
    assert first[0] == 200
    result = first[1]
    assert result["status"] == result["resolution"] == "pending_human"
    assert result["delivery"] == ("sent" if failed_step is None else "unknown")
    assert result["handoff"]["state"] == "requested"
    conversation = setup.repository.get_conversation("tenant_a", result["conversation_id"])
    assert conversation.ai_paused
    count = 3 if failed_step is None else failed_step + 1
    assert len(setup.transport.requests) == count
    assert request(setup.app, body) == first
    # Fresh customer messages are recorded while AI output remains paused.
    payload["id"] += 1
    payload["content"] = "Any update?"
    assert request(setup.app, json.dumps(payload).encode())[1]["status"] == "paused"
    assert agent.calls == 1 and len(setup.transport.requests) == count
    audit = setup.store.list_audit("tenant_a", conversation.id)
    deliveries = [x["data"]["result"] for x in audit if x["kind"] == "handoff_delivery_result"]
    assert len(deliveries) == 1
    assert len(deliveries[0]["actions"]) == count
    assert [x["delivery"] for x in deliveries[0]["actions"]] == (
        ["sent"] * count if failed_step is None else ["sent"] * failed_step + ["unknown"]
    )
    assert not any(x["kind"] in {"handoff_accepted", "handoff_in_progress"} for x in audit)
