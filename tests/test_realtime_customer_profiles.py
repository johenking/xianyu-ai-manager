"""实时消息客户昵称落库的隔离契约测试。"""

import unittest

import XianyuAutoAsync as runtime


class _RecordingDatabase:
    def __init__(self):
        self.calls = []

    def upsert_customer_observation(self, **kwargs):
        self.calls.append(kwargs)
        return True


class RealtimeCustomerProfileTests(unittest.TestCase):
    def setUp(self):
        self.helper = getattr(runtime, "_upsert_realtime_customer_profile", None)
        self.assertIsNotNone(self.helper, "实时身份写入辅助函数尚未实现")

    def test_nonempty_sender_identity_is_stored_with_message_observation_time(self):
        database = _RecordingDatabase()

        result = self.helper(
            database=database,
            cookie_id="account-1",
            sender_user_id="buyer-1",
            sender_nickname="买家昵称",
            observed_at=1720000000.25,
        )

        self.assertTrue(result)
        self.assertEqual(
            database.calls,
            [{
                "cookie_id": "account-1",
                "buyer_id": "buyer-1",
                "display_name": "买家昵称",
                "avatar_url": "",
                "source": "realtime_message",
                "observed_at": 1720000000.25,
            }],
        )

    def test_missing_or_placeholder_identity_writes_nothing(self):
        database = _RecordingDatabase()
        cases = (
            ("", "buyer-1", "买家昵称", 1.0),
            ("account-1", "", "买家昵称", 1.0),
            ("account-1", "unknown", "买家昵称", 1.0),
            ("account-1", "buyer-1", "", 1.0),
            ("account-1", "buyer-1", "未知用户", 1.0),
            ("account-1", "buyer-1", "买家昵称", 0.0),
        )
        for cookie_id, buyer_id, nickname, observed_at in cases:
            with self.subTest(
                cookie_id=cookie_id,
                buyer_id=buyer_id,
                nickname=nickname,
                observed_at=observed_at,
            ):
                self.assertFalse(self.helper(
                    database=database,
                    cookie_id=cookie_id,
                    sender_user_id=buyer_id,
                    sender_nickname=nickname,
                    observed_at=observed_at,
                ))
        self.assertEqual(database.calls, [])


if __name__ == "__main__":
    unittest.main()
