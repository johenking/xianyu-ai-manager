import os
import inspect
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from db_manager import db_manager
from utils import item_search


def ready_context(
    *,
    user_id=7,
    account_id="account-1",
    unb="9988",
    revision=3,
):
    return {
        "state": "ready",
        "user_id": user_id,
        "account_id": account_id,
        "xianyu_unb": unb,
        "cookie_revision": revision,
        "value": f"unb={unb}; cookie2=session-value",
        "browser_user_agent": "Mozilla/5.0 Test Browser",
    }


class ItemSearchAccountBindingTests(unittest.IsolatedAsyncioTestCase):
    def test_search_risk_handler_has_no_automation_or_remote_control(self):
        source = inspect.getsource(item_search.XianyuSearcher.handle_slider_verification)
        self.assertNotIn("solve_slider", source)
        self.assertNotIn("XianyuSliderStealth", source)
        self.assertNotIn("captcha_controller", source)

    async def test_risk_control_is_reported_without_invoking_slider_solver(self):
        class VisibleElement:
            async def is_visible(self):
                return True

        class RiskPage:
            def __init__(self):
                self.main_frame = object()
                self.frames = [self.main_frame]

            async def content(self):
                return "<div id='nc_1_n1z'>verification</div>"

            async def query_selector(self, selector):
                return VisibleElement() if selector == "#nc_1_n1z" else None

        class ForbiddenSlider:
            calls = 0

            def __init__(self, **_kwargs):
                self.__class__.calls += 1

        fake_slider_module = types.ModuleType("utils.xianyu_slider_stealth")
        fake_slider_module.XianyuSliderStealth = ForbiddenSlider
        searcher = item_search.XianyuSearcher(
            user_id=7,
            account_id="account-1",
            account_context=ready_context(),
        )

        with (
            patch.object(item_search.asyncio, "sleep", new=AsyncMock()),
            patch.dict(
                sys.modules,
                {"utils.xianyu_slider_stealth": fake_slider_module},
            ),
        ):
            with self.assertRaises(item_search.SearchAccountBindingError) as raised:
                await searcher.handle_slider_verification(RiskPage())

        self.assertEqual(raised.exception.state, "action_required")
        self.assertEqual(raised.exception.reason, "risk_control")
        self.assertEqual(ForbiddenSlider.calls, 0)

    async def test_cross_user_binding_fails_before_playwright_starts(self):
        with (
            patch.object(
                db_manager,
                "get_owned_cookie_search_context",
                return_value={
                    "state": "ownership_mismatch",
                    "reason": "account_not_owned",
                },
            ),
            patch.object(item_search, "async_playwright") as playwright_mock,
        ):
            result = await item_search.search_xianyu_items(
                "canary",
                user_id=7,
                account_id="other-account",
            )

        self.assertEqual(result["error_code"], "ownership_mismatch")
        playwright_mock.assert_not_called()

    async def test_empty_account_fails_before_database_or_playwright(self):
        with (
            patch.object(
                db_manager,
                "get_owned_cookie_search_context",
            ) as context_mock,
            patch.object(item_search, "async_playwright") as playwright_mock,
        ):
            result = await item_search.search_xianyu_items(
                "canary",
                user_id=7,
                account_id="",
            )

        self.assertEqual(result["error_code"], "action_required")
        context_mock.assert_not_called()
        playwright_mock.assert_not_called()

    async def test_revision_change_fails_before_playwright_starts(self):
        context = ready_context()
        searcher = item_search.XianyuSearcher(
            user_id=7,
            account_id="account-1",
            account_context=context,
        )
        changed = dict(context, cookie_revision=4)
        with (
            patch.object(
                db_manager,
                "get_owned_cookie_search_context",
                return_value=changed,
            ),
            patch.object(item_search, "async_playwright") as playwright_mock,
        ):
            result = await searcher.search_items("canary")

        self.assertIn("cookie_revision_conflict", result["error"])
        playwright_mock.assert_not_called()

    async def test_parsed_item_drops_full_response_fields(self):
        searcher = item_search.XianyuSearcher(
            user_id=7,
            account_id="account-1",
            account_context=ready_context(),
        )
        parsed = await searcher._parse_real_item({
            "secret_response_field": "must-not-survive",
            "data": {
                "item": {
                    "main": {
                        "exContent": {
                            "title": "Canary item",
                            "price": [{"text": "99"}],
                            "area": "杭州",
                            "userNickName": "seller",
                            "picUrl": "//img.example/item.jpg",
                            "fishTags": {},
                        },
                        "clickParam": {
                            "args": {
                                "item_id": "item-1",
                                "publishTime": "1700000000000",
                            }
                        },
                        "targetUrl": "fleamarket://item?id=item-1",
                    }
                }
            },
        })

        self.assertEqual(parsed["item_id"], "item-1")
        self.assertNotIn("raw_data", parsed)
        self.assertNotIn("secret_response_field", parsed)

    def test_response_schema_and_result_limit_are_enforced(self):
        with self.assertRaises(ValueError):
            item_search.XianyuSearcher._extract_search_items({"data": {}})
        with self.assertRaises(ValueError):
            item_search.XianyuSearcher._extract_search_items({
                "data": {
                    "resultList": [
                        {"data": index}
                        for index in range(item_search.SEARCH_RESPONSE_ITEM_LIMIT + 1)
                    ]
                }
            })

    def test_browser_profiles_are_account_isolated(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"XIANYU_SEARCH_BROWSER_DATA_DIR": root},
        ):
            first = item_search.XianyuSearcher(
                user_id=7,
                account_id="account-1",
                account_context=ready_context(account_id="account-1"),
            )
            second = item_search.XianyuSearcher(
                user_id=7,
                account_id="account-2",
                account_context=ready_context(account_id="account-2"),
            )

            self.assertNotEqual(first._profile_path(), second._profile_path())
            self.assertTrue(first._profile_path().is_dir())
            self.assertTrue(second._profile_path().is_dir())


if __name__ == "__main__":
    unittest.main()
