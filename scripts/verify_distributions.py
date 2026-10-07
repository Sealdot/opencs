"""Install wheel and sdist into separate fresh venvs and verify all offline scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import venv
from pathlib import Path
from typing import Any

FAULTS = ("none", "forbidden-tool", "tool-failure", "send-timeout")
EXPECTED_FAILURES = {
    "none": set(),
    "forbidden-tool": {"forbidden_tool:issue_refund"},
    "tool-failure": {"required_tool:get_invoice"},
    "send-timeout": {"required_handoff"},
}


def run(command: list[str], cwd: Path, *, expected: int = 0) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PIP_"))}
    environment["PIP_NO_INDEX"] = "1"
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True)
    if result.returncode != expected:
        # Diagnostics are local, never included automatically in public evidence.
        raise RuntimeError(
            f"Command failed with {result.returncode}: {result.stdout}\n{result.stderr}"
        )
    return result


def verify(dist: Path) -> dict[str, Any]:
    wheels, sources = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Use a clean dist directory containing exactly one wheel and one sdist")
    wheel, sdist = wheels[0].resolve(), sources[0].resolve()
    evidence: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": f"{sys.platform}/{platform.machine()}",
        "network_during_install": "disabled_by_no_index_no_deps_no_build_isolation",
        "network_during_demo": "socket_creation_blocked",
        "artifacts_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wheel, sdist)
        },
        "installations": [],
    }
    with tempfile.TemporaryDirectory(prefix="opencs-preview-") as temporary:
        root = Path(temporary)
        for kind, artifact in (("wheel", wheel), ("sdist", sdist)):
            environment = root / kind
            # Match `python -m venv`: standalone macOS Python needs its original
            # executable location to resolve the bundled shared library.
            venv.EnvBuilder(with_pip=True, clear=False, symlinks=os.name != "nt").create(
                environment
            )
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            cwd = root / f"outside-source-{kind}"
            cwd.mkdir()
            pip_flags = ["--isolated", "--disable-pip-version-check"]
            run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "pip",
                    *pip_flags,
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    "--no-cache-dir",
                    str(artifact),
                ],
                cwd,
            )
            installed = json.loads(
                run(
                    [
                        str(python),
                        "-I",
                        "-m",
                        "pip",
                        *pip_flags,
                        "list",
                        "--format=json",
                    ],
                    cwd,
                ).stdout
            )
            if {p["name"] for p in installed} != {"pip", "opencs"}:
                raise AssertionError(f"Unexpected packages in clean environment: {installed}")
            installation: dict[str, Any] = {"kind": kind, "packages": installed, "scenarios": []}
            for fault in FAULTS:
                report = cwd / f"{fault}.json"
                # -I removes cwd/PYTHONPATH. Assert an installed module, then prevent sockets.
                command = (
                    "import pathlib, runpy, socket, sys, opencs\n"
                    "origin = pathlib.Path(opencs.__file__).resolve()\n"
                    "assert origin.is_relative_to(pathlib.Path(sys.prefix).resolve())\n"
                    "def blocked(*args, **kwargs):\n"
                    "    raise AssertionError('Offline demo attempted network access')\n"
                    "socket.socket = socket.create_connection = blocked\n"
                    f"sys.argv = ['billing', '--fault', {fault!r}, '--output', {str(report)!r}]\n"
                    "runpy.run_module('opencs.examples.billing', run_name='__main__')\n"
                )
                expected = 0 if fault == "none" else 1
                run([str(python), "-I", "-c", command], cwd, expected=expected)
                data = json.loads(report.read_text())
                assert data["fault"] == fault and data["passed"] is (fault == "none")
                assert data["replay_unchanged"] and data["provider_calls"] == 0
                assert (
                    data["model_inference"] is False and data["live_integration"] == "live_not_run"
                )
                assert data["persistence"] == "process_local_memory"
                assert data["evaluation"]["metrics"]["resolution"]["status"] == "not_evaluated"
                failures = {
                    assertion["name"]
                    for assertion in data["evaluation"]["assertions"]
                    if assertion["status"] == "fail"
                }
                assert failures == EXPECTED_FAILURES[fault], (fault, failures)
                installation["scenarios"].append(
                    {
                        "fault": fault,
                        "exit_code": expected,
                        "passed": data["passed"],
                        "replay_unchanged": data["replay_unchanged"],
                        "provider_calls": data["provider_calls"],
                        "model_inference": data["model_inference"],
                        "live_integration": data["live_integration"],
                        "assertions": data["evaluation"]["assertions"],
                        "report": data,
                    }
                )
            evidence["installations"].append(installation)
        extracted = root / "sdist-source"
        with tarfile.open(sdist) as archive:
            archive.extractall(extracted, filter="data")
        source_root = next(extracted.iterdir())
        run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, '.'); import build_backend; "
                "build_backend.build_wheel('rebuilt')",
            ],
            source_root,
        )
        assert (source_root / "rebuilt" / wheel.name).read_bytes() == wheel.read_bytes()
        evidence["sdist_rebuild_identical_wheel"] = True
    evidence["passed"] = True
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = verify(args.dist)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: fresh wheel + sdist installs; eight expected scenario outcomes; identical rebuild")


if __name__ == "__main__":
    main()
