"""Build the Chrome extension archives reproducibly."""

from __future__ import annotations

from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ARCHIVE = ROOT / "dist" / "xianyu-cookie-importer.zip"
PUBLIC_ARCHIVE = ROOT.parent / "static" / "downloads" / SOURCE_ARCHIVE.name
PACKAGE_FILES = (
    "manifest.json",
    "popup.html",
    "popup.css",
    "popup.js",
    "lib.mjs",
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


if __name__ == "__main__":
    build_archive()
