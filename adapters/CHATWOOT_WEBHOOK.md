# Chatwoot signed webhook ingress

`create_chatwoot_app(adapter, runtime, verifier)` exposes `POST /webhooks/chatwoot`
as a standard ASGI callable. It verifies original request bytes, normalizes the
message through the configured Adapter, and invokes `Runtime.ingest`. It does not
start a server or provide a durable queue. Existing `/v1/events` and
`ChatwootAdapter.normalize(payload, credential=...)` interfaces remain unchanged.

## Composition

```python
import os

from adapters import ChatwootAdapter, ChatwootWebhookVerifier
from adapters.chatwoot_webhook import create_chatwoot_app

# Supply the same repository to the adapter and runtime.
adapter = ChatwootAdapter(
    base_url=os.environ["CHATWOOT_BASE_URL"],
    account_id=int(os.environ["CHATWOOT_ACCOUNT_ID"]),
    inbox_id=int(os.environ["CHATWOOT_INBOX_ID"]),
    tenant_id=os.environ["OPENCS_TENANT_ID"],
    token=os.environ["CHATWOOT_API_TOKEN"],
    transport=transport,
    repository=repository,
    webhook_secret=os.environ["OPENCS_GATEWAY_CREDENTIAL"],
)
# Assemble Runtime with channel=adapter here, using the existing application ports.
verifier = ChatwootWebhookVerifier(os.environ["CHATWOOT_SIGNING_SECRET"])
app = create_chatwoot_app(adapter, runtime, verifier)
```

Serve or mount this callable with your ASGI server. The public proxy must preserve
body bytes and authentication headers and enforce request deadlines. One app
binds one instance/account/inbox/tenant; mount separate configured apps for other
bindings. Configure the source to send `message_created` events. No HTTP payload
or header can select another tenant, signing secret or handoff transition grant.

`OPENCS_GATEWAY_CREDENTIAL` is the existing legacy integration credential;
`CHATWOOT_SIGNING_SECRET` is the native webhook secret. They have different roles.
The signed route never accepts a bearer/gateway credential as a signature fallback.

## Upstream contract

Source baseline: **Chatwoot v4.17.1**, inspected 2026-09-22.

- [Trigger](https://github.com/chatwoot/chatwoot/blob/v4.17.1/lib/webhooks/trigger.rb):
  signature is `sha256=` plus HMAC-SHA256 of `{timestamp}.{raw_body}` using the
  `X-Chatwoot-Timestamp` header. Parse JSON only after verification.
- [WebhookListener](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/listeners/webhook_listener.rb):
  account webhooks use `webhook.secret`; API inbox webhooks use `inbox.channel.secret`.
- [AgentBotListener](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/listeners/agent_bot_listener.rb):
  AgentBot webhooks use `agent_bot.secret`.

These secrets are not the API token or contact identity-validation HMAC token.
Older deployments may differ; absent signing headers are rejected. Timestamp and
signature must each occur exactly once. Delivery IDs are outside the signed input
and are not trusted as identity or deduplication keys.

## Verification and responses

Default maximum age is 300 seconds and future clock skew is 30 seconds, both
configurable nonnegative integer seconds. These are receiver defaults to bound
replay exposure while allowing clock skew, not an upstream guarantee or a new
customer-service policy. Synchronize clocks. Tests inject a clock.

A valid replay within the window still uses Runtime's stable normalized event ID;
new delivery timestamps/IDs do not cause a second reply. Changed normalized content
for the same event ID produces a conflict audit and HTTP 409. The default 1 MiB
body limit also applies to chunked requests without Content-Length. Duplicate
authentication headers, duplicate JSON keys, invalid UTF-8, non-finite numbers and
incomplete bodies are rejected before mapping. Filtered events never call the
Agent. Native webhook events cannot authorize human lifecycle transitions.

| HTTP | Meaning |
|---|---|
| 200 | Stored Runtime outcome, including failed/unknown; or `status=ignored` |
| 400 | Malformed JSON/UTF-8, invalid framing or mismatched Content-Length |
| 401 | Missing, ambiguous, invalid, stale or future authentication |
| 403 | Runtime rejects authority |
| 404 / 405 | Wrong path / method |
| 409 | Same normalized event ID with conflicting content |
| 413 / 415 | Body too large / unsupported content type |
| 422 | Invalid shape, duplicate keys, non-finite numbers or account/inbox binding |
| 500 | Unexpected failure; generic response without internal details |

The endpoint waits for Runtime completion; it does not acknowledge an event as
durably queued. Production ingress needs a durable inbox and worker before slow
model/tool calls can safely outlive the provider's webhook timeout. An HTTP retry
must not blindly repeat an uncertain external action.

## Evidence and remaining work

```sh
UV_OFFLINE=1 uv run --no-sync pytest -q tests/test_chatwoot_webhook.py tests/test_runtime_adapters.py tests/test_runtime_api.py
```

Tests connect signed request bytes through ASGI to the actual in-memory repository,
Runtime, policy and Chatwoot Adapter, with synthetic Agent and outbound responses.
They verify one reply on replay, conflicts, authentication before side effects,
malformed inputs and uncertain delivery. They do not prove a live Chatwoot service,
Internet ingress/TLS, real LLM, durable replay, deployment or human acceptance.
AgentBot queue opening and assignment now have a separate, explicit
[handoff delivery mode](CHATWOOT_HANDOFF.md). Its provider contract is tested
offline; actual bot permissions, pending queue behavior and human acceptance still
require a real integration environment.

For explicit real HTTP transport, environment configuration and the local listener,
see the [billing integration service](HTTP_SERVICE.md). Its loopback tests still
use simulated providers and do not change the live integration gates above.
