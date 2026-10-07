# Offline adapter contracts

Status: **offline_passed / live_not_run**. These modules use only the standard
library and the OpenCS package. They contain no real HTTP transport, SDK, environment
key discovery or retry loop. Construct every adapter with an explicit transport;
`FakeTransport` records requests and consumes queued `HttpResponse` or `TimeoutError`.
Exhausting its queue raises an error and never falls back to network access.

- `ChatwootAdapter`: validates an integration credential, account and inbox; maps
  incoming public text via the repository; sends messages and private context notes,
  then requests assignment to a configured team. Provider IDs are scoped by instance
  URL and account. Target mappings are recorded only from trusted normalization,
  never accepted from event metadata or model decisions. Tokens are excluded from
  request repr and audit results. Private/outgoing/non-text/attachment events are ignored.
- `OpenAIResponsesAdapter`: requires an explicit model/key; maps a single strict
  structured decision through Responses. Invalid JSON, schema drift, refusal and
  incomplete output fail closed. Tool arguments use `arguments_json` on the wire
  to support arbitrary argument objects under a strict schema; Core receives the
  parsed `arguments` dictionary. The model cannot execute tools through this adapter.
- `GenericHttpToolAdapter`: static name → HTTP URL/method/tenant registry. The
  configured endpoint receives `{"arguments": {...}}` plus trusted tenant/action
  headers. This is OpenCS's minimal HTTP tool contract; vendor APIs need explicit
  mapping. Model-supplied URLs cannot alter destinations. The Core must additionally
  enforce policy and persist its pre-action audit before calling the adapter.

Channel results report `delivery=sent|failed|unknown`; they never claim human
acceptance. A message success requires a positive provider message ID. A malformed
success or timeout is unknown and must be reconciled before an explicit retry.
Non-2xx HTTP results fail without retry. Tool/LLM HTTP errors raise `AdapterError`;
timeouts propagate `TimeoutError`. No automatic redirects or SDK retry behavior exists.

Handoff defaults to two stable child action IDs (`:context`, `:assignment`). Explicit
`handoff_mode="agent_bot"` adds `:open` after team assignment, with stricter response
validation and an actual AgentBot token as a deployment precondition. See the
[delivery and failure contract](CHATWOOT_HANDOFF.md) for source evidence and limits.
Same-action replays return the recorded result, including failed/unknown outcomes,
without repeating requests in the same adapter instance. Conflicting context or
targets are rejected. This process-local record does not implement durable recovery.
The optional
`before_request` hook runs before each request with safe action/tenant/URL/operation
facts. Inject the runtime audit sink for application use; callback failure prevents
the request. Returned `actions` record each result. If the private note fails or its
result is unknown, assignment is not attempted. An assignment HTTP response records
request delivery only; it does not generate Accepted/InProgress events.

The original credential check models a trusted gateway/integration credential.
The separate [native signed ingress](CHATWOOT_WEBHOOK.md) verifies Chatwoot HMAC
before normalization and is covered by in-process integration tests. Actual signing
secret and permission configuration must be verified in a real environment. In-memory target
bindings require normalization again after restart; they are not durable persistence.
No provider reconciliation, media transport, real LLM quality or network recovery is
proved by these tests. Synthetic tokens/IDs in tests are not real credentials.

Evidence and source contracts:

- [Chatwoot v4.17.1 message webhook source](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/models/message.rb)
- [Chatwoot create message API](https://developers.chatwoot.com/api-reference/messages/create-new-message)
- [Chatwoot assignment API](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Run `UV_OFFLINE=1 uv run --no-sync pytest tests/test_runtime_adapters.py` for the
synthetic adapter integration suite. It covers trusted tenant binding, inbound
filters, response mapping/schema changes, handoff delivery separation, static tool
allowlists, 401/403/429/500, refusals and no-retry timeouts.
