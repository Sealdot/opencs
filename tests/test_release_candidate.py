"""Release boundaries: committed snapshots, excluded assets and detectable tampering."""

import json
import subprocess
from pathlib import Path

import pytest

import build_backend
from scripts.release_candidate import MANIFEST, check, export, inspect_content, public_files


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "private-repo"
    root.mkdir()
    (root / "README.md").write_text("Synthetic public example\n")
    (root / "private.env").write_text("PRIVATE_BUSINESS_DATA=excluded\n")
    manifest = root / MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "0.1.0a0", "files": ["README.md", MANIFEST]}))
    for arguments in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Release test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-m",
            "Synthetic baseline",
        ],
    ):
        subprocess.run(["git", *arguments], cwd=root, capture_output=True, check=True)
    return root


def test_export_has_no_private_files_or_history_and_detects_tampering(tmp_path: Path) -> None:
    root = repository(tmp_path)
    output = tmp_path / "public"
    baseline = export(root, output)
    assert check(output)["passed"]
    assert not (output / ".git").exists()
    assert not (output / "private.env").exists()
    assert (
        baseline["source_commit"]
        == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    )
    (output / "README.md").write_text("Unexpected change")
    with pytest.raises(ValueError, match="hash mismatch"):
        check(output)


def test_export_refuses_dirty_source_and_existing_destination(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "README.md").write_text("Uncommitted")
    with pytest.raises(ValueError, match="Commit candidate"):
        export(root, tmp_path / "dirty-export")
    with pytest.raises(ValueError, match="must not exist"):
        export(root, root)
    assert (root / "private.env").read_text().startswith("PRIVATE_BUSINESS_DATA")


def test_export_refuses_git_symlink_even_with_safe_destination(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "README.md").unlink()
    (root / "README.md").symlink_to("private.env")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Release test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-m",
            "Synthetic symlink",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    output = tmp_path / "public"
    with pytest.raises(ValueError, match="regular tracked"):
        export(root, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "name", ["../README.md", "/etc/passwd", "upstream/a.py", ".env", "project-kit/a.md"]
)
def test_public_manifest_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(ValueError, match="Forbidden public path"):
        public_files({"files": [name]})


def test_check_rejects_extra_file_and_home_path(tmp_path: Path) -> None:
    root = repository(tmp_path)
    output = tmp_path / "public"
    export(root, output)
    (output / "customer-export.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory mismatch"):
        check(output)
    home = "/" + "Users" + "/example/private/"
    with pytest.raises(ValueError, match="Private content"):
        inspect_content("README.md", home.encode())


def test_build_omits_unreviewed_runtime_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    package = root / "opencs"
    package.mkdir()
    (package / "__init__.py").write_text("# selected\n")
    (package / "customer_export.py").write_text("# unreviewed and must not ship\n")
    (root / MANIFEST).write_text(json.dumps({"files": sorted(["opencs/__init__.py", MANIFEST])}))
    monkeypatch.setattr(build_backend, "ROOT", root)
    assert build_backend._package_files() == {"opencs/__init__.py": b"# selected\n"}
