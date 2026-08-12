"""Per-user installation and startup registration for packaged helpers."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from . import DEFAULT_PORT, HELPER_VERSION, PROTOCOL_VERSION
from .keystore import default_state_dir


SERVICE_LABEL = "com.cxywjx.xianyu-native-helper"
WINDOWS_RUN_VALUE = "XianyuNativeBrowserHelper"


class InstallerError(RuntimeError):
    pass


def _normalized_architecture(value: Optional[str] = None) -> str:
    raw = str(value or platform.machine() or "").strip().lower()
    if raw in {"arm64", "aarch64", "arm64e"}:
        return "arm64"
    if raw in {"x86_64", "amd64", "x64"}:
        return "x64"
    if raw in {"i386", "i686", "x86", "x32"}:
        return "x86"
    return raw or "unknown"


@dataclass(frozen=True)
class InstallStatus:
    platform: str
    version: str
    installed: bool
    startup_registered: bool
    running: bool
    install_path: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def _helper_health(port: int = DEFAULT_PORT, *, timeout: float = 0.5) -> Optional[dict[str, Any]]:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/health",
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None
    if payload.get("service") != "xianyu-native-browser-helper":
        return None
    return payload


def wait_for_helper(
    *,
    port: int = DEFAULT_PORT,
    version: str = HELPER_VERSION,
    timeout: float = 8.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = _helper_health(port)
        if health and str(health.get("version") or "") == version:
            return True
        time.sleep(0.15)
    return False


class NativeHelperInstaller:
    def __init__(
        self,
        *,
        system: Optional[str] = None,
        executable: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
        port: int = DEFAULT_PORT,
        state_dir: Optional[Path] = None,
    ):
        self.system = system or platform.system()
        self.executable = Path(executable or sys.executable).resolve()
        self.environ = dict(os.environ if environ is None else environ)
        self.port = int(port)
        if self.port < 1 or self.port > 65535:
            raise InstallerError("本机助手端口无效")
        self.state_dir = Path(
            state_dir
            or self.environ.get("XMC_HELPER_STATE_DIR")
            or default_state_dir()
        ).expanduser()

    def install_path(self) -> Path:
        override = str(self.environ.get("XMC_HELPER_INSTALL_ROOT") or "").strip()
        if override:
            root = Path(override).expanduser()
            return root / (
                "XianyuNativeHelper.app"
                if self.system == "Darwin"
                else "XianyuNativeHelper.exe"
            )
        if self.system == "Darwin":
            return Path.home() / "Applications" / "XianyuNativeHelper.app"
        if self.system == "Windows":
            local = Path(self.environ.get("LOCALAPPDATA", str(Path.home())))
            return (
                local
                / "XianyuNativeHelper"
                / f"XianyuNativeHelper-{HELPER_VERSION}.exe"
            )
        raise InstallerError("本机助手安装包仅支持 macOS 和 Windows")

    def installed_executable(self) -> Path:
        path = self.install_path()
        if self.system == "Darwin":
            return path / "Contents" / "MacOS" / "XianyuNativeHelper"
        return path

    def launch_agent_path(self) -> Path:
        override = str(self.environ.get("XMC_HELPER_LAUNCH_AGENT_PATH") or "").strip()
        if override:
            return Path(override).expanduser()
        return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"

    def status(self) -> InstallStatus:
        diagnostics = self.health_dict()
        return InstallStatus(
            platform=str(diagnostics["platform"]),
            version=HELPER_VERSION,
            installed=bool(diagnostics["installed"]),
            startup_registered=bool(diagnostics["startupRegistered"]),
            running=bool(diagnostics["running"]),
            install_path=str(self.install_path()),
        )

    def health_dict(self, *, running: Optional[bool] = None) -> dict[str, Any]:
        """Return the bounded, secret-free state exposed by the loopback health check."""
        installed = self.installed_executable().is_file()
        if self.system == "Darwin":
            startup_registered = self.launch_agent_path().is_file()
        elif self.system == "Windows":
            startup_registered = self._windows_run_value() == self._windows_command()
        else:
            startup_registered = False
        if running is None:
            health = _helper_health(self.port)
            running = bool(health and health.get("version") == HELPER_VERSION)
        return {
            "platform": self.system,
            "arch": _normalized_architecture(),
            "protocolVersion": PROTOCOL_VERSION,
            "installed": installed,
            "startupRegistered": startup_registered,
            "running": bool(running),
        }

    def install_and_start(self) -> InstallStatus:
        if not is_packaged() and not self.environ.get("XMC_HELPER_ALLOW_SOURCE_INSTALL"):
            raise InstallerError("请使用正式本机助手安装包")
        if self.system == "Darwin":
            self._install_macos()
        elif self.system == "Windows":
            self._install_windows()
        else:
            raise InstallerError("本机助手安装包仅支持 macOS 和 Windows")
        if not wait_for_helper(port=self.port):
            health = _helper_health(self.port)
            if health:
                raise InstallerError(
                    "检测到旧版助手仍在运行，请退出旧版助手或重启电脑后再试"
                )
            raise InstallerError("助手已安装，但启动检查未通过")
        return self.status()

    def uninstall(self) -> InstallStatus:
        if self.system == "Darwin":
            self._bootout_macos()
            self._wait_until_stopped()
            self.launch_agent_path().unlink(missing_ok=True)
            target = self.install_path()
            if target.exists():
                shutil.rmtree(target)
        elif self.system == "Windows":
            self._delete_windows_run_value()
            if _helper_health(self.port):
                self._stop_windows_service()
            target = self.install_path()
            if target.exists():
                if target == self.executable:
                    self._schedule_windows_self_delete(target)
                else:
                    target.unlink()
        else:
            raise InstallerError("本机助手安装包仅支持 macOS 和 Windows")
        return self.status()

    def _mac_source_bundle(self) -> Path:
        for parent in (self.executable, *self.executable.parents):
            if parent.suffix == ".app":
                return parent
        raise InstallerError("当前文件不是有效的 macOS 应用包")

    @staticmethod
    def _replace_tree(source: Path, target: Path) -> None:
        if source.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.installing-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging, symlinks=True)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)

    def _install_macos(self) -> None:
        target = self.install_path()
        self._bootout_macos()
        self._wait_until_stopped()
        self._replace_tree(self._mac_source_bundle(), target)
        executable = self.installed_executable()
        if not executable.is_file():
            raise InstallerError("macOS 助手复制后缺少可执行文件")
        state_dir = self.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        plist_path = self.launch_agent_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": SERVICE_LABEL,
            "ProgramArguments": [
                str(executable),
                "--serve",
                "--port",
                str(self.port),
                "--state-dir",
                str(state_dir),
            ],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Background",
            "StandardOutPath": str(state_dir / "helper.log"),
            "StandardErrorPath": str(state_dir / "helper.error.log"),
        }
        environment_variables = {
            key: str(self.environ[key])
            for key in (
                "XMC_HELPER_KEYCHAIN_SERVICE",
                "XMC_HELPER_KEYCHAIN_ACCOUNT",
            )
            if str(self.environ.get(key) or "").strip()
        }
        if environment_variables:
            payload["EnvironmentVariables"] = environment_variables
        temporary = plist_path.with_suffix(".plist.tmp")
        temporary.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
        temporary.chmod(0o600)
        temporary.replace(plist_path)
        plist_path.chmod(0o600)
        domain = f"gui/{os.getuid()}"
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise InstallerError("macOS 开机启动注册失败")
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"{domain}/{SERVICE_LABEL}"],
            check=False,
            capture_output=True,
        )

    def _bootout_macos(self) -> None:
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"],
            check=False,
            capture_output=True,
        )

    def _install_windows(self) -> None:
        target = self.install_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        health = _helper_health(self.port)
        if health and str(health.get("version") or "") != HELPER_VERSION:
            self._stop_windows_service()
        if self.executable != target:
            staging = target.with_suffix(".exe.installing")
            shutil.copy2(self.executable, staging)
            staging.replace(target)
        self._set_windows_run_value(self._windows_command())
        health = _helper_health(self.port)
        if health and str(health.get("version") or "") == HELPER_VERSION:
            return
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            [
                str(target),
                "--serve",
                "--port",
                str(self.port),
                "--state-dir",
                str(self.state_dir),
            ],
            close_fds=True,
            creationflags=creationflags,
        )

    def _stop_windows_service(self) -> None:
        pid_file = self.state_dir / "helper.pid"
        try:
            record = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = int(record.get("pid") or 0)
            executable = Path(str(record.get("executable") or "")).resolve()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise InstallerError("检测到旧版助手仍在运行，请先退出旧版助手")
        target_root = self.install_path().parent.resolve()
        if (
            pid <= 0
            or executable.parent != target_root
            or not executable.name.lower().startswith("xianyunativehelper")
        ):
            raise InstallerError("旧版助手进程记录无效，请重启电脑后再安装")
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        if result.returncode not in {0, 128}:
            raise InstallerError("旧版助手退出失败，请重启电脑后再安装")
        deadline = time.monotonic() + 5
        while _helper_health(self.port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _helper_health(self.port):
            raise InstallerError("旧版助手退出超时，请重启电脑后再安装")

    def _wait_until_stopped(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while _helper_health(self.port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _helper_health(self.port):
            raise InstallerError("本机助手退出超时，请重启电脑后再试")

    @staticmethod
    def _schedule_windows_self_delete(target: Path) -> None:
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        command = f'ping 127.0.0.1 -n 3 >NUL & del /F /Q "{target}"'
        subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", command],
            close_fds=True,
            creationflags=creationflags,
        )

    def _windows_command(self) -> str:
        return subprocess.list2cmdline(
            [
                str(self.installed_executable()),
                "--serve",
                "--port",
                str(self.port),
                "--state-dir",
                str(self.state_dir),
            ]
        )

    @staticmethod
    def _windows_registry():
        try:
            import winreg
        except ImportError as exc:  # pragma: no cover - Windows-only import
            raise InstallerError("Windows 启动项接口不可用") from exc
        return winreg

    def _windows_run_value(self) -> str:
        if self.system != "Windows":
            return ""
        winreg = self._windows_registry()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                return str(winreg.QueryValueEx(key, WINDOWS_RUN_VALUE)[0] or "")
        except OSError:
            return ""

    def _set_windows_run_value(self, command: str) -> None:
        winreg = self._windows_registry()
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            winreg.SetValueEx(key, WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, command)

    def _delete_windows_run_value(self) -> None:
        winreg = self._windows_registry()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
        except OSError:
            return


def notify_user(message: str, *, error: bool = False) -> None:
    text = str(message or "")[:500]
    if platform.system() == "Darwin":
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                'display dialog (item 1 of argv) with title "本机浏览器助手" buttons {"好"} default button "好"',
                "-e",
                "end run",
                "--",
                text,
            ],
            check=False,
            capture_output=True,
        )
    elif platform.system() == "Windows":  # pragma: no cover - Windows GUI
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "本机浏览器助手", 0x10 if error else 0x40)


__all__ = [
    "InstallStatus",
    "InstallerError",
    "NativeHelperInstaller",
    "SERVICE_LABEL",
    "WINDOWS_RUN_VALUE",
    "is_packaged",
    "notify_user",
    "wait_for_helper",
]
