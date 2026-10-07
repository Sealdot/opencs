"""Billing integration preview with real HTTP and process-local state.

Configuration is read only when explicitly constructing the service or running
its CLI. Importing this module never binds a listener or calls a provider.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from adapters.chatwoot import ChatwootAdapter, checked_url
from adapters.chatwoot_signing import ChatwootWebhookVerifier
from adapters.chatwoot_webhook import create_chatwoot_app
from adapters.http_tools import GenericHttpToolAdapter, ToolDefinition
from adapters.openai import OpenAIResponsesAdapter
from adapters.stdlib_http import StdlibHttpTransport
from opencs.api.app import ASGIApp, ASGIMessage, Receive, Send
from opencs.application.memory_store import MemoryRuntimeStore
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Tenant
from opencs.examples.billing import resource
from opencs.persistence.memory import MemoryRepository


@dataclass(frozen=True)
class ServiceConfig:
    tenant_id: str
    chatwoot_base_url: str
    account_id: int
    inbox_id: int
    team_id: int
    chatwoot_token: str = field(repr=False)
    signing_secret: str = field(repr=False)
    openai_model: str
    openai_api_key: str = field(repr=False)
    customer_url: str
    invoice_url: str
    handoff_mode: Literal["assignment", "agent_bot"] = "assignment"
    openai_base_url: str = "https://api.openai.com/v1"
    allow_http: bool = False
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        for name in ("tenant_id", "chatwoot_token", "openai_api_key"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or any(not 33 <= ord(char) <= 126 for char in value)
            ):
                raise ValueError(f"Invalid configuration: {name}")
        for name in ("signing_secret", "openai_model"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError(f"Invalid configuration: {name}")
        for name in ("account_id", "inbox_id", "team_id"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"Invalid configuration: {name}")
        if self.handoff_mode not in ("assignment", "agent_bot"):
            raise ValueError("Invalid configuration: handoff_mode")
        if type(self.allow_http) is not bool:
            raise ValueError("Invalid configuration: allow_http")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Invalid configuration: timeout_seconds")
        for name in ("chatwoot_base_url", "openai_base_url", "customer_url", "invoice_url"):
            value = getattr(self, name)
            try:
                if not isinstance(value, str) or any(not 33 <= ord(c) <= 126 for c in value):
                    raise ValueError
                checked_url(value)
                parts = urlsplit(value)
                if (
                    not parts.hostname
                    or parts.port == 0
                    or parts.username is not None
                    or "?" in value
                    or "#" in value
                    or (parts.scheme == "http" and not self.allow_http)
                ):
                    raise ValueError
            except ValueError:
                raise ValueError(f"Invalid configuration: {name}") from None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceConfig:
        values = os.environ if env is None else env

        def required(name: str) -> str:
            value = values.get(name, "")
            if not value.strip():
                raise ValueError(f"Missing configuration: {name}")
            return value

        def identifier(name: str) -> int:
            value = required(name)
            if not value.isascii() or not value.isdigit() or len(value) > 20:
                raise ValueError(f"Invalid configuration: {name}")
            return int(value)

        allow_http = values.get("OPENCS_ALLOW_HTTP", "0")
        if allow_http not in ("0", "1"):
            raise ValueError("Invalid configuration: OPENCS_ALLOW_HTTP")
        try:
            timeout = float(values.get("OPENCS_HTTP_TIMEOUT_SECONDS", "10"))
        except ValueError:
            raise ValueError("Invalid configuration: OPENCS_HTTP_TIMEOUT_SECONDS") from None
        return cls(
            tenant_id=required("OPENCS_TENANT_ID"),
            chatwoot_base_url=required("CHATWOOT_BASE_URL"),
            account_id=identifier("CHATWOOT_ACCOUNT_ID"),
            inbox_id=identifier("CHATWOOT_INBOX_ID"),
            team_id=identifier("CHATWOOT_TEAM_ID"),
            chatwoot_token=required("CHATWOOT_API_TOKEN"),
            signing_secret=required("CHATWOOT_SIGNING_SECRET"),
            openai_model=required("OPENAI_MODEL"),
            openai_api_key=required("OPENAI_API_KEY"),
            customer_url=required("BILLING_CUSTOMER_URL"),
            invoice_url=required("BILLING_INVOICE_URL"),
            openai_base_url=values.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            handoff_mode=cast(
                Literal["assignment", "agent_bot"],
                values.get("CHATWOOT_HANDOFF_MODE", "assignment"),
            ),
            allow_http=allow_http == "1",
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class Service:
    app: ASGIApp
    runtime: Runtime
    repository: MemoryRepository
    store: MemoryRuntimeStore
    channel: ChatwootAdapter


def build_service(config: ServiceConfig) -> Service:
    """Compose one trusted tenant/account/inbox; construction performs no I/O."""
    tenant = config.tenant_id
    repository, store = MemoryRepository(), MemoryRuntimeStore()
    repository.add_tenant(Tenant(tenant, "Billing integration preview"))
    transport = StdlibHttpTransport(
        timeout_seconds=config.timeout_seconds, allow_http=config.allow_http
    )

    def audit_http(data: dict[str, Any]) -> None:
        store.append_audit(tenant, data["conversation_id"], "channel_http_prepared", data)

    channel = ChatwootAdapter(
        base_url=config.chatwoot_base_url,
        account_id=config.account_id,
        inbox_id=config.inbox_id,
        tenant_id=tenant,
        token=config.chatwoot_token,
        transport=transport,
        repository=repository,
        # Legacy normalize() is not exposed by this service.
        webhook_secret=secrets.token_urlsafe(32),
        team_id=config.team_id,
        handoff_mode=config.handoff_mode,
        before_request=audit_http,
    )
    policy = resource("policy.json")
    runtime = Runtime(
        repository=repository,
        store=store,
        agent=OpenAIResponsesAdapter(
            model=config.openai_model,
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            transport=transport,
        ),
        channel=channel,
        tools=GenericHttpToolAdapter(
            transport=transport,
            registry={
                name: ToolDefinition(url, "POST", frozenset({tenant}))
                for name, url in {
                    "get_customer": config.customer_url,
                    "get_invoice": config.invoice_url,
                }.items()
            },
        ),
        policy=ToolPolicy(
            frozenset(policy["allowed_tools"]),
            frozenset(policy["forbidden_tools"]),
            frozenset({tenant}),
        ),
        handoff_target={"type": "human_team", "id": str(config.team_id)},
        handoff_reason=policy["handoff_reason"],
        pending_resolution=policy["expected_resolution"],
    )
    webhook = create_chatwoot_app(channel, runtime, ChatwootWebhookVerifier(config.signing_secret))

    async def app(scope: ASGIMessage, receive: Receive, send: Send) -> None:
        path = scope.get("path")
        if scope["type"] == "http" and path in ("/health/live", "/health/ready"):
            status = 200 if path == "/health/live" else 503
            result = {
                "status": "alive" if status == 200 else "not_production_ready",
                "mode": "integration_preview",
                "storage": "process_local_memory",
                "durable": False,
            }
            if scope.get("method") != "GET":
                status, result = 405, {"error": "method_not_allowed"}
            body = json.dumps(result).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await webhook(scope, receive, send)

    return Service(app, runtime, repository, store, channel)


def create_app() -> ASGIApp:
    """Explicit factory for a separately supplied ASGI server (single worker only)."""
    return build_service(ServiceConfig.from_env()).app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate config without network calls"
    )
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        config = ServiceConfig.from_env()
    except ValueError as error:
        print(json.dumps({"error": str(error)}))
        return 2
    if args.check:
        print(
            json.dumps(
                {
                    "configuration": "valid",
                    "network_calls": 0,
                    "mode": "integration_preview",
                    "durable": False,
                }
            )
        )
        return 0
    from opencs.api.devserver import create_dev_server

    try:
        server = create_dev_server(build_service(config).app, host=args.host, port=args.port)
    except (OSError, ValueError):
        print(json.dumps({"error": "listener_start_failed"}))
        return 2
    print(
        json.dumps(
            {
                "listening": list(server.server_address),
                "mode": "integration_preview",
                "storage": "process_local_memory",
                "durable": False,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
