"""update_cookie_account_info 创建路径归属校验测试。

该方法原先在记录不存在且未提供 user_id 时，会把新账号默认划给
admin 用户：被删除或归属未知的账号凭证会被静默“复活”到 admin
名下（跨租户凭证接管）。要求：创建必须显式提供归属 user_id，
否则拒绝创建且不落库；已存在记录的更新路径不受影响。
"""

import os
from pathlib import Path
import tempfile
import unittest

from db_manager import DBManager


class CookieCreationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "cookie-creation.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _cookie_owner(self, cookie_id):
        row = self.db.conn.execute(
            "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,)
        ).fetchone()
        return row[0] if row else None

    def test_creation_without_user_id_is_rejected_and_nothing_is_saved(self):
        result = self.db.update_cookie_account_info(
            "ghost-account", cookie_value="unb=901; cookie2=x"
        )
        self.assertFalse(result)
        self.assertIsNone(self._cookie_owner("ghost-account"))

    def test_deleted_account_is_not_resurrected_under_admin(self):
        self.assertTrue(
            self.db.update_cookie_account_info(
                "acct-one",
                cookie_value="unb=111; cookie2=a",
                user_id=self.user_one["id"],
            )
        )
        with self.db.lock:
            self.db.conn.execute("DELETE FROM cookies WHERE id = 'acct-one'")
            self.db.conn.commit()
        # 模拟后台刷新等旧路径在记录被删除后不带 user_id 调用：必须拒绝
        result = self.db.update_cookie_account_info(
            "acct-one", cookie_value="unb=111; cookie2=refreshed"
        )
        self.assertFalse(result)
        self.assertIsNone(self._cookie_owner("acct-one"))

    def test_creation_with_explicit_owner_succeeds(self):
        self.assertTrue(
            self.db.update_cookie_account_info(
                "acct-new",
                cookie_value="unb=222; cookie2=b",
                user_id=self.user_one["id"],
            )
        )
        self.assertEqual(self._cookie_owner("acct-new"), self.user_one["id"])

    def test_existing_record_update_without_user_id_keeps_owner(self):
        self.assertTrue(
            self.db.update_cookie_account_info(
                "acct-keep",
                cookie_value="unb=333; cookie2=c",
                user_id=self.user_one["id"],
            )
        )
        self.assertTrue(
            self.db.update_cookie_account_info(
                "acct-keep", cookie_value="unb=333; cookie2=renewed"
            )
        )
        self.assertEqual(self._cookie_owner("acct-keep"), self.user_one["id"])
        value = self.db.conn.execute(
            "SELECT value FROM cookies WHERE id = 'acct-keep'"
        ).fetchone()[0]
        self.assertIn("renewed", value)


if __name__ == "__main__":
    unittest.main()
