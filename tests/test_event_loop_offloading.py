"""异步路由里的阻塞工作必须离开事件循环线程（Excel 解析与整批 DAL 写入）。"""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

import cookie_manager
import db_manager as db_manager_module
from db_manager import DBManager
import reply_server


class _StubCookieManager:
    def get_cookie_status(self, cookie_id: str) -> bool:
        return True


def _runs_on_event_loop_thread() -> bool:
    """在事件循环线程里执行时为 True；被 to_thread 卸载后为 False。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class EventLoopOffloadingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "offloading.db"))
        self.assertTrue(
            self.db.create_user("seller", "seller@example.test", "Strong-pass-2026!")
        )
        self.user = self.db.get_user_by_username("seller")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-one", "unb=1; cookie2=x", self.user["id"]),
            )
            self.db.conn.commit()

        self.original_reply_db = reply_server.db_manager
        self.original_module_db = db_manager_module.db_manager
        reply_server.db_manager = self.db
        db_manager_module.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.original_manager = cookie_manager.manager
        cookie_manager.manager = _StubCookieManager()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self.on_loop_thread = []

    def tearDown(self):
        self.client.close()
        cookie_manager.manager = self.original_manager
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_reply_db
        db_manager_module.db_manager = self.original_module_db
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    @property
    def headers(self):
        token, _ = reply_server.create_login_session(self.user)
        return {"Authorization": f"Bearer {token}"}

    def _recording(self, label, result):
        def recorder(*_args, **_kwargs):
            self.on_loop_thread.append((label, _runs_on_event_loop_thread()))
            return result

        return recorder

    def test_order_import_writes_leave_the_event_loop_thread(self):
        # 未卸载的轻量归属查询作为对照组，证明探针能区分两种执行位置
        with patch.object(
            self.db, "get_all_cookies", self._recording("owned", {"acct-one": "v"})
        ), patch.object(
            self.db, "insert_or_update_order", self._recording("write", True)
        ):
            response = self.client.post(
                "/api/orders/import",
                headers=self.headers,
                json=[{
                    "order_id": "order-import-1",
                    "cookie_id": "acct-one",
                    "amount": "10.00",
                }],
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["success_count"], 1)
        self.assertEqual(
            self.on_loop_thread, [("owned", True), ("write", False)]
        )

    def test_order_workbook_parsing_leaves_the_event_loop_thread(self):
        with patch.object(
            reply_server, "_orders_from_xlsx", self._recording("parse", [])
        ):
            response = self.client.post(
                "/api/orders/import",
                headers=self.headers,
                files={"file": ("orders.xlsx", b"not-a-real-workbook", "application/vnd.ms-excel")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.on_loop_thread, [("parse", False)])

    def test_keyword_import_parsing_and_bulk_write_leave_the_event_loop_thread(self):
        frame = pd.DataFrame([
            {"关键词": "你好", "商品ID": "", "关键词内容": "您好"},
        ])
        with patch.object(pd, "read_excel", self._recording("parse", frame)), \
                patch.object(self.db, "get_keywords_with_type", self._recording("read", [])), \
                patch.object(self.db, "save_text_keywords_only", self._recording("write", True)):
            response = self.client.post(
                "/keywords-import/acct-one",
                headers=self.headers,
                files={"file": ("keywords.xlsx", b"not-a-real-workbook", "application/vnd.ms-excel")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.on_loop_thread,
            [("parse", False), ("read", False), ("write", False)],
        )


if __name__ == "__main__":
    unittest.main()
