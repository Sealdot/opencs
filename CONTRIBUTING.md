# Contributing to OpenCS

Start with [README](README.md), [AGENTS](AGENTS.md), [current specs](docs/specs/),
and the [accepted D1–D6](docs/bootstrap/DECISIONS.md). This is an offline alpha
preview with unstable APIs. Voice, production databases, architecture expansion
and new provider integrations are paused for this candidate.

Pick one [starter issue draft](docs/release/STARTER_ISSUES.md) or propose one focused
problem with an acceptance criterion. Keep domain logic in `opencs/domain`, use
application ports, and put external integration code in `adapters`. Industry rules
belong in example configuration. Do not add runtime/build dependencies casually.

Follow existing specs for conversation, handoff, routing, permissions, resolution
and evaluation. A new convention requires a recorded maintainer decision before
implementation; D1–D6 already have approval and need no repeated approval.

Run the README checks and relevant tests. Adapter changes require synthetic
integration tests and source/version evidence; distinguish those from live tests.
When adding a public file, explicitly update `docs/release/public-files.json`;
unlisted files do not ship. The frozen SOURCE_BASELINE.json describes the original
candidate, so its hash gate runs only in the manual release workflow. Normal PR CI
checks changed code, contracts and installed distributions without requiring those
historical hashes to match. Maintainers create a fresh snapshot for each candidate.
Use the PR template: Problem, Solution, Key decisions, Tests, Risks, Follow-up work.
Never submit customer records, secrets, workstation paths or unrelated upstream
patches. All fixtures must be synthetic. Main changes require maintainer review.

Contributions are intended under Apache-2.0. Submit only material you are authorized
to license, retain existing attribution, and disclose third-party origins. No CLA,
DCO bot or additional governance process is configured. The initial candidate's
remaining provenance questions are recorded in [license audit](docs/release/LICENSE_AUDIT.md).
