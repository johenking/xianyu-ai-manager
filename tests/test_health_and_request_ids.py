import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_factory import assert_single_worker_configuration, create_app


class HealthAndRequestIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_live_probe_and_http_error_include_request_id(self):
        with TestClient(self.app) as client:
            live = client.get("/health/live", headers={"X-Request-ID": "request-test-123"})
            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.headers["X-Request-ID"], "request-test-123")

            missing = client.get("/api/definitely-missing")
            self.assertEqual(missing.status_code, 404)
            self.assertTrue(missing.json()["request_id"])
            self.assertIn("detail", missing.json())

    def test_ready_probe_reports_schema_and_runtime_summary(self):
        with TestClient(self.app) as client:
            response = client.get("/health/ready")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "ready")
            self.assertIn("migration_version", payload)
            self.assertIn("runtime_sessions", payload)

    def test_multiple_workers_are_rejected(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}):
            with self.assertRaisesRegex(RuntimeError, "单 worker"):
                assert_single_worker_configuration()


if __name__ == "__main__":
    unittest.main()
