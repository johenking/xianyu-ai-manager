#!/usr/bin/env python3
"""历史订单图片回填脚本（一次性）。

背景：订单原本不存图片，列表接口靠 (cookie_id, item_id) 实时 join 商品目录取图；
商品一旦下架/删除，join 落空 → 订单图片变灰。本脚本把「当前仍能 join 到目录」的
订单主图写进 orders.item_image 快照列，让图片在商品后续下架后依然保留。

安全设计：
- 默认「只读演练」，只统计不写库；加 --apply 才真正 UPDATE。
- 只回填 item_image 为空的订单，绝不覆盖已有快照。
- join 不到目录（下架/从未同步的商品）的订单无法回填，如实计入 unrecoverable。
- 库路径复用 DBManager（读 DB_PATH 环境变量），与线上服务同库；DBManager 初始化
  会自动补 item_image 列，无需手动迁移。

用法：
    # dev 只读演练
    .venv/bin/python backfill_order_images.py
    # dev 正式回填
    .venv/bin/python backfill_order_images.py --apply
    # 生产：先备份库，再指定库路径演练 / 回填
    DB_PATH="/path/to/prod.db" .venv/bin/python backfill_order_images.py
    DB_PATH="/path/to/prod.db" .venv/bin/python backfill_order_images.py --apply
"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史订单图片快照")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库；不加则仅只读演练输出统计",
    )
    args = parser.parse_args()

    # 延迟导入：DBManager 初始化会读取 DB_PATH 并自动补 item_image 列
    from db_manager import db_manager

    print(f"数据库路径: {db_manager.db_path}")
    print(f"模式: {'正式回填 (--apply)' if args.apply else '只读演练 (dry-run)'}")
    print("-" * 48)

    with db_manager.lock:
        cursor = db_manager.conn.cursor()

        # 1. 取所有图片为空的订单
        cursor.execute(
            """
            SELECT order_id, cookie_id, item_id
            FROM orders
            WHERE (item_image IS NULL OR item_image = '')
            """
        )
        empty_rows = cursor.fetchall()
        total_empty = len(empty_rows)

        # 订单总数（用于展示占比）
        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]

    print(f"订单总数: {total_orders}")
    print(f"图片为空待回填: {total_empty}")

    if total_empty == 0:
        print("无待回填订单，结束。")
        return 0

    # 2. 按涉及的 cookie 批量取商品目录
    cookie_ids = sorted({str(row[1]) for row in empty_rows if row[1]})
    catalog_lookup = db_manager.get_item_catalog_lookup(cookie_ids)

    # 3. 逐单匹配目录主图
    fillable = []          # (order_id, cookie_id, image)
    unrecoverable = 0      # join 不到目录，或目录里该商品无图
    missing_item_id = 0    # 订单本身缺 item_id，无从关联

    for order_id, cookie_id, item_id in empty_rows:
        item_id_str = str(item_id or "")
        if not item_id_str:
            missing_item_id += 1
            unrecoverable += 1
            continue
        catalog_item = catalog_lookup.get((str(cookie_id or ""), item_id_str))
        image = (catalog_item or {}).get("item_image") or ""
        if image:
            fillable.append((str(order_id), str(cookie_id or ""), image))
        else:
            unrecoverable += 1

    print(f"可回填(仍能 join 到目录且有图): {len(fillable)}")
    print(f"无法回填(商品下架/删除/无图): {unrecoverable}"
          + (f"，其中缺 item_id: {missing_item_id}" if missing_item_id else ""))

    if not args.apply:
        print("-" * 48)
        print("只读演练结束，未写库。确认无误后加 --apply 正式回填。")
        return 0

    # 4. 正式回填：仅在 item_image 仍为空时写入，避免覆盖
    updated = 0
    with db_manager.lock:
        cursor = db_manager.conn.cursor()
        for order_id, cookie_id, image in fillable:
            cursor.execute(
                """
                UPDATE orders SET item_image = ?
                WHERE order_id = ? AND cookie_id = ?
                  AND (item_image IS NULL OR item_image = '')
                """,
                (image, order_id, cookie_id),
            )
            updated += cursor.rowcount
        db_manager.conn.commit()

    print("-" * 48)
    print(f"回填完成，实际写入: {updated} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
