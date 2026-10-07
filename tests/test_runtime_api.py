"""In-process ASGI tests; no socket, ASGI server or framework dependencies."""

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from opencs.api import AccessGrant, ASGIApp, create_app
from opencs.application.memory_store import MemoryRuntimeStore
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Conversation, Customer, Event, Tenant
from opencs.persistence.memory import MemoryRepository


class DoneAgent:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"intent": "billing", "action": {"type": "done"}}


class NoActions:
    def send(
        self, *, tenant_id: str, conversation_id: str, text: str, action_id: str
    ) -> dict[str, Any]:
        raise AssertionError("Unexpected output")

    def handoff(
        self, *, tenant_id: str, conversation_id: str, snapshot: dict[str, Any], action_id: str
    ) -> dict[str, Any]:
        raise AssertionError("Unexpected handoff")

    def execute(
        self, name: str, arguments: dict[str, Any], *, tenant_id: str, action_id: str
    ) -> dict[str, Any]:
        raise AssertionError("Unexpected tool")


@pytest.fixture
def payload() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(Path("tests/fixtures/event.json").read_text())
    return value


@pytest.fixture
def setup() -> tuple[ASGIApp, DoneAgent, MemoryRuntimeStore]:
    repository = MemoryRepository()
    repository.add_tenant(Tenant("tenant_acme"))
    repository.add_customer(Customer("tenant_acme", "cus_01"))
    repository.add_conversation(Conversation("tenant_acme", "conv_01", "cus_01"))
    agent = DoneAgent()
    store = MemoryRuntimeStore()
    runtime = Runtime(
        repository=repository,
        store=store,
        agent=agent,
        channel=NoActions(),
        tools=NoActions(),
        policy=ToolPolicy(frozenset(), frozenset(), frozenset({"tenant_acme"})),
        handoff_target={"type": "human_queue", "id": "billing"},
        handoff_reason="refund_requires_approval",
        pending_resolution="pending_human",
    )
    return create_app(runtime, {"test-token": AccessGrant("tenant_acme")}), agent, store


def request(
    app: ASGIApp,
    body: bytes = b"",
    *,
    path: str = "/v1/events",
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
    chunks: list[dict[str, Any]] | None = None,
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
                "headers": headers
                if headers is not None
                else [
                    (b"authorization", b"Bearer test-token"),
                    (b"content-type", b"application/json"),
                ],
            },
            receive,
            send,
        )
        assert len(outgoing) == 2
        assert outgoing[0]["type"] == "http.response.start"
        assert outgoing[1]["type"] == "http.response.body"
        assert (b"cache-control", b"no-store") in outgoing[0]["headers"]
        return outgoing[0]["status"], json.loads(outgoing[1]["body"])

    return asyncio.run(run())


def test_api_ingests_and_replays_once(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore], payload: dict[str, Any]
) -> None:
    app, agent, _ = setup
    body = json.dumps(payload).encode()
    first = request(app, body)
    assert first[0] == 200 and first[1]["status"] == "completed"
    assert request(app, body) == first
    assert agent.calls == 1
    payload["event"]["content"] = "changed"
    assert request(app, json.dumps(payload).encode()) == (409, {"error": "event_replay_conflict"})
    assert agent.calls == 1


@pytest.mark.parametrize("auth", [None, b"Basic test-token", b"Bearer unknown", b"Bearer", b"\xff"])
def test_api_authentication(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore],
    auth: bytes | None,
    payload: dict[str, Any],
) -> None:
    headers = [(b"content-type", b"application/json")]
    if auth is not None:
        headers.append((b"authorization", auth))
    assert request(setup[0], json.dumps(payload).encode(), headers=headers)[0] == 401
    assert setup[1].calls == 0


def test_api_duplicate_auth_rejected(setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore]) -> None:
    assert request(setup[0], headers=[(b"authorization", b"Bearer test-token")] * 2)[0] == 401


def test_api_tenant_spoof_and_unknown_subject(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore], payload: dict[str, Any]
) -> None:
    payload["tenant_id"] = "tenant_other"
    assert request(setup[0], json.dumps(payload).encode())[0] == 403
    payload["tenant_id"] = "tenant_acme"
    payload["conversation_id"] = "absent"
    assert request(setup[0], json.dumps(payload).encode())[0] == 404
    assert setup[1].calls == 0


def test_api_normal_grant_cannot_change_handoff(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore],
) -> None:
    value = Path("tests/fixtures/handoff-event.json").read_bytes()
    assert request(setup[0], value) == (403, {"error": "event_forbidden"})
    assert not setup[2].list_audit("tenant_acme", "conv_01")


@pytest.mark.parametrize(
    "body,code",
    [
        (b"{", 400),
        (b"\xff", 400),
        (b'{"x":NaN}', 400),
        (b'{"x":Infinity}', 400),
        (b'{"x":1,"x":2}', 400),
        (b"[]", 422),
        (b"{}", 422),
    ],
)
def test_api_invalid_json_or_event(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore], body: bytes, code: int
) -> None:
    assert request(setup[0], body)[0] == code
    assert setup[1].calls == 0


def test_api_nonfinite_exponent_rejected(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore], payload: dict[str, Any]
) -> None:
    payload["metadata"]["x"] = "HUGE_NUMBER"
    body = json.dumps(payload).replace('"HUGE_NUMBER"', "1e99999").encode()
    assert request(setup[0], body)[0] == 422


@pytest.mark.parametrize(
    "path,status", [("/health/live", "alive"), ("/health/ready", "ready_for_offline")]
)
def test_health_is_explicitly_offline(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore], path: str, status: str
) -> None:
    code, body = request(setup[0], path=path, method="GET", headers=[])
    assert code == 200
    assert body == {
        "status": status,
        "mode": "offline",
        "storage": "inmemory",
        "durable": False,
        "live_integrations": "not_run",
    }


def test_api_route_method_and_content_type(
    setup: tuple[ASGIApp, DoneAgent, MemoryRuntimeStore],
) -> None:
    app = setup[0]
    assert request(app, path="/unknown")[0] == 404
    assert request(app, method="GET")[0] == 405
    assert request(app, path="/health/ready")[0] == 405
    assert request(app, headers=[(b"authorization", b"Bearer test-token")])[0] == 415


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[Event, str, bool]] = []

    def ingest(
        self, event: Event, *, trusted_tenant: str, can_transition: bool = False
    ) -> dict[str, Any]:
        self.calls.append((event, trusted_tenant, can_transition))
        return {"status": "accepted_for_test"}


def test_transition_grant_comes_only_from_configuration(payload: dict[str, Any]) -> None:
    runtime = RecordingRuntime()
    credentials = {"test-token": AccessGrant("tenant_acme", can_transition=True)}
    app = create_app(runtime, credentials)
    credentials["test-token"] = AccessGrant("other")
    payload["metadata"]["can_transition"] = False
    assert request(app, json.dumps(payload).encode())[0] == 200
    assert runtime.calls[0][1:] == ("tenant_acme", True)


@pytest.mark.parametrize("declared", [None, b"11"])
def test_body_limit_uses_actual_chunks(declared: bytes | None) -> None:
    runtime = RecordingRuntime()
    app = create_app(runtime, {"test-token": AccessGrant("tenant_acme")}, max_body_bytes=10)
    headers = [(b"authorization", b"Bearer test-token"), (b"content-type", b"application/json")]
    if declared:
        headers.append((b"content-length", declared))
    assert (
        request(
            app,
            headers=headers,
            chunks=[
                {"type": "http.request", "body": b"123456", "more_body": True},
                {"type": "http.request", "body": b"12345"},
            ],
        )[0]
        == 413
    )
    assert not runtime.calls


def test_chunked_body_equal_limit_is_accepted(payload: dict[str, Any]) -> None:
    runtime = RecordingRuntime()
    body = json.dumps(payload).encode()
    app = create_app(runtime, {"test-token": AccessGrant("tenant_acme")}, max_body_bytes=len(body))
    assert (
        request(
            app,
            chunks=[
                {"type": "http.request", "body": body[:15], "more_body": True},
                {"type": "http.request", "body": body[15:]},
            ],
        )[0]
        == 200
    )
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("length", [b"bad", b"-1", b"0"])
def test_invalid_content_length(length: bytes) -> None:
    runtime = RecordingRuntime()
    app = create_app(runtime, {"test-token": AccessGrant("tenant_acme")})
    assert (
        request(
            app,
            b"{}",
            headers=[
                (b"authorization", b"Bearer test-token"),
                (b"content-type", b"application/json"),
                (b"content-length", length),
            ],
        )[0]
        == 400
    )
    assert not runtime.calls


def test_disconnect_does_not_ingest() -> None:
    runtime = RecordingRuntime()
    app = create_app(runtime, {"test-token": AccessGrant("tenant_acme")})

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(value: dict[str, Any]) -> None:
            raise AssertionError("Must not respond after disconnect")

        await app(
            {
                "type": "http",
                "path": "/v1/events",
                "method": "POST",
                "headers": [
                    (b"authorization", b"Bearer test-token"),
                    (b"content-type", b"application/json"),
                ],
            },
            receive,
            send,
        )

    asyncio.run(run())
    assert not runtime.calls


def test_asgi_lifespan() -> None:
    app = create_app(RecordingRuntime(), {})

    async def run() -> None:
        messages = deque([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        output: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return messages.popleft()

        async def send(value: dict[str, Any]) -> None:
            output.append(value)

        await app({"type": "lifespan"}, receive, send)
        assert output == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    asyncio.run(run())
