"""账号头像/昵称缓存测试。

控制台此前只能显示灰色空白占位头像。账号连上后补一次只读用户主页接口即可拿到
平台头像与昵称；平台返回的是 http:// 地址，而控制台走 HTTPS，不升级协议浏览器会
按混合内容拦截图片（实测发现）。失败一律静默，不能影响消息监听主流程。
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db_manager import DBManager


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self, content_type=None):
        del content_type
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, payload, recorder):
        self._payload = payload
        self._recorder = recorder

    def post(self, url, params=None, data=None, headers=None):
        self._recorder.append({"url": url, "params": params, "data": data})
        if isinstance(self._payload, Exception):
            raise self._payload
        return FakeResponse(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def head_payload(avatar, nickname="小梅很专业"):
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {"module": {"base": {"displayName": nickname, "avatar": {"avatar": avatar}}}},
    }


class AccountProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "profile.db"))
        self.assertTrue(self.db.create_user("seller", "s@example.test", "Strong-pass-2026!"))
        self.user = self.db.get_user_by_username("seller")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-one", "unb=42; _m_h5_tk=token123_456", self.user["id"]),
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _live(self):
        from XianyuAutoAsync import XianyuLive

        live = object.__new__(XianyuLive)
        live.cookie_id = "acct-one"
        live.cookies_str = "unb=42; _m_h5_tk=token123_456"
        live.myid = "42"
        live.browser_user_agent = "Mozilla/5.0 Chrome/133.0.0.0 Safari/537.36"
        live._account_profile_synced = False
        return live

    def _run_sync(self, payload):
        calls = []
        live = self._live()
        with patch("XianyuAutoAsync.db_manager", self.db), \
             patch("aiohttp.ClientSession", lambda *a, **kw: FakeSession(payload, calls)):
            asyncio.run(live._sync_account_profile())
        return live, calls

    def _stored(self):
        details = self.db.get_cookie_details("acct-one") or {}
        return details.get("avatar_url", ""), details.get("xianyu_nick", "")

    def test_http_avatar_is_upgraded_to_https_for_the_https_console(self):
        live, calls = self._run_sync(head_payload("http://img.alicdn.com/avatar.jpg"))
        avatar, nickname = self._stored()
        self.assertEqual(avatar, "https://img.alicdn.com/avatar.jpg")
        self.assertEqual(nickname, "小梅很专业")
        self.assertTrue(live._account_profile_synced)
        self.assertEqual(len(calls), 1)

    def test_protocol_relative_avatar_is_completed(self):
        self._run_sync(head_payload("//img.alicdn.com/avatar.jpg"))
        self.assertEqual(self._stored()[0], "https://img.alicdn.com/avatar.jpg")

    def test_non_http_avatar_value_is_dropped_but_nickname_is_kept(self):
        self._run_sync(head_payload("javascript:alert(1)"))
        avatar, nickname = self._stored()
        self.assertEqual(avatar, "")
        self.assertEqual(nickname, "小梅很专业")

    def test_sync_runs_at_most_once_per_instance(self):
        live, calls = self._run_sync(head_payload("https://img.alicdn.com/a.jpg"))
        with patch("XianyuAutoAsync.db_manager", self.db), \
             patch("aiohttp.ClientSession", lambda *a, **kw: FakeSession(head_payload("x"), calls)):
            asyncio.run(live._sync_account_profile())
        self.assertEqual(len(calls), 1)

    def test_platform_failure_is_silent_and_leaves_profile_untouched(self):
        self._run_sync(RuntimeError("network down"))
        self.assertEqual(self._stored(), ("", ""))

    def test_unexpected_payload_shape_is_ignored(self):
        self._run_sync({"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"], "data": {}})
        self.assertEqual(self._stored(), ("", ""))

    def test_missing_token_skips_the_platform_call_entirely(self):
        calls = []
        live = self._live()
        live.cookies_str = "unb=42"
        with patch("XianyuAutoAsync.db_manager", self.db), \
             patch("aiohttp.ClientSession", lambda *a, **kw: FakeSession(head_payload("x"), calls)):
            asyncio.run(live._sync_account_profile())
        self.assertEqual(calls, [])
        self.assertFalse(live._account_profile_synced)


if __name__ == "__main__":
    unittest.main()
