"""Distribution and architecture checks run without build tools or site-packages."""

import ast
import base64
import csv
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

import build_backend
import opencs

ROOT = Path(__file__).resolve().parents[1]


def test_project_version_and_dependency_free_build_contract() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert opencs.__version__ == project["project"]["version"] == "0.1.0a0"
    assert project["project"]["dependencies"] == []
    assert project["project"]["requires-python"] == ">=3.12"
    assert project["build-system"] == {
        "requires": [],
        "build-backend": "build_backend",
        "backend-path": ["."],
    }
    assert build_backend.get_requires_for_build_wheel() == []
    assert build_backend.get_requires_for_build_sdist() == []


def test_wheel_record_metadata_license_and_contents(tmp_path: Path) -> None:
    path = tmp_path / build_backend.build_wheel(str(tmp_path))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        prefix = f"opencs-{opencs.__version__}.dist-info"
        assert len(names) == len(set(names))
        assert "opencs/__init__.py" in names
        assert "opencs/py.typed" in names
        assert all(name.startswith(("opencs/", "adapters/", prefix + "/")) for name in names)
        assert not any("__pycache__" in name or ".env" in name for name in names)
        metadata = BytesParser().parsebytes(archive.read(prefix + "/METADATA"))
        assert metadata["Metadata-Version"] == "2.4"
        assert metadata["Name"] == "opencs"
        assert metadata["Version"] == opencs.__version__
        assert metadata["License-Expression"] == "Apache-2.0"
        assert metadata["License-File"] == "LICENSE"
        assert metadata["Requires-Python"] == ">=3.12"
        assert metadata.get_all("Requires-Dist") is None
        assert archive.read(prefix + "/licenses/LICENSE") == (ROOT / "LICENSE").read_bytes()
        rows = list(csv.reader(io.StringIO(archive.read(prefix + "/RECORD").decode())))
        assert {row[0] for row in rows} == set(names)
        assert len(rows) == len(names)
        for name, digest, size in rows:
            if name == prefix + "/RECORD":
                assert digest == size == ""
            else:
                data = archive.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
                assert digest == "sha256=" + expected.decode()
                assert int(size) == len(data)


def test_prepared_metadata_is_preserved(tmp_path: Path) -> None:
    directory = tmp_path / "metadata"
    name = build_backend.prepare_metadata_for_build_wheel(str(directory))
    (directory / name / "custom-build-evidence").write_text("retained across hooks")
    path = tmp_path / build_backend.build_wheel(
        str(tmp_path), metadata_directory=str(directory / name)
    )
    with zipfile.ZipFile(path) as archive:
        for file in (directory / name).rglob("*"):
            if file.is_file():
                relative = file.relative_to(directory).as_posix()
                assert archive.read(relative) == file.read_bytes()


def test_sdist_rebuilds_identical_wheel_with_standard_library(tmp_path: Path) -> None:
    wheel = tmp_path / build_backend.build_wheel(str(tmp_path))
    sdist = tmp_path / build_backend.build_sdist(str(tmp_path))
    extracted = tmp_path / "source"
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        stem = f"opencs-{opencs.__version__}"
        assert f"{stem}/pyproject.toml" in names
        assert f"{stem}/PKG-INFO" in names
        assert not any("/experiments/" in name or "/project-kit/" in name for name in names)
        manifest = json.loads((ROOT / "docs/release/public-files.json").read_text())
        assert set(names) == {f"{stem}/{name}" for name in manifest["files"]} | {
            f"{stem}/PKG-INFO",
            *([f"{stem}/SOURCE_BASELINE.json"] if (ROOT / "SOURCE_BASELINE.json").exists() else []),
        }
        archive.extractall(extracted, filter="data")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys; sys.path.insert(0, '.'); import build_backend; "
            "build_backend.build_wheel('rebuilt')",
        ],
        cwd=extracted / stem,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (extracted / stem / "rebuilt" / wheel.name).read_bytes() == wheel.read_bytes()


def test_wheel_imports_all_modules_without_repo_or_dependencies(tmp_path: Path) -> None:
    wheel = tmp_path / build_backend.build_wheel(str(tmp_path))
    with zipfile.ZipFile(wheel) as archive:
        modules = []
        for name in archive.namelist():
            if name.endswith(".py"):
                module = name[:-3].replace("/", ".")
                modules.append(module.removesuffix(".__init__"))
    command = (
        "import sys, importlib, importlib.metadata; "
        f"sys.path.insert(0, {str(wheel)!r}); "
        f"[importlib.import_module(name) for name in {modules!r}]; "
        f"assert importlib.metadata.version('opencs') == {opencs.__version__!r}; "
        "assert not any(name.startswith(('experiments', 'scripts')) for name in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", command],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fault", ["none", "forbidden-tool", "tool-failure", "send-timeout"])
def test_packaged_billing_cli_runs_without_repository_or_dependencies(
    tmp_path: Path, fault: str
) -> None:
    wheel = tmp_path / build_backend.build_wheel(str(tmp_path))
    report_path = tmp_path / "reports" / f"{fault}.json"
    command = (
        "import sys, socket\n"
        f"sys.path.insert(0, {str(wheel)!r})\n"
        "def forbid_network(*args, **kwargs):\n"
        "    raise AssertionError('Offline wheel must not access the network')\n"
        "socket.socket = socket.create_connection = forbid_network\n"
        "from opencs.examples import billing\n"
        "assert '.whl/' in billing.__file__\n"
        "assert billing.resource('policy.json')['allowed_tools']\n"
        f"sys.argv = ['billing', '--fault', {fault!r}, '--output', {str(report_path)!r}]\n"
        "exit_code = billing.main()\n"
        "assert not any(name.startswith(('scripts', 'experiments')) for name in sys.modules)\n"
        "raise SystemExit(exit_code)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", command],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == (0 if fault == "none" else 1), result.stderr
    summary = json.loads(result.stdout)
    report = json.loads(report_path.read_text())
    for value in (summary, report):
        assert value["fault"] == fault
        assert value["passed"] is (fault == "none")
        assert value["replay_unchanged"] is True
        assert value["provider_calls"] == 0
        assert value["model_inference"] is False
        assert value["live_integration"] == "live_not_run"
    assert report["evidence_kind"] == "offline_contract"
    assert report["evaluation"]["scenario"] == "duplicate_charge_enterprise"
    assert report["audit"]


def _imports(path: Path) -> set[str]:
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    package = module.rsplit(".", 1)[0]
    imports: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            imported = importlib.util.resolve_name(prefix, package) if node.level else prefix
            imports.add(imported)
            imports.update(f"{imported}.{alias.name}" for alias in node.names)
    return imports


def test_domain_dependencies_point_inward() -> None:
    forbidden = ("adapters", "opencs.application", "opencs.persistence", "opencs.api")
    for path in (ROOT / "opencs" / "domain").rglob("*.py"):
        for name in _imports(path):
            assert not any(name == item or name.startswith(item + ".") for item in forbidden), (
                path,
                name,
            )


def test_application_depends_on_ports_not_concrete_adapters() -> None:
    forbidden = {"adapters", "scripts", "experiments"}
    for path in (ROOT / "opencs" / "application").rglob("*.py"):
        for name in _imports(path):
            assert name.split(".")[0] not in forbidden, (path, name)


def test_runtime_imports_only_standard_library_or_runtime_packages() -> None:
    # Runtime code must remain independent of experiments, validation scripts,
    # provider SDKs and the development-only JSON Schema/test dependencies.
    allowed = sys.stdlib_module_names | {"opencs", "adapters", "__future__"}
    for package in ("opencs", "adapters"):
        for path in (ROOT / package).rglob("*.py"):
            for name in _imports(path):
                assert name.split(".")[0] in allowed, (path, name)
