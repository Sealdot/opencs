"""Run the formal billing use case with synthetic HTTP and no network dependencies."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

from adapters import (
    ChatwootAdapter,
    FakeTransport,
    GenericHttpToolAdapter,
    HttpResponse,
    OpenAIResponsesAdapter,
    ToolDefinition,
)
from opencs.application.memory_store import MemoryRuntimeStore
from opencs.application.policy import ToolPolicy
from opencs.application.runtime import Runtime
from opencs.domain import Tenant
from opencs.evaluation import evaluate_scenario
from opencs.persistence.memory import MemoryRepository

FAULTS = ("none", "forbidden-tool", "tool-failure", "send-timeout")


def resource(name: str) -> dict[str, Any]:
    value: Any = json.loads(files("opencs.examples").joinpath("data", name).read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Example resource must be an object")
    return value


def response(action: dict[str, Any], intent: str) -> HttpResponse:
    """Explicit scripted Responses envelope; this does not infer intent or call a model."""
    return HttpResponse(
        200,
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"intent": intent, "action": action}),
                        }
                    ],
                }
            ],
        },
    )


def run_billing(*, fault: str = "none") -> dict[str, Any]:
    if fault not in FAULTS:
        raise ValueError("Unknown fault")
    scenario, config = resource("scenario.json"), resource("policy.json")
    tenant = "billing_demo"
    repository = MemoryRepository()
    repository.add_tenant(Tenant(tenant, "Synthetic billing demo"))
    store = MemoryRuntimeStore()
    channel_responses: list[HttpResponse | TimeoutError] = [
        TimeoutError() if fault == "send-timeout" else HttpResponse(200, {"id": 501}),
        HttpResponse(200, {"id": 502}),
        HttpResponse(200, {}),
    ]
    channel_http = FakeTransport(channel_responses)
    channel = ChatwootAdapter(
        base_url="https://chatwoot.invalid",
        account_id=1,
        inbox_id=2,
        tenant_id=tenant,
        token="synthetic-unused-token",
        transport=channel_http,
        repository=repository,
        webhook_secret="synthetic-gateway-secret",
        team_id=9,
    )
    event = channel.normalize(
        resource("chatwoot-message.json"), credential="synthetic-gateway-secret"
    )
    if event is None:
        raise ValueError("Example input was filtered")

    def audit_http(data: dict[str, Any]) -> None:
        store.append_audit(tenant, event.conversation_id, "channel_http_prepared", data)

    channel.before_request = audit_http
    actions: list[dict[str, Any]] = [
        {
            "type": "tool",
            "name": "get_customer",
            "arguments_json": json.dumps({"customer_id": event.customer_id}),
        },
        {
            "type": "tool",
            "name": "get_invoice",
            "arguments_json": json.dumps({"customer_id": event.customer_id}),
        },
        {
            "type": "reply",
            "text": "I will send the investigation context for human refund review.",
        },
        {
            "type": "handoff",
            "reason": config["handoff_reason"],
            "summary": "Human refund review requested",
        },
    ]
    if fault == "forbidden-tool":
        actions.insert(0, {"type": "tool", "name": "issue_refund", "arguments_json": "{}"})
    model_http = FakeTransport(
        [response(action, scenario["expect"]["intent"]) for action in actions]
    )
    tool_http = FakeTransport(
        [
            HttpResponse(200, {"customer_id": event.customer_id, **scenario["given"]["customer"]}),
            HttpResponse(500, {})
            if fault == "tool-failure"
            else HttpResponse(200, scenario["given"]["invoice"]),
        ]
    )
    runtime = Runtime(
        repository=repository,
        store=store,
        agent=OpenAIResponsesAdapter(
            model="synthetic-script", api_key="synthetic-unused-key", transport=model_http
        ),
        channel=channel,
        tools=GenericHttpToolAdapter(
            transport=tool_http,
            registry={
                name: ToolDefinition(f"https://billing.invalid/{name}", "POST", frozenset({tenant}))
                for name in config["allowed_tools"]
            },
        ),
        policy=ToolPolicy(
            frozenset(config["allowed_tools"]),
            frozenset(config["forbidden_tools"]),
            frozenset({tenant}),
        ),
        handoff_target={"type": "human_team", "id": "billing"},
        handoff_reason=config["handoff_reason"],
        pending_resolution=config["expected_resolution"],
    )
    result = runtime.ingest(event, trusted_tenant=tenant)
    audit = store.list_audit(tenant, event.conversation_id)
    counts = (
        len(model_http.requests),
        len(tool_http.requests),
        len(channel_http.requests),
        len(audit),
    )
    replay = runtime.ingest(event, trusted_tenant=tenant)
    after = (
        len(model_http.requests),
        len(tool_http.requests),
        len(channel_http.requests),
        len(store.list_audit(tenant, event.conversation_id)),
    )
    evaluated = evaluate_scenario(
        scenario,
        audit,
        repository.get_conversation(tenant, event.conversation_id).resolution,
        tenant_id=tenant,
        conversation_id=event.conversation_id,
        run_id=f"formal-billing-{fault}",
    )
    request_log = [
        {
            "method": request.method,
            "url": request.url,
            "body": deepcopy(request.json_body),
        }
        for request in channel_http.requests
    ]
    return {
        **evaluated,
        "fault": fault,
        "result": result,
        "audit": audit,
        "replay_unchanged": counts == after and replay == result,
        "synthetic_http_requests": {"model": counts[0], "tools": counts[1], "channel": counts[2]},
        "channel_requests": request_log,
        "provider_calls": 0,
        "model_inference": False,
        "persistence": "process_local_memory",
        "live_integration": "live_not_run",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=FAULTS, default="none")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_billing(fault=args.fault)
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "passed",
                    "fault",
                    "replay_unchanged",
                    "provider_calls",
                    "model_inference",
                    "live_integration",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] and report["replay_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
