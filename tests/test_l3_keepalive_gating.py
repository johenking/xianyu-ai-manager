"""L3 主动保活的按号灰度开关持久化。

背景：保活会用浏览器去打 passport「快速进入」。全局开关一刀切的话，没配住宅
代理的账号会从机房 IP 出去，在风控下有伤号风险——于是保活一直不敢开。按号开关
让「只给配了住宅代理的号先开」成为可能。

这里锁死：列迁移幂等、默认关闭、读写往返，以及 XianyuLive 构造时把该开关读进
实例（保活判定用实例属性，不能在热路径回查数据库）。
"""

import os
import tempfile
import unittest
from pathlib import Path

from db_manager import DBManager


class L3KeepaliveGatingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db_path = str(self.root / "keepalive.db")
        self.db = DBManager(self.db_path)
        admin = self.db.get_user_by_username("admin")
        self.user_id = admin["user_id"] if "user_id" in admin.keys() else admin["id"]
        self.assertTrue(self.db.save_cookie("acct-ka-1", "unb=1; cookie2=x", self.user_id))

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def test_defaults_to_disabled(self):
        self.assertFalse(self.db.get_l3_keepalive_enabled("acct-ka-1"))

    def test_roundtrip(self):
        self.assertTrue(self.db.set_l3_keepalive_enabled("acct-ka-1", True))
        self.assertTrue(self.db.get_l3_keepalive_enabled("acct-ka-1"))
        self.assertTrue(self.db.set_l3_keepalive_enabled("acct-ka-1", False))
        self.assertFalse(self.db.get_l3_keepalive_enabled("acct-ka-1"))

    def test_unknown_account_reads_false_without_raising(self):
        self.assertFalse(self.db.get_l3_keepalive_enabled("no-such-account"))

    def test_migration_is_idempotent_across_reopen(self):
        """同一个库再开一次不得重复 ALTER，且已写入的值要留存。"""
        self.assertTrue(self.db.set_l3_keepalive_enabled("acct-ka-1", True))
        self.db.close()
        reopened = DBManager(self.db_path)
        try:
            self.assertTrue(reopened.get_l3_keepalive_enabled("acct-ka-1"))
        finally:
            reopened.close()
            self.db = DBManager(self.db_path)


if __name__ == "__main__":
    unittest.main()
