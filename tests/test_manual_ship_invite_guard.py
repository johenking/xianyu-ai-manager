"""后台"完整发货"拦截邀请商品测试。

邀请自动发货商品由邀请服务独占卡密库存并发货，后台"完整发货"(full_delivery)
会与其重复发码。新逻辑在 full_delivery 分支入口拦截邀请商品；status_only
（仅在闲鱼标记发货状态）不受影响。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class ManualShipInviteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "manual-ship.db"))
        self.assertTrue(
            self.db.create_user("seller", "s@example.test", "Strong-pass-2026!")
        )
        self.user = self.db.get_user_by_username("seller")
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                ("acct-one", "unb=1; cookie2=x", self.user["id"]),
            )
            self.db.conn.commit()
        self.db.insert_or_update_order(
            order_id="order-invite", item_id="invite-item", buyer_id="buyer-1",
            amount="¥12.50", order_status="pending_ship", cookie_id="acct-one",
        )
        self.db.insert_or_update_order(
            order_id="order-normal", item_id="normal-item", buyer_id="buyer-2",
            amount="¥9.00", order_status="pending_ship", cookie_id="acct-one",
        )
        # 只有 invite-item 是邀请自动发货商品
        self.db.is_invite_auto_fulfillment_enabled = (
            lambda cookie_id, item_id: item_id == "invite-item"
        )

        self.original_db = reply_server.db_manager
        reply_server.db_manager = self.db
        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)

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

    def _headers(self):
        token, _ = reply_server.create_login_session(self.user)
        return {"Authorization": f"Bearer {token}"}

    def _result_for(self, response, order_id):
        for row in response.json()["results"]:
            if row["order_id"] == order_id:
                return row
        return None

    def _stored_status(self, order_id):
        with self.db.lock:
            return self.db.conn.execute(
                "SELECT order_status FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()[0]

    def test_full_delivery_blocks_invite_item(self):
        response = self.client.post(
            "/api/orders/manual-ship",
            json={"order_ids": ["order-invite"], "ship_mode": "full_delivery"},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = self._result_for(response, "order-invite")
        self.assertIsNotNone(row)
        self.assertIs(row["success"], False)
        # 用拦截文案的确切子串断言，区分"被邀请拦截"与"因账号离线失败"
        self.assertIn("邀请自动发货商品", row["message"])
        self.assertIn("status_only", row["message"])
        # 被拦截后订单不应被本地标记为已发货
        self.assertEqual(self._stored_status("order-invite"), "pending_ship")

    def test_full_delivery_normal_item_not_blocked_by_invite_guard(self):
        response = self.client.post(
            "/api/orders/manual-ship",
            json={"order_ids": ["order-normal"], "ship_mode": "full_delivery"},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = self._result_for(response, "order-normal")
        self.assertIsNotNone(row)
        # 普通商品会因账号未在线等原因失败，但绝不能是被邀请拦截
        self.assertNotIn("邀请自动发货商品", row["message"])

    def test_status_only_not_blocked_for_invite_item(self):
        import secure_confirm_decrypted

        class _Confirm:
            def __init__(self, *_args):
                pass

            async def auto_confirm(self, order_id, item_id):
                return {"success": True}

        with patch.object(secure_confirm_decrypted, "SecureConfirm", _Confirm):
            response = self.client.post(
                "/api/orders/manual-ship",
                json={"order_ids": ["order-invite"], "ship_mode": "status_only"},
                headers=self._headers(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        row = self._result_for(response, "order-invite")
        self.assertIsNotNone(row)
        # status_only 不受邀请拦截影响，可正常标记发货状态
        self.assertIs(row["success"], True)


if __name__ == "__main__":
    unittest.main()
