# Offline Runtime application

`Runtime` coordinates provider-neutral `Event` records with injected `EntityRepository`,
`RuntimeStore`, `AgentPort`, `ToolPort`, and `ChannelPort` interfaces. It imports no
adapter implementation. The supplied `MemoryRepository` and `MemoryRuntimeStore`
are process-local development implementations; restarting discards state.

## Use-case contract

1. `ingest(event, trusted_tenant=...)` checks the authenticated tenant and existing
   conversation/customer mapping before claiming `(tenant_id, event_id)`.
2. A matching replay returns the saved outcome, or `pending` while the first call
   is executing. A changed payload raises `ReplayConflict` and records the conflict.
3. Customer text becomes an Interaction. Requests in one conversation execute in
   sequence and receive only that conversation's history, tool results, and policy.
4. The Agent returns one strictly validated decision: `reply`, `tool`, `handoff`, or
   `done`. `done` ends processing; it does not mark the customer's issue resolved.
5. Core records each tool attempt, checks its tenant/name allowlist, and audits
   authorization before executing. A denied attempt remains evaluation evidence.
6. Replies have an action id and prior audit. A final responsibility check suppresses
   text generated while an external handoff request arrived. Channel subactions
   must also use an audit hook, as wired in the packaged billing example.

The bounded loop defaults to eight decisions. Invalid model output, provider errors,
step limits, failed delivery, and unknown outcomes are explicit checkpointed results.
Timeouts never trigger a blind resend. This is single-process coordination; it does
not prove crash recovery, durable outbox semantics, or distributed exactly-once effects.
Input and generated Interaction identifiers have separate, conversation-scoped
namespaces. An audit exception produces a failed checkpoint rather than leaving
an event indefinitely pending; if failure logging also fails, the result explicitly
sets `failure_audit_recorded=false`. Persistence implementations still need their
own transactional recovery; this preview supplies no database rollback protocol.

## Responsibility and evidence

Handoff target, reason and pending resolution are required configuration. Billing
tool names and refund rules are in the example JSON, not domain defaults.
Generated Handoff facts and evidence reference actual successful tool calls; the
summary preserves the customer's original request. An assignment acknowledgement
records delivery only. It never changes `requested` to `accepted`.

`can_transition=True` is reserved for a trusted responsibility integration. Callback
events carry a complete snapshot and metadata `trigger` / `expected_version`.
Their event type and snapshot state must match. Initial snapshots must also match
the configured target/reason and the trusted customer's organization. Subsequent
callbacks cannot rewrite the existing handoff context; attempts, versions and edges
follow `docs/specs/handoff-lifecycle.json`. The caller is responsible for verifying
the authenticity of external human evidence before issuing this grant.

Requested handoffs pause AI. Failed, cancelled and completed handoffs retain the
pause and do not fabricate a resolved result. Retry uses the same id, a new event
id, incremented attempt and version, and retains interaction/audit history. Callback
ingestion records verified state changes; it does not implicitly resend a provider
assignment. There is no automatic resume or retry worker in this preview.

## Validation

`tests/test_runtime_application.py` covers concurrency, scope, policy, audit order,
failure checkpoints, responsibility lifecycle, and late reply suppression.
`tests/test_runtime_scenario.py` connects the actual Core to synthetic HTTP adapters.
`tests/test_runtime_api.py` exercises authenticated in-process ASGI ingress.
