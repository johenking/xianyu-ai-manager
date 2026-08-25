#!/usr/bin/env python3
"""Read-only historical seller-rating discovery with an explicit apply gate.

The default mode only reads the configured database and the platform order
list. It never imports the rating submitter and never writes SQLite. ``--apply``
re-scans the platform and schedules currently rateable, untracked orders for
the existing serial seller-rating scheduler.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set, Tuple

from order_sync_service import XianyuOrderListClient, _parse_order_timestamp


SCAN_DAYS = 365
MAX_PAGES = 100
MAX_ORDERS = 2000
REQUEST_INTERVAL = 0.8
MIN_DELAY_SECONDS = 300
MAX_DELAY_SECONDS = 900


class ReadOnlyDatabase:
    """A live, WAL-aware SQLite read view that cannot run writes or migrations."""

    def __init__(self, db_path: str):
        path = Path(db_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        self.path = path
        self.conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        self.conn.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ReadOnlyDatabase":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def enabled_accounts(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, value, user_id, xianyu_unb, browser_user_agent, "
            "cookie_revision, auto_rate_enabled_at "
            "FROM cookies WHERE auto_rate_enabled = 1 "
            "AND auto_rate_enabled_at IS NOT NULL ORDER BY id"
        ).fetchall()
        return [
            {
                "cookie_id": str(row[0]),
                "cookie_string": str(row[1] or ""),
                "user_id": int(row[2]),
                "xianyu_unb": str(row[3] or ""),
                "browser_user_agent": str(row[4] or ""),
                "cookie_revision": int(row[5] or 0),
                "enabled_at": float(row[6]),
            }
            for row in rows
        ]

    def tracked_keys(self) -> Set[Tuple[int, str, str]]:
        return {
            (int(user_id), str(cookie_id), str(order_id))
            for user_id, cookie_id, order_id in self.conn.execute(
                "SELECT user_id, cookie_id, order_id FROM order_auto_ratings"
            ).fetchall()
        }


def _client() -> XianyuOrderListClient:
    return XianyuOrderListClient(
        max_pages=MAX_PAGES,
        max_orders=MAX_ORDERS,
        request_interval=REQUEST_INTERVAL,
    )


def _empty_account_summary(slot: int) -> Dict[str, Any]:
    return {
        "account_slot": slot,
        "success": False,
        "complete": False,
        "coverage": "unknown",
        "pages_scanned": 0,
        "truncated": False,
        "rateable": 0,
        "tracked": 0,
        "new_candidates": 0,
        "invalid_records": 0,
        "error_code": "",
    }


async def discover_historical_candidates(
    accounts: Iterable[Dict[str, Any]],
    tracked_keys: Set[Tuple[int, str, str]],
    *,
    client_factory: Callable[[], XianyuOrderListClient] = _client,
) -> Dict[str, Any]:
    """Scan all enabled accounts without touching the database."""
    summaries: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for slot, account in enumerate(accounts, 1):
        summary = _empty_account_summary(slot)
        try:
            discovery = await client_factory().discover(
                cookie_id=account["cookie_id"],
                cookie_string=account["cookie_string"],
                days=SCAN_DAYS,
                user_agent=account["browser_user_agent"],
            )
        except Exception as exc:
            summary["error_code"] = f"{type(exc).__name__}"
            summaries.append(summary)
            continue

        raw_coverage = discovery.get("coverage")
        # Production may still run the older compatible client response shape;
        # a successful, non-truncated list is complete unless it explicitly
        # declares a partial coverage mode.
        coverage = (
            str(raw_coverage)
            if raw_coverage is not None
            else "full_recent"
            if not discovery.get("truncated")
            else "unknown"
        )
        summary.update(
            success=bool(discovery.get("success")),
            coverage=coverage,
            pages_scanned=int(discovery.get("pages_scanned") or 0),
            truncated=bool(discovery.get("truncated")),
            error_code=str(discovery.get("error_code") or ""),
        )
        if not summary["success"]:
            summaries.append(summary)
            continue

        summary["complete"] = (
            summary["coverage"] == "full_recent" and not summary["truncated"]
        )
        for order in discovery.get("orders") or []:
            if not order.get("can_rate"):
                continue
            summary["rateable"] += 1
            order_id = str(order.get("order_id") or "").strip()
            created_at = _parse_order_timestamp(order.get("created_at"))
            if not order_id or created_at is None:
                summary["invalid_records"] += 1
                continue
            key = (int(account["user_id"]), account["cookie_id"], order_id)
            if key in tracked_keys:
                summary["tracked"] += 1
                continue
            summary["new_candidates"] += 1
            candidates.append(
                {
                    "account": account,
                    "order_id": order_id,
                    "item_title": str(order.get("item_title") or ""),
                    "order_created_at": float(created_at),
                    "updated_cookie_string": str(
                        discovery.get("updated_cookie_string") or ""
                    ),
                }
            )
        summaries.append(summary)

    incomplete = any(not summary["complete"] for summary in summaries)
    invalid = sum(int(summary["invalid_records"]) for summary in summaries)
    return {
        "accounts": summaries,
        "candidates": candidates,
        "incomplete": incomplete,
        "invalid_records": invalid,
        "rateable": sum(int(summary["rateable"]) for summary in summaries),
        "tracked": sum(int(summary["tracked"]) for summary in summaries),
        "new_candidates": sum(
            int(summary["new_candidates"]) for summary in summaries
        ),
    }


def _public_report(scan: Dict[str, Any], *, mode: str, applied: int = 0) -> Dict[str, Any]:
    return {
        "mode": mode,
        "account_count": len(scan["accounts"]),
        "accounts": scan["accounts"],
        "rateable": scan["rateable"],
        "tracked": scan["tracked"],
        "new_candidates": scan["new_candidates"],
        "invalid_records": scan["invalid_records"],
        "incomplete": bool(scan["incomplete"]),
        "applied": int(applied),
        "apply_allowed": not scan["incomplete"] and not scan["invalid_records"],
    }


def _write_manifest(path: Path, task_ids: List[int], started_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"created_at": started_at, "task_ids": task_ids},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _persist_cookie_update(db: Any, candidate: Dict[str, Any]) -> bool:
    updated = candidate.get("updated_cookie_string") or ""
    if not updated or updated == candidate["account"]["cookie_string"]:
        return True
    account = candidate["account"]
    result = db.compare_and_swap_cookie_session(
        account["cookie_id"],
        user_id=account["user_id"],
        expected_xianyu_unb=account["xianyu_unb"],
        expected_revision=account["cookie_revision"],
        cookie_value=updated,
        browser_user_agent=account["browser_user_agent"],
    )
    return result.get("state") in {"updated", "unchanged"}


def apply_candidates(scan: Dict[str, Any], manifest_path: Path) -> int:
    """Schedule candidates only after a complete, valid re-scan."""
    if scan["incomplete"] or scan["invalid_records"]:
        return 0
    from db_manager import db_manager

    started_at = time.time()
    task_ids: List[int] = []
    applied = 0
    refreshed_accounts: Set[Tuple[int, str]] = set()
    for candidate in scan["candidates"]:
        account = candidate["account"]
        account_key = (int(account["user_id"]), str(account["cookie_id"]))
        if account_key not in refreshed_accounts:
            if not _persist_cookie_update(db_manager, candidate):
                continue
            refreshed_accounts.add(account_key)
        inserted = db_manager.schedule_auto_rate_task(
            user_id=account["user_id"],
            cookie_id=account["cookie_id"],
            order_id=candidate["order_id"],
            item_title=candidate["item_title"],
            order_created_at=candidate["order_created_at"],
            due_at=started_at + random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS),
            now=started_at,
            allow_historical=True,
        )
        if not inserted:
            continue
        row = db_manager.conn.execute(
            "SELECT id FROM order_auto_ratings WHERE user_id = ? "
            "AND cookie_id = ? AND order_id = ?",
            (account["user_id"], account["cookie_id"], candidate["order_id"]),
        ).fetchone()
        if row:
            task_ids.append(int(row[0]))
        applied += 1
        _write_manifest(manifest_path, task_ids, started_at)
    return applied


async def run(args: argparse.Namespace) -> int:
    db_path = os.getenv("DB_PATH", "data/xianyu_data.db")
    with ReadOnlyDatabase(db_path) as readonly:
        accounts = readonly.enabled_accounts()
        tracked = readonly.tracked_keys()
        scan = await discover_historical_candidates(accounts, tracked)

    if args.apply:
        applied = apply_candidates(scan, Path(args.manifest_path))
        report = _public_report(scan, mode="apply", applied=applied)
    else:
        report = _public_report(scan, mode="dry_run")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if args.apply and (scan["incomplete"] or scan["invalid_records"]):
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="schedule the freshly discovered candidates; never submits a review directly",
    )
    parser.add_argument(
        "--manifest-path",
        default="auto-rate-backfill-manifest.json",
        help="private rollback manifest for task ids created by --apply",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
