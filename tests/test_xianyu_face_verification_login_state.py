import unittest

from utils.xianyu_slider_stealth import XianyuSliderStealth


class FakeElement:
    def __init__(self, *, visible=True, children=0, text="", html=""):
        self._visible = visible
        self._children = children
        self._text = text
        self._html = html

    def evaluate(self, _script):
        return self._children

    def inner_html(self):
        return self._html

    def inner_text(self):
        return self._text

    def is_visible(self):
        return self._visible


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class FakePage:
    def __init__(self, *, selectors=None, cookies=None, frames=None, url="https://www.goofish.com/im", content=""):
        self._selectors = selectors or {}
        self.context = FakeContext(cookies or [])
        self.frames = frames or []
        self.url = url
        self._content = content

    def query_selector(self, selector):
        return self._selectors.get(selector)

    def content(self):
        return self._content


def make_checker():
    checker = object.__new__(XianyuSliderStealth)
    checker.pure_user_id = "account-1"
    return checker


def auth_cookies():
    return [
        {"name": "unb", "value": "123"},
        {"name": "cookie2", "value": "cookie2-value"},
        {"name": "_m_h5_tk", "value": "token"},
    ]


class FaceVerificationLoginStateTests(unittest.TestCase):
    def test_face_verification_success_accepts_empty_chat_list_when_auth_cookies_exist(self):
        checker = make_checker()
        page = FakePage(
            selectors={
                ".rc-virtual-list-holder-inner": FakeElement(visible=True, children=0),
            },
            cookies=auth_cookies(),
        )

        self.assertTrue(checker._check_login_success_by_element(page))

    def test_login_form_blocks_cookie_only_success_detection(self):
        checker = make_checker()
        page = FakePage(
            selectors={
                ".rc-virtual-list-holder-inner": FakeElement(visible=True, children=0),
                "#fm-login-id": FakeElement(visible=True),
            },
            cookies=auth_cookies(),
        )

        self.assertFalse(checker._check_login_success_by_element(page))

    def test_verification_iframe_blocks_cookie_only_success_detection(self):
        checker = make_checker()
        page = FakePage(
            selectors={
                ".rc-virtual-list-holder-inner": FakeElement(visible=True, children=0),
                "iframe#alibaba-login-box": FakeElement(visible=True),
            },
            cookies=auth_cookies(),
        )

        self.assertFalse(checker._check_login_success_by_element(page))

    def test_existing_non_empty_chat_list_still_counts_as_success(self):
        checker = make_checker()
        page = FakePage(
            selectors={
                ".rc-virtual-list-holder-inner": FakeElement(visible=True, children=2),
            },
            cookies=[],
        )

        self.assertTrue(checker._check_login_success_by_element(page))

    def test_chat_text_about_captcha_does_not_block_cookie_success_detection(self):
        checker = make_checker()
        page = FakePage(
            selectors={
                ".rc-virtual-list-holder-inner": FakeElement(visible=True, children=0),
            },
            cookies=auth_cookies(),
            content="买家聊天里提到了验证码和滑块，但这不是安全验证页面",
        )

        self.assertTrue(checker._check_login_success_by_element(page))


if __name__ == "__main__":
    unittest.main()
