import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from native_browser_helper.helper import (
    NativeBrowserHelper,
    NativeHelperError,
    _cookies_for_import,
)
from native_browser_helper.cdp import BrowserLauncher, CDPClient
from native_browser_helper.keystore import IdentityStore
from native_browser_helper.protocol import DeviceIdentity
from native_browser_helper.server import HelperHTTPServer


class FakeIdentityStore:
    def __init__(self, identity):
        self.identity = identity

    def load_or_create(self, _browser_family):
        return self.identity


class FakeClient:
    def __init__(self):
        self.closed = False
        self.calls = 0
        self.target_id = "helper-target-fixture"
        self.close_target_calls = 0

    def location(self):
        return "https://www.goofish.com/"

    def cookies(self):
        self.calls += 1
        return [
            {
                "name": "unb",
                "value": "account-1",
                "domain": ".goofish.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 4_000_000_000,
            },
            {
                "name": "cookie2",
                "value": "fixture-session",
                "domain": ".goofish.com",
                "path": "/",
            },
        ]

    def user_agent(self):
        return "Mozilla/5.0 Fixture Chrome"

    def navigate(self, _url):
        return None

    def close_target(self):
        self.close_target_calls += 1
        self.closed = True


class FakeLauncher:
    def __init__(self, client):
        self.client = client
        self.open_calls = []
        self.close_calls = []

    def open(self, browser_family, url):
        self.open_calls.append((browser_family, url))
        return self.client

    def close(self, client):
        self.close_calls.append(client)
        client.closed = True


class BrowserLauncherPlatformTests(unittest.TestCase):
    def test_macos_and_windows_browser_commands_use_the_helper_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            with (
                patch("native_browser_helper.cdp.platform.system", return_value="Darwin"),
                patch("native_browser_helper.cdp.Path.exists", return_value=True),
            ):
                mac_path = launcher._executable("chrome")
            self.assertEqual(
                mac_path,
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )

            with (
                patch("native_browser_helper.cdp.platform.system", return_value="Windows"),
                patch.dict(
                    "native_browser_helper.cdp.os.environ",
                    {"LOCALAPPDATA": "C:/Users/Fixture/AppData/Local"},
                    clear=False,
                ),
                patch("native_browser_helper.cdp.Path.exists", return_value=True),
            ):
                windows_path = launcher._executable("chrome")
            self.assertTrue(windows_path.endswith("Google/Chrome/Application/chrome.exe"))

    def test_managed_profile_creates_an_owned_official_page_and_closes_initial_blank(self):
        client = FakeClient()
        client.target_id = "official-target"
        initial_targets = [
            {
                "id": "blank",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/blank",
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            with (
                patch("native_browser_helper.cdp._json_get", return_value=initial_targets),
                patch("native_browser_helper.cdp.CDPClient.create_target", return_value=client) as create_target,
                patch("native_browser_helper.cdp._http_request", return_value=b"Target is closing") as close_target,
            ):
                selected = launcher._create_managed_target(9222, "https://www.goofish.com/")
        self.assertEqual(selected.target_id, "official-target")
        create_target.assert_called_once_with(9222, "https://www.goofish.com/")
        close_target.assert_called_once_with(9222, "/json/close/blank")

    def test_existing_debug_endpoint_is_preferred_over_launch(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            with (
                patch.object(launcher, "_existing_endpoint", return_value=9222),
                patch("native_browser_helper.cdp.CDPClient.create_target", return_value=client) as create_target,
                patch("native_browser_helper.cdp.CDPClient.connect") as connect,
                patch.object(launcher, "_executable") as executable,
            ):
                opened = launcher.open("chrome", "https://www.goofish.com/")
        self.assertIs(opened, client)
        create_target.assert_called_once_with(9222, "https://www.goofish.com/")
        connect.assert_not_called()
        executable.assert_not_called()

    def test_close_only_closes_a_helper_owned_target(self):
        owned = FakeClient()
        unrelated = FakeClient()
        unrelated.target_id = "user-existing-tab"
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            launcher._owned_clients.add(owned.target_id)
            launcher.close(unrelated)
            launcher.close(owned)
        self.assertEqual(unrelated.close_target_calls, 0)
        self.assertEqual(owned.close_target_calls, 1)

    def test_create_target_uses_new_tab_endpoint_and_activates_it(self):
        target = {
            "id": "new-target-fixture",
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/new-target-fixture",
        }
        websocket = unittest.mock.MagicMock()
        websocket.recv.side_effect = [
            json.dumps({"id": 1, "result": {"frameId": "frame-fixture"}}),
        ]
        with (
            patch("native_browser_helper.cdp._json_request", return_value=target) as json_request,
            patch("native_browser_helper.cdp._http_request", return_value=b"Target activated") as http_request,
            patch("native_browser_helper.cdp.websocket.create_connection", return_value=websocket) as connect,
        ):
            client = CDPClient.create_target(9222, "https://www.goofish.com/login?a=1")
        json_request.assert_called_once_with(
            9222,
            "/json/new?https%3A%2F%2Fwww.goofish.com%2Flogin%3Fa%3D1",
            method="PUT",
        )
        websocket.send.assert_called_once_with(json.dumps({
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": "https://www.goofish.com/login?a=1"},
        }))
        http_request.assert_called_once_with(9222, "/json/activate/new-target-fixture")
        connect.assert_called_once_with(
            target["webSocketDebuggerUrl"],
            timeout=8,
            suppress_origin=True,
        )
        self.assertTrue(client.owned_target)

    def test_runtime_evaluate_reads_the_cdp_remote_object_value(self):
        websocket = unittest.mock.MagicMock()
        websocket.recv.return_value = json.dumps({
            "id": 1,
            "result": {
                "result": {
                    "type": "string",
                    "value": "https://www.goofish.com/",
                },
            },
        })
        target = {
            "id": "target-fixture",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/target-fixture",
        }
        with patch("native_browser_helper.cdp.websocket.create_connection", return_value=websocket):
            client = CDPClient(9222, target)
            self.assertEqual(client.location(), "https://www.goofish.com/")

    def test_managed_browser_process_stops_after_its_last_owned_target_closes(self):
        client = FakeClient()
        process = unittest.mock.MagicMock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            launcher.process = process
            launcher._owned_clients.add(client.target_id)
            launcher.close(client)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        process.kill.assert_not_called()
        self.assertIsNone(launcher.process)


class CookieAndKeyStoreTests(unittest.TestCase):
    def test_cookie_snapshot_only_contains_platform_domains(self):
        filtered = _cookies_for_import([
            {"name": "unb", "value": "1", "domain": ".goofish.com"},
            {"name": "cookie2", "value": "2", "domain": "login.taobao.com"},
            {"name": "private", "value": "3", "domain": "mail.example.com"},
            {"name": "suffix-trick", "value": "4", "domain": "notgoofish.com"},
        ])
        self.assertEqual([item["name"] for item in filtered], ["unb", "cookie2"])

    def test_windows_dpapi_failure_never_writes_plaintext_key(self):
        with tempfile.TemporaryDirectory() as temp:
            store = IdentityStore(Path(temp))
            identity = DeviceIdentity.generate("chrome")
            with (
                patch("native_browser_helper.keystore.platform.system", return_value="Windows"),
                patch.object(store, "_save_dpapi", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "DPAPI"):
                    store.save(identity)
            self.assertFalse(store.secret_file.exists())

    def test_macos_keychain_failure_never_writes_plaintext_key(self):
        with tempfile.TemporaryDirectory() as temp:
            store = IdentityStore(Path(temp))
            identity = DeviceIdentity.generate("chrome")
            with (
                patch("native_browser_helper.keystore.platform.system", return_value="Darwin"),
                patch.object(store, "_save_keychain", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "Keychain"):
                    store.save(identity)
            self.assertFalse(store.secret_file.exists())


class NativeBrowserHelperTests(unittest.TestCase):
    def setUp(self):
        self.identity = DeviceIdentity.generate("chrome")
        self.client = FakeClient()
        self.launcher = FakeLauncher(self.client)
        self.requests = []

        def request(method, url, payload=None):
            self.requests.append((method, url, payload))
            if url.endswith("/challenge"):
                return {
                    "success": True,
                    "data": {
                        "challenge_id": "challenge-fixture",
                        "device_id": self.identity.device_id,
                        "purpose": "login_import",
                        "nonce": "nonce-fixture",
                        "expires_at": time.time() + 30,
                    },
                }
            if url.endswith("/import"):
                return {
                    "success": True,
                    "data": {
                        "session_id": "session-fixture",
                        "account_id": "account-1",
                        "state": "awaiting_confirmation",
                    },
                }
            raise AssertionError(url)

        self.request = request
        self.temp_dir = tempfile.TemporaryDirectory()
        self.helper = NativeBrowserHelper(
            browser_family="chrome",
            state_dir=Path(self.temp_dir.name),
            allowed_origins={"http://127.0.0.1:8091", "https://xianyu.cxywjx.top"},
            launcher=self.launcher,
            identity_store=FakeIdentityStore(self.identity),
            request=request,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_proofed_import_reaches_awaiting_confirmation_without_exposing_cookies(self):
        initial = self.helper.start(
            {
                "session_id": "session-fixture",
                "device_id": self.identity.device_id,
                "mode": "qr",
                "server_origin": "http://127.0.0.1:8091",
                "expires_at": time.time() + 20,
            }
        )
        self.assertEqual(initial["state"], "opening_browser")
        deadline = time.time() + 3
        status = self.helper.status("session-fixture")
        while status["state"] != "awaiting_confirmation" and time.time() < deadline:
            time.sleep(0.02)
            status = self.helper.status("session-fixture")
        self.assertEqual(status["state"], "awaiting_confirmation")
        self.assertEqual(status["account_id"], "account-1")
        self.assertNotIn("fixture-session", json.dumps(status))
        self.assertEqual(self.launcher.open_calls[0][1], "https://www.goofish.com/")
        import_payload = self.requests[-1][2]
        self.assertEqual(import_payload["device_id"], self.identity.device_id)
        self.assertEqual(import_payload["cookies"][0]["name"], "unb")
        self.assertNotIn("fixture-session", json.dumps(status))

        closed = self.helper.close("session-fixture", account_id="account-1")
        self.assertEqual(closed["state"], "success")
        self.assertEqual(self.launcher.close_calls, [self.client])

    def test_origin_and_device_are_checked_before_browser_start(self):
        with self.assertRaises(NativeHelperError) as origin:
            self.helper.start(
                {
                    "session_id": "session-fixture",
                    "device_id": self.identity.device_id,
                    "mode": "qr",
                    "server_origin": "https://unexpected.example",
                    "expires_at": time.time() + 20,
                }
            )
        self.assertEqual(origin.exception.code, "origin_not_allowed")
        with self.assertRaises(NativeHelperError) as device:
            self.helper.start(
                {
                    "session_id": "session-fixture",
                    "device_id": "other-device-fixture",
                    "mode": "qr",
                    "server_origin": "http://127.0.0.1:8091",
                    "expires_at": time.time() + 20,
                }
            )
        self.assertEqual(device.exception.code, "device_mismatch")
        self.assertEqual(self.launcher.open_calls, [])

    def test_loopback_api_serves_health_and_device_without_private_material(self):
        server = HelperHTTPServer(("127.0.0.1", 0), self.helper)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                health = json.loads(response.read())
            device_request = Request(
                f"http://127.0.0.1:{port}/v1/device",
                headers={"Origin": "http://127.0.0.1:8091"},
            )
            with urlopen(device_request, timeout=2) as response:
                device = json.loads(response.read())
            self.assertTrue(health["ok"])
            self.assertEqual(health["version"], "1.0.1")
            self.assertEqual(device["data"]["clientType"], "native_helper")
            self.assertNotIn("private", json.dumps(device))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_private_api_rejects_a_request_without_console_origin(self):
        server = HelperHTTPServer(("127.0.0.1", 0), self.helper)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as rejected:
                urlopen(
                    f"http://127.0.0.1:{server.server_address[1]}/v1/device",
                    timeout=2,
                )
            self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_private_network_cors_allows_configured_https_origin(self):
        server = HelperHTTPServer(("127.0.0.1", 0), self.helper)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = Request(
                f"http://127.0.0.1:{port}/v1/device",
                method="OPTIONS",
                headers={
                    "Origin": "https://xianyu.cxywjx.top",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Private-Network": "true",
                },
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(
                    response.headers["Access-Control-Allow-Origin"],
                    "https://xianyu.cxywjx.top",
                )
                self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")

            request = Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Origin": "https://xianyu.cxywjx.top"},
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_private_network_cors_rejects_unconfigured_origin(self):
        server = HelperHTTPServer(("127.0.0.1", 0), self.helper)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/device",
                method="OPTIONS",
                headers={"Origin": "https://unexpected.example"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=2)
            self.assertEqual(rejected.exception.code, 403)
            self.assertIsNone(rejected.exception.headers.get("Access-Control-Allow-Origin"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_repeated_start_reuses_the_active_attempt(self):
        payload = {
            "session_id": "session-fixture",
            "device_id": self.identity.device_id,
            "mode": "qr",
            "server_origin": "http://127.0.0.1:8091",
            "expires_at": time.time() + 20,
        }
        first = self.helper.start(payload)
        second = self.helper.start(payload)
        self.assertEqual(first["session_id"], second["session_id"])
        deadline = time.time() + 1
        while not self.launcher.open_calls and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.launcher.open_calls), 1)

    def test_close_rejects_a_different_confirmed_account(self):
        self.helper.start({
            "session_id": "session-fixture",
            "device_id": self.identity.device_id,
            "mode": "qr",
            "server_origin": "http://127.0.0.1:8091",
            "expires_at": time.time() + 20,
        })
        deadline = time.time() + 3
        status = self.helper.status("session-fixture")
        while status["state"] != "awaiting_confirmation" and time.time() < deadline:
            time.sleep(0.02)
            status = self.helper.status("session-fixture")
        with self.assertRaises(NativeHelperError) as mismatch:
            self.helper.close("session-fixture", account_id="different-account")
        self.assertEqual(mismatch.exception.code, "account_mismatch")

    def test_retryable_import_failure_requests_a_fresh_challenge(self):
        challenge_count = 0
        import_count = 0

        def request(method, url, payload=None):
            nonlocal challenge_count, import_count
            if url.endswith("/challenge"):
                challenge_count += 1
                return {
                    "success": True,
                    "data": {
                        "challenge_id": f"challenge-{challenge_count}",
                        "device_id": self.identity.device_id,
                        "purpose": "login_import",
                        "nonce": f"nonce-{challenge_count}",
                        "expires_at": time.time() + 30,
                    },
                }
            if url.endswith("/import"):
                import_count += 1
                if import_count == 1:
                    raise NativeHelperError(
                        "平台连接暂时异常",
                        code="session_probe_retryable",
                        status=503,
                    )
                return {
                    "success": True,
                    "data": {
                        "session_id": "session-fixture",
                        "account_id": "account-1",
                        "state": "awaiting_confirmation",
                    },
                }
            raise AssertionError((method, url, payload))

        self.helper.request = request
        with patch("native_browser_helper.helper.time.sleep", return_value=None):
            self.helper.start({
                "session_id": "session-fixture",
                "device_id": self.identity.device_id,
                "mode": "qr",
                "server_origin": "http://127.0.0.1:8091",
                "expires_at": time.time() + 20,
            })
            deadline = time.time() + 3
            status = self.helper.status("session-fixture")
            while status["state"] != "awaiting_confirmation" and time.time() < deadline:
                time.sleep(0.01)
                status = self.helper.status("session-fixture")

        self.assertEqual(status["state"], "awaiting_confirmation")
        self.assertEqual(challenge_count, 2)
        self.assertEqual(import_count, 2)


if __name__ == "__main__":
    unittest.main()
