"""Small OS-aware key store for the native helper device identity."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

from .protocol import DeviceIdentity


SERVICE_NAME = "xianyu-monitor-native-helper-v1"
ACCOUNT_NAME = "native-helper"


def default_state_dir() -> Path:
    override = os.environ.get("XMC_HELPER_STATE_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "XianyuNativeHelper"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "XianyuNativeHelper"
    return Path.home() / ".config" / "xianyu-native-helper"


class IdentityStore:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir or default_state_dir())
        self.state_file = self.state_dir / "device.json"
        self.secret_file = self.state_dir / "device.secret"
        self.keychain_service = (
            os.environ.get("XMC_HELPER_KEYCHAIN_SERVICE") or SERVICE_NAME
        )
        self.keychain_account = (
            os.environ.get("XMC_HELPER_KEYCHAIN_ACCOUNT") or ACCOUNT_NAME
        )

    def load_or_create(self, browser_family: str) -> DeviceIdentity:
        record = self._read_record()
        if record:
            identity = DeviceIdentity.from_record(record)
            if identity.browser_family != browser_family:
                identity.browser_family = browser_family
                self.save(identity)
            return identity
        identity = DeviceIdentity.generate(browser_family)
        self.save(identity)
        return identity

    def save(self, identity: DeviceIdentity) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(identity.to_record(), ensure_ascii=True, sort_keys=True)
        system = platform.system()
        if system == "Darwin":
            if not self._save_keychain(payload):
                raise RuntimeError("macOS Keychain is unavailable")
            self._write_metadata(identity)
            return
        if system == "Windows":
            if not self._save_dpapi(payload):
                raise RuntimeError("Windows DPAPI is unavailable")
            self._write_metadata(identity)
            return
        self._write_file(self.secret_file, payload)

    def _read_record(self) -> Optional[dict[str, Any]]:
        raw = None
        system = platform.system()
        if system == "Darwin":
            raw = self._read_keychain()
            if not raw and self.state_file.exists():
                raise RuntimeError("macOS Keychain key record is unreadable")
        elif system == "Windows":
            raw = self._read_dpapi()
            if not raw and self.secret_file.exists():
                raise RuntimeError("Windows DPAPI key record is unreadable")
        elif self.secret_file.exists():
            raw = self.secret_file.read_text(encoding="utf-8")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("native helper key store is corrupted") from exc

    def _write_metadata(self, identity: DeviceIdentity) -> None:
        self._write_file(
            self.state_file,
            json.dumps(
                {"device_id": identity.device_id, "browser_family": identity.browser_family},
                ensure_ascii=True,
                sort_keys=True,
            ),
        )

    def _write_file(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)

    def _read_keychain(self) -> Optional[str]:
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    self.keychain_account,
                    "-s",
                    self.keychain_service,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.CalledProcessError):
            return None

    def _save_keychain(self, value: str) -> bool:
        try:
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-a",
                    self.keychain_account,
                    "-s",
                    self.keychain_service,
                    "-w",
                    value,
                    "-U",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    @staticmethod
    def _dpapi(value: bytes, *, protect: bool) -> Optional[bytes]:
        """Use the current Windows user profile to protect the key record."""
        if platform.system() != "Windows":
            return None

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        source = ctypes.create_string_buffer(value)
        input_blob = DATA_BLOB(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
        output_blob = DATA_BLOB()
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(input_blob), "XianyuNativeHelper", None, None, None, 0,
                ctypes.byref(output_blob),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob), None, None, None, None, 0,
                ctypes.byref(output_blob),
            )
        if not ok:
            return None
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def _save_dpapi(self, value: str) -> bool:
        protected = self._dpapi(value.encode("utf-8"), protect=True)
        if protected is None:
            return False
        self._write_file(self.secret_file, protected.hex())
        return True

    def _read_dpapi(self) -> Optional[str]:
        try:
            raw = bytes.fromhex(self.secret_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        unprotected = self._dpapi(raw, protect=False)
        return unprotected.decode("utf-8") if unprotected else None


__all__ = ["IdentityStore", "default_state_dir"]
