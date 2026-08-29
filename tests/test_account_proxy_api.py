"""账号级住宅代理：DB 读写加密 + API 端点（保存/连通性测试）。

背景（2026-08-29）：云端机房 IP 是闲鱼滑块风控的主因，方案是每账号配住宅代理。
这里锁死三件事：
1. DB 层密码加密落盘、读取解密，password=None 保留原密码的语义；
2. PUT /cookies/{cid}/proxy 只许账号归属者操作，响应绝不回传明文密码；
3. POST /cookies/{cid}/proxy/test 用已存配置实测出口 IP 并落库。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class AccountProxyFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "proxy-api.db"))
        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        # 端点内部走 `from db_manager import db_manager`，必须补全局补丁
        self._db_patch = patch("db_manager.db_manager", self.db)
        self._db_patch.start()
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(
            reply_server.app,
            raise_server_exceptions=False,
            client=("127.0.0.1", 50000),
        )
        admin = self.db.get_user_by_username("admin")
        self.user_id = admin["user_id"] if "user_id" in admin.keys() else admin["id"]
        token, _ = reply_server.create_login_session(admin)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.assertTrue(self.db.save_cookie("acct-proxy-1", "unb=1; cookie2=x", self.user_id))

    def tearDown(self):
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        self._db_patch.stop()
        reply_server.db_manager = self.original_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()


class AccountProxyDbTests(AccountProxyFixture):
    def test_roundtrip_encrypts_password_at_rest(self):
        self.assertTrue(
            self.db.set_account_proxy(
                "acct-proxy-1",
                server="http://gw.example.net:1000",
                username="u1",
                password="secret-pass",
                region="上海·电信",
            )
        )
        config = self.db.get_account_proxy_config("acct-proxy-1")
        self.assertEqual(config["server"], "http://gw.example.net:1000")
        self.assertEqual(config["username"], "u1")
        self.assertEqual(config["password"], "secret-pass")
        # 落盘不可为明文
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT proxy_password_encrypted FROM cookies WHERE id = ?",
            ("acct-proxy-1",),
        )
        stored = cursor.fetchone()[0]
        self.assertTrue(stored)
        self.assertNotIn("secret-pass", str(stored))

    def test_none_password_keeps_existing_secret(self):
        self.db.set_account_proxy(
            "acct-proxy-1", server="http://gw:1000", password="keep-me"
        )
        self.db.set_account_proxy(
            "acct-proxy-1", server="http://gw:2000", password=None
        )
        config = self.db.get_account_proxy_config("acct-proxy-1")
        self.assertEqual(config["server"], "http://gw:2000")
        self.assertEqual(config["password"], "keep-me")

    def test_disabled_returns_none_for_login_paths(self):
        self.db.set_account_proxy(
            "acct-proxy-1", server="http://gw:1000", enabled=False
        )
        self.assertIsNone(self.db.get_account_proxy_config("acct-proxy-1"))


class AccountProxyApiTests(AccountProxyFixture):
    def test_save_and_echo_without_plaintext_password(self):
        response = self.client.put(
            "/cookies/acct-proxy-1/proxy",
            json={
                "proxy_enabled": True,
                "proxy_server": "http://gw.example.net:1000",
                "proxy_username": "u1",
                "proxy_password": "secret-pass",
                "proxy_region": "上海·电信",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        data = payload["data"]
        self.assertTrue(data["proxy_enabled"])
        self.assertTrue(data["proxy_password_set"])
        self.assertNotIn("secret-pass", response.text)
        # 登录路径立即可用
        config = self.db.get_account_proxy_config("acct-proxy-1")
        self.assertEqual(config["password"], "secret-pass")

    def test_enabled_without_server_is_rejected(self):
        response = self.client.put(
            "/cookies/acct-proxy-1/proxy",
            json={"proxy_enabled": True, "proxy_server": ""},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_foreign_account_is_forbidden(self):
        response = self.client.put(
            "/cookies/not-my-account/proxy",
            json={"proxy_enabled": False, "proxy_server": "http://gw:1"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_probe_unconfigured_reports_not_configured(self):
        response = self.client.post(
            "/cookies/acct-proxy-1/proxy/test", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["data"]["status"], "not_configured")

    def test_probe_records_egress_ip(self):
        self.db.set_account_proxy(
            "acct-proxy-1", server="http://gw:1000", password="p", enabled=True
        )
        probe = Mock(
            return_value={"ok": True, "ip": "1.2.3.4", "status": "ok", "error": ""}
        )
        with patch("utils.browser_runtime.probe_proxy_egress", probe):
            response = self.client.post(
                "/cookies/acct-proxy-1/proxy/test", headers=self.headers
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["ip"], "1.2.3.4")
        # 探测配置必须带解密后的密码直达 probe
        self.assertEqual(probe.call_args.args[0]["password"], "p")
        details = self.db.get_cookie_details("acct-proxy-1")
        self.assertEqual(details["proxy_last_ip"], "1.2.3.4")
        self.assertEqual(details["proxy_last_status"], "ok")
        self.assertTrue(details["proxy_last_check_at"])

    def test_details_list_exposes_proxy_summary_only(self):
        self.db.set_account_proxy(
            "acct-proxy-1",
            server="http://gw:1000",
            username="u1",
            password="secret-pass",
            region="上海",
            enabled=True,
        )
        with patch.object(
            reply_server.cookie_manager, "manager",
            Mock(get_cookie_status=Mock(return_value=True)),
        ):
            response = self.client.get("/cookies/details", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        rows = [row for row in response.json() if row["id"] == "acct-proxy-1"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["proxy_enabled"])
        self.assertEqual(row["proxy_server"], "http://gw:1000")
        self.assertTrue(row["proxy_password_set"])
        self.assertNotIn("secret-pass", response.text)


if __name__ == "__main__":
    unittest.main()
