import inspect
import unittest
from pathlib import Path

import reply_server
from XianyuAutoAsync import XianyuLive


class OfficialLoginArchitectureTests(unittest.TestCase):
    def test_legacy_qr_automation_is_not_reachable_from_active_server_routes(self):
        source = Path(reply_server.__file__).read_text(encoding="utf-8")

        self.assertNotIn("from utils.qr_login import", source)
        self.assertNotIn("qr_login_manager", source)
        self.assertNotIn("process_qr_login_cookies", source)
        self.assertNotIn("refresh_cookies_from_qr_login(", source)

    def test_runtime_refresh_never_submits_saved_password(self):
        source = inspect.getsource(XianyuLive._try_password_login_refresh)

        self.assertNotIn("account_info.get('password')", source)
        self.assertNotIn("allow_password = not", source)
        self.assertIn("allow_password=False", source)

    def test_diagnostics_do_not_claim_saved_password_is_required_for_refresh(self):
        source = inspect.getsource(reply_server.diagnose_auto_reply)

        self.assertNotIn("Cookie 过期后无法自动刷新", source)
        self.assertNotIn("保存闲鱼账号密码后再自动刷新", source)

    def test_official_browser_callback_only_posts_state_to_the_event_loop(self):
        source = inspect.getsource(XianyuLive._try_password_login_refresh)
        callback = source.split("def notification_callback", 1)[1].split(
            "async def commit_validated_result", 1
        )[0]

        self.assertIn("owner_loop.call_soon_threadsafe", callback)
        self.assertNotIn("db_manager.", callback)
        self.assertNotIn("run_coroutine_threadsafe", callback)

    def test_runtime_cleanup_does_not_reactivate_legacy_qr_manager(self):
        source_path = Path(inspect.getsourcefile(XianyuLive) or "XianyuAutoAsync.py")
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn("from utils.qr_login import qr_login_manager", source)
        self.assertNotIn("cookie['value'][:50]", source)
