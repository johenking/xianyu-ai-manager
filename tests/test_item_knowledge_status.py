"""商品知识档案状态聚合：DAL 状态映射与商品列表接口附加字段。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from db_manager import DBManager
import reply_server


def _knowledge(text: str = "", faqs=None):
    return {
        "overview": {"text": text, "source": "user", "status": "confirmed"},
        "pricing": [],
        "process": [],
        "after_sales": [],
        "forbidden": [],
        "faqs": faqs or [],
        "notes": [],
    }


class KnowledgeStatusDalTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        admin = self.db.get_user_by_username("admin")
        self.assertIsNotNone(admin)
        self.assertTrue(
            self.db.save_cookie(
                "account-1",
                "unb=account-1; cookie2=synthetic-session",
                admin["id"],
            )
        )

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def _insert_item(self, item_id, title="测试商品"):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_price, item_detail)"
                " VALUES (?, ?, ?, ?, ?)",
                ("account-1", item_id, title, "135", "商品详情"),
            )
            self.db.conn.commit()

    def test_status_map_reports_draft_published_and_skips_items_without_content(self):
        for item_id in ("item-a", "item-b", "item-c", "item-d"):
            self._insert_item(item_id)
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", _knowledge("已发布商品"), "hash-a")
        self.db.publish_ai_item_knowledge("account-1", "item-a")
        self.db.save_ai_item_knowledge_draft("account-1", "item-b", _knowledge("只有草稿"), "hash-b")
        # item-c 有档案行但没有任何真实内容，不应被标记为有档案
        self.db.save_ai_item_knowledge_draft("account-1", "item-c", _knowledge(""), "hash-c")

        status = self.db.get_ai_item_knowledge_status_by_cookie("account-1")

        self.assertIn("item-a", status)
        self.assertTrue(status["item-a"]["has_draft"])
        self.assertEqual(status["item-a"]["published_version"], 1)
        self.assertIn("item-b", status)
        self.assertTrue(status["item-b"]["has_draft"])
        self.assertEqual(status["item-b"]["published_version"], 0)
        self.assertNotIn("item-c", status)
        self.assertNotIn("item-d", status)

    def test_status_content_check_matches_frontend_semantics(self):
        self._insert_item("item-a")
        self._insert_item("item-b")
        # 概览为空白但常见问答有条目 -> 视为有内容
        self.db.save_ai_item_knowledge_draft(
            "account-1",
            "item-a",
            _knowledge("  ", faqs=[{"question": "怎么发货", "answer": "自动发货", "status": "confirmed"}]),
            "hash-a",
        )
        # 概览只有空白且各列表为空 -> 视为无内容
        self.db.save_ai_item_knowledge_draft("account-1", "item-b", _knowledge("   "), "hash-b")

        status = self.db.get_ai_item_knowledge_status_by_cookie("account-1")

        self.assertIn("item-a", status)
        self.assertNotIn("item-b", status)

    def test_status_map_is_isolated_by_cookie(self):
        self._insert_item("item-a")
        self.db.save_ai_item_knowledge_draft("account-1", "item-a", _knowledge("账号一档案"), "hash-a")

        self.assertEqual(self.db.get_ai_item_knowledge_status_by_cookie("account-2"), {})

    def test_broken_draft_json_is_treated_as_no_content(self):
        self._insert_item("item-a")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO ai_item_knowledge_profiles (cookie_id, item_id, draft_json, published_version)"
                " VALUES (?, ?, ?, ?)",
                ("account-1", "item-a", "not-json", 0),
            )
            self.db.conn.commit()

        self.assertEqual(self.db.get_ai_item_knowledge_status_by_cookie("account-1"), {})


class KnowledgeStatusRouteTests(unittest.TestCase):
    @staticmethod
    def _endpoint(path: str):
        for route in reply_server.content_router.routes:
            if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
                return route.endpoint
        raise AssertionError(f"未找到 GET {path} 路由")

    def test_items_by_cookie_route_attaches_knowledge_status(self):
        endpoint = self._endpoint("/items/cookie/{cookie_id}")
        status = {"item-a": {"has_draft": True, "published_version": 2,
                             "draft_updated_at": None, "published_at": None}}
        with patch.object(reply_server.db_manager, "get_all_cookies",
                          return_value={"account-1": "cookie"}), \
             patch.object(reply_server.db_manager, "get_items_by_cookie",
                          return_value=[{"item_id": "item-a"}, {"item_id": "item-b"}]), \
             patch.object(reply_server.db_manager, "get_ai_item_knowledge_status_by_cookie",
                          return_value=status) as status_call:
            result = endpoint("account-1", current_user={"user_id": 1})

        status_call.assert_called_once_with("account-1")
        items = {entry["item_id"]: entry for entry in result["items"]}
        self.assertTrue(items["item-a"]["knowledge_has_draft"])
        self.assertEqual(items["item-a"]["knowledge_published_version"], 2)
        self.assertFalse(items["item-b"]["knowledge_has_draft"])
        self.assertEqual(items["item-b"]["knowledge_published_version"], 0)

    def test_all_items_route_attaches_knowledge_status_per_cookie(self):
        endpoint = self._endpoint("/items")
        per_cookie_items = {
            "account-1": [{"item_id": "item-a"}],
            "account-2": [{"item_id": "item-b"}],
        }
        per_cookie_status = {
            "account-1": {"item-a": {"has_draft": True, "published_version": 0,
                                     "draft_updated_at": None, "published_at": None}},
            "account-2": {},
        }
        with patch.object(reply_server.db_manager, "get_all_cookies",
                          return_value={"account-1": "c1", "account-2": "c2"}), \
             patch.object(reply_server.db_manager, "get_items_by_cookie",
                          side_effect=lambda cookie_id, **_: per_cookie_items[cookie_id]), \
             patch.object(reply_server.db_manager, "get_ai_item_knowledge_status_by_cookie",
                          side_effect=lambda cookie_id: per_cookie_status[cookie_id]):
            result = endpoint(current_user={"user_id": 1})

        items = {entry["item_id"]: entry for entry in result["items"]}
        self.assertTrue(items["item-a"]["knowledge_has_draft"])
        self.assertEqual(items["item-a"]["knowledge_published_version"], 0)
        self.assertFalse(items["item-b"]["knowledge_has_draft"])


if __name__ == "__main__":
    unittest.main()
