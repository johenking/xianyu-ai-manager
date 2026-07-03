import os
import tempfile
import unittest

from ai_provider_service import (
    ProviderTestTokenStore,
    extract_gemini_models,
    extract_openai_models,
)
from db_manager import DBManager


class AIProviderDatabaseTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DBManager(self.db_path)
        with self.db.lock:
            self.db.conn.execute(
                "INSERT OR IGNORE INTO users (id, username, email, password_hash) VALUES (2, 'other', 'other@example.com', 'x')"
            )
            self.db.conn.execute(
                "INSERT INTO cookies (id, value, user_id, remark) VALUES ('account-1', 'cookie-value', 1, '主账号')"
            )
            self.db.conn.commit()

    def tearDown(self):
        self.db.conn.close()
        os.unlink(self.db_path)

    def test_provider_profile_crud_is_user_scoped_and_key_is_masked(self):
        profile_id = self.db.create_ai_provider_profile(1, {
            "name": "OpenRouter",
            "provider_type": "openai_compatible",
            "preset": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-secret-value",
            "default_model": "openai/gpt-4.1-mini",
        })

        public_profile = self.db.get_ai_provider_profile(profile_id, 1)
        private_profile = self.db.get_ai_provider_profile(profile_id, 1, include_secret=True)

        self.assertNotIn("sk-secret-value", str(public_profile))
        self.assertTrue(public_profile["api_key_configured"])
        self.assertTrue(public_profile["api_key_masked"].endswith("alue"))
        self.assertEqual(private_profile["api_key"], "sk-secret-value")
        self.assertIsNone(self.db.get_ai_provider_profile(profile_id, 2))
        self.assertEqual(self.db.list_ai_provider_profiles(2), [])

    def test_referenced_provider_cannot_be_deleted(self):
        profile_id = self.db.create_ai_provider_profile(1, {
            "name": "DeepSeek",
            "provider_type": "openai_compatible",
            "preset": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-existing",
            "default_model": "deepseek-v4-flash",
        })
        self.db.save_ai_reply_settings("account-1", {
            "ai_enabled": True,
            "provider_profile_id": profile_id,
            "model_name": "deepseek-v4-flash",
        })

        with self.assertRaisesRegex(ValueError, "正在被账号使用"):
            self.db.delete_ai_provider_profile(profile_id, 1)

    def test_legacy_migration_preserves_effective_account_configuration(self):
        self.db.set_system_setting("ai_api_key", "sk-legacy")
        self.db.set_system_setting("ai_api_url", "https://api.deepseek.com")
        self.db.set_system_setting("ai_model", "deepseek-v4-flash")
        self.db.save_ai_reply_settings("account-1", {
            "ai_enabled": True,
            "model_name": "deepseek-v4-flash",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
        })
        before = self.db.get_ai_reply_settings("account-1")

        migrated = self.db.ensure_legacy_ai_provider_profiles(1)
        after = self.db.get_ai_reply_settings("account-1")

        self.assertEqual(migrated, 1)
        self.assertIsNotNone(after["provider_profile_id"])
        self.assertEqual(after["provider_type"], "openai_compatible")
        self.assertEqual(after["api_key"], before["api_key"])
        self.assertEqual(after["base_url"], before["base_url"])
        self.assertEqual(after["model_name"], before["model_name"])


class AIProviderServiceTests(unittest.TestCase):
    def test_openai_model_list_is_normalized_and_sorted(self):
        result = extract_openai_models({"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]})
        self.assertEqual(result, ["a-model", "z-model"])

    def test_gemini_model_list_keeps_only_generate_content_models(self):
        result = extract_gemini_models({
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            ]
        })
        self.assertEqual(result, ["gemini-2.5-flash"])

    def test_test_token_is_bound_to_user_profile_and_model(self):
        store = ProviderTestTokenStore(ttl_seconds=60)
        token = store.issue(user_id=1, profile_id=9, model_name="deepseek-chat")

        self.assertTrue(store.consume(token, user_id=1, profile_id=9, model_name="deepseek-chat"))
        self.assertFalse(store.consume(token, user_id=1, profile_id=9, model_name="deepseek-reasoner"))
        other = store.issue(user_id=1, profile_id=9, model_name="deepseek-chat")
        self.assertFalse(store.consume(other, user_id=2, profile_id=9, model_name="deepseek-chat"))


if __name__ == "__main__":
    unittest.main()
