import plistlib
import struct
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from native_browser_helper.scripts.verify_package import verify_macos, verify_windows


class NativeHelperPackageVerifierTests(unittest.TestCase):
    @staticmethod
    def _macho64(cpu_type: int) -> bytes:
        executable = bytearray(32)
        executable[:4] = b"\xcf\xfa\xed\xfe"
        struct.pack_into("<I", executable, 4, cpu_type)
        return bytes(executable)

    def test_macos_verifier_checks_members_versions_and_arm64_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "mac.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "XianyuNativeHelper.app/Contents/Info.plist",
                    plistlib.dumps({
                        "CFBundleShortVersionString": "1.0.2",
                        "CFBundleVersion": "1.0.2",
                    }),
                )
                archive.writestr(
                    "XianyuNativeHelper.app/Contents/MacOS/XianyuNativeHelper",
                    self._macho64(0x0100000C),
                )
            verify_macos(archive_path, "1.0.2", "arm64")
            with self.assertRaisesRegex(ValueError, "version mismatch"):
                verify_macos(archive_path, "1.0.3", "arm64")

            with ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "XianyuNativeHelper.app/Contents/Info.plist",
                    plistlib.dumps({
                        "CFBundleShortVersionString": "1.0.2",
                        "CFBundleVersion": "1.0.2",
                    }),
                )
                archive.writestr(
                    "XianyuNativeHelper.app/Contents/MacOS/XianyuNativeHelper",
                    self._macho64(0x01000007),
                )
            with self.assertRaisesRegex(ValueError, "arm64"):
                verify_macos(archive_path, "1.0.2", "arm64")

    def test_windows_verifier_checks_real_pe32_plus_x64_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "windows.zip"
            executable = bytearray(0x200)
            executable[:2] = b"MZ"
            struct.pack_into("<I", executable, 0x3C, 0x80)
            executable[0x80:0x84] = b"PE\0\0"
            struct.pack_into("<H", executable, 0x84, 0x8664)
            struct.pack_into("<H", executable, 0x98, 0x20B)
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("XianyuNativeHelper.exe", executable)
            verify_windows(archive_path)
            executable[0x84:0x86] = struct.pack("<H", 0x14C)
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("XianyuNativeHelper.exe", executable)
            with self.assertRaisesRegex(ValueError, "PE32\+ x86-64"):
                verify_windows(archive_path)


if __name__ == "__main__":
    unittest.main()
