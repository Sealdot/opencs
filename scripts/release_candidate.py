"""Export an exact committed public file list; never copy the workspace wholesale."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST = "docs/release/public-files.json"
BASELINE = "SOURCE_BASELINE.json"
FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "project-kit",
    "upstream",
    "integration-notes",
    "experiments",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
PRIVATE_PATTERNS = (
    rb"/(?:Users|home)/[A-Za-z0-9_.-]+/",
    rb"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+\.lan\b",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"\b(?:ghp|github_pat|sk_live)_[A-Za-z0-9]{20,}\b",
)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def public_files(manifest: dict[str, Any]) -> list[str]:
    files = manifest["files"]
    if not isinstance(files, list) or not files or not all(isinstance(x, str) for x in files):
        raise ValueError("Public file list must contain explicit paths")
    if files != sorted(set(files)):
        raise ValueError("Public file list must be sorted and unique")
    for name in files:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or path.as_posix() != name
            or ".." in path.parts
            or FORBIDDEN_PARTS.intersection(path.parts)
            or any(part.startswith(".env") and part != ".env.example" for part in path.parts)
            or (path.parts[0][:2].isdigit() and "_" in path.parts[0])
        ):
            raise ValueError(f"Forbidden public path: {name}")
    return files


def inspect_content(name: str, data: bytes) -> None:
    for pattern in PRIVATE_PATTERNS:
        if re.search(pattern, data):
            raise ValueError(f"Private content pattern detected in {name}")


def export(root: Path, output: Path, revision: str = "HEAD") -> dict[str, Any]:
    if output.exists():
        raise ValueError("Export destination must not exist; existing assets are never overwritten")
    if git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Commit candidate changes before exporting")
    commit = git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    manifest = json.loads(git(root, "show", f"{commit}:{MANIFEST}"))
    files = public_files(manifest)
    content: dict[str, bytes] = {}
    for name in files:
        entry = git(root, "ls-tree", commit, "--", name).decode().split()
        if len(entry) < 4 or entry[0] != "100644" or entry[1] != "blob":
            raise ValueError(f"Public files must be regular tracked files: {name}")
        data = git(root, "show", f"{commit}:{name}")
        inspect_content(name, data)
        content[name] = data
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    baseline = {
        "version": manifest["version"],
        "source_commit": commit,
        "export_kind": "allowlisted_snapshot_without_source_git_history",
        "files_sha256": hashes,
    }
    # Validate everything before creating the destination.
    output.mkdir(parents=True)
    for name, data in content.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / BASELINE).write_text(json.dumps(baseline, indent=2) + "\n")
    return baseline


def check(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / MANIFEST).read_text())
    names = public_files(manifest)
    expected = set(names) | {BASELINE}
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError(f"Public source contains a symbolic link: {relative}")
        if path.is_file():
            actual.add(relative.as_posix())
    if actual != expected:
        raise ValueError(
            f"Public inventory mismatch: missing={expected - actual}, extra={actual - expected}"
        )
    baseline = json.loads((root / BASELINE).read_text())
    if set(baseline["files_sha256"]) != set(names):
        raise ValueError("Baseline inventory does not match public manifest")
    for name in expected:
        data = (root / name).read_bytes()
        inspect_content(name, data)
        if name != BASELINE and hashlib.sha256(data).hexdigest() != baseline["files_sha256"][name]:
            raise ValueError(f"Public source hash mismatch: {name}")
    return {"source_commit": baseline["source_commit"], "public_files": len(names), "passed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("export")
    create.add_argument("--out", type=Path, required=True)
    create.add_argument("--revision", default="HEAD")
    verify = sub.add_parser("check")
    verify.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    if args.action == "export":
        baseline = export(Path(__file__).resolve().parents[1], args.out, args.revision)
        print(
            json.dumps(
                {"source_commit": baseline["source_commit"], "files": len(baseline["files_sha256"])}
            )
        )
    else:
        print(json.dumps(check(args.root)))


if __name__ == "__main__":
    main()
