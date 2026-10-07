# Offline ASGI ingress

`create_app(runtime, credentials, max_body_bytes=1_048_576)` returns a standard
ASGI callable using only the Python standard library and OpenCS. No FastAPI,
Uvicorn, sockets or provider services are required by this package or its tests.
The thin ingress can later be wrapped or replaced by FastAPI without moving
customer-service rules out of `Runtime.ingest`.

```python
from opencs.api import AccessGrant, create_app

# Load tokens through your chosen local secret mechanism at composition time.
# Never commit real tokens or obtain permissions from the submitted envelope.
app = create_app(
    runtime,
    {
        customer_events_token: AccessGrant(tenant_id="tenant_acme"),
        trusted_responsibility_token: AccessGrant(tenant_id="tenant_acme", can_transition=True),
    },
)
```

`AccessGrant` is frozen; the factory copies the credential mapping. Each token
binds one tenant and an explicit handoff transition capability. There is no
anonymous event ingestion. A normal grant cannot submit handoff lifecycle events.
The capability authorizes a trusted evidence source; it does not itself prove
that a provider has recorded human acceptance.

| Endpoint | Behavior |
|---|---|
| `POST /v1/events` | Bearer authentication; `application/json` normalized event envelope; passes trusted tenant and transition grant to Runtime |
| `GET /health/live` | Process API liveness; explicitly offline |
| `GET /health/ready` | Readiness for the in-memory offline harness only; `durable=false`, `live_integrations=not_run` |

Event ingestion returns HTTP 200 and the stored application result, including on
an identical replay. A stored `failed`, `unknown`, or handoff `rejected` result remains that outcome;
HTTP success does not mean the external action succeeded, and must not trigger
blind retries. A conflicting replay returns 409. Permission failures return
401/403; unknown subjects return 404; invalid JSON/length returns 400; oversized
bodies return 413; wrong media type returns 415; invalid event shape or unsupported
ingestion type returns 422. A valid envelope with a rejected lifecycle transition
is checkpointed as `status=rejected` with HTTP 200. Unexpected runtime exceptions return a generic 500 without exposing
credentials or exception contents. Responses are JSON with `Cache-Control: no-store`.

The actual accumulated request bytes are bounded even without Content-Length.
Bodies are UTF-8 JSON, reject duplicate object keys, NaN/Infinity, invalid domain
values and incomplete requests. The adapter does not ingest after a disconnect
before body completion. Runtime invocation runs in the standard library thread
executor so synchronous use cases do not block the ASGI event loop. Lifespan
startup/shutdown acknowledgement is supported; no external resource is started.

Validation:

```sh
UV_OFFLINE=1 uv run --no-sync pytest tests/test_runtime_api.py
```

These are in-process ASGI contract tests with real Runtime replay/permission
checks and explicit recording doubles for boundary cases. They do not validate
a network listener, TLS, proxy/header configuration, request timeouts, rate limits,
production authentication, PostgreSQL readiness/durability or actual providers.
A real HTTP server and deployment integration remain **live_not_run**.
