"""订单成交快照写入守卫、客户档案与服务端订单查询的行为测试。

覆盖：
- apply_order_sync_update 快照棘轮矩阵（空则填 / 低升高覆盖 / 高不被低冲 / 同级不覆盖）
- insert_or_update_order UPDATE 分支的 item_image 写一次守卫（实时/导入路径的共用面）
- 规范化字段只填空值；backfill_cst_assumed 允许被真实报文纠正
- OrderSyncCoordinator 列表/详情阶段接线（快照、金额分、UTC 时间、客户观察）
- upsert_customer_observation 首次/复跑/身份棘轮/观察窗口
- delete_cookie 清理 customer_profiles
- query_orders 过滤/分页/隐私分离/跨账号隔离/搜索转义
- 回填脚本 dry-run 零写入与 --apply 幂等
"""

import os
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from db_manager import DBManager
from order_sync_service import OrderSyncCoordinator

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SnapshotWriteTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("account-1", "unb=account-1; cookie2=value", 1),
                    ("account-2", "unb=account-2; cookie2=value", 2),
                ),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def order_row(self, order_id, *columns):
        with self.db.lock:
            return self.db.conn.execute(
                f"SELECT {', '.join(columns)} FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()


class ApplySyncSnapshotRatchetTests(SnapshotWriteTestCase):
    def setUp(self):
        super().setUp()
        self.db.insert_or_update_order(
            order_id="order-1", item_id="item-1", buyer_id="buyer-1",
            order_status="pending_ship", cookie_id="account-1",
        )

    def apply(self, **kwargs):
        return self.db.apply_order_sync_update(
            order_id="order-1", cookie_id="account-1",
            incoming_status="pending_ship", **kwargs,
        )

    def test_empty_fields_filled_by_any_source(self):
        self.apply(item_snapshot={
            "item_title": "目录标题", "item_image": "https://img/cat.jpg", "source": "catalog",
        })
        row = self.order_row("order-1", "item_title", "item_image",
                             "item_snapshot_source", "item_snapshot_at")
        self.assertEqual(row[0], "目录标题")
        self.assertEqual(row[1], "https://img/cat.jpg")
        self.assertEqual(row[2], "catalog")
        self.assertIsNotNone(row[3])

    def test_higher_source_overwrites_and_invalidates_cache_key(self):
        self.apply(item_snapshot={
            "item_title": "目录标题", "item_image": "https://img/cat.jpg", "source": "catalog",
        })
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE orders SET item_image_cache_key = 'cache-old' WHERE order_id = 'order-1'"
            )
            self.db.conn.commit()
        self.apply(status_source="order_detail", item_snapshot={
            "item_title": "成交标题", "item_image": "https://img/deal.jpg", "source": "order_detail",
        })
        row = self.order_row("order-1", "item_title", "item_image",
                             "item_snapshot_source", "item_image_cache_key")
        self.assertEqual(row[0], "成交标题")
        self.assertEqual(row[1], "https://img/deal.jpg")
        self.assertEqual(row[2], "order_detail")
        self.assertEqual(row[3], "", "旧图缓存键必须随图片替换而作废")

    def test_lower_source_never_overwrites_existing_snapshot(self):
        self.apply(item_snapshot={
            "item_title": "列表标题", "item_image": "https://img/list.jpg", "source": "order_list",
        })
        self.apply(item_snapshot={
            "item_title": "目录改名", "item_image": "https://img/recat.jpg", "source": "catalog",
        })
        row = self.order_row("order-1", "item_title", "item_image", "item_snapshot_source")
        self.assertEqual(row[0], "列表标题")
        self.assertEqual(row[1], "https://img/list.jpg")
        self.assertEqual(row[2], "order_list")

    def test_same_rank_source_does_not_overwrite(self):
        self.apply(item_snapshot={"item_title": "第一次", "source": "order_list"})
        self.apply(item_snapshot={"item_title": "第二次", "source": "order_list"})
        self.assertEqual(self.order_row("order-1", "item_title")[0], "第一次")

    def test_lower_source_still_fills_remaining_empty_fields(self):
        self.apply(item_snapshot={"item_title": "列表标题", "source": "order_list"})
        self.apply(item_snapshot={"item_image": "https://img/cat.jpg", "source": "catalog"})
        row = self.order_row("order-1", "item_title", "item_image", "item_snapshot_source")
        self.assertEqual(row[0], "列表标题")
        self.assertEqual(row[1], "https://img/cat.jpg", "低级来源可填补空位")
        self.assertEqual(row[2], "order_list", "组来源单调不降")

    def test_title_and_image_keep_independent_sources(self):
        self.apply(item_snapshot={
            "item_title": "列表标题",
            "source": "order_list",
        })
        self.apply(item_snapshot={
            "item_image": "https://img/catalog.jpg",
            "source": "catalog_backfill",
        })
        row = self.order_row(
            "order-1",
            "item_title",
            "item_image",
            "item_title_source",
            "item_image_source",
        )
        self.assertEqual(row, (
            "列表标题",
            "https://img/catalog.jpg",
            "order_list",
            "catalog_backfill",
        ))

    def test_buyer_snapshot_group_uses_same_ratchet(self):
        self.apply(buyer_snapshot={
            "buyer_nickname": "买家A", "buyer_avatar_url": "https://a/1.jpg",
            "source": "realtime_message",
        })
        self.apply(buyer_snapshot={
            "buyer_nickname": "买家A改", "source": "catalog",
        })
        row = self.order_row("order-1", "buyer_nickname", "buyer_avatar_url",
                             "buyer_snapshot_source")
        self.assertEqual(row[0], "买家A")
        self.assertEqual(row[2], "realtime_message")
        self.apply(buyer_snapshot={"buyer_nickname": "买家A真名", "source": "order_detail"})
        self.assertEqual(self.order_row("order-1", "buyer_nickname")[0], "买家A真名")

    def test_legacy_item_image_kwarg_folds_into_snapshot(self):
        self.apply(status_source="order_list", item_image="https://img/legacy.jpg")
        row = self.order_row("order-1", "item_image", "item_snapshot_source")
        self.assertEqual(row[0], "https://img/legacy.jpg")
        self.assertEqual(row[1], "order_list")

    def test_normalized_fields_fill_null_only(self):
        self.apply(ordered_at=(1750000000.0, "cst_string"), paid_amount_fen=1250)
        self.apply(ordered_at=(1760000000.0, "cst_string"), paid_amount_fen=9999)
        row = self.order_row("order-1", "ordered_at_utc", "ordered_at_source", "paid_amount_fen")
        self.assertEqual(row[0], 1750000000.0)
        self.assertEqual(row[1], "cst_string")
        self.assertEqual(row[2], 1250)

    def test_backfill_assumed_time_is_corrected_by_real_parse(self):
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE orders SET ordered_at_utc = 111.0,"
                " ordered_at_source = 'backfill_cst_assumed' WHERE order_id = 'order-1'"
            )
            self.db.conn.commit()
        self.apply(ordered_at=(222.0, "epoch"))
        row = self.order_row("order-1", "ordered_at_utc", "ordered_at_source")
        self.assertEqual(row[0], 222.0)
        self.assertEqual(row[1], "epoch")


class InsertOrUpdateGuardTests(SnapshotWriteTestCase):
    def test_update_branch_never_clobbers_existing_item_image(self):
        self.db.insert_or_update_order(
            order_id="order-1", item_id="item-1", cookie_id="account-1",
            order_status="pending_ship", item_image="https://img/first.jpg",
        )
        self.db.insert_or_update_order(
            order_id="order-1", cookie_id="account-1", item_image="https://img/second.jpg",
        )
        self.assertEqual(
            self.order_row("order-1", "item_image")[0], "https://img/first.jpg"
        )

    def test_update_branch_fills_empty_item_image(self):
        self.db.insert_or_update_order(
            order_id="order-1", item_id="item-x", cookie_id="account-1",
            order_status="pending_ship",
        )
        self.db.insert_or_update_order(
            order_id="order-1", cookie_id="account-1", item_image="https://img/late.jpg",
        )
        self.assertEqual(
            self.order_row("order-1", "item_image")[0], "https://img/late.jpg"
        )


class CustomerObservationTests(SnapshotWriteTestCase):
    def test_first_observation_creates_profile(self):
        self.assertTrue(self.db.upsert_customer_observation(
            "account-1", "buyer-1", "买家甲", "https://a/1.jpg", "order_list", 1000.0,
        ))
        profiles = self.db.get_customer_profiles(["account-1"])
        profile = profiles[("account-1", "buyer-1")]
        self.assertEqual(profile["display_name"], "买家甲")
        self.assertEqual(profile["first_observed_at"], 1000.0)
        self.assertEqual(profile["last_observed_at"], 1000.0)
        self.assertEqual(profile["observation_count"], 1)

    def test_repeat_observation_extends_window_and_ratchets_identity(self):
        self.db.upsert_customer_observation(
            "account-1", "buyer-1", "买家甲", "", "order_list", 2000.0,
        )
        # 更早的回溯观察前移 first_observed_at；低级来源不改名
        self.db.upsert_customer_observation(
            "account-1", "buyer-1", "目录名", "", "catalog", 1000.0,
        )
        # 更高级来源可纠正身份
        self.db.upsert_customer_observation(
            "account-1", "buyer-1", "真实昵称", "https://a/real.jpg", "order_detail", 3000.0,
        )
        profile = self.db.get_customer_profiles(["account-1"])[("account-1", "buyer-1")]
        self.assertEqual(profile["display_name"], "真实昵称")
        self.assertEqual(profile["avatar_url"], "https://a/real.jpg")
        self.assertEqual(profile["profile_source"], "order_detail")
        self.assertEqual(profile["first_observed_at"], 1000.0)
        self.assertEqual(profile["last_observed_at"], 3000.0)
        self.assertEqual(profile["observation_count"], 3)

    def test_name_and_avatar_sources_ratchet_independently(self):
        self.db.upsert_customer_observation(
            "account-1", "buyer-fields", "权威昵称", "", "order_detail", 1000.0,
        )
        # 低级来源可填头像空值，但不得降低已有昵称的来源。
        self.db.upsert_customer_observation(
            "account-1", "buyer-fields", "目录昵称", "https://a/catalog.jpg",
            "catalog", 2000.0,
        )
        profile = self.db.get_customer_profiles(["account-1"])[
            ("account-1", "buyer-fields")
        ]
        self.assertEqual(profile["display_name"], "权威昵称")
        self.assertEqual(profile["display_name_source"], "order_detail")
        self.assertEqual(profile["avatar_url"], "https://a/catalog.jpg")
        self.assertEqual(profile["avatar_source"], "catalog")
        self.assertEqual(profile["profile_source"], "order_detail")

        # 中级来源只升级头像；同次携带的昵称冲不掉更高级非空值。
        self.db.upsert_customer_observation(
            "account-1", "buyer-fields", "实时昵称", "https://a/realtime.jpg",
            "realtime_message", 3000.0,
        )
        profile = self.db.get_customer_profiles(["account-1"])[
            ("account-1", "buyer-fields")
        ]
        self.assertEqual(profile["display_name"], "权威昵称")
        self.assertEqual(profile["display_name_source"], "order_detail")
        self.assertEqual(profile["avatar_url"], "https://a/realtime.jpg")
        self.assertEqual(profile["avatar_source"], "realtime_message")
        self.assertEqual(profile["profile_source"], "order_detail")

        # 高级来源封顶；后续低级观察不得覆盖任一非空字段。
        self.db.upsert_customer_observation(
            "account-1", "buyer-fields", "", "https://a/detail.jpg",
            "order_detail", 4000.0,
        )
        self.db.upsert_customer_observation(
            "account-1", "buyer-fields", "回退昵称", "https://a/fallback.jpg",
            "catalog", 5000.0,
        )
        profile = self.db.get_customer_profiles(["account-1"])[
            ("account-1", "buyer-fields")
        ]
        self.assertEqual(profile["display_name"], "权威昵称")
        self.assertEqual(profile["display_name_source"], "order_detail")
        self.assertEqual(profile["avatar_url"], "https://a/detail.jpg")
        self.assertEqual(profile["avatar_source"], "order_detail")
        self.assertEqual(profile["profile_source"], "order_detail")

    def test_missing_ids_are_rejected(self):
        self.assertFalse(self.db.upsert_customer_observation("", "buyer-1"))
        self.assertFalse(self.db.upsert_customer_observation("account-1", ""))

    def test_empty_high_rank_observation_does_not_create_or_mask_real_source(self):
        self.assertFalse(self.db.upsert_customer_observation(
            "account-1", "buyer-1", "", "", "order_list", 1000.0,
        ))
        self.assertNotIn(
            ("account-1", "buyer-1"),
            self.db.get_customer_profiles(["account-1"]),
        )
        self.assertTrue(self.db.upsert_customer_observation(
            "account-1", "buyer-1", "实时昵称", "", "realtime_message", 2000.0,
        ))
        profile = self.db.get_customer_profiles(["account-1"])[("account-1", "buyer-1")]
        self.assertEqual(profile["display_name"], "实时昵称")
        self.assertEqual(profile["profile_source"], "realtime_message")

        # 兼容历史上已误建的高等级空记录：低等级真实值填空时必须接管来源。
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO customer_profiles"
                " (cookie_id, buyer_id, display_name, avatar_url, profile_source,"
                " first_observed_at, last_observed_at, observation_count)"
                " VALUES ('account-1', 'buyer-legacy', '', '', 'order_list', 1, 1, 1)"
            )
            self.db.conn.commit()
        self.assertTrue(self.db.upsert_customer_observation(
            "account-1", "buyer-legacy", "后来实时昵称", "", "realtime_message", 2.0,
        ))
        legacy = self.db.get_customer_profiles(["account-1"])[
            ("account-1", "buyer-legacy")
        ]
        self.assertEqual(legacy["display_name"], "后来实时昵称")
        self.assertEqual(legacy["profile_source"], "realtime_message")

    def test_delete_cookie_removes_customer_profiles(self):
        self.db.upsert_customer_observation(
            "account-1", "buyer-1", "买家甲", "", "order_list", 1000.0,
        )
        self.db.upsert_customer_observation(
            "account-2", "buyer-9", "买家乙", "", "order_list", 1000.0,
        )
        self.assertTrue(self.db.delete_cookie("account-1"))
        remaining = self.db.get_customer_profiles(["account-1", "account-2"])
        self.assertNotIn(("account-1", "buyer-1"), remaining)
        self.assertIn(("account-2", "buyer-9"), remaining)


class CoordinatorWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("account-1", "unb=account-1; cookie2=value", 1),
            )
            self.db.conn.commit()

    async def asyncTearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    async def test_list_phase_persists_snapshots_amount_time_and_customer(self):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_image)"
                " VALUES ('account-1', 'item-1', '目录标题', 'https://img/catalog.jpg')"
            )
            self.db.conn.commit()

        async def discoverer(**_kwargs):
            return {
                "success": True,
                "orders": [{
                    "order_id": "order-new",
                    "item_id": "item-1",
                    "buyer_id": "buyer-1",
                    "buyer_nickname": "买家甲",
                    "buyer_avatar_url": "https://a/1.jpg",
                    "item_title": "成交标题",
                    "quantity": "1",
                    "amount": "¥12.50",
                    "order_status": "pending_ship",
                    "created_at": "2026-07-20 10:00:00",
                }],
            }

        coordinator = OrderSyncCoordinator(self.db, discoverer=discoverer)
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )
        self.assertTrue(result["success"])
        order = self.db.get_order_by_id("order-new")
        self.assertEqual(order["item_title"], "成交标题")
        self.assertEqual(order["item_snapshot_source"], "order_list")
        self.assertEqual(order["item_title_source"], "order_list")
        self.assertEqual(order["item_image"], "https://img/catalog.jpg")
        self.assertEqual(order["item_image_source"], "catalog")
        self.assertEqual(order["buyer_nickname"], "买家甲")
        self.assertEqual(order["buyer_avatar_url"], "https://a/1.jpg")
        self.assertEqual(order["paid_amount_fen"], 1250)
        # 2026-07-20 10:00 北京时间 = 02:00 UTC
        self.assertEqual(order["ordered_at_utc"], 1784512800.0)
        self.assertEqual(order["ordered_at_source"], "cst_string")
        profile = self.db.get_customer_profiles(["account-1"])[("account-1", "buyer-1")]
        self.assertEqual(profile["display_name"], "买家甲")
        self.assertEqual(profile["first_observed_at"], 1784512800.0)

    async def test_detail_phase_upgrades_snapshot_sources(self):
        self.db.insert_or_update_order(
            order_id="order-1", item_id="item-1", buyer_id="buyer-1",
            order_status="pending_ship", cookie_id="account-1",
        )
        self.db.apply_order_sync_update(
            order_id="order-1", cookie_id="account-1", incoming_status="pending_ship",
            status_source="order_list",
            item_snapshot={"item_title": "列表标题", "source": "order_list"},
        )

        async def discoverer(**_kwargs):
            return {"success": True, "orders": []}

        async def detail_fetcher(order_ids, cookie_id, cookie_string):
            return [{
                "order_id": "order-1",
                "order_status": "3",
                "status_text": "交易成功",
                "item_title": "详情标题",
                "item_image": "https://img/detail.jpg",
                "buyer_nickname": "详情昵称",
                "buyer_id": "buyer-1",
                "amount": "12.50",
                "order_time": "2026-07-20 10:00:00",
            }]

        coordinator = OrderSyncCoordinator(
            self.db, discoverer=discoverer, detail_fetcher=detail_fetcher,
        )
        result = await coordinator.sync_account(
            cookie_id="account-1",
            cookie_string="unb=account-1; cookie2=value",
            days=90,
        )
        self.assertTrue(result["success"])
        order = self.db.get_order_by_id("order-1")
        self.assertEqual(order["item_title"], "详情标题")
        self.assertEqual(order["item_image"], "https://img/detail.jpg")
        self.assertEqual(order["item_snapshot_source"], "order_detail")
        self.assertEqual(order["buyer_nickname"], "详情昵称")
        self.assertEqual(order["buyer_snapshot_source"], "order_detail")


class QueryOrdersTests(SnapshotWriteTestCase):
    def seed(self):
        for index, (order_id, cookie, status, amount, created) in enumerate((
            ("order-a1", "account-1", "pending_ship", "¥10.00", "2026-07-01 12:00:00"),
            ("order-a2", "account-1", "completed", "¥20.00", "2026-07-10 12:00:00"),
            ("order-a3", "account-1", "unknown", "", "2026-07-20 12:00:00"),
            ("order-b1", "account-2", "completed", "¥99.00", "2026-07-10 12:00:00"),
        )):
            self.db.insert_or_update_order(
                order_id=order_id, item_id=f"item-{index}", buyer_id=f"buyer-{index}",
                amount=amount, order_status=status, cookie_id=cookie,
                created_at=created, receiver_name="张三", receiver_phone="13800000000",
                receiver_address="某地", receiver_city="某市",
            )
        self.db.apply_order_sync_update(
            order_id="order-a1", cookie_id="account-1", incoming_status="pending_ship",
            item_snapshot={"item_title": "快照唯一标题", "source": "order_list"},
            buyer_snapshot={"buyer_nickname": "快照买家", "source": "order_list"},
        )

    def test_privacy_fields_absent_from_list_and_present_in_detail(self):
        self.seed()
        result = self.db.query_orders(["account-1"])
        self.assertEqual(result["total"], 3)
        for item in result["items"]:
            for key in ("receiver_name", "receiver_phone", "receiver_address", "receiver_city"):
                self.assertNotIn(key, item)
        detail = self.db.get_order_by_id("order-a1")
        self.assertEqual(detail["receiver_name"], "张三")
        self.assertEqual(detail["receiver_phone"], "13800000000")

    def test_cross_account_isolation_and_status_filter(self):
        self.seed()
        result = self.db.query_orders(["account-1"], status="completed")
        self.assertEqual([item["order_id"] for item in result["items"]], ["order-a2"])
        self.assertEqual(self.db.query_orders([], status="completed")["total"], 0)

    def test_search_matches_snapshot_title_and_escapes_like(self):
        self.seed()
        hits = self.db.query_orders(["account-1"], search="快照唯一")
        self.assertEqual([item["order_id"] for item in hits["items"]], ["order-a1"])
        self.assertEqual(self.db.query_orders(["account-1"], search="100%")["total"], 0)

    def test_search_matches_customer_profile_display_name_before_pagination(self):
        self.seed()
        self.db.upsert_customer_observation(
            "account-1", "buyer-2", "实时昵称唯一", "", "realtime_message", 1000.0,
        )
        result = self.db.query_orders(
            ["account-1"],
            search="实时昵称唯一",
            page=1,
            page_size=1,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual([row["order_id"] for row in result["items"]], ["order-a3"])

    def test_date_range_and_pagination(self):
        self.seed()
        window = self.db.query_orders(
            ["account-1"], start_date="2026-07-05", end_date="2026-07-15",
        )
        self.assertEqual([item["order_id"] for item in window["items"]], ["order-a2"])
        first_page = self.db.query_orders(["account-1"], page=1, page_size=2)
        second_page = self.db.query_orders(["account-1"], page=2, page_size=2)
        self.assertEqual(first_page["total"], 3)
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertEqual(
            [item["order_id"] for item in first_page["items"]],
            ["order-a3", "order-a2"],
            "按成交时间轴倒序",
        )


class BackfillScriptTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "backfill.db")
        db = DBManager(self.db_path)
        with db.lock:
            db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES ('acct', 'unb=1', 1)"
            )
            db.conn.commit()
        db.insert_or_update_order(
            order_id="order-1", item_id="item-1", buyer_id="buyer-1",
            amount="¥12.50", order_status="completed", cookie_id="acct",
            created_at="2026-07-01 08:00:00",
        )
        db.insert_or_update_order(
            order_id="order-2", item_id="item-gone", buyer_id="buyer-2",
            amount="N/A", order_status="unknown", cookie_id="acct",
        )
        with db.lock:
            db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_image)"
                " VALUES ('acct', 'item-1', '目录标题', 'https://img/1.jpg')"
            )
            # 构造历史形态：清掉 insert 时的目录兜底图，交给回填脚本处理
            db.conn.execute("UPDATE orders SET item_image = ''")
            db.conn.commit()
        db.conn.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def run_script(self, *extra_args, env_overrides=None):
        env = dict(os.environ, DB_PATH=self.db_path, **(env_overrides or {}))
        return subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "backfill_order_snapshots.py"), *extra_args],
            capture_output=True, text=True, env=env, cwd=PROJECT_ROOT, timeout=120,
        )

    def snapshot_state(self):
        connection = sqlite3.connect(self.db_path)
        try:
            orders = connection.execute(
                "SELECT order_id, item_title, item_image, item_snapshot_source,"
                " paid_amount_fen, ordered_at_utc, ordered_at_source, buyer_snapshot_source"
                " FROM orders ORDER BY order_id"
            ).fetchall()
            profiles = connection.execute(
                "SELECT cookie_id, buyer_id, profile_source, observation_count"
                " FROM customer_profiles ORDER BY buyer_id"
            ).fetchall()
            return orders, profiles
        finally:
            connection.close()

    def test_dry_run_writes_nothing(self):
        before = self.snapshot_state()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("只读演练", result.stdout)
        self.assertEqual(self.snapshot_state(), before)

    def test_dry_run_is_physically_read_only_and_creates_no_keys(self):
        key_paths = {
            "ACCOUNT_CREDENTIAL_KEY_FILE": os.path.join(self.tempdir.name, "account.key"),
            "SYSTEM_SECRET_KEY_FILE": os.path.join(self.tempdir.name, "system.key"),
            "AI_PROVIDER_KEY_FILE": os.path.join(self.tempdir.name, "ai.key"),
        }
        before_bytes = Path(self.db_path).read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()
        before_stat = os.stat(self.db_path)
        connection = sqlite3.connect(self.db_path)
        before_schema = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        connection.close()

        result = self.run_script(env_overrides=key_paths)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(Path(self.db_path).read_bytes()).hexdigest(), before_hash)
        after_stat = os.stat(self.db_path)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        connection = sqlite3.connect(self.db_path)
        self.assertEqual(
            connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            before_schema,
        )
        connection.close()
        self.assertTrue(all(not Path(path).exists() for path in key_paths.values()))

    def test_apply_fills_and_marks_and_reruns_idempotently(self):
        result = self.run_script("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        orders, profiles = self.snapshot_state()
        order_1 = orders[0]
        self.assertEqual(order_1[1], "目录标题")
        self.assertEqual(order_1[2], "https://img/1.jpg")
        self.assertEqual(order_1[3], "catalog_metadata")
        self.assertEqual(order_1[4], 1250)
        # 2026-07-01 08:00 按北京时间解释 = 00:00 UTC
        self.assertEqual(order_1[5], 1782864000.0)
        self.assertEqual(order_1[6], "backfill_cst_assumed")
        self.assertEqual(order_1[7], "history_unsaved")
        connection = sqlite3.connect(self.db_path)
        self.assertEqual(
            connection.execute(
                "SELECT item_title_source, item_image_source FROM orders"
                " WHERE order_id = 'order-1'"
            ).fetchone(),
            ("catalog_backfill", "catalog_backfill"),
        )
        connection.close()
        order_2 = orders[1]
        self.assertEqual(order_2[3], "history_unsaved", "目录缺失如实标注")
        self.assertIsNone(order_2[4], "金额 N/A 不得补零")
        self.assertEqual(
            [(row[1], row[2], row[3]) for row in profiles],
            [("buyer-1", "history_unsaved", 1), ("buyer-2", "history_unsaved", 1)],
            "时间可解析（含 CURRENT_TIMESTAMP 默认值）的订单都播种档案，身份标历史未保存",
        )

        rerun = self.run_script("--apply")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(self.snapshot_state(), (orders, profiles), "复跑零变化")


if __name__ == "__main__":
    unittest.main()
