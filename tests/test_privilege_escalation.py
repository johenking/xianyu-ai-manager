"""代理多租户 · 任务 6：越权渗透测试固化（consolidated privilege-escalation sweep）。

从普通代理视角对系统做一次端到端越权走查，作为多租户隔离的回归安全网：

1. 纵向越权（vertical / privilege escalation）：普通登录用户尝试访问每一个受
   ``require_admin`` / ``verify_admin_token`` 保护的管理端路由，一律必须 403。
   任何未来新增的管理端路由若漏挂管理员守卫，都会在这张矩阵里立刻变红。
2. 横向越权（horizontal / cross-tenant）：用户 A 用自己的令牌去读取 / 修改 / 删除
   用户 B 名下的闲鱼账号资源（账号本体、会话续期、内容、AI 配置、扫码会话），
   一律必须 403（扫码会话不存在时 404、属主不符时 403）。

与既有分散用例互补——把散落在 ``test_user_dashboard_access`` /
``test_account_ownership_routing`` / ``test_security_hardening`` /
``test_account_proxy_api`` 里的越权断言收敛成一张可枚举、可反向验证的矩阵。
本文件只新增测试，不改动任何产品代码。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import cookie_manager
import db_manager as db_manager_module
import reply_server
from db_manager import DBManager
from session_registry import get_session_registry


# 仅受 require_admin / verify_admin_token 保护的管理端路由：(method, path, json_body)。
# 普通用户命中任意一条都必须 403。path 里的占位 id 用不存在的值即可——管理员守卫
# 作为依赖会在路由函数体、路径/请求体校验之前触发，因此拿什么 id 不影响结论。
ADMIN_ONLY_ROUTES = [
    ("POST", "/change-admin-password", {}),  # verify_admin_token
    ("GET", "/system-settings", None),
    ("GET", "/api/settings/summary", None),
    ("PUT", "/api/settings/sections/general", {}),
    ("POST", "/api/settings/verify/smtp", {}),
    ("POST", "/api/settings/verify/smtp/confirm", {}),
    ("PUT", "/system-settings/registration_enabled", {}),
    ("GET", "/api/admin/registration/status", None),
    ("POST", "/api/admin/registration/invites", {}),
    ("GET", "/api/admin/registration/invites", None),
    ("DELETE", "/api/admin/registration/invites/999999", None),
    ("PUT", "/api/admin/registration/invite-required", {}),
    ("PUT", "/api/admin/registration/limit", {}),
    ("GET", "/api/admin/registration/users", None),
    ("PUT", "/api/admin/registration/users/999999", {}),
    ("PUT", "/api/admin/registration/enabled", {}),
    ("PUT", "/registration-settings", {}),
    ("PUT", "/login-info-settings", {}),
    ("POST", "/system/reload-cache", {}),
    ("GET", "/logs", None),
    ("GET", "/risk-control-logs", None),
    ("DELETE", "/risk-control-logs/999999", None),
    ("GET", "/logs/stats", None),
    ("POST", "/logs/clear", {}),
    ("GET", "/admin/users", None),
    ("DELETE", "/admin/users/999999", None),
    ("GET", "/admin/risk-control-logs", None),
    ("GET", "/admin/cookies", None),
    ("GET", "/admin/logs", None),
    ("GET", "/admin/log-files", None),
    ("GET", "/admin/logs/export?file=xianyu.log", None),
    ("GET", "/admin/stats", None),
    ("GET", "/api/admin/dashboard/agents", None),
    ("GET", "/api/admin/accounts/overview", None),
    ("GET", "/admin/backup/download", None),
    ("POST", "/admin/backup/upload", {}),
    ("GET", "/admin/backup/list", None),
    ("POST", "/admin/reload-cache", {}),
    ("GET", "/admin/data/users", None),
    ("DELETE", "/admin/data/users/999999", None),
    ("DELETE", "/admin/data/audit_dummy_table", None),
]

# 账号(cookie)级归属校验路由：{cid} 会被替换成他人账号。普通用户命中他人账号都必须 403。
# 请求体校验发生在归属检查之前（归属检查在函数体内），因此写路由必须给合法请求体，
# 保证请求进入函数体后被归属守卫拦下，而不是提前 422。
COOKIE_SCOPED_ROUTES = [
    # —— 只读：账号本体 / 内容 / AI ——
    ("GET", "/cookie/{cid}/details", None),
    ("GET", "/default-replies/{cid}", None),
    ("GET", "/keywords/{cid}", None),
    ("GET", "/keywords-with-item-id/{cid}", None),
    ("GET", "/items/{cid}", None),
    ("GET", "/cookies/{cid}/auto-confirm", None),
    ("GET", "/cookies/{cid}/remark", None),
    ("GET", "/cookies/{cid}/pause-duration", None),
    ("GET", "/ai-reply-settings/{cid}", None),
    ("GET", "/ai-training-rules/{cid}", None),
    ("GET", "/api/accounts/{cid}/session-status", None),
    ("GET", "/api/diagnostics/auto-reply/{cid}", None),
    ("GET", "/face-verification/screenshot/{cid}", None),
    # —— 写 / 删：账号本体 ——
    ("PUT", "/cookies/{cid}", {"value": "unb=hijack; cookie2=stolen"}),
    ("PUT", "/cookies/{cid}/status", {"enabled": False}),
    ("POST", "/cookie/{cid}/account-info", {"value": "unb=hijack; cookie2=stolen"}),
    ("PUT", "/cookies/{cid}/auto-confirm", {"auto_confirm": True}),
    ("PUT", "/cookies/{cid}/auto-rate", {"auto_rate_enabled": False}),
    ("PUT", "/cookies/{cid}/remark", {"remark": "hijacked"}),
    ("PUT", "/cookies/{cid}/pause-duration", {"pause_duration": 5}),
    ("DELETE", "/cookies/{cid}", None),
    # —— 写 / 删：内容 ——
    ("PUT", "/default-replies/{cid}", {"enabled": True, "reply_content": "x", "reply_once": False}),
    ("DELETE", "/default-replies/{cid}", None),
    ("DELETE", "/face-verification/screenshot/{cid}", None),
    # —— 账号续期设备绑定（越权即可代绑他人账号的续期设备）——
    (
        "POST",
        "/api/accounts/{cid}/renewal-binding",
        {
            "login_session_id": "sess-1234567890",
            "device_id": "device-abcdefghijklmnop",
            "username": "attacker",
            "password": "attacker-pass",
            "authorized": True,
            "authorized_at": 1_756_000_000.0,
        },
    ),
]


class _PrivilegeEscalationBase(unittest.TestCase):
    """两用户 + 管理员 + 临时库 + TestClient 的共享夹具（复用归属路由测试的搭法）。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "privilege.db"))
        for username in ("agent-a", "agent-b"):
            self.assertTrue(
                self.db.create_user(
                    username, f"{username}@example.test", "Strong-pass-2026!"
                )
            )
        self.user_a = self.db.get_user_by_username("agent-a")
        self.user_b = self.db.get_user_by_username("agent-b")
        self.admin = self.db.get_user_by_username("admin")
        self.assertIsNotNone(self.admin, "内置管理员账号应在初始化时创建")

        # 归属检查散布在两种写法里：有的用 reply_server 模块级 db_manager，有的在函数体内
        # 做 `from db_manager import db_manager`（模块单例）。两处都要指到临时库，
        # 否则横向用例会读到另一个库、以“错误的理由”通过，正向对照也会失败。
        self.original_reply_db = reply_server.db_manager
        self.original_singleton_db = db_manager_module.db_manager
        reply_server.db_manager = self.db
        db_manager_module.db_manager = self.db

        # 许多账号路由在归属检查之前会先判 `cookie_manager.manager is None -> 500`；
        # 用 Mock 顶上让请求得以走到归属守卫。跨租户请求会在触及 manager 的任何方法前被 403 拦下。
        self.original_manager = cookie_manager.manager
        cookie_manager.manager = MagicMock()

        reply_server.SESSION_TOKENS.clear()
        self.client = TestClient(reply_server.app, raise_server_exceptions=False)
        self._seed_accounts()

    def tearDown(self):
        self.client.close()
        reply_server.SESSION_TOKENS.clear()
        reply_server.db_manager = self.original_reply_db
        db_manager_module.db_manager = self.original_singleton_db
        cookie_manager.manager = self.original_manager
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def headers_for(self, user):
        token, _ = reply_server.create_login_session(user)
        return {"Authorization": f"Bearer {token}"}

    def _seed_accounts(self):
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.executemany(
                "INSERT INTO cookies (id, value, user_id, xianyu_unb) VALUES (?, ?, ?, ?)",
                (
                    ("fish-a", "unb=111001; cookie2=session-a", self.user_a["id"], "111001"),
                    ("fish-b", "unb=222002; cookie2=session-b", self.user_b["id"], "222002"),
                ),
            )
            cursor.executemany(
                "INSERT INTO cookie_status (cookie_id, enabled) VALUES (?, 1)",
                (("fish-a",), ("fish-b",)),
            )
            self.db.conn.commit()

    def _dispatch(self, method, path, headers, json_body):
        return self.client.request(method, path, headers=headers, json=json_body)


class VerticalPrivilegeEscalationTests(_PrivilegeEscalationBase):
    """普通用户不得访问任何管理员专属路由（纵向越权全覆盖）。"""

    def test_regular_user_is_forbidden_from_every_admin_route(self):
        headers = self.headers_for(self.user_a)
        for method, path, body in ADMIN_ONLY_ROUTES:
            with self.subTest(route=f"{method} {path}"):
                response = self._dispatch(method, path, headers, body)
                # 403 而不是 404/405/422：既证明管理员守卫拦住了普通用户，
                # 也顺带证明这条路由确实存在、方法与路径写对了。
                self.assertEqual(
                    response.status_code,
                    403,
                    f"{method} {path} 未拦截普通用户：{response.status_code} {response.text}",
                )

    def test_unauthenticated_requests_are_rejected_before_authorization(self):
        # 无令牌 → 401（认证缺失），与 403（已登录但非管理员）区分开。
        for method, path, body in (
            ("GET", "/system-settings", None),
            ("POST", "/change-admin-password", {}),
            ("GET", "/api/admin/registration/users", None),
        ):
            with self.subTest(route=f"{method} {path}"):
                response = self._dispatch(method, path, headers=None, json_body=body)
                self.assertEqual(response.status_code, 401, response.text)

    def test_admin_passes_the_same_guard(self):
        # 正向对照：同一守卫对管理员放行，证明上面的 403 来自权限判定而非路由普遍不可用。
        headers = self.headers_for(self.admin)
        allowed = self.client.get("/admin/users", headers=headers)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        usernames = {u["username"] for u in allowed.json().get("users", [])}
        self.assertIn("agent-a", usernames)
        self.assertIn("agent-b", usernames)


class HorizontalPrivilegeEscalationTests(_PrivilegeEscalationBase):
    """用户 A 不得触碰用户 B 名下的账号资源（横向越权全覆盖）。"""

    def test_user_cannot_touch_another_tenants_account_resources(self):
        headers_a = self.headers_for(self.user_a)
        for method, template, body in COOKIE_SCOPED_ROUTES:
            path = template.format(cid="fish-b")  # B 的账号
            with self.subTest(route=f"{method} {path}"):
                response = self._dispatch(method, path, headers_a, body)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"{method} {path} 允许了跨租户访问：{response.status_code} {response.text}",
                )

    def test_owner_passes_the_same_ownership_checks(self):
        # 正向对照：本人访问自己的账号不会被 403，证明上面的 403 来自归属判定而非路由普遍不可用。
        headers_a = self.headers_for(self.user_a)
        headers_b = self.headers_for(self.user_b)

        own_default_reply = self.client.get("/default-replies/fish-a", headers=headers_a)
        self.assertEqual(own_default_reply.status_code, 200, own_default_reply.text)

        own_remark = self.client.get("/cookies/fish-a/remark", headers=headers_a)
        self.assertEqual(own_remark.status_code, 200, own_remark.text)

        # B 也能访问 B 自己的账号（对称验证：403 只针对“别人的”账号）。
        b_own = self.client.get("/default-replies/fish-b", headers=headers_b)
        self.assertEqual(b_own.status_code, 200, b_own.text)

    def test_cross_tenant_write_leaves_victim_data_untouched(self):
        # 越权写被拒后，受害账号的库内数据必须一字未改。
        headers_a = self.headers_for(self.user_a)
        hijack = self.client.put(
            "/cookies/fish-b",
            headers=headers_a,
            json={"value": "unb=hijack; cookie2=stolen"},
        )
        self.assertEqual(hijack.status_code, 403, hijack.text)
        with self.db.lock:
            value = self.db.conn.execute(
                "SELECT value FROM cookies WHERE id = 'fish-b'"
            ).fetchone()[0]
        self.assertEqual(value, "unb=222002; cookie2=session-b")

    def test_qr_login_session_is_owner_scoped(self):
        # 他人扫码会话：存在但非属主 → 403；完全不存在 → 404。
        registry = get_session_registry()
        registry.register(
            "pe-qr-of-b",
            "qr_login",
            self.user_b["id"],
            status="processing",
            ttl_seconds=900,
        )
        headers_a = self.headers_for(self.user_a)

        stolen_image = self.client.get(
            "/qr-login/verification-image/pe-qr-of-b", headers=headers_a
        )
        self.assertEqual(stolen_image.status_code, 403, stolen_image.text)

        stolen_continue = self.client.post(
            "/qr-login/continue/pe-qr-of-b", headers=headers_a
        )
        self.assertEqual(stolen_continue.status_code, 403, stolen_continue.text)

        missing = self.client.post(
            "/qr-login/continue/pe-does-not-exist", headers=headers_a
        )
        self.assertEqual(missing.status_code, 404, missing.text)


if __name__ == "__main__":
    unittest.main()
