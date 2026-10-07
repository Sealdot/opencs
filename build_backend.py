"""Small, repository-specific PEP 517 backend requiring only Python's standard library.

Builds pure Python wheels and rebuildable source archives. This is deliberately
not a general packaging framework: no editable installation, entry points,
extension compilation, dynamic metadata, or dependency resolution is supported.
Package files are allowlisted; repository fixtures, experiments and secrets are
never copied wholesale. See the PyPA binary/source distribution specifications:
https://packaging.python.org/en/latest/specifications/binary-distribution-format/
https://packaging.python.org/en/latest/specifications/source-distribution-format/
"""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _project() -> dict[str, Any]:
    project: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    if project["name"] != "opencs" or project.get("dependencies") or project.get("dynamic"):
        raise ValueError("This backend only supports the dependency-free, static OpenCS package")
    return project


def _stem() -> str:
    return f"opencs-{_project()['version']}"


def _metadata() -> bytes:
    project = _project()
    headers = [
        "Metadata-Version: 2.4",
        f"Name: {project['name']}",
        f"Version: {project['version']}",
        f"Summary: {project['description']}",
        f"Requires-Python: {project['requires-python']}",
        f"License-Expression: {project['license']}",
        "License-File: LICENSE",
        "Description-Content-Type: text/markdown",
    ]
    readme = str(project["readme"])
    return ("\n".join(headers) + "\n\n" + (ROOT / readme).read_text()).encode()


def _package_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name, data in _source_files().items():
        relative = Path(name)
        if relative.parts[0] not in {"opencs", "adapters"}:
            continue
        is_code = relative.suffix == ".py" or relative.name == "py.typed"
        is_example = (
            relative.parts[:3] == ("opencs", "examples", "data") and relative.suffix == ".json"
        )
        if is_code or is_example:
            files[name] = data
    return files


def _source_files() -> dict[str, bytes]:
    manifest = json.loads((ROOT / "docs/release/public-files.json").read_text())
    names = manifest["files"]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("Public source manifest must contain file paths")
    if names != sorted(set(names)):
        raise ValueError("Public source manifest must be sorted and unique")
    files: dict[str, bytes] = {}
    for name in names:
        path = ROOT / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or not path.resolve().is_relative_to(ROOT)
            or any(
                part.is_symlink()
                for part in (path, *path.parents)
                if part != ROOT and part.is_relative_to(ROOT)
            )
        ):
            raise ValueError("Unsafe public source path")
        files[name] = path.read_bytes()
    return files


def _dist_info() -> dict[str, bytes]:
    return {
        "METADATA": _metadata(),
        "WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: opencs-stdlib-backend\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "licenses/LICENSE": (ROOT / "LICENSE").read_bytes(),
    }


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: dict[str, Any] | None = None
) -> str:
    name = f"{_stem()}.dist-info"
    for relative, data in _dist_info().items():
        target = Path(metadata_directory) / name / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return name


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    name = f"{_stem()}-py3-none-any.whl"
    prefix = f"{_stem()}.dist-info"
    files = _package_files()
    if metadata_directory is not None:
        metadata_root = Path(metadata_directory)
        # PEP 517 passes the actual .dist-info directory, not its parent.
        files.update(
            {
                f"{prefix}/{path.relative_to(metadata_root).as_posix()}": path.read_bytes()
                for path in sorted(metadata_root.rglob("*"))
                if path.is_file()
            }
        )
    else:
        files.update({f"{prefix}/{relative}": data for relative, data in _dist_info().items()})
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for relative, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((relative, f"sha256={digest}", len(data)))
    writer.writerow((f"{prefix}/RECORD", "", ""))
    files[f"{prefix}/RECORD"] = record.getvalue().encode()
    Path(wheel_directory).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(Path(wheel_directory) / name, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, data in files.items():
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return name


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    stem = _stem()
    name = f"{stem}.tar.gz"
    files = _package_files()
    # The source archive uses the same reviewed, exact file list as public export.
    # It remains independently testable without the private kit or Spike checkout.
    files.update(_source_files())
    baseline = ROOT / "SOURCE_BASELINE.json"
    if baseline.exists():
        if baseline.is_symlink():
            raise ValueError("Source baseline must not be a symbolic link")
        files[baseline.name] = baseline.read_bytes()
    files["PKG-INFO"] = _metadata()
    Path(sdist_directory).mkdir(parents=True, exist_ok=True)
    with (Path(sdist_directory) / name).open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative, data in files.items():
                    info = tarfile.TarInfo(f"{stem}/{relative}")
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
    return name
