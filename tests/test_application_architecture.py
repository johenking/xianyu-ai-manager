import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

import Start
from app_factory import create_app


class ApplicationFactoryTests(unittest.IsolatedAsyncioTestCase):
    def test_all_legacy_routes_are_registered_through_domain_routers(self):
        app = create_app()
        openapi = app.openapi()
        self.assertEqual(openapi["info"]["version"], "1.10.2")
        paths = openapi["paths"]
        signatures = {
            (method.upper(), path)
            for path, definition in paths.items()
            for method in definition
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
        }
        self.assertEqual(len(signatures), 243)
        self.assertEqual(
            set(app.state.domain_routers),
            {
                "accounts",
                "admin",
                "ai",
                "auth",
                "content",
                "frontend",
                "orders",
                "settings",
                "skills",
                "system",
            },
        )
        self.assertIn(("POST", "/login"), signatures)
        self.assertIn(("POST", "/api/orders/sync"), signatures)
        self.assertIn(("POST", "/ai-reply-lab/reply/{cookie_id}"), signatures)
        self.assertIn(("GET", "/api/accounts/{cookie_id}/session-status"), signatures)
        self.assertIn(("GET", "/api/dashboard/summary"), signatures)
        self.assertIn(("GET", "/analytics/items/performance"), signatures)
        self.assertIn(("GET", "/analytics/items/traffic"), signatures)
        self.assertIn(("GET", "/analytics/items/metrics/status"), signatures)
        self.assertIn(("POST", "/analytics/items/metrics/sync"), signatures)
        self.assertIn(("GET", "/api/settings/user-summary"), signatures)
        self.assertIn(("PUT", "/api/settings/user-basic"), signatures)
        self.assertIn(("GET", "/health/live"), signatures)
        self.assertIn(("GET", "/health/ready"), signatures)
        self.assertIn(("GET", "/api/auth/registration-config"), signatures)
        self.assertIn(("POST", "/api/auth/password-reset"), signatures)
        self.assertIn(("POST", "/api/auth/password-reset/verify-code"), signatures)
        self.assertIn(("POST", "/api/admin/registration/invites"), signatures)
        self.assertIn(("PUT", "/api/admin/registration/limit"), signatures)
        self.assertIn(("POST", "/api/settings/verify/smtp/confirm"), signatures)
        self.assertIn(("POST", "/api/official-login/sessions"), signatures)
        self.assertIn(("GET", "/api/official-login/sessions/{session_id}"), signatures)
        self.assertIn(("POST", "/api/official-login/sessions/{session_id}/show-browser"), signatures)
        self.assertIn(("POST", "/api/official-login/sessions/{session_id}/cancel"), signatures)
        self.assertIn(("POST", "/api/accounts/{cookie_id}/session-refresh/show-browser"), signatures)
        self.assertIn(("POST", "/qr-login/cancel/{session_id}"), signatures)
        self.assertIn(("POST", "/api/accounts/{cid}/renewal-binding"), signatures)
        self.assertIn(("POST", "/api/client-browser/devices"), signatures)
        self.assertIn(("GET", "/api/client-browser/devices"), signatures)
        self.assertIn(("DELETE", "/api/client-browser/devices/{device_id}"), signatures)
        self.assertIn(("POST", "/api/client-browser/sessions"), signatures)
        self.assertIn(("POST", "/api/client-browser/sessions/{session_id}/challenge"), signatures)
        self.assertIn(("POST", "/api/client-browser/import"), signatures)
        self.assertIn(("POST", "/api/client-browser/renewal/claim"), signatures)
        self.assertIn(("POST", "/official-window-login"), signatures)
        self.assertIn(("GET", "/official-window-login/check/{session_id}"), signatures)
        self.assertIn(("POST", "/official-window-login/cancel/{session_id}"), signatures)
        self.assertNotIn(("POST", "/qr-login/refresh-cookies"), signatures)
        self.assertNotIn(("POST", "/qr-login/reset-cooldown/{cookie_id}"), signatures)
        self.assertNotIn(("GET", "/qr-login/cooldown-status/{cookie_id}"), signatures)

    async def test_lifespan_starts_and_stops_runtime_on_the_same_loop(self):
        app = create_app()
        loop_id = id(asyncio.get_running_loop())
        runtime = object()

        async def start():
            self.assertEqual(id(asyncio.get_running_loop()), loop_id)
            return runtime

        async def stop():
            self.assertEqual(id(asyncio.get_running_loop()), loop_id)

        with patch("app_factory.start_runtime", side_effect=start) as start_mock, patch(
            "app_factory.stop_runtime", side_effect=stop
        ) as stop_mock:
            async with app.router.lifespan_context(app):
                self.assertIs(app.state.runtime, runtime)

        start_mock.assert_awaited_once()
        stop_mock.assert_awaited_once()
        self.assertIsNone(app.state.runtime)

    def test_start_module_does_not_create_a_second_event_loop_or_thread(self):
        source = Path("Start.py").read_text(encoding="utf-8")
        self.assertNotIn("threading", source)
        self.assertNotIn("new_event_loop", source)
        self.assertNotIn("run_until_complete", source)
        self.assertIn('"app_factory:create_app"', source)

    def test_start_disables_raw_uvicorn_access_logs(self):
        with patch.object(Start, "_server_address", return_value=("127.0.0.1", 8091)), patch.object(
            Start.uvicorn, "run"
        ) as run_mock:
            Start.main()

        self.assertFalse(run_mock.call_args.kwargs["access_log"])

    def test_server_address_uses_host_and_port_without_legacy_route_url(self):
        with patch.object(
            Start,
            "AUTO_REPLY",
            {"api": {"host": "127.0.0.7", "port": 8092}},
        ), patch.dict(Start.os.environ, {}, clear=True):
            self.assertEqual(Start._server_address(), ("127.0.0.7", 8092))

        with patch.object(
            Start,
            "AUTO_REPLY",
            {"api": {"host": "127.0.0.7", "port": 8092}},
        ), patch.dict(
            Start.os.environ,
            {"API_HOST": "127.0.0.8", "API_PORT": "8093"},
            clear=True,
        ):
            self.assertEqual(Start._server_address(), ("127.0.0.8", 8093))

    def test_auth_logs_do_not_reference_the_default_password_constant(self):
        source = Path("reply_server.py").read_text(encoding="utf-8")
        logging_lines = [line for line in source.splitlines() if "logger." in line]
        self.assertTrue(logging_lines)
        self.assertTrue(
            all("DEFAULT_ADMIN_PASSWORD" not in line for line in logging_lines)
        )

    def test_obsolete_order_detail_browser_adapters_are_removed(self):
        for path in (
            Path("utils/order_detail_fetcher.py"),
            Path("utils/order_fetcher_optimized.py"),
        ):
            self.assertFalse(path.exists(), f"obsolete browser adapter remains: {path}")

        runtime_source = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
        api_source = Path("reply_server.py").read_text(encoding="utf-8")
        combined = f"{runtime_source}\n{api_source}"
        self.assertNotIn("fetch_order_detail_info", combined)
        self.assertNotIn("order_detail_fetcher", combined)


if __name__ == "__main__":
    unittest.main()
