"""Build a deterministic source bundle for the native helper."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import shutil


ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT.parent
VERSION = "1.0.2"
ARCHIVE_NAME = f"xianyu-native-browser-helper-source-{VERSION}.zip"
SOURCE_ARCHIVE = ROOT / "dist" / ARCHIVE_NAME
PUBLIC_ARCHIVE = PROJECT_ROOT / "static" / "downloads" / ARCHIVE_NAME
FILES = (
    "__init__.py",
    "__main__.py",
    "cdp.py",
    "helper.py",
    "installer.py",
    "keystore.py",
    "protocol.py",
    "runtime.py",
    "server.py",
    "README.md",
    "requirements.txt",
    "packaging/README.md",
    "packaging/entry.py",
    "packaging/macos.spec",
    "packaging/windows.spec",
    "packaging/build-macos.sh",
    "packaging/build-windows.ps1",
    "scripts/build_package.py",
    "scripts/package_macos.py",
    "scripts/verify_package.py",
)


def build_archive() -> None:
    SOURCE_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(SOURCE_ARCHIVE, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in FILES:
            source = ROOT / relative
            info = ZipInfo(f"xianyu-native-browser-helper-{VERSION}/{relative}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100755 << 16 if relative.endswith((".sh", ".ps1")) else 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    shutil.copyfile(SOURCE_ARCHIVE, PUBLIC_ARCHIVE)
    print(SOURCE_ARCHIVE)
    print(PUBLIC_ARCHIVE)


if __name__ == "__main__":
    build_archive()
