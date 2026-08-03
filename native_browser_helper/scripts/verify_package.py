"""Verify native helper archives before they are published."""

from __future__ import annotations

import argparse
import plistlib
import struct
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile


REQUIRED_MAC_FILES = {
    "XianyuNativeHelper.app/Contents/Info.plist",
    "XianyuNativeHelper.app/Contents/MacOS/XianyuNativeHelper",
}
REQUIRED_WINDOWS_FILES = {"XianyuNativeHelper.exe"}
MACHO_CPU_TYPES = {
    "arm64": 0x0100000C,
    "x86_64": 0x01000007,
}


def _read_member(archive: ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        raise ValueError(f"missing archive member: {name}") from exc


def verify_macos(path: Path, version: str, architecture: str = "arm64") -> None:
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = REQUIRED_MAC_FILES - names
        if missing:
            raise ValueError(f"missing macOS members: {sorted(missing)}")
        info = plistlib.loads(_read_member(archive, "XianyuNativeHelper.app/Contents/Info.plist"))
        if (
            info.get("CFBundleShortVersionString") != version
            or info.get("CFBundleVersion") != version
        ):
            raise ValueError("macOS bundle version mismatch")
        executable = _read_member(archive, "XianyuNativeHelper.app/Contents/MacOS/XianyuNativeHelper")
        if len(executable) < 8 or executable[:4] not in {
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
        }:
            raise ValueError("macOS executable is not a 64-bit Mach-O")
        byte_order = "<" if executable[:4] == b"\xcf\xfa\xed\xfe" else ">"
        cpu_type = struct.unpack_from(f"{byte_order}I", executable, 4)[0]
        expected_cpu_type = MACHO_CPU_TYPES[architecture]
        if cpu_type != expected_cpu_type:
            raise ValueError(f"macOS executable is not {architecture}")
        archive.testzip()


def verify_windows(path: Path) -> None:
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = REQUIRED_WINDOWS_FILES - names
        if missing:
            raise ValueError(f"missing Windows members: {sorted(missing)}")
        executable = _read_member(archive, "XianyuNativeHelper.exe")
        if executable[:2] != b"MZ" or len(executable) < 0x100:
            raise ValueError("Windows executable is not a PE file")
        pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
        if executable[pe_offset:pe_offset + 4] != b"PE\0\0":
            raise ValueError("Windows executable has no PE signature")
        machine = struct.unpack_from("<H", executable, pe_offset + 4)[0]
        optional_magic = struct.unpack_from("<H", executable, pe_offset + 24)[0]
        if machine != 0x8664 or optional_magic != 0x20B:
            raise ValueError("Windows executable is not PE32+ x86-64")
        archive.testzip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", default="1.0.2")
    parser.add_argument("--arch", choices=tuple(MACHO_CPU_TYPES), default="arm64")
    args = parser.parse_args()
    try:
        if args.platform == "macos":
            verify_macos(args.archive, args.version, args.arch)
        else:
            verify_windows(args.archive)
    except (BadZipFile, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{args.platform} helper package verification passed: {args.archive}")


if __name__ == "__main__":
    main()
