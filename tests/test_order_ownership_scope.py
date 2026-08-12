"""订单归属下推与平台态只读：DAL 的 WHERE 自带归属，PUT 不能本地伪造发货/支付态。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from db_manager import DBManager
import reply_server


class OrderOwnershipScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "order-ownership.db"))
        self.assertTrue(
            self.db.create_user("owner-one", "one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("owner-two", "two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("owner-one")
        self.user_two = self.db.get_user_by_username("owner-two")
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                (
                    ("acct-one", "unb=1; cookie2=x", self.user_one["id"]),
                    ("acct-two", "unb=2; cookie2=x", self.user_two["id"]),
                ),
            )
            self.db.conn.commit()
        self.db.insert_or_update_order(
            order_id="order-one", item_id="item-1", buyer_id="buyer-1",
            amount="¥12.50", order_status="pending_ship", cookie_id="acct-one",
            spec_value="原始规格",
        )
        self.db.insert_or_update_order(
            order_id="order-two", item_id="item-2", buyer_id="buyer-2",
            amount="¥99.00", order_status="pending_ship", cookie_id="acct-two",
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

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def _stored_order(self, order_id):
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT order_status, system_shipped, spec_value FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return row

    # ---------------- DAL 归属下推 ----------------

    def test_detail_query_scoped_by_cookie_ids_hides_foreign_order(self):
        self.assertIsNotNone(self.db.get_order_by_id("order-one", cookie_ids=["acct-one"]))
        self.assertIsNone(self.db.get_order_by_id("order-two", cookie_ids=["acct-one"]))

    def test_detail_query_scoped_by_user_id_hides_foreign_order(self):
        self.assertIsNotNone(
            self.db.get_order_by_id("order-one", user_id=self.user_one["id"])
        )
        self.assertIsNone(
            self.db.get_order_by_id("order-two", user_id=self.user_one["id"])
        )

    def test_empty_owned_cookie_set_matches_nothing(self):
        self.assertIsNone(self.db.get_order_by_id("order-one", cookie_ids=[]))
        self.assertFalse(self.db.delete_order("order-one", cookie_ids=[]))
        self.assertIsNotNone(self._stored_order("order-one"))

    def test_delete_is_refused_for_unowned_scope_and_row_survives(self):
        self.assertFalse(self.db.delete_order("order-two", cookie_ids=["acct-one"]))
        self.assertIsNotNone(self._stored_order("order-two"))
        self.assertTrue(self.db.delete_order("order-two", cookie_ids=["acct-two"]))
        self.assertIsNone(self._stored_order("order-two"))

    def test_unscoped_calls_still_serve_sync_and_fulfillment_paths(self):
        self.assertIsNotNone(self.db.get_order_by_id("order-two"))

    # ---------------- 路由层归属 ----------------

    def test_detail_and_delete_reject_foreign_order(self):
        headers = self.headers_for(self.user_one)
        detail = self.client.get("/api/orders/order-two", headers=headers)
        self.assertEqual(detail.status_code, 404, detail.text)
        removed = self.client.delete("/api/orders/order-two", headers=headers)
        self.assertEqual(removed.status_code, 404, removed.text)
        self.assertIsNotNone(self._stored_order("order-two"))

    def test_delete_cannot_be_widened_by_a_stale_ownership_read(self):
        """先查后删之间归属被改写时，DELETE 语句自身的归属条件仍然拦住越权删除。"""
        headers = self.headers_for(self.user_one)
        foreign_order = dict(self.db.get_order_by_id("order-two"))
        foreign_order["cookie_id"] = "acct-one"

        with patch.object(
            self.db, "get_order_by_id", return_value=foreign_order
        ):
            response = self.client.delete("/api/orders/order-two", headers=headers)

        self.assertEqual(response.status_code, 500, response.text)
        self.assertIsNotNone(self._stored_order("order-two"))

    # ---------------- 平台态只读 ----------------

    def test_put_cannot_forge_shipped_status(self):
        headers = self.headers_for(self.user_one)
        response = self.client.put(
            "/api/orders/order-one",
            json={"order_status": "shipped"},
            headers=headers,
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["detail"]["code"], "platform_state_readonly"
        )
        self.assertEqual(response.json()["detail"]["fields"], ["order_status"])
        self.assertEqual(self._stored_order("order-one")[0], "pending_ship")

    def test_put_cannot_forge_system_shipped_flag(self):
        headers = self.headers_for(self.user_one)
        response = self.client.put(
            "/api/orders/order-one",
            json={"system_shipped": True, "spec_value": "夹带修改"},
            headers=headers,
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["fields"], ["system_shipped"])
        stored = self._stored_order("order-one")
        self.assertFalse(bool(stored[1]))
        self.assertEqual(stored[2], "原始规格")

    def test_put_ignores_unchanged_platform_state_echo_and_reports_it(self):
        headers = self.headers_for(self.user_one)
        response = self.client.put(
            "/api/orders/order-one",
            json={"order_status": "pending_ship", "spec_value": "人工修正规格"},
            headers=headers,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("order_status", response.json()["ignored_fields"])
        stored = self._stored_order("order-one")
        self.assertEqual(stored[0], "pending_ship")
        self.assertEqual(stored[2], "人工修正规格")

    def test_platform_state_fields_are_outside_the_manual_whitelist(self):
        self.assertEqual(
            reply_server.ORDER_PLATFORM_STATE_FIELDS
            & reply_server.ORDER_MANUAL_EDITABLE_FIELDS,
            frozenset(),
        )
        self.assertIn("order_status", reply_server.ORDER_PLATFORM_STATE_FIELDS)
        self.assertIn("system_shipped", reply_server.ORDER_PLATFORM_STATE_FIELDS)


if __name__ == "__main__":
    unittest.main()
