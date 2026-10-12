# OpenCS 0.1.0a1 — Python 3.13 Validation Preview

GitHub **prerelease** for offline development. APIs remain unstable; this preview
cannot be used for production customer service.

## Changes from 0.1.0a0

- CI runs the complete offline checks on Python 3.12 and 3.13, explicitly selects
  the interpreter and asserts its actual version before validation.
- Each version builds and freshly installs wheel and sdist, verifies the normal
  billing demo and three expected fault scenarios for each installation, and
  confirms that rebuilding from sdist produces an identical wheel.
- Matrix failures are independent, and artifacts/evidence have version-specific
  names. Installation examples and package metadata now use 0.1.0a1.
- Runtime APIs, D1–D6 domain rules, provider scope and dependency versions are
  unchanged. The original v0.1.0a0 tag and attachments remain available.

## Try it

Python 3.12+ is required; 3.12 and 3.13 are validated. Other versions are unverified.
Runtime and build dependencies are empty. No Docker, provider account, API key or
model download is needed for the offline billing demo.

Follow the [README](https://github.com/Sealdot/opencs/blob/v0.1.0a1/README.md) to build
from source or install the wheel with `--no-index --no-deps --no-build-isolation`.
The normal billing demo exits 0 with `pending_human`; `forbidden-tool`,
`tool-failure` and `send-timeout` intentionally exit 1. Decisions and responses are
scripted, provider_calls=0, and replay does not add external actions.

## Verification and attachments

The Release includes wheel, rebuildable sdist, the exact allowlisted public-source
snapshot, SHA256SUMS and a sanitized verification record. SOURCE_BASELINE.json
identifies the reviewed public source commit and selected file hashes; archives
exclude private workspace assets, Git history, caches and credentials.

Release publication requires push CI and manually dispatched frozen-snapshot CI
on the exact tagged public commit, for both Python versions. Run links and actual
test counts accompany the Release in its verification record. The installer
verifier blocks sockets during all eight demo runs and compares rebuilt wheel
bytes. This evidence covers the offline reference runtime only.

## Limits and license

State, deduplication and audit remain process-local memory; restart loses them.
Production persistence, recovery, horizontal scaling and deployment are unverified.
`pending_human` and successful assignment do not mean human acceptance or resolution.
Real Chatwoot authentication/queues, OpenAI inference/model quality and Voice/media
integrations remain `live_not_run`. Resolution, Correctness and Grounding remain
`not_evaluated` in this demo; there is no aggregate model-quality score.

Apache-2.0 is the declared license. Developer dependencies keep their own licenses
and are not bundled into the runtime. The
[license audit](https://github.com/Sealdot/opencs/blob/v0.1.0a1/docs/release/LICENSE_AUDIT.md)
retains the maintainer authorization, bounded kit-source trace and evidence limits.
Security/community reporting contacts remain in SECURITY.md and CODE_OF_CONDUCT.md.
PyPI publication, Python 3.14+ and new live integrations are outside this release.
