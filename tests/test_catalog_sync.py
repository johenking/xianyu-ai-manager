import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from XianyuAutoAsync import (
    XianyuLive,
    extract_catalog_image_url,
    normalize_catalog_image_url,
)
from db_manager import DBManager
from reply_server import get_user_orders
from schema_migrations import _item_catalog_state_v1


class CatalogImageTests(unittest.TestCase):
    def test_primary_image_is_extracted_and_upgraded_to_https(self):
        self.assertEqual(
            extract_catalog_image_url({
                "picInfo": {"picUrl": "http://img.alicdn.com/item.heic"},
                "detailParams": {"picUrl": "http://fallback.invalid/item.jpg"},
            }),
            "https://img.alicdn.com/item.heic",
        )

    def test_image_infos_major_image_is_used_as_last_fallback(self):
        self.assertEqual(
            extract_catalog_image_url({
                "detailParams": {
                    "imageInfos": json.dumps([
                        {"major": False, "url": "//img.alicdn.com/secondary.jpg"},
                        {"major": True, "url": "//img.alicdn.com/primary.jpg"},
                    ])
                }
            }),
            "https://img.alicdn.com/primary.jpg",
        )
        self.assertEqual(normalize_catalog_image_url("javascript:alert(1)"), "")


class CatalogPersistenceTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("account-1", "unb=account-1", 1),
                    ("account-2", "unb=account-2", 1),
                ),
            )
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_detail) VALUES (?, ?, ?, ?)",
                ("account-1", "item-a", "旧标题", "保留的商品详情"),
            )
            self.db.conn.execute(
                "INSERT INTO ai_item_knowledge_profiles (cookie_id, item_id, draft_json, published_json) VALUES (?, ?, ?, ?)",
                ("account-1", "item-a", '{"overview":{"text":"草稿"}}', '{"overview":{"text":"已发布"}}'),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    @staticmethod
    def item(item_id, title, image):
        return {
            "item_id": item_id,
            "item_title": title,
            "item_category": "100",
            "item_price": "¥19.9",
            "item_image": image,
            "platform_item_status": 0,
            "catalog_metadata": {"item_status": 0},
        }

    def test_complete_reconciliation_hides_unseen_without_deleting_knowledge(self):
        first = self.db.reconcile_catalog_items(
            "account-1",
            [
                self.item("item-a", "商品A", "https://img.alicdn.com/a.jpg"),
                self.item("item-b", "商品B", "https://img.alicdn.com/b.jpg"),
            ],
            reconcile=True,
        )
        second = self.db.reconcile_catalog_items(
            "account-1",
            [self.item("item-a", "商品A新版", "https://img.alicdn.com/a-new.jpg")],
            reconcile=True,
        )

        active = self.db.get_items_by_cookie("account-1", include_inactive=False)
        all_items = self.db.get_items_by_cookie("account-1", include_inactive=True)
        profile = self.db.get_ai_item_knowledge_profile("account-1", "item-a")

        self.assertEqual(first["active_count"], 2)
        self.assertEqual(second["hidden_count"], 1)
        self.assertEqual([row["item_id"] for row in active], ["item-a"])
        self.assertFalse(next(row for row in all_items if row["item_id"] == "item-b")["catalog_active"])
        self.assertEqual(active[0]["item_title"], "商品A新版")
        self.assertEqual(active[0]["item_image"], "https://img.alicdn.com/a-new.jpg")
        self.assertEqual(active[0]["item_detail"], "保留的商品详情")
        self.assertTrue(profile["draft"])
        self.assertTrue(profile["published"])

    def test_catalog_lookup_is_scoped_by_account_and_item(self):
        self.db.reconcile_catalog_items(
            "account-1",
            [self.item("shared-item", "账号一商品", "https://img.alicdn.com/one.jpg")],
        )
        self.db.reconcile_catalog_items(
            "account-2",
            [self.item("shared-item", "账号二商品", "https://img.alicdn.com/two.jpg")],
        )

        lookup = self.db.get_item_catalog_lookup(["account-1", "account-2"])

        self.assertEqual(lookup[("account-1", "shared-item")]["item_title"], "账号一商品")
        self.assertEqual(lookup[("account-2", "shared-item")]["item_image"], "https://img.alicdn.com/two.jpg")

    def test_non_published_rows_are_rejected_without_replacing_active_catalog(self):
        self.db.reconcile_catalog_items(
            "account-1",
            [self.item("item-a", "商品A", "https://img.alicdn.com/a.jpg")],
        )
        draft = self.item("draft-item", "草稿", "https://img.alicdn.com/draft.jpg")
        draft["platform_item_status"] = 1

        result = self.db.reconcile_catalog_items(
            "account-1",
            [draft],
            reconcile=False,
        )

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(
            [row["item_id"] for row in self.db.get_items_by_cookie("account-1", include_inactive=False)],
            ["item-a"],
        )


class CatalogPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_failure_does_not_reconcile_or_hide_existing_items(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "account-1"
        responses = iter((
            {
                "success": True,
                "items": [{"id": "item-a", "item_status": 0}],
                "filtered_count": 0,
                "has_next_page": True,
            },
            {
                "success": False,
                "error": "第二页失败",
                "error_code": "page_failed",
            },
        ))

        async def load_page(*_args, **_kwargs):
            return next(responses)

        live.get_item_list_info = load_page
        live.save_items_list_to_db = AsyncMock()

        with patch("XianyuAutoAsync.asyncio.sleep", new=AsyncMock()):
            result = await live.get_all_items(page_size=20)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "page_failed")
        live.save_items_list_to_db.assert_not_awaited()


class OrderCatalogAssociationTests(unittest.TestCase):
    def test_order_api_uses_account_scoped_product_metadata(self):
        fake_db = SimpleNamespace(
            get_all_cookies=lambda _user_id: {
                "account-1": "cookie-one",
                "account-2": "cookie-two",
            },
            get_item_catalog_lookup=lambda _cookie_ids: {
                ("account-1", "shared-item"): {
                    "item_title": "账号一商品",
                    "item_price": "10",
                    "item_image": "https://img.alicdn.com/one.jpg",
                },
                ("account-2", "shared-item"): {
                    "item_title": "账号二商品",
                    "item_price": "20",
                    "item_image": "https://img.alicdn.com/two.jpg",
                },
            },
            get_orders_by_cookie=lambda cookie_id, limit=1000: [{
                "order_id": f"order-{cookie_id}",
                "item_id": "shared-item",
                "status": "completed",
                "created_at": "2026-07-20 12:00:00",
            }],
        )

        with (
            patch("db_manager.db_manager", fake_db),
            patch("reply_server.log_with_user"),
        ):
            response = get_user_orders(
                current_user={"user_id": 1},
                page=1,
                page_size=20,
                cookie_id=None,
                status=None,
            )

        rows = {row["cookie_id"]: row for row in response["data"]}
        self.assertEqual(rows["account-1"]["item_title"], "账号一商品")
        self.assertEqual(rows["account-1"]["item_image"], "https://img.alicdn.com/one.jpg")
        self.assertEqual(rows["account-2"]["item_title"], "账号二商品")
        self.assertEqual(rows["account-2"]["item_image"], "https://img.alicdn.com/two.jpg")


class CatalogMigrationTests(unittest.TestCase):
    def test_legacy_metadata_is_backfilled_without_rewriting_item_detail(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE item_info (
                id INTEGER PRIMARY KEY,
                cookie_id TEXT,
                item_id TEXT,
                item_detail TEXT,
                updated_at TIMESTAMP
            )
            """
        )
        metadata = json.dumps({
            "item_status": 0,
            "pic_info": {"picUrl": "http://img.alicdn.com/legacy.heic"},
        })
        connection.execute(
            "INSERT INTO item_info VALUES (1, 'account-1', 'item-1', ?, '2026-07-20 00:00:00')",
            (metadata,),
        )

        _item_catalog_state_v1(connection.cursor(), ":memory:")
        row = connection.execute(
            "SELECT item_image, platform_item_status, catalog_active, catalog_metadata, item_detail FROM item_info"
        ).fetchone()

        self.assertEqual(row[0], "https://img.alicdn.com/legacy.heic")
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], metadata)
        self.assertEqual(row[4], metadata)
        connection.close()


if __name__ == "__main__":
    unittest.main()
