# Architecture — offline preview implementation

This 2026-10-07 release description summarizes the existing implementation; it
introduces no new architecture or domain rule. The project began from a supplied
kit. Its original architecture document is retained privately; provenance questions
for the derived specification set are recorded in ../release/LICENSE_AUDIT.md.

```text
API / provider adapters / example assembly
                 ↓
Application ports + deterministic policy + Runtime
                 ↓
Domain entities + events + handoff lifecycle

MemoryRepository + MemoryRuntimeStore implement process-local storage.
Evaluation consumes tenant/conversation-scoped evidence.
```

Core owns identity, conversation, routing/policy, handoff and evaluation semantics.
External provider payloads, request/response mapping and transport belong in
`adapters`; application code uses explicit ports and domain code points inward.
Business rules remain testable code/configuration. No service registry, event bus,
provider SDK or runtime framework is required. Python 3.12+ and standard-library
runtime/build code are the shipped stack. Each external action must be auditable,
and structured decisions are validated before execution.

The supplied kit's PostgreSQL, web framework, migrations and media integrations
remain future production/reference goals. This alpha implements no production
database or Voice adapter. Architecture expansion, Voice and new providers are
paused. Local experiments and upstream checkouts stay outside public source.

In-memory replay and synthetic HTTP do not prove persistence, live authentication,
network recovery or production readiness. Live provider/model behavior and human
acceptance require independent evidence. See the README for the implemented
boundary and the current domain/event/handoff/policy/evaluation specs for rules.
