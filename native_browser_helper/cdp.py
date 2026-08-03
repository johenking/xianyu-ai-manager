"""Minimal Chrome DevTools Protocol client and local browser launcher."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import websocket


OFFICIAL_HOSTS = {"www.goofish.com", "goofish.com", "www.xianyu.com", "xianyu.com"}


class CDPError(RuntimeError):
    pass


class CDPClient:
    def __init__(self, port: int, target: dict[str, Any], *, owned_target: bool = False):
        self.port = int(port)
        self.target_id = str(target.get("id") or "")
        self.owned_target = bool(owned_target)
        self.ws = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=8,
            suppress_origin=True,
        )
        self._next_id = 0

    @classmethod
    def connect(
        cls,
        port: int,
        *,
        url_hint: str = "",
        owned_target: bool = False,
    ) -> "CDPClient":
        targets = _json_get(port, "/json/list")
        pages = [item for item in targets if item.get("type") == "page"]
        if url_hint:
            pages.sort(key=lambda item: 0 if url_hint in str(item.get("url") or "") else 1)
        if not pages:
            raise CDPError("没有找到 Chrome 页面目标")
        return cls(port, pages[0], owned_target=owned_target)

    @classmethod
    def create_target(cls, port: int, url: str) -> "CDPClient":
        encoded_url = urllib.parse.quote(str(url), safe="")
        target = _json_request(port, f"/json/new?{encoded_url}", method="PUT")
        if (
            not isinstance(target, dict)
            or target.get("type") != "page"
            or not target.get("id")
            or not target.get("webSocketDebuggerUrl")
        ):
            raise CDPError("Chrome 未创建登录标签页")
        client = cls(port, target, owned_target=True)
        client.navigate(url)
        return client

    def call(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self._next_id += 1
        self.ws.send(json.dumps({"id": self._next_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = json.loads(self.ws.recv())
            if response.get("id") != self._next_id:
                continue
            if "error" in response:
                raise CDPError(str(response["error"]))
            return response.get("result") or {}
        raise CDPError("Chrome DevTools 响应超时")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        self.activate()

    def activate(self) -> None:
        _http_request(
            self.port,
            f"/json/activate/{urllib.parse.quote(self.target_id, safe='')}",
        )

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (result.get("result") or {}).get("value")

    def cookies(self) -> list[dict[str, Any]]:
        return list(self.call("Network.getAllCookies").get("cookies") or [])

    def location(self) -> str:
        return str(self.evaluate("location.href") or "")

    def user_agent(self) -> str:
        return str(self.evaluate("navigator.userAgent") or "")

    def close_target(self) -> None:
        try:
            if self.owned_target:
                _http_request(
                    self.port,
                    f"/json/close/{urllib.parse.quote(self.target_id, safe='')}",
                )
        finally:
            self.ws.close()


class BrowserLauncher:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.process: Optional[subprocess.Popen[Any]] = None
        self.profile_dir: Optional[Path] = None
        self._owned_clients: set[str] = set()

    def open(self, browser_family: str, url: str) -> CDPClient:
        endpoint = self._existing_endpoint(browser_family)
        if endpoint:
            client = CDPClient.create_target(endpoint, url)
            self._owned_clients.add(client.target_id)
            return client
        executable = self._executable(browser_family)
        if not executable:
            raise CDPError(f"未找到 {browser_family} 浏览器")
        self.profile_dir = self.state_dir / "profiles" / browser_family
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            executable,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "about:blank",
        ]
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = self._wait_for_port(self.profile_dir)
        client = self._create_managed_target(port, url)
        self._owned_clients.add(client.target_id)
        return client

    def resolve_browser_family(self, preferred: str = "auto") -> str:
        normalized = str(preferred or "auto").strip().lower()
        if normalized in {"chrome", "edge"}:
            return normalized
        if normalized != "auto":
            raise CDPError("仅支持 Chrome 或 Edge")
        for family in ("chrome", "edge"):
            if self._existing_endpoint(family):
                return family
        for family in ("chrome", "edge"):
            if self._executable(family):
                return family
        return "chrome"

    def close(self, client: Optional[CDPClient]) -> None:
        if client and client.target_id in self._owned_clients:
            try:
                client.close_target()
            except Exception:
                pass
            finally:
                self._owned_clients.discard(client.target_id)
        if self.process and not self._owned_clients:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None

    def _create_managed_target(self, port: int, url: str) -> CDPClient:
        deadline = time.monotonic() + 15
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                initial_targets = [
                    item
                    for item in _json_get(port, "/json/list")
                    if item.get("type") == "page"
                ]
                client = CDPClient.create_target(port, url)
                for target in initial_targets:
                    target_id = str(target.get("id") or "")
                    target_url = str(target.get("url") or "")
                    if (
                        target_id
                        and target_id != client.target_id
                        and target_url in {"about:blank", "chrome://newtab/"}
                    ):
                        try:
                            _http_request(
                                port,
                                f"/json/close/{urllib.parse.quote(target_id, safe='')}",
                            )
                        except Exception:
                            pass
                return client
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        raise CDPError(str(last_error or "Chrome DevTools 连接失败"))

    def _wait_for_port(self, profile_dir: Path) -> int:
        active_file = profile_dir / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                port = int(active_file.read_text(encoding="utf-8").splitlines()[0])
                if _json_get(port, "/json/version"):
                    return port
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        raise CDPError("Chrome DevTools 端口启动超时")

    def _existing_endpoint(self, browser_family: str) -> Optional[int]:
        explicit = os.environ.get("XMC_CDP_PORT")
        if explicit:
            try:
                port = int(explicit)
                if _json_get(port, "/json/version"):
                    return port
            except (ValueError, urllib.error.URLError):
                pass
        for path in self._profile_candidates(browser_family):
            try:
                port = int((path / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[0])
                if _json_get(port, "/json/version"):
                    return port
            except (OSError, ValueError, urllib.error.URLError):
                continue
        return None

    @staticmethod
    def _profile_candidates(browser_family: str) -> list[Path]:
        home = Path.home()
        if platform.system() == "Darwin":
            base = home / "Library" / "Application Support"
            names = {"chrome": "Google/Chrome/User Data", "edge": "Microsoft Edge/User Data"}
            return [base / names[browser_family]]
        if platform.system() == "Windows":
            base = Path(os.environ.get("LOCALAPPDATA", home))
            names = {"chrome": "Google/Chrome/User Data", "edge": "Microsoft/Edge/User Data"}
            return [base / names[browser_family]]
        return [home / ".config" / ("google-chrome" if browser_family == "chrome" else "microsoft-edge")]

    @staticmethod
    def _executable(browser_family: str) -> Optional[str]:
        env_key = "XMC_CHROME_PATH" if browser_family == "chrome" else "XMC_EDGE_PATH"
        if os.environ.get(env_key):
            return os.environ[env_key]
        system = platform.system()
        candidates: list[Path] = []
        if system == "Darwin":
            app = "Google Chrome" if browser_family == "chrome" else "Microsoft Edge"
            relative = Path(f"{app}.app/Contents/MacOS/{app}")
            candidates.extend([Path("/Applications") / relative, Path.home() / "Applications" / relative])
        elif system == "Windows":
            local = Path(os.environ.get("LOCALAPPDATA", ""))
            program = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            program_x86 = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
            folder = "Google/Chrome/Application/chrome.exe" if browser_family == "chrome" else "Microsoft/Edge/Application/msedge.exe"
            candidates.extend([local / folder, program / folder, program_x86 / folder])
        else:
            candidates.extend([Path("/usr/bin/google-chrome"), Path("/usr/bin/microsoft-edge")])
        return next((str(path) for path in candidates if path.exists()), None)


def _json_request(port: int, path: str, *, method: str = "GET") -> Any:
    raw = _http_request(port, path, method=method)
    return json.loads(raw.decode("utf-8"))


def _http_request(port: int, path: str, *, method: str = "GET") -> bytes:
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}{path}",
        method=method,
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.read()


def _json_get(port: int, path: str) -> Any:
    return _json_request(port, path)


def is_official_url(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host in OFFICIAL_HOSTS


__all__ = ["BrowserLauncher", "CDPClient", "CDPError", "is_official_url"]
