"""卡券所有权（IDOR）隔离测试。

覆盖：
- update_card / delete_card 的 DB 层用户隔离（越权用户不能改/删他人卡券）
- PUT /cards/{id}、DELETE /cards/{id} 端点在跨租户访问时返回 404 且不改动数据
"""

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class CardOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "cards.db"
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.db_path))
        self.assertTrue(
            self.db.create_user("owner-one", "owner-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("owner-two", "owner-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("owner-one")
        self.user_two = self.db.get_user_by_username("owner-two")
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self.card_one = self._seed_card("卡券-用户一", self.user_one["id"])
        self.card_two = self._seed_card("卡券-用户二", self.user_two["id"])

    def tearDown(self):
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def _seed_card(self, name, user_id):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "INSERT INTO cards (name, type, text_content, user_id) VALUES (?, 'text', ?, ?)",
                (name, "原始内容", user_id),
            )
            self.db.conn.commit()
            return cursor.lastrowid

    def _card_field(self, card_id, field):
        with self.db.lock:
            row = self.db.conn.execute(
                f"SELECT {field} FROM cards WHERE id = ?", (card_id,)
            ).fetchone()
        return row[0] if row else None

    # ---------------- DB 层隔离 ----------------

    def test_update_card_rejects_cross_tenant_owner(self):
        """用户二不能改用户一的卡券。"""
        changed = self.db.update_card(
            card_id=self.card_one,
            name="被越权改名",
            user_id=self.user_two["id"],
        )
        self.assertFalse(changed)
        self.assertEqual(self._card_field(self.card_one, "name"), "卡券-用户一")

    def test_update_card_allows_owner(self):
        changed = self.db.update_card(
            card_id=self.card_one,
            name="正常改名",
            user_id=self.user_one["id"],
        )
        self.assertTrue(changed)
        self.assertEqual(self._card_field(self.card_one, "name"), "正常改名")

    def test_delete_card_rejects_cross_tenant_owner(self):
        deleted = self.db.delete_card(self.card_one, user_id=self.user_two["id"])
        self.assertFalse(deleted)
        self.assertIsNotNone(self._card_field(self.card_one, "id"))

    def test_delete_card_allows_owner(self):
        deleted = self.db.delete_card(self.card_one, user_id=self.user_one["id"])
        self.assertTrue(deleted)
        self.assertIsNone(self._card_field(self.card_one, "id"))

    # ---------------- 端点隔离 ----------------

    def test_put_card_endpoint_blocks_cross_tenant(self):
        resp = self.client.put(
            f"/cards/{self.card_one}",
            headers=self.headers_for(self.user_two),
            json={"name": "端点越权改名", "type": "text", "text_content": "x"},
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(self._card_field(self.card_one, "name"), "卡券-用户一")

    def test_delete_card_endpoint_blocks_cross_tenant(self):
        resp = self.client.delete(
            f"/cards/{self.card_one}",
            headers=self.headers_for(self.user_two),
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertIsNotNone(self._card_field(self.card_one, "id"))


if __name__ == "__main__":
    unittest.main()
