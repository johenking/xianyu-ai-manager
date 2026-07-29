"""迁移 2026072608 tenant_isolation_hardening_v1 测试。

历史上 legacy 启动逻辑把 user_id IS NULL 的发货规则批量划给 admin，
可能留下「规则 user_id ≠ 所绑卡券 user_id」的脏数据：匹配时会把
他人卡券内容发出去。迁移要求：
- 绑定了有效卡券的规则，归属修正为卡券所有者（卡券主人不受损）
- 卡券归属一致 / 悬空 card_id / 未绑卡券的规则保持不变
- 为 cards/delivery_rules/cookies 建 user_id 租户过滤索引
- 幂等：重复执行结果一致
"""

import os
from pathlib import Path
import tempfile
import unittest

from db_manager import DBManager
from schema_migrations import MIGRATIONS


def _find_migration(version: str):
    for migration in MIGRATIONS:
        if migration.version == version:
            return migration
    return None


class TenantIsolationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.previous_key_file = os.environ.get("SYSTEM_SECRET_KEY_FILE")
        os.environ["SYSTEM_SECRET_KEY_FILE"] = str(self.root / ".system-key")
        self.db = DBManager(str(self.root / "tenant.db"))
        self.assertTrue(
            self.db.create_user("seller-one", "seller-one@example.test", "Strong-pass-2026!")
        )
        self.assertTrue(
            self.db.create_user("seller-two", "seller-two@example.test", "Strong-pass-2026!")
        )
        self.user_one = self.db.get_user_by_username("seller-one")["id"]
        self.user_two = self.db.get_user_by_username("seller-two")["id"]

    def tearDown(self):
        self.db.close()
        if self.previous_key_file is None:
            os.environ.pop("SYSTEM_SECRET_KEY_FILE", None)
        else:
            os.environ["SYSTEM_SECRET_KEY_FILE"] = self.previous_key_file
        self.tempdir.cleanup()

    def _seed_card(self, user_id):
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO cards (name, type, text_content, enabled, user_id)"
            " VALUES ('卡', 'text', '内容', 1, ?)",
            (user_id,),
        )
        return cursor.lastrowid

    def _seed_rule(self, card_id, user_id):
        cursor = self.db.conn.cursor()
        cursor.execute(
            "INSERT INTO delivery_rules (keyword, card_id, enabled, user_id)"
            " VALUES ('kw', ?, 1, ?)",
            (card_id, user_id),
        )
        return cursor.lastrowid

    def _rule_owner(self, rule_id):
        row = self.db.conn.execute(
            "SELECT user_id FROM delivery_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return row[0]

    def _apply(self):
        migration = _find_migration("2026072608")
        self.assertIsNotNone(migration, "迁移 2026072608 未注册")
        with self.db.lock:
            cursor = self.db.conn.cursor()
            migration.apply(cursor, str(self.root / "tenant.db"))
            self.db.conn.commit()

    def test_migration_registered_with_expected_name(self):
        migration = _find_migration("2026072608")
        self.assertIsNotNone(migration)
        self.assertEqual(migration.name, "tenant_isolation_hardening_v1")

    def test_fresh_database_records_migration(self):
        row = self.db.conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '2026072608'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_repairs_rule_ownership_to_card_owner(self):
        with self.db.lock:
            card_one = self._seed_card(self.user_one)
            # 归属错乱：user_two 的规则绑了 user_one 的卡券
            mismatched = self._seed_rule(card_one, self.user_two)
            # 历史 NULL 归属：绑了 user_one 的卡券
            orphan_owner = self._seed_rule(card_one, None)
            self.db.conn.commit()
        self._apply()
        self.assertEqual(self._rule_owner(mismatched), self.user_one)
        self.assertEqual(self._rule_owner(orphan_owner), self.user_one)

    def test_keeps_consistent_and_dangling_rules_untouched(self):
        with self.db.lock:
            card_two = self._seed_card(self.user_two)
            consistent = self._seed_rule(card_two, self.user_two)
            # 卡券已被删除的悬空规则：无法推断归属，保持原状
            self.db.conn.commit()
            self.db.conn.execute("PRAGMA foreign_keys = OFF")
            dangling = self._seed_rule(999999, self.user_one)
            self.db.conn.commit()
            self.db.conn.execute("PRAGMA foreign_keys = ON")
        self._apply()
        self.assertEqual(self._rule_owner(consistent), self.user_two)
        self.assertEqual(self._rule_owner(dangling), self.user_one)

    def test_creates_tenant_indexes(self):
        self._apply()
        names = {
            row[0]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        self.assertIn("idx_cards_user", names)
        self.assertIn("idx_delivery_rules_user", names)
        self.assertIn("idx_cookies_user", names)

    def test_idempotent_reapply(self):
        with self.db.lock:
            card_one = self._seed_card(self.user_one)
            mismatched = self._seed_rule(card_one, self.user_two)
            self.db.conn.commit()
        self._apply()
        self._apply()
        self.assertEqual(self._rule_owner(mismatched), self.user_one)


if __name__ == "__main__":
    unittest.main()
