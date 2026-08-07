"""Recover a connected Cloudflare Tunnel when its process is alive but detached.

The watchdog is intentionally independent from the application worker. It only
probes cloudflared's loopback readiness endpoint and restarts the cloudflared
LaunchAgent after consecutive zero-connection observations.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_METRICS_URL = "http://127.0.0.1:20241/ready"
DEFAULT_STATE_PATH = Path.home() / ".sub2api-station" / "cloudflared-watchdog-state.json"
DEFAULT_LOCK_PATH = Path.home() / ".sub2api-station" / "cloudflared-watchdog.lock"
DEFAULT_LABEL = "com.sub2api.cloudflared"
DEFAULT_FAILURE_THRESHOLD = 2
DEFAULT_COOLDOWN_SECONDS = 180.0
DEFAULT_RECOVERY_TIMEOUT = 45.0
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_PROBE_TIMEOUT = 4.0


@dataclass(frozen=True)
class WatchdogConfig:
    state_path: Path
    metrics_url: str = DEFAULT_METRICS_URL
    label: str = DEFAULT_LABEL
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT


def _default_state() -> dict[str, int | float]:
    return {"consecutive_failures": 0, "last_restart_at": 0}


def load_state(path: Path) -> dict[str, int | float]:
    """Load only the bounded numeric fields used to rate-limit restarts."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return _default_state()
    if not isinstance(payload, dict):
        return _default_state()

    failures = payload.get("consecutive_failures", 0)
    last_restart = payload.get("last_restart_at", 0)
    if isinstance(failures, bool) or not isinstance(failures, (int, float)):
        failures = 0
    if isinstance(last_restart, bool) or not isinstance(last_restart, (int, float)):
        last_restart = 0
    return {
        "consecutive_failures": max(0, int(failures)),
        "last_restart_at": max(0, float(last_restart)),
    }


def save_state(path: Path, state: dict[str, int | float]) -> None:
    """Atomically persist private watchdog state with mode 0600."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "consecutive_failures": max(0, int(state.get("consecutive_failures", 0))),
                    "last_restart_at": max(0, float(state.get("last_restart_at", 0))),
                },
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _is_loopback_url(url: str) -> bool:
    host = urlsplit(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def probe_json(url: str, timeout: float) -> dict[str, Any] | None:
    """Read a bounded JSON health response from a loopback endpoint."""

    if not _is_loopback_url(url):
        return None
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "XianyuManager-cloudflared-watchdog/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            body = response.read(64 * 1024)
    except Exception:
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def ready_connections(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        status = int(payload.get("status", 0))
        connections = int(payload.get("readyConnections", 0))
    except (TypeError, ValueError):
        return 0
    return connections if status == 200 and connections > 0 else 0


def restart_launchd(label: str, timeout: float = 15.0) -> bool:
    """Restart exactly the connector LaunchAgent, never the application job."""

    target = f"gui/{os.getuid()}/{label}"
    try:
        completed = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def run_once(
    config: WatchdogConfig,
    *,
    probe_fn: Callable[[str, float], dict[str, Any] | None] = probe_json,
    restart_fn: Callable[[str], bool] = restart_launchd,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one deterministic watchdog sample and return a safe event record."""

    if config.failure_threshold < 1 or config.recovery_timeout < 0 or config.poll_interval <= 0:
        raise ValueError("invalid watchdog timing configuration")

    state = load_state(config.state_path)
    payload = probe_fn(config.metrics_url, config.probe_timeout)
    connections = ready_connections(payload)
    now = float(now_fn())
    if connections > 0:
        state["consecutive_failures"] = 0
        save_state(config.state_path, state)
        return {"event": "healthy", "connections": connections, "restarted": False}

    failures = int(state["consecutive_failures"]) + 1
    state["consecutive_failures"] = failures
    if failures < config.failure_threshold:
        save_state(config.state_path, state)
        return {"event": "degraded", "failures": failures, "restarted": False}

    last_restart_at = float(state["last_restart_at"])
    remaining = config.cooldown_seconds - (now - last_restart_at) if last_restart_at else 0
    if remaining > 0:
        save_state(config.state_path, state)
        return {
            "event": "cooldown",
            "failures": failures,
            "retry_after": int(math.ceil(remaining)),
            "restarted": False,
        }

    state["last_restart_at"] = now
    save_state(config.state_path, state)
    try:
        restarted = bool(restart_fn(config.label))
    except Exception:
        restarted = False
    if not restarted:
        save_state(config.state_path, state)
        return {"event": "restart_failed", "failures": failures, "restarted": False}

    attempts = max(1, int(math.ceil(config.recovery_timeout / config.poll_interval)))
    for attempt in range(attempts):
        if attempt:
            sleep_fn(config.poll_interval)
        payload = probe_fn(config.metrics_url, config.probe_timeout)
        connections = ready_connections(payload)
        if connections > 0:
            state["consecutive_failures"] = 0
            save_state(config.state_path, state)
            return {
                "event": "recovered",
                "connections": connections,
                "attempts": attempt + 1,
                "restarted": True,
            }

    save_state(config.state_path, state)
    return {"event": "restart_unconfirmed", "failures": failures, "restarted": True}


@contextmanager
def single_instance(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover a detached cloudflared LaunchAgent")
    parser.add_argument("--metrics-url", default=DEFAULT_METRICS_URL)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--failure-threshold", type=int, default=DEFAULT_FAILURE_THRESHOLD)
    parser.add_argument("--cooldown-seconds", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--recovery-timeout", type=float, default=DEFAULT_RECOVERY_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--probe-timeout", type=float, default=DEFAULT_PROBE_TIMEOUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = WatchdogConfig(
        state_path=args.state_path.expanduser(),
        metrics_url=args.metrics_url,
        label=args.label,
        failure_threshold=args.failure_threshold,
        cooldown_seconds=args.cooldown_seconds,
        recovery_timeout=args.recovery_timeout,
        poll_interval=args.poll_interval,
        probe_timeout=args.probe_timeout,
    )
    try:
        with single_instance(args.lock_path.expanduser()) as acquired:
            if not acquired:
                print(json.dumps({"event": "busy", "restarted": False}, sort_keys=True))
                return 0
            result = run_once(config)
    except Exception as error:
        print(
            json.dumps(
                {"event": "watchdog_error", "error": type(error).__name__, "restarted": False},
                sort_keys=True,
            )
        )
        return 1
    result["observed_at"] = int(time.time())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["event"] not in {"restart_failed", "restart_unconfirmed"} else 1


if __name__ == "__main__":
    sys.exit(main())
