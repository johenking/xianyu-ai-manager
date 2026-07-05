from pathlib import Path
import tempfile
import time
import unittest

from db_manager import DBManager
from repositories.runtime_session_repository import RuntimeSessionRepository
from session_registry import SessionRegistry, sanitize_runtime_error


class SessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = DBManager(str(self.root / "manager.db"))
        self.registry = SessionRegistry(
            RuntimeSessionRepository(self.db.conn),
            self.db.lock,
        )

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_registry_persists_only_safe_metadata_and_enforces_owner(self):
        transient = {
            "password": "buyer-password",
            "cookie": "unb=private-cookie",
            "browser": object(),
        }
        self.registry.register(
            "session-1",
            "password_login",
            7,
            account_id="account-1",
            status="processing",
            transient=transient,
        )
        self.registry.update(
            "session-1",
            status="failed",
            error_code="remote_error",
            error_message=(
                "password=buyer-password token=private-token "
                "https://example.com/verify?secret=value"
            ),
        )

        stored = self.db.conn.execute(
            "SELECT * FROM runtime_sessions WHERE session_id = 'session-1'"
        ).fetchone()
        serialized = "|".join(str(value or "") for value in stored)
        self.assertNotIn("buyer-password", serialized)
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("example.com", serialized)
        self.assertIs(self.registry.transient("session-1", 7), transient)
        self.assertIsNone(self.registry.transient("session-1", 8))

    def test_restart_marks_nonrecoverable_sessions_interrupted(self):
        self.registry.register("qr-1", "qr_login", 1, status="verification_required")
        self.registry.register("lab-1", "ai_training", 1, status="success")

        self.assertEqual(self.registry.recover_after_restart(), 1)
        self.assertEqual(self.registry.get("qr-1")["status"], "interrupted")
        self.assertEqual(self.registry.get("lab-1")["status"], "success")

    def test_expired_metadata_and_transient_objects_are_cleaned(self):
        self.registry.register(
            "short",
            "qr_login",
            1,
            ttl_seconds=1,
            transient=object(),
        )
        self.db.conn.execute(
            "UPDATE runtime_sessions SET expires_at = ? WHERE session_id = 'short'",
            (time.time() - 1,),
        )
        self.db.conn.commit()
        self.assertEqual(self.registry.cleanup(), 1)
        self.assertIsNone(self.registry.get("short"))

    def test_error_sanitizer_removes_sensitive_values(self):
        result = sanitize_runtime_error(
            "Cookie: unb=secret Authorization=BearerSecret https://example.com/token"
        )
        self.assertNotIn("secret", result.lower())
        self.assertNotIn("example.com", result)


if __name__ == "__main__":
    unittest.main()
