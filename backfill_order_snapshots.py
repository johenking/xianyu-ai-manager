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


class _ReadOnlyDatabase:
    """dry-run 专用最小数据库视图；不会初始化 DBManager 或执行迁移。"""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            f"{Path(self.db_path).as_uri()}?mode=ro&immutable=1",
            uri=True,
        )

    def get_item_catalog_lookup(self, cookie_ids):
        if not cookie_ids:
            return {}
        placeholders = ','.join('?' for _ in cookie_ids)
        rows = self.conn.execute(
            "SELECT cookie_id, item_id, item_title, item_image, item_price"
            f" FROM item_info WHERE cookie_id IN ({placeholders})",
            [str(value) for value in cookie_ids],
        ).fetchall()
        return {
            (str(row[0]), str(row[1])): {
                "item_title": row[2] or "",
                "item_image": row[3] or "",
                "item_price": row[4] or "",
            }
            for row in rows
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史订单成交快照与规范化字段")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库；不加则仅只读演练输出统计",
    )
    args = parser.parse_args()

    from order_sync_service import parse_amount_fen, parse_order_time_utc

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

    with db_manager.lock:
        cursor = db_manager.conn.cursor()
        cursor.execute(
            """
            SELECT order_id, cookie_id, item_id, buyer_id, amount, created_at,
                   item_title, item_image, item_snapshot_source,
                   item_title_source, item_image_source,
                   buyer_nickname, buyer_snapshot_source,
                   paid_amount_fen, ordered_at_utc
            FROM orders
            """
        )
        rows = cursor.fetchall()

    total_orders = len(rows)
    print(f"订单总数: {total_orders}")
    if total_orders == 0:
        print("库中无订单，结束。")
        return 0

    cookie_ids = sorted({str(row[1]) for row in rows if row[1]})
    catalog_lookup = db_manager.get_item_catalog_lookup(cookie_ids)

    now = time.time()
    # 分类桶（统计口径见打印）
    title_fill = []        # (title, order_id, cookie_id)
    image_fill = []        # (image, order_id, cookie_id)
    title_source_fill = []
    image_source_fill = []
    item_source_fill = []  # (source, order_id, cookie_id)
    item_unsaved = 0
    amount_fill = []       # (fen, order_id, cookie_id)
    amount_unparseable = 0
    time_fill = []         # (epoch, source, order_id, cookie_id)
    time_unparseable = 0
    buyer_unsaved = []     # (order_id, cookie_id)
    profile_seeds = {}     # (cookie_id, buyer_id) -> [min_epoch, max_epoch]

    for (order_id, cookie_id, item_id, buyer_id, amount, created_at,
         item_title, item_image, item_snapshot_source,
         item_title_source, item_image_source,
         buyer_nickname, buyer_snapshot_source,
         paid_amount_fen, ordered_at_utc) in rows:
        order_id = str(order_id)
        cookie_key = str(cookie_id or "")
        catalog_item = catalog_lookup.get((cookie_key, str(item_id or ""))) or {}

        # 商品快照：只补空位；两者都补不上且从未标注来源 → history_unsaved
        recovered_item = False
        if not (item_title or ""):
            catalog_title = str(catalog_item.get("item_title") or "").strip()
            if catalog_title:
                title_fill.append((catalog_title, order_id, cookie_key))
                title_source_fill.append(("catalog_backfill", order_id, cookie_key))
                recovered_item = True
        if not (item_image or ""):
            catalog_image = str(catalog_item.get("item_image") or "").strip()
            if catalog_image:
                image_fill.append((catalog_image, order_id, cookie_key))
                image_source_fill.append(("catalog_backfill", order_id, cookie_key))
                recovered_item = True
        if item_title and not (item_title_source or ""):
            title_source_fill.append((
                str(item_snapshot_source or "history_unsaved"),
                order_id,
                cookie_key,
            ))
        if item_image and not (item_image_source or ""):
            image_source_fill.append((
                str(item_snapshot_source or "history_unsaved"),
                order_id,
                cookie_key,
            ))
        if not (item_snapshot_source or ""):
            if recovered_item:
                item_source_fill.append(("catalog_metadata", order_id, cookie_key))
            elif not (item_title or "") and not (item_image or ""):
                item_source_fill.append(("history_unsaved", order_id, cookie_key))
                item_unsaved += 1

        # 金额：文本 → 分；失败留 NULL
        if paid_amount_fen is None:
            fen = parse_amount_fen(amount)
            if fen is None:
                amount_unparseable += 1
            else:
                amount_fill.append((fen, order_id, cookie_key))

        # 时间：created_at → UTC epoch；无时区字符串标 backfill_cst_assumed
        epoch = None
        if ordered_at_utc is None:
            epoch, source = parse_order_time_utc(created_at)
            if epoch is None:
                time_unparseable += 1
            else:
                if source == "cst_string":
                    source = "backfill_cst_assumed"
                time_fill.append((float(epoch), source, order_id, cookie_key))
        else:
            epoch = float(ordered_at_utc)

        # 买家：历史无身份可恢复，如实标注；播种档案观察时间
        if not (buyer_nickname or "") and not (buyer_snapshot_source or ""):
            buyer_unsaved.append((order_id, cookie_key))
        buyer_key = str(buyer_id or "").strip()
        if cookie_key and buyer_key and epoch is not None:
            window = profile_seeds.setdefault((cookie_key, buyer_key), [epoch, epoch])
            window[0] = min(window[0], epoch)
            window[1] = max(window[1], epoch)

    print(f"商品标题可回填(目录近似): {len(title_fill)}")
    print(f"商品图片可回填(目录近似): {len(image_fill)}")
    print(f"商品快照历史未保存: {item_unsaved}")
    print(f"金额可解析为分: {len(amount_fill)}，无法解析: {amount_unparseable}")
    print(f"下单时间可解析: {len(time_fill)}，无法解析: {time_unparseable}")
    print(f"买家快照历史未保存: {len(buyer_unsaved)}")
    print(f"客户档案待播种: {len(profile_seeds)} 个 (cookie, buyer)")

    if not args.apply:
        print("-" * 48)
        print("只读演练结束，未写库。确认无误后加 --apply 正式回填。")
        db_manager.conn.close()
        return 0

    counts = {}
    with db_manager.lock:
        cursor = db_manager.conn.cursor()
        counts["item_title"] = 0
        for title, order_id, cookie_key in title_fill:
            cursor.execute(
                "UPDATE orders SET item_title = ?, item_snapshot_at = ?"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (item_title IS NULL OR item_title = '')",
                (title, now, order_id, cookie_key),
            )
            counts["item_title"] += cursor.rowcount
        counts["item_image"] = 0
        for image, order_id, cookie_key in image_fill:
            cursor.execute(
                "UPDATE orders SET item_image = ?, item_snapshot_at = ?"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (item_image IS NULL OR item_image = '')",
                (image, now, order_id, cookie_key),
            )
            counts["item_image"] += cursor.rowcount
        counts["item_title_source"] = 0
        for source, order_id, cookie_key in title_source_fill:
            cursor.execute(
                "UPDATE orders SET item_title_source = ?"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (item_title_source IS NULL OR item_title_source = '')",
                (source, order_id, cookie_key),
            )
            counts["item_title_source"] += cursor.rowcount
        counts["item_image_source"] = 0
        for source, order_id, cookie_key in image_source_fill:
            cursor.execute(
                "UPDATE orders SET item_image_source = ?"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (item_image_source IS NULL OR item_image_source = '')",
                (source, order_id, cookie_key),
            )
            counts["item_image_source"] += cursor.rowcount
        counts["item_snapshot_source"] = 0
        for source, order_id, cookie_key in item_source_fill:
            cursor.execute(
                "UPDATE orders SET item_snapshot_source = ?"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (item_snapshot_source IS NULL OR item_snapshot_source = '')",
                (source, order_id, cookie_key),
            )
            counts["item_snapshot_source"] += cursor.rowcount
        counts["paid_amount_fen"] = 0
        for fen, order_id, cookie_key in amount_fill:
            cursor.execute(
                "UPDATE orders SET paid_amount_fen = ?"
                " WHERE order_id = ? AND cookie_id = ? AND paid_amount_fen IS NULL",
                (fen, order_id, cookie_key),
            )
            counts["paid_amount_fen"] += cursor.rowcount
        counts["ordered_at_utc"] = 0
        for epoch, source, order_id, cookie_key in time_fill:
            cursor.execute(
                "UPDATE orders SET ordered_at_utc = ?, ordered_at_source = ?"
                " WHERE order_id = ? AND cookie_id = ? AND ordered_at_utc IS NULL",
                (epoch, source, order_id, cookie_key),
            )
            counts["ordered_at_utc"] += cursor.rowcount
        counts["buyer_snapshot_source"] = 0
        for order_id, cookie_key in buyer_unsaved:
            cursor.execute(
                "UPDATE orders SET buyer_snapshot_source = 'history_unsaved'"
                " WHERE order_id = ? AND cookie_id = ?"
                "   AND (buyer_snapshot_source IS NULL OR buyer_snapshot_source = '')"
                "   AND (buyer_nickname IS NULL OR buyer_nickname = '')",
                (order_id, cookie_key),
            )
            counts["buyer_snapshot_source"] += cursor.rowcount
        counts["customer_profiles"] = 0
        for (cookie_key, buyer_key), (first_at, last_at) in profile_seeds.items():
            cursor.execute(
                "INSERT OR IGNORE INTO customer_profiles"
                " (cookie_id, buyer_id, profile_source, first_observed_at, last_observed_at)"
                " VALUES (?, ?, 'history_unsaved', ?, ?)",
                (cookie_key, buyer_key, first_at, last_at),
            )
            counts["customer_profiles"] += cursor.rowcount
            # 已存在的档案只前移/后延观察窗口，不动身份与计数（复跑幂等）
            cursor.execute(
                "UPDATE customer_profiles SET"
                " first_observed_at = MIN(first_observed_at, ?),"
                " last_observed_at = MAX(last_observed_at, ?)"
                " WHERE cookie_id = ? AND buyer_id = ?",
                (first_at, last_at, cookie_key, buyer_key),
            )
        db_manager.conn.commit()

    print("-" * 48)
    print("回填完成，实际写入：")
    for field, written in counts.items():
        print(f"  {field}: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
