import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudflared_watchdog import (
    WatchdogConfig,
    load_state,
    restart_launchd,
    run_once,
    save_state,
)


class CloudflaredWatchdogTests(unittest.TestCase):
    @staticmethod
    def _config(root: Path, **overrides) -> WatchdogConfig:
        values = {
            "state_path": root / "watchdog-state.json",
            "metrics_url": "http://127.0.0.1:20241/ready",
            "failure_threshold": 2,
            "cooldown_seconds": 180,
            "recovery_timeout": 10,
            "poll_interval": 1,
        }
        values.update(overrides)
        return WatchdogConfig(**values)

    def test_healthy_connector_clears_failures_without_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            save_state(config.state_path, {"consecutive_failures": 1, "last_restart_at": 12})
            restart = Mock(return_value=True)

            result = run_once(
                config,
                probe_fn=lambda *_: {"status": 200, "readyConnections": 2},
                restart_fn=restart,
                now_fn=lambda: 100,
            )

            self.assertEqual(result["event"], "healthy")
            self.assertEqual(result["connections"], 2)
            self.assertEqual(load_state(config.state_path)["consecutive_failures"], 0)
            restart.assert_not_called()

    def test_second_consecutive_failure_restarts_and_confirms_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            first = run_once(
                config,
                probe_fn=lambda *_: None,
                restart_fn=Mock(return_value=True),
                now_fn=lambda: 100,
            )
            self.assertEqual(first["event"], "degraded")

            probes = iter([None, {"status": 200, "readyConnections": 2}])
            restart = Mock(return_value=True)
            second = run_once(
                config,
                probe_fn=lambda *_: next(probes),
                restart_fn=restart,
                sleep_fn=lambda _seconds: None,
                now_fn=lambda: 101,
            )

            self.assertEqual(second["event"], "recovered")
            self.assertTrue(second["restarted"])
            self.assertEqual(second["connections"], 2)
            self.assertEqual(load_state(config.state_path)["consecutive_failures"], 0)
            restart.assert_called_once_with(config.label)

    def test_cooldown_prevents_restart_storm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            save_state(config.state_path, {"consecutive_failures": 1, "last_restart_at": 90})
            restart = Mock(return_value=True)

            result = run_once(
                config,
                probe_fn=lambda *_: None,
                restart_fn=restart,
                now_fn=lambda: 100,
            )

            self.assertEqual(result["event"], "cooldown")
            self.assertEqual(result["retry_after"], 170)
            restart.assert_not_called()

    def test_failed_restart_is_bounded_and_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            save_state(config.state_path, {"consecutive_failures": 1, "last_restart_at": 0})

            result = run_once(
                config,
                probe_fn=lambda *_: None,
                restart_fn=Mock(return_value=False),
                now_fn=lambda: 200,
            )

            self.assertEqual(result["event"], "restart_failed")
            state = load_state(config.state_path)
            self.assertEqual(state["consecutive_failures"], 2)
            self.assertEqual(state["last_restart_at"], 200)

    def test_state_file_is_private_and_malformed_state_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "state.json"
            path.parent.mkdir()
            path.write_text("not-json", encoding="utf-8")

            self.assertEqual(
                load_state(path),
                {"consecutive_failures": 0, "last_restart_at": 0},
            )
            save_state(path, {"consecutive_failures": 1, "last_restart_at": 2})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["consecutive_failures"], 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_restart_targets_only_the_cloudflared_launchd_job(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("cloudflared_watchdog.subprocess.run", return_value=completed) as run:
            self.assertTrue(restart_launchd("com.sub2api.cloudflared"))

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["launchctl", "kickstart", "-k"])
        self.assertEqual(command[3], f"gui/{os.getuid()}/com.sub2api.cloudflared")
        self.assertNotIn("com.cxywjx.xianyu-manager", command)

    def test_launchd_template_is_periodic_bounded_and_secret_free(self):
        root = Path(__file__).resolve().parents[1]
        template = root / "ops" / "launchd" / "com.cxywjx.cloudflared-watchdog.plist.template"
        payload = plistlib.loads(template.read_bytes())

        self.assertEqual(payload["Label"], "com.cxywjx.cloudflared-watchdog")
        self.assertEqual(payload["StartInterval"], 60)
        self.assertEqual(payload["ThrottleInterval"], 30)
        self.assertTrue(payload["RunAtLoad"])
        self.assertNotIn("KeepAlive", payload)
        arguments = payload["ProgramArguments"]
        self.assertIn("--failure-threshold", arguments)
        self.assertIn("--cooldown-seconds", arguments)
        self.assertNotIn("--token", arguments)
        self.assertNotIn("--token-file", arguments)


if __name__ == "__main__":
    unittest.main()
