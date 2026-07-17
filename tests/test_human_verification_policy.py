import inspect
import unittest

from db_manager import DBManager
from cookie_manager import CookieManager
from XianyuAutoAsync import XianyuLive, _mask_account_ids_in_log, log_captcha_event


class HumanVerificationPolicyTests(unittest.TestCase):
    def test_token_refresh_does_not_invoke_automatic_slider_solver(self):
        source = inspect.getsource(XianyuLive.refresh_token)

        self.assertNotIn("_handle_captcha_verification", source)
        self.assertNotIn("XianyuSliderStealth", source)
        self.assertNotIn("Token刷新失败: {res_json}", source)
        self.assertNotIn("滑块验证重试", source)

    def test_verification_log_uses_a_digest_instead_of_the_raw_account_id(self):
        source = inspect.getsource(log_captcha_event)

        self.assertIn("hashlib.sha256", source)
        self.assertNotIn("【{cookie_id}】", source)

    def test_runtime_log_patcher_masks_stable_account_identifiers(self):
        record = {
            "message": (
                "【2219255254384】正在重启账号监听；"
                "更新账号 2219255254384 信息成功；"
                "用户ID: 2219255254384；"
                "'cookie_id': '2219255254384'"
            )
        }

        _mask_account_ids_in_log(record)

        self.assertNotIn("2219255254384", record["message"])
        self.assertRegex(record["message"], r"^【account_[0-9a-f]{10}】")

    def test_listener_bootstrap_masks_account_id_before_xianyu_import(self):
        source = inspect.getsource(CookieManager._run_xianyu)
        before_xianyu_import = source.split("from XianyuAutoAsync", 1)[0]

        self.assertIn("_mask_cookie_id(cookie_id)", before_xianyu_import)
        self.assertNotIn("【{cookie_id}】", before_xianyu_import)

    def test_item_sync_logs_only_a_bounded_response_summary(self):
        source = inspect.getsource(XianyuLive.get_item_list_info)

        self.assertNotIn("商品信息获取响应: {res_json}", source)
        self.assertNotIn("获取商品信息失败: {res_json}", source)
        self.assertNotIn("已从Cookie读取_m_h5_tk token", source)
        self.assertNotIn('print(f"📦 账号 {self.myid}', source)
        self.assertNotIn("完整信息", source)
        self.assertIn("商品列表响应摘要", source)

    def test_item_lookup_does_not_log_the_complete_database_record(self):
        source = inspect.getsource(DBManager.get_item_info)

        self.assertNotIn("item_info: {item_info}", source)
        self.assertIn("已读取商品信息摘要", source)

    def test_item_detail_request_logs_only_a_bounded_response_summary(self):
        source = inspect.getsource(XianyuLive.get_item_info)

        self.assertNotIn("商品信息获取成功: {res_json}", source)
        self.assertNotIn("商品信息API返回格式异常: {res_json}", source)
        self.assertNotIn("已从Cookie读取_m_h5_tk token", source)
        self.assertIn("商品详情响应摘要", source)
