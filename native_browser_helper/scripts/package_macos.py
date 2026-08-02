"""Package a built macOS application bundle without losing bundle metadata."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent
VERSION = "1.0.1"
ARCHIVE_NAME = f"xianyu-native-browser-helper-macos-arm64-{VERSION}.zip"


def package(app: Path, output: Path, public_output: Optional[Path]) -> None:
    app = app.resolve()
    if not app.is_dir() or not (app / "Contents" / "MacOS" / "XianyuNativeHelper").is_file():
        raise SystemExit(f"invalid application bundle: {app}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(output)],
        check=True,
    )
    if public_output is not None:
        public_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output, public_output)
    print(output)
    if public_output is not None:
        print(public_output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / ARCHIVE_NAME)
    parser.add_argument(
        "--public-output",
        type=Path,
        default=PROJECT_ROOT / "static" / "downloads" / ARCHIVE_NAME,
    )
    parser.add_argument("--no-public-copy", action="store_true")
    args = parser.parse_args()
    package(args.app, args.output, None if args.no_public_copy else args.public_output)


if __name__ == "__main__":
    main()
