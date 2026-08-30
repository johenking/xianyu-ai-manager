"""商品知识档案跨账号搬运：DAL 二元组寻址与导入/复制路由的归属校验。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import reply_server
from db_manager import DBManager


def _knowledge(text: str = "源档案概览"):
    return {
        "overview": {"text": text, "source": "user", "status": "confirmed"},
        "pricing": [{"label": "Pro", "amount": "145", "text": "", "source": "ai", "status": "confirmed"}],
        "process": [],
        "after_sales": [],
        "forbidden": [],
        "faqs": [],
        "notes": [],
    }


class KnowledgeTransferDalTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        admin = self.db.get_user_by_username("admin")
        self.assertIsNotNone(admin)
        for cookie_id in ("acc-1", "acc-2"):
            self.assertTrue(
                self.db.save_cookie(cookie_id, f"unb={cookie_id}; cookie2=synthetic-session", admin["id"])
            )
        self._insert_item("acc-1", "item-a", "源商品")
        self._insert_item("acc-1", "item-b", "同账号目标")
        self._insert_item("acc-2", "item-c", "跨账号目标")
        self.db.save_ai_item_knowledge_draft("acc-1", "item-a", _knowledge(), "hash-a")

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def _insert_item(self, cookie_id, item_id, title):
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO item_info (cookie_id, item_id, item_title, item_price, item_detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (cookie_id, item_id, title, "9.9", "商品详情"),
            )
            self.db.conn.commit()

    def test_copy_to_targets_crosses_accounts(self):
        result = self.db.copy_ai_item_knowledge_draft_to_targets(
            "acc-1", "item-a", [("acc-1", "item-b"), ("acc-2", "item-c")]
        )

        self.assertEqual(result["copied_count"], 2)
        self.assertEqual(result["source_kind"], "draft")
        self.assertEqual(
            result["copied_targets"],
            [{"cookie_id": "acc-1", "item_id": "item-b"}, {"cookie_id": "acc-2", "item_id": "item-c"}],
        )
        for cookie_id, item_id in (("acc-1", "item-b"), ("acc-2", "item-c")):
            profile = self.db.get_ai_item_knowledge_profile(cookie_id, item_id)
            self.assertEqual(profile["draft"]["overview"]["text"], "源档案概览")
            self.assertEqual(profile["source_detail_hash"], "")
            self.assertEqual(profile["published_version"], 0)

    def test_copy_to_targets_reports_missing_pair(self):
        result = self.db.copy_ai_item_knowledge_draft_to_targets(
            "acc-1", "item-a", [("acc-2", "item-not-there")]
        )

        self.assertEqual(result["copied_count"], 0)
        self.assertEqual(result["missing_targets"], [{"cookie_id": "acc-2", "item_id": "item-not-there"}])
        self.assertEqual(result["missing_item_ids"], ["item-not-there"])

    def test_copy_to_targets_skips_source_itself_and_duplicates(self):
        result = self.db.copy_ai_item_knowledge_draft_to_targets(
            "acc-1", "item-a", [("acc-1", "item-a"), ("acc-1", "item-b"), ("acc-1", "item-b")]
        )

        self.assertEqual(result["copied_item_ids"], ["item-b"])

    def test_legacy_copy_still_scopes_to_source_account(self):
        result = self.db.copy_ai_item_knowledge_draft("acc-1", "item-a", ["item-b", "item-c"])

        self.assertEqual(result["copied_item_ids"], ["item-b"])
        self.assertEqual(result["missing_item_ids"], ["item-c"])
        self.assertEqual(result["skipped_item_ids"], [])
        self.assertEqual(result["source_kind"], "draft")

    def test_import_pulls_source_into_target_draft(self):
        result = self.db.import_ai_item_knowledge_draft("acc-2", "item-c", "acc-1", "item-a")

        self.assertEqual(result["source_kind"], "draft")
        profile = self.db.get_ai_item_knowledge_profile("acc-2", "item-c")
        self.assertEqual(profile["draft"]["pricing"][0]["label"], "Pro")

    def test_import_prefers_published_when_source_draft_is_empty(self):
        self.db.publish_ai_item_knowledge("acc-1", "item-a")
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE ai_item_knowledge_profiles SET draft_json = '{}'"
                " WHERE cookie_id = 'acc-1' AND item_id = 'item-a'"
            )
            self.db.conn.commit()

        result = self.db.import_ai_item_knowledge_draft("acc-2", "item-c", "acc-1", "item-a")

        self.assertEqual(result["source_kind"], "published")
        profile = self.db.get_ai_item_knowledge_profile("acc-2", "item-c")
        self.assertEqual(profile["draft"]["overview"]["text"], "源档案概览")

    def test_import_rejects_source_without_profile(self):
        with self.assertRaises(ValueError):
            self.db.import_ai_item_knowledge_draft("acc-1", "item-b", "acc-2", "item-c")

    def test_import_rejects_missing_target(self):
        with self.assertRaises(ValueError):
            self.db.import_ai_item_knowledge_draft("acc-2", "item-nope", "acc-1", "item-a")

    def test_source_kind_helper_reports_draft_and_published(self):
        self.assertEqual(self.db.get_ai_item_knowledge_source_kind("acc-1", "item-a"), "draft")
        self.assertEqual(self.db.get_ai_item_knowledge_source_kind("acc-1", "item-b"), "")


class KnowledgeTransferRouteTests(unittest.TestCase):
    """路由层只验归属：源与目标各校验一次，任一不属于当前用户即 403。"""

    OWNED = {"acc-1": "cookie-1", "acc-2": "cookie-2"}

    def _endpoint(self, path: str):
        for route in reply_server.ai_router.routes:
            if getattr(route, "path", "") == path and "POST" in getattr(route, "methods", set()):
                return route.endpoint
        raise AssertionError(f"未找到 POST {path} 路由")

    def _access_patches(self):
        return (
            patch.object(reply_server.db_manager, "get_all_cookies", return_value=dict(self.OWNED)),
            patch.object(reply_server.cookie_manager, "manager", SimpleNamespace(cookies=dict(self.OWNED))),
        )

    def test_import_requires_access_to_source_cookie(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/import")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "目标"}), \
                patch.object(reply_server.db_manager, "import_ai_item_knowledge_draft") as call:
            with self.assertRaises(HTTPException) as ctx:
                endpoint(
                    "acc-1", "item-a",
                    reply_server.AIItemKnowledgeImportRequest(
                        source_cookie_id="acc-9", source_item_id="item-x"
                    ),
                    current_user={"user_id": 1},
                )

        self.assertEqual(ctx.exception.status_code, 403)
        call.assert_not_called()

    def test_import_across_owned_accounts_returns_payload(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/import")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "商品"}), \
                patch.object(reply_server.db_manager, "import_ai_item_knowledge_draft",
                             return_value={"source_kind": "published"}) as call, \
                patch.object(reply_server, "_item_knowledge_payload",
                             return_value={"draft": {"overview": {"text": "搬来的"}}}):
            result = endpoint(
                "acc-2", "item-c",
                reply_server.AIItemKnowledgeImportRequest(
                    source_cookie_id="acc-1", source_item_id="item-a"
                ),
                current_user={"user_id": 1},
            )

        call.assert_called_once_with("acc-2", "item-c", "acc-1", "item-a")
        self.assertEqual(result["source_kind"], "published")
        self.assertEqual(result["draft"]["overview"]["text"], "搬来的")

    def test_import_rejects_self_as_source(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/import")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "商品"}), \
                patch.object(reply_server.db_manager, "import_ai_item_knowledge_draft") as call:
            with self.assertRaises(HTTPException) as ctx:
                endpoint(
                    "acc-1", "item-a",
                    reply_server.AIItemKnowledgeImportRequest(
                        source_cookie_id="acc-1", source_item_id="item-a"
                    ),
                    current_user={"user_id": 1},
                )

        self.assertEqual(ctx.exception.status_code, 400)
        call.assert_not_called()

    def test_copy_rejects_target_cookie_not_owned(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/copy")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "源"}), \
                patch.object(reply_server.db_manager, "copy_ai_item_knowledge_draft_to_targets") as call:
            with self.assertRaises(HTTPException) as ctx:
                endpoint(
                    "acc-1", "item-a",
                    reply_server.AIItemKnowledgeCopyRequest(
                        targets=[{"cookie_id": "acc-9", "item_id": "item-x"}]
                    ),
                    current_user={"user_id": 1},
                )

        self.assertEqual(ctx.exception.status_code, 403)
        call.assert_not_called()

    def test_copy_passes_cross_account_pairs(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/copy")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "源"}), \
                patch.object(reply_server.db_manager, "copy_ai_item_knowledge_draft_to_targets",
                             return_value={"copied_count": 2, "copied_item_ids": ["item-b", "item-c"]}) as call:
            result = endpoint(
                "acc-1", "item-a",
                reply_server.AIItemKnowledgeCopyRequest(targets=[
                    {"cookie_id": "acc-1", "item_id": "item-b"},
                    {"cookie_id": "acc-2", "item_id": "item-c"},
                ]),
                current_user={"user_id": 1},
            )

        call.assert_called_once_with("acc-1", "item-a", [("acc-1", "item-b"), ("acc-2", "item-c")])
        self.assertEqual(result["message"], "已覆盖 2 个商品草稿")

    def test_copy_keeps_legacy_same_account_payload(self):
        endpoint = self._endpoint("/ai-item-knowledge/{cookie_id}/{item_id}/copy")
        owned, manager = self._access_patches()
        with owned, manager, \
                patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "源"}), \
                patch.object(reply_server.db_manager, "copy_ai_item_knowledge_draft_to_targets",
                             return_value={"copied_count": 1, "copied_item_ids": ["item-b"]}) as call:
            endpoint(
                "acc-1", "item-a",
                reply_server.AIItemKnowledgeCopyRequest(target_item_ids=["item-b"]),
                current_user={"user_id": 1},
            )

        call.assert_called_once_with("acc-1", "item-a", [("acc-1", "item-b")])


if __name__ == "__main__":
    unittest.main()
