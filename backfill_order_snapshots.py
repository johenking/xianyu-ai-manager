#!/usr/bin/env python3
"""历史订单快照回填脚本（一次性，幂等可复跑）。

背景：订单历史上只存了平台原始文本（amount 文本、created_at 混合时区）且不存
商品/买家快照，展示靠 (cookie_id, item_id) 实时 join 商品目录。迁移
2026072601/2026072602 只加列不写值，历史数据的解析与回填全部由本脚本承担：

- item_title / item_image：从当前商品目录近似回填，各自来源标 catalog_backfill
  （最低可信档，后续任何真实订单报文来源都可棘轮升级）；目录里也找不到的
  订单标 history_unsaved，如实承认历史未保存，不伪装成成交时信息。
- paid_amount_fen：amount 文本 → 整数分；解析失败留 NULL，绝不用 0 冒充。
- ordered_at_utc：created_at → UTC epoch。历史 created_at 无法区分
  「SQLite CURRENT_TIMESTAMP(UTC)」与「平台北京时间串」，无时区字符串一律按
  北京时间解释并把出处标为 backfill_cst_assumed——此标记允许后续真实报文
  解析结果纠正（见 apply_order_sync_update）。
- buyer_*：历史订单没有买家昵称/头像可恢复，buyer_snapshot_source 标
  history_unsaved；同时按 buyer_id 播种 customer_profiles 的首次/最近
  观察时间（INSERT OR IGNORE + min/max 更新，复跑不膨胀观察计数）。

安全设计：
- 默认「只读演练」，只统计不写库；加 --apply 才真正 UPDATE。
- 所有 UPDATE 带空值断言（IS NULL / = ''），只填补空位，绝不覆盖已有快照。
- dry-run 用 SQLite `mode=ro` 直接读取 DB_PATH，不初始化 DBManager、不迁移、不建密钥；
  只有显式 --apply 才初始化 DBManager 并执行版本化迁移。

用法：
    # dev 只读演练
    .venv/bin/python backfill_order_snapshots.py
    # dev 正式回填
    .venv/bin/python backfill_order_snapshots.py --apply
    # 生产：先备份库，再指定库路径演练 / 回填
    DB_PATH="/path/to/prod.db" .venv/bin/python backfill_order_snapshots.py
    DB_PATH="/path/to/prod.db" .venv/bin/python backfill_order_snapshots.py --apply
"""
import argparse
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path


_ORDER_SCAN_COLUMNS = (
    ("order_id", "''"),
    ("cookie_id", "NULL"),
    ("item_id", "''"),
    ("buyer_id", "''"),
    ("amount", "NULL"),
    ("created_at", "NULL"),
    ("item_title", "''"),
    ("item_image", "''"),
    ("item_snapshot_source", "''"),
    ("item_title_source", "''"),
    ("item_image_source", "''"),
    ("buyer_nickname", "''"),
    ("buyer_avatar_url", "''"),
    ("buyer_snapshot_source", "''"),
    ("paid_amount_fen", "NULL"),
    ("ordered_at_utc", "NULL"),
)


class _ReadOnlyDatabase:
    """dry-run 专用最小数据库视图；不会初始化 DBManager 或执行迁移。"""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            f"{Path(self.db_path).as_uri()}?mode=ro&immutable=1",
            uri=True,
        )


def _table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if not exists:
        return set()
    return {
        str(row[1])
        for row in cursor.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    }


def _read_orders(cursor: sqlite3.Cursor) -> list[tuple]:
    """按当前物理 schema 动态投影；旧库缺失的新列用只读默认值代替。"""
    columns = _table_columns(cursor, "orders")
    if not columns:
        return []
    projection = [
        f'"{name}"' if name in columns else f"{default_sql} AS \"{name}\""
        for name, default_sql in _ORDER_SCAN_COLUMNS
    ]
    return cursor.execute(
        f"SELECT {', '.join(projection)} FROM orders"
    ).fetchall()


def _valid_account_ids(cursor: sqlite3.Cursor) -> set[str]:
    columns = _table_columns(cursor, "cookies")
    if "id" not in columns:
        return set()
    return {
        str(row[0])
        for row in cursor.execute(
            "SELECT id FROM cookies WHERE id IS NOT NULL AND id != ''"
        ).fetchall()
    }


def _catalog_lookup(
    cursor: sqlite3.Cursor,
    cookie_ids: list[str],
) -> dict[tuple[str, str], dict[str, str]]:
    columns = _table_columns(cursor, "item_info")
    if not cookie_ids or not {"cookie_id", "item_id"} <= columns:
        return {}
    placeholders = ",".join("?" for _ in cookie_ids)
    title_expr = "item_title" if "item_title" in columns else "''"
    image_expr = "item_image" if "item_image" in columns else "''"
    price_expr = "item_price" if "item_price" in columns else "''"
    rows = cursor.execute(
        f"SELECT cookie_id, item_id, {title_expr}, {image_expr}, {price_expr}"
        f" FROM item_info WHERE cookie_id IN ({placeholders})",
        cookie_ids,
    ).fetchall()
    return {
        (str(row[0]), str(row[1])): {
            "item_title": str(row[2] or ""),
            "item_image": str(row[3] or ""),
            "item_price": str(row[4] or ""),
        }
        for row in rows
    }


def _plan_backfill(
    rows: list[tuple],
    catalog_lookup: dict[tuple[str, str], dict[str, str]],
    valid_accounts: set[str],
    *,
    parse_amount_fen,
    parse_order_time_utc,
    now: float,
) -> dict:
    """把同一事务内的扫描结果转换为带空值 CAS 的写入计划。"""
    plan = {
        "now": now,
        "title_fill": [],
        "image_fill": [],
        "title_source_fill": [],
        "image_source_fill": [],
        "item_source_fill": [],
        "amount_fill": [],
        "time_fill": [],
        "buyer_unsaved": [],
        "profile_seeds": {},
        "item_unsaved": 0,
        "amount_unparseable": 0,
        "time_unparseable": 0,
        "orphan_orders": 0,
        "catalog_skipped": 0,
        "profile_skipped": 0,
    }

    for (
        order_id,
        cookie_id,
        item_id,
        buyer_id,
        amount,
        created_at,
        item_title,
        item_image,
        item_snapshot_source,
        item_title_source,
        item_image_source,
        buyer_nickname,
        buyer_avatar_url,
        buyer_snapshot_source,
        paid_amount_fen,
        ordered_at_utc,
    ) in rows:
        order_key = str(order_id)
        cookie_key = str(cookie_id or "")
        account_is_valid = bool(
            cookie_key and cookie_key in valid_accounts
        )
        if not account_is_valid:
            plan["orphan_orders"] += 1
            plan["catalog_skipped"] += 1
        catalog_item = (
            catalog_lookup.get((cookie_key, str(item_id or ""))) or {}
            if account_is_valid
            else {}
        )

        # 商品快照：只补空位；两者都补不上且从未标注来源 → history_unsaved。
        recovered_item = False
        if not (item_title or ""):
            catalog_title = str(catalog_item.get("item_title") or "").strip()
            if catalog_title:
                plan["title_fill"].append((catalog_title, order_key))
                plan["title_source_fill"].append(
                    ("catalog_backfill", order_key)
                )
                recovered_item = True
        if not (item_image or ""):
            catalog_image = str(catalog_item.get("item_image") or "").strip()
            if catalog_image:
                plan["image_fill"].append((catalog_image, order_key))
                plan["image_source_fill"].append(
                    ("catalog_backfill", order_key)
                )
                recovered_item = True
        if item_title and not (item_title_source or ""):
            plan["title_source_fill"].append(
                (str(item_snapshot_source or "history_unsaved"), order_key)
            )
        if item_image and not (item_image_source or ""):
            plan["image_source_fill"].append(
                (str(item_snapshot_source or "history_unsaved"), order_key)
            )
        if not (item_snapshot_source or ""):
            if recovered_item:
                plan["item_source_fill"].append(
                    ("catalog_metadata", order_key)
                )
            elif not (item_title or "") and not (item_image or ""):
                plan["item_source_fill"].append(
                    ("history_unsaved", order_key)
                )
                plan["item_unsaved"] += 1

        # 金额和时间属于订单本地事实，以全局主键 order_id 回填，
        # 即使旧行 cookie_id 为 NULL 也不丢失可恢复数据。
        if paid_amount_fen is None:
            fen = parse_amount_fen(amount)
            if fen is None:
                plan["amount_unparseable"] += 1
            else:
                plan["amount_fill"].append((fen, order_key))

        epoch = None
        if ordered_at_utc is None:
            epoch, source = parse_order_time_utc(created_at)
            if epoch is None:
                plan["time_unparseable"] += 1
            else:
                if source == "cst_string":
                    source = "backfill_cst_assumed"
                epoch = float(epoch)
                plan["time_fill"].append((epoch, source, order_key))
        else:
            epoch = float(ordered_at_utc)

        if (
            not (buyer_nickname or "")
            and not (buyer_avatar_url or "")
            and not (buyer_snapshot_source or "")
        ):
            plan["buyer_unsaved"].append(order_key)
        buyer_key = str(buyer_id or "").strip()
        if not account_is_valid:
            if buyer_key:
                plan["profile_skipped"] += 1
        elif buyer_key and epoch is not None:
            window = plan["profile_seeds"].setdefault(
                (cookie_key, buyer_key), [epoch, epoch]
            )
            window[0] = min(window[0], epoch)
            window[1] = max(window[1], epoch)

    return plan


def _apply_backfill_plan(cursor: sqlite3.Cursor, plan: dict) -> dict[str, int]:
    """执行写入计划；每一条仍带空值 CAS，复跑和并发均不覆盖新事实。"""
    counts: dict[str, int] = {}
    counts["item_title"] = 0
    for title, order_id in plan["title_fill"]:
        cursor.execute(
            "UPDATE orders SET item_title = ?, item_snapshot_at = ?"
            " WHERE order_id = ? AND (item_title IS NULL OR item_title = '')",
            (title, plan["now"], order_id),
        )
        counts["item_title"] += cursor.rowcount

    counts["item_image"] = 0
    for image, order_id in plan["image_fill"]:
        cursor.execute(
            "UPDATE orders SET item_image = ?, item_snapshot_at = ?"
            " WHERE order_id = ? AND (item_image IS NULL OR item_image = '')",
            (image, plan["now"], order_id),
        )
        counts["item_image"] += cursor.rowcount

    counts["item_title_source"] = 0
    for source, order_id in plan["title_source_fill"]:
        cursor.execute(
            "UPDATE orders SET item_title_source = ?"
            " WHERE order_id = ?"
            " AND (item_title_source IS NULL OR item_title_source = '')",
            (source, order_id),
        )
        counts["item_title_source"] += cursor.rowcount

    counts["item_image_source"] = 0
    for source, order_id in plan["image_source_fill"]:
        cursor.execute(
            "UPDATE orders SET item_image_source = ?"
            " WHERE order_id = ?"
            " AND (item_image_source IS NULL OR item_image_source = '')",
            (source, order_id),
        )
        counts["item_image_source"] += cursor.rowcount

    counts["item_snapshot_source"] = 0
    for source, order_id in plan["item_source_fill"]:
        cursor.execute(
            "UPDATE orders SET item_snapshot_source = ?"
            " WHERE order_id = ?"
            " AND (item_snapshot_source IS NULL OR item_snapshot_source = '')",
            (source, order_id),
        )
        counts["item_snapshot_source"] += cursor.rowcount

    counts["paid_amount_fen"] = 0
    for fen, order_id in plan["amount_fill"]:
        cursor.execute(
            "UPDATE orders SET paid_amount_fen = ?"
            " WHERE order_id = ? AND paid_amount_fen IS NULL",
            (fen, order_id),
        )
        counts["paid_amount_fen"] += cursor.rowcount

    counts["ordered_at_utc"] = 0
    for epoch, source, order_id in plan["time_fill"]:
        cursor.execute(
            "UPDATE orders SET ordered_at_utc = ?, ordered_at_source = ?"
            " WHERE order_id = ? AND ordered_at_utc IS NULL",
            (epoch, source, order_id),
        )
        counts["ordered_at_utc"] += cursor.rowcount

    counts["buyer_snapshot_source"] = 0
    for order_id in plan["buyer_unsaved"]:
        cursor.execute(
            "UPDATE orders SET buyer_snapshot_source = 'history_unsaved'"
            " WHERE order_id = ?"
            " AND (buyer_snapshot_source IS NULL OR buyer_snapshot_source = '')"
            " AND (buyer_nickname IS NULL OR buyer_nickname = '')"
            " AND (buyer_avatar_url IS NULL OR buyer_avatar_url = '')",
            (order_id,),
        )
        counts["buyer_snapshot_source"] += cursor.rowcount

    counts["customer_profiles"] = 0
    for (cookie_key, buyer_key), (
        first_at,
        last_at,
    ) in plan["profile_seeds"].items():
        cursor.execute(
            "INSERT OR IGNORE INTO customer_profiles"
            " (cookie_id, buyer_id, profile_source, first_observed_at, last_observed_at)"
            " VALUES (?, ?, 'history_unsaved', ?, ?)",
            (cookie_key, buyer_key, first_at, last_at),
        )
        counts["customer_profiles"] += cursor.rowcount
        cursor.execute(
            "UPDATE customer_profiles SET"
            " first_observed_at = MIN(first_observed_at, ?),"
            " last_observed_at = MAX(last_observed_at, ?)"
            " WHERE cookie_id = ? AND buyer_id = ?",
            (first_at, last_at, cookie_key, buyer_key),
        )
    return counts


def _emit_plan_summary(plan: dict, emit) -> None:
    emit(f"商品标题可回填(目录近似): {len(plan['title_fill'])}")
    emit(f"商品图片可回填(目录近似): {len(plan['image_fill'])}")
    emit(f"商品快照历史未保存: {plan['item_unsaved']}")
    emit(
        f"金额可解析为分: {len(plan['amount_fill'])}，"
        f"无法解析: {plan['amount_unparseable']}"
    )
    emit(
        f"下单时间可解析: {len(plan['time_fill'])}，"
        f"无法解析: {plan['time_unparseable']}"
    )
    emit(f"买家快照历史未保存: {len(plan['buyer_unsaved'])}")
    emit(
        f"客户档案待播种: {len(plan['profile_seeds'])} 个 (cookie, buyer)"
    )
    emit(f"无账号孤儿订单: {plan['orphan_orders']}")
    emit(f"目录回填跳过(无账号): {plan['catalog_skipped']}")
    emit(f"客户档案播种跳过(无账号): {plan['profile_skipped']}")


def run_backfill(database, *, apply: bool, emit=print) -> dict:
    """扫描并执行回填；apply 模式从扫描前即持有 BEGIN IMMEDIATE。"""
    from order_sync_service import parse_amount_fen, parse_order_time_utc

    with database.lock:
        cursor = database.conn.cursor()
        if apply:
            cursor.execute("BEGIN IMMEDIATE")
        try:
            rows = _read_orders(cursor)
            emit(f"订单总数: {len(rows)}")
            if not rows:
                if apply:
                    database.conn.commit()
                emit("库中无订单，结束。")
                return {"total_orders": 0, "counts": {}}

            valid_accounts = _valid_account_ids(cursor)
            catalog_lookup = _catalog_lookup(
                cursor,
                sorted(valid_accounts),
            )
            plan = _plan_backfill(
                rows,
                catalog_lookup,
                valid_accounts,
                parse_amount_fen=parse_amount_fen,
                parse_order_time_utc=parse_order_time_utc,
                now=time.time(),
            )
            _emit_plan_summary(plan, emit)

            if not apply:
                emit("-" * 48)
                emit("只读演练结束，未写库。确认无误后加 --apply 正式回填。")
                return {
                    "total_orders": len(rows),
                    "plan": plan,
                    "counts": {},
                }

            counts = _apply_backfill_plan(cursor, plan)
            database.conn.commit()
            emit("-" * 48)
            emit("回填完成，实际写入：")
            for field, written in counts.items():
                emit(f"  {field}: {written}")
            return {
                "total_orders": len(rows),
                "plan": plan,
                "counts": counts,
            }
        except Exception:
            if apply and database.conn.in_transaction:
                database.conn.rollback()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史订单成交快照与规范化字段")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库；不加则仅只读演练输出统计",
    )
    args = parser.parse_args()

    if args.apply:
        # 只有显式 --apply 才允许初始化 DBManager、迁移与密钥。
        from db_manager import db_manager
    else:
        db_manager = _ReadOnlyDatabase(
            os.environ.get("DB_PATH", "data/xianyu_data.db")
        )

    print(f"数据库路径: {db_manager.db_path}")
    print(f"模式: {'正式回填 (--apply)' if args.apply else '只读演练 (dry-run)'}")
    print("-" * 48)
    try:
        run_backfill(db_manager, apply=args.apply)
    finally:
        if not args.apply:
            db_manager.conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
