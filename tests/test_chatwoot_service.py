"""Real loopback HTTP, synthetic provider responses: no live integration evidence."""

import hashlib
import hmac
import http.client
import json
import socket
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import pytest

from opencs.api.devserver import create_dev_server
from opencs.examples.billing import resource, response
from opencs.examples.chatwoot_service import Service, ServiceConfig, build_service, main


def config(base: str = "https://provider.invalid") -> ServiceConfig:
    return ServiceConfig(
        tenant_id="test_tenant",
        chatwoot_base_url=base,
        account_id=1,
        inbox_id=2,
        team_id=9,
        chatwoot_token="synthetic-chatwoot-token",
        signing_secret="synthetic-signing-secret",
        openai_model="synthetic-model",
        openai_api_key="synthetic-model-key",
        customer_url=base + "/customer",
        invoice_url=base + "/invoice",
        openai_base_url=base + "/v1",
        handoff_mode="agent_bot",
        allow_http=base.startswith("http:"),
        timeout_seconds=1.0,
    )


def model(action: dict[str, Any]) -> dict[str, Any]:
    return response(action, "duplicate_charge").body


def actions() -> list[dict[str, Any]]:
    return [
        {"type": "tool", "name": "get_customer", "arguments_json": "{}"},
        {"type": "tool", "name": "get_invoice", "arguments_json": "{}"},
        {"type": "reply", "text": "交给人工核对退款。"},
        {"type": "handoff", "reason": "refund_requires_approval", "summary": "待人工审批"},
    ]


class Provider:
    def __init__(self, steps: list[tuple[str, int, dict[str, Any] | bytes | None]]) -> None:
        self.steps = deque(steps)
        self.calls: list[dict[str, Any]] = []
        self.errors: list[str] = []

    @contextmanager
    def run(self) -> Iterator[str]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append({"path": self.path, "body": body, "headers": dict(self.headers)})
                if not owner.steps:
                    owner.errors.append("Unexpected request")
                    self.send_error(500)
                    return
                path, status, value = owner.steps.popleft()
                if path != self.path:
                    owner.errors.append(f"Expected {path}, received {self.path}")
                if value is None:
                    self.close_connection = True
                    return
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                thread.join(timeout=2)
        assert not self.errors


@contextmanager
def listening(service: Service) -> Iterator[int]:
    with create_dev_server(service.app, port=0) as server:
        thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=2)


def request(
    port: int,
    payload: dict[str, Any] | None = None,
    *,
    valid: bool = True,
    path: str = "/webhooks/chatwoot",
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    raw = json.dumps(payload, ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"synthetic-signing-secret", timestamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    try:
        connection.request(
            "POST" if payload is not None else "GET",
            path,
            body=raw if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Chatwoot-Timestamp": timestamp,
                "X-Chatwoot-Signature": "sha256=" + (digest if valid else "0" * 64),
            },
        )
        result = connection.getresponse()
        return result.status, json.loads(result.read())
    finally:
        connection.close()


TARGET = "/api/v1/accounts/1/conversations/42/"


def test_signed_socket_roundtrip_tools_reply_handoff_replay_and_pause() -> None:
    sequence = actions()
    provider = Provider(
        [
            ("/v1/responses", 200, model(sequence[0])),
            ("/customer", 200, {"customer": "synthetic"}),
            ("/v1/responses", 200, model(sequence[1])),
            ("/invoice", 200, {"duplicate": True}),
            ("/v1/responses", 200, model(sequence[2])),
            (TARGET + "messages", 200, {"id": 500}),
            ("/v1/responses", 200, model(sequence[3])),
            (
                TARGET + "messages",
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
            (TARGET + "assignments", 200, {"id": 9}),
            (
                TARGET + "toggle_status",
                200,
                {"payload": {"conversation_id": 42, "current_status": "open", "success": None}},
            ),
        ]
    )
    with provider.run() as base:
        service = build_service(config(base))
        audit_hook = service.channel.before_request
        prepared: list[dict[str, Any]] = []

        def audit(data: dict[str, Any]) -> None:
            assert audit_hook is not None
            audit_hook(data)
            rows = service.store.list_audit("test_tenant", data["conversation_id"])
            assert rows[-1]["kind"] == "channel_http_prepared"
            assert rows[-1]["data"] == data
            assert len([c for c in provider.calls if c["path"].startswith(TARGET)]) == len(prepared)
            prepared.append(data)

        service.channel.before_request = audit
        with listening(service) as port:
            payload = resource("chatwoot-message.json")
            assert request(port, payload, valid=False)[0] == 401
            assert not provider.calls
            status, result = request(port, payload)
            assert status == 200 and result["status"] == "pending_human"
            before = len(provider.calls)
            assert request(port, payload) == (status, result)
            assert len(provider.calls) == before == 10
            payload["id"] += 1
            assert request(port, payload)[1]["status"] == "paused"
            assert len(provider.calls) == before
            assert request(port)[0] == 405
            assert request(port, path="/health/live")[1]["durable"] is False
            assert request(port, path="/health/ready")[0] == 503
            assert request(port, path="/v1/events")[0] == 404
        assert not provider.steps
        conversation_id = prepared[0]["conversation_id"]
        assert all(p["conversation_id"] == conversation_id for p in prepared)
        handoff = service.store.get_handoff("test_tenant", conversation_id)
        assert handoff is not None and handoff["state"] == "requested"
        audit_text = json.dumps(service.store.list_audit("test_tenant", conversation_id))
        for secret in (
            "synthetic-chatwoot-token",
            "synthetic-model-key",
            "synthetic-signing-secret",
        ):
            assert secret not in audit_text
        assert all(
            c["headers"]["api_access_token"] == "synthetic-chatwoot-token"
            for c in provider.calls
            if c["path"].startswith(TARGET)
        )
        assert provider.calls[-1]["body"] == {"status": "open"}
        assert provider.calls[-3]["body"]["private"] is True
        assert provider.calls[1]["headers"]["X-OpenCS-Tenant"] == "test_tenant"


@pytest.mark.parametrize("malformed", [b"not-json", None])
def test_uncertain_tool_stops_and_replay_does_not_repeat(malformed: bytes | None) -> None:
    provider = Provider(
        [("/v1/responses", 200, model(actions()[0])), ("/customer", 200, malformed)]
    )
    with provider.run() as base:
        with listening(build_service(config(base))) as port:
            payload = resource("chatwoot-message.json")
            status, result = request(port, payload)
            assert status == 200 and result["status"] == "unknown"
            assert request(port, payload) == (status, result)
            assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "status,body,delivery", [(200, b"bad-json", "unknown"), (429, b"provider secret", "failed")]
)
def test_channel_failure_preserved_without_repeat(status: int, body: bytes, delivery: str) -> None:
    provider = Provider(
        [("/v1/responses", 200, model(actions()[2])), (TARGET + "messages", status, body)]
    )
    with provider.run() as base:
        with listening(build_service(config(base))) as port:
            payload = resource("chatwoot-message.json")
            result = request(port, payload)
            assert result[0] == 200 and result[1]["status"] == delivery
            assert request(port, payload) == result
            assert len(provider.calls) == 2
            assert "provider secret" not in json.dumps(result)


def test_forbidden_tool_never_reaches_network() -> None:
    provider = Provider(
        [
            (
                "/v1/responses",
                200,
                model({"type": "tool", "name": "issue_refund", "arguments_json": "{}"}),
            ),
            ("/v1/responses", 200, model({"type": "done"})),
        ]
    )
    with provider.run() as base:
        with listening(build_service(config(base))) as port:
            assert request(port, resource("chatwoot-message.json"))[1]["status"] == "completed"
        assert [c["path"] for c in provider.calls] == ["/v1/responses", "/v1/responses"]
        assert not provider.steps


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", True),
        ("team_id", 0),
        ("tenant_id", "bad\r\ntenant"),
        ("chatwoot_token", "leaked\ntoken"),
        ("signing_secret", ""),
        ("openai_api_key", " "),
        ("openai_model", ""),
        ("handoff_mode", "unsupported"),
        ("allow_http", 1),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", 0),
        ("chatwoot_base_url", "http://provider.invalid"),
        ("customer_url", "https://secret@provider.invalid/customer"),
        ("invoice_url", "https://provider.invalid:99999/invoice"),
        ("openai_base_url", "https://provider.invalid/?token=secret"),
        ("customer_url", "https://provider.invalid/#"),
    ],
)
def test_invalid_configuration_does_not_echo_values(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match=f"^Invalid configuration: {field}$"):
        replace(config(), **{field: value})


def test_config_check_and_composition_have_no_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No network or listener expected")

    monkeypatch.setattr(socket, "socket", forbidden)
    env = {
        "OPENCS_TENANT_ID": "test_tenant",
        "CHATWOOT_BASE_URL": "https://provider.invalid",
        "CHATWOOT_ACCOUNT_ID": "1",
        "CHATWOOT_INBOX_ID": "2",
        "CHATWOOT_TEAM_ID": "9",
        "CHATWOOT_API_TOKEN": "synthetic-chatwoot-token",
        "CHATWOOT_SIGNING_SECRET": "synthetic-signing-secret",
        "OPENAI_MODEL": "synthetic-model",
        "OPENAI_API_KEY": "synthetic-model-key",
        "BILLING_CUSTOMER_URL": "https://billing.invalid/customer",
        "BILLING_INVOICE_URL": "https://billing.invalid/invoice",
    }
    settings = ServiceConfig.from_env(env)
    build_service(settings)
    assert "synthetic-chatwoot-token" not in repr(settings)
    assert "synthetic-signing-secret" not in repr(settings)
    assert "synthetic-model-key" not in repr(settings)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in (
        "OPENCS_ALLOW_HTTP",
        "OPENCS_HTTP_TIMEOUT_SECONDS",
        "OPENAI_BASE_URL",
        "CHATWOOT_HANDOFF_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["chatwoot_service", "--check"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["network_calls"] == 0
    monkeypatch.delenv("CHATWOOT_API_TOKEN")
    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "Missing configuration: CHATWOOT_API_TOKEN"
    }
    for name, value in [
        ("OPENCS_ALLOW_HTTP", "true"),
        ("CHATWOOT_ACCOUNT_ID", "secret"),
        ("OPENCS_HTTP_TIMEOUT_SECONDS", "secret"),
    ]:
        with pytest.raises(ValueError, match=f"^Invalid configuration: {name}$"):
            ServiceConfig.from_env({**env, name: value})
