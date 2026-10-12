# Candidate handoff and concrete publication steps

## Subsequent offline preview: 0.1.0a1

The maintainer authorized merging PR #4 and publishing its Python 3.13 validation
follow-up on 2026-10-12. Publish a new GitHub prerelease, keeping the existing
v0.1.0a0 tag and assets. No PyPI publication, runtime API change or dependency
upgrade is part of this release.

1. Update package/runtime/manifest versions and the root lockfile metadata, keeping
   all development dependency versions unchanged. Update installation examples,
   release notes and verification documentation; run both Python versions' checks.
2. Commit the reviewed changes in the public repository. Use the same allowlist
   exporter to a new destination and check it before creating caches. Copy its
   generated SOURCE_BASELINE.json back to the public branch and commit that file.
   Its source_commit now identifies a public reviewed commit, rather than a private
   workspace commit. Preserve the allowlist and exclude private history and assets.
3. Merge the release preparation PR with its reviewed commits retained. Wait for
   both push CI and workflow_dispatch frozen-snapshot CI on the exact final main
   commit. Both matrix entries must pass with independent artifact names.
4. Download the verified artifacts and evidence, check cross-version wheel/sdist
   hashes, and create the exact allowlisted public-source archive. Generate
   SHA256SUMS and a sanitized record with commit and CI links. Check the source
   archive inventory and frozen hashes before uploading.
5. Create v0.1.0a1 at that exact public commit and publish the prerelease with the
   wheel, sdist, public-source archive, checksums and verification record. Verify
   downloaded Release files against SHA256SUMS. Keep live_not_run and unstable API
   limits in the notes.

## First Alpha publication — historical sequence

The maintainer authorized the first public Alpha on 2026-10-07. Do not push the existing private workspace,
its branch/history, or its dist directory. No PyPI upload is part of this preview.

1. Review LICENSE_AUDIT.md. Record maintainer confirmation of OpenCS licensing
   authority and redistribution rights for every kit-derived document, schema and
   fixture; resolve or exclude disputed files and rebuild the candidate. Keep any
   private evidence out of public source. Do not mistake Apache metadata for proof.
2. Review the exact public-files.json inventory and SOURCE_BASELINE.json hashes.
   Inspect the independent source directory and sanitized evidence. Re-run
   `python -B -m scripts.release_candidate check --root .` **before** tools create
   caches or environments. The check deliberately rejects unexpected source files.
3. Build into a new empty artifact directory with `python -B -m scripts.build_package
   --out ../artifacts`; run `python -B -m scripts.verify_distributions --dist
   ../artifacts --output ../evidence/smoke.json`. Review SHA256SUMS, source archive
   inventory and the expected 0/1 demo exits. A failed scenario is not a clean pass
   unless it is the designated fault injection.
4. The prepared independent local `main` root commit contains only the public
   snapshot and no ancestors. Confirm its tree, `git rev-list --count HEAD` (=1),
   author identity and zero remotes. Retain the exact public commit and source
   commit in the handoff evidence. If editing public files, create a new candidate
   from a reviewed source commit rather than silently invalidating baseline hashes.
5. Configure a monitored security report route and private community reporting
   contact. Before receiving reports, document the actual routes in SECURITY.md
   and CODE_OF_CONDUCT.md. This requires a new candidate commit/export if those
   source files change. Do not claim an unconfigured service is available.
6. When ready, the maintainer may create an **empty** public OpenCS repository.
   From the independent source repository only: add its verified URL as `origin`,
   push `main`, enable read-only CI and private vulnerability reporting as applicable.
   Never add that remote to the upstream-contribution checkout or push its history.
7. Wait for push CI **and the manually dispatched frozen-snapshot workflow** to
   pass on the exact public commit; review branch
   protection, PR/Issue templates and license visibility. Create the three Issues
   from STARTER_ISSUES.md only after the public repository is ready.
8. Tag the verified public commit as `v0.1.0a0`, create a Release marked prerelease,
   paste ALPHA_RELEASE.md and attach only the reviewed wheel, sdist, public-source
   archive, checksums and sanitized verification record. State live_not_run and
   unstable API boundaries. Verify downloads against SHA256SUMS. Do not upload PyPI.

The workflow below records the authorized publication sequence; actual completion
is reported by repository, CI and Release evidence. Technical candidate completion and
rights clearance are separate recorded states. A later rights/contact edit changes
source/package hashes and must receive a new verification record before publication.

## Recreate a snapshot from the private candidate branch

First commit all reviewed changes and ensure its worktree is clean. Use a new
nonexistent destination (the export refuses to overwrite existing assets):

```sh
python -B -m scripts.release_candidate export --revision HEAD --out ../new-public-source
python -B -m scripts.release_candidate check --root ../new-public-source
```

The exporter reads committed Git blobs, not arbitrary filesystem contents. It
rejects symlinks, private paths and common key patterns, and writes per-file hashes.
This is a bounded automated scan plus manual source review; it is not a universal
secret detector or a legal clearance check. Evidence and virtual environments are
kept beside the source directory. The source archive must exclude `.git` and caches.
