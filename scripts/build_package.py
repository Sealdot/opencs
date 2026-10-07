"""Build local offline-preview wheel and source archives without downloading tools.

Run: UV_OFFLINE=1 uv run --no-sync python -m scripts.build_package --out dist
The artifacts are local development outputs; this command does not publish them.
"""

import argparse
from pathlib import Path

import build_backend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("dist"))
    args = parser.parse_args()
    for build in (build_backend.build_wheel, build_backend.build_sdist):
        print(args.out.resolve() / build(str(args.out)))


if __name__ == "__main__":
    main()
