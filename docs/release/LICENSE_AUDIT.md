# License and provenance audit — 2026-10-07

This is a source/metadata inventory, not proof of copyright ownership or a legal
opinion. Status: **maintainer authorization recorded; bounded kit-source trace completed**.
U1 and U2 are recorded below for this authorized release. No third-party license is replaced by
OpenCS's Apache declaration.

## Selected public baseline

The source is an exact allowlisted snapshot of the local candidate commit recorded
in SOURCE_BASELINE.json, derived from private branch baseline `b0a0d72`.
`public-files.json` enumerates every selected path; per-file SHA-256 identifies the
actual candidate bytes. The separate public Git root commit carries no original
history, contributor email addresses or upstream patches.

| Material | Finding | Candidate handling |
|---|---|---|
| `opencs/`, `adapters/`, build/release scripts and runtime tests | Created in local OpenCS commits; no vendored SDK/runtime dependencies, no separate third-party copyright notice found in selected code | Include under Apache-2.0 based on the maintainer statement recorded in U1; git authorship alone is not an authorization record |
| Original `project-kit/` and root `00_*`–`08_*` inputs | 36 supplied documentation files; no LICENSE/NOTICE or independent redistribution grant supplied; checksum evidence proves preservation only | Exclude original paths and bytes from public baseline; preserve privately; U2 applies to derivatives |
| `docs/specs/`, approved decisions/ADRs, schema fixtures and example policy | Based on the supplied kit and accepted D1–D6; ARCHITECTURE and ADAPTER_BOUNDARIES were byte-identical kit copies at intake and have been rewritten as current-release summaries | Include necessary derivative specifications under the maintainer release authorization and source statement recorded in U2; do not treat edits as independent provenance evidence |
| Synthetic billing/example/test JSON and scripted responses | Fictional IDs, amounts, contacts, messages, `.invalid` URLs and explicitly synthetic keys; no customer exports found in selected fixtures | Include as synthetic source examples; check each future addition manually |
| `upstream/` checkouts, contribution snapshots/probes/PR bodies | External repositories and upstream-targeted work, not OpenCS runtime. Local Chatwoot LICENSE distinguishes MIT outside enterprise from separately licensed enterprise/third-party content; LocalSend/native snapshots have their own LICENSE files | Exclude all; no upstream copyright/license/ownership transfer is inferred; retain original assets unchanged |
| `integration-notes/`, experiments, kit archive metadata, historical roadmap/product files | Local records, upstream evidence, archived inputs or experiments outside this frozen runtime scope | Exclude; retain original workspace and history |
| Python and GitHub CI actions | External runtime/tooling obtained independently; not copied into distributions | No vendored binaries; Python installation and CI services retain their own terms |

The declared license is the unmodified [Apache-2.0 text](../../LICENSE), cross-checked
against the [Apache Software Foundation source](https://www.apache.org/licenses/LICENSE-2.0).
Wheel metadata and embedded license are tested. There is no supplied third-party
NOTICE in selected files; none has been invented. Authorization for this selected release is recorded in U1/U2. This inventory
does not establish permission to publish unrelated external materials.

## Dependencies

`pyproject.toml`: runtime dependencies **[]**; PEP 517 build requirements **[]**.
The wheel contains only OpenCS/adapters, synthetic resource JSON and metadata/license.
Developer packages are listed in [DEV_LICENSES.json](DEV_LICENSES.json), with exact
uv.lock versions and relative metadata/license paths plus file hashes. Those
packages, wheels, site-packages and caches are **not redistributed** in this candidate.

| Developer-only licenses | Packages |
|---|---|
| MIT | attrs, iniconfig, jsonschema, jsonschema-specifications, librt, mypy, mypy-extensions, pluggy, pytest, referencing, rfc3339-validator, rpds-py, ruff, six |
| Apache-2.0 OR BSD-2-Clause | packaging |
| BSD-2-Clause | Pygments |
| MPL-2.0 | pathspec (identified from installed LICENSE; metadata lacks license expression) |
| Apache-2.0 | types-jsonschema |
| PSF-2.0 | typing_extensions |
| BSD-3-Clause | colorama 0.4.6, platform-conditional; not installed locally, verified against [tagged upstream LICENSE](https://github.com/tartley/colorama/blob/0.4.6/LICENSE.txt) |

All 20 locked developer/transitive packages are accounted for; 19 installed
packages were inspected locally. No compatibility claim for bundling these tools
is made. Recheck their original licenses/attribution if vendoring them in future.
The development environment is not part of the runtime release.

## Maintainer authorization and bounded source trace — 2026-10-07

- **U1 — recorded maintainer statement:** the maintainer states that the selected
  code is personal work written using Codex, involves no employer or third-party
  materials, and may be released under Apache-2.0. The maintainer explicitly
  authorizes this public repository, prerelease and selected artifacts.
- **U2 — kit provenance:** the maintainer identifies Codex as the kit source and
  requests source tracing as part of this release authorization. Local evidence
  identifies a supplied 36-file ZIP before runtime development. Its SHA-256 is
  `c9e3e8615e2f36149d9f9de36cca7415321553df24b75fd6f6bff0d408205295`.
  All 36 archived kit files match that ZIP byte-for-byte and match the preserved
  bootstrap checksums. The 2026-09-05 bootstrap conversation records receipt of
  the existing documents, their immutable archive, and subsequent specification
  development; the 2026-09-07 maintainer decision approves D1–D6.
  The earliest available Git commit is the 2026-09-22 offline baseline, after
  intake. No original ZIP-generation conversation or download-source metadata
  was recovered. Thus Codex generation is a maintainer-provided source statement,
  not independently proven by the later bootstrap or Git history.
  Selected-source inspection found no separately attributed external kit author,
  vendored implementation, third-party LICENSE/NOTICE, or upstream code copied
  into the selected runtime. This is bounded review, not universal origin proof.
  This release relies on the maintainer's source statement and explicit release
  authorization for the necessary derivatives. Original kit files remain private
  and excluded; external checkout and contribution assets retain their own terms.
- **U3 — provenance limits:** no copied upstream implementation was found in the
  selected runtime paths; adapters follow published contracts and synthetic
  signature vectors. Local records cannot independently certify every line's
  origin. Future third-party additions require their actual license/attribution.
- **U4 — distribution scope:** the maintainer selected `Sealdot/opencs`.
  No package-registry or trademark clearance is claimed. PyPI is outside scope;
  interoperability targets are not presented as endorsers.

## Content hygiene and evidence

Export selects committed regular blobs and rejects symlinks, unlisted files,
private home paths, local-host email patterns, private-key blocks and common access
key patterns. Manual review covered selected fixtures, `.env.example` (empty actual
credentials), URLs and file provenance. These bounded checks cannot prove absence
of every possible secret or external rights claim. Build outputs and sanitized
execution evidence live beside public source, not inside it. Public archives omit
`.git`, customer/business records, credentials, caches and unrelated upstream work.
