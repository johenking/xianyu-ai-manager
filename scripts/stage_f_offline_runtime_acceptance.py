#!/usr/bin/env python3
"""Stage F local-only runtime acceptance for the Skill Center monitor.

The parent process orchestrates disposable SQLite databases and sandboxed
single-worker app processes. Child modes import project code only after the
disposable DB/key environment is installed. No production Cookie, provider,
notification destination, or live service is read or called.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/Users/mac/Documents/咸鱼监控台/.venv/bin/python")
HOST = "127.0.0.1"
PORT = 18091
BASE_URL = f"http://{HOST}:{PORT}"
EVIDENCE_PARENT = Path("/Users/mac/Documents/Codex/evidence")
RUNTIME_PARENT = Path("/Users/mac/Documents/Codex/runtime-evidence-tmp")
ACTIVE_PROCESSES: list[subprocess.Popen[bytes]] = []


class AcceptanceFailure(RuntimeError):
    pass


def _secure_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AcceptanceFailure(f"invalid JSON object: {path.name}")
    return value


def _wait_for(
    predicate: Callable[[], Any],
    *,
    timeout: float,
    description: str,
    interval: float = 0.2,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except BaseException as exc:
            last_error = exc
        time.sleep(interval)
    suffix = f": {type(last_error).__name__}" if last_error else ""
    raise AcceptanceFailure(f"timeout waiting for {description}{suffix}")


def _port_free() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        return probe.connect_ex((HOST, PORT)) != 0
    finally:
        probe.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _one(
    db_path: Path,
    sql: str,
    params: Iterable[Any] = (),
) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        row = connection.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def _db_checks(db_path: Path) -> Dict[str, Any]:
    with _connect(db_path) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        migration = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
        "migration": str(migration[0]) if migration else "legacy",
    }


def _snapshot(db_path: Path, user_id: int) -> Dict[str, Any]:
    with _connect(db_path) as connection:
        runs = [dict(row) for row in connection.execute(
            """
            SELECT id, task_id, trigger_type, source_adapter, status, attempt,
                   recovered_from_run_id, raw_result_count,
                   accepted_result_count, error_code
            FROM skill_monitor_runs WHERE user_id = ? ORDER BY id
            """,
            (user_id,),
        ).fetchall()]
        results = []
        for row in connection.execute(
            """
            SELECT id, run_id, source_adapter, notify_status, raw_data
            FROM skill_monitor_results WHERE user_id = ? ORDER BY id
            """,
            (user_id,),
        ).fetchall():
            raw = json.loads(row[4] or "{}")
            results.append({
                "id": int(row[0]),
                "run_id": int(row[1]),
                "source_adapter": str(row[2] or ""),
                "notify_status": str(row[3] or ""),
                "raw_data": {
                    key: raw.get(key)
                    for key in (
                        "source",
                        "is_real_data",
                        "provider_mode",
                        "evidence_scope",
                        "scheduled_run",
                    )
                },
            })
        deliveries = [dict(row) for row in connection.execute(
            """
            SELECT id, result_id, status, attempt, error_code, idempotency_key
            FROM skill_monitor_deliveries WHERE user_id = ? ORDER BY id
            """,
            (user_id,),
        ).fetchall()]
        for delivery in deliveries:
            key = str(delivery.pop("idempotency_key"))
            delivery["idempotency_key_sha256"] = hashlib.sha256(
                key.encode("utf-8")
            ).hexdigest()
        event_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT event_type, COUNT(*) FROM skill_monitor_events
                WHERE user_id = ? GROUP BY event_type ORDER BY event_type
                """,
                (user_id,),
            ).fetchall()
        }
        real_searches = int(connection.execute(
            """
            SELECT COUNT(*) FROM skill_monitor_runs
            WHERE user_id = ? AND status = 'success'
              AND source_adapter IN ('playwright', 'mtop')
            """,
            (user_id,),
        ).fetchone()[0])
        real_deliveries = int(connection.execute(
            """
            SELECT COUNT(*) FROM skill_monitor_deliveries AS d
            JOIN skill_monitor_results AS r
              ON r.id = d.result_id AND r.user_id = d.user_id
            WHERE d.user_id = ? AND d.status = 'sent'
              AND d.confirmed_at IS NOT NULL
              AND r.source_adapter IN ('playwright', 'mtop')
              AND COALESCE(json_extract(r.raw_data, '$.is_real_data'), 0) = 1
              AND COALESCE(json_extract(r.raw_data, '$.provider_mode'), 'real') <> 'mocked'
            """,
            (user_id,),
        ).fetchone()[0])
    return {
        "checks": _db_checks(db_path),
        "runs": runs,
        "results": results,
        "deliveries": deliveries,
        "event_counts": event_counts,
        "truthful_capability_counts": {
            "last_real_search_candidates": real_searches,
            "last_real_delivery_candidates": real_deliveries,
        },
    }


def _child_prepare() -> int:
    from ai_provider_service import _local_encryption_secret
    from db_manager import db_manager
    from security_utils import AccountCredentialCipher, SystemSecretCipher

    AccountCredentialCipher(db_manager.db_path)
    SystemSecretCipher(db_manager.db_path)
    _local_encryption_secret()
    user = db_manager.conn.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()
    if not user:
        raise AcceptanceFailure("synthetic database has no local user")
    user_id = int(user[0])
    account_id = "stage-f-synthetic-account"
    db_manager.conn.execute(
        """
        INSERT INTO cookies (
            id, value, user_id, xianyu_unb, remark, username,
            cookie_refresh_enabled, cookie_revision
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1)
        """,
        (
            account_id,
            "unb=stage-f-synthetic-account",
            user_id,
            "stage-f-synthetic-account",
            "stage-f synthetic disabled account",
            "stage-f-synthetic",
        ),
    )
    db_manager.conn.execute(
        "INSERT INTO cookie_status (cookie_id, enabled) VALUES (?, 0)",
        (account_id,),
    )
    db_manager.conn.commit()
    for key, value in {
        "skill_monitor_enabled": "true",
        "skill_monitor_scheduler_enabled": "true",
        "skill_monitor_delivery_enabled": "true",
        "skill_monitor_mtop_enabled": "false",
        "registration_enabled": "false",
        "item_sync_enabled": "false",
        "smtp_enabled": "false",
    }.items():
        if not db_manager.set_system_setting(key, value):
            raise AcceptanceFailure(f"failed to set synthetic flag: {key}")
    channel_id = db_manager.create_notification_channel(
        "stage-f-loopback",
        "webhook",
        json.dumps({"url": f"{BASE_URL}/_stage_f/loopback"}),
        user_id=user_id,
    )
    task_id = db_manager.create_skill_monitor_task(
        user_id,
        {
            "name": "stage-f-runtime-canary",
            "keyword": "Stage F Canary",
            "account_id": account_id,
            "notify_enabled": True,
            "enabled": True,
            "schedule_enabled": True,
            "schedule_interval_minutes": 15,
            "next_run_at": "1970-01-01 00:00:00",
        },
    )
    if not task_id:
        raise AcceptanceFailure("failed to create synthetic monitor task")
    seed_delivery_id = None
    if os.getenv("STAGE_F_SEED_DELIVERY") == "1":
        claim = db_manager.claim_skill_monitor_run(
            task_id,
            user_id,
            trigger_type="manual",
            source_adapter="mocked",
        )
        if not claim.get("claimed"):
            raise AcceptanceFailure("failed to create seed run")
        created = db_manager.persist_skill_monitor_match(
            {
                "task_id": task_id,
                "user_id": user_id,
                "title": "Stage F Canary seeded transport",
                "price": 1.0,
                "region": "synthetic",
                "item_url": "https://example.invalid/stage-f-seed",
                "item_id": "stage-f-seed",
                "source_adapter": "mocked",
                "raw_data": {
                    "source": "mocked",
                    "is_real_data": False,
                    "provider_mode": "mocked",
                    "evidence_scope": "mocked_provider",
                },
            },
            run_id=int(claim["run_id"]),
            claim_token=str(claim["claim_token"]),
        )
        if not created.get("created") or len(created.get("delivery_ids") or []) != 1:
            raise AcceptanceFailure("failed to create seed delivery")
        seed_delivery_id = int(created["delivery_ids"][0])
        if not db_manager.finish_skill_monitor_run(
            int(claim["run_id"]),
            str(claim["claim_token"]),
            status="success",
            raw_result_count=1,
            accepted_result_count=1,
        ):
            raise AcceptanceFailure("failed to finish seed run")
    _secure_json(
        Path(os.environ["STAGE_F_STATE_FILE"]),
        {
            "user_id": user_id,
            "account_id": account_id,
            "channel_id": int(channel_id),
            "task_id": int(task_id),
            "seed_delivery_id": seed_delivery_id,
            "migration": str(getattr(db_manager, "schema_version", "legacy")),
        },
    )
    db_manager.close()
    return 0


def _append_receipt(path: Path, payload: Dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def _child_worker() -> int:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    import uvicorn

    # With postponed annotations FastAPI resolves the request type from the
    # defining module's globals, not this function's local scope.
    globals()["Request"] = Request

    from app_factory import create_app
    from reply_server import execute_skill_monitor_task
    from skill_monitor_delivery_dispatcher import skill_monitor_delivery_dispatcher
    from skill_monitor_scheduler import skill_monitor_scheduler

    marker_path = Path(os.environ["STAGE_F_MARKER_FILE"])
    receipt_path = Path(os.environ["STAGE_F_RECEIPT_FILE"])
    search_mode = os.getenv("STAGE_F_SEARCH_MODE", "normal")
    item_suffix = os.getenv("STAGE_F_ITEM_SUFFIX", "normal")
    delay_seconds = float(os.getenv("STAGE_F_DELIVERY_DELAY", "0"))
    provider_calls = 0
    seen_receipts: set[str] = set()

    async def fake_search_provider(**_kwargs: Any) -> Dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        _secure_json(
            marker_path,
            {"provider_calls": provider_calls, "state": search_mode},
        )
        if search_mode == "block":
            await asyncio.Event().wait()
        return {
            "is_real_data": True,
            "source": "fixture-self-claim",
            "items": [
                {
                    "item_id": f"stage-f-{item_suffix}",
                    "title": f"Stage F Canary synthetic product {item_suffix}",
                    "price": "88.00",
                    "area": "synthetic",
                    "item_url": f"https://example.invalid/stage-f-{item_suffix}",
                    "seller_name": "synthetic-seller",
                },
                {
                    "item_id": f"stage-f-nonmatch-{item_suffix}",
                    "title": "unrelated synthetic product",
                    "price": "99.00",
                    "area": "synthetic",
                    "item_url": f"https://example.invalid/nonmatch-{item_suffix}",
                },
            ],
        }

    async def injected_executor(
        task: Dict[str, Any],
        user_id: int,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await execute_skill_monitor_task(
            task,
            user_id,
            scheduled_run=bool(kwargs.get("scheduled_run")),
            search_provider=fake_search_provider,
        )

    skill_monitor_scheduler.poll_interval_seconds = 0.2
    skill_monitor_scheduler.task_executor = injected_executor
    skill_monitor_delivery_dispatcher.poll_interval_seconds = 1
    app = create_app()

    @app.post("/_stage_f/loopback")
    async def stage_f_loopback(request: Request) -> JSONResponse:
        body = await request.json()
        header_key = str(request.headers.get("Idempotency-Key") or "")
        payload_key = (
            str(body.get("idempotency_key") or "")
            if isinstance(body, dict)
            else ""
        )
        key_digest = hashlib.sha256(header_key.encode("utf-8")).hexdigest()
        duplicate = key_digest in seen_receipts
        seen_receipts.add(key_digest)
        _append_receipt(
            receipt_path,
            {
                "idempotency_key_sha256": key_digest,
                "header_present": bool(header_key),
                "header_payload_match": bool(
                    header_key and header_key == payload_key
                ),
                "duplicate": duplicate,
            },
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        return JSONResponse({"ok": True, "duplicate": duplicate})

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        workers=1,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=3,
    )
    return 0


def _child_fence() -> int:
    from db_manager import db_manager

    private = _read_json(Path(os.environ["STAGE_F_PRIVATE_FILE"]))
    result = {
        "old_run_heartbeat_accepted": db_manager.heartbeat_skill_monitor_run(
            int(private["run_id"]),
            str(private["run_claim_token"]),
        ),
        "old_run_finish_accepted": db_manager.finish_skill_monitor_run(
            int(private["run_id"]),
            str(private["run_claim_token"]),
            status="success",
        ),
        "old_run_persist_state": db_manager.persist_skill_monitor_match(
            {
                "task_id": int(private["task_id"]),
                "user_id": int(private["user_id"]),
                "item_id": "stage-f-old-token-fence",
                "item_url": "https://example.invalid/old-token-fence",
                "title": "old token fence",
                "source_adapter": "mocked",
                "raw_data": {
                    "source": "mocked",
                    "is_real_data": False,
                    "provider_mode": "mocked",
                    "evidence_scope": "mocked_provider",
                },
            },
            run_id=int(private["run_id"]),
            claim_token=str(private["run_claim_token"]),
        ).get("state"),
        "old_delivery_heartbeat_accepted": db_manager.heartbeat_skill_monitor_delivery(
            int(private["delivery_id"]),
            str(private["delivery_claim_token"]),
        ),
        "old_delivery_finish_accepted": db_manager.finish_skill_monitor_delivery(
            int(private["delivery_id"]),
            str(private["delivery_claim_token"]),
            status="sent",
        ),
    }
    _secure_json(Path(os.environ["STAGE_F_FENCE_RESULT_FILE"]), result)
    db_manager.close()
    return 0


def _child_main(mode: str) -> int:
    if mode == "prepare":
        return _child_prepare()
    if mode == "worker":
        return _child_worker()
    if mode == "fence":
        return _child_fence()
    raise AcceptanceFailure(f"unknown child mode: {mode}")


def _secure_text(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(path, 0o600)


def _scenario(root: Path, name: str) -> Dict[str, Path]:
    scenario = root / name
    scenario.mkdir(mode=0o700)
    for child in ("home", "tmp"):
        (scenario / child).mkdir(mode=0o700)
    return {
        "root": scenario,
        "db": scenario / "stage-f.db",
        "account_key": scenario / ".account_credential_key",
        "system_key": scenario / ".system_secret_key",
        "ai_key": scenario / ".ai_provider_key",
        "state": scenario / "state.json",
        "marker": scenario / "provider-marker.json",
        "receipts": scenario / "receipts.jsonl",
        "log": scenario / "runtime.log",
        "private": scenario / "private-tokens.json",
        "fence": scenario / "fence-result.json",
    }


def _child_env(
    paths: Dict[str, Path],
    *,
    seed_delivery: bool = False,
    search_mode: str = "normal",
    item_suffix: str = "normal",
    delivery_delay: float = 0,
) -> Dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "HOME": str(paths["root"] / "home"),
        "TMPDIR": str(paths["root"] / "tmp"),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DB_PATH": str(paths["db"]),
        "ACCOUNT_CREDENTIAL_KEY_FILE": str(paths["account_key"]),
        "SYSTEM_SECRET_KEY_FILE": str(paths["system_key"]),
        "AI_PROVIDER_KEY_FILE": str(paths["ai_key"]),
        "SQL_LOG_ENABLED": "false",
        "WEB_CONCURRENCY": "1",
        "UVICORN_WORKERS": "1",
        "WORKERS": "1",
        "COOKIES_STR": "",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "STAGE_F_STATE_FILE": str(paths["state"]),
        "STAGE_F_MARKER_FILE": str(paths["marker"]),
        "STAGE_F_RECEIPT_FILE": str(paths["receipts"]),
        "STAGE_F_PRIVATE_FILE": str(paths["private"]),
        "STAGE_F_FENCE_RESULT_FILE": str(paths["fence"]),
        "STAGE_F_SEED_DELIVERY": "1" if seed_delivery else "0",
        "STAGE_F_SEARCH_MODE": search_mode,
        "STAGE_F_ITEM_SUFFIX": item_suffix,
        "STAGE_F_DELIVERY_DELAY": str(delivery_delay),
    }


def _sandbox_command(
    profile: Path,
    env: Dict[str, str],
    mode: str,
) -> list[str]:
    assignments = [f"{key}={value}" for key, value in sorted(env.items())]
    return [
        "/usr/bin/env",
        "-i",
        *assignments,
        "/usr/bin/sandbox-exec",
        "-f",
        str(profile),
        str(PYTHON),
        str(Path(__file__).resolve()),
        mode,
    ]


def _run_child(
    profile: Path,
    paths: Dict[str, Path],
    env: Dict[str, str],
    mode: str,
    *,
    timeout: float = 120,
) -> int:
    descriptor = os.open(
        paths["log"],
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    with os.fdopen(descriptor, "ab", closefd=True) as log_handle:
        completed = subprocess.run(
            _sandbox_command(profile, env, mode),
            cwd=paths["root"],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        raise AcceptanceFailure(f"{mode} child exit {completed.returncode}")
    return completed.returncode


def _start_worker(
    profile: Path,
    paths: Dict[str, Path],
    env: Dict[str, str],
) -> subprocess.Popen[bytes]:
    if not _port_free():
        raise AcceptanceFailure(f"port {PORT} is already in use")
    descriptor = os.open(
        paths["log"],
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    log_handle = os.fdopen(descriptor, "ab", closefd=True)
    process = subprocess.Popen(
        _sandbox_command(profile, env, "worker"),
        cwd=paths["root"],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    ACTIVE_PROCESSES.append(process)
    log_handle.close()

    def healthy() -> Optional[Dict[str, Any]]:
        if process.poll() is not None:
            raise AcceptanceFailure(f"worker exited early: {process.returncode}")
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and payload.get("status") == "healthy":
                return payload
        except Exception:
            return None
        return None

    _wait_for(healthy, timeout=30, description="healthy loopback worker")
    return process


def _stop_worker(
    process: subprocess.Popen[bytes],
    *,
    force: bool = False,
) -> int:
    if process.poll() is None:
        process.send_signal(signal.SIGKILL if force else signal.SIGTERM)
    try:
        exit_code = int(process.wait(timeout=20))
        if process in ACTIVE_PROCESSES:
            ACTIVE_PROCESSES.remove(process)
        return exit_code
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=10)
        if process in ACTIVE_PROCESSES:
            ACTIVE_PROCESSES.remove(process)
        raise AcceptanceFailure("worker did not stop within timeout") from exc


def _process_evidence(
    process: subprocess.Popen[bytes],
    scenario_root: Path,
) -> Dict[str, Any]:
    ps = subprocess.run(
        ["ps", "-p", str(process.pid), "-o", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    cwd = subprocess.run(
        ["lsof", "-a", "-p", str(process.pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    sockets = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(process.pid), "-iTCP"],
        capture_output=True,
        text=True,
        check=False,
    )
    children = subprocess.run(
        ["pgrep", "-P", str(process.pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    socket_lines = [
        line for line in sockets.stdout.splitlines()
        if "TCP" in line
    ]
    non_loopback = [
        line for line in socket_lines
        if not any(value in line for value in ("127.0.0.1", "localhost", "::1"))
    ]
    return {
        "pid_observed": process.pid > 0,
        "command_uses_stage_f_harness": "stage_f_offline_runtime_acceptance.py"
        in ps.stdout,
        "cwd_is_disposable_scenario": str(scenario_root) in cwd.stdout,
        "child_process_count": len(
            [line for line in children.stdout.splitlines() if line.strip()]
        ),
        "single_worker": children.returncode != 0
        or not children.stdout.strip(),
        "tcp_socket_count": len(socket_lines),
        "non_loopback_socket_count": len(non_loopback),
        "listen_port": PORT,
        "bind_host": HOST,
    }


def _read_receipts(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    receipts = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    receipts.append(value)
    return receipts


def _validate_keys(paths: Dict[str, Path]) -> Dict[str, Any]:
    key_paths = (paths["account_key"], paths["system_key"], paths["ai_key"])
    modes = [oct(path.stat().st_mode & 0o777) for path in key_paths]
    return {
        "count": sum(path.is_file() for path in key_paths),
        "all_mode_0600": all(mode == "0o600" for mode in modes),
    }


def _prepare(
    profile: Path,
    paths: Dict[str, Path],
    *,
    seed_delivery: bool,
) -> tuple[Dict[str, str], Dict[str, Any]]:
    env = _child_env(paths, seed_delivery=seed_delivery)
    _run_child(profile, paths, env, "prepare")
    state = _read_json(paths["state"])
    checks = _db_checks(paths["db"])
    keys = _validate_keys(paths)
    if checks["integrity_check"] != "ok" or checks["foreign_key_violations"] != 0:
        raise AcceptanceFailure("prepared database failed integrity checks")
    if keys["count"] != 3 or not keys["all_mode_0600"]:
        raise AcceptanceFailure("prepared database key gate failed")
    return env, state


def _normal_scenario(
    profile: Path,
    paths: Dict[str, Path],
) -> Dict[str, Any]:
    env, state = _prepare(profile, paths, seed_delivery=False)
    env.update({
        "STAGE_F_SEARCH_MODE": "normal",
        "STAGE_F_ITEM_SUFFIX": "normal",
        "STAGE_F_DELIVERY_DELAY": "0",
    })
    process = _start_worker(profile, paths, env)
    try:
        run = _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT id, raw_result_count, accepted_result_count
                FROM skill_monitor_runs
                WHERE trigger_type = 'scheduled' AND status = 'success'
                ORDER BY id DESC LIMIT 1
                """,
            ),
            timeout=20,
            description="scheduler success",
        )
        delivery = _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT id, idempotency_key FROM skill_monitor_deliveries
                WHERE status = 'sent' ORDER BY id DESC LIMIT 1
                """,
            ),
            timeout=20,
            description="loopback delivery sent",
        )
        receipts = _wait_for(
            lambda: _read_receipts(paths["receipts"]),
            timeout=10,
            description="loopback receipt",
        )
        process_state = _process_evidence(process, paths["root"])
        health = _wait_for(
            lambda: _http_health_parent(),
            timeout=10,
            description="fresh health",
        )
    finally:
        exit_code = _stop_worker(process)
    _wait_for(_port_free, timeout=10, description="normal port release")
    delivery_digest = hashlib.sha256(
        str(delivery["idempotency_key"]).encode("utf-8")
    ).hexdigest()
    if int(run["raw_result_count"]) != 2 or int(run["accepted_result_count"]) != 1:
        raise AcceptanceFailure("normal scheduler counts are not 2 raw / 1 accepted")
    if not any(
        receipt.get("idempotency_key_sha256") == delivery_digest
        and receipt.get("header_present")
        and receipt.get("header_payload_match")
        for receipt in receipts
    ):
        raise AcceptanceFailure("loopback idempotency receipt did not match outbox")
    snapshot = _snapshot(paths["db"], int(state["user_id"]))
    if snapshot["truthful_capability_counts"] != {
        "last_real_search_candidates": 0,
        "last_real_delivery_candidates": 0,
    }:
        raise AcceptanceFailure("mocked runtime polluted real capability evidence")
    result_raw = snapshot["results"][-1]["raw_data"]
    if result_raw != {
        "source": "mocked",
        "is_real_data": False,
        "provider_mode": "mocked",
        "evidence_scope": "mocked_provider",
        "scheduled_run": True,
    }:
        raise AcceptanceFailure("mocked result evidence tags are not fail-closed")
    return {
        "scope": "mocked/local",
        "health": {
            "status": health.get("status"),
            "migration": health.get("migration_version"),
        },
        "process": process_state,
        "scheduler_chain": {
            "raw_result_count": int(run["raw_result_count"]),
            "accepted_result_count": int(run["accepted_result_count"]),
            "result_first_seen_events": snapshot["event_counts"].get(
                "result_first_seen", 0
            ),
            "delivery_sent_events": snapshot["event_counts"].get(
                "delivery_sent", 0
            ),
        },
        "outbox": {
            "receipt_count": len(receipts),
            "stable_idempotency_key": True,
            "status": "sent",
        },
        "truthful_capabilities": snapshot["truthful_capability_counts"],
        "database": snapshot["checks"],
        "temporary_keys": _validate_keys(paths),
        "graceful_exit_code": exit_code,
        "port_released": _port_free(),
    }


def _http_health_parent() -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if response.status == 200 and payload.get("status") == "healthy":
            return payload
    except Exception:
        return None
    return None


def _graceful_scenario(
    profile: Path,
    paths: Dict[str, Path],
) -> Dict[str, Any]:
    env, state = _prepare(profile, paths, seed_delivery=True)
    env.update({
        "STAGE_F_SEARCH_MODE": "block",
        "STAGE_F_ITEM_SUFFIX": "graceful",
        "STAGE_F_DELIVERY_DELAY": "30",
    })
    process = _start_worker(profile, paths, env)
    try:
        _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT id FROM skill_monitor_runs
                WHERE trigger_type = 'scheduled' AND status = 'running'
                ORDER BY id DESC LIMIT 1
                """,
            ),
            timeout=15,
            description="graceful in-flight run",
        )
        _wait_for(
            lambda: _one(
                paths["db"],
                "SELECT id FROM skill_monitor_deliveries WHERE status = 'sending'",
            ),
            timeout=15,
            description="graceful in-flight delivery",
        )
        _wait_for(
            lambda: paths["marker"].exists(),
            timeout=10,
            description="slow provider marker",
        )
        _wait_for(
            lambda: _read_receipts(paths["receipts"]),
            timeout=10,
            description="slow transport receipt",
        )
        process_state = _process_evidence(process, paths["root"])
        exit_code = _stop_worker(process)
    except Exception:
        if process.poll() is None:
            _stop_worker(process, force=True)
        raise
    _wait_for(_port_free, timeout=10, description="graceful port release")
    run = _wait_for(
        lambda: _one(
            paths["db"],
            """
            SELECT status, error_code FROM skill_monitor_runs
            WHERE trigger_type = 'scheduled' ORDER BY id DESC LIMIT 1
            """,
        ),
        timeout=15,
        description="graceful run final state",
    )
    delivery = _wait_for(
        lambda: (
            row
            if (row := _one(
                paths["db"],
                """
                SELECT status, error_code FROM skill_monitor_deliveries
                WHERE id = ?
                """,
                (int(state["seed_delivery_id"]),),
            )) and row["status"] != "sending"
            else None
        ),
        timeout=15,
        description="graceful delivery final state",
    )
    if run != {"status": "interrupted", "error_code": "shutdown_interrupted"}:
        raise AcceptanceFailure(f"graceful run state mismatch: {run}")
    if not delivery or delivery["status"] == "sending":
        raise AcceptanceFailure("graceful delivery remained sending")
    checks = _db_checks(paths["db"])
    return {
        "scope": "mocked/local",
        "process": process_state,
        "run": run,
        "delivery": delivery,
        "slow_transport_receipts": len(_read_receipts(paths["receipts"])),
        "exit_code": exit_code,
        "port_released": _port_free(),
        "database": checks,
    }


def _crash_scenario(
    profile: Path,
    paths: Dict[str, Path],
) -> Dict[str, Any]:
    env, state = _prepare(profile, paths, seed_delivery=True)
    env.update({
        "STAGE_F_SEARCH_MODE": "block",
        "STAGE_F_ITEM_SUFFIX": "crashed",
        "STAGE_F_DELIVERY_DELAY": "30",
    })
    process = _start_worker(profile, paths, env)
    running = _wait_for(
        lambda: _one(
            paths["db"],
            """
            SELECT id, claim_token FROM skill_monitor_runs
            WHERE trigger_type = 'scheduled' AND status = 'running'
            ORDER BY id DESC LIMIT 1
            """,
        ),
        timeout=15,
        description="crash in-flight run",
    )
    sending = _wait_for(
        lambda: _one(
            paths["db"],
            """
            SELECT id, claim_token FROM skill_monitor_deliveries
            WHERE status = 'sending' ORDER BY id LIMIT 1
            """,
        ),
        timeout=15,
        description="crash in-flight delivery",
    )
    _wait_for(
        lambda: _read_receipts(paths["receipts"]),
        timeout=10,
        description="crash transport receipt",
    )
    first_process_state = _process_evidence(process, paths["root"])
    expiry = time.time() + 2
    with _connect(paths["db"]) as connection:
        connection.execute(
            "UPDATE skill_monitor_runs SET lease_expires_at = ? WHERE id = ?",
            (expiry, int(running["id"])),
        )
        connection.execute(
            "UPDATE skill_monitor_deliveries SET lease_expires_at = ? WHERE id = ?",
            (expiry, int(sending["id"])),
        )
        connection.commit()
    killed_exit = _stop_worker(process, force=True)
    _wait_for(_port_free, timeout=10, description="crashed port release")
    time.sleep(max(0, expiry - time.time()) + 1)

    private = {
        "run_id": int(running["id"]),
        "run_claim_token": str(running["claim_token"]),
        "delivery_id": int(sending["id"]),
        "delivery_claim_token": str(sending["claim_token"]),
        "task_id": int(state["task_id"]),
        "user_id": int(state["user_id"]),
    }
    _secure_json(paths["private"], private)
    restart_env = dict(env)
    restart_env.update({
        "STAGE_F_SEARCH_MODE": "normal",
        "STAGE_F_ITEM_SUFFIX": "recovered",
        "STAGE_F_DELIVERY_DELAY": "0",
    })
    restarted = _start_worker(profile, paths, restart_env)
    try:
        old_run = _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT status, error_code FROM skill_monitor_runs
                WHERE id = ? AND status = 'interrupted'
                """,
                (int(running["id"]),),
            ),
            timeout=15,
            description="stale run recovery",
        )
        old_delivery = _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT status, error_code FROM skill_monitor_deliveries
                WHERE id = ? AND status = 'unknown'
                """,
                (int(sending["id"]),),
            ),
            timeout=15,
            description="stale delivery recovery",
        )
        successor = _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT id, status, attempt, recovered_from_run_id
                FROM skill_monitor_runs
                WHERE recovered_from_run_id = ? AND status = 'success'
                ORDER BY id DESC LIMIT 1
                """,
                (int(running["id"]),),
            ),
            timeout=20,
            description="successor run",
        )
        _wait_for(
            lambda: _one(
                paths["db"],
                """
                SELECT id FROM skill_monitor_deliveries
                WHERE id <> ? AND status = 'sent' ORDER BY id DESC LIMIT 1
                """,
                (int(sending["id"]),),
            ),
            timeout=20,
            description="successor delivery",
        )
        _run_child(profile, paths, restart_env, "fence")
        fence = _read_json(paths["fence"])
        paths["private"].unlink()
        restart_process_state = _process_evidence(restarted, paths["root"])
    finally:
        restart_exit = _stop_worker(restarted)
    _wait_for(_port_free, timeout=10, description="recovery port release")
    expected_fence = {
        "old_run_heartbeat_accepted": False,
        "old_run_finish_accepted": False,
        "old_run_persist_state": "lease_lost",
        "old_delivery_heartbeat_accepted": False,
        "old_delivery_finish_accepted": False,
    }
    if fence != expected_fence:
        raise AcceptanceFailure(f"old token fence failed: {fence}")
    if old_run.get("error_code") != "lease_expired":
        raise AcceptanceFailure(f"old run was not lease-expired: {old_run}")
    if old_delivery.get("error_code") != "send_outcome_unknown":
        raise AcceptanceFailure(
            f"old delivery was not outcome-unknown: {old_delivery}"
        )
    if int(successor["attempt"]) != 2:
        raise AcceptanceFailure(f"successor attempt was not 2: {successor}")
    if _one(
        paths["db"],
        "SELECT id FROM skill_monitor_deliveries WHERE status = 'sending'",
    ):
        raise AcceptanceFailure("delivery remained permanently sending")
    snapshot = _snapshot(paths["db"], int(state["user_id"]))
    return {
        "scope": "mocked/local",
        "first_process": first_process_state,
        "restart_process": restart_process_state,
        "sigkill_exit_code": killed_exit,
        "restart_exit_code": restart_exit,
        "stale_run": old_run,
        "stale_delivery": old_delivery,
        "successor": {
            "status": successor["status"],
            "attempt": int(successor["attempt"]),
            "recovered_from_matches": int(successor["recovered_from_run_id"])
            == int(running["id"]),
        },
        "old_token_fence": fence,
        "sending_deliveries_remaining": 0,
        "database": snapshot["checks"],
        "truthful_capabilities": snapshot["truthful_capability_counts"],
        "port_released": _port_free(),
    }


def _external_canary(profile: Path, log_path: Path) -> Dict[str, Any]:
    completed = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile),
            "/usr/bin/curl",
            "-sS",
            "--max-time",
            "3",
            "https://example.com",
        ],
        capture_output=True,
        check=False,
    )
    _secure_text(
        log_path,
        f"exit_code={completed.returncode}\n",
    )
    if completed.returncode == 0:
        raise AcceptanceFailure("sandbox external network canary was not blocked")
    return {
        "attempted_destination": "non-loopback canary",
        "blocked": True,
        "exit_code": int(completed.returncode),
    }


def _write_evidence(
    evidence_dir: Path,
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    _secure_json(evidence_dir / "stage-f-runtime-acceptance.json", summary)
    files = sorted(path for path in evidence_dir.iterdir() if path.is_file())
    manifest_lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in files
    ]
    _secure_text(evidence_dir / "SHA256SUMS", "\n".join(manifest_lines) + "\n")
    root_hash = hashlib.sha256(
        ("\n".join(manifest_lines) + "\n").encode("utf-8")
    ).hexdigest()
    _secure_text(evidence_dir / "ROOT_SHA256", root_hash + "\n")
    non_0600 = [
        path.name
        for path in evidence_dir.iterdir()
        if path.is_file() and (path.stat().st_mode & 0o777) != 0o600
    ]
    if (evidence_dir.stat().st_mode & 0o777) != 0o700 or non_0600:
        raise AcceptanceFailure("evidence permission gate failed")
    return {
        "directory": str(evidence_dir),
        "root_sha256": root_hash,
        "file_count": len([path for path in evidence_dir.iterdir() if path.is_file()]),
        "directory_mode": "0700",
        "file_mode": "0600",
    }


def _parent_main() -> int:
    os.umask(0o077)
    if sys.platform != "darwin":
        raise AcceptanceFailure("Stage F sandbox harness requires macOS")
    if not PYTHON.is_file():
        raise AcceptanceFailure(f"project Python missing: {PYTHON}")
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise AcceptanceFailure("sandbox-exec is unavailable")
    if not _port_free():
        raise AcceptanceFailure(f"port {PORT} is already in use")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    EVIDENCE_PARENT.mkdir(parents=True, mode=0o700, exist_ok=True)
    RUNTIME_PARENT.mkdir(parents=True, mode=0o700, exist_ok=True)
    evidence_dir = EVIDENCE_PARENT / f"xianyu-monitor-stage-f-{stamp}-{os.getpid()}"
    runtime_root = RUNTIME_PARENT / f"xianyu-monitor-stage-f-{stamp}-{os.getpid()}"
    evidence_dir.mkdir(mode=0o700)
    runtime_root.mkdir(mode=0o700)
    profile = runtime_root / "network.sb"
    _secure_text(
        profile,
        """(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
""",
    )
    try:
        canary = _external_canary(profile, runtime_root / "network-canary.log")
        normal = _normal_scenario(profile, _scenario(runtime_root, "normal"))
        graceful = _graceful_scenario(
            profile,
            _scenario(runtime_root, "graceful"),
        )
        crash = _crash_scenario(profile, _scenario(runtime_root, "crash"))
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        summary = {
            "evidence_version": "stage-f-offline-runtime-v1",
            "observed_at": time.time(),
            "git_head_before_stage_f_commit": git_head,
            "scope": {
                "search_provider": "mocked",
                "notification_receiver": "local loopback",
                "xianyu_account": "synthetic disabled row",
                "ai_provider": "not called",
                "mtop": "disabled/not called",
                "live_deployment": "not performed",
            },
            "network_isolation": {
                "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
                "external_canary": canary,
                "successful_non_loopback_connections": 0,
            },
            "normal_scheduler_loop": normal,
            "graceful_shutdown": graceful,
            "stale_lease_recovery": crash,
            "acceptance": "passed",
            "temporary_runtime_cleanup": "pending",
        }
    except Exception as exc:
        failure = {
            "acceptance": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "scope": "mocked/local",
        }
        _secure_json(evidence_dir / "stage-f-failure.json", failure)
        raise
    finally:
        for process in list(ACTIVE_PROCESSES):
            if process.poll() is None:
                _stop_worker(process, force=True)
            elif process in ACTIVE_PROCESSES:
                ACTIVE_PROCESSES.remove(process)
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
    if runtime_root.exists():
        raise AcceptanceFailure("temporary runtime directory was not removed")
    summary["temporary_runtime_cleanup"] = {
        "runtime_root_removed": True,
        "test_db_removed": True,
        "three_keys_removed": True,
        "raw_logs_removed": True,
        "private_tokens_removed": True,
        "pid_files_removed": True,
        "port_released": _port_free(),
    }
    evidence = _write_evidence(evidence_dir, summary)
    print(json.dumps({"acceptance": "passed", "evidence": evidence}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"prepare", "worker", "fence"}:
        try:
            raise SystemExit(_child_main(sys.argv[1]))
        except Exception as exc:
            print(
                f"stage-f child failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            raise
    parser = argparse.ArgumentParser(
        description="Run Stage F mocked/local runtime acceptance",
    )
    parser.parse_args()
    raise SystemExit(_parent_main())
