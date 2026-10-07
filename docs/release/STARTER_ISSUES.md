# Three starter issue drafts

These are the three scoped starter Issues for the first public Alpha. Each is one small change,
requires no provider account, and must preserve D1–D6 and the dependency-free runtime.

## 1. Test Unicode and nested report paths in the installed billing CLI

Problem: packaged CLI coverage checks report content, but does not explicitly cover
an output path with spaces and non-ASCII directory names in an installed environment.
Scope: extend packaging regression tests only; no runtime behavior change.
Acceptance: build/install the wheel in a temporary environment; run normal billing
with a nested Unicode/space path; verify UTF-8 JSON, exit 0, replay unchanged and
provider_calls=0 from outside the repository. A failure must identify missing data
or incorrect encoding. Existing four billing modes remain unchanged.
Tests: focused packaging test plus README lint/type/test checks.
Out of scope: file uploads, business data, live HTTP and path-policy changes.

## 2. Add a readable offline audit walkthrough

Problem: a first-time contributor can run the demo but may not know how to connect
assertion evidence_refs to tenant/conversation-scoped audit records.
Scope: one short guide and one README link, using freshly generated synthetic data.
Acceptance: explain required-tool success, denied refund attempt, handoff Requested
and pending_human; resolve each shown evidence reference to its audit record; mark
all IDs/data as synthetic and real model/provider behavior as unverified. Commands
must reproduce the described output. Never imply assignment means Accepted or
pending_human means resolved. Do not check in raw execution reports.
Tests: execute the documented commands and manually verify every reference/link.
Out of scope: new evaluator rules, scoring, routing or provider setup.

## 3. Verify wheel/sdist acceptance on Python 3.13 in CI

Problem: the current candidate has direct local acceptance on Python 3.12 only,
while metadata permits 3.12+.
Scope: extend the existing CI Python matrix to 3.13, using the same locked tools
and installed-distribution verifier. Keep standard-library-only runtime/build.
Acceptance: formatting/type/tests/contracts pass on the added interpreter; wheel
and sdist install with --no-index/--no-deps/--no-build-isolation; normal scenario
exits 0 and all three fault modes exit 1; sdist rebuild matches wheel bytes. Attach
actual hosted CI evidence or clearly report an unresolved tool compatibility issue.
Tests: both matrix entries; no skip or weakened assertion to hide a failure.
Out of scope: dependency upgrades, Python 3.14+, Windows packaging or live providers.
