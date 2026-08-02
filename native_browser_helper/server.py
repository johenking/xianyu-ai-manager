"""Loopback HTTP API for the native browser helper."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from . import HELPER_VERSION
from .helper import NativeBrowserHelper, NativeHelperError


MAX_BODY_BYTES = 64 * 1024


class HelperHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], helper: NativeBrowserHelper):
        self.helper = helper
        super().__init__(address, HelperRequestHandler)


class HelperRequestHandler(BaseHTTPRequestHandler):
    server: HelperHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _cors_origin(self) -> Optional[str]:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        try:
            from .helper import _origin

            normalized = _origin(origin)
        except NativeHelperError:
            return None
        return origin if normalized in self.server.helper.allowed_origins else None

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin:
            return self._cors_origin() is not None
        return self.command == "GET" and urlsplit(self.path).path == "/health"

    def _send(self, status: int, payload: dict[str, Any], *, cors: bool = True) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if cors:
            origin = self._cors_origin()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "false")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: NativeHelperError) -> None:
        self._send(exc.status, {"success": False, "error": {"code": exc.code, "message": str(exc)}})

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise NativeHelperError("请求格式无效", code="invalid_request") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise NativeHelperError("请求体过大", code="request_too_large", status=413)
        try:
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeHelperError("请求格式无效", code="invalid_request") from exc
        if not isinstance(value, dict):
            raise NativeHelperError("请求格式无效", code="invalid_request")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send(403, {"success": False, "error": {"code": "origin_not_allowed", "message": "来源不在允许列表"}})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send(403, {"success": False, "error": {"code": "origin_not_allowed", "message": "来源不在允许列表"}})
            return
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/health":
                self._send(200, {"ok": True, "service": "xianyu-native-browser-helper", "version": HELPER_VERSION})
                return
            if parsed.path == "/v1/device":
                self._send(200, {"success": True, "data": self.server.helper.device_record()})
                return
            if parsed.path == "/v1/login/status":
                session_id = str((parse_qs(parsed.query).get("session_id") or [""])[0])
                self._send(200, {"success": True, "data": self.server.helper.status(session_id)})
                return
            if parsed.path.startswith("/v1/login/status/"):
                session_id = parsed.path.rsplit("/", 1)[-1]
                self._send(200, {"success": True, "data": self.server.helper.status(session_id)})
                return
            raise NativeHelperError("接口不存在", code="not_found", status=404)
        except NativeHelperError as exc:
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send(403, {"success": False, "error": {"code": "origin_not_allowed", "message": "来源不在允许列表"}})
            return
        try:
            payload = self._read_json()
            path = urlsplit(self.path).path
            if path == "/v1/login/start":
                value = self.server.helper.start(payload)
            elif path == "/v1/login/cancel":
                value = self.server.helper.cancel(str(payload.get("session_id") or ""))
            elif path == "/v1/login/close":
                value = self.server.helper.close(
                    str(payload.get("session_id") or ""),
                    account_id=str(payload.get("account_id") or ""),
                )
            else:
                raise NativeHelperError("接口不存在", code="not_found", status=404)
            self._send(200, {"success": True, "data": value})
        except NativeHelperError as exc:
            self._error(exc)


def run_server(
    helper: NativeBrowserHelper,
    *,
    host: str = "127.0.0.1",
    port: int = 17890,
) -> HelperHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("native helper must bind to loopback")
    server = HelperHTTPServer((host, int(port)), helper)
    server.serve_forever()
    return server


__all__ = ["HelperHTTPServer", "HelperRequestHandler", "run_server"]
