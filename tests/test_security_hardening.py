import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app_factory import create_app
from utils.outbound_http import PublicHTTPResponse

import reply_server
from official_login_sessions import (
    OfficialLoginSessionCoordinator,
    OfficialLoginSessionRecord,
)
from utils.qr_login import QRLoginManager, QRLoginSession
from utils.qr_verification_browser import QRVerificationBrowser
from utils.xianyu_official_login import XianyuOfficialLoginService


class _Registry:
    def __init__(self, records=None):
        self.records = dict(records or {})

    def cleanup(self):
        return None

    def get(self, session_id):
        return self.records.get(session_id)

    def register(self, session_id, session_type, owner_user_id, **kwargs):
        self.records[session_id] = {
            "session_id": session_id,
            "session_type": session_type,
            "owner_user_id": owner_user_id,
            "status": kwargs.get("status", "created"),
        }

    def update(self, session_id, **kwargs):
        if session_id in self.records:
            self.records[session_id].update(kwargs)


class RemovedRemoteControlTests(unittest.TestCase):
    def test_legacy_unauthenticated_control_surfaces_are_absent(self):
        paths = create_app().openapi()["paths"]

        self.assertFalse(any(path.startswith("/api/captcha") for path in paths))
        self.assertNotIn("/xianyu/reply", paths)
        self.assertFalse(Path("api_captcha_remote.py").exists())
        self.assertFalse(Path("captcha_control.html").exists())
        self.assertFalse(Path("utils/captcha_remote_control.py").exists())

    def test_removed_reply_route_is_absent_from_runtime_configuration(self):
        removed_path = "/xianyu/" + "reply"
        for path in (
            Path("Start.py"),
            Path("config.py"),
            Path("global_config.yml"),
            Path("tests/snapshots/openapi_methods.json"),
        ):
            with self.subTest(path=path):
                self.assertNotIn(removed_path, path.read_text(encoding="utf-8"))

    def test_safe_login_image_routes_are_part_of_the_authenticated_api(self):
        paths = create_app().openapi()["paths"]

        self.assertIn(
            "/api/official-login/sessions/{session_id}/image",
            paths,
        )
        self.assertIn("/qr-login/verification-image/{session_id}", paths)
        self.assertIn("/face-verification/screenshot/{account_id}", paths)


class ApiLogIdentityTests(unittest.TestCase):
    def test_user_log_prefix_is_a_stable_digest_without_identity(self):
        user_info = {"user_id": 2219255254384, "username": "private-operator"}
        expected = hashlib.sha256(
            str(user_info["user_id"]).encode("utf-8")
        ).hexdigest()[:10]

        prefix = reply_server.get_user_log_prefix(user_info)

        self.assertEqual(prefix, f"【user_{expected}】")
        self.assertNotIn(user_info["username"], prefix)
        self.assertNotIn(str(user_info["user_id"]), prefix)


class UserBackupUploadTests(unittest.IsolatedAsyncioTestCase):
    def _upload(self, filename: str, body: bytes):
        return reply_server.UploadFile(filename=filename, file=io.BytesIO(body))

    async def _invoke_import(self, filename: str, body: bytes):
        result = reply_server.import_backup(
            self._upload(filename, body),
            current_user={"user_id": 7, "username": "operator"},
        )
        if inspect.isawaitable(result):
            return await result
        return result

    async def test_invalid_extension_preserves_client_error_status(self):
        with self.assertRaises(reply_server.HTTPException) as raised:
            await self._invoke_import("backup.txt", b"{}")

        self.assertEqual(raised.exception.status_code, 400)

    async def test_oversized_backup_is_rejected_before_json_parsing(self):
        with patch.object(reply_server, "USER_BACKUP_MAX_BYTES", 16):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await self._invoke_import("backup.json", b"x" * 17)

        self.assertEqual(raised.exception.status_code, 413)

    async def test_database_rejection_preserves_bad_request_status(self):
        database = Mock()
        database.import_backup.return_value = False
        payload = json.dumps({"version": 1, "data": {}}).encode("utf-8")

        with patch("db_manager.db_manager", database):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await self._invoke_import("backup.json", payload)

        self.assertEqual(raised.exception.status_code, 400)

    async def test_import_does_not_report_success_when_runtime_reconcile_fails(self):
        database = Mock()
        database.import_backup.return_value = True
        manager = Mock()
        manager.reconcile_from_db = AsyncMock(
            return_value={"success": False, "failed": 1}
        )
        payload = json.dumps({"version": 1, "data": {}}).encode("utf-8")

        with (
            patch("db_manager.db_manager", database),
            patch("cookie_manager.manager", manager),
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await self._invoke_import("backup.json", payload)

        self.assertEqual(raised.exception.status_code, 503)
        manager.reconcile_from_db.assert_awaited_once()


class RuntimeDestructiveOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_deletion_does_not_report_success_when_reconcile_fails(self):
        database = Mock()
        database.get_user_by_id.return_value = {
            "id": 8,
            "username": "removed-user",
        }
        database.delete_user_and_data.return_value = True
        manager = Mock()
        manager.reconcile_from_db = AsyncMock(
            return_value={"success": False, "failed": 1}
        )

        with (
            patch("db_manager.db_manager", database),
            patch("cookie_manager.manager", manager),
        ):
            result = reply_server.delete_user(
                8,
                admin_user={"user_id": 1, "username": "admin"},
            )
            if inspect.isawaitable(result):
                with self.assertRaises(reply_server.HTTPException) as raised:
                    await result
            else:
                self.fail("delete_user must await runtime reconciliation")

        self.assertEqual(raised.exception.status_code, 503)
        database.delete_user_and_data.assert_called_once_with(8)
        manager.reconcile_from_db.assert_awaited_once()

    async def test_online_raw_database_restore_is_permanently_disabled(self):
        signature = inspect.signature(reply_server.upload_database_backup)
        self.assertNotIn(
            "backup_file",
            signature.parameters,
            "disabled restore route must not accept or parse a database upload",
        )

        with self.assertRaises(reply_server.HTTPException) as raised:
            await reply_server.upload_database_backup(
                admin_user={"user_id": 1, "username": "admin"}
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("停服", raised.exception.detail)


class TenantScopedReplyTests(unittest.TestCase):
    def test_item_reply_uses_the_cookie_and_item_composite_key(self):
        database = Mock()
        database.get_all_cookies.return_value = {"account-1": "redacted"}
        database.get_item_reply.return_value = {
            "cookie_id": "account-1",
            "item_id": "item-1",
            "reply": "synthetic reply",
        }
        database.get_itemReplays_by_cookie.return_value = [
            database.get_item_reply.return_value
        ]

        with patch.object(reply_server, "db_manager", database):
            result = reply_server.get_item_reply(
                "account-1",
                "item-1",
                current_user={"user_id": 7, "username": "operator"},
            )

        self.assertEqual(result["item_id"], "item-1")
        database.get_item_reply.assert_called_once_with("account-1", "item-1")
        database.get_itemReplays_by_cookie.assert_not_called()


class QRSessionOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_rejects_a_session_missing_from_the_owner_registry(self):
        qr_manager = Mock()
        qr_manager.get_session_status.return_value = {"status": "waiting"}

        with (
            patch.object(reply_server, "get_session_registry", return_value=_Registry()),
            patch.object(reply_server, "qr_login_manager", qr_manager),
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.check_qr_code_status(
                    "missing-session",
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 404)
        qr_manager.get_session_status.assert_not_called()

    async def test_consumption_rejects_a_session_missing_from_the_owner_registry(self):
        qr_manager = Mock()

        with (
            patch.object(reply_server, "get_session_registry", return_value=_Registry()),
            patch.object(reply_server, "qr_login_manager", qr_manager),
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.continue_qr_code_after_verification(
                    "missing-session",
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 404)
        qr_manager.continue_after_verification.assert_not_called()


class PrivateVerificationImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_login_status_uses_an_opaque_authenticated_image_url(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"XIANYU_PRIVATE_VERIFICATION_DIR": temp_dir},
        ):
            image_path = Path(temp_dir) / "official.png"
            image_path.write_bytes(b"synthetic-image")
            coordinator = OfficialLoginSessionCoordinator(
                completion_handler=lambda *_args: None,
                registry=_Registry(),
            )
            record = OfficialLoginSessionRecord(
                session_id="official-session",
                owner_user_id=7,
                mode="qr",
                state="waiting_user",
                image_path=str(image_path),
            )
            coordinator._sessions[record.session_id] = record

            status = await coordinator.get_status(record.session_id, 7)
            owned_path = await coordinator.get_image_path(record.session_id, 7)
            other_path = await coordinator.get_image_path(record.session_id, 8)

        self.assertEqual(
            status["qr_image_url"],
            "/api/official-login/sessions/official-session/image",
        )
        self.assertNotIn(temp_dir, str(status))
        self.assertEqual(owned_path, str(image_path.resolve()))
        self.assertIsNone(other_path)

    def test_default_screenshot_roots_are_private_and_environment_configurable(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"XIANYU_PRIVATE_VERIFICATION_DIR": temp_dir},
        ):
            official = XianyuOfficialLoginService()
            qr_browser = QRVerificationBrowser(profile_root=Path(temp_dir) / "profiles")

        self.assertEqual(official.verification_root, Path(temp_dir))
        self.assertEqual(qr_browser.verification_root, Path(temp_dir))
        self.assertNotIn("static", official.verification_root.parts)
        self.assertNotIn("static", qr_browser.verification_root.parts)

    def test_api_qr_status_never_returns_the_private_filesystem_path(self):
        manager = QRLoginManager(verification_browser=Mock())
        session = QRLoginSession("qr-session")
        session.status = "verification_required"
        session.verification_url = "https://passport.example.test/verify"
        session.verification_screenshot_path = "/private/runtime/qr.png"
        manager.sessions[session.session_id] = session

        status = manager.get_session_status(session.session_id)

        self.assertEqual(
            status["verification_screenshot_path"],
            "/qr-login/verification-image/qr-session",
        )
        self.assertNotIn("/private/runtime", str(status))


class LegacyAISettingsVerificationTests(unittest.TestCase):
    def _request(self, url: str):
        return reply_server.SystemSettingsVerifyIn(
            settings={
                "ai_api_url": url,
                "ai_model": "synthetic-model",
                "ai_api_key": "synthetic-secret",
            },
            secret_actions={"ai_api_key": "set"},
        )

    def test_legacy_ai_verification_uses_the_guarded_http_client(self):
        database = Mock()
        database.get_all_system_settings.return_value = {}
        response = PublicHTTPResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"choices":[{"message":{"content":"OK"}}]}',
        )

        with (
            patch.object(reply_server, "db_manager", database),
            patch.object(
                reply_server,
                "request_public_http_sync",
                return_value=response,
            ) as request_mock,
            patch("openai.OpenAI") as legacy_client,
        ):
            result = reply_server.verify_settings_section(
                "ai",
                self._request("https://provider.example.test/v1"),
                _={"user_id": 1, "username": "admin"},
            )

        self.assertTrue(result["success"])
        legacy_client.assert_not_called()
        request_mock.assert_called_once()
        self.assertEqual(
            request_mock.call_args.args[:2],
            ("POST", "https://provider.example.test/v1/chat/completions"),
        )
        self.assertEqual(
            request_mock.call_args.kwargs["allowed_methods"],
            ("POST",),
        )

    def test_private_or_plain_http_ai_targets_fail_before_any_request(self):
        database = Mock()
        database.get_all_system_settings.return_value = {}

        for url in ("http://127.0.0.1/v1", "http://provider.example.test/v1"):
            with self.subTest(url=url):
                with (
                    patch.object(reply_server, "db_manager", database),
                    patch.object(
                        reply_server,
                        "request_public_http_sync",
                    ) as request_mock,
                    patch("openai.OpenAI") as legacy_client,
                ):
                    with self.assertRaises(reply_server.HTTPException):
                        reply_server.verify_settings_section(
                            "ai",
                            self._request(url),
                            _={"user_id": 1, "username": "admin"},
                        )

                    request_mock.assert_not_called()
                    legacy_client.assert_not_called()


class SourcePrivacyCleanupTests(unittest.TestCase):
    def test_message_samples_and_deprecated_ai_debug_code_are_removed(self):
        ai_source = Path("ai_reply_engine.py").read_text(encoding="utf-8")
        util_source = Path("utils/xianyu_utils.py").read_text(encoding="utf-8")
        js_source = Path("static/xianyu_js_version_2.js").read_text(encoding="utf-8")
        api_source = Path("reply_server.py").read_text(encoding="utf-8")

        self.assertNotIn("def increment_bargain_count", ai_source)
        self.assertNotIn("最近10条user消息", ai_source)
        self.assertNotIn("msg[0][:10]", ai_source)
        self.assertNotIn("if __name__ == '__main__':", util_source)
        self.assertNotIn("// let msg =", js_source)
        self.assertNotIn("// msg =", js_source)
        self.assertNotIn("message_preview", api_source)


if __name__ == "__main__":
    unittest.main()
