# OpenCS 0.1.0a0 — Offline Development Preview

Distribution: GitHub **prerelease**, not a production release. This candidate freezes existing IM functionality and adds release
packaging, documentation and reproducibility checks. APIs may change.

OpenCS makes customer-service identity, conversation, tool permissions, AI/human
responsibility, audit and scenario assertions explicit. This alpha is for developers
learning the contracts or contributing focused tests and documentation.

## Included

Provider-neutral Python domain/application code, in-memory repository/store,
lightweight API, existing Chatwoot/Responses/HTTP adapters, a synthetic billing CLI,
contract/runtime/adapter tests, schemas, a standard-library PEP 517 backend and CI.
Runtime and build dependencies are empty; Python 3.12+ is required.

## Try it

Follow the README ten-minute source-build or local-wheel installation. The normal
billing demo exits 0 with `pending_human`. `forbidden-tool`, `tool-failure` and
`send-timeout` intentionally exit 1. Decisions/responses are scripted, no provider
or real model is called, and replay does not add external actions.

## Verification and attachments

Attach the wheel, rebuildable sdist, exact public-source snapshot, SHA256SUMS and
sanitized verification record from the frozen candidate. SOURCE_BASELINE.json
identifies the private source commit and all selected file hashes; the separate
public root commit contains only the reviewed snapshot. Do not attach the private
workspace or its Git history. The clean-environment verifier installs both artifacts
into separate venvs, blocks sockets during all eight demo runs and compares rebuilt
wheel bytes. Local check counts belong in the accompanying evidence record.
Publication requires passing push CI and the manually dispatched frozen-snapshot
workflow on the exact tagged public commit; actual run links accompany the Release.
The manually dispatched workflow adds the frozen-source hash gate; normal push/PR
checks allow reviewed contribution changes without imposing historical file hashes.

## Limits

State, deduplication and audit are process-local memory; restart loses them. No
production persistence, recovery, horizontal scaling or deployment acceptance.
`pending_human` and successful assignment do not mean human acceptance or resolution.
Real Chatwoot/authentication/queue behavior, OpenAI inference/model quality and
Voice/media integrations remain `live_not_run`. Offline quality metrics are limited;
Resolution, Correctness and Grounding remain `not_evaluated` in this demo.
No new Voice, database or provider scope is part of this release.

## License and remaining work

Apache-2.0 is the declared project license. Developer dependencies keep their own
licenses and are not vendored. The [license audit](https://github.com/Sealdot/opencs/blob/v0.1.0a0/docs/release/LICENSE_AUDIT.md)
records maintainer authorization and the bounded kit-source trace, including its
evidence limits. Private security reporting and a maintainer contact are documented
in SECURITY.md and CODE_OF_CONDUCT.md. No PyPI publication is included.

Next contributions are the three [scoped issue drafts](https://github.com/Sealdot/opencs/blob/v0.1.0a0/docs/release/STARTER_ISSUES.md). Live
integration and production work require separate resources and acceptance later.
