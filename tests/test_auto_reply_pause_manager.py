from unittest.mock import patch

from XianyuAutoAsync import AutoReplyPauseManager


def test_pause_is_isolated_by_account_for_the_same_chat_id():
    manager = AutoReplyPauseManager()

    with (
        patch("db_manager.db_manager.get_cookie_pause_duration", return_value=2),
        patch("XianyuAutoAsync.time.time", return_value=1_000),
    ):
        manager.pause_chat("shared-chat", "account-a")

        assert manager.is_chat_paused("shared-chat", "account-a") is True
        assert manager.get_remaining_pause_time("shared-chat", "account-a") == 120
        assert manager.is_chat_paused("shared-chat", "account-b") is False
        assert manager.get_remaining_pause_time("shared-chat", "account-b") == 0


def test_expired_pause_removal_does_not_remove_another_accounts_pause():
    manager = AutoReplyPauseManager()
    manager.paused_chats = {
        ("account-a", "shared-chat"): 1_100,
        ("account-b", "shared-chat"): 1_300,
    }

    with patch("XianyuAutoAsync.time.time", return_value=1_200):
        assert manager.is_chat_paused("shared-chat", "account-a") is False
        assert manager.is_chat_paused("shared-chat", "account-b") is True

    assert ("account-a", "shared-chat") not in manager.paused_chats
    assert ("account-b", "shared-chat") in manager.paused_chats
