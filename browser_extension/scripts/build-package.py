"""Build the Chrome extension archives reproducibly."""

from __future__ import annotations

from pathlib import Path
import json
import shutil
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent.parent
EXTENSION_VERSION = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
ARCHIVE_NAME = f"xianyu-browser-bridge-{EXTENSION_VERSION}.zip"
SOURCE_ARCHIVE = ROOT / "dist" / ARCHIVE_NAME
PUBLIC_ARCHIVE = ROOT.parent / "static" / "downloads" / ARCHIVE_NAME
COMPATIBILITY_ARCHIVE = ROOT.parent / "static" / "downloads" / "xianyu-cookie-importer.zip"
PACKAGE_FILES = (
    "manifest.json",
    "popup.html",
    "popup.css",
    "popup.js",
    "lib.mjs",
    "background.js",
    "content.js",
    "README.md",
    "icons/icon-16.png",
    "icons/icon-32.png",
    "icons/icon-48.png",
    "icons/icon-128.png",
)


def build_archive() -> None:
    SOURCE_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(SOURCE_ARCHIVE, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path in PACKAGE_FILES:
            source = ROOT / relative_path
            info = ZipInfo(relative_path, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, source.read_bytes(), compresslevel=9)

    shutil.copyfile(SOURCE_ARCHIVE, PUBLIC_ARCHIVE)
    shutil.copyfile(SOURCE_ARCHIVE, COMPATIBILITY_ARCHIVE)


if __name__ == "__main__":
    build_archive()
