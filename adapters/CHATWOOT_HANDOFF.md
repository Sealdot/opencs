# Chatwoot handoff delivery

The adapter supports two explicit modes. Existing callers default to
`handoff_mode="assignment"` and keep the private-note → team-assignment sequence.
For an AgentBot integration, configure `handoff_mode="agent_bot"` and an actual
**AgentBot API token**, plus the authorized account, inbox and human team.

```python
adapter = ChatwootAdapter(
    # Existing base_url/account_id/inbox_id/tenant_id/repository/transport configuration...
    token=agent_bot_api_token,
    webhook_secret=gateway_credential,
    team_id=human_team_id,
    handoff_mode="agent_bot",
)
```

This is an addition to the signed [webhook ingress](CHATWOOT_WEBHOOK.md). Signing
secrets authenticate incoming requests; the API token authorizes outbound requests.
The adapter cannot determine a token's provider-side actor type from its string.
The bot token, pending conversation and team permissions must be verified in the
test instance. A regular user's token can trigger different assignment behavior.

## Delivery sequence and evidence

In AgentBot mode, a handoff executes these actions in order:

| Step / child action ID | Request | Required response evidence |
|---|---|---|
| Context / `:context` | `POST .../messages`, outgoing text with `private: true` | Positive message ID, exact bound conversation/inbox IDs, `private: true`, integer `message_type: 1`, text content type |
| Team / `:assignment` | `POST .../assignments`, only the configured `team_id` | Raw Team object with exact positive team ID |
| Open / `:open` | `POST .../toggle_status`, explicit `{"status":"open"}` | `payload.conversation_id` matches, `current_status` is `open`, `success` is explicitly `true` or `null` |

The next step starts only after the previous response is validated as sent. Team
assignment precedes open because opening the conversation can trigger automatic
assignment. Never omit `status` from toggle_status: an unqualified toggle can
resolve an already open conversation.

The AgentBot pending→open path calls `bot_handoff!` and clears the AI assignee in
the reference implementation. Team-only assignment does not clear the bot. An
open response alone does not prove that a previously pending bot was cleared if
another actor has already changed the state. All delivery results remain transport
evidence; no assignment, open response or automatic assignment is human acceptance.

`payload.success` can be null on a successful AgentBot handoff: this controller
branch does not set the variable used by the response template. Validating only
that boolean would incorrectly report failure. Conversely, HTTP 2xx without the
expected identifiers/state is `unknown`, not confirmed delivery.

The compatibility assignment mode retains its existing response checks and does
not open conversations. Its output remains a delivery acknowledgement, not a
verification of the assigned team. Strict response checks above apply to the new
AgentBot handoff path, not to ordinary public replies.

## Replay and failure contract

Each adapter instance records handoff delivery results under `(tenant_id, action_id)`.
The fingerprint binds the outbound target, configured team/mode and canonical
snapshot. Reordering JSON object keys does not change it. Reusing the action ID
with different context or target raises `AdapterError` before another request.

A record is claimed before the optional `before_request` audit hook; each step is
checkpointed as unknown/in progress before its request. Same-input calls return
isolated copies of the recorded result without repeating requests, including after
failed/unknown delivery. Concurrent calls are serialized per adapter instance;
reentrant calls observe an unknown in-progress snapshot instead of starting again.
Snapshots and previously returned results cannot rewrite the stored evidence.

| Outcome | Behavior |
|---|---|
| All steps validated | Return `delivery=sent` and ordered child outcomes |
| Non-2xx | Record failed; stop later steps and preserve earlier successes |
| Timeout / malformed successful response | Record unknown; stop later steps |
| Unexpected request or hook exception | Conservatively record unknown; retain earlier outcomes; no later steps |
| Interrupted execution | Its preclaimed unknown checkpoint prevents replay in the surviving instance |

The optional audit hook still runs before each actual request with safe action,
tenant, URL and operation facts. A failing hook prevents that request. No raw
provider body, note text or API credential is included in returned delivery facts.
Runtime records the final aggregate and its child outcomes in the handoff delivery
audit. Configure a persistent audit sink before relying on this in production.

### Explicit reconciliation remains required

There is no automatic rollback, delete-note, reset-to-pending or retry operation.
Do not manufacture a new action ID to bypass an uncertain recorded result. Before
any recovery operation, inspect the provider's note, team and conversation state
and correlate them with the known child actions. A timeout can mean the request
succeeded but its response was lost. If delivery cannot be determined, retain
unknown for explicit handling.

Known failed results are also replayed unchanged; this task does not add a recovery
API. A future recovery executor must reconcile provider state, preserve completed
steps, persist a new authorized attempt and audit it. Business `Failed → Requested`
is a separate D3 transition; HTTP failure does not itself advance that lifecycle.

Runtime keeps the handoff Requested and AI paused regardless of delivery success,
failure or uncertainty. `pending_human` is not resolution, and Accepted still
requires separate trusted human evidence. D2/D3/D5 are unchanged.

## Scope and source references

The delivery records and locks are **in memory**. They do not survive adapter/process
replacement, prevent duplicate execution across workers, enforce provider-side
idempotency or solve an external race. They retain context for the adapter's
lifetime and have no automatic eviction. A durable inbox/outbox and explicit
reconciliation remain production requirements. The handoff contract tests use
synthetic responses. A separate [HTTP service preview](HTTP_SERVICE.md) now runs
real loopback HTTP through simulated providers; no live provider instance, queue
permission or human acceptance has been verified.

Reference version: Chatwoot **v4.17.1**, source checked 2026-09-22:

- [Message response](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/views/api/v1/models/_message.json.jbuilder)
- [Assignments controller](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/controllers/api/v1/accounts/conversations/assignments_controller.rb)
- [Assignment handler](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/models/concerns/assignment_handler.rb)
- [Conversation controller](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/controllers/api/v1/accounts/conversations_controller.rb)
- [Status response](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/views/api/v1/accounts/conversations/toggle_status.json.jbuilder)
- [Automatic assignment](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/models/concerns/auto_assignment_handler.rb)
- [Conversation model](https://github.com/chatwoot/chatwoot/blob/v4.17.1/app/models/conversation.rb)

Run the focused Adapter/Runtime tests:

```sh
UV_OFFLINE=1 uv run --no-sync pytest tests/test_chatwoot_handoff.py tests/test_runtime_adapters.py tests/test_chatwoot_webhook.py
```
