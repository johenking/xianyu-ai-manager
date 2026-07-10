import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.xianyu_official_login import (
    OfficialLoginWorker,
    XianyuOfficialLoginService,
)


def authenticated_cookies(unb: str = "123456"):
    return [
        {"name": "unb", "value": unb, "domain": ".goofish.com", "path": "/"},
        {"name": "cookie2", "value": "session-cookie", "domain": ".goofish.com", "path": "/"},
        {"name": "_m_h5_tk", "value": "token", "domain": ".goofish.com", "path": "/"},
    ]


class FakeElement:
    def __init__(self, *, text="", checked=None, on_click=None):
        self.text = text
        self.checked = checked
        self.on_click = on_click
        self.clicked = 0
        self.filled = []
        self.visible = True

    def is_visible(self):
        return self.visible

    def click(self):
        self.clicked += 1
        if self.checked is not None:
            self.checked = not self.checked
        if self.on_click:
            self.on_click()

    def fill(self, value):
        self.filled.append(value)

    def is_checked(self):
        if self.checked is None:
            raise RuntimeError("not a checkbox")
        return self.checked

    def inner_text(self):
        return self.text


class FakeLocator:
    def __init__(self, element=None):
        self.element = element

    def count(self):
        return 1 if self.element is not None else 0

    @property
    def first(self):
        return self.element


class FakePage:
    def __init__(self):
        self.selectors = {}
        self.texts = {}
        self.frames = []
        self.url = "https://www.goofish.com/im"
        self.goto_calls = []

    def query_selector(self, selector):
        return self.selectors.get(selector)

    def get_by_text(self, text, exact=True):
        del exact
        return FakeLocator(self.texts.get(text))

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def wait_for_timeout(self, timeout):
        del timeout

    def screenshot(self, path, **kwargs):
        del kwargs
        Path(path).write_bytes(b"verification")


class FakeContext:
    def __init__(self, page, cookies=None):
        self.pages = [page]
        self.cookies_data = list(cookies or [])
        self.added_cookies = []
        self.closed = False

    def cookies(self, urls=None):
        del urls
        return list(self.cookies_data)

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, factory, context):
        self.factory = factory
        self.context = context

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.factory.launches.append((Path(user_data_dir), kwargs))
        return self.context


class FakePlaywright:
    def __init__(self, factory, context):
        self.chromium = FakeChromium(factory, context)
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeStarter:
    def __init__(self, factory, context):
        self.factory = factory
        self.context = context

    def start(self):
        return FakePlaywright(self.factory, self.context)


class SequencePlaywrightFactory:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.launches = []

    def __call__(self):
        if not self.contexts:
            raise RuntimeError("no fake context left")
        return FakeStarter(self, self.contexts.pop(0))


def make_password_context(*, unb="123456", error_message="", security=False):
    page = FakePage()
    context = FakeContext(page)
    account_input = FakeElement()
    password_input = FakeElement()
    agreement = FakeElement(checked=False)
    keep_login = FakeElement()

    def switch_to_password():
        page.selectors["#fm-login-password"] = password_input

    password_tab = FakeElement(on_click=switch_to_password)

    def submit_login():
        for selector in (
            "#fm-login-id",
            "#fm-login-password",
            "a.password-login-tab-item",
            "input[type='checkbox']",
            "button.password-login",
        ):
            page.selectors.pop(selector, None)
        if error_message:
            page.selectors[".fm-error"] = FakeElement(text=error_message)
        elif security:
            page.selectors[".nc-container"] = FakeElement()
        else:
            def confirm_keep_login():
                context.cookies_data = authenticated_cookies(unb)
                page.texts.pop("保持登录", None)

            keep_login.on_click = confirm_keep_login
            page.texts["保持登录"] = keep_login

    login_button = FakeElement(on_click=submit_login)
    page.selectors.update({
        "#fm-login-id": account_input,
        "a.password-login-tab-item": password_tab,
        "input[type='checkbox']": agreement,
        "button.password-login": login_button,
    })
    return context, {
        "account_input": account_input,
        "password_input": password_input,
        "password_tab": password_tab,
        "agreement": agreement,
        "keep_login": keep_login,
        "login_button": login_button,
    }


class XianyuOfficialLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.profile_root = self.root / "browser_data"
        self.verification_root = self.root / "static" / "uploads" / "images"

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self, factory, **kwargs):
        return XianyuOfficialLoginService(
            profile_root=self.profile_root,
            verification_root=self.verification_root,
            playwright_factory=factory,
            poll_interval=kwargs.pop("poll_interval", 0.001),
            login_timeout=kwargs.pop("login_timeout", 0.05),
            verification_timeout=kwargs.pop("verification_timeout", 0.02),
            **kwargs,
        )

    def test_initial_login_switches_from_sms_and_confirms_agreement_and_keep_login(self):
        context, elements = make_password_context(unb="9988")
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)

        result = service.login_with_password(
            account="13800138000",
            password="secret",
            show_browser=True,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.unb, "9988")
        self.assertTrue(result.used_password)
        self.assertEqual(elements["password_tab"].clicked, 1)
        self.assertEqual(elements["account_input"].filled, ["13800138000"])
        self.assertEqual(elements["password_input"].filled, ["secret"])
        self.assertTrue(elements["agreement"].checked)
        self.assertEqual(elements["keep_login"].clicked, 1)
        self.assertEqual(factory.launches[0][0].parent, self.profile_root)
        self.assertTrue(factory.launches[0][0].name.startswith(".login_"))
        self.assertTrue((self.profile_root / "user_9988").is_dir())
        self.assertEqual(list(self.profile_root.glob(".login_*")), [])

    def test_refresh_reuses_canonical_profile_without_password(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("9988"))
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
        )

        self.assertTrue(result.succeeded)
        self.assertFalse(result.used_password)
        self.assertEqual(factory.launches[0][0], self.profile_root / "user_9988")

    def test_authenticated_cookies_do_not_override_a_visible_login_form(self):
        context, _ = make_password_context(unb="9988")
        context.cookies_data = authenticated_cookies("9988")
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "no_credentials")

    def test_refresh_rejects_a_profile_logged_into_another_unb(self):
        page = FakePage()
        context = FakeContext(page, authenticated_cookies("other-unb"))
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.refresh_session(
            profile_unb="expected-unb",
            current_cookie="unb=expected-unb; cookie2=old",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "account_mismatch")
        self.assertEqual(result.unb, "other-unb")

    def test_refresh_falls_back_to_saved_credentials_in_same_profile(self):
        context, elements = make_password_context(unb="9988")
        factory = SequencePlaywrightFactory([context])
        service = self.make_service(factory)

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=expired",
            account="seller@example.com",
            password="secret",
        )

        self.assertTrue(result.succeeded)
        self.assertTrue(result.used_password)
        self.assertEqual(elements["password_tab"].clicked, 1)
        self.assertEqual(factory.launches[0][0], self.profile_root / "user_9988")

    def test_wrong_password_returns_official_error(self):
        context, _ = make_password_context(error_message="账号或密码错误")
        service = self.make_service(SequencePlaywrightFactory([context]))

        result = service.login_with_password(
            account="seller@example.com",
            password="wrong",
            show_browser=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "invalid_credentials")
        self.assertEqual(result.message, "账号或密码错误")

    def test_verification_reopens_visible_browser_and_times_out(self):
        first_context, _ = make_password_context(security=True)
        second_page = FakePage()
        second_page.selectors[".nc-container"] = FakeElement()
        second_context = FakeContext(second_page)
        factory = SequencePlaywrightFactory([first_context, second_context])
        statuses = []
        service = self.make_service(factory)

        result = service.login_with_password(
            account="seller@example.com",
            password="secret",
            show_browser=False,
            on_status=statuses.append,
        )

        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_code, "verification_timeout")
        self.assertTrue(Path(result.verification_image_path).is_file())
        self.assertEqual([launch[1]["headless"] for launch in factory.launches], [False, False])
        self.assertIn("--window-position=-32000,-32000", factory.launches[0][1]["args"])
        self.assertNotIn("--window-position=-32000,-32000", factory.launches[1][1]["args"])
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].status, "verification_required")

    def test_pre_cancelled_worker_stops_before_browser_launch(self):
        factory = SequencePlaywrightFactory([])
        service = self.make_service(factory)
        worker = OfficialLoginWorker()
        worker.close_browser()

        result = service.refresh_session(
            profile_unb="9988",
            current_cookie="unb=9988; cookie2=old",
            worker=worker,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(factory.launches, [])

    def test_profile_promotion_restores_backup_when_replacement_fails(self):
        service = self.make_service(SequencePlaywrightFactory([]))
        temporary = self.profile_root / ".login_temp"
        target = self.profile_root / "user_9988"
        temporary.mkdir(parents=True)
        target.mkdir(parents=True)
        (temporary / "new.txt").write_text("new", encoding="utf-8")
        (target / "old.txt").write_text("old", encoding="utf-8")
        real_replace = os.replace

        def flaky_replace(source, destination):
            if Path(source) == temporary and Path(destination) == target:
                raise OSError("promotion failed")
            return real_replace(source, destination)

        with patch("utils.xianyu_official_login.os.replace", side_effect=flaky_replace):
            with self.assertRaises(OSError):
                service._promote_profile(temporary, "9988")

        self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old")
        self.assertTrue((temporary / "new.txt").is_file())
        self.assertEqual(list(self.profile_root.glob("user_9988.backup-*")), [])


if __name__ == "__main__":
    unittest.main()
