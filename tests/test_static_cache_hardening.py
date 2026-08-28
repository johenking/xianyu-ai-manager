"""静态资源错误响应禁缓存契约：/static 与 /assets 的 4xx 必须带 no-store。

背景（2026-08-28 生产白屏事故）：部署窗口内入口 JS 404 被 Cloudflare 按默认规则
附加 max-age=14400 缓存到用户浏览器，服务端修复后用户普通刷新仍拿到缓存里的 404。
源侧对静态资源错误响应显式 no-store（含 CDN-Cache-Control），杜绝边缘与浏览器缓存投毒。
"""

import unittest

from fastapi.testclient import TestClient

import reply_server


class StaticErrorNoStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(reply_server.app)

    def test_static_missing_asset_404_sets_no_store(self):
        resp = self.client.get("/static/assets/definitely-missing-20260828.js")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate"
        )
        self.assertEqual(resp.headers.get("CDN-Cache-Control"), "no-store")

    def test_assets_mount_missing_asset_404_sets_no_store(self):
        resp = self.client.get("/assets/definitely-missing-20260828.js")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate"
        )
        self.assertEqual(resp.headers.get("CDN-Cache-Control"), "no-store")

    def test_static_success_response_not_forced_no_store(self):
        resp = self.client.get("/static/index.html")
        if resp.status_code != 200:
            self.skipTest("测试环境缺少 static/index.html，跳过 200 语义校验")
        self.assertNotIn("no-store", resp.headers.get("Cache-Control", ""))
        self.assertIsNone(resp.headers.get("CDN-Cache-Control"))

    def test_non_static_error_response_unaffected(self):
        resp = self.client.get("/api/definitely-missing-route-20260828")
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertNotIn("no-store", resp.headers.get("Cache-Control", ""))
        self.assertIsNone(resp.headers.get("CDN-Cache-Control"))


if __name__ == "__main__":
    unittest.main()
