import json
import plistlib
import subprocess
import sys
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
from native_browser_helper.__main__ import _emit_result
from native_browser_helper.installer import NativeHelperInstaller
from native_browser_helper.cdp import BrowserLauncher, CDPClient
from native_browser_helper.keystore import IdentityStore
from native_browser_helper.protocol import DeviceIdentity
from native_browser_helper.runtime import ServiceAlreadyRunning, ServicePidFile
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


class NativeHelperInstallerTests(unittest.TestCase):
    @staticmethod
    def _mac_app(root: Path) -> Path:
        app = root / "DownloadedHelper.app"
        executable = app / "Contents" / "MacOS" / "XianyuNativeHelper"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture-macos-executable")
        executable.chmod(0o755)
        return app

    def test_macos_first_open_installs_registers_startup_and_starts_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._mac_app(root)
            install_root = root / "Applications"
            launch_agent = root / "LaunchAgents" / "helper.plist"
            state_dir = root / "state"
            installer = NativeHelperInstaller(
                system="Darwin",
                executable=source / "Contents" / "MacOS" / "XianyuNativeHelper",
                environ={
                    "XMC_HELPER_INSTALL_ROOT": str(install_root),
                    "XMC_HELPER_LAUNCH_AGENT_PATH": str(launch_agent),
                    "XMC_HELPER_ALLOW_SOURCE_INSTALL": "1",
                    "XMC_HELPER_KEYCHAIN_SERVICE": "fixture-helper-service",
                    "XMC_HELPER_KEYCHAIN_ACCOUNT": "fixture-helper-account",
                },
                port=17891,
                state_dir=state_dir,
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with (
                patch("native_browser_helper.installer.subprocess.run", return_value=completed) as run,
                patch("native_browser_helper.installer.wait_for_helper", return_value=True),
                patch.object(installer, "_wait_until_stopped") as wait_until_stopped,
                patch("native_browser_helper.installer._helper_health", return_value={
                    "service": "xianyu-native-browser-helper",
                    "version": "1.0.2",
                }),
            ):
                status = installer.install_and_start()

            installed = install_root / "XianyuNativeHelper.app" / "Contents" / "MacOS" / "XianyuNativeHelper"
            self.assertEqual(installed.read_bytes(), b"fixture-macos-executable")
            payload = plistlib.loads(launch_agent.read_bytes())
            self.assertEqual(payload["ProgramArguments"], [
                str(installed),
                "--serve",
                "--port",
                "17891",
                "--state-dir",
                str(state_dir),
            ])
            self.assertTrue(payload["RunAtLoad"])
            self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
            self.assertEqual(payload["EnvironmentVariables"], {
                "XMC_HELPER_KEYCHAIN_ACCOUNT": "fixture-helper-account",
                "XMC_HELPER_KEYCHAIN_SERVICE": "fixture-helper-service",
            })
            self.assertTrue(status.installed)
            self.assertTrue(status.startup_registered)
            self.assertTrue(status.running)
            wait_until_stopped.assert_called_once_with()
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(command[:2] == ["launchctl", "bootstrap"] for command in commands))
            self.assertTrue(any(command[:3] == ["launchctl", "kickstart", "-k"] for command in commands))

    def test_lifecycle_result_file_is_written_without_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "status.json"
            with patch.object(sys, "stdout", None):
                _emit_result({"running": True, "version": "1.0.2"}, target)
            self.assertEqual(
                json.loads(target.read_text()),
                {"running": True, "version": "1.0.2"},
            )
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_service_pid_file_rejects_a_second_live_instance_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            with ServicePidFile(state_dir) as first:
                original = first.path.read_bytes()
                with self.assertRaises(ServiceAlreadyRunning):
                    ServicePidFile(state_dir).__enter__()
                self.assertEqual(first.path.read_bytes(), original)
            self.assertFalse((state_dir / "helper.pid").exists())

    def test_service_pid_file_replaces_a_stale_record(self):
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            pid_file = state_dir / "helper.pid"
            pid_file.write_text(json.dumps({"pid": 999999999, "version": "old"}))
            with ServicePidFile(state_dir):
                record = json.loads(pid_file.read_text())
                self.assertEqual(record["pid"], __import__("os").getpid())
            self.assertFalse(pid_file.exists())

    def test_windows_pid_liveness_probe_never_sends_a_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "helper.pid"
            pid_file.write_text(json.dumps({"pid": 4321, "version": "1.0.2"}))
            record = ServicePidFile(Path(temp))
            with (
                patch("native_browser_helper.runtime.platform.system", return_value="Windows"),
                patch("native_browser_helper.runtime._windows_process_alive", return_value=True) as probe,
                patch("native_browser_helper.runtime.os.kill") as kill,
            ):
                self.assertTrue(record._existing_process_alive())
            probe.assert_called_once_with(4321)
            kill.assert_not_called()

    def test_macos_uninstall_removes_user_install_and_startup_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "Applications" / "XianyuNativeHelper.app"
            (target / "Contents" / "MacOS").mkdir(parents=True)
            (target / "Contents" / "MacOS" / "XianyuNativeHelper").write_bytes(b"fixture")
            launch_agent = root / "LaunchAgents" / "helper.plist"
            launch_agent.parent.mkdir(parents=True)
            launch_agent.write_text("fixture")
            installer = NativeHelperInstaller(
                system="Darwin",
                executable=target / "Contents" / "MacOS" / "XianyuNativeHelper",
                environ={
                    "XMC_HELPER_INSTALL_ROOT": str(root / "Applications"),
                    "XMC_HELPER_LAUNCH_AGENT_PATH": str(launch_agent),
                },
            )
            with (
                patch("native_browser_helper.installer.subprocess.run"),
                patch("native_browser_helper.installer._helper_health", return_value=None),
            ):
                status = installer.uninstall()
            self.assertFalse(target.exists())
            self.assertFalse(launch_agent.exists())
            self.assertFalse(status.installed)
            self.assertFalse(status.startup_registered)

    def test_windows_first_open_copies_versioned_executable_and_registers_user_startup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "download" / "XianyuNativeHelper.exe"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fixture-windows-executable")
            installer = NativeHelperInstaller(
                system="Windows",
                executable=source,
                environ={
                    "LOCALAPPDATA": str(root / "localappdata"),
                    "XMC_HELPER_STATE_DIR": str(root / "state"),
                    "XMC_HELPER_ALLOW_SOURCE_INSTALL": "1",
                },
            )
            run_values: dict[str, str] = {}
            with (
                patch.object(installer, "_set_windows_run_value", side_effect=lambda value: run_values.update(value=value)),
                patch.object(installer, "_windows_run_value", side_effect=lambda: run_values.get("value", "")),
                patch("native_browser_helper.installer.subprocess.Popen") as popen,
                patch("native_browser_helper.installer.wait_for_helper", return_value=True),
                patch("native_browser_helper.installer._helper_health", side_effect=lambda *_args, **_kwargs: (
                    {
                        "service": "xianyu-native-browser-helper",
                        "version": "1.0.2",
                    }
                    if popen.called
                    else None
                )),
            ):
                status = installer.install_and_start()

            installed = (
                root
                / "localappdata"
                / "XianyuNativeHelper"
                / "XianyuNativeHelper-1.0.2.exe"
            )
            self.assertEqual(installed.read_bytes(), b"fixture-windows-executable")
            command = [
                str(installed),
                "--serve",
                "--port",
                "17890",
                "--state-dir",
                str(root / "state"),
            ]
            self.assertEqual(run_values["value"], subprocess.list2cmdline(command))
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], command)
            self.assertTrue(status.installed)
            self.assertTrue(status.startup_registered)
            self.assertTrue(status.running)


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

    def test_windows_browser_lookup_includes_program_files_x86(self):
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            environment = {
                "LOCALAPPDATA": "C:/Users/Fixture/AppData/Local",
                "PROGRAMFILES": "C:/Program Files",
                "PROGRAMFILES(X86)": "C:/Program Files (x86)",
            }
            with (
                patch("native_browser_helper.cdp.platform.system", return_value="Windows"),
                patch.dict("native_browser_helper.cdp.os.environ", environment, clear=False),
                patch(
                    "native_browser_helper.cdp.Path.exists",
                    autospec=True,
                    side_effect=lambda path: "Program Files (x86)" in str(path),
                ),
            ):
                windows_path = launcher._executable("edge")
            self.assertEqual(
                windows_path,
                "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            )

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

    def test_auto_browser_prefers_existing_chrome_then_installed_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            launcher = BrowserLauncher(Path(temp))
            with (
                patch.object(launcher, "_existing_endpoint", side_effect=lambda family: 9222 if family == "chrome" else None),
                patch.object(launcher, "_executable") as executable,
            ):
                self.assertEqual(launcher.resolve_browser_family("auto"), "chrome")
                executable.assert_not_called()
            with (
                patch.object(launcher, "_existing_endpoint", return_value=None),
                patch.object(launcher, "_executable", side_effect=lambda family: "edge.exe" if family == "edge" else None),
            ):
                self.assertEqual(launcher.resolve_browser_family("auto"), "edge")

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
        self.assertEqual(self.launcher.open_calls[0][1], "https://www.goofish.com/login")
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
            self.assertEqual(health["version"], "1.0.2")
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
