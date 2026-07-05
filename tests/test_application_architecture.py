import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

from app_factory import create_app


class ApplicationFactoryTests(unittest.IsolatedAsyncioTestCase):
    def test_all_legacy_routes_are_registered_through_domain_routers(self):
        app = create_app()
        paths = app.openapi()["paths"]
        signatures = {
            (method.upper(), path)
            for path, definition in paths.items()
            for method in definition
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
        }
        self.assertEqual(len(signatures), 198)
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
        self.assertIn(("GET", "/health/live"), signatures)
        self.assertIn(("GET", "/health/ready"), signatures)

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


if __name__ == "__main__":
    unittest.main()
