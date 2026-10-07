# Offline domain package

`opencs.domain` exports immutable `Tenant`, `Organization`, `Customer`, `Identity`,
`Conversation`, `Interaction` records, `Event`, and pure `validate_handoff` /
`transition_handoff` checks. It imports only the Python standard library.

`Event.from_dict(payload)` validates the complete 0.0 envelope, all seventeen event
types, timezone-bearing timestamps, finite JSON, and handoff subject/state agreement.
`to_dict()`, `content` and `metadata` return independent snapshots. Handoff checks
apply the seven approved edges, evidence requirement, attempt progression, expected
version and active-handoff guard from D2/D3. The caller supplies trusted triggers;
these functions do not authenticate provider evidence or perform transactions.

`opencs.persistence.MemoryRepository` is the offline entity test substitute. Add a
trusted `Tenant` before resolving references. `resolve_external(tenant, provider,
external_conversation_id, external_contact_id)` atomically creates or reuses explicit
contact and conversation mappings under a process-local lock. Adapters compose the
provider account/inbox scope into external IDs. Names and emails never automatically
merge records. Reopen does not reset resolution, AI pause or interaction history.
Tenant-scoped references and duplicate identity keys are rejected on writes; a
conversation cannot silently switch customer. Repository reads/writes copy nested
interaction data so callers cannot mutate its stored history.

Identity uniqueness uses `(tenant_id, type, provider, value)`, with a nonempty
provider required for `external_id`; identity record IDs are unique within a tenant.
The repository does not normalize email/phone formats or infer identity links.
Application code remains responsible for authorized state changes and serialization
of complete workflows. Memory state is lost at process exit; this does not validate
PostgreSQL constraints, cross-process races, migrations or crash durability.

Verification: `UV_OFFLINE=1 uv run --no-sync pytest tests/test_runtime_domain.py`.
