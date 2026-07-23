from html.parser import HTMLParser
from pathlib import Path
import unittest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "goofish_official_login.html"


class FixtureContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.attributes.append((tag, dict(attrs)))


class OfficialLoginFixtureTests(unittest.TestCase):
    def test_fixture_covers_official_parent_and_human_login_surfaces(self):
        source = FIXTURE_PATH.read_text(encoding="utf-8")
        parser = FixtureContractParser()
        parser.feed(source)
        attributes = parser.attributes

        self.assertTrue(any(attrs.get("data-entry") == "https://www.goofish.com/login" for _, attrs in attributes))
        self.assertTrue(any(attrs.get("data-app-name") == "xianyu" for _, attrs in attributes))
        self.assertTrue(any("qrcode-img" in attrs.get("class", "") for _, attrs in attributes))
        self.assertTrue(any(attrs.get("type") == "tel" for _, attrs in attributes))
        self.assertTrue(any(attrs.get("id") == "fm-login-password" for _, attrs in attributes))
        self.assertTrue(any("nc-container" in attrs.get("class", "") for _, attrs in attributes))
        self.assertIn("__xianyuOfficialLoginEvents", source)
        self.assertIn("loginResult", source)
