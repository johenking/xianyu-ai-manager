"""Runtime lifecycle records for the packaged loopback service."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Optional

from . import HELPER_VERSION
from .keystore import default_state_dir


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class ServicePidFile:
    def __init__(self, state_dir: Optional[Path] = None):
        self.path = Path(state_dir or default_state_dir()) / "helper.pid"

    def __enter__(self) -> "ServicePidFile":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "version": HELPER_VERSION,
                "executable": str(Path(sys.executable).resolve()),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        for _attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._existing_process_alive():
                    raise ServiceAlreadyRunning("本机浏览器助手已在运行")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            return self
        raise RuntimeError("本机浏览器助手运行记录创建失败")

    def __exit__(self, *_exc_info) -> None:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if int(record.get("pid") or 0) == os.getpid():
            self.path.unlink(missing_ok=True)

    def _existing_process_alive(self) -> bool:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(record.get("pid") or 0)
            if pid <= 0:
                return False
            if platform.system() == "Windows":
                return _windows_process_alive(pid)
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False


class ServiceAlreadyRunning(RuntimeError):
    pass


__all__ = ["ServiceAlreadyRunning", "ServicePidFile"]
