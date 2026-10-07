# OpenCS Engineering Guide

This is the 0.1.0a0 offline preview candidate. Do not expand architecture, Voice,
production storage or provider scope during release preparation. Runtime and build
backend use only Python's standard library; prefer small typed modules and injection.

Current domain rules live in `docs/specs`; D1–D6 were accepted on 2026-09-07
(see `docs/bootstrap/DECISIONS.md`). Follow those rules for identity, conversation,
routing, policy, resolution, handoff and evaluation. If a rule is missing, stop
scope expansion, describe options and wait for a maintainer decision. Passing tests
or a generated ADR do not constitute approval.

Domain code belongs in `opencs/domain` and must not depend on vendors. Integrations
belong in `adapters`; application uses ports. Business rules must be testable code,
not prompts alone. External actions require auditable results. Pending human review
is not resolution or acceptance. Evidence must stay within tenant/conversation scope.

Before completion: run relevant tests; add regression tests for behavior changes;
run synthetic adapter integration tests for adapter changes; update public docs;
report risks, unsupported cases and any API/semantic change. Never infer live
provider behavior or production persistence from in-memory/fake evidence.

Checks: `uv sync --locked`, `uv run ruff format --check .`, `uv run ruff check .`,
`uv run mypy opencs adapters scripts tests build_backend.py`, `uv run pytest`,
`uv run python scripts/check_foundation.py --contracts-only`.
For a prepared offline environment use `UV_OFFLINE=1 uv run --no-sync ...`.
Build with `python -m scripts.build_package --out dist`; installed-package acceptance
uses `python -m scripts.verify_distributions --dist dist --output evidence/smoke.json`.
The normal billing demo exits 0; its three injected fault modes must exit 1.

The complete private workspace retains immutable `project-kit`, root numbered
input copies, experiments and upstream assets; the public manifest excludes these.
There, also run default `scripts/check_foundation.py` and type-check experiments.
Do not delete or relicense retained assets. Read bootstrap REVIEW and execution
IMPLEMENTATION_PLAN in that workspace before expanding implementation later.

Export only the committed files listed in `docs/release/public-files.json` using
`scripts/release_candidate.py`. Never publish the private source Git history.
A PR should cover one feature/fix/refactor/docs task and state Problem, Solution,
Key decisions, Tests, Risks and Follow-up work.
