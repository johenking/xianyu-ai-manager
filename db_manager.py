import sqlite3
import os
import threading
import hashlib
import math
import time
import json
import random
import string
import asyncio
import io
import base64
import binascii
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Dict, Optional, Any, Sequence, Iterable
from zoneinfo import ZoneInfo
from loguru import logger

from schema_migrations import MigrationRunner, get_schema_version
from security_utils import (
    ACCOUNT_PASSWORD_ENCRYPTION_VERSION,
    PASSWORD_HASH_VERSION,
    SYSTEM_SECRET_ENCRYPTION_VERSION,
    SYSTEM_SECRET_PREFIX,
    AccountCredentialCipher,
    SystemSecretCipher,
    hash_user_password,
    token_digest,
)
from repositories.auth_repository import (
    AuthSessionRepository,
    UserRepository,
    public_user_view,
)
from services.auth_service import AuthService
from auth_registration_service import (
    AuthRateLimiter,
    RegistrationService,
    mask_email_for_log,
)
from auth_email_service import (
    SMTP_CONFIGURATION_KEYS,
    SMTPConfigurationError,
    SMTPDeliveryError,
    SMTPEmailSender,
    canonical_smtp_setting_value,
    smtp_configuration_status,
)
from account_session_refresh import (
    normalize_login_method,
    supports_automatic_refresh,
)
from client_browser_login import (
    RENEWAL_TASK_TTL_SECONDS,
    ClientBrowserError,
    normalize_browser_family,
    normalize_client_type,
    normalize_device_id,
    normalize_public_jwk,
    seal_renewal_credential,
)
from utils.image_utils import image_manager

COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES = 1440
COOKIE_REFRESH_MIN_INTERVAL_MINUTES = 60
COOKIE_REFRESH_MAX_INTERVAL_MINUTES = 10080
SKILL_MONITOR_RUN_LEASE_SECONDS = 180
SKILL_MONITOR_DELIVERY_LEASE_SECONDS = 60
SKILL_MONITOR_RETENTION_SECONDS = 30 * 24 * 60 * 60
ITEM_METRIC_MAX_FUTURE_SKEW_SECONDS = 5 * 60
ITEM_METRIC_RECOMMENDATION_MIN_INTERVAL_SECONDS = 2 * 60 * 60
ITEM_METRIC_RECOMMENDATION_MAX_INTERVAL_SECONDS = 6 * 60 * 60
FULFILLMENT_ATTEMPT_LEASE_SECONDS = 6 * 60 * 60
FULFILLMENT_MAX_QUANTITY = 100
FULFILLMENT_API_PROTOCOL = "fulfillment_api_v1"
CARD_STOCK_IMPORT_MAX_ITEMS = 10_000
CARD_STOCK_ITEM_MAX_BYTES = 2_048
BACKUP_MAX_SERIALIZED_BYTES = 128 * 1024 * 1024
BACKUP_MAX_TOTAL_ROWS = 1_000_000
BACKUP_MAX_TABLES = 32

# AI 账号级配置回落到系统全局配置时使用的默认值。历史版本会把通义千问默认值写进
# 账号级配置，这些旧默认值不应覆盖当前的全局配置。
AI_DEFAULT_BASE_URL = 'https://api.deepseek.com'
AI_DEFAULT_MODEL = 'deepseek-v4-flash'
AI_LEGACY_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
AI_LEGACY_MODEL = 'qwen-plus'


def mask_secret_preview(value: str) -> str:
    """返回可展示的密钥预览，不暴露存量值。"""
    value = str(value or '')
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def resolve_ai_model_and_base_url(account_model: str, account_base_url: str,
                                  system_model: str, system_base_url: str
                                  ) -> Tuple[str, str]:
    """账号级值为空或仍是历史硬编码默认值时，回落到系统全局配置。"""
    use_model = account_model if (
        account_model and account_model not in {AI_DEFAULT_MODEL, AI_LEGACY_MODEL}
    ) else system_model
    use_base_url = account_base_url if (
        account_base_url and account_base_url not in {AI_DEFAULT_BASE_URL, AI_LEGACY_BASE_URL}
    ) else system_base_url
    return use_model, use_base_url


USER_BACKUP_TABLES = (
    "cookies",
    "keywords",
    "cookie_status",
    "default_replies",
    "message_notifications",
    "item_info",
    "ai_reply_settings",
    "ai_conversations",
    "ai_training_rules",
    "ai_item_knowledge_profiles",
    "ai_item_knowledge_versions",
    "item_metric_snapshots",
    "item_metric_collection_states",
    "order_auto_ratings",
)
SYSTEM_BACKUP_TABLES = (
    "cookies",
    "keywords",
    "cookie_status",
    "cards",
    "delivery_rules",
    "default_replies",
    "notification_channels",
    "message_notifications",
    "system_settings",
    "item_info",
    "ai_reply_settings",
    "ai_conversations",
    "ai_item_cache",
    "ai_training_rules",
    "ai_item_knowledge_profiles",
    "ai_item_knowledge_versions",
    "item_metric_snapshots",
    "item_metric_collection_states",
    "fulfillment_attempts",
    "fulfillment_card_reservations",
    "fulfillment_api_operations",
    "fulfillment_delivery_payloads",
    "fulfillment_resend_events",
    "invite_bridge_operations",
    "order_auto_ratings",
)
BACKUP_INSERT_ORDER = (
    "cookies",
    "cards",
    "notification_channels",
    "system_settings",
    "keywords",
    "cookie_status",
    "default_replies",
    "item_info",
    "ai_reply_settings",
    "ai_conversations",
    "ai_item_cache",
    "ai_training_rules",
    "ai_item_knowledge_profiles",
    "ai_item_knowledge_versions",
    "item_metric_snapshots",
    "item_metric_collection_states",
    "fulfillment_attempts",
    "fulfillment_card_reservations",
    "fulfillment_api_operations",
    "fulfillment_delivery_payloads",
    "fulfillment_resend_events",
    "invite_bridge_operations",
    "order_auto_ratings",
    "delivery_rules",
    "message_notifications",
)
BACKUP_AUTO_ID_TABLES = {
    "ai_conversations",
    "ai_training_rules",
    "ai_item_knowledge_versions",
    "item_info",
    "item_metric_snapshots",
    "order_auto_ratings",
    "fulfillment_attempts",
    "fulfillment_card_reservations",
    "fulfillment_api_operations",
    "fulfillment_delivery_payloads",
    "fulfillment_resend_events",
    "message_notifications",
}
BACKUP_IMAGE_REFERENCE_COLUMNS = {
    "keywords": ("image_url",),
    "default_replies": ("reply_image_url",),
    "cards": ("image_url",),
}


def _account_log_reference(account_id: str) -> str:
    value = str(account_id or "")
    if value.startswith("account_") and len(value) == 18:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"account_{digest}"


class AccountIdentityMismatchError(ValueError):
    code = "account_identity_mismatch"

    def __init__(self) -> None:
        super().__init__("Cookie 中的闲鱼账号身份与当前账号不一致")


class OrderQueryError(RuntimeError):
    """订单读路径失败；调用方必须与“空结果/未找到”区分处理。"""


_ORDER_QUERY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _order_query_date_bound(
    value: str,
    *,
    next_day: bool,
) -> Tuple[float, str]:
    """把上海本地日历边界转换为 UTC epoch 与旧 created_at 文本边界。"""
    parsed = datetime.strptime(str(value), "%Y-%m-%d")
    if next_day:
        parsed += timedelta(days=1)
    local_boundary = parsed.replace(tzinfo=_ORDER_QUERY_TIMEZONE)
    utc_boundary = local_boundary.astimezone(timezone.utc)
    return utc_boundary.timestamp(), utc_boundary.strftime("%Y-%m-%d %H:%M:%S")


SKILL_MONITOR_BUDGET_RETENTION_SECONDS = 24 * 60 * 60
SKILL_MONITOR_MTOP_BREAKER_RETENTION_SECONDS = 30 * 24 * 60 * 60


class DBManager:
    """SQLite数据库管理，持久化存储Cookie和关键字"""

    # 管理端只读导出（/admin/data）必须剔除的敏感列：明文平台凭据、账号密码、
    # AI Key、买家 PII、验证码与会话 token。口径与 /cookie/{id}/details 主动剥离一致，
    # 避免 /admin/data 成为绕过“凭据不进 API”边界的后门。
    SENSITIVE_EXPORT_COLUMNS = {
        "cookies": {"value", "xianyu_unb", "password", "password_encrypted"},
        "ai_reply_settings": {"api_key"},
        "ai_provider_profiles": {"api_key", "api_key_encrypted"},
        "orders": {
            "receiver_name",
            "receiver_phone",
            "receiver_address",
            "receiver_city",
        },
        "users": {"password_hash"},
        "email_verifications": {"code"},
        "captcha_codes": {"code"},
        "auth_sessions": {"token"},
    }

    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise RuntimeError("SQLite 外键约束未启用")
        return connection

    def __init__(self, db_path: str = None):
        """初始化数据库连接和表结构"""
        # 支持环境变量配置数据库路径
        if db_path is None:
            db_path = os.getenv('DB_PATH', 'data/xianyu_data.db')

        # 确保数据目录存在并有正确权限
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, mode=0o755, exist_ok=True)
                logger.info(f"创建数据目录: {db_dir}")
            except PermissionError as e:
                logger.error(f"创建数据目录失败，权限不足: {e}")
                # 尝试使用当前目录
                db_path = os.path.basename(db_path)
                logger.warning(f"使用当前目录作为数据库路径: {db_path}")
            except Exception as e:
                logger.error(f"创建数据目录失败: {e}")
                raise

        # 检查目录权限
        if db_dir and os.path.exists(db_dir):
            if not os.access(db_dir, os.W_OK):
                logger.error(f"数据目录没有写权限: {db_dir}")
                # 尝试使用当前目录
                db_path = os.path.basename(db_path)
                logger.warning(f"使用当前目录作为数据库路径: {db_path}")

        self.db_path = db_path
        self._database_preexisting = os.path.exists(db_path) and os.path.getsize(db_path) > 0
        logger.info(f"数据库路径: {self.db_path}")
        self.conn = None
        self.lock = threading.RLock()  # 使用可重入锁保护数据库操作
        self._fulfillment_owner_token = secrets.token_urlsafe(24)

        # SQL日志配置 - 默认启用
        self.sql_log_enabled = True  # 默认启用SQL日志
        self.sql_log_level = 'DEBUG'  # SQL明细默认只在DEBUG级别记录

        # 允许通过环境变量覆盖默认设置
        if os.getenv('SQL_LOG_ENABLED'):
            self.sql_log_enabled = os.getenv('SQL_LOG_ENABLED', 'true').lower() == 'true'
        if os.getenv('SQL_LOG_LEVEL'):
            self.sql_log_level = os.getenv('SQL_LOG_LEVEL', 'DEBUG').upper()

        sql_log_state = '已启用' if self.sql_log_enabled else '已禁用'
        logger.info(f"SQL日志{sql_log_state}，日志级别: {self.sql_log_level}")

        self.init_db()

    def init_db(self):
        """初始化数据库表结构"""
        try:
            self.conn = self._connect(self.db_path)
            cursor = self.conn.cursor()

            # 创建用户表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建邮箱验证码表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建图形验证码表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS captcha_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建后台登录会话表，用于服务重启后保持登录状态
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            ''')

            # 创建cookies表（添加user_id字段和auto_confirm字段）
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cookies (
                id TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                xianyu_unb TEXT,
                auto_confirm INTEGER DEFAULT 1,
                remark TEXT DEFAULT '',
                pause_duration INTEGER DEFAULT 10,
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                show_browser INTEGER DEFAULT 0,
                cookie_refresh_enabled INTEGER DEFAULT 0,
                cookie_refresh_interval_minutes INTEGER DEFAULT 1440,
                browser_user_agent TEXT NOT NULL DEFAULT '',
                cookie_revision INTEGER NOT NULL DEFAULT 0,
                login_method TEXT NOT NULL DEFAULT 'unknown',
                avatar_url TEXT NOT NULL DEFAULT '',
                xianyu_nick TEXT NOT NULL DEFAULT '',
                last_login_at REAL,
                last_validated_at REAL,
                last_expired_at REAL,
                has_l3_memory INTEGER NOT NULL DEFAULT 0,
                l3_memory_at REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            ''')


            # 创建keywords表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                cookie_id TEXT,
                keyword TEXT,
                reply TEXT,
                item_id TEXT,
                type TEXT DEFAULT 'text',
                image_url TEXT,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建cookie_status表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cookie_status (
                cookie_id TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                receiver_city TEXT DEFAULT '',
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建AI回复配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_reply_settings (
                cookie_id TEXT PRIMARY KEY,
                ai_enabled BOOLEAN DEFAULT FALSE,
                provider_profile_id INTEGER,
                model_name TEXT DEFAULT 'qwen-plus',
                api_key TEXT,
                base_url TEXT DEFAULT 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                max_discount_percent INTEGER DEFAULT 10,
                max_discount_amount INTEGER DEFAULT 100,
                max_bargain_rounds INTEGER DEFAULT 3,
                custom_prompts TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_provider_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL CHECK(provider_type IN ('openai_compatible', 'gemini')),
                preset TEXT NOT NULL DEFAULT 'custom',
                base_url TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL DEFAULT '',
                default_model TEXT NOT NULL DEFAULT '',
                models_cache TEXT NOT NULL DEFAULT '[]',
                models_cached_at REAL,
                verification_status TEXT NOT NULL DEFAULT 'unverified',
                verification_message TEXT NOT NULL DEFAULT '',
                last_verified_at REAL,
                is_default BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, name)
            )
            ''')
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_provider_profiles_user
            ON ai_provider_profiles(user_id, is_default)
            ''')
            cursor.execute("PRAGMA table_info(ai_reply_settings)")
            ai_settings_columns = {row[1] for row in cursor.fetchall()}
            if 'provider_profile_id' not in ai_settings_columns:
                cursor.execute("ALTER TABLE ai_reply_settings ADD COLUMN provider_profile_id INTEGER")

            # 创建AI对话历史表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                bargain_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies (id) ON DELETE CASCADE
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_training_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL CHECK(scope IN ('global', 'item')),
                rule_text TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies (id) ON DELETE CASCADE,
                UNIQUE(cookie_id, item_id, rule_text)
            )
            ''')
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_training_rules_lookup
            ON ai_training_rules(cookie_id, item_id, enabled)
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_item_knowledge_profiles (
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                draft_json TEXT NOT NULL DEFAULT '{}',
                published_json TEXT NOT NULL DEFAULT '{}',
                source_detail_hash TEXT DEFAULT '',
                published_version INTEGER DEFAULT 0,
                draft_updated_at TIMESTAMP,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (cookie_id, item_id),
                FOREIGN KEY (cookie_id) REFERENCES cookies (id) ON DELETE CASCADE
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_item_knowledge_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                source_detail_hash TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cookie_id, item_id, version),
                FOREIGN KEY (cookie_id) REFERENCES cookies (id) ON DELETE CASCADE
            )
            ''')
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_item_knowledge_versions
            ON ai_item_knowledge_versions(cookie_id, item_id, version DESC)
            ''')

            # 创建AI商品信息缓存表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_item_cache (
                item_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                price REAL,
                description TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建技能中心 - 监控任务表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS skill_monitor_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                keyword TEXT NOT NULL,
                min_price REAL,
                max_price REAL,
                region TEXT DEFAULT '',
                published_within_hours INTEGER DEFAULT 24,
                ai_filter TEXT DEFAULT '',
                notify_enabled BOOLEAN DEFAULT FALSE,
                account_id TEXT DEFAULT '',
                enabled BOOLEAN DEFAULT TRUE,
                schedule_enabled BOOLEAN DEFAULT FALSE,
                schedule_interval_minutes INTEGER DEFAULT 60,
                next_run_at TIMESTAMP,
                last_status TEXT DEFAULT 'idle',
                last_error TEXT DEFAULT '',
                last_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            ''')

            # 创建技能中心 - 监控结果表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS skill_monitor_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                price REAL,
                region TEXT DEFAULT '',
                item_url TEXT DEFAULT '',
                item_image TEXT DEFAULT '',
                seller_name TEXT DEFAULT '',
                ai_score INTEGER DEFAULT 0,
                ai_reason TEXT DEFAULT '',
                notify_status TEXT DEFAULT 'pending',
                raw_data TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES skill_monitor_tasks (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            ''')

            # 创建技能中心 - AI专家提示词表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS skill_agent_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                prompt_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, prompt_type),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            ''')

            # 创建技能中心 - 运行日志表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS skill_run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                level TEXT DEFAULT 'info',
                message TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            ''')

            # 创建卡券表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('api', 'text', 'data', 'image')),
                api_config TEXT,
                text_content TEXT,
                data_content TEXT,
                image_url TEXT,
                description TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                delay_seconds INTEGER DEFAULT 0,
                is_multi_spec BOOLEAN DEFAULT FALSE,
                spec_name TEXT,
                spec_value TEXT,
                user_id INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            ''')

            # 创建订单表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                item_id TEXT,
                buyer_id TEXT,
                spec_name TEXT,
                spec_value TEXT,
                quantity TEXT,
                amount TEXT,
                order_status TEXT DEFAULT 'unknown',
                cookie_id TEXT,
                is_bargain INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            cursor.execute("PRAGMA table_info(orders)")
            order_columns = {row[1] for row in cursor.fetchall()}
            order_sync_columns = {
                'platform_status_code': "TEXT DEFAULT ''",
                'platform_status_text': "TEXT DEFAULT ''",
                'status_source': "TEXT DEFAULT ''",
                'status_synced_at': "TIMESTAMP",
                'last_sync_error': "TEXT DEFAULT ''",
            }
            # 订单身份快照列（item_image / item_title / buyer_* / ordered_at_utc 等）
            # 只走版本化迁移 2026072601/2026072602，不再进入本即席 ALTER 轨道。
            for column_name, column_sql in order_sync_columns.items():
                if column_name not in order_columns:
                    cursor.execute(f"ALTER TABLE orders ADD COLUMN {column_name} {column_sql}")

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_status_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                order_id TEXT DEFAULT '',
                item_id TEXT DEFAULT '',
                buyer_id TEXT DEFAULT '',
                chat_id TEXT DEFAULT '',
                normalized_status TEXT NOT NULL,
                raw_status TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'system_message',
                occurred_at REAL NOT NULL,
                match_state TEXT NOT NULL DEFAULT 'pending',
                matched_order_id TEXT DEFAULT '',
                matched_at REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_order_status_events_pending
            ON order_status_events(cookie_id, match_state, occurred_at)
            ''')

            # 检查并添加 is_bargain 列（用于标记小刀订单）
            try:
                self._execute_sql(cursor, "SELECT is_bargain FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                # is_bargain 列不存在，需要添加
                logger.info("正在为 orders 表添加 is_bargain 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN is_bargain INTEGER DEFAULT 0")
                logger.info("orders 表 is_bargain 列添加完成")

            # 检查并添加收货人信息列
            try:
                self._execute_sql(cursor, "SELECT receiver_name FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                # receiver_name 列不存在，需要添加
                logger.info("正在为 orders 表添加收货人信息列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_name TEXT DEFAULT ''")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_phone TEXT DEFAULT ''")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_address TEXT DEFAULT ''")
                logger.info("orders 表收货人信息列添加完成")

            # 检查并添加收货城市列（订单分析接口会使用）
            try:
                self._execute_sql(cursor, "SELECT receiver_city FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("正在为 orders 表添加 receiver_city 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_city TEXT DEFAULT ''")
                logger.info("orders 表 receiver_city 列添加完成")

            # 检查并添加 version 列（用于乐观锁）
            try:
                self._execute_sql(cursor, "SELECT version FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                # version 列不存在，需要添加
                logger.info("正在为 orders 表添加 version 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN version INTEGER DEFAULT 1")
                logger.info("orders 表 version 列添加完成")

            # 检查并添加 chat_id 列到 orders 表（用于手动发货时发送消息）
            try:
                self._execute_sql(cursor, "SELECT chat_id FROM orders LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("正在为 orders 表添加 chat_id 列...")
                self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN chat_id TEXT DEFAULT ''")
                logger.info("orders 表 chat_id 列添加完成")

            # 检查并添加 user_id 列（用于数据库迁移）
            try:
                self._execute_sql(cursor, "SELECT user_id FROM cards LIMIT 1")
            except sqlite3.OperationalError:
                # user_id 列不存在，需要添加
                logger.info("正在为 cards 表添加 user_id 列...")
                self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
                self._execute_sql(cursor, "CREATE INDEX IF NOT EXISTS idx_cards_user_id ON cards(user_id)")
                logger.info("cards 表 user_id 列添加完成")

            # 检查并添加 delay_seconds 列（用于自动发货延时功能）
            try:
                self._execute_sql(cursor, "SELECT delay_seconds FROM cards LIMIT 1")
            except sqlite3.OperationalError:
                # delay_seconds 列不存在，需要添加
                logger.info("正在为 cards 表添加 delay_seconds 列...")
                self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN delay_seconds INTEGER DEFAULT 0")
                logger.info("cards 表 delay_seconds 列添加完成")

            # 检查并添加 item_id 列（用于自动回复商品ID功能）
            try:
                self._execute_sql(cursor, "SELECT item_id FROM keywords LIMIT 1")
            except sqlite3.OperationalError:
                # item_id 列不存在，需要添加
                logger.info("正在为 keywords 表添加 item_id 列...")
                self._execute_sql(cursor, "ALTER TABLE keywords ADD COLUMN item_id TEXT")
                logger.info("keywords 表 item_id 列添加完成")

            # 创建商品信息表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS item_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_title TEXT,
                item_description TEXT,
                item_category TEXT,
                item_price TEXT,
                item_detail TEXT,
                item_image TEXT NOT NULL DEFAULT '',
                platform_item_status INTEGER,
                catalog_active BOOLEAN NOT NULL DEFAULT FALSE,
                catalog_last_seen_at TIMESTAMP,
                catalog_metadata TEXT NOT NULL DEFAULT '{}',
                invite_auto_fulfillment BOOLEAN NOT NULL DEFAULT FALSE,
                is_multi_spec BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE,
                UNIQUE(cookie_id, item_id)
            )
            ''')

            # 检查并添加 multi_quantity_delivery 列（用于多数量发货功能）
            try:
                self._execute_sql(cursor, "SELECT multi_quantity_delivery FROM item_info LIMIT 1")
            except sqlite3.OperationalError:
                # multi_quantity_delivery 列不存在，需要添加
                logger.info("正在为 item_info 表添加 multi_quantity_delivery 列...")
                self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN multi_quantity_delivery BOOLEAN DEFAULT FALSE")
                logger.info("item_info 表 multi_quantity_delivery 列添加完成")

            # 创建自动发货规则表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS delivery_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                card_id INTEGER NOT NULL,
                delivery_count INTEGER DEFAULT 1,
                enabled BOOLEAN DEFAULT TRUE,
                description TEXT,
                delivery_times INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
            )
            ''')

            # 创建默认回复表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS default_replies (
                cookie_id TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                reply_content TEXT,
                reply_image_url TEXT,
                reply_once BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 添加 reply_once 字段（如果不存在）
            try:
                cursor.execute('ALTER TABLE default_replies ADD COLUMN reply_once BOOLEAN DEFAULT FALSE')
                self.conn.commit()
                logger.info("已添加 reply_once 字段到 default_replies 表")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加 reply_once 字段失败: {e}")

            # 添加 reply_image_url 字段（如果不存在）
            try:
                cursor.execute('ALTER TABLE default_replies ADD COLUMN reply_image_url TEXT')
                self.conn.commit()
                logger.info("已添加 reply_image_url 字段到 default_replies 表")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加 reply_image_url 字段失败: {e}")

            # 创建指定商品回复表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS item_replay (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    cookie_id TEXT NOT NULL,
                    reply_content TEXT NOT NULL ,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 创建默认回复记录表（记录已回复的chat_id）
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS default_reply_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cookie_id, chat_id),
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 创建通知渠道表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk','dingtalk','feishu','lark','bark','email','webhook','wechat','telegram')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建系统设置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 创建消息通知配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE,
                FOREIGN KEY (channel_id) REFERENCES notification_channels(id) ON DELETE CASCADE,
                UNIQUE(cookie_id, channel_id)
            )
            ''')

            # 创建用户设置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, key)
            )
            ''')

            # 创建风控日志表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_control_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_id TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'slider_captcha',
                event_description TEXT,
                processing_result TEXT,
                processing_status TEXT DEFAULT 'processing',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_session_refresh_status (
                cookie_id TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'idle',
                trigger TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                verification_image_path TEXT NOT NULL DEFAULT '',
                started_at REAL,
                last_attempt_at REAL,
                last_success_at REAL,
                expires_at REAL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 插入默认系统设置（不包括管理员密码，由reply_server.py初始化）
            cursor.execute('''
            INSERT OR IGNORE INTO system_settings (key, value, description) VALUES
            ('theme_color', 'blue', '主题颜色'),
            ('registration_enabled', 'false', '是否开启用户注册'),
            ('show_default_login_info', 'true', '是否显示默认登录信息'),
            ('login_captcha_enabled', 'true', '登录滑动验证码开关'),
            ('smtp_server', '', 'SMTP服务器地址'),
            ('smtp_port', '587', 'SMTP端口'),
            ('smtp_user', '', 'SMTP登录用户名（发件邮箱）'),
            ('smtp_password', '', 'SMTP登录密码/授权码'),
            ('smtp_from', '', '发件人显示名（留空则使用用户名）'),
            ('smtp_use_tls', 'true', '是否启用TLS'),
            ('smtp_use_ssl', 'false', '是否启用SSL'),
            ('terms_version', 'v2', '当前注册条款版本'),
            ('registration_user_limit', '20', '非管理员注册用户上限'),
            ('support_email', '', '公开支持邮箱'),
            ('smtp_verified_fingerprint', '', '已验证SMTP配置指纹'),
            ('smtp_verified_at', '', 'SMTP配置验证时间'),
            ('auth_trusted_proxies', '', '认证可信代理列表'),
            ('qq_reply_secret_key', '', 'QQ回复消息API秘钥'),
            ('qq_reply_secret_user_id', '', 'QQ回复消息API秘钥绑定的用户ID（留空则该秘钥通道关闭）'),
            ('item_sync_enabled', 'true', '是否启用定时自动同步商品'),
            ('item_sync_interval', '600', '商品同步间隔时间（秒）'),
            ('item_sync_max_pages', '5', '每次最多同步的页数')
            ''')

            # 检查并升级数据库
            self.check_and_upgrade_db(cursor)

            # 执行数据库迁移
            self._migrate_database(cursor)

            self.conn.commit()
            migration_runner = MigrationRunner(
                self.conn,
                self.db_path,
                backup_enabled=self._database_preexisting,
            )
            applied_migrations = migration_runner.run()
            self.schema_version = get_schema_version(self.conn)
            if applied_migrations:
                logger.info(f"数据库迁移完成: {', '.join(applied_migrations)}")
            self.backfill_cookie_identities()
            self.user_repository = UserRepository(self.conn)
            self.auth_session_repository = AuthSessionRepository(self.conn)
            self.auth_service = AuthService(
                self.user_repository,
                self.auth_session_repository,
                lock=self.lock,
            )
            self.registration_service = RegistrationService(
                self.conn,
                self.db_path,
                lock=self.lock,
            )
            self.auth_rate_limiter = AuthRateLimiter(
                self.conn,
                self.db_path,
                lock=self.lock,
            )
            logger.info("数据库初始化完成")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            self.conn.rollback()
            raise

    def _migrate_database(self, cursor):
        """执行数据库迁移"""
        try:
            # 检查cards表是否存在image_url列
            cursor.execute("PRAGMA table_info(cards)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'image_url' not in columns:
                logger.info("添加cards表的image_url列...")
                cursor.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
                logger.info("数据库迁移完成：添加image_url列")

            # 检查并更新CHECK约束（重建表以支持image类型）
            self._update_cards_table_constraints(cursor)

            # 检查cookies表是否存在remark列
            cursor.execute("PRAGMA table_info(cookies)")
            cookie_columns = [column[1] for column in cursor.fetchall()]

            if 'remark' not in cookie_columns:
                logger.info("添加cookies表的remark列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN remark TEXT DEFAULT ''")
                logger.info("数据库迁移完成：添加remark列")

            # 检查cookies表是否存在pause_duration列
            if 'pause_duration' not in cookie_columns:
                logger.info("添加cookies表的pause_duration列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN pause_duration INTEGER DEFAULT 10")
                logger.info("数据库迁移完成：添加pause_duration列")

            if 'xianyu_unb' not in cookie_columns:
                logger.info("添加cookies表的xianyu_unb列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN xianyu_unb TEXT")
                logger.info("数据库迁移完成：添加xianyu_unb列")

            if 'cookie_refresh_enabled' not in cookie_columns:
                logger.info("添加cookies表的cookie_refresh_enabled列...")
                cursor.execute("ALTER TABLE cookies ADD COLUMN cookie_refresh_enabled INTEGER DEFAULT 0")
                logger.info("数据库迁移完成：添加cookie_refresh_enabled列")

            if 'cookie_refresh_interval_minutes' not in cookie_columns:
                logger.info("添加cookies表的cookie_refresh_interval_minutes列...")
                cursor.execute(
                    "ALTER TABLE cookies ADD COLUMN cookie_refresh_interval_minutes INTEGER DEFAULT 1440"
                )
                logger.info("数据库迁移完成：添加cookie_refresh_interval_minutes列")

            if 'l3_keepalive_enabled' not in cookie_columns:
                logger.info("添加cookies表的l3_keepalive_enabled列...")
                cursor.execute(
                    "ALTER TABLE cookies ADD COLUMN l3_keepalive_enabled INTEGER DEFAULT 0"
                )
                logger.info("数据库迁移完成：添加l3_keepalive_enabled列")

            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_cookies_user_unb
                ON cookies(user_id, xianyu_unb)
                WHERE xianyu_unb IS NOT NULL AND xianyu_unb <> ''
            ''')

            # 确保商品同步配置存在
            cursor.execute("SELECT key FROM system_settings WHERE key IN ('item_sync_enabled', 'item_sync_interval', 'item_sync_max_pages')")
            existing_keys = [row[0] for row in cursor.fetchall()]

            if 'item_sync_enabled' not in existing_keys:
                logger.info("添加商品同步配置：item_sync_enabled...")
                cursor.execute("INSERT INTO system_settings (key, value, description) VALUES ('item_sync_enabled', 'true', '是否启用定时自动同步商品')")
            if 'item_sync_interval' not in existing_keys:
                logger.info("添加商品同步配置：item_sync_interval...")
                cursor.execute("INSERT INTO system_settings (key, value, description) VALUES ('item_sync_interval', '600', '商品同步间隔时间（秒）')")
            if 'item_sync_max_pages' not in existing_keys:
                logger.info("添加商品同步配置：item_sync_max_pages...")
                cursor.execute("INSERT INTO system_settings (key, value, description) VALUES ('item_sync_max_pages', '5', '每次最多同步的页数')")

        except Exception as e:
            logger.error(f"数据库迁移失败: {e}")
            # 迁移失败不应该阻止程序启动
            pass

    def _update_cards_table_constraints(self, cursor):
        """更新cards表的CHECK约束以支持image类型"""
        try:
            # 尝试插入一个测试的image类型记录来检查约束
            cursor.execute('''
                INSERT INTO cards (name, type, user_id)
                VALUES ('__test_image_constraint__', 'image', 1)
            ''')
            # 如果插入成功，立即删除测试记录
            cursor.execute("DELETE FROM cards WHERE name = '__test_image_constraint__'")
            logger.info("cards表约束检查通过，支持image类型")
        except Exception as e:
            if "CHECK constraint failed" in str(e) or "constraint" in str(e).lower():
                logger.info("检测到旧的CHECK约束，开始更新cards表...")

                # 重建表以更新约束
                try:
                    # 1. 创建新表
                    cursor.execute('''
                    CREATE TABLE IF NOT EXISTS cards_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        type TEXT NOT NULL CHECK (type IN ('api', 'text', 'data', 'image')),
                        api_config TEXT,
                        text_content TEXT,
                        data_content TEXT,
                        image_url TEXT,
                        description TEXT,
                        enabled BOOLEAN DEFAULT TRUE,
                        delay_seconds INTEGER DEFAULT 0,
                        is_multi_spec BOOLEAN DEFAULT FALSE,
                        spec_name TEXT,
                        spec_value TEXT,
                        user_id INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                    ''')

                    # 2. 复制数据
                    cursor.execute('''
                    INSERT INTO cards_new (id, name, type, api_config, text_content, data_content, image_url,
                                          description, enabled, delay_seconds, is_multi_spec, spec_name, spec_value,
                                          user_id, created_at, updated_at)
                    SELECT id, name, type, api_config, text_content, data_content, image_url,
                           description, enabled, delay_seconds, is_multi_spec, spec_name, spec_value,
                           user_id, created_at, updated_at
                    FROM cards
                    ''')

                    # 3. 删除旧表
                    cursor.execute("DROP TABLE cards")

                    # 4. 重命名新表
                    cursor.execute("ALTER TABLE cards_new RENAME TO cards")

                    logger.info("cards表约束更新完成，现在支持image类型")

                except Exception as rebuild_error:
                    logger.error(f"重建cards表失败: {rebuild_error}")
                    # 如果重建失败，尝试回滚
                    try:
                        cursor.execute("DROP TABLE IF EXISTS cards_new")
                    except:
                        pass
            else:
                logger.error(f"检查cards表约束时出现未知错误: {e}")

    def check_and_upgrade_db(self, cursor):
        """检查数据库版本并执行必要的升级"""
        try:
            # 获取当前数据库版本
            current_version = self.get_system_setting("db_version") or "1.0"
            logger.info(f"当前数据库版本: {current_version}")

            if current_version == "1.0":
                logger.info("开始升级数据库到版本1.0...")
                self.update_admin_user_id(cursor)
                self.set_system_setting("db_version", "1.0", "数据库版本号")
                logger.info("数据库升级到版本1.0完成")

            # 如果版本低于需要升级的版本，执行升级
            if current_version < "1.1":
                logger.info("开始升级数据库到版本1.1...")
                self.upgrade_notification_channels_table(cursor)
                self.set_system_setting("db_version", "1.1", "数据库版本号")
                logger.info("数据库升级到版本1.1完成")

            # 升级到版本1.2 - 支持更多通知渠道类型
            if current_version < "1.2":
                logger.info("开始升级数据库到版本1.2...")
                self.upgrade_notification_channels_types(cursor)
                self.set_system_setting("db_version", "1.2", "数据库版本号")
                logger.info("数据库升级到版本1.2完成")

            # 升级到版本1.3 - 添加关键词类型和图片URL字段
            if current_version < "1.3":
                logger.info("开始升级数据库到版本1.3...")
                self.upgrade_keywords_table_for_image_support(cursor)
                self.set_system_setting("db_version", "1.3", "数据库版本号")
                logger.info("数据库升级到版本1.3完成")


            # 升级到版本1.4 - 添加关键词类型和图片URL字段
            if current_version < "1.4":
                logger.info("开始升级数据库到版本1.4...")
                self.upgrade_notification_channels_types(cursor)
                self.set_system_setting("db_version", "1.4", "数据库版本号")
                logger.info("数据库升级到版本1.4完成")

            # 升级到版本1.5 - 为cookies表添加账号登录字段
            if current_version < "1.5":
                logger.info("开始升级数据库到版本1.5...")
                self.upgrade_cookies_table_for_account_login(cursor)
                self.set_system_setting("db_version", "1.5", "数据库版本号")
                logger.info("数据库升级到版本1.5完成")

            if current_version < "1.6":
                logger.info("开始升级数据库到版本1.6...")
                self.upgrade_skill_monitor_tasks_for_scheduler(cursor)
                self.set_system_setting("db_version", "1.6", "数据库版本号")
                logger.info("数据库升级到版本1.6完成")

            # 迁移遗留数据（在所有版本升级完成后执行）
            self.upgrade_skill_monitor_tasks_for_scheduler(cursor)
            self.migrate_legacy_data(cursor)

        except Exception as e:
            logger.error(f"数据库版本检查或升级失败: {e}")
            raise

    def upgrade_skill_monitor_tasks_for_scheduler(self, cursor):
        """为技能中心监控任务补齐调度和运行状态字段。"""
        columns = {
            'schedule_enabled': "ALTER TABLE skill_monitor_tasks ADD COLUMN schedule_enabled BOOLEAN DEFAULT FALSE",
            'schedule_interval_minutes': "ALTER TABLE skill_monitor_tasks ADD COLUMN schedule_interval_minutes INTEGER DEFAULT 60",
            'next_run_at': "ALTER TABLE skill_monitor_tasks ADD COLUMN next_run_at TIMESTAMP",
            'last_status': "ALTER TABLE skill_monitor_tasks ADD COLUMN last_status TEXT DEFAULT 'idle'",
            'last_error': "ALTER TABLE skill_monitor_tasks ADD COLUMN last_error TEXT DEFAULT ''",
        }
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='skill_monitor_tasks'")
            if not cursor.fetchone():
                return
            cursor.execute("PRAGMA table_info(skill_monitor_tasks)")
            existing = {row[1] for row in cursor.fetchall()}
            for column, sql in columns.items():
                if column not in existing:
                    self._execute_sql(cursor, sql)
                    logger.info(f"为skill_monitor_tasks添加字段: {column}")
            self._execute_sql(
                cursor,
                "UPDATE skill_monitor_tasks SET schedule_interval_minutes = 60 "
                "WHERE schedule_interval_minutes IS NULL OR schedule_interval_minutes < 15"
            )
            self._execute_sql(
                cursor,
                "UPDATE skill_monitor_tasks SET last_status = 'idle' "
                "WHERE last_status IS NULL OR last_status = ''"
            )
            self._execute_sql(
                cursor,
                "UPDATE skill_monitor_tasks SET last_error = '' WHERE last_error IS NULL"
            )
        except Exception as e:
            logger.error(f"升级skill_monitor_tasks调度字段失败: {e}")
            raise

    def update_admin_user_id(self, cursor):
        """更新admin用户ID"""
        try:
            logger.info("开始更新admin用户ID...")
            # 创建默认admin用户（只在首次初始化时创建）
            cursor.execute('SELECT COUNT(*) FROM users WHERE username = ?', ('admin',))
            admin_exists = cursor.fetchone()[0] > 0

            if not admin_exists:
                # 首次创建admin用户，设置默认密码
                default_admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
                default_password_hash = hashlib.sha256(default_admin_password.encode()).hexdigest()
                cursor.execute('''
                INSERT INTO users (username, email, password_hash) VALUES
                ('admin', 'admin@localhost', ?)
                ''', (default_password_hash,))
                logger.info("创建默认admin用户")

            # 获取admin用户ID，用于历史数据绑定
            self._execute_sql(cursor, "SELECT id FROM users WHERE username = 'admin'")
            admin_user = cursor.fetchone()
            if admin_user:
                admin_user_id = admin_user[0]

                # 将历史cookies数据绑定到admin用户（如果user_id列不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM cookies LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE cookies SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE cookies SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为cookies表添加auto_confirm字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT auto_confirm FROM cookies LIMIT 1")
                except sqlite3.OperationalError:
                    # auto_confirm列不存在，需要添加并设置默认值
                    self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN auto_confirm INTEGER DEFAULT 1")
                    self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = 1 WHERE auto_confirm IS NULL")
                else:
                    # auto_confirm列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = 1 WHERE auto_confirm IS NULL")

                # 为delivery_rules表添加user_id字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM delivery_rules LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE delivery_rules ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE delivery_rules SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE delivery_rules SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为notification_channels表添加user_id字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT user_id FROM notification_channels LIMIT 1")
                except sqlite3.OperationalError:
                    # user_id列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE notification_channels ADD COLUMN user_id INTEGER")
                    self._execute_sql(cursor, "UPDATE notification_channels SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
                else:
                    # user_id列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE notification_channels SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

                # 为email_verifications表添加type字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT type FROM email_verifications LIMIT 1")
                except sqlite3.OperationalError:
                    # type列不存在，需要添加并更新历史数据
                    self._execute_sql(cursor, "ALTER TABLE email_verifications ADD COLUMN type TEXT DEFAULT 'register'")
                    self._execute_sql(cursor, "UPDATE email_verifications SET type = 'register' WHERE type IS NULL")
                else:
                    # type列存在，更新NULL值
                    self._execute_sql(cursor, "UPDATE email_verifications SET type = 'register' WHERE type IS NULL")

                # 为cards表添加多规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT is_multi_spec FROM cards LIMIT 1")
                except sqlite3.OperationalError:
                    # 多规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN is_multi_spec BOOLEAN DEFAULT FALSE")
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_name TEXT")
                    self._execute_sql(cursor, "ALTER TABLE cards ADD COLUMN spec_value TEXT")
                    logger.info("为cards表添加多规格字段")

                # 为item_info表添加多规格字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT is_multi_spec FROM item_info LIMIT 1")
                except sqlite3.OperationalError:
                    # 多规格字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN is_multi_spec BOOLEAN DEFAULT FALSE")
                    logger.info("为item_info表添加多规格字段")

                # 为item_info表添加多数量发货字段（如果不存在）
                try:
                    self._execute_sql(cursor, "SELECT multi_quantity_delivery FROM item_info LIMIT 1")
                except sqlite3.OperationalError:
                    # 多数量发货字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE item_info ADD COLUMN multi_quantity_delivery BOOLEAN DEFAULT FALSE")
                    logger.info("为item_info表添加多数量发货字段")

                # 检查orders表是否有is_bargain字段
                try:
                    self._execute_sql(cursor, "SELECT is_bargain FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # is_bargain字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN is_bargain INTEGER DEFAULT 0")
                    logger.info("为orders表添加is_bargain字段")

                # 检查orders表是否有receiver_name字段
                try:
                    self._execute_sql(cursor, "SELECT receiver_name FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # receiver_name字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_name TEXT")
                    logger.info("为orders表添加receiver_name字段")

                # 检查orders表是否有receiver_phone字段
                try:
                    self._execute_sql(cursor, "SELECT receiver_phone FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # receiver_phone字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_phone TEXT")
                    logger.info("为orders表添加receiver_phone字段")

                # 检查orders表是否有receiver_address字段
                try:
                    self._execute_sql(cursor, "SELECT receiver_address FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # receiver_address字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_address TEXT")
                    logger.info("为orders表添加receiver_address字段")

                # 检查orders表是否有receiver_city字段
                try:
                    self._execute_sql(cursor, "SELECT receiver_city FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN receiver_city TEXT")
                    logger.info("为orders表添加receiver_city字段")

                # 检查orders表是否有system_shipped字段（系统是否已发货）
                try:
                    self._execute_sql(cursor, "SELECT system_shipped FROM orders LIMIT 1")
                except sqlite3.OperationalError:
                    # system_shipped字段不存在，需要添加
                    self._execute_sql(cursor, "ALTER TABLE orders ADD COLUMN system_shipped INTEGER DEFAULT 0")
                    logger.info("为orders表添加system_shipped字段")

                # 处理keywords表的唯一约束问题
                # 由于SQLite不支持直接修改约束，我们需要重建表
                self._migrate_keywords_table_constraints(cursor)

            self.conn.commit()
            logger.info(f"admin用户ID更新完成")
        except Exception as e:
            logger.error(f"更新admin用户ID失败: {e}")
            raise

    def upgrade_notification_channels_table(self, cursor):
        """升级notification_channels表的type字段约束"""
        try:
            logger.info("开始升级notification_channels表...")

            # 检查表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notification_channels'")
            if not cursor.fetchone():
                logger.info("notification_channels表不存在，无需升级")
                return True

            # 检查表中是否有数据
            cursor.execute("SELECT COUNT(*) FROM notification_channels")
            count = cursor.fetchone()[0]

            # 删除可能存在的临时表
            cursor.execute("DROP TABLE IF EXISTS notification_channels_new")

            # 创建临时表
            cursor.execute('''
            CREATE TABLE notification_channels_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 复制数据，并转换不兼容的类型
            if count > 0:
                logger.info(f"复制 {count} 条通知渠道数据到新表")
                # 先查看现有数据的类型
                cursor.execute("SELECT DISTINCT type FROM notification_channels")
                existing_types = [row[0] for row in cursor.fetchall()]
                logger.info(f"现有通知渠道类型: {existing_types}")

                # 获取所有现有数据进行逐行处理
                cursor.execute("SELECT * FROM notification_channels")
                existing_data = cursor.fetchall()

                # 逐行转移数据，确保类型映射正确
                for row in existing_data:
                    old_type = row[3] if len(row) > 3 else 'qq'  # type字段，默认为qq

                    # 类型映射规则
                    type_mapping = {
                        'dingtalk': 'ding_talk',
                        'ding_talk': 'ding_talk',
                        'qq': 'qq',
                        'email': 'qq',  # 暂时映射为qq，后续版本会支持
                        'webhook': 'qq',  # 暂时映射为qq，后续版本会支持
                        'wechat': 'qq',  # 暂时映射为qq，后续版本会支持
                        'telegram': 'qq'  # 暂时映射为qq，后续版本会支持
                    }

                    new_type = type_mapping.get(old_type, 'qq')  # 默认转换为qq类型

                    if old_type != new_type:
                        logger.info(f"转换通知渠道类型: {old_type} -> {new_type}")

                    # 插入到新表
                    cursor.execute('''
                    INSERT INTO notification_channels_new
                    (id, name, user_id, type, config, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row[0],  # id
                        row[1],  # name
                        row[2],  # user_id
                        new_type,  # type (转换后的)
                        row[4] if len(row) > 4 else '{}',  # config
                        row[5] if len(row) > 5 else True,  # enabled
                        row[6] if len(row) > 6 else None,  # created_at
                        row[7] if len(row) > 7 else None   # updated_at
                    ))

            # 删除旧表
            cursor.execute("DROP TABLE notification_channels")

            # 重命名新表
            cursor.execute("ALTER TABLE notification_channels_new RENAME TO notification_channels")

            logger.info("notification_channels表升级完成")
            return True
        except Exception as e:
            logger.error(f"升级notification_channels表失败: {e}")
            raise

    def upgrade_notification_channels_types(self, cursor):
        """升级notification_channels表支持更多渠道类型"""
        try:
            logger.info("开始升级notification_channels表支持更多渠道类型...")

            # 检查表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notification_channels'")
            if not cursor.fetchone():
                logger.info("notification_channels表不存在，无需升级")
                return True

            # 检查表中是否有数据
            cursor.execute("SELECT COUNT(*) FROM notification_channels")
            count = cursor.fetchone()[0]

            # 获取现有数据
            existing_data = []
            if count > 0:
                cursor.execute("SELECT * FROM notification_channels")
                existing_data = cursor.fetchall()
                logger.info(f"备份 {count} 条通知渠道数据")

            # 创建新表，支持所有通知渠道类型
            cursor.execute('''
            CREATE TABLE notification_channels_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('qq','ding_talk','dingtalk','feishu','lark','bark','email','webhook','wechat','telegram')),
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # 复制数据，同时处理类型映射
            if existing_data:
                logger.info(f"迁移 {len(existing_data)} 条通知渠道数据到新表")
                for row in existing_data:
                    # 处理类型映射，支持更多渠道类型
                    old_type = row[3] if len(row) > 3 else 'qq'  # type字段

                    # 完整的类型映射规则，支持所有通知渠道
                    type_mapping = {
                        'ding_talk': 'dingtalk',  # 统一为dingtalk
                        'dingtalk': 'dingtalk',
                        'qq': 'qq',
                        'feishu': 'feishu',      # 飞书通知
                        'lark': 'lark',          # 飞书通知（英文名）
                        'bark': 'bark',          # Bark通知
                        'email': 'email',        # 邮件通知
                        'webhook': 'webhook',    # Webhook通知
                        'wechat': 'wechat',      # 微信通知
                        'telegram': 'telegram'   # Telegram通知
                    }

                    new_type = type_mapping.get(old_type, 'qq')  # 默认为qq

                    if old_type != new_type:
                        logger.info(f"转换通知渠道类型: {old_type} -> {new_type}")

                    # 插入到新表，确保字段完整性
                    cursor.execute('''
                    INSERT INTO notification_channels_new
                    (id, name, user_id, type, config, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row[0],  # id
                        row[1],  # name
                        row[2],  # user_id
                        new_type,  # type (转换后的)
                        row[4] if len(row) > 4 else '{}',  # config
                        row[5] if len(row) > 5 else True,  # enabled
                        row[6] if len(row) > 6 else None,  # created_at
                        row[7] if len(row) > 7 else None   # updated_at
                    ))

            # 删除旧表
            cursor.execute("DROP TABLE notification_channels")

            # 重命名新表
            cursor.execute("ALTER TABLE notification_channels_new RENAME TO notification_channels")

            logger.info("notification_channels表类型升级完成")
            logger.info("✅ 现在支持以下所有通知渠道类型:")
            logger.info("   - qq (QQ通知)")
            logger.info("   - ding_talk/dingtalk (钉钉通知)")
            logger.info("   - feishu/lark (飞书通知)")
            logger.info("   - bark (Bark通知)")
            logger.info("   - email (邮件通知)")
            logger.info("   - webhook (Webhook通知)")
            logger.info("   - wechat (微信通知)")
            logger.info("   - telegram (Telegram通知)")
            return True
        except Exception as e:
            logger.error(f"升级notification_channels表类型失败: {e}")
            raise

    def upgrade_cookies_table_for_account_login(self, cursor):
        """升级cookies表支持账号密码登录功能"""
        try:
            logger.info("开始为cookies表添加账号登录相关字段...")

            # 为cookies表添加username字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT username FROM cookies LIMIT 1")
                logger.info("cookies表username字段已存在")
            except sqlite3.OperationalError:
                # username字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN username TEXT DEFAULT ''")
                logger.info("为cookies表添加username字段")

            # 为cookies表添加password字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT password FROM cookies LIMIT 1")
                logger.info("cookies表password字段已存在")
            except sqlite3.OperationalError:
                # password字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN password TEXT DEFAULT ''")
                logger.info("为cookies表添加password字段")

            # 为cookies表添加show_browser字段（如果不存在）
            try:
                self._execute_sql(cursor, "SELECT show_browser FROM cookies LIMIT 1")
                logger.info("cookies表show_browser字段已存在")
            except sqlite3.OperationalError:
                # show_browser字段不存在，需要添加
                self._execute_sql(cursor, "ALTER TABLE cookies ADD COLUMN show_browser INTEGER DEFAULT 0")
                logger.info("为cookies表添加show_browser字段")

            logger.info("✅ cookies表账号登录字段升级完成")
            logger.info("   - username: 用于密码登录的用户名")
            logger.info("   - password: 用于密码登录的密码")
            logger.info("   - show_browser: 登录时是否显示浏览器（0=隐藏，1=显示）")
            return True
        except Exception as e:
            logger.error(f"升级cookies表账号登录字段失败: {e}")
            raise

    def migrate_legacy_data(self, cursor):
        """迁移遗留数据到新表结构"""
        try:
            logger.info("开始检查和迁移遗留数据...")

            # 检查是否有需要迁移的老表
            legacy_tables = [
                'old_notification_channels',
                'legacy_delivery_rules',
                'old_keywords',
                'backup_cookies'
            ]

            for table_name in legacy_tables:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if cursor.fetchone():
                    logger.info(f"发现遗留表: {table_name}，开始迁移数据...")
                    self._migrate_table_data(cursor, table_name)

            logger.info("遗留数据迁移完成")
            return True
        except Exception as e:
            logger.error(f"迁移遗留数据失败: {e}")
            return False

    def _migrate_table_data(self, cursor, table_name: str):
        """迁移指定表的数据"""
        try:
            if table_name == 'old_notification_channels':
                # 迁移通知渠道数据
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]

                if count > 0:
                    cursor.execute(f"SELECT * FROM {table_name}")
                    old_data = cursor.fetchall()

                    for row in old_data:
                        # 处理数据格式转换
                        cursor.execute('''
                        INSERT OR IGNORE INTO notification_channels
                        (name, user_id, type, config, enabled)
                        VALUES (?, ?, ?, ?, ?)
                        ''', (
                            row[1] if len(row) > 1 else f"迁移渠道_{row[0]}",
                            row[2] if len(row) > 2 else 1,  # 默认admin用户
                            self._normalize_channel_type(row[3] if len(row) > 3 else 'qq'),
                            row[4] if len(row) > 4 else '{}',
                            row[5] if len(row) > 5 else True
                        ))

                    logger.info(f"成功迁移 {count} 条通知渠道数据")

                    # 迁移完成后删除老表
                    cursor.execute(f"DROP TABLE {table_name}")
                    logger.info(f"已删除遗留表: {table_name}")

        except Exception as e:
            logger.error(f"迁移表 {table_name} 数据失败: {e}")

    def _normalize_channel_type(self, old_type: str) -> str:
        """标准化通知渠道类型"""
        type_mapping = {
            'ding_talk': 'dingtalk',
            'dingtalk': 'dingtalk',
            'qq': 'qq',
            'email': 'email',
            'webhook': 'webhook',
            'wechat': 'wechat',
            'telegram': 'telegram',
            # 处理一些可能的变体
            'dingding': 'dingtalk',
            'weixin': 'wechat',
            'tg': 'telegram'
        }
        return type_mapping.get(old_type.lower(), 'qq')

    def _migrate_keywords_table_constraints(self, cursor):
        """迁移keywords表的约束，支持基于商品ID的唯一性校验"""
        try:
            # 检查是否已经迁移过（通过检查是否存在新的唯一索引）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_keywords_unique_with_item'")
            if cursor.fetchone():
                logger.info("keywords表约束已经迁移过，跳过")
                return

            logger.info("开始迁移keywords表约束...")

            # 1. 创建临时表，不设置主键约束
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords_temp (
                cookie_id TEXT,
                keyword TEXT,
                reply TEXT,
                item_id TEXT,
                FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
            )
            ''')

            # 2. 复制现有数据到临时表
            cursor.execute('''
            INSERT INTO keywords_temp (cookie_id, keyword, reply, item_id)
            SELECT cookie_id, keyword, reply, item_id FROM keywords
            ''')

            # 3. 删除原表
            cursor.execute('DROP TABLE keywords')

            # 4. 重命名临时表
            cursor.execute('ALTER TABLE keywords_temp RENAME TO keywords')

            # 5. 创建复合唯一索引来实现我们需要的约束逻辑
            # 对于item_id为空的情况：(cookie_id, keyword)必须唯一
            cursor.execute('''
            CREATE UNIQUE INDEX idx_keywords_unique_no_item
            ON keywords(cookie_id, keyword)
            WHERE item_id IS NULL OR item_id = ''
            ''')

            # 对于item_id不为空的情况：(cookie_id, keyword, item_id)必须唯一
            cursor.execute('''
            CREATE UNIQUE INDEX idx_keywords_unique_with_item
            ON keywords(cookie_id, keyword, item_id)
            WHERE item_id IS NOT NULL AND item_id != ''
            ''')

            logger.info("keywords表约束迁移完成")

        except Exception as e:
            logger.error(f"迁移keywords表约束失败: {e}")
            # 如果迁移失败，尝试回滚
            try:
                cursor.execute('DROP TABLE IF EXISTS keywords_temp')
            except:
                pass
            raise

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_connection(self):
        """获取数据库连接，如果已关闭则重新连接"""
        if self.conn is None:
            self.conn = self._connect(self.db_path)
        return self.conn

    def _log_sql(self, sql: str, params: tuple = None, operation: str = "EXECUTE"):
        """记录SQL执行日志"""
        if not self.sql_log_enabled:
            return

        # 格式化SQL（移除多余空白）
        formatted_sql = ' '.join(sql.split())
        sensitive_sql_terms = (
            'auth_sessions',
            'auth_challenges',
            'registration_invites',
            'auth_rate_events',
            'account_session_refresh_status',
            'cookies',
            'password',
            'token',
            'api_key',
            'smtp_password',
            'system_settings',
            'ai_reply_settings',
            'custom_prompts',
            'ai_training',
            'item_knowledge',
        )
        redact_params = any(term in formatted_sql.lower() for term in sensitive_sql_terms)

        # 格式化参数
        params_str = ""
        if params:
            if isinstance(params, (list, tuple)):
                if len(params) > 0:
                    # 限制参数长度，避免日志过长
                    formatted_params = []
                    for param in params:
                        if redact_params and isinstance(param, (str, bytes)):
                            formatted_params.append("'[REDACTED]'")
                        elif isinstance(param, str) and len(param) > 100:
                            formatted_params.append(f"{param[:100]}...")
                        else:
                            formatted_params.append(repr(param))
                    params_str = f" | 参数: [{', '.join(formatted_params)}]"

        # 根据配置的日志级别输出
        log_message = f"🗄️ SQL {operation}: {formatted_sql}{params_str}"

        if self.sql_log_level == 'DEBUG':
            logger.debug(log_message)
        elif self.sql_log_level == 'INFO':
            logger.info(log_message)
        elif self.sql_log_level == 'WARNING':
            logger.warning(log_message)
        else:
            logger.debug(log_message)

    def _execute_sql(self, cursor, sql: str, params: tuple = None):
        """执行SQL并记录日志"""
        self._log_sql(sql, params, "EXECUTE")
        if params:
            return cursor.execute(sql, params)
        else:
            return cursor.execute(sql)

    def _executemany_sql(self, cursor, sql: str, params_list):
        """批量执行SQL并记录日志"""
        self._log_sql(sql, f"批量执行 {len(params_list)} 条记录", "EXECUTEMANY")
        return cursor.executemany(sql, params_list)

    # -------------------- 后台登录会话操作 --------------------
    def save_auth_session(self, token: str, user_id: int, username: str, is_admin: bool, expires_at: float) -> bool:
        """保存后台登录会话，使服务重启后仍可保持登录"""
        with self.lock:
            try:
                now = time.time()
                digest = token_digest(token)
                storage_id = f"digest:{digest}"
                self.auth_session_repository.save(
                    storage_id, digest, user_id, username, is_admin, now, expires_at
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"保存登录会话失败: {e}")
                self.conn.rollback()
                return False

    def get_auth_session(self, token: str) -> Optional[Dict[str, Any]]:
        """读取未过期的后台登录会话"""
        with self.lock:
            try:
                digest = token_digest(token)
                row = self.auth_session_repository.get(digest, token)
                if not row:
                    return None

                if time.time() > float(row[5]):
                    self.delete_auth_session(token)
                    return None

                self.auth_session_repository.touch(digest, token, time.time())
                self.conn.commit()
                return {
                    'token': token,
                    'user_id': row[1],
                    'username': row[2],
                    'is_admin': bool(row[3]),
                    'timestamp': float(row[4]),
                    'expires_at': float(row[5]),
                    'last_seen_at': row[6]
                }
            except Exception as e:
                logger.error(f"读取登录会话失败: {e}")
                return None

    def delete_auth_session(self, token: str) -> bool:
        """删除指定后台登录会话"""
        with self.lock:
            try:
                digest = token_digest(token)
                self.auth_session_repository.delete(digest, token)
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"删除登录会话失败: {e}")
                self.conn.rollback()
                return False

    def cleanup_expired_auth_sessions(self) -> bool:
        """清理过期后台登录会话"""
        with self.lock:
            try:
                self.auth_session_repository.cleanup_expired(time.time())
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"清理过期登录会话失败: {e}")
                self.conn.rollback()
                return False

    # -------------------- Cookie操作 --------------------
    @staticmethod
    def _extract_cookie_unb(cookie_value: str) -> str:
        if not cookie_value:
            return ''
        try:
            parsed = SimpleCookie()
            parsed.load(cookie_value)
            if 'unb' in parsed:
                return parsed['unb'].value.strip()
        except Exception:
            pass
        for part in cookie_value.split(';'):
            key, separator, value = part.strip().partition('=')
            if separator and key == 'unb':
                return value.strip()
        return ''

    def backfill_cookie_identities(self) -> int:
        updated = 0
        with self.lock:
            cursor = self.conn.cursor()
            self._execute_sql(cursor, "SELECT id, value, user_id FROM cookies WHERE xianyu_unb IS NULL OR xianyu_unb = ''")
            for cookie_id, cookie_value, user_id in cursor.fetchall():
                unb = self._extract_cookie_unb(cookie_value)
                if not unb:
                    continue
                self._execute_sql(
                    cursor,
                    "SELECT id FROM cookies WHERE user_id = ? AND xianyu_unb = ? AND id <> ?",
                    (user_id, unb, cookie_id),
                )
                if cursor.fetchone():
                    continue
                self._execute_sql(cursor, "UPDATE cookies SET xianyu_unb = ? WHERE id = ?", (unb, cookie_id))
                updated += cursor.rowcount
            self.conn.commit()
        return updated

    def find_cookie_id_by_unb(self, user_id: int, unb: str) -> Optional[str]:
        if not unb:
            return None
        with self.lock:
            cursor = self.conn.cursor()
            self._execute_sql(
                cursor,
                "SELECT id FROM cookies WHERE user_id = ? AND xianyu_unb = ? LIMIT 1",
                (user_id, unb),
            )
            row = cursor.fetchone()
            if row:
                return row[0]

            self._execute_sql(cursor, "SELECT id, value FROM cookies WHERE user_id = ?", (user_id,))
            for cookie_id, cookie_value in cursor.fetchall():
                if self._extract_cookie_unb(cookie_value) == unb:
                    self._execute_sql(cursor, "UPDATE cookies SET xianyu_unb = ? WHERE id = ?", (unb, cookie_id))
                    self.conn.commit()
                    return cookie_id
        return None

    def save_cookie(
        self,
        cookie_id: str,
        cookie_value: str,
        user_id: int = None,
        *,
        login_method: str = None,
    ) -> bool:
        """保存Cookie到数据库，如存在则更新"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 如果没有提供user_id，尝试从现有记录获取，否则使用admin用户ID
                if user_id is None:
                    self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,))
                    existing = cursor.fetchone()
                    if existing:
                        user_id = existing[0]
                    else:
                        # 获取admin用户ID作为默认值
                        self._execute_sql(cursor, "SELECT id FROM users WHERE username = 'admin'")
                        admin_user = cursor.fetchone()
                        user_id = admin_user[0] if admin_user else 1

                xianyu_unb = self._extract_cookie_unb(cookie_value)
                self._execute_sql(
                    cursor,
                    "SELECT xianyu_unb, value FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                identity_row = cursor.fetchone()
                stable_unb = (identity_row[0] or "").strip() if identity_row else ""
                if identity_row and not stable_unb:
                    stable_unb = self._extract_cookie_unb(identity_row[1])
                if stable_unb and xianyu_unb != stable_unb:
                    raise AccountIdentityMismatchError()
                self._execute_sql(cursor, '''
                    INSERT INTO cookies (id, value, user_id, xianyu_unb)
                    VALUES (?, ?, ?, NULLIF(?, ''))
                    ON CONFLICT(id) DO UPDATE SET
                        cookie_revision = CASE
                            WHEN cookies.value <> excluded.value
                            THEN cookies.cookie_revision + 1
                            ELSE cookies.cookie_revision
                        END,
                        value = excluded.value,
                        xianyu_unb = COALESCE(NULLIF(excluded.xianyu_unb, ''), cookies.xianyu_unb)
                    WHERE cookies.user_id = excluded.user_id
                      AND (
                          cookies.xianyu_unb IS NULL
                          OR cookies.xianyu_unb = ''
                          OR excluded.xianyu_unb IS NULL
                          OR excluded.xianyu_unb = ''
                          OR cookies.xianyu_unb = excluded.xianyu_unb
                      )
                ''', (cookie_id, cookie_value, user_id, xianyu_unb))

                if cursor.rowcount != 1:
                    self.conn.rollback()
                    logger.warning("Cookie保存被账号归属或身份校验阻止")
                    return False

                if login_method is not None:
                    normalized_method = normalize_login_method(login_method)
                    now = time.time()
                    self._execute_sql(
                        cursor,
                        "UPDATE cookies SET login_method = ?, last_login_at = ?, "
                        "last_validated_at = NULL, last_expired_at = NULL, "
                        "cookie_refresh_enabled = CASE WHEN ? = 'password' "
                        "THEN cookie_refresh_enabled ELSE 0 END WHERE id = ?",
                        (normalized_method, now, normalized_method, cookie_id),
                    )
                    self._execute_sql(
                        cursor,
                        "DELETE FROM account_session_refresh_status WHERE cookie_id = ?",
                        (cookie_id,),
                    )

                self.conn.commit()
                logger.info(f"Cookie保存成功: {cookie_id} (用户ID: {user_id})")

                # 验证保存结果
                self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,))
                saved_user_id = cursor.fetchone()
                if saved_user_id:
                    logger.info(f"Cookie保存验证: {cookie_id} 实际绑定到用户ID: {saved_user_id[0]}")
                else:
                    logger.error(f"Cookie保存验证失败: {cookie_id} 未找到记录")
                return True
            except AccountIdentityMismatchError:
                self.conn.rollback()
                raise
            except Exception as e:
                logger.error(f"Cookie保存失败: {type(e).__name__}")
                self.conn.rollback()
                return False


    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _delete_cookie_children(
        self,
        cursor: sqlite3.Cursor,
        cookie_ids: Sequence[str],
    ) -> None:
        normalized_ids = [str(cookie_id) for cookie_id in cookie_ids if cookie_id]
        if not normalized_ids:
            return
        placeholders = ",".join("?" for _ in normalized_ids)
        tables = cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table_name,) in tables:
            table_name = str(table_name)
            if table_name == "cookies":
                continue
            columns = {
                str(row[1])
                for row in cursor.execute(
                    f"PRAGMA table_info({self._quote_identifier(table_name)})"
                ).fetchall()
            }
            if "cookie_id" not in columns:
                continue
            cursor.execute(
                f"DELETE FROM {self._quote_identifier(table_name)} "
                f"WHERE cookie_id IN ({placeholders})",
                normalized_ids,
            )

    def delete_cookie(self, cookie_id: str) -> bool:
        """原子删除账号及所有 cookie_id 子表数据。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                self._delete_cookie_children(cursor, [cookie_id])
                self._execute_sql(cursor, "DELETE FROM cookies WHERE id = ?", (cookie_id,))
                self.conn.commit()
                logger.debug(f"Cookie删除成功: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"Cookie删除失败: {e}")
                self.conn.rollback()
                return False

    def get_cookie(self, cookie_id: str) -> Optional[str]:
        """获取指定Cookie值"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT value FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                return result[0] if result else None
            except Exception as e:
                logger.error(f"获取Cookie失败: {e}")
                return None

    def get_all_cookies(self, user_id: int = None) -> Dict[str, str]:
        """获取所有Cookie（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "SELECT id, value FROM cookies WHERE user_id = ?", (user_id,))
                else:
                    self._execute_sql(cursor, "SELECT id, value FROM cookies")
                return {row[0]: row[1] for row in cursor.fetchall()}
            except Exception as e:
                logger.error(f"获取所有Cookie失败: {e}")
                return {}



    def get_cookie_by_id(self, cookie_id: str) -> Optional[Dict[str, str]]:
        """根据ID获取Cookie信息

        Args:
            cookie_id: Cookie ID

        Returns:
            Dict包含cookie信息，包括cookies_str字段，如果不存在返回None
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT id, value, created_at FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    return {
                        'id': result[0],
                        'cookies_str': result[1],  # 使用cookies_str字段名以匹配调用方期望
                        'value': result[1],        # 保持向后兼容
                        'created_at': result[2]
                    }
                return None
            except Exception as e:
                logger.error(f"根据ID获取Cookie失败: {e}")
                return None

    def get_cookie_user_id(self, cookie_id: str) -> Optional[int]:
        """获取 cookie（闲鱼账号）归属的用户 ID，不存在返回 None"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,))
                row = cursor.fetchone()
                return row[0] if row else None
            except Exception as e:
                logger.error(f"获取Cookie归属用户失败: {e}")
                return None

    def get_cookie_details(self, cookie_id: str) -> Optional[Dict[str, any]]:
        """获取Cookie的详细信息，包括user_id、auto_confirm、remark、pause_duration、username、password和show_browser"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT id, value, user_id, auto_confirm, remark, pause_duration, username, password, "
                    "show_browser, created_at, xianyu_unb, password_encrypted, "
                    "cookie_refresh_enabled, cookie_refresh_interval_minutes, browser_user_agent, "
                    "cookie_revision, login_method, last_login_at, last_validated_at, "
                    "last_expired_at, avatar_url, xianyu_nick, "
                    "auto_rate_enabled, auto_rate_enabled_at, has_l3_memory, l3_memory_at, "
                    "proxy_enabled, proxy_server, proxy_username, proxy_password_encrypted, "
                    "proxy_bypass, proxy_region, proxy_last_ip, proxy_last_status, proxy_last_check_at "
                    "FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                result = cursor.fetchone()
                if result:
                    password = result[7] or ''
                    if result[11]:
                        password = AccountCredentialCipher(self.db_path).decrypt(result[11])
                    return {
                        'id': result[0],
                        'value': result[1],
                        'user_id': result[2],
                        'auto_confirm': bool(result[3]),
                        'remark': result[4] or '',
                        'pause_duration': result[5] if result[5] is not None else 10,  # 0是有效值，表示不暂停
                        'username': result[6] or '',
                        'password': password,
                        'show_browser': bool(result[8]) if result[8] is not None else False,
                        'created_at': result[9],
                        'xianyu_unb': result[10] or '',
                        'cookie_refresh_enabled': bool(result[12]) if result[12] is not None else False,
                        'cookie_refresh_interval_minutes': (
                            result[13]
                            if result[13] is not None
                            else COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES
                        ),
                        'browser_user_agent': result[14] or '',
                        'cookie_revision': int(result[15] or 0),
                        'login_method': normalize_login_method(result[16]),
                        'last_login_at': result[17],
                        'last_validated_at': result[18],
                        'last_expired_at': result[19],
                        'avatar_url': result[20] or '',
                        'xianyu_nick': result[21] or '',
                        'auto_rate_enabled': bool(result[22]),
                        'auto_rate_enabled_at': result[23],
                        'has_l3_memory': bool(result[24]),
                        'l3_memory_at': result[25],
                        'proxy_enabled': bool(result[26]),
                        'proxy_server': result[27] or '',
                        'proxy_username': result[28] or '',
                        'proxy_password_set': bool(result[29]),
                        'proxy_bypass': result[30] or '',
                        'proxy_region': result[31] or '',
                        'proxy_last_ip': result[32] or '',
                        'proxy_last_status': result[33] or '',
                        'proxy_last_check_at': result[34],
                    }
                return None
            except Exception as e:
                logger.error(f"获取Cookie详细信息失败: {e}")
                return None

    def update_account_profile(
        self,
        cookie_id: str,
        avatar_url: str = '',
        xianyu_nick: str = '',
    ) -> bool:
        """Cache the account's own Xianyu avatar/nickname; empty values never overwrite."""
        normalized_avatar = str(avatar_url or '').strip()[:512]
        normalized_nick = str(xianyu_nick or '').strip()[:64]
        if not normalized_avatar and not normalized_nick:
            return False
        assignments = []
        params: List[any] = []
        if normalized_avatar:
            assignments.append('avatar_url = ?')
            params.append(normalized_avatar)
        if normalized_nick:
            assignments.append('xianyu_nick = ?')
            params.append(normalized_nick)
        params.append(str(cookie_id))
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    f"UPDATE cookies SET {', '.join(assignments)} WHERE id = ?",
                    tuple(params),
                )
                if cursor.rowcount <= 0:
                    # 0 行也已隐式开启事务，必须显式结束，避免悬挂事务击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error("更新账号资料失败: {}", type(exc).__name__)
                self.conn.rollback()
                return False

    def get_owned_cookie_search_context(self, user_id: int, cookie_id: str) -> Dict[str, any]:
        """Return the minimum owner-scoped account context needed by item search."""
        normalized_cookie_id = str(cookie_id or '').strip()
        try:
            normalized_user_id = int(user_id)
        except (TypeError, ValueError):
            normalized_user_id = 0
        if normalized_user_id <= 0 or not normalized_cookie_id:
            return {
                'state': 'action_required',
                'reason': 'missing_account_binding',
            }

        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT id, value, user_id, xianyu_unb, cookie_revision, browser_user_agent "
                    "FROM cookies WHERE id = ? AND user_id = ?",
                    (normalized_cookie_id, normalized_user_id),
                )
                row = cursor.fetchone()
                if not row:
                    self._execute_sql(
                        cursor,
                        "SELECT 1 FROM cookies WHERE id = ?",
                        (normalized_cookie_id,),
                    )
                    return {
                        'state': (
                            'ownership_mismatch'
                            if cursor.fetchone()
                            else 'not_found'
                        ),
                        'reason': 'account_not_owned',
                    }

                cookie_value = str(row[1] or '')
                stored_unb = str(row[3] or '').strip()
                cookie_unb = self._extract_cookie_unb(cookie_value)
                if not stored_unb or not cookie_unb or cookie_unb != stored_unb:
                    return {
                        'state': 'action_required',
                        'reason': 'account_identity_incomplete',
                    }

                return {
                    'state': 'ready',
                    'account_id': str(row[0]),
                    'value': cookie_value,
                    'user_id': int(row[2]),
                    'xianyu_unb': stored_unb,
                    'cookie_revision': int(row[4] or 0),
                    'browser_user_agent': str(row[5] or '').strip(),
                }
            except Exception as exc:
                logger.error(
                    f"读取账号搜索上下文失败: {type(exc).__name__}"
                )
                return {
                    'state': 'error',
                    'reason': 'account_context_read_failed',
                }

    def compare_and_swap_cookie_session(
        self,
        cookie_id: str,
        *,
        user_id: int,
        expected_xianyu_unb: str,
        expected_revision: int,
        cookie_value: str,
        browser_user_agent: str = None,
    ) -> Dict[str, any]:
        """Persist a refreshed Cookie only while owner, identity, and revision match."""
        normalized_cookie_id = str(cookie_id or '').strip()
        normalized_expected_unb = str(expected_xianyu_unb or '').strip()
        normalized_cookie_value = str(cookie_value or '').strip()
        incoming_unb = self._extract_cookie_unb(normalized_cookie_value)
        try:
            normalized_user_id = int(user_id)
            normalized_revision = int(expected_revision)
        except (TypeError, ValueError):
            return {
                'state': 'action_required',
                'reason': 'invalid_cookie_cas_identity',
                'updated': False,
            }

        if (
            not normalized_cookie_id
            or normalized_user_id <= 0
            or normalized_revision < 0
            or not normalized_expected_unb
            or not incoming_unb
        ):
            return {
                'state': 'action_required',
                'reason': 'account_identity_incomplete',
                'updated': False,
            }

        with self.lock:
            cursor = self.conn.cursor()
            try:
                self._execute_sql(
                    cursor,
                    "SELECT value, user_id, xianyu_unb, cookie_revision, browser_user_agent "
                    "FROM cookies WHERE id = ?",
                    (normalized_cookie_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return {
                        'state': 'not_found',
                        'reason': 'account_missing',
                        'updated': False,
                    }

                stored_value = str(row[0] or '')
                stored_user_id = int(row[1] or 0)
                stored_unb = str(row[2] or '').strip()
                stored_revision = int(row[3] or 0)
                stored_cookie_unb = self._extract_cookie_unb(stored_value)
                if stored_user_id != normalized_user_id:
                    return {
                        'state': 'ownership_mismatch',
                        'reason': 'account_not_owned',
                        'updated': False,
                    }
                if (
                    not stored_unb
                    or not stored_cookie_unb
                    or stored_cookie_unb != stored_unb
                    or normalized_expected_unb != stored_unb
                    or incoming_unb != stored_unb
                ):
                    return {
                        'state': 'action_required',
                        'reason': 'account_identity_changed',
                        'updated': False,
                    }
                if stored_revision != normalized_revision:
                    return {
                        'state': 'revision_conflict',
                        'reason': 'cookie_revision_conflict',
                        'updated': False,
                        'cookie_revision': stored_revision,
                    }

                cookie_changed = stored_value != normalized_cookie_value
                next_revision = stored_revision + (1 if cookie_changed else 0)
                next_user_agent = (
                    str(browser_user_agent).strip()
                    if browser_user_agent is not None
                    else str(row[4] or '').strip()
                )
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET value = ?, xianyu_unb = ?, cookie_revision = ?, "
                    "browser_user_agent = ? "
                    "WHERE id = ? AND user_id = ? AND xianyu_unb = ? AND cookie_revision = ?",
                    (
                        normalized_cookie_value,
                        stored_unb,
                        next_revision,
                        next_user_agent,
                        normalized_cookie_id,
                        normalized_user_id,
                        stored_unb,
                        stored_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return {
                        'state': 'revision_conflict',
                        'reason': 'cookie_revision_conflict',
                        'updated': False,
                    }
                self.conn.commit()
                return {
                    'state': 'updated' if cookie_changed else 'unchanged',
                    'reason': '',
                    'updated': cookie_changed,
                    'cookie_revision': next_revision,
                }
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"Cookie CAS 保存失败: {type(exc).__name__}")
                return {
                    'state': 'error',
                    'reason': 'cookie_cas_failed',
                    'updated': False,
                }

    def _validate_cookie_refresh_interval(self, interval_minutes: int) -> int:
        try:
            normalized_interval = int(interval_minutes)
        except (TypeError, ValueError):
            raise ValueError("Cookie定时刷新间隔必须是数字")

        if not (
            COOKIE_REFRESH_MIN_INTERVAL_MINUTES
            <= normalized_interval
            <= COOKIE_REFRESH_MAX_INTERVAL_MINUTES
        ):
            raise ValueError("Cookie定时刷新间隔必须在1小时到7天之间")

        return normalized_interval

    def update_cookie_refresh_settings(
        self,
        cookie_id: str,
        *,
        enabled: bool,
        interval_minutes: int,
    ) -> bool:
        """更新账号级定时Cookie刷新设置"""
        normalized_interval = self._validate_cookie_refresh_interval(interval_minutes)
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT login_method, username, password, password_encrypted, "
                    "COALESCE(has_l3_memory, 0) "
                    "FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                capability = cursor.fetchone()
                if not capability:
                    return False
                binding = cursor.execute(
                    """
                    SELECT 1 FROM account_renewal_bindings AS b
                    JOIN client_browser_devices AS d
                      ON d.device_id = b.device_id AND d.user_id = b.user_id
                    WHERE b.cookie_id = ? AND b.revoked_at IS NULL
                      AND d.revoked_at IS NULL
                    """,
                    (cookie_id,),
                ).fetchone()
                auto_refresh_supported = bool(
                    capability[4]
                    or (binding and capability[1] and (capability[2] or capability[3]))
                )
                if enabled and not auto_refresh_supported:
                    raise ValueError("当前账号尚未建立浏览器登录记忆，也未绑定可用的续期设备和凭据")
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET cookie_refresh_enabled = ?, "
                    "cookie_refresh_interval_minutes = ? WHERE id = ?",
                    (1 if enabled else 0, normalized_interval, cookie_id),
                )
                self.conn.commit()
                if cursor.rowcount <= 0:
                    logger.warning(f"账号 {cookie_id} 不存在，无法更新Cookie定时刷新设置")
                    return False
                status = "开启" if enabled else "关闭"
                logger.info(f"更新账号 {cookie_id} Cookie定时刷新设置: {status}, 间隔 {normalized_interval} 分钟")
                return True
            except ValueError:
                raise
            except Exception as e:
                logger.error(f"更新账号Cookie定时刷新设置失败: {e}")
                return False

    def get_cookie_refresh_settings(self, cookie_id: str) -> Dict[str, Any]:
        """获取账号级定时Cookie刷新设置，缺失时使用保守默认值"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT cookie_refresh_enabled, cookie_refresh_interval_minutes, "
                    "login_method, username, password, password_encrypted, "
                    "COALESCE(has_l3_memory, 0) "
                    "FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return {
                        'enabled': False,
                        'interval_minutes': COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES,
                        'auto_refresh_supported': False,
                    }
                interval = row[1] if row[1] is not None else COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES
                try:
                    interval = self._validate_cookie_refresh_interval(interval)
                except ValueError:
                    interval = COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES
                binding = self.conn.execute(
                    """
                    SELECT 1 FROM account_renewal_bindings AS b
                    JOIN client_browser_devices AS d
                      ON d.device_id = b.device_id AND d.user_id = b.user_id
                    WHERE b.cookie_id = ? AND b.revoked_at IS NULL
                      AND d.revoked_at IS NULL
                    """,
                    (cookie_id,),
                ).fetchone()
                auto_refresh_supported = bool(
                    row[6] or (binding and row[3] and (row[4] or row[5]))
                )
                return {
                    'enabled': bool(row[0]) if row[0] is not None and auto_refresh_supported else False,
                    'interval_minutes': interval,
                    'auto_refresh_supported': auto_refresh_supported,
                }
            except Exception as e:
                logger.error(f"获取账号Cookie定时刷新设置失败: {e}")
                return {
                    'enabled': False,
                    'interval_minutes': COOKIE_REFRESH_DEFAULT_INTERVAL_MINUTES,
                    'auto_refresh_supported': False,
                }

    def mark_l3_memory(self, cookie_id: str, *, ready: bool) -> bool:
        """Record whether this account has a reusable persistent-browser L3 memory."""
        now = time.time() if ready else None
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET has_l3_memory = ?, l3_memory_at = ? WHERE id = ?",
                    (1 if ready else 0, now, cookie_id),
                )
                if cursor.rowcount <= 0:
                    self.conn.rollback()
                    return False
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error("更新账号 L3 记忆标记失败: {}", type(exc).__name__)
                self.conn.rollback()
                return False

    def get_l3_keepalive_enabled(self, cookie_id: str) -> bool:
        """该账号是否单独开启 L3 主动保活（按号灰度，与全局开关取或）。

        存在的意义是灰度：保活会用浏览器打 passport，只有配了住宅代理的号
        才适合先开，全局一刀切会让没配代理的号从机房 IP 出去。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT COALESCE(l3_keepalive_enabled, 0) FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                row = cursor.fetchone()
                return bool(row[0]) if row else False
            except Exception as exc:
                logger.error("读取账号L3保活开关失败: {}", type(exc).__name__)
                return False

    def set_l3_keepalive_enabled(self, cookie_id: str, enabled: bool) -> bool:
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET l3_keepalive_enabled = ? WHERE id = ?",
                    (1 if enabled else 0, cookie_id),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as exc:
                logger.error("写入账号L3保活开关失败: {}", type(exc).__name__)
                return False

    def get_account_proxy_config(self, cookie_id: str) -> Optional[Dict[str, str]]:
        """返回登录路径直接可用的代理配置（含解密后的密码）。

        仅当账号 proxy_enabled=1 且填了 proxy_server 时返回；否则返回 None
        表示直连（保持未接入前的原行为）。密码字段为明文，仅供进程内使用。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "SELECT proxy_enabled, proxy_server, proxy_username, "
                    "proxy_password_encrypted, proxy_bypass, proxy_region "
                    "FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                row = cursor.fetchone()
            except Exception as exc:
                logger.error("读取账号代理配置失败: {}", type(exc).__name__)
                return None
        if not row:
            return None
        enabled = bool(row[0])
        server = str(row[1] or "").strip()
        if not enabled or not server:
            return None
        password = ""
        if row[3]:
            try:
                password = AccountCredentialCipher(self.db_path).decrypt(row[3])
            except ValueError:
                logger.error("账号 {} 代理密码解密失败，按无密码处理", cookie_id)
                password = ""
        config = {
            "server": server,
            "username": str(row[2] or ""),
            "password": password,
            "bypass": str(row[4] or ""),
            "region": str(row[5] or ""),
        }
        return config

    def set_account_proxy(
        self,
        cookie_id: str,
        *,
        server: str,
        username: str = "",
        password: Optional[str] = None,
        bypass: str = "",
        region: str = "",
        enabled: Optional[bool] = None,
    ) -> bool:
        """保存/更新账号代理配置。

        password=None 表示保留原密码不变；password='' 表示清空密码。
        enabled=None 时按是否填了 server 自动推断（填了即启用）。
        """
        server = str(server or "").strip()
        if enabled is None:
            enabled = bool(server)
        fields = [
            "proxy_enabled = ?",
            "proxy_server = ?",
            "proxy_username = ?",
            "proxy_bypass = ?",
            "proxy_region = ?",
        ]
        values: list[Any] = [
            1 if enabled else 0,
            server,
            str(username or ""),
            str(bypass or ""),
            str(region or ""),
        ]
        if password is not None:
            encrypted = (
                AccountCredentialCipher(self.db_path).encrypt(password) if password else ""
            )
            fields.append("proxy_password_encrypted = ?")
            values.append(encrypted)
            fields.append("proxy_password_encryption_version = ?")
            values.append(ACCOUNT_PASSWORD_ENCRYPTION_VERSION if password else 0)
        values.append(cookie_id)
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    f"UPDATE cookies SET {', '.join(fields)} WHERE id = ?",
                    tuple(values),
                )
                if cursor.rowcount <= 0:
                    self.conn.rollback()
                    return False
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error("保存账号代理配置失败: {}", type(exc).__name__)
                self.conn.rollback()
                return False

    def record_proxy_probe(
        self,
        cookie_id: str,
        *,
        ip: str = "",
        status: str = "",
    ) -> bool:
        """记录一次代理出口自检结果（出口 IP + 状态 + 时间）。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET proxy_last_ip = ?, proxy_last_status = ?, "
                    "proxy_last_check_at = ? WHERE id = ?",
                    (str(ip or ""), str(status or ""), time.time(), cookie_id),
                )
                if cursor.rowcount <= 0:
                    self.conn.rollback()
                    return False
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error("记录代理自检结果失败: {}", type(exc).__name__)
                self.conn.rollback()
                return False

    def update_account_session_refresh(
        self,
        cookie_id: str,
        *,
        state: str,
        trigger: str = '',
        message: str = '',
        error_code: str = '',
        verification_image_path: str = '',
        expires_at: float = None,
    ) -> bool:
        allowed_states = {
            'idle', 'action_required', 'refreshing', 'verification_required',
            'success', 'failed', 'timeout', 'cancelled', 'manual_reauth_required',
        }
        if state not in allowed_states:
            raise ValueError(f"不支持的刷新状态: {state}")
        now = time.time()
        schedule_client_task = False
        task_owner_user_id = 0
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if state == 'manual_reauth_required':
                    binding = cursor.execute(
                        """
                        SELECT c.user_id
                        FROM cookies AS c
                        JOIN account_renewal_bindings AS b
                          ON b.cookie_id = c.id AND b.user_id = c.user_id
                        JOIN client_browser_devices AS d
                          ON d.device_id = b.device_id AND d.user_id = b.user_id
                        WHERE c.id = ? AND c.cookie_refresh_enabled = 1
                          AND b.revoked_at IS NULL AND d.revoked_at IS NULL
                        """,
                        (cookie_id,),
                    ).fetchone()
                    if binding:
                        state = 'refreshing'
                        message = '已等待绑定的当前设备领取续期任务'
                        error_code = 'client_device_renewal_pending'
                        expires_at = now + RENEWAL_TASK_TTL_SECONDS
                        schedule_client_task = True
                        task_owner_user_id = int(binding[0])
                self._execute_sql(
                    cursor,
                    "SELECT state, last_success_at, started_at "
                    "FROM account_session_refresh_status WHERE cookie_id = ?",
                    (cookie_id,),
                )
                existing = cursor.fetchone()
                if state == 'manual_reauth_required' and existing and existing[0] == state:
                    return True
                last_success_at = now if state == 'success' else (existing[1] if existing else None)
                started_at = now if state == 'refreshing' else (existing[2] if existing else now)
                image_path = verification_image_path if state == 'verification_required' else ''
                self._execute_sql(cursor, '''
                    INSERT INTO account_session_refresh_status (
                        cookie_id, state, trigger, message, error_code,
                        verification_image_path, started_at, last_attempt_at,
                        last_success_at, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cookie_id) DO UPDATE SET
                        state = excluded.state,
                        trigger = excluded.trigger,
                        message = excluded.message,
                        error_code = excluded.error_code,
                        verification_image_path = excluded.verification_image_path,
                        started_at = excluded.started_at,
                        last_attempt_at = excluded.last_attempt_at,
                        last_success_at = excluded.last_success_at,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                ''', (
                    cookie_id, state, trigger, message[:240], error_code[:80], image_path,
                    started_at, now, last_success_at, expires_at, now,
                ))
                self.conn.commit()
                if schedule_client_task:
                    try:
                        self.create_client_renewal_task(
                            user_id=task_owner_user_id,
                            cookie_id=cookie_id,
                            trigger=trigger or 'runtime_expired',
                            now=now,
                        )
                    except ClientBrowserError as task_error:
                        if task_error.error_code != 'renewal_task_exists':
                            raise
                return True
            except Exception:
                self.conn.rollback()
                raise

    def get_account_session_refresh(self, cookie_id: str) -> Dict[str, Any]:
        with self.lock:
            cursor = self.conn.cursor()
            self._execute_sql(cursor, '''
                SELECT state, trigger, message, error_code, verification_image_path,
                       started_at, last_attempt_at, last_success_at, expires_at, updated_at
                FROM account_session_refresh_status WHERE cookie_id = ?
            ''', (cookie_id,))
            row = cursor.fetchone()
            self._execute_sql(
                cursor,
                "SELECT last_expired_at FROM cookies WHERE id = ?",
                (cookie_id,),
            )
            expiry_row = cursor.fetchone()
        last_expired_at = expiry_row[0] if expiry_row else None
        if not row:
            return {
                'state': 'idle', 'trigger': '', 'message': '', 'error_code': '',
                'verification_image_url': '', 'started_at': None, 'last_attempt_at': None,
                'last_success_at': None, 'expires_at': None, 'updated_at': None,
                'last_expired_at': last_expired_at,
            }
        image_path = (row[4] or '').replace('\\', '/')
        image_url = f"/{image_path}" if image_path.startswith('static/uploads/images/') else ''
        return {
            'state': row[0],
            'trigger': row[1],
            'message': row[2],
            'error_code': row[3],
            'verification_image_url': image_url,
            'started_at': row[5],
            'last_attempt_at': row[6],
            'last_success_at': row[7],
            'expires_at': row[8],
            'updated_at': row[9],
            'last_expired_at': last_expired_at,
        }

    def update_auto_confirm(self, cookie_id: str, auto_confirm: bool) -> bool:
        """更新Cookie的自动确认发货设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET auto_confirm = ? WHERE id = ?", (int(auto_confirm), cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 自动确认发货设置: {'开启' if auto_confirm else '关闭'}")
                return True
            except Exception as e:
                logger.error(f"更新自动确认发货设置失败: {e}")
                return False

    def update_auto_rate(
        self,
        cookie_id: str,
        user_id: int,
        enabled: bool,
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Owner-scoped opt-in; enabling starts a new-order-only boundary."""
        enabled_at = float(time.time() if now is None else now)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                row = cursor.execute(
                    "SELECT auto_rate_enabled FROM cookies WHERE id = ? AND user_id = ?",
                    (str(cookie_id), int(user_id)),
                ).fetchone()
                if not row:
                    return False
                if bool(row[0]) == bool(enabled):
                    return True
                cursor.execute(
                    "UPDATE cookies SET auto_rate_enabled = ?, auto_rate_enabled_at = ? "
                    "WHERE id = ? AND user_id = ?",
                    (
                        int(bool(enabled)),
                        enabled_at if enabled else None,
                        str(cookie_id),
                        int(user_id),
                    ),
                )
                if not enabled:
                    cursor.execute(
                        "UPDATE order_auto_ratings SET state = 'cancelled', "
                        "result_code = 'account_disabled', updated_at = ? "
                        "WHERE cookie_id = ? AND user_id = ? AND state = 'scheduled'",
                        (enabled_at, str(cookie_id), int(user_id)),
                    )
                self.conn.commit()
                return True
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"更新自动好评设置失败: {type(exc).__name__}")
                return False

    def get_auto_rate_settings(
        self,
        cookie_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            params: List[Any] = [str(cookie_id)]
            owner_clause = ""
            if user_id is not None:
                owner_clause = " AND user_id = ?"
                params.append(int(user_id))
            row = self.conn.execute(
                "SELECT auto_rate_enabled, auto_rate_enabled_at FROM cookies "
                f"WHERE id = ?{owner_clause}",
                tuple(params),
            ).fetchone()
            if not row:
                return None
            counts = {
                str(state): int(count)
                for state, count in self.conn.execute(
                    "SELECT state, COUNT(*) FROM order_auto_ratings "
                    f"WHERE cookie_id = ?{owner_clause} GROUP BY state",
                    tuple(params),
                ).fetchall()
            }
        return {
            "enabled": bool(row[0]),
            "enabled_at": row[1],
            "pending_count": counts.get("scheduled", 0) + counts.get("submitting", 0),
            "success_count": counts.get("succeeded", 0),
            "failed_count": counts.get("failed", 0),
            "needs_reconcile_count": counts.get("needs_reconcile", 0),
        }

    def get_auto_rate_enabled_accounts(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, value, user_id, xianyu_unb, browser_user_agent, "
                "cookie_revision, auto_rate_enabled_at FROM cookies "
                "WHERE auto_rate_enabled = 1 AND auto_rate_enabled_at IS NOT NULL "
                "ORDER BY id"
            ).fetchall()
        return [
            {
                "cookie_id": str(row[0]),
                "cookie_string": str(row[1] or ""),
                "user_id": int(row[2]),
                "xianyu_unb": str(row[3] or ""),
                "browser_user_agent": str(row[4] or ""),
                "cookie_revision": int(row[5] or 0),
                "enabled_at": float(row[6]),
            }
            for row in rows
        ]

    def schedule_auto_rate_task(
        self,
        *,
        user_id: int,
        cookie_id: str,
        order_id: str,
        item_title: str,
        order_created_at: float,
        due_at: float,
        now: Optional[float] = None,
        allow_historical: bool = False,
    ) -> bool:
        """Insert once, while ownership and opt-in checks still hold.

        Historical backfill is opt-in at the caller; normal scheduler callers
        keep the enable-time boundary by default.
        """
        created_at = float(time.time() if now is None else now)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO order_auto_ratings ("
                    "user_id, cookie_id, order_id, item_title, order_created_at, "
                    "due_at, created_at, updated_at"
                    ") SELECT ?, ?, ?, ?, ?, ?, ?, ? FROM cookies "
                    "WHERE id = ? AND user_id = ? AND auto_rate_enabled = 1 "
                    "AND auto_rate_enabled_at IS NOT NULL "
                    "AND (? = 1 OR ? >= auto_rate_enabled_at)",
                    (
                        int(user_id), str(cookie_id), str(order_id),
                        str(item_title or "")[:200], float(order_created_at),
                        float(due_at), created_at, created_at,
                        str(cookie_id), int(user_id), int(bool(allow_historical)),
                        float(order_created_at),
                    ),
                )
                inserted = cursor.rowcount == 1
                self.conn.commit()
                return inserted
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"创建自动好评任务失败: {type(exc).__name__}")
                return False

    def claim_due_auto_rate_task(self, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        claimed_at = float(time.time() if now is None else now)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                row = cursor.execute(
                    "SELECT r.id, r.user_id, r.cookie_id, r.order_id, r.item_title, "
                    "r.feedback, r.attempt_count FROM order_auto_ratings AS r "
                    "JOIN cookies AS c ON c.id = r.cookie_id AND c.user_id = r.user_id "
                    "WHERE r.state = 'scheduled' AND r.due_at <= ? "
                    "AND c.auto_rate_enabled = 1 ORDER BY r.due_at, r.id LIMIT 1",
                    (claimed_at,),
                ).fetchone()
                if not row:
                    return None
                cursor.execute(
                    "UPDATE order_auto_ratings SET state = 'submitting', "
                    "attempt_count = attempt_count + 1, updated_at = ? "
                    "WHERE id = ? AND state = 'scheduled'",
                    (claimed_at, int(row[0])),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return None
                self.conn.commit()
                return {
                    "id": int(row[0]),
                    "user_id": int(row[1]),
                    "cookie_id": str(row[2]),
                    "order_id": str(row[3]),
                    "item_title": str(row[4] or ""),
                    "feedback": str(row[5] or ""),
                    "attempt_count": int(row[6] or 0) + 1,
                }
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"领取自动好评任务失败: {type(exc).__name__}")
                return None

    def set_auto_rate_feedback(self, task_id: int, feedback: str) -> bool:
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    "UPDATE order_auto_ratings SET feedback = ?, updated_at = ? "
                    "WHERE id = ? AND state = 'submitting'",
                    (str(feedback or "")[:200], time.time(), int(task_id)),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self.conn.rollback()
                return False

    def mark_auto_rate_submission_started(
        self,
        task_id: int,
        *,
        now: Optional[float] = None,
    ) -> bool:
        started_at = float(time.time() if now is None else now)
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    "UPDATE order_auto_ratings SET submitted_at = ?, updated_at = ? "
                    "WHERE id = ? AND state = 'submitting' AND submitted_at IS NULL "
                    "AND EXISTS (SELECT 1 FROM cookies AS c "
                    "WHERE c.id = order_auto_ratings.cookie_id "
                    "AND c.user_id = order_auto_ratings.user_id "
                    "AND c.auto_rate_enabled = 1)",
                    (started_at, started_at, int(task_id)),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self.conn.rollback()
                return False

    def finish_auto_rate_task(
        self,
        task_id: int,
        *,
        state: str,
        result_code: str,
        error: str = "",
        response: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> bool:
        if state not in {"succeeded", "failed", "needs_reconcile"}:
            raise ValueError("invalid auto-rate terminal state")
        finished_at = float(time.time() if now is None else now)
        response_json = json.dumps(response or {}, ensure_ascii=False, separators=(",", ":"))[:4000]
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    "UPDATE order_auto_ratings SET state = ?, result_code = ?, "
                    "last_error = ?, response_json = ?, updated_at = ? "
                    "WHERE id = ? AND state = 'submitting'",
                    (
                        state, str(result_code or "")[:100], str(error or "")[:500],
                        response_json, finished_at, int(task_id),
                    ),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"完成自动好评任务失败: {type(exc).__name__}")
                return False

    def reconcile_interrupted_auto_rate_tasks(self, *, now: Optional[float] = None) -> int:
        """Replay only work that stopped before its durable pre-POST marker."""
        updated_at = float(time.time() if now is None else now)
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE order_auto_ratings SET state = 'scheduled', "
                "result_code = 'service_restarted_before_submit', "
                "last_error = '', due_at = ?, updated_at = ? "
                "WHERE state = 'submitting' AND submitted_at IS NULL",
                (updated_at, updated_at),
            )
            cursor.execute(
                "UPDATE order_auto_ratings SET state = 'needs_reconcile', "
                "result_code = 'service_restarted', "
                "last_error = '提交开始后服务重启，请人工核对平台评价状态', updated_at = ? "
                "WHERE state = 'submitting' AND submitted_at IS NOT NULL",
                (updated_at,),
            )
            count = cursor.rowcount
            self.conn.commit()
            return int(count)

    def update_cookie_remark(self, cookie_id: str, remark: str) -> bool:
        """更新Cookie的备注"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET remark = ? WHERE id = ?", (remark, cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 备注: {remark}")
                return True
            except Exception as e:
                logger.error(f"更新账号备注失败: {e}")
                return False

    def update_cookie_pause_duration(self, cookie_id: str, pause_duration: int) -> bool:
        """更新Cookie的自动回复暂停时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "UPDATE cookies SET pause_duration = ? WHERE id = ?", (pause_duration, cookie_id))
                self.conn.commit()
                logger.info(f"更新账号 {cookie_id} 自动回复暂停时间: {pause_duration}分钟")
                return True
            except Exception as e:
                logger.error(f"更新账号自动回复暂停时间失败: {e}")
                return False

    def get_cookie_pause_duration(self, cookie_id: str) -> int:
        """获取Cookie的自动回复暂停时间"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT pause_duration FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    if result[0] is None:
                        logger.warning(f"账号 {cookie_id} 的pause_duration为NULL，使用默认值10分钟并修复数据库")
                        # 修复数据库中的NULL值
                        self._execute_sql(cursor, "UPDATE cookies SET pause_duration = 10 WHERE id = ?", (cookie_id,))
                        self.conn.commit()
                        return 10
                    return result[0]  # 返回实际值，包括0（0表示不暂停）
                else:
                    logger.warning(f"账号 {cookie_id} 未找到记录，使用默认值10分钟")
                    return 10
            except Exception as e:
                logger.error(f"获取账号自动回复暂停时间失败: {e}")
                return 10

    def update_cookie_account_info(
        self,
        cookie_id: str,
        cookie_value: str = None,
        username: str = None,
        password: str = None,
        show_browser: bool = None,
        user_id: int = None,
        browser_user_agent: str = None,
        *,
        login_method: str = None,
        login_validated: bool = False,
        has_l3_memory: bool = None,
    ) -> bool:
        """更新Cookie的账号信息（包括cookie值、用户名、密码和显示浏览器设置）
        如果记录不存在，会先创建记录（需要提供cookie_value和user_id）
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 检查记录是否存在
                self._execute_sql(
                    cursor,
                    "SELECT user_id, xianyu_unb, value FROM cookies WHERE id = ?",
                    (cookie_id,),
                )
                existing_row = cursor.fetchone()
                exists = existing_row is not None
                candidate_unb = self._extract_cookie_unb(cookie_value) if cookie_value is not None else ""

                if not exists:
                    # 记录不存在，需要创建新记录
                    if cookie_value is None:
                        logger.warning(f"账号 {cookie_id} 不存在，且未提供cookie_value，无法创建新记录")
                        return False

                    # 创建新记录必须显式提供归属 user_id：
                    # 禁止回退到 admin，否则被删除/未知归属的账号凭证会被
                    # 静默划给 admin（跨租户凭证接管）
                    if user_id is None:
                        logger.warning(
                            f"账号 {_account_log_reference(cookie_id)} 不存在且未提供归属 user_id，拒绝创建"
                        )
                        return False

                    # 构建插入语句
                    insert_fields = ['id', 'value', 'user_id']
                    insert_values = [cookie_id, cookie_value, user_id]
                    insert_placeholders = ['?', '?', '?']

                    xianyu_unb = candidate_unb
                    if xianyu_unb:
                        insert_fields.append('xianyu_unb')
                        insert_values.append(xianyu_unb)
                        insert_placeholders.append('?')

                    if username is not None:
                        insert_fields.append('username')
                        insert_values.append(username)
                        insert_placeholders.append('?')

                    if password is not None:
                        encrypted_password = AccountCredentialCipher(self.db_path).encrypt(password)
                        insert_fields.extend(['password', 'password_encrypted', 'password_encryption_version'])
                        insert_values.extend(['', encrypted_password, ACCOUNT_PASSWORD_ENCRYPTION_VERSION])
                        insert_placeholders.extend(['?', '?', '?'])

                    if show_browser is not None:
                        insert_fields.append('show_browser')
                        insert_values.append(1 if show_browser else 0)
                        insert_placeholders.append('?')

                    if browser_user_agent is not None:
                        insert_fields.append('browser_user_agent')
                        insert_values.append(str(browser_user_agent or '')[:1000])
                        insert_placeholders.append('?')

                    if login_method is not None:
                        normalized_method = normalize_login_method(login_method)
                        now = time.time()
                        insert_fields.extend(['login_method', 'last_login_at'])
                        insert_values.extend([normalized_method, now])
                        insert_placeholders.extend(['?', '?'])
                        if login_validated:
                            insert_fields.append('last_validated_at')
                            insert_values.append(now)
                            insert_placeholders.append('?')

                    if has_l3_memory is not None:
                        insert_fields.extend(['has_l3_memory', 'l3_memory_at'])
                        insert_values.extend([
                            1 if has_l3_memory else 0,
                            time.time() if has_l3_memory else None,
                        ])
                        insert_placeholders.extend(['?', '?'])

                    sql = f"INSERT INTO cookies ({', '.join(insert_fields)}) VALUES ({', '.join(insert_placeholders)})"
                    self._execute_sql(cursor, sql, tuple(insert_values))
                    self.conn.commit()
                    logger.info(
                        f"创建新账号 {_account_log_reference(cookie_id)} "
                        f"并保存信息成功: {insert_fields}"
                    )
                    return True
                else:
                    if user_id is not None and int(existing_row[0]) != int(user_id):
                        logger.warning("账号信息更新被所有权校验阻止")
                        return False
                    if cookie_value is not None:
                        stored_unb = str(existing_row[1] or '').strip()
                        if not stored_unb:
                            stored_unb = self._extract_cookie_unb(existing_row[2])
                        if stored_unb and candidate_unb != stored_unb:
                            raise AccountIdentityMismatchError()

                    # 记录存在，执行更新
                    # 构建动态SQL更新语句
                    update_fields = []
                    params = []

                    if cookie_value is not None:
                        update_fields.append(
                            "cookie_revision = cookie_revision + CASE WHEN value <> ? THEN 1 ELSE 0 END"
                        )
                        params.append(cookie_value)
                        update_fields.append("value = ?")
                        params.append(cookie_value)
                        if candidate_unb and not stored_unb:
                            update_fields.append("xianyu_unb = ?")
                            params.append(candidate_unb)

                    if username is not None:
                        update_fields.append("username = ?")
                        params.append(username)

                    if password is not None:
                        update_fields.extend([
                            "password = ''",
                            "password_encrypted = ?",
                            "password_encryption_version = ?",
                        ])
                        params.extend([
                            AccountCredentialCipher(self.db_path).encrypt(password),
                            ACCOUNT_PASSWORD_ENCRYPTION_VERSION,
                        ])

                    if show_browser is not None:
                        update_fields.append("show_browser = ?")
                        params.append(1 if show_browser else 0)

                    if browser_user_agent is not None:
                        update_fields.append("browser_user_agent = ?")
                        params.append(str(browser_user_agent or '')[:1000])

                    if login_method is not None:
                        normalized_method = normalize_login_method(login_method)
                        now = time.time()
                        update_fields.extend([
                            "login_method = ?",
                            "last_login_at = ?",
                            "last_expired_at = NULL",
                        ])
                        params.extend([normalized_method, now])
                        if login_validated:
                            update_fields.append("last_validated_at = ?")
                            params.append(now)
                        else:
                            update_fields.append("last_validated_at = NULL")
                        keep_refresh = bool(has_l3_memory) or normalized_method == 'password'
                        if not keep_refresh:
                            update_fields.append("cookie_refresh_enabled = 0")

                    if has_l3_memory is not None:
                        update_fields.extend(["has_l3_memory = ?", "l3_memory_at = ?"])
                        params.extend([
                            1 if has_l3_memory else 0,
                            time.time() if has_l3_memory else None,
                        ])

                    if not update_fields:
                        logger.warning(f"更新账号 {cookie_id} 信息时没有提供任何更新字段")
                        return False

                    params.append(cookie_id)
                    sql = f"UPDATE cookies SET {', '.join(update_fields)} WHERE id = ?"

                    self._execute_sql(cursor, sql, tuple(params))
                    if login_method is not None:
                        self._execute_sql(
                            cursor,
                            "DELETE FROM account_session_refresh_status WHERE cookie_id = ?",
                            (cookie_id,),
                        )
                    self.conn.commit()
                    logger.info(
                        f"更新账号 {_account_log_reference(cookie_id)} "
                        f"信息成功: {update_fields}"
                    )
                    return True
            except AccountIdentityMismatchError:
                self.conn.rollback()
                raise
            except Exception as e:
                logger.error(f"更新账号信息失败: {type(e).__name__}")
                self.conn.rollback()
                return False

    def register_client_browser_device(
        self,
        *,
        user_id: int,
        device_id: str,
        browser_family: str,
        display_name: str,
        signing_public_jwk: Dict[str, Any],
        encryption_public_jwk: Dict[str, Any],
        client_type: str = "extension",
    ) -> Dict[str, Any]:
        normalized_device_id = normalize_device_id(device_id)
        normalized_family = normalize_browser_family(browser_family)
        normalized_client_type = normalize_client_type(client_type)
        signing_jwk = normalize_public_jwk(signing_public_jwk)
        encryption_jwk = normalize_public_jwk(encryption_public_jwk)
        now = time.time()
        with self.lock:
            cursor = self.conn.cursor()
            existing = cursor.execute(
                "SELECT user_id, signing_public_jwk, encryption_public_jwk, "
                "revoked_at, client_type "
                "FROM client_browser_devices WHERE device_id = ?",
                (normalized_device_id,),
            ).fetchone()
            if existing and int(existing[0]) != int(user_id):
                raise ClientBrowserError(
                    "设备已属于其他用户",
                    error_code="device_owner_mismatch",
                    http_status=403,
                )
            serialized_signing = json.dumps(
                signing_jwk, sort_keys=True, separators=(",", ":")
            )
            serialized_encryption = json.dumps(
                encryption_jwk, sort_keys=True, separators=(",", ":")
            )
            if existing and (
                existing[1] != serialized_signing
                or existing[2] != serialized_encryption
            ):
                raise ClientBrowserError(
                    "设备密钥与已注册记录不匹配，请生成新的设备连接",
                    error_code="device_key_mismatch",
                    http_status=409,
                )
            if existing and str(existing[4] or "extension") != normalized_client_type:
                raise ClientBrowserError(
                    "设备连接类型与已注册记录不匹配，请生成新的设备连接",
                    error_code="device_type_mismatch",
                    http_status=409,
                )
            cursor.execute(
                """
                INSERT INTO client_browser_devices (
                    device_id, user_id, browser_family, display_name,
                    client_type,
                    signing_public_jwk, encryption_public_jwk,
                    registered_at, last_seen_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(device_id) DO UPDATE SET
                    browser_family = excluded.browser_family,
                    display_name = excluded.display_name,
                    client_type = excluded.client_type,
                    last_seen_at = excluded.last_seen_at,
                    revoked_at = NULL
                WHERE client_browser_devices.user_id = excluded.user_id
                """,
                (
                    normalized_device_id,
                    int(user_id),
                    normalized_family,
                    str(display_name or "当前设备").strip()[:80],
                    normalized_client_type,
                    serialized_signing,
                    serialized_encryption,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        return {
            "device_id": normalized_device_id,
            "browser_family": normalized_family,
            "client_type": normalized_client_type,
            "display_name": str(display_name or "当前设备").strip()[:80],
            "last_seen_at": now,
            "revoked": False,
        }

    def get_client_browser_device(
        self,
        *,
        user_id: int,
        device_id: str,
        include_public_keys: bool = False,
    ) -> Optional[Dict[str, Any]]:
        normalized_device_id = normalize_device_id(device_id)
        with self.lock:
            row = self.conn.execute(
                """
                SELECT device_id, browser_family, client_type, display_name, registered_at,
                       last_seen_at, revoked_at, signing_public_jwk,
                       encryption_public_jwk
                FROM client_browser_devices
                WHERE device_id = ? AND user_id = ?
                """,
                (normalized_device_id, int(user_id)),
            ).fetchone()
        if not row:
            return None
        result = {
            "device_id": row[0],
            "browser_family": row[1],
            "client_type": row[2],
            "display_name": row[3],
            "registered_at": row[4],
            "last_seen_at": row[5],
            "revoked_at": row[6],
            "revoked": row[6] is not None,
        }
        if include_public_keys:
            result["signing_public_jwk"] = json.loads(row[7])
            result["encryption_public_jwk"] = json.loads(row[8])
        return result

    def list_client_browser_devices(self, user_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT device_id, browser_family, client_type, display_name, registered_at,
                       last_seen_at, revoked_at
                FROM client_browser_devices
                WHERE user_id = ?
                ORDER BY revoked_at IS NOT NULL, last_seen_at DESC
                """,
                (int(user_id),),
            ).fetchall()
        return [
            {
                "device_id": row[0],
                "browser_family": row[1],
                "client_type": row[2],
                "display_name": row[3],
                "registered_at": row[4],
                "last_seen_at": row[5],
                "revoked_at": row[6],
                "revoked": row[6] is not None,
            }
            for row in rows
        ]

    def find_active_client_browser_device(
        self,
        device_id: str,
        *,
        include_public_keys: bool = False,
    ) -> Optional[Dict[str, Any]]:
        normalized_device_id = normalize_device_id(device_id)
        with self.lock:
            row = self.conn.execute(
                """
                SELECT user_id, device_id, browser_family, client_type, display_name,
                       registered_at, last_seen_at, signing_public_jwk,
                       encryption_public_jwk
                FROM client_browser_devices
                WHERE device_id = ? AND revoked_at IS NULL
                """,
                (normalized_device_id,),
            ).fetchone()
        if not row:
            return None
        result = {
            "user_id": int(row[0]),
            "device_id": row[1],
            "browser_family": row[2],
            "client_type": row[3],
            "display_name": row[4],
            "registered_at": row[5],
            "last_seen_at": row[6],
        }
        if include_public_keys:
            result["signing_public_jwk"] = json.loads(row[7])
            result["encryption_public_jwk"] = json.loads(row[8])
        return result

    def get_account_renewal_binding(
        self,
        *,
        user_id: int,
        cookie_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT b.device_id, b.credential_authorized_at, b.bound_at,
                       d.browser_family, d.client_type, d.display_name, d.last_seen_at
                FROM account_renewal_bindings AS b
                JOIN client_browser_devices AS d
                  ON d.device_id = b.device_id AND d.user_id = b.user_id
                WHERE b.cookie_id = ? AND b.user_id = ?
                  AND b.revoked_at IS NULL AND d.revoked_at IS NULL
                """,
                (cookie_id, int(user_id)),
            ).fetchone()
        if not row:
            return None
        return {
            "device_id": row[0],
            "credential_authorized_at": row[1],
            "bound_at": row[2],
            "browser_family": row[3],
            "client_type": row[4],
            "display_name": row[5],
            "last_seen_at": row[6],
        }

    def touch_client_browser_device(self, *, user_id: int, device_id: str) -> bool:
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE client_browser_devices SET last_seen_at = ? "
                "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
                (time.time(), normalize_device_id(device_id), int(user_id)),
            )
            self.conn.commit()
            return cursor.rowcount == 1

    def revoke_client_browser_device(self, *, user_id: int, device_id: str) -> bool:
        normalized_device_id = normalize_device_id(device_id)
        now = time.time()
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            updated = cursor.execute(
                "UPDATE client_browser_devices SET revoked_at = ? "
                "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
                (now, normalized_device_id, int(user_id)),
            ).rowcount
            cursor.execute(
                "UPDATE account_renewal_bindings SET revoked_at = ? "
                "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
                (now, normalized_device_id, int(user_id)),
            )
            cursor.execute(
                "UPDATE client_renewal_tasks SET state = 'cancelled', "
                "completed_at = ?, updated_at = ?, error_code = 'device_revoked', "
                "encrypted_payload_json = '' WHERE device_id = ? AND user_id = ? "
                "AND state IN ('pending', 'claimed', 'action_required', 'validating')",
                (now, now, normalized_device_id, int(user_id)),
            )
            self.conn.commit()
            return updated == 1

    def bind_account_renewal_device(
        self,
        *,
        user_id: int,
        cookie_id: str,
        device_id: str,
        username: str,
        password: str,
        authorized_at: float,
    ) -> Dict[str, Any]:
        normalized_device_id = normalize_device_id(device_id)
        normalized_username = str(username or "").strip()
        secret = str(password or "")
        if not normalized_username or not secret:
            raise ClientBrowserError(
                "续期账号和密码不能为空", error_code="credential_missing"
            )
        now = time.time()
        if abs(now - float(authorized_at)) > 300:
            raise ClientBrowserError(
                "保存密码授权已过期", error_code="credential_authorization_expired"
            )
        encrypted = AccountCredentialCipher(self.db_path).encrypt(secret)
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            owned = cursor.execute(
                "SELECT 1 FROM cookies WHERE id = ? AND user_id = ?",
                (cookie_id, int(user_id)),
            ).fetchone()
            device = cursor.execute(
                "SELECT 1 FROM client_browser_devices WHERE device_id = ? "
                "AND user_id = ? AND revoked_at IS NULL "
                "AND client_type = 'extension'",
                (normalized_device_id, int(user_id)),
            ).fetchone()
            if not owned or not device:
                self.conn.rollback()
                raise ClientBrowserError(
                    "账号或续期设备不匹配",
                    error_code="renewal_binding_mismatch",
                    http_status=403,
                )
            cursor.execute(
                "UPDATE cookies SET username = ?, password = '', "
                "password_encrypted = ?, password_encryption_version = ?, "
                "cookie_refresh_enabled = 1 WHERE id = ? AND user_id = ?",
                (
                    normalized_username, encrypted,
                    ACCOUNT_PASSWORD_ENCRYPTION_VERSION, cookie_id, int(user_id),
                ),
            )
            cursor.execute(
                """
                INSERT INTO account_renewal_bindings (
                    cookie_id, user_id, device_id, credential_authorized_at,
                    bound_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(cookie_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    device_id = excluded.device_id,
                    credential_authorized_at = excluded.credential_authorized_at,
                    bound_at = excluded.bound_at,
                    revoked_at = NULL
                """,
                (cookie_id, int(user_id), normalized_device_id, float(authorized_at), now),
            )
            self.conn.commit()
        return {
            "account_id": cookie_id,
            "device_id": normalized_device_id,
            "bound_at": now,
            "credential_authorized_at": float(authorized_at),
        }

    def create_client_renewal_task(
        self,
        *,
        user_id: int,
        cookie_id: str,
        trigger: str,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        created_at = time.time() if now is None else float(now)
        expires_at = created_at + RENEWAL_TASK_TTL_SECONDS
        task_id = uuid.uuid4().hex
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                """
                SELECT c.username, c.password_encrypted, b.device_id,
                       d.encryption_public_jwk
                FROM cookies AS c
                JOIN account_renewal_bindings AS b
                  ON b.cookie_id = c.id AND b.user_id = c.user_id
                JOIN client_browser_devices AS d
                  ON d.device_id = b.device_id AND d.user_id = b.user_id
                WHERE c.id = ? AND c.user_id = ? AND c.cookie_refresh_enabled = 1
                  AND b.revoked_at IS NULL AND d.revoked_at IS NULL
                  AND d.client_type = 'extension'
                """,
                (cookie_id, int(user_id)),
            ).fetchone()
            if not row or not row[1]:
                self.conn.rollback()
                raise ClientBrowserError(
                    "账号尚未绑定可用续期设备",
                    error_code="client_device_binding_required",
                    http_status=409,
                )
            active = cursor.execute(
                "SELECT task_id, state, expires_at FROM client_renewal_tasks "
                "WHERE cookie_id = ? AND state IN "
                "('pending', 'claimed', 'action_required', 'validating')",
                (cookie_id,),
            ).fetchone()
            if active and float(active[2]) > created_at:
                self.conn.rollback()
                raise ClientBrowserError(
                    "该账号已有续期任务",
                    error_code="renewal_task_exists",
                    http_status=409,
                )
            if active:
                cursor.execute(
                    "UPDATE client_renewal_tasks SET state = 'expired', "
                    "completed_at = ?, updated_at = ?, encrypted_payload_json = '', "
                    "error_code = 'task_expired' WHERE task_id = ?",
                    (created_at, created_at, active[0]),
                )
            context = {
                "version": 1,
                "owner_user_id": int(user_id),
                "device_id": row[2],
                "account_id": cookie_id,
                "task_id": task_id,
                "expires_at": expires_at,
            }
            encrypted_payload = seal_renewal_credential(
                encryption_public_jwk=json.loads(row[3]),
                username=str(row[0] or ""),
                password=AccountCredentialCipher(self.db_path).decrypt(row[1]),
                context=context,
            )
            cursor.execute(
                """
                INSERT INTO client_renewal_tasks (
                    task_id, user_id, cookie_id, device_id, state, trigger,
                    public_context_json, encrypted_payload_json, expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, int(user_id), cookie_id, row[2], str(trigger or "automatic")[:80],
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                    json.dumps(encrypted_payload, sort_keys=True, separators=(",", ":")),
                    expires_at, created_at, created_at,
                ),
            )
            self.conn.commit()
        return {**context, "state": "pending"}

    def claim_client_renewal_task(
        self,
        *,
        user_id: int,
        device_id: str,
        task_id: str,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        claimed_at = time.time() if now is None else float(now)
        normalized_device_id = normalize_device_id(device_id)
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT cookie_id, state, public_context_json, "
                "encrypted_payload_json, expires_at FROM client_renewal_tasks "
                "WHERE task_id = ? AND user_id = ? AND device_id = ?",
                (task_id, int(user_id), normalized_device_id),
            ).fetchone()
            if not row:
                self.conn.rollback()
                raise ClientBrowserError(
                    "续期任务不存在", error_code="renewal_task_not_found", http_status=404
                )
            if float(row[4]) <= claimed_at:
                cursor.execute(
                    "UPDATE client_renewal_tasks SET state = 'expired', "
                    "completed_at = ?, updated_at = ?, encrypted_payload_json = '', "
                    "error_code = 'task_expired' WHERE task_id = ?",
                    (claimed_at, claimed_at, task_id),
                )
                self.conn.commit()
                raise ClientBrowserError(
                    "续期任务已过期", error_code="renewal_task_expired", http_status=410
                )
            if row[1] != "pending" or not row[3]:
                self.conn.rollback()
                raise ClientBrowserError(
                    "续期任务已领取", error_code="renewal_task_already_claimed", http_status=409
                )
            cursor.execute(
                "UPDATE client_renewal_tasks SET state = 'claimed', claimed_at = ?, "
                "updated_at = ?, encrypted_payload_json = '' WHERE task_id = ? "
                "AND state = 'pending'",
                (claimed_at, claimed_at, task_id),
            )
            self.conn.commit()
        return {
            "task_id": task_id,
            "account_id": row[0],
            "state": "claimed",
            "context": json.loads(row[2]),
            "encrypted_payload": json.loads(row[3]),
            "expires_at": row[4],
        }

    def claim_next_client_renewal_task(
        self,
        *,
        user_id: int,
        device_id: str,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        current_time = time.time() if now is None else float(now)
        normalized_device_id = normalize_device_id(device_id)
        with self.lock:
            expired = self.conn.execute(
                "UPDATE client_renewal_tasks SET state = 'expired', "
                "completed_at = ?, updated_at = ?, encrypted_payload_json = '', "
                "error_code = 'task_expired' WHERE user_id = ? AND device_id = ? "
                "AND state = 'pending' AND expires_at <= ?",
                (current_time, current_time, int(user_id), normalized_device_id, current_time),
            )
            row = self.conn.execute(
                "SELECT task_id FROM client_renewal_tasks WHERE user_id = ? "
                "AND device_id = ? AND state = 'pending' AND expires_at > ? "
                "ORDER BY created_at, task_id LIMIT 1",
                (int(user_id), normalized_device_id, current_time),
            ).fetchone()
            if expired.rowcount:
                self.conn.commit()
        if not row:
            return None
        return self.claim_client_renewal_task(
            user_id=user_id,
            device_id=normalized_device_id,
            task_id=row[0],
            now=current_time,
        )

    def set_client_renewal_task_state(
        self,
        *,
        user_id: int,
        device_id: str,
        task_id: str,
        expected_state: str,
        state: str,
        error_code: str = "",
    ) -> bool:
        if state not in {"action_required", "validating", "success", "failed"}:
            raise ValueError("续期任务状态无效")
        now = time.time()
        completed_at = now if state in {"success", "failed"} else None
        time_guard = (
            "expires_at > ?"
            if expected_state == "claimed"
            else "updated_at > ?"
        )
        freshness = now if expected_state == "claimed" else now - 900
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE client_renewal_tasks SET state = ?, error_code = ?, "
                "completed_at = ?, updated_at = ?, encrypted_payload_json = '' "
                "WHERE task_id = ? AND user_id = ? AND device_id = ? "
                f"AND state = ? AND {time_guard}",
                (
                    state, str(error_code or "")[:80], completed_at, now, task_id,
                    int(user_id), normalize_device_id(device_id), expected_state, freshness,
                ),
            )
            self.conn.commit()
            return cursor.rowcount == 1

    def get_client_renewal_task(
        self,
        *,
        user_id: int,
        device_id: str,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute(
                "SELECT task_id, cookie_id, device_id, state, trigger, "
                "public_context_json, claimed_at, completed_at, expires_at, "
                "created_at, updated_at, error_code "
                "FROM client_renewal_tasks WHERE task_id = ? AND user_id = ? "
                "AND device_id = ?",
                (task_id, int(user_id), normalize_device_id(device_id)),
            ).fetchone()
        if not row:
            return None
        return {
            "task_id": row[0],
            "account_id": row[1],
            "device_id": row[2],
            "state": row[3],
            "trigger": row[4],
            "context": json.loads(row[5] or "{}"),
            "claimed_at": row[6],
            "completed_at": row[7],
            "expires_at": row[8],
            "created_at": row[9],
            "updated_at": row[10],
            "error_code": row[11],
        }

    def cancel_active_client_renewal_task(
        self,
        *,
        user_id: int,
        cookie_id: str,
    ) -> bool:
        now = time.time()
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE client_renewal_tasks SET state = 'cancelled', "
                "completed_at = ?, updated_at = ?, encrypted_payload_json = '', "
                "error_code = 'user_cancelled' WHERE user_id = ? AND cookie_id = ? "
                "AND state IN ('pending', 'claimed', 'action_required', 'validating')",
                (now, now, int(user_id), cookie_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def mark_cookie_expired(self, cookie_id: str) -> bool:
        """Record the first expiry for the current login without churning reminder keys."""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET last_expired_at = COALESCE(last_expired_at, ?) "
                    "WHERE id = ?",
                    (time.time(), cookie_id),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as exc:
                logger.error(f"记录账号登录态过期失败: {type(exc).__name__}")
                self.conn.rollback()
                return False

    def mark_cookie_validated(self, cookie_id: str) -> bool:
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "UPDATE cookies SET last_validated_at = ?, last_expired_at = NULL "
                    "WHERE id = ?",
                    (time.time(), cookie_id),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as exc:
                logger.error(f"记录账号登录态验证成功失败: {type(exc).__name__}")
                self.conn.rollback()
                return False

    def get_auto_confirm(self, cookie_id: str) -> bool:
        """获取Cookie的自动确认发货设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT auto_confirm FROM cookies WHERE id = ?", (cookie_id,))
                result = cursor.fetchone()
                if result:
                    return bool(result[0])
                return True  # 默认开启
            except Exception as e:
                logger.error(f"获取自动确认发货设置失败: {e}")
                return True  # 出错时默认开启

    # -------------------- 关键字操作 --------------------
    def save_keywords(self, cookie_id: str, keywords: List[Tuple[str, str]]) -> bool:
        """保存关键字列表，先删除旧数据再插入新数据（向后兼容方法）"""
        # 转换为新格式（不包含item_id）
        keywords_with_item_id = [(keyword, reply, None) for keyword, reply in keywords]
        return self.save_keywords_with_item_id(cookie_id, keywords_with_item_id)

    def save_keywords_with_item_id(self, cookie_id: str, keywords: List[Tuple[str, str, str]]) -> bool:
        """保存关键字列表（包含商品ID），先删除旧数据再插入新数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 先删除该cookie_id的所有关键字
                self._execute_sql(cursor, "DELETE FROM keywords WHERE cookie_id = ?", (cookie_id,))

                # 插入新关键字，使用INSERT OR REPLACE来处理可能的唯一约束冲突
                for keyword, reply, item_id in keywords:
                    # 标准化item_id：空字符串转为NULL
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    try:
                        self._execute_sql(cursor,
                            "INSERT INTO keywords (cookie_id, keyword, reply, item_id) VALUES (?, ?, ?, ?)",
                            (cookie_id, keyword, reply, normalized_item_id))
                    except sqlite3.IntegrityError as ie:
                        # 如果遇到唯一约束冲突，记录详细错误信息
                        item_desc = f"商品ID: {normalized_item_id}" if normalized_item_id else "通用关键词"
                        logger.error(f"关键词唯一约束冲突: Cookie={cookie_id}, 关键词='{keyword}', {item_desc}")
                        raise ie

                self.conn.commit()
                logger.info(f"关键字保存成功: {cookie_id}, {len(keywords)}条")
                return True
            except Exception as e:
                logger.error(f"关键字保存失败: {e}")
                self.conn.rollback()
                return False

    def save_text_keywords_only(self, cookie_id: str, keywords: List[Tuple[str, str, str]]) -> bool:
        """保存文本关键字列表，只删除文本类型的关键词，保留图片关键词"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 检查是否与现有图片关键词冲突
                for keyword, reply, item_id in keywords:
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    # 检查是否存在同名的图片关键词
                    if normalized_item_id:
                        # 有商品ID的情况：检查 (cookie_id, keyword, item_id) 是否存在图片关键词
                        self._execute_sql(cursor,
                            "SELECT type FROM keywords WHERE cookie_id = ? AND keyword = ? AND item_id = ? AND type = 'image'",
                            (cookie_id, keyword, normalized_item_id))
                    else:
                        # 通用关键词的情况：检查 (cookie_id, keyword) 是否存在图片关键词
                        self._execute_sql(cursor,
                            "SELECT type FROM keywords WHERE cookie_id = ? AND keyword = ? AND (item_id IS NULL OR item_id = '') AND type = 'image'",
                            (cookie_id, keyword))

                    if cursor.fetchone():
                        # 存在同名图片关键词，抛出友好的错误信息
                        item_desc = f"商品ID: {normalized_item_id}" if normalized_item_id else "通用关键词"
                        error_msg = f"关键词 '{keyword}' （{item_desc}） 已存在（图片关键词），无法保存为文本关键词"
                        logger.warning(f"文本关键词与图片关键词冲突: Cookie={cookie_id}, 关键词='{keyword}', {item_desc}")
                        raise ValueError(error_msg)

                # 只删除该cookie_id的文本类型关键字，保留图片关键词
                self._execute_sql(cursor,
                    "DELETE FROM keywords WHERE cookie_id = ? AND (type IS NULL OR type = 'text')",
                    (cookie_id,))

                # 插入新的文本关键字
                for keyword, reply, item_id in keywords:
                    # 标准化item_id：空字符串转为NULL
                    normalized_item_id = item_id if item_id and item_id.strip() else None

                    self._execute_sql(cursor,
                        "INSERT INTO keywords (cookie_id, keyword, reply, item_id, type) VALUES (?, ?, ?, ?, 'text')",
                        (cookie_id, keyword, reply, normalized_item_id))

                self.conn.commit()
                logger.info(f"文本关键字保存成功: {cookie_id}, {len(keywords)}条，图片关键词已保留")
                return True
            except ValueError:
                # 重新抛出友好的错误信息
                raise
            except Exception as e:
                logger.error(f"文本关键字保存失败: {e}")
                self.conn.rollback()
                return False

    def get_keywords(self, cookie_id: str) -> List[Tuple[str, str]]:
        """获取指定Cookie的关键字列表（向后兼容方法）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT keyword, reply FROM keywords WHERE cookie_id = ?", (cookie_id,))
                return [(row[0], row[1]) for row in cursor.fetchall()]
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []

    def get_keywords_with_item_id(self, cookie_id: str) -> List[Tuple[str, str, str]]:
        """获取指定Cookie的关键字列表（包含商品ID）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT keyword, reply, item_id FROM keywords WHERE cookie_id = ?", (cookie_id,))
                return [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []

    def check_keyword_duplicate(self, cookie_id: str, keyword: str, item_id: str = None) -> bool:
        """检查关键词是否重复"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if item_id:
                    # 如果有商品ID，检查相同cookie_id、keyword、item_id的组合
                    self._execute_sql(cursor,
                        "SELECT COUNT(*) FROM keywords WHERE cookie_id = ? AND keyword = ? AND item_id = ?",
                        (cookie_id, keyword, item_id))
                else:
                    # 如果没有商品ID，检查相同cookie_id、keyword且item_id为空的组合
                    self._execute_sql(cursor,
                        "SELECT COUNT(*) FROM keywords WHERE cookie_id = ? AND keyword = ? AND (item_id IS NULL OR item_id = '')",
                        (cookie_id, keyword))

                count = cursor.fetchone()[0]
                return count > 0
            except Exception as e:
                logger.error(f"检查关键词重复失败: {e}")
                return False

    def save_image_keyword(self, cookie_id: str, keyword: str, image_url: str, item_id: str = None) -> bool:
        """保存图片关键词（调用前应先检查重复）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 标准化item_id：空字符串转为NULL
                normalized_item_id = item_id if item_id and item_id.strip() else None

                # 直接插入图片关键词（重复检查应在调用前完成）
                self._execute_sql(cursor,
                    "INSERT INTO keywords (cookie_id, keyword, reply, item_id, type, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                    (cookie_id, keyword, '', normalized_item_id, 'image', image_url))

                self.conn.commit()
                logger.info(f"图片关键词保存成功: {cookie_id}, 关键词: {keyword}, 图片: {image_url}")
                return True
            except Exception as e:
                logger.error(f"图片关键词保存失败: {e}")
                self.conn.rollback()
                return False

    def get_keywords_with_type(self, cookie_id: str) -> List[Dict[str, any]]:
        """获取指定Cookie的关键字列表（包含类型信息）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor,
                    "SELECT keyword, reply, item_id, type, image_url FROM keywords WHERE cookie_id = ?",
                    (cookie_id,))

                results = []
                for row in cursor.fetchall():
                    keyword_data = {
                        'keyword': row[0],
                        'reply': row[1],
                        'item_id': row[2],
                        'type': row[3] or 'text',  # 默认为text类型
                        'image_url': row[4]
                    }
                    results.append(keyword_data)

                return results
            except Exception as e:
                logger.error(f"获取关键字失败: {e}")
                return []

    def update_keyword_image_url(self, cookie_id: str, keyword: str, new_image_url: str) -> bool:
        """更新关键词的图片URL"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 更新图片URL
                self._execute_sql(cursor,
                    "UPDATE keywords SET image_url = ? WHERE cookie_id = ? AND keyword = ? AND type = 'image'",
                    (new_image_url, cookie_id, keyword))

                self.conn.commit()

                # 检查是否有行被更新
                if cursor.rowcount > 0:
                    logger.info(f"关键词图片URL更新成功: {cookie_id}, 关键词: {keyword}, 新URL: {new_image_url}")
                    return True
                else:
                    logger.warning(f"未找到匹配的图片关键词: {cookie_id}, 关键词: {keyword}")
                    return False

            except Exception as e:
                logger.error(f"更新关键词图片URL失败: {e}")
                self.conn.rollback()
                return False

    def delete_keyword_by_index(self, cookie_id: str, index: int) -> bool:
        """根据索引删除关键词"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 先获取所有关键词
                self._execute_sql(cursor,
                    "SELECT rowid FROM keywords WHERE cookie_id = ? ORDER BY rowid",
                    (cookie_id,))
                rows = cursor.fetchall()

                if 0 <= index < len(rows):
                    rowid = rows[index][0]
                    self._execute_sql(cursor, "DELETE FROM keywords WHERE rowid = ?", (rowid,))
                    self.conn.commit()
                    logger.info(f"删除关键词成功: {cookie_id}, 索引: {index}")
                    return True
                else:
                    logger.warning(f"关键词索引超出范围: {index}")
                    return False

            except Exception as e:
                logger.error(f"删除关键词失败: {e}")
                self.conn.rollback()
                return False


    def get_all_keywords(self, user_id: int = None) -> Dict[str, List[Tuple[str, str]]]:
        """获取所有Cookie的关键字（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute("""
                    SELECT k.cookie_id, k.keyword, k.reply
                    FROM keywords k
                    JOIN cookies c ON k.cookie_id = c.id
                    WHERE c.user_id = ?
                    """, (user_id,))
                else:
                    self._execute_sql(cursor, "SELECT cookie_id, keyword, reply FROM keywords")

                result = {}
                for row in cursor.fetchall():
                    cookie_id, keyword, reply = row
                    if cookie_id not in result:
                        result[cookie_id] = []
                    result[cookie_id].append((keyword, reply))

                return result
            except Exception as e:
                logger.error(f"获取所有关键字失败: {e}")
                return {}

    def save_cookie_status(self, cookie_id: str, enabled: bool):
        """保存Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO cookie_status (cookie_id, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (cookie_id, enabled))
                self.conn.commit()
                logger.debug(f"保存Cookie状态: {cookie_id} -> {'启用' if enabled else '禁用'}")
            except Exception as e:
                logger.error(f"保存Cookie状态失败: {e}")
                raise

    def get_cookie_status(self, cookie_id: str) -> bool:
        """获取Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT enabled FROM cookie_status WHERE cookie_id = ?', (cookie_id,))
                result = cursor.fetchone()
                return bool(result[0]) if result else True  # 默认启用
            except Exception as e:
                logger.error(f"获取Cookie状态失败: {e}")
                return True  # 出错时默认启用

    def get_all_cookie_status(self) -> Dict[str, bool]:
        """获取所有Cookie的启用状态"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT cookie_id, enabled FROM cookie_status')

                result = {}
                for row in cursor.fetchall():
                    cookie_id, enabled = row
                    result[cookie_id] = bool(enabled)

                return result
            except Exception as e:
                logger.error(f"获取所有Cookie状态失败: {e}")
                return {}

    # -------------------- AI回复设置操作 --------------------
    def save_ai_reply_settings(self, cookie_id: str, settings: dict) -> bool:
        """保存AI回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                existing = cursor.execute(
                    "SELECT provider_profile_id FROM ai_reply_settings WHERE cookie_id = ?", (cookie_id,)
                ).fetchone()
                provider_profile_id = settings.get(
                    'provider_profile_id', existing[0] if existing else None
                )
                cursor.execute('''
                INSERT OR REPLACE INTO ai_reply_settings
                (cookie_id, ai_enabled, provider_profile_id, model_name, api_key, base_url,
                 max_discount_percent, max_discount_amount, max_bargain_rounds,
                 custom_prompts, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    cookie_id,
                    settings.get('ai_enabled', False),
                    provider_profile_id,
                    settings.get('model_name', 'deepseek-v4-flash'),
                    settings.get('api_key', ''),
                    settings.get('base_url', 'https://api.deepseek.com'),
                    settings.get('max_discount_percent', 10),
                    settings.get('max_discount_amount', 100),
                    settings.get('max_bargain_rounds', 3),
                    settings.get('custom_prompts', '')
                ))
                self.conn.commit()
                logger.debug(f"AI回复设置保存成功: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"保存AI回复设置失败: {e}")
                self.conn.rollback()
                return False

    def get_ai_reply_settings(self, cookie_id: str) -> dict:
        """获取AI回复设置

        优先使用账号级别的设置，如果账号没有配置api_key/base_url/model_name，
        则从系统设置中读取全局AI配置作为默认值
        """
        # 默认值常量与批量查询共用同一份（模块级），避免两处判定漂移。
        DEFAULT_BASE_URL = AI_DEFAULT_BASE_URL
        DEFAULT_MODEL = AI_DEFAULT_MODEL

        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT ai_enabled, model_name, api_key, base_url,
                       max_discount_percent, max_discount_amount, max_bargain_rounds,
                       custom_prompts, provider_profile_id
                FROM ai_reply_settings WHERE cookie_id = ?
                ''', (cookie_id,))

                result = cursor.fetchone()

                # 获取系统级别的AI设置作为默认值
                system_api_key = self.get_system_setting('ai_api_key') or ''
                system_base_url = self.get_system_setting('ai_api_url') or DEFAULT_BASE_URL
                system_model = self.get_system_setting('ai_model') or DEFAULT_MODEL

                if result:
                    provider_profile_id = result[8]
                    if provider_profile_id:
                        account = self.get_cookie_details(cookie_id) or {}
                        profile = self.get_ai_provider_profile(
                            provider_profile_id, account.get('user_id'), include_secret=True
                        )
                        if profile:
                            return {
                                'ai_enabled': bool(result[0]),
                                'provider_profile_id': provider_profile_id,
                                'provider_name': profile['name'],
                                'provider_type': profile['provider_type'],
                                'provider_status': profile['verification_status'],
                                'model_name': result[1] or profile['default_model'],
                                'api_key': profile['api_key'],
                                'base_url': profile['base_url'],
                                'max_discount_percent': result[4],
                                'max_discount_amount': result[5],
                                'max_bargain_rounds': result[6],
                                'custom_prompts': result[7]
                            }
                    # 账号有设置，但如果api_key/base_url/model_name为空或等于默认值，使用系统设置
                    account_model = result[1]
                    account_api_key = result[2]
                    account_base_url = result[3]

                    # 账号无自有 Key 时优先回退站级共享配置（admin 默认平台），
                    # 让代理账号直接消耗主站的 Key；仍无则走系统全局设置。
                    if not account_api_key:
                        site_profile = self.get_site_default_ai_provider_profile(include_secret=True)
                        if site_profile and site_profile.get('api_key'):
                            # 账号残留的历史默认模型名不可信，Key/URL/模型必须整体来自站级配置。
                            site_model, site_base_url = resolve_ai_model_and_base_url(
                                account_model, '',
                                site_profile['default_model'], site_profile['base_url']
                            )
                            # provider_profile_id 恒为 None：站级 profile 不属于该用户，
                            # 透出真实 id 会被 PUT 接口的属主校验拒绝、并污染前端绑定下拉。
                            return {
                                'ai_enabled': bool(result[0]),
                                'provider_profile_id': None,
                                'provider_name': site_profile['name'],
                                'provider_type': site_profile['provider_type'],
                                'provider_status': site_profile['verification_status'],
                                'model_name': site_model,
                                'api_key': site_profile['api_key'],
                                'base_url': site_base_url,
                                'api_key_source': 'site',
                                'max_discount_percent': result[4],
                                'max_discount_amount': result[5],
                                'max_bargain_rounds': result[6],
                                'custom_prompts': result[7]
                            }

                    # 如果账号值为空或等于硬编码默认值，则使用系统设置
                    use_model, use_base_url = resolve_ai_model_and_base_url(
                        account_model, account_base_url, system_model, system_base_url
                    )
                    use_api_key = account_api_key if account_api_key else system_api_key

                    return {
                        'ai_enabled': bool(result[0]),
                        'provider_profile_id': None,
                        'provider_type': 'gemini' if 'gemini' in use_model.lower() else 'openai_compatible',
                        'model_name': use_model,
                        'api_key': use_api_key,
                        'base_url': use_base_url,
                        'max_discount_percent': result[4],
                        'max_discount_amount': result[5],
                        'max_bargain_rounds': result[6],
                        'custom_prompts': result[7]
                    }
                else:
                    # 账号没有设置：优先站级共享配置（admin 默认平台），再退系统全局设置
                    site_profile = self.get_site_default_ai_provider_profile(include_secret=True)
                    if site_profile and site_profile.get('api_key'):
                        return {
                            'ai_enabled': False,
                            'provider_profile_id': None,
                            'provider_name': site_profile['name'],
                            'provider_type': site_profile['provider_type'],
                            'provider_status': site_profile['verification_status'],
                            'model_name': site_profile['default_model'],
                            'api_key': site_profile['api_key'],
                            'base_url': site_profile['base_url'],
                            'api_key_source': 'site',
                            'max_discount_percent': 10,
                            'max_discount_amount': 100,
                            'max_bargain_rounds': 3,
                            'custom_prompts': ''
                        }
                    return {
                        'ai_enabled': False,
                        'provider_profile_id': None,
                        'provider_type': 'gemini' if 'gemini' in system_model.lower() else 'openai_compatible',
                        'model_name': system_model,
                        'api_key': system_api_key,
                        'base_url': system_base_url,
                        'max_discount_percent': 10,
                        'max_discount_amount': 100,
                        'max_bargain_rounds': 3,
                        'custom_prompts': ''
                    }
            except Exception as e:
                logger.error(f"获取AI回复设置失败: {e}")
                return {
                    'ai_enabled': False,
                    'provider_profile_id': None,
                    'provider_type': 'openai_compatible',
                    'model_name': 'deepseek-v4-flash',
                    'api_key': '',
                    'base_url': 'https://api.deepseek.com',
                    'max_discount_percent': 10,
                    'max_discount_amount': 100,
                    'max_bargain_rounds': 3,
                    'custom_prompts': ''
                }

    def get_all_ai_reply_settings(self, user_id: Optional[int] = None) -> Dict[str, dict]:
        """获取账号级AI回复设置原始行

        传入 user_id 时归属过滤直接进 SQL，不再全表取出后在 Python 里筛。
        任何情况下都不返回明文 api_key，只返回“是否已配置”与掩码。
        """
        sql = '''
        SELECT s.cookie_id, s.ai_enabled, s.model_name, s.api_key, s.base_url,
               s.max_discount_percent, s.max_discount_amount, s.max_bargain_rounds,
               s.custom_prompts, s.provider_profile_id
        FROM ai_reply_settings s
        '''
        params: List[Any] = []
        if user_id is not None:
            sql += ' JOIN cookies c ON c.id = s.cookie_id WHERE c.user_id = ?'
            params.append(int(user_id))

        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(sql, params)

                result = {}
                for row in cursor.fetchall():
                    cookie_id = row[0]
                    account_api_key = row[3] or ''
                    result[cookie_id] = {
                        'ai_enabled': bool(row[1]),
                        'provider_profile_id': row[9],
                        'model_name': row[2],
                        'api_key_configured': bool(account_api_key),
                        'api_key_masked': mask_secret_preview(account_api_key),
                        'base_url': row[4],
                        'max_discount_percent': row[5],
                        'max_discount_amount': row[6],
                        'max_bargain_rounds': row[7],
                        'custom_prompts': row[8]
                    }

                return result
            except Exception as e:
                logger.error(f"获取所有AI回复设置失败: {e}")
                return {}

    def get_ai_reply_settings_for_user(self, user_id: int) -> Dict[str, dict]:
        """批量返回某用户全部账号的AI回复展示设置

        查询数固定（账号行 1 次 + 系统设置 3 次 + 平台配置 1 次），不随账号数增长，
        且全程不返回明文密钥。
        """
        rows = self.get_all_ai_reply_settings(user_id)
        if not rows:
            return {}

        system_api_key = self.get_system_setting('ai_api_key') or ''
        system_base_url = self.get_system_setting('ai_api_url') or AI_DEFAULT_BASE_URL
        system_model = self.get_system_setting('ai_model') or AI_DEFAULT_MODEL
        profiles = {
            profile['id']: profile
            for profile in self.list_ai_provider_profiles(user_id)
        }
        # 站级共享配置（admin 默认平台）查一次供全部账号复用，保持查询数固定。
        site_profile = self.get_site_default_ai_provider_profile()

        resolved: Dict[str, dict] = {}
        for cookie_id, row in rows.items():
            profile = profiles.get(row.get('provider_profile_id'))
            settings = {
                'ai_enabled': row['ai_enabled'],
                'max_discount_percent': row['max_discount_percent'],
                'max_discount_amount': row['max_discount_amount'],
                'max_bargain_rounds': row['max_bargain_rounds'],
                'custom_prompts': row['custom_prompts'],
            }
            if profile:
                settings.update({
                    'provider_profile_id': row['provider_profile_id'],
                    'provider_name': profile['name'],
                    'provider_type': profile['provider_type'],
                    'provider_status': profile['verification_status'],
                    'model_name': row['model_name'] or profile['default_model'],
                    'base_url': profile['base_url'],
                    'api_key_source': 'provider',
                    'api_key_masked': profile.get('api_key_masked', ''),
                    'has_effective_api_key': bool(profile.get('api_key_configured')),
                })
            elif not row['api_key_configured'] and site_profile and site_profile.get('api_key_configured'):
                # 无自有 Key → 站级共享配置（与 get_ai_reply_settings 的运行时回退一致）。
                # provider_profile_id 必须为 None：站级 profile 不属于该用户，
                # 返回真实 id 会污染前端绑定下拉与"测试连接"按钮（按属主校验会 404）。
                site_model, site_base_url = resolve_ai_model_and_base_url(
                    row['model_name'], '',
                    site_profile['default_model'], site_profile['base_url']
                )
                settings.update({
                    'provider_profile_id': None,
                    'provider_name': site_profile['name'],
                    'provider_type': site_profile['provider_type'],
                    'provider_status': site_profile['verification_status'],
                    'model_name': site_model,
                    'base_url': site_base_url,
                    'api_key_source': 'site',
                    'api_key_masked': site_profile.get('api_key_masked', ''),
                    'has_effective_api_key': True,
                })
            else:
                use_model, use_base_url = resolve_ai_model_and_base_url(
                    row['model_name'], row['base_url'], system_model, system_base_url
                )
                if row['api_key_configured']:
                    api_key_source = 'account'
                    api_key_masked = row['api_key_masked']
                elif system_api_key:
                    api_key_source = 'global'
                    api_key_masked = mask_secret_preview(system_api_key)
                else:
                    api_key_source = 'missing'
                    api_key_masked = ''
                settings.update({
                    'provider_profile_id': None,
                    'provider_type': 'gemini' if 'gemini' in use_model.lower() else 'openai_compatible',
                    'model_name': use_model,
                    'base_url': use_base_url,
                    'api_key_source': api_key_source,
                    'api_key_masked': api_key_masked,
                    'has_effective_api_key': bool(
                        row['api_key_configured'] or system_api_key
                    ),
                })
            settings['api_key'] = ''
            resolved[cookie_id] = settings

        return resolved

    # -------------------- AI平台配置 --------------------
    def _serialize_ai_provider_profile(self, row, include_secret: bool = False) -> dict:
        from ai_provider_service import decrypt_provider_key, mask_provider_key

        api_key = decrypt_provider_key(row[6]) if row[6] else ''
        profile = {
            'id': row[0],
            'user_id': row[1],
            'name': row[2],
            'provider_type': row[3],
            'preset': row[4],
            'base_url': row[5],
            'default_model': row[7],
            'models': json.loads(row[8] or '[]'),
            'models_cached_at': row[9],
            'verification_status': row[10],
            'verification_message': row[11] or '',
            'last_verified_at': row[12],
            'is_default': bool(row[13]),
            'api_key_configured': bool(api_key),
            'api_key_masked': mask_provider_key(api_key),
            'created_at': row[14],
            'updated_at': row[15],
        }
        if include_secret:
            profile['api_key'] = api_key
        return profile

    def create_ai_provider_profile(self, user_id: int, data: dict) -> int:
        from ai_provider_service import encrypt_provider_key, normalize_provider_models

        with self.lock:
            cursor = self.conn.cursor()
            try:
                if data.get('is_default'):
                    cursor.execute("UPDATE ai_provider_profiles SET is_default = 0 WHERE user_id = ?", (user_id,))
                has_models = 'models' in data
                models = normalize_provider_models(data.get('models')) if has_models else []
                cursor.execute('''
                INSERT INTO ai_provider_profiles
                (user_id, name, provider_type, preset, base_url, api_key_encrypted,
                 default_model, models_cache, models_cached_at, verification_status,
                 verification_message, last_verified_at, is_default)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id,
                    str(data.get('name') or '').strip(),
                    data.get('provider_type', 'openai_compatible'),
                    data.get('preset', 'custom'),
                    str(data.get('base_url') or '').rstrip('/'),
                    encrypt_provider_key(data.get('api_key', '')),
                    str(data.get('default_model') or '').strip(),
                    json.dumps(models, ensure_ascii=False),
                    time.time() if has_models else None,
                    data.get('verification_status', 'unverified'),
                    data.get('verification_message', ''),
                    data.get('last_verified_at'),
                    int(bool(data.get('is_default'))),
                ))
                self.conn.commit()
                return int(cursor.lastrowid)
            except Exception:
                self.conn.rollback()
                raise

    def list_ai_provider_profiles(self, user_id: int) -> List[dict]:
        with self.lock:
            rows = self.conn.execute('''
                SELECT id, user_id, name, provider_type, preset, base_url, api_key_encrypted,
                       default_model, models_cache, models_cached_at, verification_status,
                       verification_message, last_verified_at, is_default, created_at, updated_at
                FROM ai_provider_profiles WHERE user_id = ?
                ORDER BY is_default DESC, name COLLATE NOCASE
            ''', (user_id,)).fetchall()
            return [self._serialize_ai_provider_profile(row) for row in rows]

    def get_ai_provider_profile(self, profile_id: int, user_id: Optional[int], include_secret: bool = False) -> Optional[dict]:
        if not profile_id or user_id is None:
            return None
        with self.lock:
            row = self.conn.execute('''
                SELECT id, user_id, name, provider_type, preset, base_url, api_key_encrypted,
                       default_model, models_cache, models_cached_at, verification_status,
                       verification_message, last_verified_at, is_default, created_at, updated_at
                FROM ai_provider_profiles WHERE id = ? AND user_id = ?
            ''', (profile_id, user_id)).fetchone()
            return self._serialize_ai_provider_profile(row, include_secret) if row else None

    def get_site_admin_user_id(self) -> Optional[int]:
        """站级共享属主：admin 用户 id（与 reply_server.ADMIN_USERNAME 口径一致）。

        代理（子账号）无自有发货规则时回退匹配该用户的规则并消耗其卡密库存
        （产品决策见 2026-08-29 会话：代理完全同步主站）。
        """
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM users WHERE lower(username) = 'admin' LIMIT 1"
            ).fetchone()
            return int(row[0]) if row else None

    def get_site_default_ai_provider_profile(self, include_secret: bool = False) -> Optional[dict]:
        """站级共享 AI 平台配置：admin 账号的默认且已验证的平台配置。

        代理（子账号）未绑定平台配置、也没有自有 Key 时回退到这份配置，
        让代理开箱即用主站的 AI 能力（消耗主站 Key，产品决策见 2026-08-29 会话）。
        管理员判定与 reply_server.ADMIN_USERNAME 一致：username == 'admin'。
        """
        with self.lock:
            row = self.conn.execute('''
                SELECT p.id, p.user_id, p.name, p.provider_type, p.preset, p.base_url,
                       p.api_key_encrypted, p.default_model, p.models_cache, p.models_cached_at,
                       p.verification_status, p.verification_message, p.last_verified_at,
                       p.is_default, p.created_at, p.updated_at
                FROM ai_provider_profiles p
                JOIN users u ON u.id = p.user_id
                WHERE lower(u.username) = 'admin'
                  AND p.is_default = 1
                  AND p.verification_status = 'verified'
                ORDER BY p.id
                LIMIT 1
            ''').fetchone()
            return self._serialize_ai_provider_profile(row, include_secret) if row else None

    def count_ai_provider_references(self, profile_id: int) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM ai_reply_settings WHERE provider_profile_id = ?", (profile_id,)
            ).fetchone()
            return int(row[0] if row else 0)

    def update_ai_provider_profile(self, profile_id: int, user_id: int, data: dict) -> dict:
        from ai_provider_service import encrypt_provider_key, normalize_provider_models
        from settings_service import apply_secret_action

        with self.lock:
            current = self.get_ai_provider_profile(profile_id, user_id, include_secret=True)
            if not current:
                raise ValueError('平台配置不存在')
            key_action = data.get('api_key_action', 'keep')
            api_key = apply_secret_action(current.get('api_key', ''), key_action, data.get('api_key', ''))
            provider_type = data.get('provider_type', current['provider_type'])
            base_url = str(data.get('base_url', current['base_url'])).rstrip('/')
            preset = data.get('preset', current['preset'])
            sensitive_changed = any([
                provider_type != current['provider_type'],
                base_url != current['base_url'],
                preset != current['preset'],
                api_key != current.get('api_key', ''),
            ])
            if sensitive_changed and self.count_ai_provider_references(profile_id):
                raise ValueError('该平台正在被账号使用，请先让账号切换到其他平台再修改连接信息')
            has_models = 'models' in data
            models = normalize_provider_models(data.get('models')) if has_models else current.get('models', [])
            models_cached_at = time.time() if has_models else current.get('models_cached_at')
            if has_models and not models and current.get('models') and not sensitive_changed:
                models = current['models']
                models_cached_at = current.get('models_cached_at')
            if sensitive_changed and not has_models:
                models = []
                models_cached_at = None
            if data.get('is_default'):
                self.conn.execute("UPDATE ai_provider_profiles SET is_default = 0 WHERE user_id = ?", (user_id,))
            self.conn.execute('''
                UPDATE ai_provider_profiles SET
                    name = ?, provider_type = ?, preset = ?, base_url = ?, api_key_encrypted = ?,
                    default_model = ?, models_cache = ?, models_cached_at = ?,
                    verification_status = ?, verification_message = ?,
                    last_verified_at = ?, is_default = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            ''', (
                str(data.get('name', current['name'])).strip(), provider_type, preset, base_url,
                encrypt_provider_key(api_key), str(data.get('default_model', current['default_model'])).strip(),
                json.dumps(models, ensure_ascii=False), models_cached_at,
                'unverified' if sensitive_changed else current['verification_status'],
                '' if sensitive_changed else current['verification_message'],
                None if sensitive_changed else current['last_verified_at'],
                int(bool(data.get('is_default', current['is_default']))), profile_id, user_id,
            ))
            self.conn.commit()
            return self.get_ai_provider_profile(profile_id, user_id)

    def update_ai_provider_verification(self, profile_id: int, user_id: int, status: str, message: str) -> None:
        with self.lock:
            self.conn.execute('''
                UPDATE ai_provider_profiles SET verification_status = ?, verification_message = ?,
                    last_verified_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            ''', (status, str(message or '')[:500], time.time() if status == 'verified' else None, profile_id, user_id))
            self.conn.commit()

    def update_ai_provider_models(self, profile_id: int, user_id: int, models: List[str]) -> None:
        from ai_provider_service import normalize_provider_models

        with self.lock:
            self.conn.execute('''
                UPDATE ai_provider_profiles SET models_cache = ?, models_cached_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            ''', (json.dumps(normalize_provider_models(models), ensure_ascii=False), time.time(), profile_id, user_id))
            self.conn.commit()

    def delete_ai_provider_profile(self, profile_id: int, user_id: int) -> bool:
        with self.lock:
            profile = self.get_ai_provider_profile(profile_id, user_id)
            if not profile:
                return False
            if self.count_ai_provider_references(profile_id):
                raise ValueError('该平台正在被账号使用，不能删除')
            self.conn.execute("DELETE FROM ai_provider_profiles WHERE id = ? AND user_id = ?", (profile_id, user_id))
            self.conn.commit()
            return True

    def ensure_legacy_ai_provider_profiles(self, user_id: int) -> int:
        """Idempotently bind existing account AI settings to encrypted provider profiles."""
        with self.lock:
            cookie_ids = list(self.get_all_cookies(user_id).keys())
            if not cookie_ids:
                return 0
            existing_profiles = self.list_ai_provider_profiles(user_id)
            profile_by_config = {}
            for profile in existing_profiles:
                private = self.get_ai_provider_profile(profile['id'], user_id, include_secret=True)
                profile_by_config[(private['provider_type'], private['base_url'], private.get('api_key', ''))] = private['id']

            migrated = 0
            for cookie_id in cookie_ids:
                row = self.conn.execute(
                    "SELECT provider_profile_id FROM ai_reply_settings WHERE cookie_id = ?", (cookie_id,)
                ).fetchone()
                if row and row[0]:
                    continue
                effective = self.get_ai_reply_settings(cookie_id)
                provider_type = effective.get('provider_type') or ('gemini' if 'gemini' in effective.get('model_name', '').lower() else 'openai_compatible')
                config_key = (provider_type, effective.get('base_url', ''), effective.get('api_key', ''))
                profile_id = profile_by_config.get(config_key)
                if not profile_id:
                    base_name = '现有 AI 配置'
                    suffix = len(existing_profiles) + 1
                    name = base_name if suffix == 1 else f'{base_name} {suffix}'
                    model = effective.get('model_name', '')
                    url = effective.get('base_url', '')
                    preset = 'gemini' if provider_type == 'gemini' else 'deepseek' if 'deepseek' in url.lower() else 'qwen' if 'dashscope' in url.lower() else 'custom'
                    profile_id = self.create_ai_provider_profile(user_id, {
                        'name': name,
                        'provider_type': provider_type,
                        'preset': preset,
                        'base_url': url,
                        'api_key': effective.get('api_key', ''),
                        'default_model': model,
                        'verification_status': 'verified' if effective.get('api_key') else 'unverified',
                        'verification_message': '从现有账号配置安全迁移',
                        'last_verified_at': time.time() if effective.get('api_key') else None,
                        'is_default': not existing_profiles,
                    })
                    existing_profiles.append({'id': profile_id})
                    profile_by_config[config_key] = profile_id

                if row:
                    self.conn.execute(
                        "UPDATE ai_reply_settings SET provider_profile_id = ? WHERE cookie_id = ?",
                        (profile_id, cookie_id),
                    )
                else:
                    self.conn.execute('''
                        INSERT INTO ai_reply_settings
                        (cookie_id, ai_enabled, provider_profile_id, model_name, api_key, base_url)
                        VALUES (?, 0, ?, ?, '', ?)
                    ''', (cookie_id, profile_id, effective.get('model_name', ''), effective.get('base_url', '')))
                migrated += 1
            self.conn.commit()
            return migrated

    # -------------------- AI 对话订单作用域 --------------------
    _AI_CONVERSATION_SOURCES = frozenset({
        "buyer",
        "seller_human",
        "assistant_generated",
        "keyword",
        "system",
        "legacy",
    })
    _AI_CONVERSATION_DELIVERY_STATES = frozenset({
        "legacy",
        "not_applicable",
        "received",
        "recorded",
        "draft",
        "pending",
        "succeeded",
        "failed",
        "ambiguous",
    })

    @classmethod
    def _normalize_ai_conversation_provenance(
        cls,
        role: str,
        source: Optional[str],
        delivery_state: Optional[str],
    ) -> Tuple[str, str]:
        normalized_role = str(role or "").strip().lower()
        source_value = str(source or "").strip().lower()
        if not source_value:
            source_value = {
                "user": "buyer",
                "buyer": "buyer",
                "assistant": "assistant_generated",
                "seller": "seller_human",
                "human": "seller_human",
                "system": "system",
                "keyword": "keyword",
            }.get(normalized_role, "legacy")
        if source_value not in cls._AI_CONVERSATION_SOURCES:
            source_value = "legacy"

        state_value = str(delivery_state or "").strip().lower()
        if not state_value:
            state_value = (
                "draft" if source_value == "assistant_generated" else "not_applicable"
            )
        if state_value not in cls._AI_CONVERSATION_DELIVERY_STATES:
            state_value = "ambiguous"
        return source_value, state_value

    def insert_ai_conversation(
        self,
        cookie_id: str,
        chat_id: str,
        user_id: str,
        item_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        *,
        order_id: Optional[str] = None,
        source: Optional[str] = None,
        delivery_state: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert one conversation row with explicit order/provenance fields.

        ``assistant_generated`` rows default to ``draft``; callers must update
        them to ``succeeded`` only after the platform confirms delivery.
        """
        source_value, state_value = self._normalize_ai_conversation_provenance(
            role, source, delivery_state
        )
        normalized_order_id = str(order_id or "").strip() or None
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO ai_conversations (
                        cookie_id, chat_id, user_id, item_id, order_id,
                        role, content, intent, source, delivery_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(cookie_id),
                        str(chat_id),
                        str(user_id),
                        str(item_id),
                        normalized_order_id,
                        str(role or ""),
                        str(content or ""),
                        intent,
                        source_value,
                        state_value,
                    ),
                )
                conversation_id = int(cursor.lastrowid)
                row = cursor.execute(
                    """
                    SELECT id, cookie_id, chat_id, user_id, item_id, order_id,
                           role, content, intent, bargain_count, source,
                           delivery_state, created_at
                    FROM ai_conversations WHERE id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                self.conn.commit()
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"保存AI对话失败: {type(exc).__name__}")
                return None
        if not row:
            return None
        keys = (
            "id", "cookie_id", "chat_id", "user_id", "item_id", "order_id",
            "role", "content", "intent", "bargain_count", "source",
            "delivery_state", "created_at",
        )
        return dict(zip(keys, row))

    def get_ai_conversations(
        self,
        cookie_id: str,
        chat_id: str,
        item_id: str,
        *,
        order_id: Optional[str] = None,
        limit: int = 20,
        include_unscoped: bool = False,
        trusted_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Read deterministically ordered, optionally order-scoped history."""
        bounded_limit = max(1, min(int(limit), 200))
        params: List[Any] = [str(cookie_id), str(chat_id), str(item_id)]
        where = ["cookie_id = ?", "chat_id = ?", "item_id = ?"]
        normalized_order_id = str(order_id or "").strip()
        if normalized_order_id:
            if include_unscoped:
                where.append("(order_id = ? OR order_id IS NULL)")
            else:
                where.append("order_id = ?")
            params.append(normalized_order_id)
        if trusted_only:
            # Buyer/human/system records are usable conversation evidence;
            # generated assistant text is trusted only after delivery succeeds.
            # Legacy rows stay continuity-only until a caller re-records them
            # with an explicit provenance value.
            where.append(
                "(source IN ('buyer', 'seller_human', 'keyword', 'system') "
                "OR (source = 'assistant_generated' AND delivery_state = 'succeeded'))"
            )
        sql = f"""
            SELECT id, cookie_id, chat_id, user_id, item_id, order_id,
                   role, content, intent, bargain_count, source,
                   delivery_state, created_at
            FROM (
                SELECT id, cookie_id, chat_id, user_id, item_id, order_id,
                       role, content, intent, bargain_count, source,
                       delivery_state, created_at
                FROM ai_conversations
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, id ASC
        """
        params.append(bounded_limit)
        with self.lock:
            try:
                rows = self.conn.execute(sql, tuple(params)).fetchall()
            except Exception as exc:
                logger.error(f"读取AI对话失败: {type(exc).__name__}")
                return []
        keys = (
            "id", "cookie_id", "chat_id", "user_id", "item_id", "order_id",
            "role", "content", "intent", "bargain_count", "source",
            "delivery_state", "created_at",
        )
        return [dict(zip(keys, row)) for row in rows]

    def update_ai_conversation_delivery_state(
        self,
        conversation_id: int,
        delivery_state: str,
        *,
        cookie_id: Optional[str] = None,
    ) -> bool:
        """Set the platform delivery outcome for an AI draft."""
        state_value = str(delivery_state or "").strip().lower()
        if state_value not in self._AI_CONVERSATION_DELIVERY_STATES:
            raise ValueError("无效的AI对话送达状态")
        params: List[Any] = [state_value, int(conversation_id)]
        owner_clause = ""
        if cookie_id is not None:
            owner_clause = " AND cookie_id = ?"
            params.append(str(cookie_id))
        with self.lock:
            try:
                cursor = self.conn.execute(
                    "UPDATE ai_conversations SET delivery_state = ? "
                    f"WHERE id = ?{owner_clause}",
                    tuple(params),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as exc:
                self.conn.rollback()
                logger.error(f"更新AI对话送达状态失败: {type(exc).__name__}")
                return False

    def save_ai_training_rules(self, cookie_id: str, item_id: str, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """新增或恢复启用训练规则，商品规则严格绑定当前商品。"""
        normalized_item_id = str(item_id or '').strip()
        saved = []
        with self.lock:
            cursor = self.conn.cursor()
            try:
                for rule in rules:
                    scope = str(rule.get('scope', 'item')).strip().lower()
                    text = str(rule.get('text', '')).strip()
                    if scope not in {'global', 'item'} or not text:
                        continue
                    rule_item_id = '' if scope == 'global' else normalized_item_id
                    if scope == 'item' and not rule_item_id:
                        raise ValueError('商品级训练规则必须提供 item_id')
                    cursor.execute('''
                    INSERT INTO ai_training_rules
                    (cookie_id, item_id, scope, rule_text, enabled, updated_at)
                    VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(cookie_id, item_id, rule_text) DO UPDATE SET
                        scope = excluded.scope,
                        enabled = 1,
                        updated_at = CURRENT_TIMESTAMP
                    ''', (cookie_id, rule_item_id, scope, text))
                self.conn.commit()
                saved_rules = self.get_ai_training_rules(cookie_id, normalized_item_id, include_disabled=True)
                saved.extend(saved_rules['global_rules'])
                saved.extend(saved_rules['item_rules'])
                return saved
            except Exception:
                self.conn.rollback()
                raise

    def get_ai_training_rules(self, cookie_id: str, item_id: str = '', include_disabled: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """读取全店规则和当前商品规则，不返回其他商品的规则。"""
        normalized_item_id = str(item_id or '').strip()
        with self.lock:
            cursor = self.conn.cursor()
            enabled_clause = '' if include_disabled else 'AND enabled = 1'
            cursor.execute(f'''
            SELECT id, item_id, scope, rule_text, enabled, created_at, updated_at
            FROM ai_training_rules
            WHERE cookie_id = ? AND (item_id = '' OR item_id = ?) {enabled_clause}
            ORDER BY CASE scope WHEN 'global' THEN 0 ELSE 1 END, id ASC
            ''', (cookie_id, normalized_item_id))
            result = {'global_rules': [], 'item_rules': []}
            for row in cursor.fetchall():
                value = {
                    'id': row[0],
                    'item_id': row[1],
                    'scope': row[2],
                    'text': row[3],
                    'enabled': bool(row[4]),
                    'created_at': row[5],
                    'updated_at': row[6],
                }
                result['global_rules' if row[2] == 'global' else 'item_rules'].append(value)
            return result

    def get_ai_training_rule_context(self, cookie_id: str, item_id: str = '') -> Dict[str, Any]:
        """返回规则装载清单，明确区分适用、其他商品和已停用规则。"""
        normalized_item_id = str(item_id or '').strip()
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, item_id, scope, rule_text, enabled, created_at, updated_at
            FROM ai_training_rules
            WHERE cookie_id = ?
            ORDER BY CASE scope WHEN 'global' THEN 0 ELSE 1 END, id ASC
            ''', (cookie_id,))
            applied_rules = []
            excluded_rules = []
            disabled_rules = []
            for row in cursor.fetchall():
                rule = {
                    'id': row[0],
                    'item_id': row[1],
                    'scope': row[2],
                    'text': row[3],
                    'enabled': bool(row[4]),
                    'created_at': row[5],
                    'updated_at': row[6],
                }
                if not rule['enabled']:
                    rule['reason'] = 'disabled'
                    disabled_rules.append(rule)
                elif rule['scope'] == 'global' or rule['item_id'] == normalized_item_id:
                    rule['reason'] = 'applied'
                    applied_rules.append(rule)
                else:
                    rule['reason'] = 'other_item'
                    excluded_rules.append(rule)
            return {
                'applied_rules': applied_rules,
                'excluded_rules': excluded_rules,
                'disabled_rules': disabled_rules,
                'applied_count': len(applied_rules),
                'excluded_count': len(excluded_rules),
                'disabled_count': len(disabled_rules),
                'total_count': len(applied_rules) + len(excluded_rules) + len(disabled_rules),
            }

    def delete_ai_training_rule(self, cookie_id: str, rule_id: int) -> bool:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM ai_training_rules WHERE cookie_id = ? AND id = ?', (cookie_id, rule_id))
            self.conn.commit()
            return cursor.rowcount > 0

    def set_ai_training_rule_enabled(self, cookie_id: str, rule_id: int, enabled: bool) -> bool:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            UPDATE ai_training_rules
            SET enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE cookie_id = ? AND id = ?
            ''', (bool(enabled), cookie_id, rule_id))
            self.conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _knowledge_has_pending(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get('status') == 'pending':
                return True
            return any(DBManager._knowledge_has_pending(item) for item in value.values())
        if isinstance(value, list):
            return any(DBManager._knowledge_has_pending(item) for item in value)
        return False

    def get_ai_item_knowledge_profile(self, cookie_id: str, item_id: str) -> Dict[str, Any]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT draft_json, published_json, source_detail_hash, published_version,
                   draft_updated_at, published_at, created_at
            FROM ai_item_knowledge_profiles
            WHERE cookie_id = ? AND item_id = ?
            ''', (cookie_id, item_id))
            row = cursor.fetchone()
            if not row:
                return {
                    'cookie_id': cookie_id,
                    'item_id': item_id,
                    'draft': {},
                    'published': {},
                    'source_detail_hash': '',
                    'published_version': 0,
                    'draft_updated_at': None,
                    'published_at': None,
                    'created_at': None,
                }
            return {
                'cookie_id': cookie_id,
                'item_id': item_id,
                'draft': json.loads(row[0] or '{}'),
                'published': json.loads(row[1] or '{}'),
                'source_detail_hash': row[2] or '',
                'published_version': int(row[3] or 0),
                'draft_updated_at': row[4],
                'published_at': row[5],
                'created_at': row[6],
            }

    def save_ai_item_knowledge_draft(self, cookie_id: str, item_id: str,
                                     draft: Dict[str, Any], source_detail_hash: str = '') -> Dict[str, Any]:
        if not isinstance(draft, dict):
            raise ValueError('商品知识档案必须是对象')
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('SELECT 1 FROM item_info WHERE cookie_id = ? AND item_id = ?', (cookie_id, item_id))
            if not cursor.fetchone():
                raise ValueError('当前账号中找不到这个商品')
            cursor.execute('''
            INSERT INTO ai_item_knowledge_profiles
            (cookie_id, item_id, draft_json, source_detail_hash, draft_updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                draft_json = excluded.draft_json,
                source_detail_hash = excluded.source_detail_hash,
                draft_updated_at = CURRENT_TIMESTAMP
            ''', (cookie_id, item_id, json.dumps(draft, ensure_ascii=False), source_detail_hash or ''))
            self.conn.commit()
        return self.get_ai_item_knowledge_profile(cookie_id, item_id)

    def _read_ai_item_knowledge_source(self, cursor, cookie_id: str, item_id: str) -> Tuple[Dict[str, Any], str]:
        """读取源商品可搬运的档案：草稿优先，没有草稿才取已发布版本。"""
        cursor.execute('''
        SELECT draft_json, published_json
        FROM ai_item_knowledge_profiles
        WHERE cookie_id = ? AND item_id = ?
        ''', (cookie_id, item_id))
        source_row = cursor.fetchone()
        if not source_row:
            raise ValueError('源商品还没有知识档案')
        source_draft = json.loads(source_row[0] or '{}')
        source_published = json.loads(source_row[1] or '{}')
        source_profile = source_draft or source_published
        if not source_profile:
            raise ValueError('源商品知识档案为空')
        return source_profile, ('draft' if source_draft else 'published')

    def get_ai_item_knowledge_source_kind(self, cookie_id: str, item_id: str) -> str:
        """搬运前预告这次会取哪一份：'draft' / 'published' / ''（没有可搬运内容）。"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                _, source_kind = self._read_ai_item_knowledge_source(cursor, cookie_id, item_id)
            except ValueError:
                return ''
        return source_kind

    def copy_ai_item_knowledge_draft_to_targets(self, source_cookie_id: str, source_item_id: str,
                                                targets: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
        """把源商品档案覆盖到任意账号下的目标商品草稿，不自动发布。

        目标以 (cookie_id, item_id) 二元组寻址，因此支持跨账号；账号归属由路由层校验。
        """
        normalized_targets: List[Tuple[str, str]] = []
        for pair in targets or []:
            target_cookie = str((pair[0] if pair else '') or '').strip()
            target_item = str((pair[1] if pair and len(pair) > 1 else '') or '').strip()
            if not target_cookie or not target_item:
                continue
            if (target_cookie, target_item) == (source_cookie_id, source_item_id):
                continue
            if (target_cookie, target_item) not in normalized_targets:
                normalized_targets.append((target_cookie, target_item))
        with self.lock:
            cursor = self.conn.cursor()
            source_profile, source_kind = self._read_ai_item_knowledge_source(
                cursor, source_cookie_id, source_item_id
            )
            profile_json = json.dumps(source_profile, ensure_ascii=False)

            copied: List[Dict[str, str]] = []
            missing: List[Dict[str, str]] = []
            try:
                for target_cookie, target_item in normalized_targets:
                    cursor.execute(
                        'SELECT 1 FROM item_info WHERE cookie_id = ? AND item_id = ?',
                        (target_cookie, target_item),
                    )
                    if not cursor.fetchone():
                        missing.append({'cookie_id': target_cookie, 'item_id': target_item})
                        continue
                    cursor.execute('''
                    INSERT INTO ai_item_knowledge_profiles
                    (cookie_id, item_id, draft_json, source_detail_hash, draft_updated_at)
                    VALUES (?, ?, ?, '', CURRENT_TIMESTAMP)
                    ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                        draft_json = excluded.draft_json,
                        source_detail_hash = '',
                        draft_updated_at = CURRENT_TIMESTAMP
                    ''', (target_cookie, target_item, profile_json))
                    copied.append({'cookie_id': target_cookie, 'item_id': target_item})
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            return {
                'copied_targets': copied,
                'missing_targets': missing,
                'copied_item_ids': [target['item_id'] for target in copied],
                'skipped_item_ids': [],
                'missing_item_ids': [target['item_id'] for target in missing],
                'source_kind': source_kind,
                'copied_count': len(copied),
                'skipped_count': 0,
                'missing_count': len(missing),
                'skipped_reasons': {
                    target['item_id']: '目标商品不存在或不属于该账号' for target in missing
                },
            }

    def copy_ai_item_knowledge_draft(self, cookie_id: str, source_item_id: str,
                                     target_item_ids: List[str], overwrite: bool = True) -> Dict[str, Any]:
        """复制源商品当前档案并覆盖同账号目标草稿，不自动发布。"""
        return self.copy_ai_item_knowledge_draft_to_targets(
            cookie_id,
            source_item_id,
            [(cookie_id, str(value or '').strip()) for value in (target_item_ids or [])],
        )

    def import_ai_item_knowledge_draft(self, target_cookie_id: str, target_item_id: str,
                                       source_cookie_id: str, source_item_id: str) -> Dict[str, Any]:
        """把源商品（可跨账号）的档案拉进目标商品草稿，不自动发布。"""
        result = self.copy_ai_item_knowledge_draft_to_targets(
            source_cookie_id, source_item_id, [(target_cookie_id, target_item_id)]
        )
        if result['copied_count'] != 1:
            raise ValueError('目标商品不存在或不属于该账号')
        return {'source_kind': result['source_kind']}

    def publish_ai_item_knowledge(self, cookie_id: str, item_id: str) -> Dict[str, Any]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT draft_json, source_detail_hash, published_version
            FROM ai_item_knowledge_profiles WHERE cookie_id = ? AND item_id = ?
            ''', (cookie_id, item_id))
            row = cursor.fetchone()
            if not row:
                raise ValueError('请先保存商品知识草稿')
            draft = json.loads(row[0] or '{}')
            if not draft:
                raise ValueError('商品知识草稿为空')
            if self._knowledge_has_pending(draft):
                raise ValueError('仍有待确认的AI内容，请确认或删除后再发布')
            version = int(row[2] or 0) + 1
            profile_json = json.dumps(draft, ensure_ascii=False)
            cursor.execute('''
            INSERT INTO ai_item_knowledge_versions
            (cookie_id, item_id, version, profile_json, source_detail_hash)
            VALUES (?, ?, ?, ?, ?)
            ''', (cookie_id, item_id, version, profile_json, row[1] or ''))
            cursor.execute('''
            UPDATE ai_item_knowledge_profiles
            SET published_json = ?, published_version = ?, published_at = CURRENT_TIMESTAMP
            WHERE cookie_id = ? AND item_id = ?
            ''', (profile_json, version, cookie_id, item_id))
            self.conn.commit()
        profile = self.get_ai_item_knowledge_profile(cookie_id, item_id)
        profile['version'] = version
        return profile

    def get_ai_item_knowledge_versions(self, cookie_id: str, item_id: str) -> List[Dict[str, Any]]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT version, profile_json, source_detail_hash, created_at
            FROM ai_item_knowledge_versions
            WHERE cookie_id = ? AND item_id = ?
            ORDER BY version DESC
            ''', (cookie_id, item_id))
            return [{
                'version': row[0],
                'profile': json.loads(row[1] or '{}'),
                'source_detail_hash': row[2] or '',
                'created_at': row[3],
            } for row in cursor.fetchall()]

    def rollback_ai_item_knowledge(self, cookie_id: str, item_id: str, version: int) -> Dict[str, Any]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT profile_json, source_detail_hash
            FROM ai_item_knowledge_versions
            WHERE cookie_id = ? AND item_id = ? AND version = ?
            ''', (cookie_id, item_id, version))
            target = cursor.fetchone()
            if not target:
                raise ValueError('指定的知识档案版本不存在')
            cursor.execute('''
            SELECT published_version FROM ai_item_knowledge_profiles
            WHERE cookie_id = ? AND item_id = ?
            ''', (cookie_id, item_id))
            current = cursor.fetchone()
            next_version = int(current[0] or 0) + 1
            cursor.execute('''
            INSERT INTO ai_item_knowledge_versions
            (cookie_id, item_id, version, profile_json, source_detail_hash)
            VALUES (?, ?, ?, ?, ?)
            ''', (cookie_id, item_id, next_version, target[0], target[1] or ''))
            cursor.execute('''
            UPDATE ai_item_knowledge_profiles
            SET draft_json = ?, published_json = ?, source_detail_hash = ?,
                published_version = ?, draft_updated_at = CURRENT_TIMESTAMP,
                published_at = CURRENT_TIMESTAMP
            WHERE cookie_id = ? AND item_id = ?
            ''', (target[0], target[0], target[1] or '', next_version, cookie_id, item_id))
            self.conn.commit()
        profile = self.get_ai_item_knowledge_profile(cookie_id, item_id)
        profile['version'] = next_version
        return profile

    @staticmethod
    def _knowledge_payload_has_content(profile: Any) -> bool:
        """判断知识档案 JSON 是否包含真实内容（与前端 hasKnowledgeContent 同语义）。"""
        if not isinstance(profile, dict):
            return False
        overview = profile.get('overview')
        if isinstance(overview, dict) and str(overview.get('text') or '').strip():
            return True
        for section in ('pricing', 'process', 'after_sales', 'forbidden', 'faqs', 'notes'):
            value = profile.get(section)
            if isinstance(value, list) and len(value) > 0:
                return True
        return False

    def get_ai_item_knowledge_status_by_cookie(self, cookie_id: str) -> Dict[str, Dict[str, Any]]:
        """按账号返回商品知识档案状态映射，用于商品列表标识与复制目标提示。

        Returns:
            Dict[item_id, {'has_draft': bool, 'published_version': int,
                           'draft_updated_at': str|None, 'published_at': str|None}]
            只包含存在草稿内容或已发布版本的商品。
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT item_id, draft_json, published_version, draft_updated_at, published_at
                FROM ai_item_knowledge_profiles
                WHERE cookie_id = ?
                ''', (cookie_id,))
                rows = cursor.fetchall()
            status: Dict[str, Dict[str, Any]] = {}
            for item_id, draft_json, published_version, draft_updated_at, published_at in rows:
                try:
                    draft = json.loads(draft_json or '{}')
                except (TypeError, ValueError):
                    draft = {}
                has_draft = self._knowledge_payload_has_content(draft)
                version = int(published_version or 0)
                if not has_draft and version <= 0:
                    continue
                status[str(item_id)] = {
                    'has_draft': has_draft,
                    'published_version': version,
                    'draft_updated_at': draft_updated_at,
                    'published_at': published_at,
                }
            return status
        except Exception as e:
            logger.error(f"读取商品知识档案状态失败: {e}")
            return {}

    # -------------------- 默认回复操作 --------------------
    def save_default_reply(self, cookie_id: str, enabled: bool, reply_content: str = None, reply_once: bool = False, reply_image_url: str = None):
        """保存默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO default_replies (cookie_id, enabled, reply_content, reply_image_url, reply_once, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (cookie_id, enabled, reply_content, reply_image_url, reply_once))
                self.conn.commit()
                logger.debug(f"保存默认回复设置: {cookie_id} -> {'启用' if enabled else '禁用'}, 只回复一次: {'是' if reply_once else '否'}, 图片: {reply_image_url}")
            except Exception as e:
                logger.error(f"保存默认回复设置失败: {e}")
                raise

    def get_default_reply(self, cookie_id: str) -> Optional[Dict[str, any]]:
        """获取指定账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT enabled, reply_content, reply_once, reply_image_url FROM default_replies WHERE cookie_id = ?
                ''', (cookie_id,))
                result = cursor.fetchone()
                if result:
                    enabled, reply_content, reply_once, reply_image_url = result
                    return {
                        'enabled': bool(enabled),
                        'reply_content': reply_content or '',
                        'reply_once': bool(reply_once) if reply_once is not None else False,
                        'reply_image_url': reply_image_url or ''
                    }
                return None
            except Exception as e:
                logger.error(f"获取默认回复设置失败: {e}")
                return None

    def get_all_default_replies(self) -> Dict[str, Dict[str, any]]:
        """获取所有账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT cookie_id, enabled, reply_content, reply_once, reply_image_url FROM default_replies')

                result = {}
                for row in cursor.fetchall():
                    cookie_id, enabled, reply_content, reply_once, reply_image_url = row
                    result[cookie_id] = {
                        'enabled': bool(enabled),
                        'reply_content': reply_content or '',
                        'reply_once': bool(reply_once) if reply_once is not None else False,
                        'reply_image_url': reply_image_url or ''
                    }

                return result
            except Exception as e:
                logger.error(f"获取所有默认回复设置失败: {e}")
                return {}

    def add_default_reply_record(self, cookie_id: str, chat_id: str):
        """记录已回复的chat_id"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR IGNORE INTO default_reply_records (cookie_id, chat_id)
                VALUES (?, ?)
                ''', (cookie_id, chat_id))
                self.conn.commit()
                logger.debug(f"记录默认回复: {cookie_id} -> {chat_id}")
            except Exception as e:
                logger.error(f"记录默认回复失败: {e}")

    def has_default_reply_record(self, cookie_id: str, chat_id: str) -> bool:
        """检查是否已经回复过该chat_id"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT 1 FROM default_reply_records WHERE cookie_id = ? AND chat_id = ?
                ''', (cookie_id, chat_id))
                result = cursor.fetchone()
                return result is not None
            except Exception as e:
                logger.error(f"检查默认回复记录失败: {e}")
                return False

    def clear_default_reply_records(self, cookie_id: str):
        """清空指定账号的默认回复记录"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM default_reply_records WHERE cookie_id = ?', (cookie_id,))
                self.conn.commit()
                logger.debug(f"清空默认回复记录: {cookie_id}")
            except Exception as e:
                logger.error(f"清空默认回复记录失败: {e}")

    def find_chat_id_by_buyer(self, cookie_id: str, buyer_id: str) -> str:
        """根据买家ID查找最近的chat_id（从AI对话记录中查找）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT chat_id FROM ai_conversations
                    WHERE cookie_id = ? AND user_id = ?
                    AND chat_id IS NOT NULL AND chat_id != ''
                    ORDER BY id DESC LIMIT 1
                ''', (cookie_id, buyer_id))
                row = cursor.fetchone()
                if row:
                    return row[0]
                return None
            except Exception as e:
                logger.error(f"查找chat_id失败: {e}")
                return None

    def delete_default_reply(self, cookie_id: str) -> bool:
        """删除指定账号的默认回复设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "DELETE FROM default_replies WHERE cookie_id = ?", (cookie_id,))
                self.conn.commit()
                logger.debug(f"删除默认回复设置: {cookie_id}")
                return True
            except Exception as e:
                logger.error(f"删除默认回复设置失败: {e}")
                self.conn.rollback()
                return False

    def update_default_reply_image_url(self, cookie_id: str, new_image_url: str) -> bool:
        """更新默认回复的图片URL（用于将本地图片URL更新为CDN URL）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE default_replies SET reply_image_url = ? WHERE cookie_id = ?
                ''', (new_image_url, cookie_id))
                self.conn.commit()
                logger.debug(f"更新默认回复图片URL: {cookie_id} -> {new_image_url}")
                return True
            except Exception as e:
                logger.error(f"更新默认回复图片URL失败: {e}")
                self.conn.rollback()
                return False

    # -------------------- 通知渠道操作 --------------------
    def create_notification_channel(self, name: str, channel_type: str, config: str, user_id: int = None) -> int:
        """创建通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT INTO notification_channels (name, type, config, user_id)
                VALUES (?, ?, ?, ?)
                ''', (name, channel_type, config, user_id))
                self.conn.commit()
                channel_id = cursor.lastrowid
                logger.debug(f"创建通知渠道: {name} (ID: {channel_id})")
                return channel_id
            except Exception as e:
                logger.error(f"创建通知渠道失败: {e}")
                self.conn.rollback()
                raise

    def get_notification_channels(self, user_id: int = None) -> List[Dict[str, any]]:
        """获取所有通知渠道"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at
                    FROM notification_channels
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT id, name, type, config, enabled, created_at, updated_at
                    FROM notification_channels
                    ORDER BY created_at DESC
                    ''')

                channels = []
                for row in cursor.fetchall():
                    channels.append({
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'config': row[3],
                        'enabled': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6]
                    })

                return channels
            except Exception as e:
                logger.error(f"获取通知渠道失败: {e}")
                return []

    def get_notification_channel(
        self,
        channel_id: int,
        user_id: int,
    ) -> Optional[Dict[str, any]]:
        """获取当前租户的指定通知渠道。"""
        if user_id is None:
            raise ValueError("get_notification_channel 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT id, name, type, config, enabled, created_at, updated_at
                FROM notification_channels WHERE id = ? AND user_id = ?
                ''', (channel_id, int(user_id)))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'type': row[2],
                        'config': row[3],
                        'enabled': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6]
                    }
                return None
            except Exception as e:
                logger.error(f"获取通知渠道失败: {e}")
                return None

    def update_notification_channel(
        self,
        channel_id: int,
        name: str,
        config: str,
        enabled: bool,
        user_id: int,
    ) -> bool:
        """更新当前租户的通知渠道。"""
        if user_id is None:
            raise ValueError("update_notification_channel 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE notification_channels
                SET name = ?, config = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                ''', (name, config, enabled, channel_id, int(user_id)))
                self.conn.commit()
                logger.debug(f"更新通知渠道: {channel_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"更新通知渠道失败: {e}")
                self.conn.rollback()
                return False

    def delete_notification_channel(self, channel_id: int, user_id: int) -> bool:
        """删除当前租户的通知渠道。"""
        if user_id is None:
            raise ValueError("delete_notification_channel 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "DELETE FROM notification_channels WHERE id = ? AND user_id = ?",
                    (channel_id, int(user_id)),
                )
                self.conn.commit()
                logger.debug(f"删除通知渠道: {channel_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除通知渠道失败: {e}")
                self.conn.rollback()
                return False

    # -------------------- 消息通知配置操作 --------------------
    def set_message_notification(
        self,
        cookie_id: str,
        channel_id: int,
        enabled: bool,
        user_id: int,
    ) -> bool:
        """在同一租户内关联账号与通知渠道。"""
        if user_id is None:
            raise ValueError("set_message_notification 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                ownership = cursor.execute(
                    "SELECT 1 FROM cookies AS c "
                    "JOIN notification_channels AS nc ON nc.id = ? "
                    "WHERE c.id = ? AND c.user_id = ? AND nc.user_id = ?",
                    (channel_id, cookie_id, int(user_id), int(user_id)),
                ).fetchone()
                if not ownership:
                    return False
                cursor.execute('''
                INSERT OR REPLACE INTO message_notifications (cookie_id, channel_id, enabled)
                VALUES (?, ?, ?)
                ''', (cookie_id, channel_id, enabled))
                self.conn.commit()
                logger.debug(f"设置消息通知: {cookie_id} -> {channel_id}")
                return True
            except Exception as e:
                logger.error(f"设置消息通知失败: {e}")
                self.conn.rollback()
                return False

    def get_account_notifications(self, cookie_id: str) -> List[Dict[str, any]]:
        """获取账号的通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT mn.id, mn.channel_id, mn.enabled, nc.name, nc.type, nc.config
                FROM message_notifications mn
                JOIN notification_channels nc ON mn.channel_id = nc.id
                JOIN cookies c ON c.id = mn.cookie_id AND c.user_id = nc.user_id
                WHERE mn.cookie_id = ? AND nc.enabled = 1
                ORDER BY mn.id
                ''', (cookie_id,))

                notifications = []
                for row in cursor.fetchall():
                    notifications.append({
                        'id': row[0],
                        'channel_id': row[1],
                        'enabled': bool(row[2]),
                        'channel_name': row[3],
                        'channel_type': row[4],
                        'channel_config': row[5]
                    })

                return notifications
            except Exception as e:
                logger.error(f"获取账号通知配置失败: {e}")
                return []

    def get_all_message_notifications(self) -> Dict[str, List[Dict[str, any]]]:
        """获取所有账号的通知配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT mn.cookie_id, mn.id, mn.channel_id, mn.enabled, nc.name, nc.type, nc.config
                FROM message_notifications mn
                JOIN notification_channels nc ON mn.channel_id = nc.id
                JOIN cookies c ON c.id = mn.cookie_id AND c.user_id = nc.user_id
                WHERE nc.enabled = 1
                ORDER BY mn.cookie_id, mn.id
                ''')

                result = {}
                for row in cursor.fetchall():
                    cookie_id = row[0]
                    if cookie_id not in result:
                        result[cookie_id] = []

                    result[cookie_id].append({
                        'id': row[1],
                        'channel_id': row[2],
                        'enabled': bool(row[3]),
                        'channel_name': row[4],
                        'channel_type': row[5],
                        'channel_config': row[6]
                    })

                return result
            except Exception as e:
                logger.error(f"获取所有消息通知配置失败: {e}")
                return {}

    def delete_message_notification(self, notification_id: int, user_id: int) -> bool:
        """删除当前租户的消息通知配置。"""
        if user_id is None:
            raise ValueError("delete_message_notification 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "DELETE FROM message_notifications WHERE id = ? "
                    "AND cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)",
                    (notification_id, int(user_id)),
                )
                self.conn.commit()
                logger.debug(f"删除消息通知配置: {notification_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除消息通知配置失败: {e}")
                self.conn.rollback()
                return False

    def delete_account_notifications(self, cookie_id: str, user_id: int) -> bool:
        """删除当前租户账号的所有消息通知配置。"""
        if user_id is None:
            raise ValueError("delete_account_notifications 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(
                    cursor,
                    "DELETE FROM message_notifications WHERE cookie_id = ? "
                    "AND cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)",
                    (cookie_id, int(user_id)),
                )
                self.conn.commit()
                logger.debug(f"删除账号通知配置: {cookie_id}")
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除账号通知配置失败: {e}")
                self.conn.rollback()
                return False

    # -------------------- 备份和恢复操作 --------------------
    def export_backup(self, user_id: int = None) -> Dict[str, any]:
        """导出系统备份数据（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                backup_data = {
                    'version': '1.0',
                    'timestamp': time.time(),
                    'user_id': user_id,
                    'data': {}
                }

                if user_id is not None:
                    # 用户级备份：只备份该用户的数据
                    # 备份用户的cookies
                    self._execute_sql(cursor, "SELECT * FROM cookies WHERE user_id = ?", (user_id,))
                    columns = [description[0] for description in cursor.description]
                    rows = cursor.fetchall()
                    backup_data['data']['cookies'] = {
                        'columns': columns,
                        'rows': [list(row) for row in rows]
                    }

                    # 备份用户cookies相关的其他数据
                    user_cookie_ids = [row[0] for row in rows]  # 获取用户的cookie_id列表

                    if user_cookie_ids:
                        placeholders = ','.join(['?' for _ in user_cookie_ids])

                        # 备份关键字
                        cursor.execute(f"SELECT * FROM keywords WHERE cookie_id IN ({placeholders})", user_cookie_ids)
                        columns = [description[0] for description in cursor.description]
                        rows = cursor.fetchall()
                        backup_data['data']['keywords'] = {
                            'columns': columns,
                            'rows': [list(row) for row in rows]
                        }

                        # 备份其他相关表
                        related_tables = [
                            table
                            for table in USER_BACKUP_TABLES
                            if table not in {"cookies", "keywords"}
                        ]

                        for table in related_tables:
                            cursor.execute(f"SELECT * FROM {table} WHERE cookie_id IN ({placeholders})", user_cookie_ids)
                            columns = [description[0] for description in cursor.description]
                            rows = cursor.fetchall()
                            backup_data['data'][table] = {
                                'columns': columns,
                                'rows': [list(row) for row in rows]
                            }
                else:
                    # 系统级备份：备份所有数据
                    tables = SYSTEM_BACKUP_TABLES

                    for table in tables:
                        cursor.execute(f"SELECT * FROM {table}")
                        columns = [description[0] for description in cursor.description]
                        rows = cursor.fetchall()

                        backup_data['data'][table] = {
                            'columns': columns,
                            'rows': [list(row) for row in rows]
                        }

                logger.info(f"导出备份成功，用户ID: {user_id}")
                return backup_data

            except Exception as e:
                logger.error(f"导出备份失败: {e}")
                raise

    @staticmethod
    def _looks_like_fernet_ciphertext(value: str) -> bool:
        if not value.startswith('fernet:'):
            return False
        token = value.rsplit(':', 1)[-1]
        try:
            decoded = base64.b64decode(
                token.encode('ascii'),
                altchars=b'-_',
                validate=True,
            )
        except (binascii.Error, UnicodeError, ValueError):
            return False
        return len(decoded) >= 73 and decoded[0] == 0x80

    def _normalize_imported_smtp_password(self, value: Any) -> tuple[str, bool]:
        imported_value = str(value if value is not None else '')
        if not imported_value:
            return '', False

        cipher = SystemSecretCipher(self.db_path)
        if imported_value.startswith(SYSTEM_SECRET_PREFIX):
            try:
                cipher.decrypt(imported_value)
            except ValueError:
                if self._looks_like_fernet_ciphertext(imported_value):
                    return '', True
                return cipher.encrypt(imported_value), False
            return imported_value, False
        if self._looks_like_fernet_ciphertext(imported_value):
            return '', True
        return cipher.encrypt(imported_value), False

    def _prepare_imported_system_settings(
        self,
        columns: List[str],
        rows: List[List[Any]],
    ) -> tuple[List[List[Any]], bool, bool]:
        try:
            key_index = columns.index('key')
            value_index = columns.index('value')
        except ValueError as exc:
            raise ValueError("系统设置备份缺少必要字段") from exc

        prepared_rows = []
        smtp_settings_imported = False
        smtp_reconfiguration_required = False
        for row in rows:
            prepared_row = list(row)
            key = str(prepared_row[key_index])
            if key.startswith('smtp_'):
                smtp_settings_imported = True
            if key == 'smtp_password':
                normalized, requires_reconfiguration = (
                    self._normalize_imported_smtp_password(
                        prepared_row[value_index]
                    )
                )
                prepared_row[value_index] = normalized
                smtp_reconfiguration_required |= requires_reconfiguration
            elif key in {'smtp_verified_fingerprint', 'smtp_verified_at'}:
                prepared_row[value_index] = ''
            prepared_rows.append(prepared_row)

        return (
            prepared_rows,
            smtp_settings_imported,
            smtp_reconfiguration_required,
        )

    @staticmethod
    def _sanitize_imported_image_references(
        prepared: Dict[str, Dict[str, Any]],
    ) -> None:
        """Drop backup-provided image references outside the managed boundary."""
        for table_name, image_columns in BACKUP_IMAGE_REFERENCE_COLUMNS.items():
            table = prepared.get(table_name)
            if not table:
                continue
            columns = table["columns"]
            indexes = [
                columns.index(column)
                for column in image_columns
                if column in columns
            ]
            for row in table["rows"]:
                for index in indexes:
                    row[index] = image_manager.normalize_image_reference(
                        row[index]
                    )

    def _backup_table_columns(
        self,
        cursor: sqlite3.Cursor,
        table_name: str,
    ) -> List[str]:
        rows = cursor.execute(
            f"PRAGMA table_info({self._quote_identifier(table_name)})"
        ).fetchall()
        if not rows:
            raise ValueError(f"备份表不存在: {table_name}")
        return [str(row[1]) for row in rows]

    def _prepare_backup_import(
        self,
        backup_data: Dict[str, Any],
        user_id: Optional[int],
        cursor: sqlite3.Cursor,
    ) -> tuple[Dict[str, Dict[str, Any]], bool, bool]:
        if not isinstance(backup_data, dict) or not isinstance(
            backup_data.get("data"), dict
        ):
            raise ValueError("备份数据格式无效")
        try:
            serialized_size = len(
                json.dumps(
                    backup_data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("备份数据无法序列化") from exc
        if serialized_size > BACKUP_MAX_SERIALIZED_BYTES:
            raise ValueError("备份数据超过大小上限")

        data = backup_data["data"]
        if len(data) > BACKUP_MAX_TABLES:
            raise ValueError("备份数据表数量超过上限")
        allowed_tables = set(
            USER_BACKUP_TABLES if user_id is not None else SYSTEM_BACKUP_TABLES
        )
        unknown_tables = set(map(str, data)) - allowed_tables
        if unknown_tables:
            raise ValueError("备份包含不允许导入的数据表")
        if user_id is not None and "cookies" not in data:
            raise ValueError("用户备份缺少账号表")

        prepared: Dict[str, Dict[str, Any]] = {}
        total_rows = 0
        for table_name, table_data in data.items():
            table_name = str(table_name)
            if not isinstance(table_data, dict):
                raise ValueError(f"备份表结构无效: {table_name}")
            columns = table_data.get("columns")
            rows = table_data.get("rows")
            if not isinstance(columns, list) or not all(
                isinstance(column, str) and column for column in columns
            ):
                raise ValueError(f"备份列定义无效: {table_name}")
            if len(columns) != len(set(columns)):
                raise ValueError(f"备份包含重复列: {table_name}")
            if not isinstance(rows, list):
                raise ValueError(f"备份行定义无效: {table_name}")

            server_columns = self._backup_table_columns(cursor, table_name)
            if len(columns) != len(server_columns) or set(columns) != set(
                server_columns
            ):
                raise ValueError(f"备份列与当前 schema 不一致: {table_name}")

            normalized_rows: List[List[Any]] = []
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                    raise ValueError(f"备份行列数量不匹配: {table_name}")
                row_by_column = dict(zip(columns, row))
                normalized_rows.append(
                    [row_by_column[column] for column in server_columns]
                )
            total_rows += len(normalized_rows)
            if total_rows > BACKUP_MAX_TOTAL_ROWS:
                raise ValueError("备份总行数超过上限")
            prepared[table_name] = {
                "columns": server_columns,
                "rows": normalized_rows,
            }

        smtp_settings_imported = False
        smtp_reconfiguration_required = False
        if "system_settings" in prepared:
            table = prepared["system_settings"]
            (
                table["rows"],
                smtp_settings_imported,
                smtp_reconfiguration_required,
            ) = self._prepare_imported_system_settings(
                table["columns"], table["rows"]
            )

        self._sanitize_imported_image_references(prepared)

        if user_id is not None:
            cookie_table = prepared["cookies"]
            cookie_columns = cookie_table["columns"]
            cookie_id_index = cookie_columns.index("id")
            cookie_user_index = cookie_columns.index("user_id")
            imported_cookie_ids: set[str] = set()
            for row in cookie_table["rows"]:
                cookie_id = str(row[cookie_id_index] or "").strip()
                if not cookie_id or cookie_id in imported_cookie_ids:
                    raise ValueError("用户备份包含无效或重复账号")
                imported_cookie_ids.add(cookie_id)
                row[cookie_id_index] = cookie_id
                row[cookie_user_index] = int(user_id)

            for table_name, table in prepared.items():
                if table_name == "cookies":
                    continue
                columns = table["columns"]
                if "cookie_id" not in columns:
                    raise ValueError(f"用户备份表缺少账号归属: {table_name}")
                cookie_index = columns.index("cookie_id")
                user_index = columns.index("user_id") if "user_id" in columns else None
                id_index = (
                    columns.index("id")
                    if table_name in BACKUP_AUTO_ID_TABLES and "id" in columns
                    else None
                )
                for row in table["rows"]:
                    cookie_id = str(row[cookie_index] or "").strip()
                    if cookie_id not in imported_cookie_ids:
                        raise ValueError("用户备份包含其他账号的关联数据")
                    row[cookie_index] = cookie_id
                    if table_name in {
                        "item_metric_snapshots",
                        "item_metric_collection_states",
                    } and user_index is not None:
                        row[user_index] = int(user_id)
                    if id_index is not None:
                        row[id_index] = None

        return (
            prepared,
            smtp_settings_imported,
            smtp_reconfiguration_required,
        )

    def _insert_backup_rows(
        self,
        cursor: sqlite3.Cursor,
        table_name: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> None:
        if not rows:
            return
        quoted_columns = ",".join(
            self._quote_identifier(column) for column in columns
        )
        placeholders = ",".join("?" for _ in columns)
        cursor.executemany(
            f"INSERT INTO {self._quote_identifier(table_name)} "
            f"({quoted_columns}) VALUES ({placeholders})",
            rows,
        )

    def _upsert_imported_cookies(
        self,
        cursor: sqlite3.Cursor,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        *,
        user_id: Optional[int],
    ) -> set[str]:
        id_index = columns.index("id")
        imported_ids = {str(row[id_index]) for row in rows}
        if user_id is not None and imported_ids:
            placeholders = ",".join("?" for _ in imported_ids)
            foreign = cursor.execute(
                f"SELECT id FROM cookies WHERE id IN ({placeholders}) "
                "AND user_id != ? LIMIT 1",
                [*sorted(imported_ids), int(user_id)],
            ).fetchone()
            if foreign:
                raise PermissionError("备份账号已属于其他用户")

        update_columns = [column for column in columns if column != "id"]
        update_sql = ",".join(
            f"{self._quote_identifier(column)} = ?" for column in update_columns
        )
        for row in rows:
            row_map = dict(zip(columns, row))
            cookie_id = str(row_map["id"])
            owner_clause = " AND user_id = ?" if user_id is not None else ""
            params = [row_map[column] for column in update_columns]
            params.append(cookie_id)
            if user_id is not None:
                params.append(int(user_id))
            cursor.execute(
                f"UPDATE cookies SET {update_sql} WHERE id = ?{owner_clause}",
                params,
            )
            if cursor.rowcount:
                continue
            self._insert_backup_rows(cursor, "cookies", columns, [row])
        return imported_ids

    def import_backup(self, backup_data: Dict[str, any], user_id: int = None) -> bool:
        """导入系统备份数据（支持用户隔离）"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                (
                    prepared,
                    smtp_settings_imported,
                    smtp_reconfiguration_required,
                ) = self._prepare_backup_import(backup_data, user_id, cursor)

                cursor.execute("BEGIN IMMEDIATE")
                if user_id is not None:
                    current_cookie_ids = {
                        str(row[0])
                        for row in cursor.execute(
                            "SELECT id FROM cookies WHERE user_id = ?",
                            (int(user_id),),
                        ).fetchall()
                    }
                    for table_name in reversed(BACKUP_INSERT_ORDER):
                        if table_name in {"cookies", "system_settings"}:
                            continue
                        if table_name not in prepared or not current_cookie_ids:
                            continue
                        placeholders = ",".join("?" for _ in current_cookie_ids)
                        cursor.execute(
                            f"DELETE FROM {self._quote_identifier(table_name)} "
                            f"WHERE cookie_id IN ({placeholders})",
                            sorted(current_cookie_ids),
                        )
                else:
                    for table_name in reversed(BACKUP_INSERT_ORDER):
                        if table_name == "cookies" or table_name not in prepared:
                            continue
                        if table_name == "system_settings":
                            cursor.execute(
                                "DELETE FROM system_settings "
                                "WHERE key != 'admin_password_hash'"
                            )
                        else:
                            cursor.execute(
                                f"DELETE FROM {self._quote_identifier(table_name)}"
                            )

                imported_cookie_ids: set[str] = set()
                if "cookies" in prepared:
                    cookie_table = prepared["cookies"]
                    imported_cookie_ids = self._upsert_imported_cookies(
                        cursor,
                        cookie_table["columns"],
                        cookie_table["rows"],
                        user_id=user_id,
                    )
                    if user_id is not None:
                        stale_ids = current_cookie_ids - imported_cookie_ids
                        self._delete_cookie_children(cursor, sorted(stale_ids))
                        if stale_ids:
                            placeholders = ",".join("?" for _ in stale_ids)
                            cursor.execute(
                                f"DELETE FROM cookies WHERE id IN ({placeholders}) "
                                "AND user_id = ?",
                                [*sorted(stale_ids), int(user_id)],
                            )
                    else:
                        existing_ids = {
                            str(row[0])
                            for row in cursor.execute("SELECT id FROM cookies").fetchall()
                        }
                        stale_ids = existing_ids - imported_cookie_ids
                        self._delete_cookie_children(cursor, sorted(stale_ids))
                        if stale_ids:
                            placeholders = ",".join("?" for _ in stale_ids)
                            cursor.execute(
                                f"DELETE FROM cookies WHERE id IN ({placeholders})",
                                sorted(stale_ids),
                            )

                for table_name in BACKUP_INSERT_ORDER:
                    if table_name == "cookies" or table_name not in prepared:
                        continue
                    table = prepared[table_name]
                    rows = table["rows"]
                    if table_name == "system_settings":
                        key_index = table["columns"].index("key")
                        rows = [
                            row
                            for row in rows
                            if row[key_index] != "admin_password_hash"
                        ]
                    self._insert_backup_rows(
                        cursor,
                        table_name,
                        table["columns"],
                        rows,
                    )

                if smtp_settings_imported:
                    self._clear_smtp_verification(cursor)

                self.conn.commit()
                if smtp_reconfiguration_required:
                    logger.warning(
                        "导入的 SMTP 密码无法由当前系统密钥解密，已清空，请重新配置"
                    )
                logger.info("导入备份成功")
                return True

            except Exception as e:
                logger.error(f"导入备份失败: {e}")
                self.conn.rollback()
                return False

    # -------------------- 系统设置操作 --------------------
    def _decode_system_setting(self, key: str, value: str) -> str:
        if key == 'smtp_password':
            return SystemSecretCipher(self.db_path).decrypt(str(value or ''))
        return value

    def _encode_system_setting(self, cursor, key: str, value: Any) -> Any:
        if key != 'smtp_password':
            return value

        plaintext = str(value if value is not None else '')
        if not plaintext:
            return ''

        cipher = SystemSecretCipher(self.db_path)
        existing_row = cursor.execute(
            "SELECT value FROM system_settings WHERE key = 'smtp_password'"
        ).fetchone()
        if existing_row:
            existing_value = str(existing_row[0] or '')
            if existing_value.startswith(SYSTEM_SECRET_PREFIX):
                try:
                    existing_plaintext = cipher.decrypt(existing_value)
                except ValueError:
                    pass
                else:
                    if existing_plaintext == plaintext:
                        return existing_value
        return cipher.encrypt(plaintext)

    def _system_setting_value(self, cursor, key: str) -> str:
        row = cursor.execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return ""
        return str(self._decode_system_setting(key, row[0]) or "")

    def _smtp_configuration_changed(self, cursor, settings: Dict[str, Any]) -> bool:
        for key in SMTP_CONFIGURATION_KEYS.intersection(settings):
            current = canonical_smtp_setting_value(
                key,
                self._system_setting_value(cursor, key),
            )
            candidate = canonical_smtp_setting_value(key, settings[key])
            if current != candidate:
                return True
        return False

    def _write_system_settings(self, cursor, settings: Dict[str, Any]) -> None:
        for key, value in settings.items():
            if isinstance(value, bool):
                stored_value = "true" if value else "false"
            else:
                stored_value = str(value if value is not None else "")
            stored_value = self._encode_system_setting(cursor, key, stored_value)
            cursor.execute(
                """
                INSERT INTO system_settings (key, value, description, updated_at)
                VALUES (?, ?, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, stored_value),
            )

    def _clear_smtp_verification(self, cursor) -> None:
        self._write_system_settings(
            cursor,
            {
                "smtp_verified_fingerprint": "",
                "smtp_verified_at": "",
                "registration_enabled": "false",
            },
        )
        cursor.execute(
            """
            UPDATE auth_challenges
            SET consumed_at = CAST(strftime('%s', 'now') AS REAL)
            WHERE purpose = 'smtp_verify_email' AND consumed_at IS NULL
            """
        )

    def get_system_setting(self, key: str) -> Optional[str]:
        """获取系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT value FROM system_settings WHERE key = ?", (key,))
                result = cursor.fetchone()
                return self._decode_system_setting(key, result[0]) if result else None
            except Exception as e:
                logger.error(f"获取系统设置失败: {e}")
                return None

    def set_system_setting(self, key: str, value: str, description: str = None) -> bool:
        """设置系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                smtp_changed = self._smtp_configuration_changed(
                    cursor,
                    {key: value},
                )
                stored_value = self._encode_system_setting(cursor, key, value)
                cursor.execute(
                    """
                    INSERT INTO system_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        description = COALESCE(excluded.description, system_settings.description),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, stored_value, description),
                )
                if smtp_changed:
                    self._clear_smtp_verification(cursor)
                self.conn.commit()
                logger.debug(f"设置系统设置: {key}")
                return True
            except Exception as e:
                logger.error(f"设置系统设置失败: {e}")
                self.conn.rollback()
                return False

    def save_system_settings_section(self, settings: Dict[str, Any]) -> bool:
        """Atomically persist one allowlisted settings section."""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN")
                smtp_changed = self._smtp_configuration_changed(cursor, settings)
                self._write_system_settings(cursor, settings)
                if smtp_changed:
                    self._clear_smtp_verification(cursor)
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"分区保存系统设置失败: {e}")
                self.conn.rollback()
                return False

    def save_unverified_smtp_settings(self, settings: Dict[str, Any]) -> bool:
        """Persist an SMTP candidate and force a fresh email confirmation."""
        allowed = {
            key: value
            for key, value in settings.items()
            if key in SMTP_CONFIGURATION_KEYS
        }
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                self._write_system_settings(cursor, allowed)
                self._clear_smtp_verification(cursor)
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"保存待验证 SMTP 配置失败: {type(e).__name__}")
                self.conn.rollback()
                return False

    def save_verified_smtp_settings(
        self,
        settings: Dict[str, Any],
        *,
        fingerprint: str,
        verified_at: str,
        expected_settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Atomically save SMTP settings and mark that exact configuration verified."""
        if not str(fingerprint or "") or not str(verified_at or ""):
            return False
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN")
                if expected_settings is not None:
                    expected = {
                        key: value
                        for key, value in expected_settings.items()
                        if key in SMTP_CONFIGURATION_KEYS
                    }
                    if self._smtp_configuration_changed(cursor, expected):
                        self.conn.rollback()
                        return False
                allowed = {
                    key: value
                    for key, value in settings.items()
                    if key in SMTP_CONFIGURATION_KEYS
                }
                self._write_system_settings(cursor, allowed)
                self._write_system_settings(
                    cursor,
                    {
                        "smtp_verified_fingerprint": fingerprint,
                        "smtp_verified_at": verified_at,
                    },
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"保存 SMTP 验证状态失败: {type(e).__name__}")
                self.conn.rollback()
                return False

    def get_all_system_settings(self) -> Dict[str, str]:
        """获取所有系统设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                self._execute_sql(cursor, "SELECT key, value FROM system_settings")

                settings = {}
                for row in cursor.fetchall():
                    settings[row[0]] = self._decode_system_setting(row[0], row[1])

                return settings
            except Exception as e:
                logger.error(f"获取所有系统设置失败: {e}")
                return {}

    # 管理员密码现在统一使用用户表管理，不再需要单独的方法

    # ==================== 用户管理方法 ====================

    def create_user(self, username: str, email: str, password: str) -> bool:
        """创建新用户"""
        with self.lock:
            try:
                password_hash_v2 = hash_user_password(password)
                self.user_repository.create(
                    username, email, password_hash_v2, PASSWORD_HASH_VERSION
                )

                self.conn.commit()
                logger.info(f"创建用户成功: {username}")
                return True
            except sqlite3.IntegrityError as e:
                logger.error(f"创建用户失败，用户名或邮箱已存在: {e}")
                self.conn.rollback()
                return False
            except Exception as e:
                logger.error(f"创建用户失败: {e}")
                self.conn.rollback()
                return False

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取用户信息"""
        with self.lock:
            try:
                return self.user_repository.get_by_username(username)
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """根据邮箱获取用户信息"""
        with self.lock:
            try:
                return self.user_repository.get_by_email(email)
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None

    def get_user_by_email_for_public_auth(
        self,
        email: str,
    ) -> Optional[Dict[str, Any]]:
        """Look up normalized email using only the migration-backed index."""

        with self.lock:
            return self.user_repository.get_by_email_indexed(email)

    def verify_user_password(self, username: str, password: str) -> bool:
        """验证用户密码"""
        with self.lock:
            return self.auth_service.verify_password(username, password)

    def update_user_password(self, username: str, new_password: str) -> bool:
        """更新用户密码"""
        with self.lock:
            try:
                password_hash_v2 = hash_user_password(new_password)
                row_count = self.user_repository.set_password(
                    username, password_hash_v2, PASSWORD_HASH_VERSION
                )

                if row_count > 0:
                    self.conn.commit()
                    logger.info(f"用户 {username} 密码更新成功")
                    return True
                else:
                    logger.warning(f"用户 {username} 不存在，密码更新失败")
                    return False

            except Exception as e:
                logger.error(f"更新用户密码失败: {e}")
                self.conn.rollback()
                return False

    def update_user_password_and_revoke_sessions(
        self,
        username: str,
        new_password: str,
    ) -> Optional[int]:
        """原子更新后台密码并撤销该用户的全部持久化会话。"""
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                user = self.user_repository.get_by_identifier(username)
                if not user or not user.get("is_active"):
                    self.conn.rollback()
                    return None
                password_hash_v2 = hash_user_password(new_password)
                user_id = int(user["id"])
                row_count = self.user_repository.set_password_by_id(
                    user_id,
                    password_hash_v2,
                    PASSWORD_HASH_VERSION,
                )
                if row_count != 1:
                    self.conn.rollback()
                    return None
                self.auth_session_repository.delete_by_user_id(user_id)
                self.conn.commit()
                logger.info(f"用户密码与会话更新成功 user_id={user_id}")
                return user_id
            except Exception as e:
                logger.error(f"更新用户密码与会话失败: {type(e).__name__}")
                self.conn.rollback()
                return None

    def generate_verification_code(self) -> str:
        """生成6位数字验证码"""
        return ''.join(random.choices(string.digits, k=6))

    def generate_captcha(self) -> Tuple[str, str]:
        """生成图形验证码
        返回: (验证码文本, base64编码的图片)
        """
        try:
            # 生成4位随机验证码（数字+字母）
            chars = string.ascii_uppercase + string.digits
            captcha_text = ''.join(random.choices(chars, k=4))

            # 创建图片
            width, height = 120, 40
            image = Image.new('RGB', (width, height), color='white')
            draw = ImageDraw.Draw(image)

            # 尝试使用系统字体，如果失败则使用默认字体
            try:
                # Windows系统字体
                font = ImageFont.truetype("arial.ttf", 20)
            except:
                try:
                    # 备用字体
                    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
                except:
                    # 使用默认字体
                    font = ImageFont.load_default()

            # 绘制验证码文本
            for i, char in enumerate(captcha_text):
                # 随机颜色
                color = (
                    random.randint(0, 100),
                    random.randint(0, 100),
                    random.randint(0, 100)
                )

                # 随机位置（稍微偏移）
                x = 20 + i * 20 + random.randint(-3, 3)
                y = 8 + random.randint(-3, 3)

                draw.text((x, y), char, font=font, fill=color)

            # 添加干扰线
            for _ in range(3):
                start = (random.randint(0, width), random.randint(0, height))
                end = (random.randint(0, width), random.randint(0, height))
                draw.line([start, end], fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)), width=1)

            # 添加干扰点
            for _ in range(20):
                x = random.randint(0, width)
                y = random.randint(0, height)
                draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))

            # 转换为base64
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode()

            return captcha_text, f"data:image/png;base64,{img_base64}"

        except Exception as e:
            logger.error(f"生成图形验证码失败: {e}")
            # 返回简单的文本验证码作为备用
            simple_code = ''.join(random.choices(string.digits, k=4))
            return simple_code, ""

    def save_captcha(self, session_id: str, captcha_text: str, expires_minutes: int = 5) -> bool:
        """保存图形验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                expires_at = time.time() + (expires_minutes * 60)

                # 删除该session的旧验证码
                cursor.execute('DELETE FROM captcha_codes WHERE session_id = ?', (session_id,))

                cursor.execute('''
                INSERT INTO captcha_codes (session_id, code, expires_at)
                VALUES (?, ?, ?)
                ''', (session_id, captcha_text.upper(), expires_at))

                self.conn.commit()
                logger.debug(f"保存图形验证码成功: {session_id}")
                return True
            except Exception as e:
                logger.error(f"保存图形验证码失败: {e}")
                self.conn.rollback()
                return False

    def verify_captcha(self, session_id: str, user_input: str) -> bool:
        """验证图形验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                current_time = time.time()

                # 查找有效的验证码
                cursor.execute('''
                SELECT id FROM captcha_codes
                WHERE session_id = ? AND code = ? AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
                ''', (session_id, user_input.upper(), current_time))

                row = cursor.fetchone()
                if row:
                    # 删除已使用的验证码
                    cursor.execute('DELETE FROM captcha_codes WHERE id = ?', (row[0],))
                    self.conn.commit()
                    logger.debug(f"图形验证码验证成功: {session_id}")
                    return True
                else:
                    logger.warning(f"兼容图形验证码验证失败: {session_id}")
                    return False
            except Exception as e:
                logger.error(f"验证图形验证码失败: {e}")
                return False

    def save_verification_code(self, email: str, code: str, code_type: str = 'register', expires_minutes: int = 10) -> bool:
        """保存邮箱验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                expires_at = time.time() + (expires_minutes * 60)

                cursor.execute('''
                INSERT INTO email_verifications (email, code, type, expires_at)
                VALUES (?, ?, ?, ?)
                ''', (email, code, code_type, expires_at))

                self.conn.commit()
                logger.info(
                    f"保存兼容验证码成功: {mask_email_for_log(email)} "
                    f"({code_type})"
                )
                return True
            except Exception as e:
                logger.error(f"保存验证码失败: {e}")
                self.conn.rollback()
                return False

    def verify_email_code(self, email: str, code: str, code_type: str = 'register') -> bool:
        """验证邮箱验证码"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                current_time = time.time()

                # 查找有效的验证码
                cursor.execute('''
                SELECT id FROM email_verifications
                WHERE email = ? AND code = ? AND type = ? AND expires_at > ? AND used = FALSE
                ORDER BY created_at DESC LIMIT 1
                ''', (email, code, code_type, current_time))

                row = cursor.fetchone()
                if row:
                    # 标记验证码为已使用
                    cursor.execute('''
                    UPDATE email_verifications SET used = TRUE WHERE id = ?
                    ''', (row[0],))
                    self.conn.commit()
                    logger.info(
                        f"兼容验证码验证成功: {mask_email_for_log(email)} "
                        f"({code_type})"
                    )
                    return True
                else:
                    logger.warning(
                        f"兼容验证码验证失败: {mask_email_for_log(email)} "
                        f"({code_type})"
                    )
                    return False
            except Exception as e:
                logger.error(f"验证邮箱验证码失败: {e}")
                return False

    async def send_verification_email(self, email: str, code: str) -> bool:
        """Compatibility wrapper that sends only through verified SMTP."""
        try:
            settings = self.get_all_system_settings()
            status = smtp_configuration_status(settings, db_path=self.db_path)
            if not status['smtp_verified']:
                logger.warning("SMTP 未验证，拒绝发送兼容验证码邮件")
                return False
            await asyncio.to_thread(
                SMTPEmailSender().send,
                settings,
                recipient=email,
                subject="闲鱼监控台邮箱验证码",
                text=(
                    f"您的验证码是 {code}\n\n"
                    "验证码在 10 分钟内有效，请勿向任何人泄露。"
                ),
            )
            logger.info(
                f"兼容验证码邮件已提交 email={mask_email_for_log(email)}"
            )
            return True
        except (SMTPConfigurationError, SMTPDeliveryError) as e:
            logger.warning(f"兼容验证码邮件发送失败 type={type(e).__name__}")
            return False
        except Exception as e:
            logger.error(f"兼容验证码邮件异常 type={type(e).__name__}")
            return False

    # ==================== 卡券管理方法 ====================

    @staticmethod
    def _card_api_config_dict(api_config: Any) -> Optional[Dict[str, Any]]:
        if api_config is None or api_config == "":
            return None
        if isinstance(api_config, str):
            try:
                api_config = json.loads(api_config)
            except (TypeError, ValueError) as exc:
                raise ValueError("API 配置必须是 JSON 对象") from exc
        if not isinstance(api_config, dict):
            raise ValueError("API 配置必须是对象")
        return dict(api_config)

    def _prepare_card_api_storage(
        self,
        card_type: str,
        api_config: Any,
        api_token: Optional[str],
    ) -> tuple[Optional[str], str, int, str]:
        config = self._card_api_config_dict(api_config)
        if str(card_type or "") != "api":
            serialized = (
                json.dumps(config, ensure_ascii=False, separators=(",", ":"))
                if config is not None
                else None
            )
            return serialized, "", 0, "unvalidated"

        config = config or {}
        protocol = str(config.get("protocol") or "").strip()
        embedded_token = ""
        if protocol == FULFILLMENT_API_PROTOCOL:
            embedded_token = str(
                config.pop("api_token", config.pop("token", "")) or ""
            ).strip()
            allowed = {"protocol", "url", "method", "timeout", "spec"}
            if set(config) - allowed:
                raise ValueError("幂等 API 只允许 protocol、url、method、timeout 和 spec")
            url = str(config.get("url") or "").strip()
            if not url.lower().startswith("https://"):
                raise ValueError("幂等 API 地址必须使用 HTTPS")
            method = str(config.get("method") or "POST").strip().upper()
            if method != "POST":
                raise ValueError("幂等 API 只支持 POST")
            try:
                timeout = int(config.get("timeout", 10))
            except (TypeError, ValueError) as exc:
                raise ValueError("API 超时时间无效") from exc
            if timeout < 1 or timeout > 30:
                raise ValueError("API 超时时间必须在 1 到 30 秒之间")
            spec = config.get("spec") or {}
            if not isinstance(spec, dict):
                raise ValueError("API spec 必须是对象")
            config = {
                "protocol": FULFILLMENT_API_PROTOCOL,
                "url": url,
                "method": "POST",
                "timeout": timeout,
                "spec": spec,
            }
            validation_status = "unvalidated"
        else:
            # Arbitrary legacy configs remain stored for manual inspection but
            # never become bindable automatic resources.
            validation_status = "manual_only"

        token = str(api_token if api_token is not None else embedded_token).strip()
        encrypted = SystemSecretCipher(self.db_path).encrypt(token) if token else ""
        version = SYSTEM_SECRET_ENCRYPTION_VERSION if encrypted else 0
        serialized = json.dumps(
            config,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return serialized, encrypted, version, validation_status

    @staticmethod
    def _public_card_api_config(raw_config: Any) -> Optional[Dict[str, Any]]:
        if raw_config is None or raw_config == "":
            return None
        try:
            config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
        except (TypeError, ValueError):
            return {"legacy": True}
        if not isinstance(config, dict):
            return {"legacy": True}
        allowed = ("protocol", "url", "method", "timeout", "spec")
        return {key: config[key] for key in allowed if key in config}

    def _card_token_preview(self, encrypted: Any) -> str:
        value = str(encrypted or "")
        if not value:
            return ""
        try:
            return mask_secret_preview(SystemSecretCipher(self.db_path).decrypt(value))
        except ValueError:
            return "***"

    @staticmethod
    def _card_stock_stats(
        cursor: sqlite3.Cursor,
        *,
        card_id: int,
        card_type: str,
        data_content: Any,
        low_stock_threshold: int,
    ) -> Dict[str, Any]:
        available = len(
            [line for line in str(data_content or "").splitlines() if line.strip()]
        ) if card_type == "data" else 0
        counts = {
            str(state): int(count)
            for state, count in cursor.execute(
                "SELECT state, COUNT(*) FROM fulfillment_card_reservations "
                "WHERE card_id = ? GROUP BY state",
                (int(card_id),),
            ).fetchall()
        }
        bound = cursor.execute(
            "SELECT COUNT(*) FROM item_info WHERE delivery_card_id = ?",
            (int(card_id),),
        ).fetchone()
        threshold = max(0, int(low_stock_threshold or 0))
        return {
            "available": available,
            "reserved": counts.get("reserved", 0),
            "used": counts.get("committed", 0),
            "review": counts.get("manual_review", 0),
            "bound": int(bound[0] if bound else 0),
            "low_stock": card_type == "data" and available <= threshold,
        }

    def _decode_card_row(
        self,
        cursor: sqlite3.Cursor,
        row: Sequence[Any],
    ) -> Dict[str, Any]:
        (
            card_id,
            name,
            card_type,
            api_config,
            text_content,
            data_content,
            image_url,
            description,
            enabled,
            delay_seconds,
            is_multi_spec,
            spec_name,
            spec_value,
            created_at,
            updated_at,
            low_stock_threshold,
            api_token_encrypted,
            _api_token_encryption_version,
            api_validation_status,
            api_validated_at,
        ) = row
        stats = self._card_stock_stats(
            cursor,
            card_id=int(card_id),
            card_type=str(card_type),
            data_content=data_content,
            low_stock_threshold=int(low_stock_threshold or 0),
        )
        return {
            "id": int(card_id),
            "name": name,
            "type": card_type,
            "api_config": self._public_card_api_config(api_config),
            "text_content": text_content,
            "data_content": data_content,
            "image_url": image_url,
            "description": description,
            "enabled": bool(enabled),
            "delay_seconds": int(delay_seconds or 0),
            "is_multi_spec": bool(is_multi_spec),
            "spec_name": spec_name,
            "spec_value": spec_value,
            "created_at": created_at,
            "updated_at": updated_at,
            "low_stock_threshold": int(low_stock_threshold or 0),
            "api_token_configured": bool(api_token_encrypted),
            "token_preview": self._card_token_preview(api_token_encrypted),
            "api_validation_status": str(api_validation_status or "unvalidated"),
            "api_validated_at": api_validated_at,
            "stats": stats,
        }

    def create_card(self, name: str, card_type: str, api_config=None,
                   text_content: str = None, data_content: str = None, image_url: str = None,
                   description: str = None, enabled: bool = True, delay_seconds: int = 0,
                   is_multi_spec: bool = False, spec_name: str = None, spec_value: str = None,
                   user_id: int = None, api_token: Optional[str] = None,
                   low_stock_threshold: int = 5):
        """创建新卡券（支持多规格）"""
        with self.lock:
            try:
                # 验证多规格参数
                if is_multi_spec:
                    if not spec_name or not spec_value:
                        raise ValueError("多规格卡券必须提供规格名称和规格值")

                    # 检查唯一性：卡券名称+规格名称+规格值
                    cursor = self.conn.cursor()
                    cursor.execute('''
                    SELECT COUNT(*) FROM cards
                    WHERE name = ? AND spec_name = ? AND spec_value = ? AND user_id = ?
                    ''', (name, spec_name, spec_value, user_id))

                    if cursor.fetchone()[0] > 0:
                        raise ValueError(f"卡券已存在：{name} - {spec_name}:{spec_value}")
                else:
                    # 检查唯一性：仅卡券名称
                    cursor = self.conn.cursor()
                    cursor.execute('''
                    SELECT COUNT(*) FROM cards
                    WHERE name = ? AND (is_multi_spec = 0 OR is_multi_spec IS NULL) AND user_id = ?
                    ''', (name, user_id))

                    if cursor.fetchone()[0] > 0:
                        raise ValueError(f"卡券名称已存在：{name}")

                try:
                    low_stock_threshold = int(low_stock_threshold)
                except (TypeError, ValueError) as exc:
                    raise ValueError("低库存阈值必须是整数") from exc
                if low_stock_threshold < 0:
                    raise ValueError("低库存阈值不能小于 0")
                (
                    api_config_str,
                    api_token_encrypted,
                    api_token_encryption_version,
                    api_validation_status,
                ) = self._prepare_card_api_storage(card_type, api_config, api_token)

                cursor.execute('''
                INSERT INTO cards (name, type, api_config, text_content, data_content, image_url,
                                 description, enabled, delay_seconds, is_multi_spec,
                                 spec_name, spec_value, user_id, low_stock_threshold,
                                 api_token_encrypted, api_token_encryption_version,
                                 api_validation_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name, card_type, api_config_str, text_content, data_content, image_url,
                      description, enabled, delay_seconds, is_multi_spec,
                      spec_name, spec_value, user_id, low_stock_threshold,
                      api_token_encrypted, api_token_encryption_version,
                      api_validation_status))
                self.conn.commit()
                card_id = cursor.lastrowid

                if is_multi_spec:
                    logger.info(f"创建多规格卡券成功: {name} - {spec_name}:{spec_value} (ID: {card_id})")
                else:
                    logger.info(f"创建卡券成功: {name} (ID: {card_id})")
                return card_id
            except Exception as e:
                logger.error(f"创建卡券失败: {e}")
                raise

    def get_all_cards(self, user_id: int = None):
        """获取所有卡券（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                columns = (
                    "id, name, type, api_config, text_content, data_content, image_url, "
                    "description, enabled, delay_seconds, is_multi_spec, spec_name, "
                    "spec_value, created_at, updated_at, low_stock_threshold, "
                    "api_token_encrypted, api_token_encryption_version, "
                    "api_validation_status, api_validated_at"
                )
                if user_id is not None:
                    cursor.execute(
                        f"SELECT {columns} FROM cards WHERE user_id = ? "
                        "ORDER BY created_at DESC, id DESC",
                        (int(user_id),),
                    )
                else:
                    cursor.execute(
                        f"SELECT {columns} FROM cards ORDER BY created_at DESC, id DESC"
                    )
                rows = cursor.fetchall()
                return [self._decode_card_row(cursor, row) for row in rows]
            except Exception as e:
                logger.error(f"获取卡券列表失败: {e}")
                return []

    def get_card_by_id(self, card_id: int, user_id: int = None):
        """根据ID获取卡券（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                columns = (
                    "id, name, type, api_config, text_content, data_content, image_url, "
                    "description, enabled, delay_seconds, is_multi_spec, spec_name, "
                    "spec_value, created_at, updated_at, low_stock_threshold, "
                    "api_token_encrypted, api_token_encryption_version, "
                    "api_validation_status, api_validated_at"
                )
                if user_id is not None:
                    cursor.execute(
                        f"SELECT {columns} FROM cards WHERE id = ? AND user_id = ?",
                        (int(card_id), int(user_id)),
                    )
                else:
                    cursor.execute(
                        f"SELECT {columns} FROM cards WHERE id = ?",
                        (int(card_id),),
                    )

                row = cursor.fetchone()
                return self._decode_card_row(cursor, row) if row else None
            except Exception as e:
                logger.error(f"获取卡券失败: {e}")
                return None

    def update_card(self, card_id: int, name: str = None, card_type: str = None,
                   api_config=None, text_content: str = None, data_content: str = None,
                   image_url: str = None, description: str = None, enabled: bool = None,
                   delay_seconds: int = None, is_multi_spec: bool = None, spec_name: str = None,
                   spec_value: str = None, user_id: int = None,
                   api_token: Optional[str] = None,
                   low_stock_threshold: Optional[int] = None):
        """更新卡券（支持用户隔离：提供 user_id 时只能改自己的卡券）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                owner_clause = " AND user_id = ?" if user_id is not None else ""
                owner_params = [int(user_id)] if user_id is not None else []
                current = cursor.execute(
                    "SELECT type, api_config, api_token_encrypted FROM cards "
                    f"WHERE id = ?{owner_clause}",
                    [int(card_id), *owner_params],
                ).fetchone()
                if not current:
                    return False

                # 构建更新语句
                update_fields = []
                params = []

                if name is not None:
                    update_fields.append("name = ?")
                    params.append(name)
                if card_type is not None:
                    update_fields.append("type = ?")
                    params.append(card_type)
                if api_config is not None or api_token is not None or card_type is not None:
                    effective_type = str(card_type or current[0])
                    config_input = api_config if api_config is not None else current[1]
                    if api_token is None and current[2]:
                        existing_token = SystemSecretCipher(self.db_path).decrypt(
                            str(current[2])
                        )
                    else:
                        existing_token = api_token
                    (
                        api_config_str,
                        encrypted_token,
                        encryption_version,
                        validation_status,
                    ) = self._prepare_card_api_storage(
                        effective_type,
                        config_input,
                        existing_token,
                    )
                    update_fields.extend(
                        [
                            "api_config = ?",
                            "api_token_encrypted = ?",
                            "api_token_encryption_version = ?",
                            "api_validation_status = ?",
                            "api_validated_at = NULL",
                        ]
                    )
                    params.extend(
                        [
                            api_config_str,
                            encrypted_token,
                            encryption_version,
                            validation_status,
                        ]
                    )
                if text_content is not None:
                    update_fields.append("text_content = ?")
                    params.append(text_content)
                if data_content is not None:
                    update_fields.append("data_content = ?")
                    params.append(data_content)
                if image_url is not None:
                    update_fields.append("image_url = ?")
                    params.append(image_url)
                if description is not None:
                    update_fields.append("description = ?")
                    params.append(description)
                if enabled is not None:
                    update_fields.append("enabled = ?")
                    params.append(enabled)
                if delay_seconds is not None:
                    update_fields.append("delay_seconds = ?")
                    params.append(delay_seconds)
                if is_multi_spec is not None:
                    update_fields.append("is_multi_spec = ?")
                    params.append(is_multi_spec)
                if spec_name is not None:
                    update_fields.append("spec_name = ?")
                    params.append(spec_name)
                if spec_value is not None:
                    update_fields.append("spec_value = ?")
                    params.append(spec_value)
                if low_stock_threshold is not None:
                    try:
                        normalized_threshold = int(low_stock_threshold)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("低库存阈值必须是整数") from exc
                    if normalized_threshold < 0:
                        raise ValueError("低库存阈值不能小于 0")
                    update_fields.append("low_stock_threshold = ?")
                    params.append(normalized_threshold)

                if not update_fields:
                    return True  # 没有需要更新的字段

                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(card_id)

                # 用户隔离：提供 user_id 时把归属纳入 WHERE，越权改动直接 0 行命中
                if user_id is not None:
                    sql = f"UPDATE cards SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
                    params.append(user_id)
                else:
                    sql = f"UPDATE cards SET {', '.join(update_fields)} WHERE id = ?"
                self._execute_sql(cursor, sql, params)

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新卡券成功: ID {card_id}")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"更新卡券失败: {e}")
                self.conn.rollback()
                raise

    def update_card_image_url(self, card_id: int, new_image_url: str) -> bool:
        """更新卡券的图片URL"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 更新图片URL
                self._execute_sql(cursor,
                    "UPDATE cards SET image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND type = 'image'",
                    (new_image_url, card_id))

                self.conn.commit()

                # 检查是否有行被更新
                if cursor.rowcount > 0:
                    logger.info(f"卡券图片URL更新成功: 卡券ID: {card_id}, 新URL: {new_image_url}")
                    return True
                else:
                    logger.warning(f"未找到匹配的图片卡券: 卡券ID: {card_id}")
                    return False

            except Exception as e:
                logger.error(f"更新卡券图片URL失败: {e}")
                self.conn.rollback()
                return False

    def get_card_api_runtime_config(
        self,
        card_id: int,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return the fixed v1 provider config only to the fulfillment runtime."""
        with self.lock:
            try:
                row = self.conn.execute(
                    "SELECT api_config, api_token_encrypted, enabled, "
                    "api_validation_status FROM cards "
                    "WHERE id = ? AND user_id = ? AND type = 'api'",
                    (int(card_id), int(user_id)),
                ).fetchone()
                if not row:
                    return None
                config = self._card_api_config_dict(row[0]) or {}
                token = SystemSecretCipher(self.db_path).decrypt(str(row[1] or ""))
                canonical = json.dumps(
                    {"config": config, "token": token},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                fingerprint = SystemSecretCipher(self.db_path).digest(
                    canonical,
                    purpose="fulfillment-api-config-v1",
                )
                return {
                    **config,
                    "api_token": token,
                    "enabled": bool(row[2]),
                    "validation_status": str(row[3] or "unvalidated"),
                    "config_fingerprint": fingerprint,
                }
            except (TypeError, ValueError) as exc:
                logger.warning("读取 API 运行配置失败 type={}", type(exc).__name__)
                return None

    def set_card_api_validation(
        self,
        card_id: int,
        user_id: int,
        status: str,
        *,
        api_token: Optional[str] = None,
    ) -> bool:
        normalized = str(status or "").strip()
        if normalized not in {"validated", "unvalidated", "failed", "manual_only"}:
            raise ValueError("API 校验状态无效")
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                row = cursor.execute(
                    "SELECT api_token_encrypted FROM cards "
                    "WHERE id = ? AND user_id = ? AND type = 'api'",
                    (int(card_id), int(user_id)),
                ).fetchone()
                if not row:
                    self.conn.rollback()
                    return False
                fields = [
                    "api_validation_status = ?",
                    "api_validated_at = ?",
                    "updated_at = CURRENT_TIMESTAMP",
                ]
                params: List[Any] = [
                    normalized,
                    time.time() if normalized == "validated" else None,
                ]
                if api_token is not None:
                    token = str(api_token).strip()
                    encrypted = (
                        SystemSecretCipher(self.db_path).encrypt(token) if token else ""
                    )
                    fields.extend(
                        [
                            "api_token_encrypted = ?",
                            "api_token_encryption_version = ?",
                        ]
                    )
                    params.extend(
                        [
                            encrypted,
                            SYSTEM_SECRET_ENCRYPTION_VERSION if encrypted else 0,
                        ]
                    )
                params.extend([int(card_id), int(user_id)])
                cursor.execute(
                    f"UPDATE cards SET {', '.join(fields)} "
                    "WHERE id = ? AND user_id = ?",
                    params,
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception:
                self.conn.rollback()
                raise

    def import_card_stock(
        self,
        card_id: int,
        user_id: int,
        values: Sequence[Any],
    ) -> Dict[str, Any]:
        """Append unique one-time values without ever recycling historical values."""
        if isinstance(values, (str, bytes)):
            values = str(values).splitlines()
        candidates = list(values)
        if len(candidates) > CARD_STOCK_IMPORT_MAX_ITEMS:
            raise ValueError("单批补货不能超过 10000 条")

        normalized: List[str] = []
        blank = 0
        invalid = 0
        for value in candidates:
            item = str(value if value is not None else "").strip()
            if not item:
                blank += 1
                continue
            if len(item.encode("utf-8")) > CARD_STOCK_ITEM_MAX_BYTES:
                invalid += 1
                continue
            normalized.append(item)

        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                card = cursor.execute(
                    "SELECT type, data_content, low_stock_threshold FROM cards "
                    "WHERE id = ? AND user_id = ?",
                    (int(card_id), int(user_id)),
                ).fetchone()
                if not card:
                    self.conn.rollback()
                    raise LookupError("资源不存在")
                if str(card[0]) != "data":
                    self.conn.rollback()
                    raise ValueError("只有一次一密资源可以补货")

                existing_values = [
                    line.strip()
                    for line in str(card[1] or "").splitlines()
                    if line.strip()
                ]
                historical = {
                    str(row[0])
                    for row in cursor.execute(
                        "SELECT value FROM fulfillment_card_reservations "
                        "WHERE card_id = ?",
                        (int(card_id),),
                    ).fetchall()
                }
                seen = set(existing_values) | historical
                added: List[str] = []
                duplicates = 0
                for value in normalized:
                    if value in seen:
                        duplicates += 1
                        continue
                    seen.add(value)
                    added.append(value)
                if added:
                    cursor.execute(
                        "UPDATE cards SET data_content = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND user_id = ?",
                        (
                            "\n".join([*existing_values, *added]),
                            int(card_id),
                            int(user_id),
                        ),
                    )
                stats = self._card_stock_stats(
                    cursor,
                    card_id=int(card_id),
                    card_type="data",
                    data_content="\n".join([*existing_values, *added]),
                    low_stock_threshold=int(card[2] or 0),
                )
                self.conn.commit()
                return {
                    "added": len(added),
                    "duplicates": duplicates,
                    "blank": blank,
                    "invalid": invalid,
                    "total": len(candidates),
                    "stats": stats,
                }
            except Exception:
                self.conn.rollback()
                raise

    def get_card_delete_blockers(
        self,
        card_id: int,
        user_id: Optional[int] = None,
    ) -> List[str]:
        with self.lock:
            cursor = self.conn.cursor()
            owner_clause = " AND user_id = ?" if user_id is not None else ""
            params: List[Any] = [int(card_id)]
            if user_id is not None:
                params.append(int(user_id))
            if not cursor.execute(
                f"SELECT 1 FROM cards WHERE id = ?{owner_clause}", params
            ).fetchone():
                return ["not_found"]
            blockers: List[str] = []
            if cursor.execute(
                "SELECT 1 FROM item_info WHERE delivery_card_id = ? LIMIT 1",
                (int(card_id),),
            ).fetchone():
                blockers.append("item_binding")
            if cursor.execute(
                "SELECT 1 FROM delivery_rules WHERE card_id = ? LIMIT 1",
                (int(card_id),),
            ).fetchone():
                blockers.append("legacy_rule")
            if cursor.execute(
                "SELECT 1 FROM fulfillment_card_reservations "
                "WHERE card_id = ? LIMIT 1",
                (int(card_id),),
            ).fetchone():
                blockers.append("fulfillment_history")
            if cursor.execute(
                "SELECT 1 FROM fulfillment_api_operations "
                "WHERE card_id = ? LIMIT 1",
                (int(card_id),),
            ).fetchone():
                blockers.append("api_history")
            if cursor.execute(
                "SELECT 1 FROM fulfillment_delivery_payloads "
                "WHERE source_card_id = ? LIMIT 1",
                (int(card_id),),
            ).fetchone():
                blockers.append("payload_history")
            return blockers

    # ==================== 自动发货规则方法 ====================

    def _assert_card_owned_by_user(self, cursor, card_id: int, user_id: int):
        """校验卡券归属：不存在或属于其他用户时抛 ValueError（防止跨租户绑定卡券）"""
        self._execute_sql(cursor, "SELECT user_id FROM cards WHERE id = ?", (card_id,))
        row = cursor.fetchone()
        if not row or row[0] != user_id:
            raise ValueError(f"卡券 {card_id} 不存在或无权绑定")

    def create_delivery_rule(self, keyword: str, card_id: int, delivery_count: int = 1,
                           enabled: bool = True, description: str = None, user_id: int = None):
        """创建发货规则（提供 user_id 时校验卡券归属）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None and card_id is not None:
                    self._assert_card_owned_by_user(cursor, card_id, user_id)
                cursor.execute('''
                INSERT INTO delivery_rules (keyword, card_id, delivery_count, enabled, description, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (keyword, card_id, delivery_count, enabled, description, user_id))
                self.conn.commit()
                rule_id = cursor.lastrowid
                logger.info(f"创建发货规则成功: {keyword} -> 卡券ID {card_id} (规则ID: {rule_id})")
                return rule_id
            except Exception as e:
                logger.error(f"创建发货规则失败: {e}")
                raise

    def get_all_delivery_rules(self, user_id: int = None):
        """获取所有发货规则"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    cursor.execute('''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.user_id = ?
                    ORDER BY dr.created_at DESC
                    ''', (user_id,))
                else:
                    cursor.execute('''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type,
                           c.is_multi_spec, c.spec_name, c.spec_value
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    ORDER BY dr.created_at DESC
                    ''')

                rules = []
                for row in cursor.fetchall():
                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'created_at': row[7],
                        'updated_at': row[8],
                        'card_name': row[9],
                        'card_type': row[10],
                        'is_multi_spec': bool(row[11]) if row[11] is not None else False,
                        'spec_name': row[12],
                        'spec_value': row[13]
                    })

                return rules
            except Exception as e:
                logger.error(f"获取发货规则列表失败: {e}")
                return []

    def get_delivery_rules_by_keyword(self, keyword: str, user_id: int = None):
        """根据关键字获取匹配的发货规则（强制按用户隔离）"""
        # 自动发货匹配必须限定租户，否则用户 A 的商品会命中用户 B 的规则，
        # 把 B 的卡券内容发给 A 的买家（内容泄露 + 消耗他人库存），fail-closed。
        if user_id is None:
            raise ValueError("get_delivery_rules_by_keyword 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                # 使用更灵活的匹配方式：既支持商品内容包含关键字，也支持关键字包含在商品内容中
                cursor.execute('''
                SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                       dr.description, dr.delivery_times,
                       c.name as card_name, c.type as card_type, c.api_config,
                       c.text_content, c.data_content, c.image_url, c.enabled as card_enabled, c.description as card_description,
                       c.delay_seconds as card_delay_seconds,
                       c.is_multi_spec, c.spec_name, c.spec_value
                FROM delivery_rules dr
                LEFT JOIN cards c ON dr.card_id = c.id
                WHERE dr.enabled = 1 AND c.enabled = 1
                AND dr.user_id = ?
                AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                ORDER BY
                    CASE
                        WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                        ELSE LENGTH(dr.keyword) / 2
                    END DESC,
                    dr.id ASC
                ''', (user_id, keyword, keyword, keyword))

                rules = []
                for row in cursor.fetchall():
                    # 解析api_config JSON字符串
                    api_config = row[9]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'card_name': row[7],
                        'card_type': row[8],
                        'api_config': api_config,  # 修复字段名
                        'text_content': row[10],
                        'data_content': row[11],
                        'image_url': row[12],
                        'card_enabled': bool(row[13]),
                        'card_description': row[14],  # 卡券备注信息
                        'card_delay_seconds': row[15] or 0,  # 延时秒数
                        'is_multi_spec': bool(row[16]) if row[16] is not None else False,
                        'spec_name': row[17],
                        'spec_value': row[18]
                    })

                return rules
            except Exception as e:
                logger.error(f"根据关键字获取发货规则失败: {e}")
                return []

    def get_delivery_rule_by_id(self, rule_id: int, user_id: int = None):
        """根据ID获取发货规则（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, '''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.id = ? AND dr.user_id = ?
                    ''', (rule_id, user_id))
                else:
                    self._execute_sql(cursor, '''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times, dr.created_at, dr.updated_at,
                           c.name as card_name, c.type as card_type
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.id = ?
                    ''', (rule_id,))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6],
                        'created_at': row[7],
                        'updated_at': row[8],
                        'card_name': row[9],
                        'card_type': row[10]
                    }
                return None
            except Exception as e:
                logger.error(f"获取发货规则失败: {e}")
                return None

    def update_delivery_rule(self, rule_id: int, keyword: str = None, card_id: int = None,
                           delivery_count: int = None, enabled: bool = None,
                           description: str = None, user_id: int = None):
        """更新发货规则（支持用户隔离；改绑卡券时校验卡券归属）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                if user_id is not None and card_id is not None:
                    self._assert_card_owned_by_user(cursor, card_id, user_id)

                # 构建更新语句
                update_fields = []
                params = []

                if keyword is not None:
                    update_fields.append("keyword = ?")
                    params.append(keyword)
                if card_id is not None:
                    update_fields.append("card_id = ?")
                    params.append(card_id)
                if delivery_count is not None:
                    update_fields.append("delivery_count = ?")
                    params.append(delivery_count)
                if enabled is not None:
                    update_fields.append("enabled = ?")
                    params.append(enabled)
                if description is not None:
                    update_fields.append("description = ?")
                    params.append(description)

                if not update_fields:
                    return True  # 没有需要更新的字段

                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(rule_id)

                if user_id is not None:
                    params.append(user_id)
                    sql = f"UPDATE delivery_rules SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
                else:
                    sql = f"UPDATE delivery_rules SET {', '.join(update_fields)} WHERE id = ?"

                self._execute_sql(cursor, sql, params)

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新发货规则成功: ID {rule_id}")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"更新发货规则失败: {e}")
                self.conn.rollback()
                raise

    def increment_delivery_times(self, rule_id: int):
        """增加发货次数"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE delivery_rules
                SET delivery_times = delivery_times + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (rule_id,))
                self.conn.commit()
                logger.debug(f"发货规则 {rule_id} 发货次数已增加")
            except Exception as e:
                logger.error(f"更新发货次数失败: {e}")

    def get_delivery_rules_by_keyword_and_spec(self, keyword: str, spec_name: str = None, spec_value: str = None, user_id: int = None):
        """根据关键字和规格信息获取匹配的发货规则（支持多规格，强制按用户隔离）"""
        # 同 get_delivery_rules_by_keyword：自动发货匹配必须限定租户，fail-closed。
        if user_id is None:
            raise ValueError("get_delivery_rules_by_keyword_and_spec 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 优先匹配：卡券名称+规格名称+规格值
                if spec_name and spec_value:
                    cursor.execute('''
                    SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                           dr.description, dr.delivery_times,
                           c.name as card_name, c.type as card_type, c.api_config,
                           c.text_content, c.data_content, c.enabled as card_enabled,
                           c.description as card_description, c.delay_seconds as card_delay_seconds,
                           c.is_multi_spec, c.spec_name, c.spec_value
                    FROM delivery_rules dr
                    LEFT JOIN cards c ON dr.card_id = c.id
                    WHERE dr.enabled = 1 AND c.enabled = 1
                    AND dr.user_id = ?
                    AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                    AND c.is_multi_spec = 1 AND c.spec_name = ? AND c.spec_value = ?
                    ORDER BY
                        CASE
                            WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                            ELSE LENGTH(dr.keyword) / 2
                        END DESC,
                        dr.delivery_times ASC
                    ''', (user_id, keyword, keyword, spec_name, spec_value, keyword))

                    rules = []
                    for row in cursor.fetchall():
                        # 解析api_config JSON字符串
                        api_config = row[9]
                        if api_config:
                            try:
                                import json
                                api_config = json.loads(api_config)
                            except (json.JSONDecodeError, TypeError):
                                # 如果解析失败，保持原始字符串
                                pass

                        rules.append({
                            'id': row[0],
                            'keyword': row[1],
                            'card_id': row[2],
                            'delivery_count': row[3],
                            'enabled': bool(row[4]),
                            'description': row[5],
                            'delivery_times': row[6] or 0,
                            'card_name': row[7],
                            'card_type': row[8],
                            'api_config': api_config,
                            'text_content': row[10],
                            'data_content': row[11],
                            'card_enabled': bool(row[12]),
                            'card_description': row[13],
                            'card_delay_seconds': row[14] or 0,
                            'is_multi_spec': bool(row[15]),
                            'spec_name': row[16],
                            'spec_value': row[17]
                        })

                    if rules:
                        logger.info(f"找到多规格匹配规则: {keyword} - {spec_name}:{spec_value}")
                        return rules

                # 兜底匹配：仅卡券名称
                cursor.execute('''
                SELECT dr.id, dr.keyword, dr.card_id, dr.delivery_count, dr.enabled,
                       dr.description, dr.delivery_times,
                       c.name as card_name, c.type as card_type, c.api_config,
                       c.text_content, c.data_content, c.enabled as card_enabled,
                       c.description as card_description, c.delay_seconds as card_delay_seconds,
                       c.is_multi_spec, c.spec_name, c.spec_value
                FROM delivery_rules dr
                LEFT JOIN cards c ON dr.card_id = c.id
                WHERE dr.enabled = 1 AND c.enabled = 1
                AND dr.user_id = ?
                AND (? LIKE '%' || dr.keyword || '%' OR dr.keyword LIKE '%' || ? || '%')
                AND (c.is_multi_spec = 0 OR c.is_multi_spec IS NULL)
                ORDER BY
                    CASE
                        WHEN ? LIKE '%' || dr.keyword || '%' THEN LENGTH(dr.keyword)
                        ELSE LENGTH(dr.keyword) / 2
                    END DESC,
                    dr.delivery_times ASC
                ''', (user_id, keyword, keyword, keyword))

                rules = []
                for row in cursor.fetchall():
                    # 解析api_config JSON字符串
                    api_config = row[9]
                    if api_config:
                        try:
                            import json
                            api_config = json.loads(api_config)
                        except (json.JSONDecodeError, TypeError):
                            # 如果解析失败，保持原始字符串
                            pass

                    rules.append({
                        'id': row[0],
                        'keyword': row[1],
                        'card_id': row[2],
                        'delivery_count': row[3],
                        'enabled': bool(row[4]),
                        'description': row[5],
                        'delivery_times': row[6] or 0,
                        'card_name': row[7],
                        'card_type': row[8],
                        'api_config': api_config,
                        'text_content': row[10],
                        'data_content': row[11],
                        'card_enabled': bool(row[12]),
                        'card_description': row[13],
                        'card_delay_seconds': row[14] or 0,
                        'is_multi_spec': bool(row[15]) if row[15] is not None else False,
                        'spec_name': row[16],
                        'spec_value': row[17]
                    })

                if rules:
                    logger.info(f"找到兜底匹配规则: {keyword}")
                else:
                    logger.info(f"未找到匹配规则: {keyword}")

                return rules

            except Exception as e:
                logger.error(f"获取发货规则失败: {e}")
                return []

    def delete_card(self, card_id: int, user_id: int = None):
        """删除卡券（支持用户隔离：提供 user_id 时只能删自己的卡券）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if self.get_card_delete_blockers(card_id, user_id):
                    return False
                if user_id is not None:
                    self._execute_sql(cursor, "DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM cards WHERE id = ?", (card_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除卡券成功: ID {card_id}")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"删除卡券失败: {e}")
                self.conn.rollback()
                raise

    def delete_delivery_rule(self, rule_id: int, user_id: int = None):
        """删除发货规则（支持用户隔离）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                if user_id is not None:
                    self._execute_sql(cursor, "DELETE FROM delivery_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
                else:
                    self._execute_sql(cursor, "DELETE FROM delivery_rules WHERE id = ?", (rule_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除发货规则成功: ID {rule_id} (用户ID: {user_id})")
                    return True
                else:
                    return False  # 没有找到对应的记录

            except Exception as e:
                logger.error(f"删除发货规则失败: {e}")
                self.conn.rollback()
                raise

    _FULFILLMENT_ATTEMPT_COLUMNS = (
        "id",
        "order_id",
        "cookie_id",
        "user_id",
        "expected_quantity",
        "state",
        "owner_token",
        "lease_expires_at",
        "reason_code",
        "sent_count",
        "delivered_count",
        "attempt_count",
        "sending_at",
        "completed_at",
        "created_at",
        "updated_at",
    )

    @classmethod
    def _decode_fulfillment_attempt(cls, row: tuple) -> Dict[str, Any]:
        attempt = dict(zip(cls._FULFILLMENT_ATTEMPT_COLUMNS, row))
        for key in (
            "id",
            "user_id",
            "expected_quantity",
            "sent_count",
            "delivered_count",
            "attempt_count",
        ):
            attempt[key] = int(attempt[key])
        return attempt

    def _load_fulfillment_attempt(
        self,
        cursor: sqlite3.Cursor,
        *,
        attempt_id: Optional[int] = None,
        order_id: Optional[str] = None,
        cookie_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        columns_sql = ", ".join(self._FULFILLMENT_ATTEMPT_COLUMNS)
        if attempt_id is not None:
            row = cursor.execute(
                f"SELECT {columns_sql} FROM fulfillment_attempts WHERE id = ?",
                (int(attempt_id),),
            ).fetchone()
        else:
            row = cursor.execute(
                f"SELECT {columns_sql} FROM fulfillment_attempts "
                "WHERE cookie_id = ? AND order_id = ?",
                (str(cookie_id or ""), str(order_id or "")),
            ).fetchone()
        return self._decode_fulfillment_attempt(row) if row else None

    @staticmethod
    def _fulfillment_reservation_values(
        cursor: sqlite3.Cursor,
        attempt_id: int,
        states: Sequence[str] = ("reserved",),
    ) -> List[str]:
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        rows = cursor.execute(
            "SELECT value FROM fulfillment_card_reservations "
            f"WHERE attempt_id = ? AND state IN ({placeholders}) "
            "ORDER BY ordinal, id",
            (int(attempt_id), *states),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _public_fulfillment_attempt(
        self,
        cursor: sqlite3.Cursor,
        attempt: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = dict(attempt)
        result.pop("owner_token", None)
        result["attempt_id"] = int(result["id"])
        reservation_state = {
            "prepared": ("reserved",),
            "sending": ("reserved",),
            "committed": ("committed",),
            "released": ("released",),
            "manual_review": ("manual_review",),
        }.get(str(attempt["state"]), ())
        result["reservation_values"] = self._fulfillment_reservation_values(
            cursor,
            int(attempt["id"]),
            reservation_state,
        )
        return result

    @staticmethod
    def _fulfillment_reason(reason_code: str) -> str:
        normalized = str(reason_code or "unknown").strip()
        return normalized[:128] or "unknown"

    def begin_fulfillment_attempt(
        self,
        order_id: str,
        cookie_id: str,
        expected_quantity: int,
    ) -> Dict[str, Any]:
        """Acquire one durable fulfillment attempt for an order."""
        order_id = str(order_id or "").strip()
        cookie_id = str(cookie_id or "").strip()
        try:
            expected_quantity = int(expected_quantity)
        except (TypeError, ValueError):
            expected_quantity = 0
        if (
            not order_id
            or not cookie_id
            or expected_quantity < 1
            or expected_quantity > FULFILLMENT_MAX_QUANTITY
        ):
            return {
                "outcome": "manual_review",
                "attempt_id": None,
                "error_code": "invalid_fulfillment_context",
            }

        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                owner = cursor.execute(
                    "SELECT user_id FROM cookies WHERE id = ?",
                    (cookie_id,),
                ).fetchone()
                if not owner or owner[0] is None:
                    self.conn.rollback()
                    return {
                        "outcome": "manual_review",
                        "attempt_id": None,
                        "error_code": "account_owner_unavailable",
                    }
                user_id = int(owner[0])
                order = cursor.execute(
                    "SELECT 1 FROM orders WHERE order_id = ? AND cookie_id = ?",
                    (order_id, cookie_id),
                ).fetchone()
                if not order:
                    self.conn.rollback()
                    return {
                        "outcome": "manual_review",
                        "attempt_id": None,
                        "error_code": "order_context_unavailable",
                    }
                now = time.time()
                lease_expires_at = now + FULFILLMENT_ATTEMPT_LEASE_SECONDS
                attempt = self._load_fulfillment_attempt(
                    cursor,
                    order_id=order_id,
                    cookie_id=cookie_id,
                )
                if attempt is None:
                    cursor.execute(
                        "INSERT INTO fulfillment_attempts "
                        "(order_id, cookie_id, user_id, expected_quantity, state, "
                        "owner_token, lease_expires_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?, ?)",
                        (
                            order_id,
                            cookie_id,
                            user_id,
                            expected_quantity,
                            self._fulfillment_owner_token,
                            lease_expires_at,
                            now,
                            now,
                        ),
                    )
                    attempt = self._load_fulfillment_attempt(
                        cursor,
                        attempt_id=int(cursor.lastrowid),
                    )
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "acquired"
                    return result

                state = str(attempt["state"])
                if state == "committed":
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "already_completed"
                    return result
                if state == "manual_review":
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "manual_review"
                    return result
                if int(attempt["expected_quantity"]) != expected_quantity:
                    cursor.execute(
                        "UPDATE fulfillment_attempts SET state = 'manual_review', "
                        "reason_code = 'expected_quantity_changed', owner_token = '', "
                        "lease_expires_at = 0, updated_at = ? WHERE id = ?",
                        (now, int(attempt["id"])),
                    )
                    cursor.execute(
                        "UPDATE fulfillment_card_reservations "
                        "SET state = 'manual_review', updated_at = ? "
                        "WHERE attempt_id = ? AND state = 'reserved'",
                        (now, int(attempt["id"])),
                    )
                    attempt = self._load_fulfillment_attempt(
                        cursor, attempt_id=int(attempt["id"])
                    )
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "manual_review"
                    return result

                same_owner = attempt["owner_token"] == self._fulfillment_owner_token
                lease_active = float(attempt["lease_expires_at"] or 0) > now
                if state in {"prepared", "sending"} and same_owner and lease_active:
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "busy"
                    return result
                if state == "sending":
                    cursor.execute(
                        "UPDATE fulfillment_attempts SET state = 'manual_review', "
                        "reason_code = 'interrupted_after_sending', owner_token = '', "
                        "lease_expires_at = 0, updated_at = ? WHERE id = ?",
                        (now, int(attempt["id"])),
                    )
                    cursor.execute(
                        "UPDATE fulfillment_card_reservations "
                        "SET state = 'manual_review', updated_at = ? "
                        "WHERE attempt_id = ? AND state = 'reserved'",
                        (now, int(attempt["id"])),
                    )
                    attempt = self._load_fulfillment_attempt(
                        cursor, attempt_id=int(attempt["id"])
                    )
                    self.conn.commit()
                    result = self._public_fulfillment_attempt(cursor, attempt)
                    result["outcome"] = "manual_review"
                    return result

                cursor.execute(
                    "UPDATE fulfillment_attempts SET state = 'prepared', "
                    "owner_token = ?, lease_expires_at = ?, reason_code = '', "
                    "sent_count = 0, delivered_count = 0, sending_at = NULL, "
                    "completed_at = NULL, attempt_count = attempt_count + 1, "
                    "updated_at = ? WHERE id = ? AND state IN ('prepared', 'released')",
                    (
                        self._fulfillment_owner_token,
                        lease_expires_at,
                        now,
                        int(attempt["id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return {
                        "outcome": "busy",
                        "attempt_id": int(attempt["id"]),
                        "error_code": "attempt_state_conflict",
                    }
                attempt = self._load_fulfillment_attempt(
                    cursor, attempt_id=int(attempt["id"])
                )
                self.conn.commit()
                result = self._public_fulfillment_attempt(cursor, attempt)
                result["outcome"] = "acquired"
                return result
            except Exception as exc:
                self.conn.rollback()
                logger.error(
                    "创建履约尝试失败 type={}",
                    type(exc).__name__,
                )
                return {
                    "outcome": "manual_review",
                    "attempt_id": None,
                    "error_code": "fulfillment_store_error",
                }

    def reserve_batch_card_data(
        self,
        attempt_id: int,
        card_id: int,
        count: int,
    ) -> Optional[List[str]]:
        """Atomically reserve batch-card values without marking them delivered."""
        try:
            attempt_id = int(attempt_id)
            card_id = int(card_id)
            count = int(count)
        except (TypeError, ValueError):
            return None
        if count < 1 or count > FULFILLMENT_MAX_QUANTITY:
            return None

        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(cursor, attempt_id=attempt_id)
                if (
                    not attempt
                    or attempt["state"] != "prepared"
                    or attempt["owner_token"] != self._fulfillment_owner_token
                    or count > int(attempt["expected_quantity"])
                ):
                    self.conn.rollback()
                    return None
                existing = cursor.execute(
                    "SELECT card_id, value FROM fulfillment_card_reservations "
                    "WHERE attempt_id = ? AND state = 'reserved' "
                    "ORDER BY ordinal, id",
                    (attempt_id,),
                ).fetchall()
                if any(row[0] is None or int(row[0]) != card_id for row in existing):
                    self.conn.rollback()
                    return None
                existing_values = [str(row[1]) for row in existing]
                if len(existing_values) > count:
                    self.conn.rollback()
                    return None
                if len(existing_values) == count:
                    self.conn.commit()
                    return existing_values

                card = cursor.execute(
                    "SELECT type, data_content, enabled FROM cards "
                    "WHERE id = ? AND user_id = ?",
                    (card_id, int(attempt["user_id"])),
                ).fetchone()
                if not card or str(card[0]) != "data" or not bool(card[2]):
                    self.conn.rollback()
                    return None
                available = [
                    line.strip()
                    for line in str(card[1] or "").splitlines()
                    if line.strip()
                ]
                needed = count - len(existing_values)
                if len(available) < needed:
                    self.conn.rollback()
                    return None
                selected = available[:needed]
                remaining = available[needed:]
                cursor.execute(
                    "UPDATE cards SET data_content = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND user_id = ?",
                    ("\n".join(remaining), card_id, int(attempt["user_id"])),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return None
                ordinal_row = cursor.execute(
                    "SELECT COALESCE(MAX(ordinal), -1) "
                    "FROM fulfillment_card_reservations WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                next_ordinal = int(ordinal_row[0]) + 1
                now = time.time()
                cursor.executemany(
                    "INSERT INTO fulfillment_card_reservations "
                    "(attempt_id, user_id, card_id, ordinal, value, state, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?)",
                    [
                        (
                            attempt_id,
                            int(attempt["user_id"]),
                            card_id,
                            next_ordinal + offset,
                            value,
                            now,
                            now,
                        )
                        for offset, value in enumerate(selected)
                    ],
                )
                cursor.execute(
                    "UPDATE fulfillment_attempts SET lease_expires_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (now + FULFILLMENT_ATTEMPT_LEASE_SECONDS, now, attempt_id),
                )
                self.conn.commit()
                return existing_values + selected
            except Exception as exc:
                self.conn.rollback()
                logger.error(
                    "预留批量卡券失败 type={}",
                    type(exc).__name__,
                )
                return None

    def mark_fulfillment_sending(self, attempt_id: int) -> bool:
        """Persist the point after which automatic inventory release is unsafe."""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(
                    cursor, attempt_id=int(attempt_id)
                )
                if not attempt:
                    self.conn.rollback()
                    return False
                if (
                    attempt["state"] == "sending"
                    and attempt["owner_token"] == self._fulfillment_owner_token
                ):
                    self.conn.commit()
                    return True
                if (
                    attempt["state"] != "prepared"
                    or attempt["owner_token"] != self._fulfillment_owner_token
                ):
                    self.conn.rollback()
                    return False
                now = time.time()
                cursor.execute(
                    "UPDATE fulfillment_attempts SET state = 'sending', "
                    "sending_at = COALESCE(sending_at, ?), lease_expires_at = ?, "
                    "updated_at = ? WHERE id = ? AND state = 'prepared' "
                    "AND owner_token = ?",
                    (
                        now,
                        now + FULFILLMENT_ATTEMPT_LEASE_SECONDS,
                        now,
                        int(attempt_id),
                        self._fulfillment_owner_token,
                    ),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception as exc:
                self.conn.rollback()
                logger.error("标记履约发送失败 type={}", type(exc).__name__)
                return False

    def commit_fulfillment_attempt(
        self,
        attempt_id: int,
        delivered_count: int,
    ) -> bool:
        """Commit a fully acknowledged delivery and permanently consume inventory."""
        try:
            attempt_id = int(attempt_id)
            delivered_count = int(delivered_count)
        except (TypeError, ValueError):
            return False
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(cursor, attempt_id=attempt_id)
                if not attempt:
                    self.conn.rollback()
                    return False
                if attempt["state"] == "committed":
                    self.conn.commit()
                    return int(attempt["delivered_count"]) == delivered_count
                if (
                    attempt["state"] != "sending"
                    or attempt["owner_token"] != self._fulfillment_owner_token
                    or delivered_count != int(attempt["expected_quantity"])
                ):
                    self.conn.rollback()
                    return False
                now = time.time()
                cursor.execute(
                    "UPDATE orders SET system_shipped = 1, order_status = 'shipped', "
                    "status_source = 'system_fulfillment', "
                    "status_synced_at = CURRENT_TIMESTAMP, last_sync_error = '', "
                    "updated_at = CURRENT_TIMESTAMP, version = version + 1 "
                    "WHERE order_id = ? AND cookie_id = ?",
                    (str(attempt["order_id"]), str(attempt["cookie_id"])),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return False
                cursor.execute(
                    "UPDATE fulfillment_attempts SET state = 'committed', "
                    "sent_count = ?, delivered_count = ?, reason_code = '', "
                    "owner_token = '', lease_expires_at = 0, completed_at = ?, "
                    "updated_at = ? WHERE id = ? AND state = 'sending' "
                    "AND owner_token = ?",
                    (
                        delivered_count,
                        delivered_count,
                        now,
                        now,
                        attempt_id,
                        self._fulfillment_owner_token,
                    ),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return False
                cursor.execute(
                    "UPDATE fulfillment_card_reservations "
                    "SET state = 'committed', updated_at = ? "
                    "WHERE attempt_id = ? AND state = 'reserved'",
                    (now, attempt_id),
                )
                self.conn.commit()
                return True
            except Exception as exc:
                self.conn.rollback()
                logger.error("提交履约失败 type={}", type(exc).__name__)
                return False

    def release_fulfillment_attempt(
        self,
        attempt_id: int,
        reason_code: str,
    ) -> bool:
        """Release only pre-send reservations and restore batch-card inventory."""
        try:
            attempt_id = int(attempt_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(cursor, attempt_id=attempt_id)
                if not attempt:
                    self.conn.rollback()
                    return False
                if attempt["state"] == "released":
                    self.conn.commit()
                    return True
                if (
                    attempt["state"] != "prepared"
                    or attempt["owner_token"] != self._fulfillment_owner_token
                ):
                    self.conn.rollback()
                    return False
                reservations = cursor.execute(
                    "SELECT card_id, value FROM fulfillment_card_reservations "
                    "WHERE attempt_id = ? AND state = 'reserved' "
                    "ORDER BY ordinal, id",
                    (attempt_id,),
                ).fetchall()
                now = time.time()
                if reservations:
                    card_ids = {row[0] for row in reservations}
                    if len(card_ids) != 1 or None in card_ids:
                        cursor.execute(
                            "UPDATE fulfillment_attempts SET state = 'manual_review', "
                            "reason_code = 'reservation_card_unavailable', owner_token = '', "
                            "lease_expires_at = 0, updated_at = ? WHERE id = ?",
                            (now, attempt_id),
                        )
                        cursor.execute(
                            "UPDATE fulfillment_card_reservations "
                            "SET state = 'manual_review', updated_at = ? "
                            "WHERE attempt_id = ? AND state = 'reserved'",
                            (now, attempt_id),
                        )
                        self.conn.commit()
                        return False
                    card_id = int(next(iter(card_ids)))
                    card = cursor.execute(
                        "SELECT data_content FROM cards WHERE id = ? AND user_id = ?",
                        (card_id, int(attempt["user_id"])),
                    ).fetchone()
                    if not card:
                        cursor.execute(
                            "UPDATE fulfillment_attempts SET state = 'manual_review', "
                            "reason_code = 'reservation_card_unavailable', owner_token = '', "
                            "lease_expires_at = 0, updated_at = ? WHERE id = ?",
                            (now, attempt_id),
                        )
                        cursor.execute(
                            "UPDATE fulfillment_card_reservations "
                            "SET state = 'manual_review', updated_at = ? "
                            "WHERE attempt_id = ? AND state = 'reserved'",
                            (now, attempt_id),
                        )
                        self.conn.commit()
                        return False
                    existing = [
                        line.strip()
                        for line in str(card[0] or "").splitlines()
                        if line.strip()
                    ]
                    restored = [str(row[1]) for row in reservations] + existing
                    cursor.execute(
                        "UPDATE cards SET data_content = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND user_id = ?",
                        ("\n".join(restored), card_id, int(attempt["user_id"])),
                    )
                    if cursor.rowcount != 1:
                        self.conn.rollback()
                        return False
                cursor.execute(
                    "UPDATE fulfillment_card_reservations SET state = 'released', "
                    "updated_at = ? WHERE attempt_id = ? AND state = 'reserved'",
                    (now, attempt_id),
                )
                cursor.execute(
                    "UPDATE fulfillment_attempts SET state = 'released', "
                    "reason_code = ?, owner_token = '', lease_expires_at = 0, "
                    "completed_at = ?, updated_at = ? WHERE id = ? AND state = 'prepared'",
                    (
                        self._fulfillment_reason(reason_code),
                        now,
                        now,
                        attempt_id,
                    ),
                )
                self.conn.commit()
                return cursor.rowcount == 1
            except Exception as exc:
                self.conn.rollback()
                logger.error("释放履约预留失败 type={}", type(exc).__name__)
                return False

    def mark_fulfillment_manual_review(
        self,
        attempt_id: int,
        reason_code: str,
        sent_count: int = 0,
    ) -> bool:
        """Quarantine uncertain delivery state without returning reserved values."""
        try:
            attempt_id = int(attempt_id)
            sent_count = int(sent_count)
        except (TypeError, ValueError):
            return False
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(cursor, attempt_id=attempt_id)
                if not attempt or attempt["state"] in {"committed", "released"}:
                    self.conn.rollback()
                    return False
                if (
                    attempt["state"] in {"prepared", "sending"}
                    and attempt["owner_token"] != self._fulfillment_owner_token
                ):
                    self.conn.rollback()
                    return False
                sent_count = max(
                    int(attempt["sent_count"]),
                    min(max(sent_count, 0), int(attempt["expected_quantity"])),
                )
                now = time.time()
                cursor.execute(
                    "UPDATE fulfillment_attempts SET state = 'manual_review', "
                    "reason_code = ?, sent_count = ?, owner_token = '', "
                    "lease_expires_at = 0, completed_at = ?, updated_at = ? "
                    "WHERE id = ? AND state IN ('prepared', 'sending', 'manual_review')",
                    (
                        self._fulfillment_reason(reason_code),
                        sent_count,
                        now,
                        now,
                        attempt_id,
                    ),
                )
                cursor.execute(
                    "UPDATE fulfillment_card_reservations "
                    "SET state = 'manual_review', updated_at = ? "
                    "WHERE attempt_id = ? AND state = 'reserved'",
                    (now, attempt_id),
                )
                self.conn.commit()
                return True
            except Exception as exc:
                self.conn.rollback()
                logger.error("标记履约人工复核失败 type={}", type(exc).__name__)
                return False

    def get_fulfillment_attempt(
        self,
        attempt_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return durable fulfillment state and its currently quarantined values."""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                attempt = self._load_fulfillment_attempt(
                    cursor, attempt_id=int(attempt_id)
                )
                if not attempt:
                    return None
                return self._public_fulfillment_attempt(cursor, attempt)
            except Exception as exc:
                logger.error("读取履约状态失败 type={}", type(exc).__name__)
                return None

    _FULFILLMENT_API_OPERATION_COLUMNS = (
        "id",
        "attempt_id",
        "user_id",
        "cookie_id",
        "card_id",
        "idempotency_key",
        "config_fingerprint",
        "request_spec_json",
        "state",
        "attempt_count",
        "http_status",
        "external_operation_id",
        "response_items_json",
        "reason_code",
        "created_at",
        "updated_at",
    )

    @classmethod
    def _decode_fulfillment_api_operation(
        cls,
        row: Optional[Sequence[Any]],
    ) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        operation = dict(zip(cls._FULFILLMENT_API_OPERATION_COLUMNS, row))
        for key in ("id", "attempt_id", "user_id", "attempt_count"):
            operation[key] = int(operation[key])
        operation["card_id"] = (
            int(operation["card_id"]) if operation["card_id"] is not None else None
        )
        try:
            operation["request_spec"] = json.loads(
                str(operation.pop("request_spec_json") or "{}")
            )
        except (TypeError, ValueError):
            operation["request_spec"] = {}
        try:
            operation["response_items"] = json.loads(
                str(operation.pop("response_items_json") or "[]")
            )
        except (TypeError, ValueError):
            operation["response_items"] = []
        return operation

    def _load_fulfillment_api_operation(
        self,
        cursor: sqlite3.Cursor,
        *,
        operation_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if operation_id is not None:
            where.append("id = ?")
            params.append(int(operation_id))
        if attempt_id is not None:
            where.append("attempt_id = ?")
            params.append(int(attempt_id))
        if user_id is not None:
            where.append("user_id = ?")
            params.append(int(user_id))
        if not where:
            return None
        columns = ", ".join(self._FULFILLMENT_API_OPERATION_COLUMNS)
        row = cursor.execute(
            f"SELECT {columns} FROM fulfillment_api_operations "
            f"WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        return self._decode_fulfillment_api_operation(row)

    def create_fulfillment_api_operation(
        self,
        *,
        attempt_id: int,
        card_id: int,
        idempotency_key: str,
        config_fingerprint: str,
        request_spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        key = str(idempotency_key or "").strip()
        fingerprint = str(config_fingerprint or "").strip()
        if not key or not fingerprint or not isinstance(request_spec, dict):
            return {"outcome": "conflict", "operation": None}
        spec_json = json.dumps(
            request_spec,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(
                    cursor, attempt_id=int(attempt_id)
                )
                if not attempt:
                    self.conn.rollback()
                    return {"outcome": "conflict", "operation": None}
                existing = self._load_fulfillment_api_operation(
                    cursor, attempt_id=int(attempt_id)
                )
                if existing:
                    same = (
                        existing.get("card_id") == int(card_id)
                        and existing.get("idempotency_key") == key
                        and existing.get("config_fingerprint") == fingerprint
                        and json.dumps(
                            existing.get("request_spec") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        == spec_json
                    )
                    self.conn.commit()
                    return {
                        "outcome": "existing" if same else "conflict",
                        "operation": existing,
                    }
                card = cursor.execute(
                    "SELECT 1 FROM cards WHERE id = ? AND user_id = ? AND type = 'api'",
                    (int(card_id), int(attempt["user_id"])),
                ).fetchone()
                if not card:
                    self.conn.rollback()
                    return {"outcome": "conflict", "operation": None}
                now = time.time()
                cursor.execute(
                    "INSERT INTO fulfillment_api_operations "
                    "(attempt_id, user_id, cookie_id, card_id, idempotency_key, "
                    "config_fingerprint, request_spec_json, state, attempt_count, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', 0, ?, ?)",
                    (
                        int(attempt_id),
                        int(attempt["user_id"]),
                        str(attempt["cookie_id"]),
                        int(card_id),
                        key,
                        fingerprint,
                        spec_json,
                        now,
                        now,
                    ),
                )
                operation = self._load_fulfillment_api_operation(
                    cursor, operation_id=int(cursor.lastrowid)
                )
                self.conn.commit()
                return {"outcome": "created", "operation": operation}
            except sqlite3.IntegrityError:
                self.conn.rollback()
                with self.lock:
                    existing = self._load_fulfillment_api_operation(
                        self.conn.cursor(), attempt_id=int(attempt_id)
                    )
                return {"outcome": "existing", "operation": existing}
            except Exception as exc:
                self.conn.rollback()
                logger.error("创建 API 履约操作失败 type={}", type(exc).__name__)
                return {"outcome": "conflict", "operation": None}

    def get_fulfillment_api_operation(
        self,
        operation_id: Optional[int] = None,
        user_id: Optional[int] = None,
        *,
        attempt_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self._load_fulfillment_api_operation(
                self.conn.cursor(),
                operation_id=operation_id,
                attempt_id=attempt_id,
                user_id=user_id,
            )

    def record_fulfillment_api_attempt(
        self,
        operation_id: int,
        *,
        state: str,
        http_status: Optional[int] = None,
        external_operation_id: str = "",
        response_items: Optional[Sequence[Any]] = None,
        reason_code: str = "",
    ) -> Optional[Dict[str, Any]]:
        normalized_state = str(state or "").strip()
        if normalized_state not in {
            "prepared", "pending", "succeeded", "failed", "manual_review"
        }:
            raise ValueError("API 履约状态无效")
        items_json = None
        if response_items is not None:
            items = [str(item) for item in response_items]
            items_json = json.dumps(
                items,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                current = self._load_fulfillment_api_operation(
                    cursor, operation_id=int(operation_id)
                )
                if not current or int(current["attempt_count"]) >= 4:
                    self.conn.rollback()
                    return current
                fields = [
                    "state = ?",
                    "attempt_count = attempt_count + 1",
                    "http_status = ?",
                    "external_operation_id = ?",
                    "reason_code = ?",
                    "updated_at = ?",
                ]
                params: List[Any] = [
                    normalized_state,
                    int(http_status) if http_status is not None else None,
                    str(external_operation_id or "")[:256],
                    self._fulfillment_reason(reason_code) if reason_code else "",
                    time.time(),
                ]
                if items_json is not None:
                    fields.append("response_items_json = ?")
                    params.append(items_json)
                params.append(int(operation_id))
                cursor.execute(
                    f"UPDATE fulfillment_api_operations SET {', '.join(fields)} "
                    "WHERE id = ? AND attempt_count < 4",
                    params,
                )
                updated = self._load_fulfillment_api_operation(
                    cursor, operation_id=int(operation_id)
                )
                self.conn.commit()
                return updated
            except Exception:
                self.conn.rollback()
                raise

    _FULFILLMENT_PAYLOAD_COLUMNS = (
        "id",
        "attempt_id",
        "user_id",
        "cookie_id",
        "source_type",
        "source_card_id",
        "source_operation_id",
        "payload_json",
        "payload_hash",
        "created_at",
    )

    @classmethod
    def _decode_fulfillment_payload(
        cls,
        row: Optional[Sequence[Any]],
    ) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        payload = dict(zip(cls._FULFILLMENT_PAYLOAD_COLUMNS, row))
        for key in ("id", "attempt_id", "user_id"):
            payload[key] = int(payload[key])
        for key in ("source_card_id", "source_operation_id"):
            payload[key] = int(payload[key]) if payload[key] is not None else None
        try:
            payload["payloads"] = json.loads(str(payload.pop("payload_json") or "[]"))
        except (TypeError, ValueError):
            payload["payloads"] = []
        return payload

    def _load_fulfillment_payload(
        self,
        cursor: sqlite3.Cursor,
        *,
        payload_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        where: List[str] = []
        params: List[Any] = []
        if payload_id is not None:
            where.append("id = ?")
            params.append(int(payload_id))
        if attempt_id is not None:
            where.append("attempt_id = ?")
            params.append(int(attempt_id))
        if user_id is not None:
            where.append("user_id = ?")
            params.append(int(user_id))
        if not where:
            return None
        columns = ", ".join(self._FULFILLMENT_PAYLOAD_COLUMNS)
        row = cursor.execute(
            f"SELECT {columns} FROM fulfillment_delivery_payloads "
            f"WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        return self._decode_fulfillment_payload(row)

    def commit_fulfillment_delivery_payload(
        self,
        attempt_id: int,
        payloads: Sequence[Any],
        *,
        source_type: str,
        source_operation_id: Optional[int] = None,
        source_card_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if isinstance(payloads, (str, bytes)):
            payload_values = [str(payloads)]
        else:
            payload_values = [str(value) for value in payloads]
        if not payload_values or any(not value for value in payload_values):
            return {"outcome": "conflict", "payload": None}
        payload_json = json.dumps(
            payload_values,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                attempt = self._load_fulfillment_attempt(
                    cursor, attempt_id=int(attempt_id)
                )
                if not attempt or len(payload_values) != int(attempt["expected_quantity"]):
                    self.conn.rollback()
                    return {"outcome": "conflict", "payload": None}
                existing = self._load_fulfillment_payload(
                    cursor, attempt_id=int(attempt_id)
                )
                if existing:
                    same = existing.get("payload_hash") == payload_hash
                    self.conn.commit()
                    return {
                        "outcome": "existing" if same else "conflict",
                        "payload": existing,
                    }
                now = time.time()
                cursor.execute(
                    "INSERT INTO fulfillment_delivery_payloads "
                    "(attempt_id, user_id, cookie_id, source_type, source_card_id, "
                    "source_operation_id, payload_json, payload_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(attempt_id),
                        int(attempt["user_id"]),
                        str(attempt["cookie_id"]),
                        str(source_type or "resource")[:32],
                        int(source_card_id) if source_card_id is not None else None,
                        int(source_operation_id)
                        if source_operation_id is not None
                        else None,
                        payload_json,
                        payload_hash,
                        now,
                    ),
                )
                payload = self._load_fulfillment_payload(
                    cursor, payload_id=int(cursor.lastrowid)
                )
                self.conn.commit()
                return {"outcome": "committed", "payload": payload}
            except sqlite3.IntegrityError:
                self.conn.rollback()
                existing = self.get_fulfillment_delivery_payload(
                    attempt_id=int(attempt_id)
                )
                return {
                    "outcome": "existing"
                    if existing and existing.get("payload_hash") == payload_hash
                    else "conflict",
                    "payload": existing,
                }
            except Exception as exc:
                self.conn.rollback()
                logger.error("提交履约载荷失败 type={}", type(exc).__name__)
                return {"outcome": "conflict", "payload": None}

    def get_fulfillment_delivery_payload(
        self,
        payload_id: Optional[int] = None,
        user_id: Optional[int] = None,
        *,
        attempt_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self._load_fulfillment_payload(
                self.conn.cursor(),
                payload_id=payload_id,
                attempt_id=attempt_id,
                user_id=user_id,
            )

    def record_fulfillment_resend_event(
        self,
        *,
        payload_id: int,
        status: str,
        user_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        cookie_id: Optional[str] = None,
        request_mid: str = "",
        reason_code: str = "",
    ) -> Optional[Dict[str, Any]]:
        normalized_status = str(status or "").strip()
        if normalized_status not in {"prepared", "succeeded", "failed", "ambiguous"}:
            raise ValueError("重发状态无效")
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                payload = self._load_fulfillment_payload(
                    cursor,
                    payload_id=int(payload_id),
                    user_id=user_id,
                )
                if not payload:
                    self.conn.rollback()
                    return None
                resolved_attempt_id = int(attempt_id or payload["attempt_id"])
                resolved_cookie_id = str(cookie_id or payload["cookie_id"])
                now = time.time()
                cursor.execute(
                    "INSERT INTO fulfillment_resend_events "
                    "(payload_id, attempt_id, user_id, cookie_id, status, "
                    "request_mid, reason_code, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(payload_id),
                        resolved_attempt_id,
                        int(payload["user_id"]),
                        resolved_cookie_id,
                        normalized_status,
                        str(request_mid or "")[:128],
                        self._fulfillment_reason(reason_code) if reason_code else "",
                        now,
                    ),
                )
                event_id = int(cursor.lastrowid)
                self.conn.commit()
                return {
                    "id": event_id,
                    "payload_id": int(payload_id),
                    "attempt_id": resolved_attempt_id,
                    "status": normalized_status,
                    "request_mid": str(request_mid or "")[:128],
                    "reason_code": self._fulfillment_reason(reason_code)
                    if reason_code
                    else "",
                    "created_at": now,
                }
            except Exception:
                self.conn.rollback()
                raise

    def list_fulfillment_records(
        self,
        user_id: int,
        *,
        state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        limit = min(max(int(limit), 1), 200)
        offset = max(int(offset), 0)
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT p.id, p.attempt_id, p.cookie_id, p.source_type,
                       p.source_card_id, p.created_at, a.state, a.reason_code,
                       a.expected_quantity, a.order_id, a.updated_at,
                       o.item_id, c.name,
                       (SELECT r.status FROM fulfillment_resend_events AS r
                        WHERE r.payload_id = p.id ORDER BY r.id DESC LIMIT 1)
                FROM fulfillment_delivery_payloads AS p
                JOIN fulfillment_attempts AS a
                  ON a.id = p.attempt_id AND a.user_id = p.user_id
                LEFT JOIN orders AS o
                  ON o.order_id = a.order_id AND o.cookie_id = a.cookie_id
                LEFT JOIN cards AS c ON c.id = p.source_card_id
                WHERE p.user_id = ?
                ORDER BY p.created_at DESC, p.id DESC
                """,
                (int(user_id),),
            ).fetchall()
        mapped: List[Dict[str, Any]] = []
        for row in rows:
            attempt_state = str(row[6] or "")
            record_state = {
                "committed": "succeeded",
                "manual_review": "manual_review",
                "released": "failed",
                "prepared": "pending",
                "sending": "pending",
            }.get(attempt_state, "pending")
            latest_resend = str(row[13] or "")
            visible_state = "ambiguous" if latest_resend == "ambiguous" else record_state
            if state and state != "all" and visible_state != state:
                continue
            mapped.append(
                {
                    "id": int(row[0]),
                    "attempt_id": int(row[1]),
                    "cookie_id": str(row[2]),
                    "source_type": str(row[3]),
                    "resource_id": int(row[4]) if row[4] is not None else None,
                    "created_at": row[5],
                    "status": visible_state,
                    "reason_code": str(row[7] or ""),
                    "quantity": int(row[8]),
                    "order_id": str(row[9]),
                    "updated_at": row[10],
                    "item_id": str(row[11] or ""),
                    "resource_name": str(row[12] or ""),
                    "payload_preview": f"已保存 {int(row[8])} 条交付内容",
                    "can_resend": attempt_state == "committed",
                    "latest_resend_status": latest_resend or None,
                }
            )
        total = len(mapped)
        return {"items": mapped[offset:offset + limit], "total": total}

    def consume_batch_data(self, card_id: int):
        """消费批量数据的第一条记录（线程安全）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 获取卡券的批量数据
                self._execute_sql(cursor, "SELECT data_content FROM cards WHERE id = ? AND type = 'data'", (card_id,))
                result = cursor.fetchone()

                if not result or not result[0]:
                    logger.warning(f"卡券 {card_id} 没有批量数据")
                    return None

                data_content = result[0]
                lines = [line.strip() for line in data_content.split('\n') if line.strip()]

                if not lines:
                    logger.warning(f"卡券 {card_id} 批量数据为空")
                    return None

                # 获取第一条数据
                first_line = lines[0]

                # 移除第一条数据，更新数据库
                remaining_lines = lines[1:]
                new_data_content = '\n'.join(remaining_lines)

                cursor.execute('''
                UPDATE cards
                SET data_content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''', (new_data_content, card_id))

                self.conn.commit()

                logger.info(f"消费批量数据成功: 卡券ID={card_id}, 剩余={len(remaining_lines)}条")
                return first_line

            except Exception as e:
                logger.error(f"消费批量数据失败: {e}")
                self.conn.rollback()
                return None

    # ==================== 商品信息管理 ====================

    @staticmethod
    def _decode_item_row(columns: List[str], row: tuple) -> Dict[str, Any]:
        item_info = dict(zip(columns, row))
        item_info['catalog_active'] = bool(item_info.get('catalog_active'))
        item_info['invite_auto_fulfillment'] = bool(
            item_info.get('invite_auto_fulfillment')
        )
        for source_key, target_key in (
            ('item_detail', 'item_detail_parsed'),
            ('catalog_metadata', 'catalog_metadata_parsed'),
        ):
            raw_value = item_info.get(source_key)
            if raw_value:
                try:
                    item_info[target_key] = json.loads(raw_value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    item_info[target_key] = {}
            else:
                item_info[target_key] = {}
        return item_info

    def save_item_basic_info(self, cookie_id: str, item_id: str, item_title: str = None,
                            item_description: str = None, item_category: str = None,
                            item_price: str = None, item_detail: str = None) -> bool:
        """保存或更新商品基本信息，使用原子操作避免并发问题

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_title: 商品标题
            item_description: 商品描述
            item_category: 商品分类
            item_price: 商品价格
            item_detail: 商品详情JSON

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 使用 INSERT OR IGNORE + UPDATE 的原子操作模式
                # 首先尝试插入，如果已存在则忽略
                cursor.execute('''
                INSERT OR IGNORE INTO item_info (cookie_id, item_id, item_title, item_description,
                                               item_category, item_price, item_detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (cookie_id, item_id, item_title or '', item_description or '',
                      item_category or '', item_price or '', item_detail or ''))

                # 如果是新插入的记录，直接返回成功
                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"新增商品基本信息: {item_id} - {item_title}")
                    return True

                # 记录已存在，使用原子UPDATE操作，只更新非空字段且不覆盖现有非空值
                update_parts = []
                params = []

                # 使用 CASE WHEN 语句进行条件更新，避免覆盖现有数据
                if item_title:
                    update_parts.append("item_title = CASE WHEN (item_title IS NULL OR item_title = '') THEN ? ELSE item_title END")
                    params.append(item_title)

                if item_description:
                    update_parts.append("item_description = CASE WHEN (item_description IS NULL OR item_description = '') THEN ? ELSE item_description END")
                    params.append(item_description)

                if item_category:
                    update_parts.append("item_category = CASE WHEN (item_category IS NULL OR item_category = '') THEN ? ELSE item_category END")
                    params.append(item_category)

                if item_price:
                    update_parts.append("item_price = CASE WHEN (item_price IS NULL OR item_price = '') THEN ? ELSE item_price END")
                    params.append(item_price)

                # 对于item_detail，只有在现有值为空时才更新
                if item_detail:
                    update_parts.append("item_detail = CASE WHEN (item_detail IS NULL OR item_detail = '' OR TRIM(item_detail) = '') THEN ? ELSE item_detail END")
                    params.append(item_detail)

                if update_parts:
                    update_parts.append("updated_at = CURRENT_TIMESTAMP")
                    params.extend([cookie_id, item_id])

                    sql = f"UPDATE item_info SET {', '.join(update_parts)} WHERE cookie_id = ? AND item_id = ?"
                    self._execute_sql(cursor, sql, params)

                    if cursor.rowcount > 0:
                        logger.info(f"更新商品基本信息: {item_id} - {item_title}")
                    else:
                        logger.debug(f"商品信息无需更新: {item_id}")

                self.conn.commit()
                return True

        except Exception as e:
            logger.error(f"保存商品基本信息失败: {e}")
            self.conn.rollback()
            return False

    def save_item_info(self, cookie_id: str, item_id: str, item_data = None) -> bool:
        """保存或更新商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_data: 商品详情数据，可以是字符串或字典，也可以为None

        Returns:
            bool: 操作是否成功
        """
        try:
            # 验证：如果只有商品ID，没有商品详情数据，则不插入数据库
            if not item_data:
                logger.debug(f"跳过保存商品信息：缺少商品详情数据 - {item_id}")
                return False

            # 如果是字典类型，检查是否有标题信息
            if isinstance(item_data, dict):
                title = item_data.get('title', '').strip()
                if not title:
                    logger.debug(f"跳过保存商品信息：缺少商品标题 - {item_id}")
                    return False

            # 如果是字符串类型，检查是否为空
            if isinstance(item_data, str) and not item_data.strip():
                logger.debug(f"跳过保存商品信息：商品详情为空 - {item_id}")
                return False

            with self.lock:
                cursor = self.conn.cursor()

                # 检查商品是否已存在
                cursor.execute('''
                SELECT id, item_detail FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                existing = cursor.fetchone()

                if existing:
                    # 如果传入的商品详情有值，则用最新数据覆盖
                    if item_data is not None and item_data:
                        # 处理字符串类型的详情数据
                        if isinstance(item_data, str):
                            cursor.execute('''
                            UPDATE item_info SET
                                item_detail = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            ''', (item_data, cookie_id, item_id))
                        else:
                            # 处理字典类型的详情数据（向后兼容）
                            cursor.execute('''
                            UPDATE item_info SET
                                item_title = ?, item_description = ?, item_category = ?,
                                item_price = ?, item_detail = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            ''', (
                                item_data.get('title', ''),
                                item_data.get('description', ''),
                                item_data.get('category', ''),
                                item_data.get('price', ''),
                                json.dumps(item_data, ensure_ascii=False),
                                cookie_id, item_id
                            ))
                        logger.info(f"更新商品信息（覆盖）: {item_id}")
                    else:
                        # 如果商品详情没有数据，则不更新，只记录存在
                        logger.debug(f"商品信息已存在，无新数据，跳过更新: {item_id}")
                        return True
                else:
                    # 新增商品信息
                    if isinstance(item_data, str):
                        # 直接保存字符串详情
                        cursor.execute('''
                        INSERT INTO item_info (cookie_id, item_id, item_detail)
                        VALUES (?, ?, ?)
                        ''', (cookie_id, item_id, item_data))
                    else:
                        # 处理字典类型的详情数据（向后兼容）
                        cursor.execute('''
                        INSERT INTO item_info (cookie_id, item_id, item_title, item_description,
                                             item_category, item_price, item_detail)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            cookie_id, item_id,
                            item_data.get('title', '') if item_data else '',
                            item_data.get('description', '') if item_data else '',
                            item_data.get('category', '') if item_data else '',
                            item_data.get('price', '') if item_data else '',
                            json.dumps(item_data, ensure_ascii=False) if item_data else ''
                        ))
                    logger.info(f"新增商品信息: {item_id}")

                self.conn.commit()
                return True

        except Exception as e:
            logger.error(f"保存商品信息失败: {e}")
            self.conn.rollback()
            return False

    def get_item_info(self, cookie_id: str, item_id: str) -> Optional[Dict]:
        """获取商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            Dict: 商品信息，如果不存在返回None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT * FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    columns = [description[0] for description in cursor.description]
                    item_info = self._decode_item_row(columns, row)
                    logger.debug(
                        "已读取商品信息摘要: item_id={}, has_detail={}, parsed_detail={}",
                        item_id,
                        bool(item_info.get('item_detail')),
                        bool(item_info.get('item_detail_parsed')),
                    )
                    return item_info
                return None

        except Exception as e:
            logger.error(f"获取商品信息失败: {e}")
            return None

    def update_item_multi_spec_status(self, cookie_id: str, item_id: str, is_multi_spec: bool) -> bool:
        """更新商品的多规格状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE item_info
                SET is_multi_spec = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (is_multi_spec, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品多规格状态成功: {item_id} -> {is_multi_spec}")
                    return True
                else:
                    logger.warning(f"商品不存在，无法更新多规格状态: {item_id}")
                    # 0 行也已隐式开启事务，必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False

        except Exception as e:
            logger.error(f"更新商品多规格状态失败: {e}")
            self.conn.rollback()
            return False

    def get_item_multi_spec_status(self, cookie_id: str, item_id: str) -> bool:
        """获取商品的多规格状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT is_multi_spec FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return bool(row[0]) if row[0] is not None else False
                return False

        except Exception as e:
            logger.error(f"获取商品多规格状态失败: {e}")
            return False

    def update_item_multi_quantity_delivery_status(self, cookie_id: str, item_id: str, multi_quantity_delivery: bool) -> bool:
        """更新商品的多数量发货状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                UPDATE item_info
                SET multi_quantity_delivery = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (multi_quantity_delivery, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品多数量发货状态成功: {item_id} -> {multi_quantity_delivery}")
                    return True
                else:
                    logger.warning(f"未找到要更新的商品: {item_id}")
                    # 0 行也已隐式开启事务，必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False

        except Exception as e:
            logger.error(f"更新商品多数量发货状态失败: {e}")
            self.conn.rollback()
            return False

    def get_item_multi_quantity_delivery_status(self, cookie_id: str, item_id: str) -> bool:
        """获取商品的多数量发货状态"""
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT multi_quantity_delivery FROM item_info
                WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return bool(row[0]) if row[0] is not None else False
                return False

        except Exception as e:
            logger.error(f"获取商品多数量发货状态失败: {e}")
            return False

    def set_item_delivery_mode(
        self,
        cookie_id: str,
        item_id: str,
        mode: str,
        user_id: int,
        *,
        card_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Atomically select off, one local resource, or invite fulfillment."""
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"off", "resource", "invite"}:
            return {"outcome": "failed", "error": "invalid_mode"}
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                item = cursor.execute(
                    "SELECT 1 FROM item_info AS i "
                    "JOIN cookies AS c ON c.id = i.cookie_id "
                    "WHERE i.cookie_id = ? AND i.item_id = ? AND c.user_id = ?",
                    (str(cookie_id), str(item_id), int(user_id)),
                ).fetchone()
                if not item:
                    self.conn.rollback()
                    return {"outcome": "failed", "error": "item_not_found"}

                selected_card_id: Optional[int] = None
                invite_enabled = 0
                if normalized_mode == "resource":
                    try:
                        selected_card_id = int(card_id) if card_id is not None else None
                    except (TypeError, ValueError):
                        selected_card_id = None
                    if selected_card_id is None:
                        self.conn.rollback()
                        return {"outcome": "failed", "error": "resource_required"}
                    card = cursor.execute(
                        "SELECT type, api_config, api_token_encrypted, "
                        "api_validation_status, enabled FROM cards "
                        "WHERE id = ? AND user_id = ?",
                        (selected_card_id, int(user_id)),
                    ).fetchone()
                    if not card:
                        self.conn.rollback()
                        return {"outcome": "failed", "error": "resource_not_found"}
                    if not bool(card[4]):
                        self.conn.rollback()
                        return {"outcome": "failed", "error": "resource_disabled"}
                    if str(card[0]) == "api":
                        try:
                            config = self._card_api_config_dict(card[1]) or {}
                        except ValueError:
                            config = {}
                        if (
                            config.get("protocol") != FULFILLMENT_API_PROTOCOL
                            or not str(config.get("url") or "").lower().startswith("https://")
                            or str(card[3] or "") != "validated"
                            or not str(card[2] or "")
                        ):
                            self.conn.rollback()
                            return {
                                "outcome": "failed",
                                "error": "api_resource_not_validated",
                            }
                elif normalized_mode == "invite":
                    invite_enabled = 1

                cursor.execute(
                    "UPDATE item_info SET delivery_mode = ?, delivery_card_id = ?, "
                    "invite_auto_fulfillment = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE cookie_id = ? AND item_id = ?",
                    (
                        normalized_mode,
                        selected_card_id,
                        invite_enabled,
                        str(cookie_id),
                        str(item_id),
                    ),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return {"outcome": "failed", "error": "item_not_found"}
                self.conn.commit()
                return {
                    "outcome": "updated",
                    "mode": normalized_mode,
                    "card_id": selected_card_id,
                }
            except Exception as exc:
                self.conn.rollback()
                logger.error("更新商品发货模式失败 type={}", type(exc).__name__)
                return {"outcome": "failed", "error": "storage_error"}

    def set_item_delivery_modes_batch(
        self,
        cookie_id: str,
        item_ids: Sequence[Any],
        mode: str,
        user_id: int,
        *,
        card_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        updated: List[str] = []
        failed: List[Dict[str, str]] = []
        seen: set[str] = set()
        for raw_item_id in list(item_ids)[:500]:
            normalized_item_id = str(raw_item_id or "").strip()
            if not normalized_item_id or normalized_item_id in seen:
                continue
            seen.add(normalized_item_id)
            result = self.set_item_delivery_mode(
                cookie_id,
                normalized_item_id,
                mode,
                user_id,
                card_id=card_id,
            )
            if result.get("outcome") == "updated":
                updated.append(normalized_item_id)
            else:
                failed.append(
                    {
                        "item_id": normalized_item_id,
                        "error": str(result.get("error") or "update_failed"),
                    }
                )
        return {"updated": updated, "failed": failed}

    def get_item_delivery_binding_status(
        self,
        cookie_id: str,
        item_id: str,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Describe an explicit selection without silently falling back."""
        with self.lock:
            try:
                row = self.conn.execute(
                    """
                    SELECT i.delivery_mode, i.delivery_card_id,
                           i.invite_auto_fulfillment,
                           c.id, c.name, c.type, c.api_config, c.text_content,
                           c.data_content, c.image_url, c.enabled, c.description,
                           c.delay_seconds, c.is_multi_spec, c.spec_name, c.spec_value,
                           c.api_token_encrypted, c.api_validation_status
                    FROM item_info AS i
                    JOIN cookies AS owner ON owner.id = i.cookie_id
                    LEFT JOIN cards AS c
                      ON c.id = i.delivery_card_id AND c.user_id = owner.user_id
                    WHERE i.cookie_id = ? AND i.item_id = ? AND owner.user_id = ?
                    """,
                    (str(cookie_id), str(item_id), int(user_id)),
                ).fetchone()
                if not row:
                    return None

                mode = str(row[0] or "").strip()
                if not mode:
                    if bool(row[2]):
                        mode = "invite"
                    elif row[1] is not None:
                        mode = "resource"
                    else:
                        return {
                            "mode": "legacy",
                            "status": "unbound",
                            "resource_status": "unbound",
                            "binding_explicit": False,
                            "delivery_card_id": None,
                            "rule": None,
                        }
                result: Dict[str, Any] = {
                    "mode": mode,
                    "status": "active",
                    "resource_status": "active",
                    "binding_explicit": True,
                    "delivery_card_id": int(row[1]) if row[1] is not None else None,
                    "rule": None,
                }
                if mode == "off":
                    result.update(status="explicit_off", resource_status="explicit_off")
                    return result
                if mode == "invite":
                    result.update(status="invite", resource_status="invite")
                    return result
                if mode != "resource" or row[1] is None or row[3] is None:
                    result.update(status="missing", resource_status="missing")
                    return result
                if not bool(row[10]):
                    result.update(status="disabled", resource_status="disabled")
                    return result

                card_type = str(row[5] or "")
                api_config: Any = self._public_card_api_config(row[6])
                if card_type == "data" and not any(
                    line.strip() for line in str(row[8] or "").splitlines()
                ):
                    result.update(status="out_of_stock", resource_status="out_of_stock")
                    return result
                if card_type == "text" and not str(row[7] or "").strip():
                    result.update(status="empty", resource_status="empty")
                    return result
                if card_type == "image" and not str(row[9] or "").strip():
                    result.update(status="empty", resource_status="empty")
                    return result
                if card_type == "api":
                    try:
                        runtime_config = self.get_card_api_runtime_config(
                            int(row[3]), int(user_id)
                        ) or {}
                    except ValueError:
                        runtime_config = {}
                    if (
                        runtime_config.get("protocol") != FULFILLMENT_API_PROTOCOL
                        or not str(runtime_config.get("url") or "").lower().startswith("https://")
                        or runtime_config.get("validation_status") != "validated"
                        or not runtime_config.get("api_token")
                    ):
                        result.update(
                            status="protocol_invalid",
                            resource_status="protocol_invalid",
                        )
                        return result
                    api_config = runtime_config

                result["rule"] = {
                    "id": None,
                    "keyword": "",
                    "card_id": int(row[3]),
                    "delivery_count": 1,
                    "enabled": True,
                    "description": row[11],
                    "delivery_times": 0,
                    "card_name": row[4],
                    "card_type": card_type,
                    "api_config": api_config,
                    "text_content": row[7],
                    "data_content": row[8],
                    "image_url": row[9],
                    "card_enabled": bool(row[10]),
                    "card_description": row[11],
                    "card_delay_seconds": int(row[12] or 0),
                    "is_multi_spec": bool(row[13]),
                    "spec_name": row[14],
                    "spec_value": row[15],
                    "source": "item_binding",
                }
                return result
            except Exception as exc:
                logger.error("读取商品发货模式失败 type={}", type(exc).__name__)
                return None

    def update_item_invite_auto_fulfillment_status(
        self,
        cookie_id: str,
        item_id: str,
        enabled: bool,
    ) -> bool:
        """Compatibility route into the same explicit three-mode transaction."""
        user_id = self.get_cookie_user_id(cookie_id)
        if user_id is None:
            return False
        result = self.set_item_delivery_mode(
            cookie_id,
            item_id,
            "invite" if enabled else "off",
            int(user_id),
        )
        return result.get("outcome") == "updated"

    def is_invite_auto_fulfillment_enabled(
        self,
        cookie_id: str,
        item_id: str,
    ) -> bool:
        """Return the exact account/item invite-fulfillment selection."""
        try:
            with self.lock:
                row = self.conn.execute(
                    """
                    SELECT invite_auto_fulfillment
                    FROM item_info
                    WHERE cookie_id = ? AND item_id = ?
                    """,
                    (cookie_id, item_id),
                ).fetchone()
                return bool(row and row[0])
        except Exception as exc:
            logger.error("读取邀请自动发货状态失败: {}", type(exc).__name__)
            return False

    def set_item_delivery_card(
        self,
        cookie_id: str,
        item_id: str,
        card_id: Optional[int],
        user_id: int,
    ) -> bool:
        """Compatibility route into the same explicit three-mode transaction."""
        result = self.set_item_delivery_mode(
            cookie_id,
            item_id,
            "resource" if card_id is not None else "off",
            user_id,
            card_id=card_id,
        )
        return result.get("outcome") == "updated"

    def get_item_bound_delivery_rule(
        self,
        cookie_id: str,
        item_id: str,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Return only an active explicit resource shaped like a delivery rule."""
        status = self.get_item_delivery_binding_status(cookie_id, item_id, user_id)
        if not status or status.get("status") != "active":
            return None
        rule = status.get("rule")
        return dict(rule) if isinstance(rule, dict) else None

    def get_invite_auto_fulfillment_item_ids(
        self,
        cookie_id: Optional[str] = None,
    ) -> set[str]:
        """List selected item IDs, optionally scoped to one Xianyu account."""
        try:
            with self.lock:
                if cookie_id:
                    rows = self.conn.execute(
                        """
                        SELECT item_id FROM item_info
                        WHERE cookie_id = ? AND invite_auto_fulfillment = TRUE
                        """,
                        (cookie_id,),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        """
                        SELECT DISTINCT item_id FROM item_info
                        WHERE invite_auto_fulfillment = TRUE
                        """
                    ).fetchall()
                return {str(row[0]) for row in rows if str(row[0] or "").strip()}
        except Exception as exc:
            logger.error("读取邀请自动发货商品失败: {}", type(exc).__name__)
            return set()

    def get_items_by_cookie(self, cookie_id: str, include_inactive: bool = True) -> List[Dict]:
        """获取指定Cookie的所有商品信息

        Args:
            cookie_id: Cookie ID

        Returns:
            List[Dict]: 商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                active_clause = '' if include_inactive else ' AND catalog_active = TRUE'
                cursor.execute(f'''
                SELECT * FROM item_info
                WHERE cookie_id = ?{active_clause}
                ORDER BY updated_at DESC
                ''', (cookie_id,))

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    items.append(self._decode_item_row(columns, row))

                return items

        except Exception as e:
            logger.error(f"获取Cookie商品信息失败: {e}")
            return []

    def get_all_items(self, include_inactive: bool = True) -> List[Dict]:
        """获取所有商品信息

        Returns:
            List[Dict]: 所有商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                active_clause = '' if include_inactive else ' WHERE catalog_active = TRUE'
                cursor.execute(f'''
                SELECT * FROM item_info{active_clause}
                ORDER BY updated_at DESC
                ''')

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    items.append(self._decode_item_row(columns, row))

                return items

        except Exception as e:
            logger.error(f"获取所有商品信息失败: {e}")
            return []

    def update_item_detail(self, cookie_id: str, item_id: str, item_detail: str) -> bool:
        """更新商品详情（不覆盖商品标题等基本信息）

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_detail: 商品详情JSON字符串

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                # 只更新item_detail字段，不影响其他字段
                cursor.execute('''
                UPDATE item_info SET
                    item_detail = ?, updated_at = CURRENT_TIMESTAMP
                WHERE cookie_id = ? AND item_id = ?
                ''', (item_detail, cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"更新商品详情成功: {item_id}")
                    return True
                else:
                    logger.warning(f"未找到要更新的商品: {item_id}")
                    # 0 行也已隐式开启事务，必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False

        except Exception as e:
            logger.error(f"更新商品详情失败: {e}")
            self.conn.rollback()
            return False

    def update_item_title_only(self, cookie_id: str, item_id: str, item_title: str) -> bool:
        """仅更新商品标题（并发安全）

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID
            item_title: 商品标题

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                # 使用 INSERT OR REPLACE 确保记录存在，但只更新标题字段
                cursor.execute('''
                INSERT INTO item_info (cookie_id, item_id, item_title, item_description,
                                     item_category, item_price, item_detail, created_at, updated_at)
                VALUES (?, ?, ?,
                       COALESCE((SELECT item_description FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_category FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_price FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT item_detail FROM item_info WHERE cookie_id = ? AND item_id = ?), ''),
                       COALESCE((SELECT created_at FROM item_info WHERE cookie_id = ? AND item_id = ?), CURRENT_TIMESTAMP),
                       CURRENT_TIMESTAMP)
                ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                    item_title = excluded.item_title,
                    updated_at = CURRENT_TIMESTAMP
                ''', (cookie_id, item_id, item_title,
                      cookie_id, item_id, cookie_id, item_id, cookie_id, item_id,
                      cookie_id, item_id, cookie_id, item_id))

                self.conn.commit()
                logger.info(f"更新商品标题成功: {item_id} - {item_title}")
                return True

        except Exception as e:
            logger.error(f"更新商品标题失败: {e}")
            self.conn.rollback()
            return False

    def batch_save_item_basic_info(self, items_data: list) -> int:
        """批量保存商品基本信息（并发安全）

        Args:
            items_data: 商品数据列表，每个元素包含 cookie_id, item_id, item_title 等字段

        Returns:
            int: 成功保存的商品数量
        """
        if not items_data:
            return 0

        success_count = 0
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 使用事务批量处理
                cursor.execute('BEGIN TRANSACTION')

                for item_data in items_data:
                    try:
                        cookie_id = item_data.get('cookie_id')
                        item_id = item_data.get('item_id')
                        item_title = item_data.get('item_title', '')
                        item_description = item_data.get('item_description', '')
                        item_category = item_data.get('item_category', '')
                        item_price = item_data.get('item_price', '')
                        item_detail = item_data.get('item_detail', '')

                        if not cookie_id or not item_id:
                            continue

                        # 验证：如果没有商品标题，则跳过保存
                        if not item_title or not item_title.strip():
                            logger.debug(f"跳过批量保存商品信息：缺少商品标题 - {item_id}")
                            continue

                        # 使用 INSERT OR IGNORE + UPDATE 模式
                        cursor.execute('''
                        INSERT OR IGNORE INTO item_info (cookie_id, item_id, item_title, item_description,
                                                       item_category, item_price, item_detail, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ''', (cookie_id, item_id, item_title, item_description,
                              item_category, item_price, item_detail))

                        if cursor.rowcount == 0:
                            # 记录已存在，进行条件更新
                            update_sql = '''
                            UPDATE item_info SET
                                item_title = CASE WHEN (item_title IS NULL OR item_title = '') AND ? != '' THEN ? ELSE item_title END,
                                item_description = CASE WHEN (item_description IS NULL OR item_description = '') AND ? != '' THEN ? ELSE item_description END,
                                item_category = CASE WHEN (item_category IS NULL OR item_category = '') AND ? != '' THEN ? ELSE item_category END,
                                item_price = CASE WHEN (item_price IS NULL OR item_price = '') AND ? != '' THEN ? ELSE item_price END,
                                item_detail = CASE WHEN (item_detail IS NULL OR item_detail = '' OR TRIM(item_detail) = '') AND ? != '' THEN ? ELSE item_detail END,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE cookie_id = ? AND item_id = ?
                            '''
                            self._execute_sql(cursor, update_sql, (
                                item_title, item_title,
                                item_description, item_description,
                                item_category, item_category,
                                item_price, item_price,
                                item_detail, item_detail,
                                cookie_id, item_id
                            ))

                        success_count += 1

                    except Exception as item_e:
                        logger.warning(f"批量保存单个商品失败 {item_data.get('item_id', 'unknown')}: {item_e}")
                        continue

                cursor.execute('COMMIT')
                logger.info(f"批量保存商品信息完成: {success_count}/{len(items_data)} 个商品")
                return success_count

        except Exception as e:
            logger.error(f"批量保存商品信息失败: {e}")
            try:
                cursor.execute('ROLLBACK')
            except:
                pass
            return success_count

    def reconcile_catalog_items(
        self,
        cookie_id: str,
        items_data: List[Dict[str, Any]],
        reconcile: bool = True,
    ) -> Dict[str, int]:
        """Upsert a seller's published catalog and optionally hide unseen rows.

        Product detail text, knowledge profiles and delivery flags are preserved.
        The caller must only request reconciliation after a complete successful
        traversal of the platform's published-item pages.
        """
        deduplicated: Dict[str, Dict[str, Any]] = {}
        failed_count = 0
        for raw_item in items_data or []:
            item_id = str(raw_item.get('item_id') or '').strip()
            item_title = str(raw_item.get('item_title') or '').strip()
            try:
                platform_status = int(raw_item.get('platform_item_status'))
            except (TypeError, ValueError):
                failed_count += 1
                continue
            if not item_id or not item_title or platform_status != 0:
                failed_count += 1
                continue
            deduplicated[item_id] = {
                **raw_item,
                'item_id': item_id,
                'item_title': item_title,
                'platform_item_status': platform_status,
            }

        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute(
                    "SELECT item_id, catalog_active, item_image FROM item_info WHERE cookie_id = ?",
                    (cookie_id,),
                )
                existing = {
                    str(row[0]): {
                        'catalog_active': bool(row[1]),
                        'item_image': str(row[2] or ''),
                    }
                    for row in cursor.fetchall()
                }
                active_ids = set(deduplicated)
                hidden_count = (
                    sum(
                        1
                        for item_id, value in existing.items()
                        if value['catalog_active'] and item_id not in active_ids
                    )
                    if reconcile
                    else 0
                )
                images_updated = sum(
                    1
                    for item_id, item in deduplicated.items()
                    if str(item.get('item_image') or '')
                    and existing.get(item_id, {}).get('item_image') != str(item.get('item_image') or '')
                )

                cursor.execute('BEGIN IMMEDIATE')
                if reconcile:
                    cursor.execute(
                        "UPDATE item_info SET catalog_active = FALSE "
                        "WHERE cookie_id = ? AND catalog_active = TRUE",
                        (cookie_id,),
                    )

                saved_count = 0
                for item in deduplicated.values():
                    metadata = item.get('catalog_metadata') or {}
                    metadata_json = (
                        metadata
                        if isinstance(metadata, str)
                        else json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))
                    )
                    cursor.execute(
                        '''
                        INSERT INTO item_info (
                            cookie_id, item_id, item_title, item_description,
                            item_category, item_price, item_detail, item_image,
                            platform_item_status, catalog_active,
                            catalog_last_seen_at, catalog_metadata,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, '', ?, ?, '', ?, ?, TRUE,
                                  CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                            item_title = excluded.item_title,
                            item_category = excluded.item_category,
                            item_price = excluded.item_price,
                            item_image = CASE
                                WHEN excluded.item_image <> '' THEN excluded.item_image
                                ELSE item_info.item_image
                            END,
                            platform_item_status = excluded.platform_item_status,
                            catalog_active = TRUE,
                            catalog_last_seen_at = CURRENT_TIMESTAMP,
                            catalog_metadata = excluded.catalog_metadata,
                            updated_at = CURRENT_TIMESTAMP
                        ''',
                        (
                            cookie_id,
                            item['item_id'],
                            item['item_title'],
                            str(item.get('item_category') or ''),
                            str(item.get('item_price') or ''),
                            str(item.get('item_image') or ''),
                            item['platform_item_status'],
                            metadata_json,
                        ),
                    )
                    saved_count += 1

                cursor.execute('COMMIT')
                return {
                    'saved_count': saved_count,
                    'active_count': len(deduplicated),
                    'hidden_count': hidden_count,
                    'images_updated': images_updated,
                    'failed_count': failed_count,
                }
        except Exception as exc:
            logger.error(f"同步在售商品目录失败: {exc}")
            try:
                self.conn.rollback()
            except Exception:
                pass
            return {
                'saved_count': 0,
                'active_count': 0,
                'hidden_count': 0,
                'images_updated': 0,
                'failed_count': failed_count + len(deduplicated),
            }

    def get_item_catalog_lookup(
        self,
        cookie_ids: List[str],
    ) -> Dict[Tuple[str, str], Dict[str, str]]:
        normalized_ids = [str(value) for value in cookie_ids if str(value)]
        if not normalized_ids:
            return {}
        placeholders = ','.join('?' for _ in normalized_ids)
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                f'''SELECT cookie_id, item_id, item_title, item_price, item_image
                    FROM item_info WHERE cookie_id IN ({placeholders})''',
                normalized_ids,
            )
            return {
                (str(row[0]), str(row[1])): {
                    'item_title': str(row[2] or ''),
                    'item_price': str(row[3] or ''),
                    'item_image': str(row[4] or ''),
                }
                for row in cursor.fetchall()
            }

    def delete_item_info(self, cookie_id: str, item_id: str) -> bool:
        """删除商品信息

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            bool: 操作是否成功
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM item_info WHERE cookie_id = ? AND item_id = ?',
                             (cookie_id, item_id))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除商品信息成功: {cookie_id} - {item_id}")
                    return True
                else:
                    logger.warning(f"未找到要删除的商品信息: {cookie_id} - {item_id}")
                    # 0 行也已隐式开启事务，必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False

        except Exception as e:
            logger.error(f"删除商品信息失败: {e}")
            self.conn.rollback()
            return False

    def batch_delete_item_info(self, items_to_delete: list, user_id: int) -> int:
        """原子删除当前租户的一批商品信息。

        Args:
            items_to_delete: 要删除的商品列表，每个元素包含 cookie_id 和 item_id

        Returns:
            int: 成功删除的商品数量
        """
        if user_id is None:
            raise ValueError("batch_delete_item_info 必须提供 user_id")
        if not items_to_delete:
            return 0

        success_count = 0
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('BEGIN IMMEDIATE')

                for item_data in items_to_delete:
                    cookie_id = str(item_data.get('cookie_id') or '').strip()
                    item_id = str(item_data.get('item_id') or '').strip()
                    if not cookie_id or not item_id:
                        raise ValueError("批量删除商品参数不完整")
                    cursor.execute(
                        "DELETE FROM item_info WHERE cookie_id = ? AND item_id = ? "
                        "AND cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)",
                        (cookie_id, item_id, int(user_id)),
                    )
                    success_count += max(0, int(cursor.rowcount or 0))

                self.conn.commit()
                logger.info(f"批量删除商品信息完成: {success_count}/{len(items_to_delete)} 个商品")
                return success_count

        except Exception as e:
            logger.error(f"批量删除商品信息失败: {e}")
            self.conn.rollback()
            return 0

    # ==================== 用户设置管理方法 ====================

    def get_user_settings(self, user_id: int):
        """获取用户的所有设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT key, value, description, updated_at
                FROM user_settings
                WHERE user_id = ?
                ORDER BY key
                ''', (user_id,))

                settings = {}
                for row in cursor.fetchall():
                    settings[row[0]] = {
                        'value': row[1],
                        'description': row[2],
                        'updated_at': row[3]
                    }

                return settings
            except Exception as e:
                logger.error(f"获取用户设置失败: {e}")
                return {}

    def get_user_setting(self, user_id: int, key: str):
        """获取用户的特定设置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT value, description, updated_at
                FROM user_settings
                WHERE user_id = ? AND key = ?
                ''', (user_id, key))

                row = cursor.fetchone()
                if row:
                    return {
                        'key': key,
                        'value': row[0],
                        'description': row[1],
                        'updated_at': row[2]
                    }
                return None
            except Exception as e:
                logger.error(f"获取用户设置失败: {e}")
                return None

    def set_user_setting(self, user_id: int, key: str, value: str, description: str = None):
        """设置用户配置"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO user_settings (user_id, key, value, description, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, key, value, description))

                self.conn.commit()
                logger.info(f"用户设置更新成功: user_id={user_id}, key={key}")
                return True
            except Exception as e:
                logger.error(f"设置用户配置失败: {e}")
                self.conn.rollback()
                return False

    def set_user_settings(self, user_id: int, settings: Dict[str, Any]) -> bool:
        """Atomically replace a bounded set of user-owned settings."""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                for key, value in settings.items():
                    stored = (
                        "true" if value is True else "false" if value is False else str(value)
                    )
                    cursor.execute(
                        """
                        INSERT INTO user_settings (user_id, key, value, description, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(user_id, key) DO UPDATE SET
                            value = excluded.value,
                            description = excluded.description,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (user_id, key, stored, "Personal item synchronization setting"),
                    )
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error(f"批量保存用户设置失败: {exc}")
                self.conn.rollback()
                return False

    def get_dashboard_stats(self, user_id: Optional[int] = None) -> Dict[str, int]:
        """Return business counters scoped to a single owner.

        必须提供 user_id：仪表盘只统计该用户自己的数据，禁止退化为全表扫描。
        """
        if user_id is None:
            raise ValueError("get_dashboard_stats 必须提供 user_id")
        with self.lock:
            cursor = self.conn.cursor()
            params: Tuple[Any, ...] = (user_id,)
            cookie_row = cursor.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN COALESCE(cs.enabled, 1) = 1 THEN 1 ELSE 0 END)
                FROM cookies AS c
                LEFT JOIN cookie_status AS cs ON cs.cookie_id = c.id
                WHERE c.user_id = ?
                """,
                params,
            ).fetchone()
            cards = cursor.execute(
                "SELECT COUNT(*) FROM cards WHERE user_id = ?",
                params,
            ).fetchone()[0]
            keywords = cursor.execute(
                """
                SELECT COUNT(*) FROM keywords AS k
                JOIN cookies AS c ON c.id = k.cookie_id
                WHERE c.user_id = ?
                """,
                params,
            ).fetchone()[0]
            orders = cursor.execute(
                """
                SELECT COUNT(*) FROM orders AS o
                JOIN cookies AS c ON c.id = o.cookie_id
                WHERE c.user_id = ?
                """,
                params,
            ).fetchone()[0]
            users = 1
            return {
                "total_users": int(users or 0),
                "total_cookies": int((cookie_row or (0, 0))[0] or 0),
                "active_cookies": int((cookie_row or (0, 0))[1] or 0),
                "total_cards": int(cards or 0),
                "total_keywords": int(keywords or 0),
                "total_orders": int(orders or 0),
            }

    def get_dashboard_item_names(
        self,
        user_id: Optional[int],
        item_ids: List[str],
    ) -> Dict[str, str]:
        # 必须提供 user_id：仅返回该用户自己商品的标题，禁止跨租户查询
        if user_id is None:
            raise ValueError("get_dashboard_item_names 必须提供 user_id")
        bounded_item_ids = list(dict.fromkeys(str(item_id) for item_id in item_ids if item_id))[:20]
        if not bounded_item_ids:
            return {}
        with self.lock:
            placeholders = ",".join("?" for _ in bounded_item_ids)
            conditions = [f"i.item_id IN ({placeholders})", "c.user_id = ?"]
            params: List[Any] = list(bounded_item_ids)
            params.append(user_id)
            rows = self.conn.execute(
                f"""
                SELECT i.item_id, MAX(COALESCE(NULLIF(i.item_title, ''), i.item_id))
                FROM item_info AS i
                JOIN cookies AS c ON c.id = i.cookie_id
                WHERE {' AND '.join(conditions)}
                GROUP BY i.item_id
                """,
                params,
            ).fetchall()
            return {str(item_id): str(title or item_id) for item_id, title in rows}

    # ==================== 管理员专用方法 ====================

    def get_all_users(self):
        """获取所有用户信息（管理员专用）"""
        with self.lock:
            try:
                return [
                    public_user_view(user)
                    for user in self.user_repository.list_recent(limit=200)
                ]
            except Exception as e:
                logger.error(f"获取所有用户失败: {e}")
                return []

    def get_user_by_id(self, user_id: int):
        """根据ID获取用户信息"""
        with self.lock:
            try:
                user = self.user_repository.get_by_id(user_id)
                return public_user_view(user) if user else None
            except Exception as e:
                logger.error(f"获取用户信息失败: {e}")
                return None

    def get_inactive_user_ids(self) -> set:
        """被停用用户的 id 集合（供运行态把其账号从监听中摘除）。

        读失败时返回空集合（宁可多监听，也不能因一次读失败把所有账号误判停用）。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("SELECT id FROM users WHERE is_active != 1")
                return {int(row[0]) for row in cursor.fetchall()}
            except Exception as e:
                logger.error(f"查询停用用户失败: {e}")
                return set()

    def delete_user_and_data(self, user_id: int):
        """删除用户及其所有相关数据"""
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cookie_ids = [
                    str(row[0])
                    for row in cursor.execute(
                        "SELECT id FROM cookies WHERE user_id = ?", (int(user_id),)
                    ).fetchall()
                ]
                self._delete_cookie_children(cursor, cookie_ids)
                cursor.execute("DELETE FROM cookies WHERE user_id = ?", (int(user_id),))

                # These legacy tables have historically lacked reliable ON DELETE CASCADE.
                for table_name in (
                    "delivery_rules",
                    "cards",
                    "notification_channels",
                    "user_settings",
                    "ai_provider_profiles",
                    "skill_monitor_results",
                    "skill_monitor_tasks",
                    "skill_agent_prompts",
                    "skill_run_logs",
                    "auth_sessions",
                ):
                    columns = {
                        str(row[1])
                        for row in cursor.execute(
                            f"PRAGMA table_info({self._quote_identifier(table_name)})"
                        ).fetchall()
                    }
                    if "user_id" in columns:
                        cursor.execute(
                            f"DELETE FROM {self._quote_identifier(table_name)} "
                            "WHERE user_id = ?",
                            (int(user_id),),
                        )
                if {
                    str(row[1])
                    for row in cursor.execute(
                        "PRAGMA table_info(runtime_sessions)"
                    ).fetchall()
                } >= {"owner_user_id"}:
                    cursor.execute(
                        "DELETE FROM runtime_sessions WHERE owner_user_id = ?",
                        (int(user_id),),
                    )
                cursor.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
                self.conn.commit()

                logger.info(f"用户及相关数据删除成功: user_id={user_id}")
                return True

            except Exception as e:
                self.conn.rollback()
                logger.error(f"删除用户及相关数据失败: {e}")
                return False

    def get_table_data(self, table_name: str):
        """获取指定表的所有数据（管理端只读导出，自动剔除敏感列）"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 获取表结构
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns_info = cursor.fetchall()
                all_columns = [col[1] for col in columns_info]  # 全部列名

                # 剔除该表的敏感列，避免明文凭据/PII 经 /admin/data 泄露给管理员
                redacted = self.SENSITIVE_EXPORT_COLUMNS.get(table_name, set())
                columns = [name for name in all_columns if name not in redacted]

                # 获取表数据
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()

                # 转换为字典列表，敏感列在此一并跳过
                data = []
                for row in rows:
                    row_dict = {}
                    for i, value in enumerate(row):
                        column_name = all_columns[i]
                        if column_name in redacted:
                            continue
                        row_dict[column_name] = value
                    data.append(row_dict)

                return data, columns

            except Exception as e:
                logger.error(f"获取表数据失败: {table_name} - {e}")
                return [], []

    def insert_or_update_order(self, order_id: str, item_id: str = None, buyer_id: str = None,
                              spec_name: str = None, spec_value: str = None, quantity: str = None,
                              amount: str = None, order_status: str = None, cookie_id: str = None,
                              is_bargain: bool = None, created_at: str = None, receiver_name: str = None,
                              receiver_phone: str = None, receiver_address: str = None,
                              receiver_city: str = None,
                              system_shipped: bool = None, expected_version: int = None,
                              chat_id: str = None, item_image: str = None):
        """插入或更新订单信息"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 检查cookie_id是否在cookies表中存在（如果提供了cookie_id）
                if cookie_id:
                    cursor.execute("SELECT id FROM cookies WHERE id = ?", (cookie_id,))
                    cookie_exists = cursor.fetchone()
                    if not cookie_exists:
                        logger.warning(f"Cookie ID {cookie_id} 不存在于cookies表中，拒绝插入订单 {order_id}")
                        return False

                # 检查订单是否已存在（同时取快照现值，供写一次守卫判断）
                cursor.execute(
                    "SELECT order_id, item_image, cookie_id FROM orders WHERE order_id = ?",
                    (order_id,),
                )
                existing = cursor.fetchone()

                if existing:
                    existing_cookie_id = existing[2]
                    if (
                        cookie_id is not None
                        and str(cookie_id) != str(existing_cookie_id)
                    ):
                        logger.warning(
                            f"订单归属不匹配，拒绝更新: {order_id}"
                        )
                        return False

                    # 更新现有订单
                    update_fields = []
                    update_values = []

                    if item_id is not None:
                        update_fields.append("item_id = ?")
                        update_values.append(item_id)
                    if buyer_id is not None:
                        update_fields.append("buyer_id = ?")
                        update_values.append(buyer_id)
                    if spec_name is not None:
                        update_fields.append("spec_name = ?")
                        update_values.append(spec_name)
                    if spec_value is not None:
                        update_fields.append("spec_value = ?")
                        update_values.append(spec_value)
                    if quantity is not None:
                        update_fields.append("quantity = ?")
                        update_values.append(quantity)
                    if amount is not None:
                        update_fields.append("amount = ?")
                        update_values.append(amount)
                    if order_status is not None:
                        update_fields.append("order_status = ?")
                        update_values.append(order_status)
                    if is_bargain is not None:
                        update_fields.append("is_bargain = ?")
                        update_values.append(1 if is_bargain else 0)
                    if created_at is not None:
                        # 更新创建时间（仅当明确提供时）
                        update_fields.append("created_at = ?")
                        update_values.append(created_at)
                    if receiver_name is not None:
                        update_fields.append("receiver_name = ?")
                        update_values.append(receiver_name)
                    if receiver_phone is not None:
                        update_fields.append("receiver_phone = ?")
                        update_values.append(receiver_phone)
                    if receiver_address is not None:
                        update_fields.append("receiver_address = ?")
                        update_values.append(receiver_address)
                    if receiver_city is not None:
                        update_fields.append("receiver_city = ?")
                        update_values.append(receiver_city)
                    if system_shipped is not None:
                        update_fields.append("system_shipped = ?")
                        update_values.append(1 if system_shipped else 0)
                    if chat_id is not None:
                        update_fields.append("chat_id = ?")
                        update_values.append(chat_id)
                    if item_image is not None and not (existing[1] or ''):
                        # 成交时快照只写一次：已有图片不被任何后续写入路径
                        # （实时消息/导入/废弃批量刷新）冲掉，口径与 apply_order_sync_update 一致
                        update_fields.append("item_image = ?")
                        update_values.append(item_image)

                    if update_fields:
                        update_fields.append("updated_at = CURRENT_TIMESTAMP")
                        # 增加版本号
                        update_fields.append("version = version + 1")

                        # 构建WHERE条件
                        if expected_version is not None:
                            # 使用乐观锁：只有version匹配时才更新
                            where_clause = "order_id = ? AND cookie_id IS ? AND version = ?"
                            update_values.extend(
                                [order_id, existing_cookie_id, expected_version]
                            )
                        else:
                            # 归属也进入原子 UPDATE 条件；任何路径都不改写 cookie_id。
                            where_clause = "order_id = ? AND cookie_id IS ?"
                            update_values.extend([order_id, existing_cookie_id])

                        sql = f"UPDATE orders SET {', '.join(update_fields)} WHERE {where_clause}"
                        cursor.execute(sql, update_values)

                        # 版本或归属在 SELECT 后发生变化时，原子条件拒绝更新。
                        if cursor.rowcount == 0:
                            self.conn.rollback()
                            logger.warning(
                                f"订单更新失败（版本或归属冲突）: {order_id},"
                                f" expected_version={expected_version}"
                            )
                            return False

                        logger.info(f"更新订单信息: {order_id}")
                else:
                    # 插入新订单
                    # 全新订单必须带归属 cookie_id，否则会成为无法被任何租户查询命中
                    # 的孤儿数据（cookie_id=NULL），拒绝写入。
                    if not cookie_id:
                        logger.warning(f"缺少 cookie_id，拒绝插入无归属订单 {order_id}")
                        return False
                    # 成交时快照主图：调用方未显式提供时从商品目录兜底，
                    # 避免商品后续下架导致订单图片失联
                    if not item_image and cookie_id and item_id:
                        catalog_row = cursor.execute(
                            "SELECT item_image FROM item_info WHERE cookie_id = ? AND item_id = ?",
                            (str(cookie_id), str(item_id)),
                        ).fetchone()
                        item_image = (catalog_row[0] if catalog_row else '') or ''
                    if created_at:
                        # 使用提供的创建时间
                        cursor.execute('''
                        INSERT INTO orders (order_id, item_id, buyer_id, spec_name, spec_value,
                                          quantity, amount, order_status, cookie_id, is_bargain, created_at,
                                          receiver_name, receiver_phone, receiver_address, receiver_city,
                                          system_shipped, chat_id, item_image)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (order_id, item_id, buyer_id, spec_name, spec_value,
                              quantity, amount, order_status or 'unknown', cookie_id,
                              1 if is_bargain else 0, created_at,
                              receiver_name, receiver_phone, receiver_address, receiver_city,
                              1 if system_shipped else 0, chat_id or '', item_image or ''))
                    else:
                        # 使用默认的创建时间（CURRENT_TIMESTAMP，UTC时间）
                        cursor.execute('''
                        INSERT INTO orders (order_id, item_id, buyer_id, spec_name, spec_value,
                                          quantity, amount, order_status, cookie_id, is_bargain,
                                          receiver_name, receiver_phone, receiver_address, receiver_city,
                                          system_shipped, chat_id, item_image)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (order_id, item_id, buyer_id, spec_name, spec_value,
                              quantity, amount, order_status or 'unknown', cookie_id,
                              1 if is_bargain else 0,
                              receiver_name, receiver_phone, receiver_address, receiver_city,
                              1 if system_shipped else 0, chat_id or '', item_image or ''))
                    logger.info(f"插入新订单: {order_id}")

                self.conn.commit()
                return True

            except Exception as e:
                logger.error(f"插入或更新订单失败: {order_id} - {e}")
                self.conn.rollback()
                return False

    # apply_order_sync_update 的普通明细白名单（快照/规范化字段单独走棘轮，不在此列）
    _ORDER_SYNC_DETAIL_FIELDS = (
        'item_id', 'buyer_id', 'spec_name', 'spec_value', 'quantity', 'amount',
        'receiver_name', 'receiver_phone', 'receiver_address', 'receiver_city',
        'created_at', 'chat_id',
    )
    # 单次 SELECT 取回的现值列（顺序即索引）
    _ORDER_SYNC_EXISTING_COLUMNS = ('order_status',) + _ORDER_SYNC_DETAIL_FIELDS + (
        'item_title', 'item_image', 'item_image_cache_key', 'item_snapshot_source',
        'item_title_source', 'item_image_source',
        'buyer_nickname', 'buyer_avatar_url', 'buyer_snapshot_source',
        'buyer_nickname_source', 'buyer_avatar_source',
        'ordered_at_utc', 'ordered_at_source', 'paid_amount_fen',
    )

    @staticmethod
    def _ratchet_snapshot_group(existing: Dict[str, Any], fields: Sequence[str],
                                incoming: Dict[str, Any], incoming_source: str,
                                source_column: str, at_column: str,
                                observed_at: float) -> Tuple[List[str], List[Any], bool]:
        """快照组棘轮写入：空则填；非空仅当新来源等级严格更高才覆盖。

        返回 (update_fields, update_values, changed)。组来源列单调不降
        （取现有与新来源的等级最大者），at 列在任一字段实际写入时刷新。
        """
        from order_sync_service import snapshot_source_rank

        new_rank = snapshot_source_rank(incoming_source)
        old_source = str(existing.get(source_column) or '')
        old_rank = snapshot_source_rank(old_source)
        update_fields: List[str] = []
        update_values: List[Any] = []
        changed = False
        filled_empty = False
        image_overwritten = False
        for field in fields:
            value = str(incoming.get(field) or '').strip()
            if not value:
                continue
            current = str(existing.get(field) or '')
            if current == value:
                if incoming_source and new_rank > old_rank:
                    changed = True
                continue
            if current and new_rank <= old_rank:
                continue
            update_fields.append(f"{field} = ?")
            update_values.append(value)
            changed = True
            filled_empty = not current
            if field == 'item_image' and current:
                image_overwritten = True
        if changed:
            if filled_empty and incoming_source and incoming_source != old_source:
                update_fields.append(f"{source_column} = ?")
                update_values.append(incoming_source)
            elif new_rank > old_rank:
                update_fields.append(f"{source_column} = ?")
                update_values.append(incoming_source)
            elif not old_source and incoming_source:
                update_fields.append(f"{source_column} = ?")
                update_values.append(incoming_source)
            update_fields.append(f"{at_column} = ?")
            update_values.append(observed_at)
            if image_overwritten:
                # 图片被更高级来源替换时，旧缓存键作废，媒体端点将按需重建
                update_fields.append("item_image_cache_key = ?")
                update_values.append('')
        return update_fields, update_values, changed

    def apply_order_sync_update(self, order_id: str, cookie_id: str,
                                incoming_status: str, platform_status_code: str = '',
                                platform_status_text: str = '', status_source: str = '',
                                sync_error: str = '',
                                item_snapshot: Optional[Dict[str, Any]] = None,
                                buyer_snapshot: Optional[Dict[str, Any]] = None,
                                ordered_at: Optional[Tuple[Optional[float], str]] = None,
                                paid_amount_fen: Optional[int] = None,
                                **details) -> Dict[str, Any]:
        """Apply one verified sync result without downgrading a known status to unknown.

        快照组（item_snapshot: item_title/item_image + source；buyer_snapshot:
        buyer_nickname/buyer_avatar_url + source）按 SNAPSHOT_SOURCE_RANK 棘轮写入；
        规范化字段 ordered_at=(epoch, source) 与 paid_amount_fen 只填空值，
        仅 backfill 假定时区的时间允许被真实报文解析结果纠正。
        """
        from order_sync_service import choose_order_status, normalize_order_status

        with self.lock:
            cursor = self.conn.cursor()
            row = cursor.execute(
                f"SELECT {', '.join(self._ORDER_SYNC_EXISTING_COLUMNS)} "
                "FROM orders WHERE order_id = ? AND cookie_id = ?",
                (order_id, cookie_id),
            ).fetchone()
            if not row:
                return {'updated': False, 'status_changed': False, 'details_changed': False}
            existing = dict(zip(self._ORDER_SYNC_EXISTING_COLUMNS, row))

            # 兼容旧调用：**details 里的 item_image 折叠进 item_snapshot，来源取 status_source
            legacy_image = details.pop('item_image', None)
            if legacy_image and not item_snapshot:
                item_snapshot = {'item_image': legacy_image, 'source': status_source}

            current_status = normalize_order_status(existing['order_status'])
            next_status = choose_order_status(current_status, incoming_status)
            status_changed = next_status != current_status
            update_fields = [
                'order_status = ?',
                'platform_status_code = ?',
                'platform_status_text = ?',
                'status_source = ?',
                'status_synced_at = CURRENT_TIMESTAMP',
                'last_sync_error = ?',
                'updated_at = CURRENT_TIMESTAMP',
                'version = version + 1',
            ]
            update_values: List[Any] = [
                next_status,
                str(platform_status_code or ''),
                str(platform_status_text or ''),
                str(status_source or ''),
                str(sync_error or ''),
            ]
            details_changed = False
            for field in self._ORDER_SYNC_DETAIL_FIELDS:
                value = details.get(field)
                if value in (None, ''):
                    continue
                if str(existing.get(field) or '') != str(value):
                    details_changed = True
                update_fields.append(f"{field} = ?")
                update_values.append(value)

            observed_at = time.time()
            if item_snapshot:
                from order_sync_service import snapshot_source_rank

                changed_item_sources = []
                for field, source_col in (
                    ('item_title', 'item_title_source'),
                    ('item_image', 'item_image_source'),
                ):
                    field_source = str(
                        item_snapshot.get(f'{field}_source')
                        or item_snapshot.get('source')
                        or status_source
                        or ''
                    )
                    snap_fields, snap_values, snap_changed = self._ratchet_snapshot_group(
                        existing,
                        (field,),
                        item_snapshot,
                        field_source,
                        source_col,
                        'item_snapshot_at',
                        observed_at,
                    )
                    update_fields.extend(snap_fields)
                    update_values.extend(snap_values)
                    if snap_changed:
                        changed_item_sources.append(field_source)
                    details_changed = details_changed or snap_changed
                if changed_item_sources:
                    compatibility_source = max(
                        [str(existing.get('item_snapshot_source') or ''), *changed_item_sources],
                        key=snapshot_source_rank,
                    )
                    if compatibility_source != str(existing.get('item_snapshot_source') or ''):
                        update_fields.append('item_snapshot_source = ?')
                        update_values.append(compatibility_source)

            if buyer_snapshot:
                from order_sync_service import snapshot_source_rank

                changed_buyer_sources = []
                for field, source_col in (
                    ('buyer_nickname', 'buyer_nickname_source'),
                    ('buyer_avatar_url', 'buyer_avatar_source'),
                ):
                    field_source = str(
                        buyer_snapshot.get(f'{field}_source')
                        or buyer_snapshot.get('source')
                        or status_source
                        or ''
                    )
                    snap_fields, snap_values, snap_changed = self._ratchet_snapshot_group(
                        existing,
                        (field,),
                        buyer_snapshot,
                        field_source,
                        source_col,
                        'buyer_snapshot_at',
                        observed_at,
                    )
                    update_fields.extend(snap_fields)
                    update_values.extend(snap_values)
                    if snap_changed:
                        changed_buyer_sources.append(field_source)
                    details_changed = details_changed or snap_changed
                if changed_buyer_sources:
                    compatibility_source = max(
                        [
                            str(existing.get('buyer_snapshot_source') or ''),
                            *changed_buyer_sources,
                        ],
                        key=snapshot_source_rank,
                    )
                    if compatibility_source != str(
                        existing.get('buyer_snapshot_source') or ''
                    ):
                        update_fields.append('buyer_snapshot_source = ?')
                        update_values.append(compatibility_source)

            if ordered_at is not None:
                epoch, time_source = ordered_at
                may_correct = existing['ordered_at_source'] == 'backfill_cst_assumed'
                if epoch is not None and (existing['ordered_at_utc'] is None or may_correct):
                    if existing['ordered_at_utc'] != epoch:
                        details_changed = True
                    update_fields.extend(['ordered_at_utc = ?', 'ordered_at_source = ?'])
                    update_values.extend([float(epoch), str(time_source or '')])
            if paid_amount_fen is not None and existing['paid_amount_fen'] is None:
                update_fields.append('paid_amount_fen = ?')
                update_values.append(int(paid_amount_fen))
                details_changed = True

            update_values.extend([order_id, cookie_id])
            cursor.execute(
                f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = ? AND cookie_id = ?",
                update_values,
            )
            self.conn.commit()
            return {
                'updated': cursor.rowcount > 0,
                'status_changed': status_changed,
                'details_changed': details_changed,
                'old_status': current_status,
                'new_status': next_status,
            }

    def upsert_customer_observation(self, cookie_id: str, buyer_id: str,
                                    display_name: str = '', avatar_url: str = '',
                                    source: str = '', observed_at: Optional[float] = None,
                                    display_name_source: Optional[str] = None,
                                    avatar_source: Optional[str] = None) -> bool:
        """记录一次买家观察：维护 (cookie_id, buyer_id) 的当前可用身份档案。

        first_observed_at 取历史最小（回填旧订单可前移），last_observed_at 取最大；
        昵称与头像按各自来源独立棘轮（空则填，非空仅更高级来源覆盖）；
        profile_source 保留为两个字段当前来源中等级更高者，供旧调用方兼容。
        行为计数不在本表维护，一律查询时从 orders 现算。
        """
        from order_sync_service import snapshot_source_rank

        if not cookie_id or not buyer_id:
            return False
        moment = float(observed_at if observed_at is not None else time.time())
        display_name = str(display_name or '').strip()
        avatar_url = str(avatar_url or '').strip()
        source = str(source or '')
        incoming_name_source = str(
            source if display_name_source is None else display_name_source
        )
        incoming_avatar_source = str(
            source if avatar_source is None else avatar_source
        )
        if not display_name and not avatar_url:
            return False

        def aggregate_source(name_source: str, image_source: str) -> str:
            candidates = [value for value in (name_source, image_source) if value]
            return max(candidates, key=snapshot_source_rank) if candidates else ''

        with self.lock:
            try:
                cursor = self.conn.cursor()
                row = cursor.execute(
                    "SELECT display_name, avatar_url, profile_source,"
                    " display_name_source, avatar_source, first_observed_at,"
                    " last_observed_at FROM customer_profiles"
                    " WHERE cookie_id = ? AND buyer_id = ?",
                    (cookie_id, buyer_id),
                ).fetchone()
                if row is None:
                    name_source = incoming_name_source if display_name else ''
                    image_source = incoming_avatar_source if avatar_url else ''
                    cursor.execute(
                        "INSERT INTO customer_profiles (cookie_id, buyer_id, display_name,"
                        " avatar_url, profile_source, display_name_source, avatar_source,"
                        " first_observed_at, last_observed_at, observation_count)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (
                            cookie_id, buyer_id, display_name, avatar_url,
                            aggregate_source(name_source, image_source),
                            name_source, image_source, moment, moment,
                        ),
                    )
                else:
                    old_name_source = str(row[3] or (row[2] if row[0] else ''))
                    old_avatar_source = str(row[4] or (row[2] if row[1] else ''))
                    wrote_name = bool(
                        display_name
                        and (
                            not row[0]
                            or snapshot_source_rank(incoming_name_source)
                            > snapshot_source_rank(old_name_source)
                        )
                    )
                    wrote_avatar = bool(
                        avatar_url
                        and (
                            not row[1]
                            or snapshot_source_rank(incoming_avatar_source)
                            > snapshot_source_rank(old_avatar_source)
                        )
                    )
                    next_name = display_name if wrote_name else row[0]
                    next_avatar = avatar_url if wrote_avatar else row[1]
                    next_name_source = (
                        incoming_name_source if wrote_name else old_name_source
                    )
                    next_avatar_source = (
                        incoming_avatar_source if wrote_avatar else old_avatar_source
                    )
                    next_source = aggregate_source(
                        next_name_source if next_name else '',
                        next_avatar_source if next_avatar else '',
                    )
                    cursor.execute(
                        "UPDATE customer_profiles SET display_name = ?, avatar_url = ?,"
                        " profile_source = ?, display_name_source = ?, avatar_source = ?,"
                        " first_observed_at = ?, last_observed_at = ?,"
                        " observation_count = observation_count + 1,"
                        " updated_at = CAST(strftime('%s','now') AS REAL)"
                        " WHERE cookie_id = ? AND buyer_id = ?",
                        (next_name, next_avatar, next_source,
                         next_name_source, next_avatar_source,
                         min(float(row[5]), moment), max(float(row[6]), moment),
                         cookie_id, buyer_id),
                    )
                self.conn.commit()
                return True
            except Exception as exc:
                logger.error(f"记录买家观察失败: {type(exc).__name__}")
                self.conn.rollback()
                return False

    def get_customer_profiles(self, cookie_ids: List[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """按账号集合取买家档案，键为 (cookie_id, buyer_id)。"""
        if not cookie_ids:
            return {}
        with self.lock:
            try:
                cursor = self.conn.cursor()
                placeholders = ','.join('?' for _ in cookie_ids)
                rows = cursor.execute(
                    "SELECT cookie_id, buyer_id, display_name, avatar_url, profile_source,"
                    " display_name_source, avatar_source,"
                    f" first_observed_at, last_observed_at, observation_count"
                    f" FROM customer_profiles WHERE cookie_id IN ({placeholders})",
                    [str(cid) for cid in cookie_ids],
                ).fetchall()
                return {
                    (row[0], row[1]): {
                        'display_name': row[2],
                        'avatar_url': row[3],
                        'profile_source': row[4],
                        'display_name_source': row[5],
                        'avatar_source': row[6],
                        'first_observed_at': row[7],
                        'last_observed_at': row[8],
                        'observation_count': row[9],
                    }
                    for row in rows
                }
            except Exception as exc:
                logger.error(f"获取买家档案失败: {type(exc).__name__}")
                raise OrderQueryError("获取买家档案失败") from exc

    def get_customer_profile(
        self,
        cookie_id: str,
        buyer_id: str,
    ) -> Optional[Dict[str, Any]]:
        """精确读取一个账号买家档案；未找到返回 None，查询故障抛异常。"""
        with self.lock:
            try:
                row = self.conn.execute(
                    "SELECT display_name, avatar_url, profile_source,"
                    " display_name_source, avatar_source,"
                    " first_observed_at, last_observed_at, observation_count"
                    " FROM customer_profiles"
                    " WHERE cookie_id = ? AND buyer_id = ?",
                    (str(cookie_id), str(buyer_id)),
                ).fetchone()
                if row is None:
                    return None
                return {
                    'display_name': row[0],
                    'avatar_url': row[1],
                    'profile_source': row[2],
                    'display_name_source': row[3],
                    'avatar_source': row[4],
                    'first_observed_at': row[5],
                    'last_observed_at': row[6],
                    'observation_count': row[7],
                }
            except Exception as exc:
                logger.error(f"获取单个买家档案失败: {type(exc).__name__}")
                raise OrderQueryError("获取买家档案失败") from exc

    # query_orders 列表列：明确排除 receiver_* 收货隐私（只在详情返回）
    _ORDER_LIST_COLUMNS = (
        'o.order_id', 'o.item_id', 'o.buyer_id', 'o.spec_name', 'o.spec_value',
        'o.quantity', 'o.amount', 'o.order_status', 'o.cookie_id', 'o.is_bargain',
        'o.created_at', 'o.updated_at', 'o.version', 'o.chat_id',
        'o.platform_status_code', 'o.platform_status_text', 'o.status_source',
        'o.status_synced_at', 'o.last_sync_error',
        'o.item_title', 'o.item_image', 'o.item_snapshot_source',
        'o.item_title_source', 'o.item_image_source',
        'o.buyer_nickname', 'o.buyer_avatar_url', 'o.buyer_snapshot_source',
        'o.buyer_nickname_source', 'o.buyer_avatar_source',
        'o.ordered_at_utc', 'o.ordered_at_source', 'o.paid_amount_fen',
        'ci.item_title AS catalog_title', 'ci.item_image AS catalog_image',
        'ci.item_price AS catalog_price',
        'cp.display_name AS profile_display_name',
        'cp.avatar_url AS profile_avatar_url',
        'cp.profile_source AS profile_source',
        'cp.display_name_source AS profile_display_name_source',
        'cp.avatar_source AS profile_avatar_source',
    )

    def query_orders(self, cookie_ids: List[str], status: Optional[str] = None,
                     search: str = '', start_date: Optional[str] = None,
                     end_date: Optional[str] = None, page: int = 1,
                     page_size: int = 20) -> Dict[str, Any]:
        """服务端过滤+分页的订单列表查询（不含收货隐私字段）。

        cookie_ids 必须是调用方已校验归属的账号集合；空集合直接返回空页。
        日期在 Python 中按 Asia/Shanghai 日历换算为 UTC 半开区间；
        标准化行走 ordered_at_utc 的数值可索引分支，旧行仅在该列为 NULL
        时走 created_at 文本兼容分支。搜索覆盖订单号/商品ID/快照标题/
        目录标题/买家昵称。
        """
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        if not cookie_ids:
            return {'items': [], 'total': 0, 'page': page, 'page_size': page_size}
        with self.lock:
            try:
                cursor = self.conn.cursor()
                placeholders = ','.join('?' for _ in cookie_ids)
                normalized_cookie_ids = [str(cid) for cid in cookie_ids]
                outer_where: List[str] = []
                outer_params: List[Any] = []
                if status:
                    outer_where.append("o.order_status = ?")
                    outer_params.append(str(status))
                if search:
                    escaped = (str(search).replace('\\', '\\\\')
                               .replace('%', '\\%').replace('_', '\\_'))
                    like = f"%{escaped}%"
                    outer_where.append(
                        "(o.order_id LIKE ? ESCAPE '\\' OR o.item_id LIKE ? ESCAPE '\\'"
                        " OR o.item_title LIKE ? ESCAPE '\\' OR o.buyer_nickname LIKE ? ESCAPE '\\'"
                        " OR IFNULL(ci.item_title, '') LIKE ? ESCAPE '\\'"
                        " OR IFNULL(cp.display_name, '') LIKE ? ESCAPE '\\')"
                    )
                    outer_params.extend([like] * 6)
                start_epoch = start_created_at = None
                end_epoch = end_created_at = None
                if start_date:
                    start_epoch, start_created_at = _order_query_date_bound(
                        start_date,
                        next_day=False,
                    )
                if end_date:
                    end_epoch, end_created_at = _order_query_date_bound(
                        end_date,
                        next_day=True,
                    )
                if (
                    start_epoch is not None
                    and end_epoch is not None
                    and start_epoch >= end_epoch
                ):
                    raise ValueError("开始日期不得晚于结束日期")

                cte = ""
                scoped_source = "orders o"
                scoped_params: List[Any] = []
                if start_date or end_date:
                    normalized_terms = [
                        f"o.cookie_id IN ({placeholders})",
                        "o.ordered_at_utc IS NOT NULL",
                    ]
                    legacy_terms = [
                        f"o.cookie_id IN ({placeholders})",
                        "o.ordered_at_utc IS NULL",
                    ]
                    normalized_params: List[Any] = []
                    legacy_params: List[Any] = []
                    if start_epoch is not None:
                        normalized_terms.append("o.ordered_at_utc >= ?")
                        legacy_terms.append("o.created_at >= ?")
                        normalized_params.append(start_epoch)
                        legacy_params.append(start_created_at)
                    if end_epoch is not None:
                        normalized_terms.append("o.ordered_at_utc < ?")
                        legacy_terms.append("o.created_at < ?")
                        normalized_params.append(end_epoch)
                        legacy_params.append(end_created_at)
                    cte = (
                        "WITH scoped_orders AS ("
                        "SELECT o.* FROM orders o"
                        f" WHERE {' AND '.join(normalized_terms)}"
                        " UNION ALL "
                        "SELECT o.* FROM orders o"
                        f" WHERE {' AND '.join(legacy_terms)}"
                        ") "
                    )
                    scoped_source = "scoped_orders o"
                    scoped_params = [
                        *normalized_cookie_ids,
                        *normalized_params,
                        *normalized_cookie_ids,
                        *legacy_params,
                    ]
                else:
                    outer_where.insert(
                        0,
                        f"o.cookie_id IN ({placeholders})",
                    )
                    scoped_params = normalized_cookie_ids

                where_sql = (
                    f" WHERE {' AND '.join(outer_where)}"
                    if outer_where
                    else ""
                )
                base = (f"FROM {scoped_source} LEFT JOIN item_info ci"
                        " ON ci.cookie_id = o.cookie_id AND ci.item_id = o.item_id"
                        " LEFT JOIN customer_profiles cp"
                        " ON cp.cookie_id = o.cookie_id AND cp.buyer_id = o.buyer_id"
                        f"{where_sql}")
                query_params = [*scoped_params, *outer_params]
                total = cursor.execute(
                    f"{cte}SELECT COUNT(*) {base}",
                    query_params,
                ).fetchone()[0]
                rows = cursor.execute(
                    f"{cte}SELECT {', '.join(self._ORDER_LIST_COLUMNS)} {base}"
                    " ORDER BY o.ordered_at_utc DESC, o.order_id DESC LIMIT ? OFFSET ?",
                    [*query_params, page_size, (page - 1) * page_size],
                ).fetchall()
                column_names = [
                    col.split(' AS ')[-1].split('.')[-1] for col in self._ORDER_LIST_COLUMNS
                ]
                items = []
                for row in rows:
                    record = dict(zip(column_names, row))
                    record['is_bargain'] = bool(record.get('is_bargain'))
                    record['status'] = record.get('order_status')
                    items.append(record)
                return {'items': items, 'total': total, 'page': page, 'page_size': page_size}
            except Exception as exc:
                logger.error(f"查询订单列表失败: {type(exc).__name__}")
                raise OrderQueryError("查询订单列表失败") from exc

    # 详情列：在列表列基础上追加收货隐私（仅详情返回）与快照时间戳/缓存键
    _ORDER_DETAIL_COLUMNS = (
        'order_id', 'item_id', 'buyer_id', 'spec_name', 'spec_value',
        'quantity', 'amount', 'order_status', 'cookie_id', 'is_bargain',
        'created_at', 'updated_at', 'version', 'chat_id',
        'platform_status_code', 'platform_status_text', 'status_source',
        'status_synced_at', 'last_sync_error',
        'item_title', 'item_image', 'item_image_cache_key',
        'item_snapshot_source', 'item_title_source', 'item_image_source', 'item_snapshot_at',
        'buyer_nickname', 'buyer_avatar_url', 'buyer_snapshot_source',
        'buyer_nickname_source', 'buyer_avatar_source', 'buyer_snapshot_at',
        'ordered_at_utc', 'ordered_at_source', 'paid_amount_fen',
        'receiver_name', 'receiver_phone', 'receiver_address', 'receiver_city',
        'system_shipped',
    )

    @staticmethod
    def _order_owner_scope(user_id: Optional[int] = None,
                           cookie_ids: Optional[Iterable[str]] = None
                           ) -> Tuple[str, List[Any]]:
        """把订单归属条件拼成 SQL 片段，避免路由层“先查后判”留下 TOCTOU 窗口。

        两个参数都为 None 表示不限制归属（同步、履约等系统路径）。
        显式传入空的 owned cookie 集合表示调用方名下没有任何账号，此时必须匹配不到
        任何订单（失败关闭），而不是退化成无归属条件的裸查。
        """
        clauses: List[str] = []
        params: List[Any] = []
        if user_id is not None:
            clauses.append("cookie_id IN (SELECT id FROM cookies WHERE user_id = ?)")
            params.append(int(user_id))
        if cookie_ids is not None:
            normalized = [str(cookie_id) for cookie_id in cookie_ids]
            if not normalized:
                return " AND 1 = 0", []
            placeholders = ', '.join('?' for _ in normalized)
            clauses.append(f"cookie_id IN ({placeholders})")
            params.extend(normalized)
        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    def get_order_by_id(self, order_id: str, user_id: Optional[int] = None,
                        cookie_ids: Optional[Iterable[str]] = None):
        """根据订单ID获取订单详情（含收货信息与成交快照全字段）

        传入 user_id 或 owned cookie 集合时，归属条件直接进 WHERE；不归属的订单
        与不存在的订单一样返回 None。
        """
        scope_sql, scope_params = self._order_owner_scope(user_id, cookie_ids)
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    f"SELECT {', '.join(self._ORDER_DETAIL_COLUMNS)} FROM orders"
                    f" WHERE order_id = ?{scope_sql}",
                    (order_id, *scope_params),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                record = dict(zip(self._ORDER_DETAIL_COLUMNS, row))
                record['id'] = record['order_id']
                record['status'] = record['order_status']  # 兼容旧代码的别名
                record['is_bargain'] = bool(record['is_bargain']) if record['is_bargain'] is not None else False
                record['system_shipped'] = bool(record['system_shipped']) if record['system_shipped'] is not None else False
                record['version'] = record['version'] if record['version'] is not None else 1
                record['chat_id'] = record['chat_id'] or ''
                return record

            except Exception as exc:
                logger.error(
                    f"获取订单信息失败: {order_id} - {type(exc).__name__}"
                )
                raise OrderQueryError("获取订单详情失败") from exc

    def set_order_item_image_cache_key(self, order_id: str, cache_key: str,
                                       expected_image: str) -> bool:
        """媒体端点缓存落盘后回写缓存键；expected_image 断言防止竞态覆盖新图。"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    "UPDATE orders SET item_image_cache_key = ? WHERE order_id = ? AND item_image = ?",
                    (str(cache_key or ''), order_id, str(expected_image or '')),
                )
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as exc:
                logger.error(f"写入订单图缓存键失败: {order_id} - {type(exc).__name__}")
                self.conn.rollback()
                return False

    def delete_order(self, order_id: str, user_id: Optional[int] = None,
                     cookie_ids: Optional[Iterable[str]] = None):
        """删除订单；归属条件与删除同属一条语句，杜绝先查后删的竞态窗口"""
        scope_sql, scope_params = self._order_owner_scope(user_id, cookie_ids)
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    f'DELETE FROM orders WHERE order_id = ?{scope_sql}',
                    (order_id, *scope_params),
                )
                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除订单成功: {order_id}")
                    return True
                # 归属拒绝/空集合会走到这里；0 行删除也已隐式开启事务，
                # 必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                self.conn.rollback()
                return False
            except Exception as e:
                logger.error(f"删除订单失败: {order_id} - {e}")
                self.conn.rollback()
                return False

    def record_order_status_event(self, cookie_id: str, normalized_status: str,
                                  raw_status: str = '', order_id: str = '',
                                  item_id: str = '', buyer_id: str = '',
                                  chat_id: str = '', source: str = 'system_message',
                                  occurred_at: float = None) -> int:
        """Persist a status event until it can be matched deterministically."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            INSERT INTO order_status_events (
                cookie_id, order_id, item_id, buyer_id, chat_id,
                normalized_status, raw_status, source, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                cookie_id,
                str(order_id or ''),
                str(item_id or ''),
                str(buyer_id or ''),
                str(chat_id or ''),
                str(normalized_status or 'unknown'),
                str(raw_status or ''),
                str(source or 'system_message'),
                float(occurred_at if occurred_at is not None else time.time()),
            ))
            event_id = int(cursor.lastrowid)
            self.conn.commit()
            return event_id

    def reconcile_order_status_events(self, cookie_id: str, order_id: str,
                                      item_id: str = '', buyer_id: str = '',
                                      chat_id: str = '') -> List[Dict[str, Any]]:
        """Match pending events by identity fields only; never consume them FIFO."""
        from order_sync_service import choose_order_status

        normalized_order_id = str(order_id or '')
        normalized_item_id = str(item_id or '')
        normalized_buyer_id = str(buyer_id or '')
        normalized_chat_id = str(chat_id or '')
        if not normalized_order_id:
            return []

        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, order_id, item_id, buyer_id, chat_id,
                   normalized_status, raw_status, source, occurred_at
            FROM order_status_events
            WHERE cookie_id = ? AND match_state = 'pending'
            ORDER BY occurred_at ASC, id ASC
            ''', (cookie_id,))
            matched = []
            for row in cursor.fetchall():
                event_order_id = str(row[1] or '')
                event_item_id = str(row[2] or '')
                event_buyer_id = str(row[3] or '')
                event_chat_id = str(row[4] or '')
                exact_order_match = bool(event_order_id and event_order_id == normalized_order_id)
                item_buyer_match = bool(
                    event_item_id and event_buyer_id
                    and event_item_id == normalized_item_id
                    and event_buyer_id == normalized_buyer_id
                )
                chat_match = bool(event_chat_id and event_chat_id == normalized_chat_id)
                if not (exact_order_match or item_buyer_match or chat_match):
                    continue

                order_row = cursor.execute(
                    "SELECT order_status FROM orders WHERE order_id = ? AND cookie_id = ?",
                    (normalized_order_id, cookie_id),
                ).fetchone()
                if not order_row:
                    continue
                next_status = choose_order_status(order_row[0], row[5])
                cursor.execute('''
                UPDATE orders
                SET order_status = ?, platform_status_text = ?, status_source = ?,
                    status_synced_at = CURRENT_TIMESTAMP, last_sync_error = '',
                    updated_at = CURRENT_TIMESTAMP, version = version + 1
                WHERE order_id = ? AND cookie_id = ?
                ''', (next_status, row[6] or '', row[7] or 'system_message', normalized_order_id, cookie_id))
                cursor.execute('''
                UPDATE order_status_events
                SET match_state = 'matched', matched_order_id = ?, matched_at = ?
                WHERE id = ?
                ''', (normalized_order_id, time.time(), row[0]))
                matched.append({
                    'id': row[0],
                    'normalized_status': row[5],
                    'raw_status': row[6] or '',
                    'source': row[7] or 'system_message',
                })
            self.conn.commit()
            return matched

    def get_recent_order_by_item_and_buyer(self, item_id: str, buyer_id: str):
        """根据商品ID和买家ID获取最近的订单

        Args:
            item_id: 商品ID
            buyer_id: 买家ID

        Returns:
            dict: 订单信息，如果没有找到则返回None
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                       quantity, amount, order_status, cookie_id, is_bargain, created_at, updated_at
                FROM orders
                WHERE item_id = ? AND buyer_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                ''', (item_id, buyer_id))

                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],  # 使用 order_id 作为 id
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'spec_name': row[3],
                        'spec_value': row[4],
                        'quantity': row[5],
                        'amount': row[6],
                        'order_status': row[7],
                        'cookie_id': row[8],
                        'is_bargain': bool(row[9]) if row[9] is not None else False,
                        'created_at': row[10],
                        'updated_at': row[11]
                    }
                return None

            except Exception as e:
                logger.error(f"获取订单信息失败: item_id={item_id}, buyer_id={buyer_id} - {e}")
                return None

    def get_orders_by_cookie(self, cookie_id: str, limit: int = 100):
        """根据Cookie ID获取订单列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                       quantity, amount, order_status, is_bargain, created_at, updated_at,
                       receiver_name, receiver_phone, receiver_address, receiver_city,
                       platform_status_code, platform_status_text, status_source,
                       status_synced_at, last_sync_error, item_image
                FROM orders WHERE cookie_id = ?
                ORDER BY created_at DESC LIMIT ?
                ''', (cookie_id, limit))

                orders = []
                for row in cursor.fetchall():
                    orders.append({
                        'id': row[0],  # 使用 order_id 作为 id
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'spec_name': row[3],
                        'spec_value': row[4],
                        'quantity': row[5],
                        'amount': row[6],
                        'status': row[7],
                        'is_bargain': bool(row[8]) if row[8] is not None else False,
                        'created_at': row[9],
                        'updated_at': row[10],
                        'receiver_name': row[11],
                        'receiver_phone': row[12],
                        'receiver_address': row[13],
                        'receiver_city': row[14],
                        'platform_status_code': row[15],
                        'platform_status_text': row[16],
                        'status_source': row[17],
                        'status_synced_at': row[18],
                        'last_sync_error': row[19],
                        'item_image': row[20] or '',
                    })

                return orders

            except Exception as e:
                logger.error(f"获取Cookie订单列表失败: {cookie_id} - {e}")
                return []

    def get_all_orders(self, limit: int = 1000):
        """获取所有订单列表"""
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT order_id, item_id, buyer_id, spec_name, spec_value,
                       quantity, amount, order_status, cookie_id, is_bargain, created_at, updated_at
                FROM orders
                ORDER BY created_at DESC LIMIT ?
                ''', (limit,))

                orders = []
                for row in cursor.fetchall():
                    orders.append({
                        'id': row[0],
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'spec_name': row[3],
                        'spec_value': row[4],
                        'quantity': row[5],
                        'amount': row[6],
                        'status': row[7],
                        'cookie_id': row[8],
                        'is_bargain': bool(row[9]) if row[9] is not None else False,
                        'created_at': row[10],
                        'updated_at': row[11]
                    })

                return orders

            except Exception as e:
                logger.error(f"获取所有订单列表失败: {e}")
                return []

    def delete_table_record(self, table_name: str, record_id: str):
        """删除指定表的指定记录"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 根据表名确定主键字段
                primary_key_map = {
                    'users': 'id',
                    'cookies': 'id',
                    'cookie_status': 'id',
                    'keywords': 'id',
                    'default_replies': 'id',
                    'default_reply_records': 'id',
                    'item_replay': 'item_id',
                    'ai_reply_settings': 'id',
                    'ai_conversations': 'id',
                    'ai_item_cache': 'id',
                    'item_info': 'id',
                    'message_notifications': 'id',
                    'cards': 'id',
                    'delivery_rules': 'id',
                    'notification_channels': 'id',
                    'user_settings': 'id',
                    'system_settings': 'id',
                    'email_verifications': 'id',
                    'captcha_codes': 'id',
                    'orders': 'order_id'
                }

                primary_key = primary_key_map.get(table_name, 'id')

                # 删除记录
                cursor.execute(f"DELETE FROM {table_name} WHERE {primary_key} = ?", (record_id,))

                if cursor.rowcount > 0:
                    self.conn.commit()
                    logger.info(f"删除表记录成功: {table_name}.{record_id}")
                    return True
                else:
                    logger.warning(f"删除表记录失败，记录不存在: {table_name}.{record_id}")
                    # 0 行也已隐式开启事务，必须显式结束，否则悬挂事务会击穿后续 BEGIN IMMEDIATE
                    self.conn.rollback()
                    return False

            except Exception as e:
                logger.error(f"删除表记录失败: {table_name}.{record_id} - {e}")
                self.conn.rollback()
                return False

    def clear_table_data(self, table_name: str):
        """清空指定表的所有数据"""
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # 清空表数据
                cursor.execute(f"DELETE FROM {table_name}")

                # 重置自增ID（如果有的话）
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))

                self.conn.commit()
                logger.info(f"清空表数据成功: {table_name}")
                return True

            except Exception as e:
                logger.error(f"清空表数据失败: {table_name} - {e}")
                self.conn.rollback()
                return False

    def upgrade_keywords_table_for_image_support(self, cursor):
        """升级keywords表以支持图片关键词"""
        try:
            logger.info("开始升级keywords表以支持图片关键词...")

            # 检查是否已经有type字段
            cursor.execute("PRAGMA table_info(keywords)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'type' not in columns:
                logger.info("添加type字段到keywords表...")
                cursor.execute("ALTER TABLE keywords ADD COLUMN type TEXT DEFAULT 'text'")

            if 'image_url' not in columns:
                logger.info("添加image_url字段到keywords表...")
                cursor.execute("ALTER TABLE keywords ADD COLUMN image_url TEXT")

            # 为现有记录设置默认类型
            cursor.execute("UPDATE keywords SET type = 'text' WHERE type IS NULL")

            logger.info("keywords表升级完成")
            return True

        except Exception as e:
            logger.error(f"升级keywords表失败: {e}")
            raise
    def get_item_reply(self, cookie_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定账号和商品的回复内容

        Args:
            cookie_id (str): 账号ID
            item_id (str): 商品ID

        Returns:
            Dict: 包含回复内容的字典，如果不存在返回None
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT reply_content, created_at, updated_at
                    FROM item_replay
                    WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))

                row = cursor.fetchone()
                if row:
                    return {
                        'reply_content': row[0] or '',
                        'created_at': row[1],
                        'updated_at': row[2]
                    }
                return None
        except Exception as e:
            logger.error(f"获取指定商品回复失败: {e}")
            return None

    def update_item_reply(self, cookie_id: str, item_id: str, reply_content: str) -> bool:
        """
        更新指定cookie和item的回复内容及更新时间

        Args:
            cookie_id (str): 账号ID
            item_id (str): 商品ID
            reply_content (str): 回复内容

        Returns:
            bool: 更新成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    UPDATE item_replay
                    SET reply_content = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE cookie_id = ? AND item_id = ?
                ''', (reply_content, cookie_id, item_id))

                if cursor.rowcount == 0:
                    # 如果没更新到，说明该条记录不存在，可以考虑插入
                    cursor.execute('''
                        INSERT INTO item_replay (item_id, cookie_id, reply_content, created_at, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ''', (item_id, cookie_id, reply_content))

                self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"更新商品回复失败: {e}")
            return False

    def get_itemReplays_by_cookie(self, cookie_id: str) -> List[Dict]:
        """获取指定Cookie的所有商品信息

        Args:
            cookie_id: Cookie ID

        Returns:
            List[Dict]: 商品信息列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                SELECT r.item_id, r.cookie_id, r.reply_content, r.created_at, r.updated_at, i.item_title, i.item_detail
                    FROM item_replay r
                    LEFT JOIN item_info i ON i.item_id = r.item_id
                    WHERE r.cookie_id = ?
                    ORDER BY r.updated_at DESC
                ''', (cookie_id,))

                columns = [description[0] for description in cursor.description]
                items = []

                for row in cursor.fetchall():
                    item_info = dict(zip(columns, row))

                    items.append(item_info)

                return items

        except Exception as e:
            logger.error(f"获取Cookie商品信息失败: {e}")
            return []

    def delete_item_reply(self, cookie_id: str, item_id: str) -> bool:
        """
        删除指定 cookie_id 和 item_id 的商品回复

        Args:
            cookie_id: Cookie ID
            item_id: 商品ID

        Returns:
            bool: 删除成功返回 True，失败返回 False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    DELETE FROM item_replay
                    WHERE cookie_id = ? AND item_id = ?
                ''', (cookie_id, item_id))
                self.conn.commit()
                # 判断是否有删除行
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除商品回复失败: {e}")
            return False

    def batch_delete_item_replies(self, items: List[Dict[str, str]]) -> Dict[str, int]:
        """
        批量删除商品回复

        Args:
            items: List[Dict] 每个字典包含 cookie_id 和 item_id

        Returns:
            Dict[str, int]: 返回成功和失败的数量，例如 {"success_count": 3, "failed_count": 1}
        """
        success_count = 0
        failed_count = 0

        try:
            with self.lock:
                cursor = self.conn.cursor()
                for item in items:
                    cookie_id = item.get('cookie_id')
                    item_id = item.get('item_id')
                    if not cookie_id or not item_id:
                        failed_count += 1
                        continue
                    cursor.execute('''
                        DELETE FROM item_replay
                        WHERE cookie_id = ? AND item_id = ?
                    ''', (cookie_id, item_id))
                    if cursor.rowcount > 0:
                        success_count += 1
                    else:
                        failed_count += 1
                self.conn.commit()
        except Exception as e:
            logger.error(f"批量删除商品回复失败: {e}")
            # 整体失败则视为全部失败
            return {"success_count": 0, "failed_count": len(items)}

        return {"success_count": success_count, "failed_count": failed_count}

    # ==================== 风控日志管理 ====================

    def add_risk_control_log(self, cookie_id: str, event_type: str = 'slider_captcha',
                           event_description: str = None, processing_result: str = None,
                           processing_status: str = 'processing', error_message: str = None) -> bool:
        """
        添加风控日志记录

        Args:
            cookie_id: Cookie ID
            event_type: 事件类型，默认为'slider_captcha'
            event_description: 事件描述
            processing_result: 处理结果
            processing_status: 处理状态 ('processing', 'success', 'failed')
            error_message: 错误信息

        Returns:
            bool: 添加成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO risk_control_logs
                    (cookie_id, event_type, event_description, processing_result, processing_status, error_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (cookie_id, event_type, event_description, processing_result, processing_status, error_message))
                self.conn.commit()
                return True
        except Exception as e:
            logger.error(f"添加风控日志失败: {e}")
            return False

    def update_risk_control_log(self, log_id: int, processing_result: str = None,
                              processing_status: str = None, error_message: str = None) -> bool:
        """
        更新风控日志记录

        Args:
            log_id: 日志ID
            processing_result: 处理结果
            processing_status: 处理状态
            error_message: 错误信息

        Returns:
            bool: 更新成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 构建更新语句
                update_fields = []
                params = []

                if processing_result is not None:
                    update_fields.append("processing_result = ?")
                    params.append(processing_result)

                if processing_status is not None:
                    update_fields.append("processing_status = ?")
                    params.append(processing_status)

                if error_message is not None:
                    update_fields.append("error_message = ?")
                    params.append(error_message)

                if update_fields:
                    update_fields.append("updated_at = CURRENT_TIMESTAMP")
                    params.append(log_id)

                    sql = f"UPDATE risk_control_logs SET {', '.join(update_fields)} WHERE id = ?"
                    cursor.execute(sql, params)
                    self.conn.commit()
                    return cursor.rowcount > 0

                return False
        except Exception as e:
            logger.error(f"更新风控日志失败: {e}")
            return False

    def get_risk_control_logs(self, cookie_id: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        获取风控日志列表

        Args:
            cookie_id: Cookie ID，为None时获取所有日志
            limit: 限制返回数量
            offset: 偏移量

        Returns:
            List[Dict]: 风控日志列表
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                if cookie_id:
                    cursor.execute('''
                        SELECT r.*, c.id as cookie_name
                        FROM risk_control_logs r
                        LEFT JOIN cookies c ON r.cookie_id = c.id
                        WHERE r.cookie_id = ?
                        ORDER BY r.created_at DESC
                        LIMIT ? OFFSET ?
                    ''', (cookie_id, limit, offset))
                else:
                    cursor.execute('''
                        SELECT r.*, c.id as cookie_name
                        FROM risk_control_logs r
                        LEFT JOIN cookies c ON r.cookie_id = c.id
                        ORDER BY r.created_at DESC
                        LIMIT ? OFFSET ?
                    ''', (limit, offset))

                columns = [description[0] for description in cursor.description]
                logs = []

                for row in cursor.fetchall():
                    log_info = dict(zip(columns, row))
                    logs.append(log_info)

                return logs
        except Exception as e:
            logger.error(f"获取风控日志失败: {e}")
            return []

    def get_risk_control_logs_count(self, cookie_id: str = None) -> int:
        """
        获取风控日志总数

        Args:
            cookie_id: Cookie ID，为None时获取所有日志数量

        Returns:
            int: 日志总数
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()

                if cookie_id:
                    cursor.execute('SELECT COUNT(*) FROM risk_control_logs WHERE cookie_id = ?', (cookie_id,))
                else:
                    cursor.execute('SELECT COUNT(*) FROM risk_control_logs')

                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"获取风控日志数量失败: {e}")
            return 0

    def delete_risk_control_log(self, log_id: int) -> bool:
        """
        删除风控日志记录

        Args:
            log_id: 日志ID

        Returns:
            bool: 删除成功返回True，失败返回False
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute('DELETE FROM risk_control_logs WHERE id = ?', (log_id,))
                self.conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除风控日志失败: {e}")
            return False

    def cleanup_old_data(self, days: int = 90) -> dict:
        """清理过期的历史数据，防止数据库无限增长

        Args:
            days: 保留最近N天的数据，默认90天

        Returns:
            清理统计信息
        """
        try:
            with self.lock:
                cursor = self.conn.cursor()
                stats = {}

                # 清理AI对话历史（保留最近90天）
                try:
                    cursor.execute(
                        "DELETE FROM ai_conversations WHERE created_at < datetime('now', '-' || ? || ' days')",
                        (days,)
                    )
                    stats['ai_conversations'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的AI对话记录（{days}天前）")
                except Exception as e:
                    logger.warning(f"清理AI对话历史失败: {e}")
                    stats['ai_conversations'] = 0

                # 清理风控日志（保留最近90天）
                try:
                    cursor.execute(
                        "DELETE FROM risk_control_logs WHERE created_at < datetime('now', '-' || ? || ' days')",
                        (days,)
                    )
                    stats['risk_control_logs'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的风控日志（{days}天前）")
                except Exception as e:
                    logger.warning(f"清理风控日志失败: {e}")
                    stats['risk_control_logs'] = 0

                # 清理AI商品缓存（保留最近30天）
                cache_days = min(days, 30)  # AI商品缓存最多保留30天
                try:
                    cursor.execute(
                        "DELETE FROM ai_item_cache WHERE last_updated < datetime('now', '-' || ? || ' days')",
                        (cache_days,)
                    )
                    stats['ai_item_cache'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的AI商品缓存（{cache_days}天前）")
                except Exception as e:
                    logger.warning(f"清理AI商品缓存失败: {e}")
                    stats['ai_item_cache'] = 0

                # 清理验证码记录（保留最近1天）
                try:
                    cursor.execute(
                        "DELETE FROM captcha_codes WHERE created_at < datetime('now', '-1 day')"
                    )
                    stats['captcha_codes'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的验证码记录")
                except Exception as e:
                    logger.warning(f"清理验证码记录失败: {e}")
                    stats['captcha_codes'] = 0

                # 清理邮箱验证记录（保留最近7天）
                try:
                    cursor.execute(
                        "DELETE FROM email_verifications WHERE created_at < datetime('now', '-7 days')"
                    )
                    stats['email_verifications'] = cursor.rowcount
                    if cursor.rowcount > 0:
                        logger.info(f"清理了 {cursor.rowcount} 条过期的邮箱验证记录")
                except Exception as e:
                    logger.warning(f"清理邮箱验证记录失败: {e}")
                    stats['email_verifications'] = 0

                # 提交更改
                self.conn.commit()

                # 执行VACUUM以释放磁盘空间（仅当清理了大量数据时）
                total_cleaned = sum(stats.values())
                if total_cleaned > 100:
                    logger.info(f"共清理了 {total_cleaned} 条记录，执行VACUUM以释放磁盘空间...")
                    cursor.execute("VACUUM")
                    logger.info("VACUUM执行完成")
                    stats['vacuum_executed'] = True
                else:
                    stats['vacuum_executed'] = False

                stats['total_cleaned'] = total_cleaned
                return stats

        except Exception as e:
            logger.error(f"清理历史数据时出错: {e}")
            return {'error': str(e)}

    # ==================== BI报表统计函数 ====================

    def _order_period_conditions(self, start_date, end_date, include_statuses):
        """全站/分代理汇总共用的订单期间过滤条件（不含 user 过滤）。"""
        where_conditions = []
        params = []
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            where_conditions.append("o.created_at >= ?")
            params.append(start.strftime("%Y-%m-%d 00:00:00"))
        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            where_conditions.append("o.created_at < ?")
            params.append(end.strftime("%Y-%m-%d 00:00:00"))
        if include_statuses:
            placeholders = ','.join(['?' for _ in include_statuses])
            where_conditions.append(f"o.order_status IN ({placeholders})")
            params.extend(include_statuses)
        return where_conditions, params

    def get_global_order_summary(self, start_date: str = None, end_date: str = None, include_statuses: list = None):
        """全站订单合计（所有登录用户可见的"总经营情况"口径）。

        只返回站级聚合数字，绝不包含任何按用户/账号/买家细分的字段，
        普通代理由此看到大盘却看不到彼此。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                where_conditions, params = self._order_period_conditions(
                    start_date, end_date, include_statuses
                )
                where_clause = (
                    f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
                )
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(DISTINCT o.order_id) AS total_orders,
                        COALESCE(SUM(o.paid_amount_fen), 0) / 100.0 AS total_amount
                    FROM orders AS o
                    {where_clause}
                    """,
                    params,
                )
                row = cursor.fetchone()
                return {
                    'total_orders': int(row[0] or 0),
                    'total_amount': round(float(row[1] or 0), 2),
                }
            except Exception as e:
                logger.error(f"全站订单汇总失败: {e}")
                return {'error': str(e)}

    def get_per_user_order_summary(self, start_date: str = None, end_date: str = None, include_statuses: list = None):
        """按归属用户分组的订单汇总（admin 独享的分代理明细）。

        含全部用户（包括没有订单/没有账号的），便于 admin 看清每个代理现状。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                where_conditions, params = self._order_period_conditions(
                    start_date, end_date, include_statuses
                )
                order_filter = (
                    f"AND {' AND '.join(where_conditions)}" if where_conditions else ""
                )
                cursor.execute(
                    f"""
                    SELECT
                        u.id AS user_id,
                        u.username,
                        u.is_active,
                        COUNT(DISTINCT c.id) AS account_count,
                        COUNT(DISTINCT o.order_id) AS total_orders,
                        COALESCE(SUM(o.paid_amount_fen), 0) / 100.0 AS total_amount
                    FROM users AS u
                    LEFT JOIN cookies AS c ON c.user_id = u.id
                    LEFT JOIN orders AS o
                        ON o.cookie_id = c.id {order_filter}
                    GROUP BY u.id, u.username, u.is_active
                    ORDER BY total_amount DESC, u.id ASC
                    """,
                    params,
                )
                return [
                    {
                        'user_id': int(row[0]),
                        'username': row[1],
                        'is_active': bool(row[2]),
                        'account_count': int(row[3] or 0),
                        'total_orders': int(row[4] or 0),
                        'total_amount': round(float(row[5] or 0), 2),
                    }
                    for row in cursor.fetchall()
                ]
            except Exception as e:
                logger.error(f"分代理订单汇总失败: {e}")
                return {'error': str(e)}

    def get_admin_account_overview(self):
        """admin 账号总览：全部闲鱼账号 + 归属用户 + 登录健康状态（一条 SQL，避免 N+1）。

        只返回运营可见性字段，绝不返回 cookie 值/密码等登录物料。
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    SELECT
                        c.id AS cookie_id,
                        u.id AS user_id,
                        u.username,
                        u.is_active AS user_is_active,
                        c.xianyu_nick,
                        c.remark,
                        c.login_method,
                        c.has_l3_memory,
                        c.last_login_at,
                        c.last_validated_at,
                        c.last_expired_at,
                        COALESCE(cs.enabled, 1) AS enabled,
                        COALESCE(r.state, 'idle') AS refresh_state
                    FROM cookies AS c
                    JOIN users AS u ON u.id = c.user_id
                    LEFT JOIN cookie_status AS cs ON cs.cookie_id = c.id
                    LEFT JOIN account_session_refresh_status AS r ON r.cookie_id = c.id
                    ORDER BY u.username COLLATE NOCASE ASC, c.id ASC
                    """
                )
                accounts = []
                for row in cursor.fetchall():
                    last_login_at = float(row[8]) if row[8] is not None else None
                    last_validated_at = float(row[9]) if row[9] is not None else None
                    last_expired_at = float(row[10]) if row[10] is not None else None
                    # 掉线判定：最近一次过期时间晚于最近一次登录/校验成功时间。
                    freshest_ok = max(
                        value for value in (last_login_at, last_validated_at, 0.0)
                        if value is not None
                    )
                    session_expired = bool(
                        last_expired_at is not None and last_expired_at > freshest_ok
                    )
                    accounts.append({
                        'cookie_id': row[0],
                        'user_id': int(row[1]),
                        'username': row[2],
                        'user_is_active': bool(row[3]),
                        'xianyu_nick': row[4] or '',
                        'remark': row[5] or '',
                        'login_method': row[6] or 'unknown',
                        'has_l3_memory': bool(row[7]),
                        'last_login_at': last_login_at,
                        'last_validated_at': last_validated_at,
                        'last_expired_at': last_expired_at,
                        'enabled': bool(row[11]),
                        'refresh_state': row[12] or 'idle',
                        'session_expired': session_expired,
                    })
                return accounts
            except Exception as e:
                logger.error(f"admin 账号总览查询失败: {e}")
                return {'error': str(e)}

    def get_order_analytics(self, start_date: str = None, end_date: str = None, user_id: int = None, include_statuses: list = None):
        """
        获取订单分析数据

        Args:
            start_date: 开始日期 (格式: YYYY-MM-DD)
            end_date: 结束日期 (格式: YYYY-MM-DD)
            user_id: 用户ID (必填，只统计该用户自己的订单)
            include_statuses: 要包含的订单状态列表 (可选，如果指定则只统计这些状态)

        Returns:
            包含订单分析数据的字典
        """
        # 必须提供 user_id：BI 报表只统计该用户自己的订单，禁止退化为全表扫描
        if user_id is None:
            raise ValueError("get_order_analytics 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()

                # Use timestamp boundaries so SQLite can use the analysis indexes.
                where_conditions = []
                params = []
                from_clause = "orders AS o JOIN cookies AS c ON c.id = o.cookie_id"

                if start_date:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    where_conditions.append("o.created_at >= ?")
                    params.append(start.strftime("%Y-%m-%d 00:00:00"))

                if end_date:
                    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                    where_conditions.append("o.created_at < ?")
                    params.append(end.strftime("%Y-%m-%d 00:00:00"))

                where_conditions.append("c.user_id = ?")
                params.append(user_id)

                # 只包含指定状态（小写形式）
                if include_statuses:
                    placeholders = ','.join(['?' for _ in include_statuses])
                    where_conditions.append(f"o.order_status IN ({placeholders})")
                    params.extend(include_statuses)
                where_clause = f"WHERE {' AND '.join(where_conditions)}"

                # 1. 总收益统计（估值，实际会扣税等）
                cursor.execute(f"""
                    SELECT
                        COUNT(DISTINCT o.order_id) as total_orders,
                        SUM(o.paid_amount_fen) / 100.0 as total_amount,
                        AVG(o.paid_amount_fen) / 100.0 as avg_amount,
                        COUNT(DISTINCT o.buyer_id) as unique_buyers,
                        COUNT(DISTINCT o.item_id) as unique_items,
                        COUNT(DISTINCT CASE WHEN o.paid_amount_fen IS NOT NULL THEN o.order_id END) as with_amount,
                        COUNT(DISTINCT CASE WHEN o.ordered_at_utc IS NOT NULL THEN o.order_id END) as with_time
                    FROM {from_clause}
                    {where_clause}
                """, params)

                row = cursor.fetchone()
                revenue_stats = {
                    'total_orders': row[0] or 0,
                    'total_amount': round(row[1] or 0, 2),
                    'avg_amount': round(row[2] or 0, 2),
                    'unique_buyers': row[3] or 0,
                    'unique_items': row[4] or 0,
                    'orders_with_amount': row[5] or 0,
                    'amount_coverage_rate': round((row[5] or 0) / (row[0] or 1), 4)
                    if row[0] else 0.0,
                } if row else {}
                total_orders = (row[0] or 0) if row else 0
                with_amount = (row[5] or 0) if row else 0
                with_time = (row[6] or 0) if row else 0
                amount_coverage = {
                    'total_orders': total_orders,
                    'with_amount': with_amount,
                    'coverage_rate': round(with_amount / total_orders, 4)
                    if total_orders else 0.0,
                }
                time_coverage = {
                    'total_orders': total_orders,
                    'with_ordered_at': with_time,
                    'coverage_rate': round(with_time / total_orders, 4)
                    if total_orders else 0.0,
                }

                # 2. 按日期统计订单量和收益
                cursor.execute(f"""
                    SELECT
                        DATE(o.created_at) as date,
                        COUNT(DISTINCT o.order_id) as order_count,
                        SUM(o.paid_amount_fen) / 100.0 as daily_amount
                    FROM {from_clause}
                    {where_clause}
                    GROUP BY DATE(o.created_at)
                    ORDER BY date DESC
                    LIMIT 30
                """, params)

                daily_stats = []
                for row in cursor.fetchall():
                    daily_stats.append({
                        'date': row[0],
                        'order_count': row[1],
                        'amount': round(row[2] or 0, 2)
                    })

                # 3. 按状态统计订单
                cursor.execute(f"""
                    SELECT
                        o.order_status,
                        COUNT(DISTINCT o.order_id) as count,
                        SUM(o.paid_amount_fen) / 100.0 as amount
                    FROM {from_clause}
                    {where_clause}
                    GROUP BY o.order_status
                    ORDER BY count DESC
                """, params)

                status_stats = []
                for row in cursor.fetchall():
                    status_stats.append({
                        'status': row[0] or 'unknown',
                        'count': row[1],
                        'amount': round(row[2] or 0, 2)
                    })

                # 4. 按城市统计地区分布（如果有收货城市数据）
                cursor.execute(f"""
                    SELECT
                        o.receiver_city,
                        COUNT(DISTINCT o.order_id) as order_count,
                        SUM(o.paid_amount_fen) / 100.0 as total_amount
                    FROM {from_clause}
                    {where_clause}
                    AND o.receiver_city IS NOT NULL AND o.receiver_city != ''
                    GROUP BY o.receiver_city
                    ORDER BY order_count DESC
                    LIMIT 50
                """, params)

                city_stats = []
                for row in cursor.fetchall():
                    city_stats.append({
                        'city': row[0],
                        'order_count': row[1],
                        'total_amount': round(row[2] or 0, 2)
                    })

                # 5. 商品排行（按订单量）
                cursor.execute(f"""
                    SELECT
                        o.item_id,
                        COUNT(DISTINCT o.order_id) as order_count,
                        SUM(o.paid_amount_fen) / 100.0 as total_amount,
                        AVG(o.paid_amount_fen) / 100.0 as avg_amount
                    FROM {from_clause}
                    {where_clause}
                    AND o.item_id IS NOT NULL AND o.item_id != ''
                    GROUP BY o.item_id
                    ORDER BY order_count DESC
                    LIMIT 20
                """, params)

                item_stats = []
                for row in cursor.fetchall():
                    item_stats.append({
                        'item_id': row[0],
                        'order_count': row[1],
                        'total_amount': round(row[2] or 0, 2),
                        'avg_amount': round(row[3] or 0, 2)
                    })

                # 6. 账号贡献（按闲鱼账号聚合；显示名备注优先，无备注回退账号 ID）
                cursor.execute(f"""
                    SELECT
                        o.cookie_id,
                        MAX(COALESCE(NULLIF(c.remark, ''), o.cookie_id)) as account_name,
                        COUNT(DISTINCT o.order_id) as order_count,
                        SUM(o.paid_amount_fen) / 100.0 as total_amount
                    FROM {from_clause}
                    {where_clause}
                    GROUP BY o.cookie_id
                    ORDER BY total_amount DESC, order_count DESC
                    LIMIT 20
                """, params)

                account_stats = []
                for row in cursor.fetchall():
                    account_stats.append({
                        'cookie_id': row[0],
                        'account_name': row[1],
                        'order_count': row[2],
                        'total_amount': round(row[3] or 0, 2),
                    })

                return {
                    'revenue_stats': revenue_stats,
                    'daily_stats': daily_stats,
                    'status_stats': status_stats,
                    'city_stats': city_stats,
                    'item_stats': item_stats,
                    'account_stats': account_stats,
                    'amount_coverage': amount_coverage,
                    'time_coverage': time_coverage,
                    'metric_source': 'order_transactions',
                }

            except Exception as e:
                logger.error(f"获取订单分析数据失败: {e}")
                return {'error': str(e)}

    def _analytics_where(self, o_alias: str = "o", c_alias: str = "c",
                         start_date: str = None, end_date: str = None,
                         user_id: int = None, include_statuses: list = None):
        """构造分析类查询共用的 WHERE 片段与参数。

        与 get_order_analytics 口径完全一致：
        - 时间边界用 created_at（配合分析索引），左闭右开；
        - 强制 c.user_id 隔离（租户）；
        - 订单量不依赖金额是否可解析；金额仅使用 paid_amount_fen。

        返回 (where_clause_str, params_list)。
        """
        where_conditions = []
        params = []
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            where_conditions.append(f"{o_alias}.created_at >= ?")
            params.append(start.strftime("%Y-%m-%d 00:00:00"))
        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            where_conditions.append(f"{o_alias}.created_at < ?")
            params.append(end.strftime("%Y-%m-%d 00:00:00"))
        where_conditions.append(f"{c_alias}.user_id = ?")
        params.append(user_id)
        if include_statuses:
            placeholders = ','.join(['?' for _ in include_statuses])
            where_conditions.append(f"{o_alias}.order_status IN ({placeholders})")
            params.extend(include_statuses)
        return f"WHERE {' AND '.join(where_conditions)}", params

    def get_traffic_analytics(self, start_date: str = None, end_date: str = None,
                              user_id: int = None, include_statuses: list = None):
        """订单时段分析：按已保存的平台订单时间分桶到东八区小时/星期。

        口径说明（阶段B经营驾驶舱）：
        - 时间边界沿用 created_at（与 get_order_analytics 一致），作覆盖率分母；
        - 只有 ordered_at_utc IS NOT NULL 的订单进入时段分桶，避免把订单
          全堆到同步任务运行时刻而失真；旧订单缺成交时间会被排除，用
          coverage 字段如实回报覆盖率，供前端标注"基于 N% 有订单时间的订单"。
        - ordered_at_utc 存 UTC 秒级 epoch，东八区分桶用 '+8 hours' 偏移。

        Args:
            start_date/end_date: YYYY-MM-DD；user_id 必填（租户隔离）。
            include_statuses: 订单状态白名单（如有效订单三态）。

        Returns:
            {coverage:{total_orders,with_ordered_at,coverage_rate},
             hourly:[{hour:int,order_count,amount}],
             weekday:[{weekday:str '0'-'6' 周日=0,order_count,amount}]}
        """
        if user_id is None:
            raise ValueError("get_traffic_analytics 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                from_clause = "orders AS o JOIN cookies AS c ON c.id = o.cookie_id"
                where_clause, params = self._analytics_where(
                    start_date=start_date, end_date=end_date,
                    user_id=user_id, include_statuses=include_statuses,
                )

                # 覆盖率：分母=窗口内有效订单，分子=其中有成交时间的订单
                cursor.execute(f"""
                    SELECT
                        COUNT(DISTINCT o.order_id) AS total_orders,
                        COUNT(DISTINCT CASE WHEN o.ordered_at_utc IS NOT NULL
                              THEN o.order_id END) AS with_ordered_at,
                        COUNT(DISTINCT CASE WHEN o.paid_amount_fen IS NOT NULL
                              THEN o.order_id END) AS with_amount
                    FROM {from_clause}
                    {where_clause}
                """, params)
                row = cursor.fetchone()
                total_orders = (row[0] or 0) if row else 0
                with_ordered_at = (row[1] or 0) if row else 0
                with_amount = (row[2] or 0) if row else 0
                coverage = {
                    'total_orders': total_orders,
                    'with_ordered_at': with_ordered_at,
                    'coverage_rate': round(with_ordered_at / total_orders, 4)
                    if total_orders else 0.0,
                }
                amount_coverage = {
                    'total_orders': total_orders,
                    'with_amount': with_amount,
                    'coverage_rate': round(with_amount / total_orders, 4)
                    if total_orders else 0.0,
                }

                # 时段分桶只针对有成交时间的订单，按东八区小时/星期聚合
                bucket_where = where_clause + " AND o.ordered_at_utc IS NOT NULL"

                cursor.execute(f"""
                    SELECT
                        CAST(strftime('%H', o.ordered_at_utc, 'unixepoch', '+8 hours') AS INTEGER) AS hour,
                        COUNT(DISTINCT o.order_id) AS order_count,
                        SUM(o.paid_amount_fen) / 100.0 AS amount
                    FROM {from_clause}
                    {bucket_where}
                    GROUP BY hour
                    ORDER BY hour ASC
                """, params)
                hourly = [{
                    'hour': r[0],
                    'order_count': r[1],
                    'amount': round(r[2] or 0, 2),
                } for r in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT
                        strftime('%w', o.ordered_at_utc, 'unixepoch', '+8 hours') AS weekday,
                        COUNT(DISTINCT o.order_id) AS order_count,
                        SUM(o.paid_amount_fen) / 100.0 AS amount
                    FROM {from_clause}
                    {bucket_where}
                    GROUP BY weekday
                    ORDER BY weekday ASC
                """, params)
                weekday = [{
                    'weekday': r[0],
                    'order_count': r[1],
                    'amount': round(r[2] or 0, 2),
                } for r in cursor.fetchall()]

                sufficient_data = (
                    total_orders >= 20
                    and coverage['coverage_rate'] >= 0.8
                )
                best_hour = max(
                    hourly,
                    key=lambda value: (value['order_count'], value['amount']),
                    default=None,
                )
                recommendation = None
                if sufficient_data and best_hour:
                    start_hour = int(best_hour['hour'])
                    recommendation = {
                        'type': 'transaction_timing',
                        'hour': start_hour,
                        'message': (
                            f"可优先在 {start_hour:02d}:00-"
                            f"{(start_hour + 1) % 24:02d}:00 安排擦亮或超级曝光；"
                            "建议依据订单时段，不代表真实曝光流量，也不会自动执行。"
                        ),
                    }
                insufficient_reason = ''
                if total_orders < 20:
                    insufficient_reason = '至少需要 20 笔有效成交订单'
                elif coverage['coverage_rate'] < 0.8:
                    insufficient_reason = '订单时间覆盖率至少需要达到 80%'

                return {
                    'coverage': coverage,
                    'time_coverage': coverage,
                    'amount_coverage': amount_coverage,
                    'metric_source': 'order_transactions',
                    'time_source': 'order_snapshot_ordered_at',
                    'time_semantics': 'platform_order_recorded_at',
                    'hourly': hourly,
                    'weekday': weekday,
                    'sufficient_data': sufficient_data,
                    'data_requirement': {
                        'minimum_orders': 20,
                        'minimum_time_coverage': 0.8,
                    },
                    'insufficient_reason': insufficient_reason,
                    'recommendation': recommendation,
                }

            except Exception as e:
                logger.error(f"获取订单时段分析失败: {e}")
                return {'error': str(e)}

    def get_buyer_behavior_analytics(self, start_date: str = None, end_date: str = None,
                                     user_id: int = None, include_statuses: list = None):
        """买家行为分析：复购、下单频次分布、买家贡献榜。

        边界说明（阶段B经营驾驶舱）：
        - 仅做订单可直接得出的行为量（下单次数、复购、贡献金额）；
          绝不刻画客户类型/年龄/职业/画像标签。
        - 时间边界用 created_at，覆盖旧订单不丢数据（这些指标不依赖成交时刻）。

        Args:
            start_date/end_date: YYYY-MM-DD；user_id 必填（租户隔离）。
            include_statuses: 订单状态白名单。

        Returns:
            {summary:{total_buyers,repeat_buyers,repeat_rate},
             frequency:[{order_count:int,buyer_count:int}],
             top_buyers:[{buyer_id,buyer_nickname,order_count,total_amount}]}
        """
        if user_id is None:
            raise ValueError("get_buyer_behavior_analytics 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()
                from_clause = "orders AS o JOIN cookies AS c ON c.id = o.cookie_id"
                where_clause, params = self._analytics_where(
                    start_date=start_date, end_date=end_date,
                    user_id=user_id, include_statuses=include_statuses,
                )
                buyer_where = where_clause + " AND o.buyer_id IS NOT NULL AND o.buyer_id != ''"

                cursor.execute(f"""
                    SELECT
                        COUNT(DISTINCT o.order_id),
                        COUNT(DISTINCT CASE WHEN o.paid_amount_fen IS NOT NULL
                              THEN o.order_id END)
                    FROM {from_clause}
                    {where_clause}
                """, params)
                coverage_row = cursor.fetchone()
                coverage_total = (coverage_row[0] or 0) if coverage_row else 0
                coverage_amount = (coverage_row[1] or 0) if coverage_row else 0
                amount_coverage = {
                    'total_orders': coverage_total,
                    'with_amount': coverage_amount,
                    'coverage_rate': round(coverage_amount / coverage_total, 4)
                    if coverage_total else 0.0,
                }

                # 每个买家的下单次数与贡献金额（后续复用为频次分布与贡献榜的基底）
                cursor.execute(f"""
                    SELECT
                        o.buyer_id,
                        MAX(o.buyer_nickname) AS buyer_nickname,
                        COUNT(DISTINCT o.order_id) AS order_count,
                        SUM(o.paid_amount_fen) / 100.0 AS total_amount
                    FROM {from_clause}
                    {buyer_where}
                    GROUP BY o.buyer_id
                """, params)
                per_buyer = cursor.fetchall()

                total_buyers = len(per_buyer)
                repeat_buyers = sum(1 for r in per_buyer if (r[2] or 0) >= 2)
                summary = {
                    'total_buyers': total_buyers,
                    'repeat_buyers': repeat_buyers,
                    'repeat_rate': round(repeat_buyers / total_buyers, 4)
                    if total_buyers else 0.0,
                }

                # 频次分布：下 N 单的买家有几个
                freq_map = {}
                for r in per_buyer:
                    n = r[2] or 0
                    freq_map[n] = freq_map.get(n, 0) + 1
                frequency = [{'order_count': n, 'buyer_count': freq_map[n]}
                             for n in sorted(freq_map)]

                # 贡献榜：按金额降序 Top 20
                ranked = sorted(
                    per_buyer, key=lambda r: (r[3] or 0), reverse=True
                )[:20]
                top_buyers = [{
                    'buyer_id': r[0],
                    'buyer_nickname': r[1] or '',
                    'order_count': r[2] or 0,
                    'total_amount': round(r[3] or 0, 2),
                } for r in ranked]

                return {
                    'summary': summary,
                    'frequency': frequency,
                    'top_buyers': top_buyers,
                    'amount_coverage': amount_coverage,
                    'metric_source': 'order_transactions',
                }

            except Exception as e:
                logger.error(f"获取买家行为分析失败: {e}")
                return {'error': str(e)}

    @staticmethod
    def _prepare_item_metric_snapshot(
        *,
        item_id: str,
        observed_at: float,
        source: str,
        exposure_count: Optional[int] = None,
        view_count: Optional[int] = None,
        want_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        item_id = str(item_id or "").strip()
        source = str(source or "").strip()
        if not item_id:
            raise ValueError("商品指标必须提供商品")
        if source not in {"seller_backend_verified", "seller_backend_api"}:
            raise ValueError("商品指标来源未经验证")
        observed_at = float(observed_at)
        if not math.isfinite(observed_at) or observed_at <= 0:
            raise ValueError("商品指标观测时间无效")
        if observed_at > time.time() + ITEM_METRIC_MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("商品指标观测时间超出允许的未来偏差")

        def normalized_count(value: Optional[int]) -> Optional[int]:
            if value is None:
                return None
            if isinstance(value, bool):
                raise ValueError("商品指标计数无效")
            parsed = int(value)
            if parsed < 0 or parsed != value:
                raise ValueError("商品指标计数必须是非负整数")
            return parsed

        counts = {
            "exposure": normalized_count(exposure_count),
            "view": normalized_count(view_count),
            "want": normalized_count(want_count),
        }
        if all(value is None for value in counts.values()):
            raise ValueError("商品指标至少包含一个可信计数")
        return {
            "item_id": item_id,
            "observed_at": observed_at,
            "observed_hour": int(observed_at // 3600),
            "source": source,
            "counts": counts,
        }

    def _record_item_metric_snapshot_locked(
        self,
        cursor: sqlite3.Cursor,
        *,
        user_id: int,
        cookie_id: str,
        prepared: Dict[str, Any],
        owner_verified: bool = False,
    ) -> Dict[str, Any]:
        if not owner_verified:
            owner = cursor.execute(
                "SELECT user_id FROM cookies WHERE id = ?",
                (cookie_id,),
            ).fetchone()
            if not owner or int(owner[0]) != int(user_id):
                raise PermissionError("账号不存在或无权访问")

        item_id = prepared["item_id"]
        observed_hour = prepared["observed_hour"]
        source = prepared["source"]
        existing = cursor.execute(
            "SELECT id, counter_reset, exposure_count, view_count, want_count "
            "FROM item_metric_snapshots "
            "WHERE cookie_id = ? AND item_id = ? AND observed_hour = ? AND source = ?",
            (cookie_id, item_id, observed_hour, source),
        ).fetchone()
        if existing:
            existing_counts = tuple(existing[2:5])
            incoming_counts = tuple(
                prepared["counts"][key] for key in ("exposure", "view", "want")
            )
            if existing_counts != incoming_counts:
                raise ValueError("同一观测时间桶存在冲突的商品指标快照")
            return {
                "inserted": False,
                "snapshot_id": int(existing[0]),
                "counter_reset": bool(existing[1]),
            }

        latest = cursor.execute(
            "SELECT observed_hour FROM item_metric_snapshots "
            "WHERE cookie_id = ? AND item_id = ? AND source = ? "
            "ORDER BY observed_hour DESC LIMIT 1",
            (cookie_id, item_id, source),
        ).fetchone()
        if latest and int(latest[0]) > int(observed_hour):
            raise ValueError("商品指标快照时间早于已保存的最新观测")

        previous = cursor.execute(
            "SELECT exposure_count, view_count, want_count "
            "FROM item_metric_snapshots "
            "WHERE cookie_id = ? AND item_id = ? AND source = ? "
            "AND observed_hour < ? ORDER BY observed_hour DESC LIMIT 1",
            (cookie_id, item_id, source, observed_hour),
        ).fetchone()
        previous_counts = dict(
            zip(("exposure", "view", "want"), previous or (None, None, None))
        )
        counts = prepared["counts"]
        deltas: Dict[str, Optional[int]] = {}
        counter_reset = False
        for key, current in counts.items():
            prior = previous_counts[key]
            if current is None or prior is None:
                deltas[key] = None
            elif current < int(prior):
                deltas[key] = None
                counter_reset = True
            else:
                deltas[key] = current - int(prior)

        cursor.execute(
            """
            INSERT INTO item_metric_snapshots (
                user_id, cookie_id, item_id, observed_hour, observed_at,
                exposure_count, view_count, want_count,
                exposure_delta, view_delta, want_delta,
                counter_reset, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id), cookie_id, item_id, observed_hour,
                prepared["observed_at"], counts["exposure"], counts["view"],
                counts["want"], deltas["exposure"], deltas["view"],
                deltas["want"], int(counter_reset), source,
            ),
        )
        return {
            "inserted": True,
            "snapshot_id": int(cursor.lastrowid),
            "counter_reset": counter_reset,
        }

    def record_item_metric_snapshot(
        self,
        *,
        user_id: int,
        cookie_id: str,
        item_id: str,
        observed_at: float,
        source: str,
        exposure_count: Optional[int] = None,
        view_count: Optional[int] = None,
        want_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Store one verified snapshot, deduplicated by its observation-hour bucket."""
        if user_id is None:
            raise ValueError("record_item_metric_snapshot 必须提供 user_id")
        cookie_id = str(cookie_id or "").strip()
        if not cookie_id:
            raise ValueError("商品指标必须提供账号")
        prepared = self._prepare_item_metric_snapshot(
            item_id=item_id,
            observed_at=observed_at,
            source=source,
            exposure_count=exposure_count,
            view_count=view_count,
            want_count=want_count,
        )

        with self.lock:
            cursor = self.conn.cursor()
            try:
                result = self._record_item_metric_snapshot_locked(
                    cursor,
                    user_id=int(user_id),
                    cookie_id=cookie_id,
                    prepared=prepared,
                )
                self.conn.commit()
                return result
            except Exception:
                self.conn.rollback()
                raise

    def record_item_metric_snapshots(
        self,
        *,
        user_id: int,
        cookie_id: str,
        rows: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Persist one adapter batch atomically; any invalid row rolls it all back."""
        if user_id is None:
            raise ValueError("record_item_metric_snapshots 必须提供 user_id")
        cookie_id = str(cookie_id or "").strip()
        if not cookie_id:
            raise ValueError("商品指标必须提供账号")
        if isinstance(rows, (str, bytes, bytearray)) or not isinstance(
            rows,
            Sequence,
        ):
            raise TypeError("商品指标适配器必须返回有界序列")
        row_count = len(rows)
        if row_count == 0:
            raise ValueError("商品指标适配器没有返回已验证快照")
        if row_count > 200:
            raise ValueError("商品指标单批最多保存 200 行")
        normalized_rows = [rows[index] for index in range(row_count)]
        prepared_rows = [
            self._prepare_item_metric_snapshot(
                item_id=str(row.get("item_id") or ""),
                observed_at=row.get("observed_at"),
                source=str(row.get("source") or ""),
                exposure_count=row.get("exposure_count"),
                view_count=row.get("view_count"),
                want_count=row.get("want_count"),
            )
            for row in normalized_rows
            if isinstance(row, dict)
        ]
        if len(prepared_rows) != len(normalized_rows):
            raise ValueError("商品指标适配器返回了无法解析的数据")

        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                owner = cursor.execute(
                    "SELECT user_id FROM cookies WHERE id = ?",
                    (cookie_id,),
                ).fetchone()
                if not owner or int(owner[0]) != int(user_id):
                    raise PermissionError("账号不存在或无权访问")
                results = [
                    self._record_item_metric_snapshot_locked(
                        cursor,
                        user_id=int(user_id),
                        cookie_id=cookie_id,
                        prepared=prepared,
                        owner_verified=True,
                    )
                    for prepared in prepared_rows
                ]
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return {
            "inserted": sum(int(bool(row["inserted"])) for row in results),
            "duplicates": sum(int(not row["inserted"]) for row in results),
            "counter_resets": sum(int(bool(row["counter_reset"])) for row in results),
            "newest_inserted_observed_at": max(
                (
                    prepared["observed_at"]
                    for prepared, result in zip(prepared_rows, results)
                    if result["inserted"]
                ),
                default=None,
            ),
        }

    def get_item_metric_collection_state(
        self,
        *,
        user_id: int,
        cookie_id: str,
    ) -> Dict[str, Any]:
        cookie_id = str(cookie_id or "").strip()
        with self.lock:
            cursor = self.conn.cursor()
            owner = cursor.execute(
                "SELECT user_id FROM cookies WHERE id = ?",
                (cookie_id,),
            ).fetchone()
            if not owner or int(owner[0]) != int(user_id):
                raise PermissionError("账号不存在或无权访问")
            row = cursor.execute(
                "SELECT canary_success_count, enabled, last_attempt_at, "
                "last_success_at, last_canary_observed_at, last_error_code, updated_at "
                "FROM item_metric_collection_states "
                "WHERE user_id = ? AND cookie_id = ?",
                (int(user_id), cookie_id),
            ).fetchone()
        if not row:
            return {
                "cookie_id": cookie_id,
                "canary_success_count": 0,
                "enabled": False,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_canary_observed_at": None,
                "last_error_code": "",
                "updated_at": None,
            }
        return {
            "cookie_id": cookie_id,
            "canary_success_count": int(row[0] or 0),
            "enabled": bool(row[1]),
            "last_attempt_at": row[2],
            "last_success_at": row[3],
            "last_canary_observed_at": row[4],
            "last_error_code": str(row[5] or ""),
            "updated_at": row[6],
        }

    def record_item_metric_canary_result(
        self,
        *,
        user_id: int,
        cookie_id: str,
        success: bool,
        observed_at: Optional[float] = None,
        error_code: str = "",
    ) -> Dict[str, Any]:
        cookie_id = str(cookie_id or "").strip()
        now = time.time()
        normalized_observed_at: Optional[float] = None
        if success and observed_at is not None:
            normalized_observed_at = float(observed_at)
            if (
                not math.isfinite(normalized_observed_at)
                or normalized_observed_at <= 0
                or normalized_observed_at
                > now + ITEM_METRIC_MAX_FUTURE_SKEW_SECONDS
            ):
                raise ValueError("商品指标金丝雀观测时间无效")
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                owner = cursor.execute(
                    "SELECT user_id FROM cookies WHERE id = ?",
                    (cookie_id,),
                ).fetchone()
                if not owner or int(owner[0]) != int(user_id):
                    raise PermissionError("账号不存在或无权访问")
                row = cursor.execute(
                    "SELECT canary_success_count, last_canary_observed_at "
                    "FROM item_metric_collection_states "
                    "WHERE user_id = ? AND cookie_id = ?",
                    (int(user_id), cookie_id),
                ).fetchone()
                current = int(row[0] or 0) if row else 0
                previous_observed_at = (
                    float(row[1]) if row and row[1] is not None else None
                )
                canary_advanced = bool(
                    success
                    and normalized_observed_at is not None
                    and (
                        previous_observed_at is None
                        or normalized_observed_at > previous_observed_at
                    )
                )
                if not success:
                    updated = 0
                elif canary_advanced:
                    updated = min(current + 1, 3)
                else:
                    updated = current
                enabled = int(success and updated >= 3)
                latest_observed_at = (
                    normalized_observed_at
                    if canary_advanced
                    else previous_observed_at
                )
                cursor.execute(
                    """
                    INSERT INTO item_metric_collection_states (
                        user_id, cookie_id, canary_success_count, enabled,
                        last_attempt_at, last_success_at, last_canary_observed_at,
                        last_error_code, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, cookie_id) DO UPDATE SET
                        canary_success_count = excluded.canary_success_count,
                        enabled = excluded.enabled,
                        last_attempt_at = excluded.last_attempt_at,
                        last_success_at = COALESCE(
                            excluded.last_success_at,
                            item_metric_collection_states.last_success_at
                        ),
                        last_canary_observed_at = COALESCE(
                            excluded.last_canary_observed_at,
                            item_metric_collection_states.last_canary_observed_at
                        ),
                        last_error_code = excluded.last_error_code,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(user_id), cookie_id, updated, enabled, now,
                        now if canary_advanced else None,
                        latest_observed_at,
                        "" if success else str(error_code or "metric_collection_failed"),
                        now,
                    ),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        state = self.get_item_metric_collection_state(
            user_id=int(user_id),
            cookie_id=cookie_id,
        )
        return {**state, "canary_advanced": canary_advanced}

    def has_enabled_item_metric_collection(self) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM item_metric_collection_states AS s "
                "JOIN cookies AS c "
                "ON c.id = s.cookie_id AND c.user_id = s.user_id "
                "WHERE s.enabled = 1 AND s.canary_success_count >= 3 LIMIT 1"
            ).fetchone()
        return bool(row)

    def get_item_traffic_analytics(
        self,
        *,
        user_id: int,
        start_date: str = None,
        end_date: str = None,
        cookie_id: str = None,
        item_id: str = None,
    ) -> Dict[str, Any]:
        """Aggregate verified deltas by the interval between consecutive snapshots."""
        if user_id is None:
            raise ValueError("get_item_traffic_analytics 必须提供 user_id")
        base_conditions = ["c.user_id = ?", "m.user_id = c.user_id"]
        base_params: List[Any] = [int(user_id)]
        if cookie_id:
            base_conditions.append("m.cookie_id = ?")
            base_params.append(str(cookie_id))
        if item_id:
            base_conditions.append("m.item_id = ?")
            base_params.append(str(item_id))
        cst = ZoneInfo("Asia/Shanghai")
        start_timestamp: Optional[float] = None
        end_timestamp: Optional[float] = None
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=cst)
            start_timestamp = start.timestamp()
        if end_date:
            end = (
                datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=cst)
                + timedelta(days=1)
            )
            end_timestamp = end.timestamp()

        scoped_conditions = []
        scoped_params: List[Any] = []
        if start_timestamp is not None:
            scoped_conditions.append("observed_at >= ?")
            scoped_params.append(start_timestamp)
        if end_timestamp is not None:
            scoped_conditions.append("observed_at < ?")
            scoped_params.append(end_timestamp)
        scoped_where = (
            "WHERE " + " AND ".join(scoped_conditions)
            if scoped_conditions
            else ""
        )

        window_conditions = [
            "window_start_at IS NOT NULL",
            "counter_reset = 0",
            "(exposure_delta IS NOT NULL OR view_delta IS NOT NULL "
            "OR want_delta IS NOT NULL)",
        ]
        window_params: List[Any] = []
        if start_timestamp is not None:
            # A delta covers the entire interval since the previous snapshot. Exclude
            # a boundary-crossing interval instead of attributing outside traffic to
            # the selected date range.
            window_conditions.append("window_start_at >= ?")
            window_params.append(start_timestamp)
        if end_timestamp is not None:
            window_conditions.append("observed_at < ?")
            window_params.append(end_timestamp)
        valid_window_where = "WHERE " + " AND ".join(window_conditions)
        base_where = "WHERE " + " AND ".join(base_conditions)
        metric_ctes = f"""
            WITH ordered_metrics AS (
                SELECT
                    m.*,
                    LAG(m.observed_at) OVER (
                        PARTITION BY m.cookie_id, m.item_id, m.source
                        ORDER BY m.observed_at, m.id
                    ) AS window_start_at
                FROM item_metric_snapshots AS m
                JOIN cookies AS c ON c.id = m.cookie_id
                {base_where}
            ),
            scoped_metrics AS (
                SELECT * FROM ordered_metrics
                {scoped_where}
            ),
            valid_windows AS (
                SELECT * FROM ordered_metrics
                {valid_window_where}
            ),
            recommendation_windows AS (
                SELECT * FROM valid_windows
                WHERE observed_at - window_start_at BETWEEN
                    {ITEM_METRIC_RECOMMENDATION_MIN_INTERVAL_SECONDS}
                    AND {ITEM_METRIC_RECOMMENDATION_MAX_INTERVAL_SECONDS}
            )
        """
        query_params = [*base_params, *scoped_params, *window_params]

        with self.lock:
            cursor = self.conn.cursor()
            if cookie_id:
                owner = cursor.execute(
                    "SELECT user_id FROM cookies WHERE id = ?", (cookie_id,)
                ).fetchone()
                if not owner or int(owner[0]) != int(user_id):
                    raise PermissionError("账号不存在或无权访问")
            cursor.execute(f"""{metric_ctes}
                SELECT
                    (SELECT COUNT(*) FROM scoped_metrics),
                    (SELECT COUNT(*) FROM valid_windows),
                    (SELECT COUNT(DISTINCT date(
                        observed_at, 'unixepoch', '+8 hours'
                    )) FROM valid_windows),
                    (SELECT SUM(COALESCE(exposure_delta, 0)) FROM valid_windows),
                    (SELECT SUM(COALESCE(view_delta, 0)) FROM valid_windows),
                    (SELECT SUM(COALESCE(want_delta, 0)) FROM valid_windows),
                    (SELECT SUM(counter_reset) FROM scoped_metrics),
                    (SELECT COUNT(*) FROM recommendation_windows),
                    (SELECT COUNT(DISTINCT date(
                        observed_at, 'unixepoch', '+8 hours'
                    )) FROM recommendation_windows)
            """, query_params)
            row = cursor.fetchone() or (0, 0, 0, 0, 0, 0, 0, 0, 0)
            snapshot_count = int(row[0] or 0)
            valid_snapshot_count = int(row[1] or 0)
            distinct_days = int(row[2] or 0)
            totals = {
                "exposure_delta": int(row[3] or 0),
                "view_delta": int(row[4] or 0),
                "want_delta": int(row[5] or 0),
            }
            reset_count = int(row[6] or 0)
            recommendation_window_count = int(row[7] or 0)
            recommendation_distinct_days = int(row[8] or 0)

            cursor.execute(f"""{metric_ctes}
                SELECT
                    CAST(strftime(
                        '%H', window_start_at, 'unixepoch', '+8 hours'
                    ) AS INTEGER),
                    CAST(strftime(
                        '%H', observed_at, 'unixepoch', '+8 hours'
                    ) AS INTEGER),
                    CAST(
                        julianday(date(observed_at, 'unixepoch', '+8 hours'))
                        - julianday(date(
                            window_start_at, 'unixepoch', '+8 hours'
                        ))
                    AS INTEGER),
                    COUNT(*),
                    AVG(observed_at - window_start_at) / 3600.0,
                    MIN(observed_at - window_start_at) / 3600.0,
                    MAX(observed_at - window_start_at) / 3600.0,
                    SUM(COALESCE(exposure_delta, 0)),
                    SUM(COALESCE(view_delta, 0)),
                    SUM(COALESCE(want_delta, 0))
                FROM recommendation_windows
                GROUP BY 1, 2, 3
                ORDER BY 2, 1, 3
            """, query_params)
            observation_windows = [{
                "start_hour": int(value[0]),
                "end_hour": int(value[1]),
                "day_span": int(value[2] or 0),
                "crosses_midnight": int(value[2] or 0) > 0,
                "window_count": int(value[3] or 0),
                "average_duration_hours": round(float(value[4] or 0), 2),
                "minimum_duration_hours": round(float(value[5] or 0), 2),
                "maximum_duration_hours": round(float(value[6] or 0), 2),
                "exposure_delta": int(value[7] or 0),
                "view_delta": int(value[8] or 0),
                "want_delta": int(value[9] or 0),
            } for value in cursor.fetchall()]

            # Compatibility alias: `hour` is the observation-window end hour,
            # not an hourly traffic bucket. New clients use observation_windows.
            hourly = [{
                "hour": value["end_hour"],
                "window_start_hour": value["start_hour"],
                "window_end_hour": value["end_hour"],
                "day_span": value["day_span"],
                "crosses_midnight": value["crosses_midnight"],
                "window_count": value["window_count"],
                "average_duration_hours": value["average_duration_hours"],
                "exposure_delta": value["exposure_delta"],
                "view_delta": value["view_delta"],
                "want_delta": value["want_delta"],
            } for value in observation_windows]

            cursor.execute(f"""{metric_ctes}
                SELECT item_id, COUNT(*),
                    SUM(COALESCE(exposure_delta, 0)),
                    SUM(COALESCE(view_delta, 0)),
                    SUM(COALESCE(want_delta, 0))
                FROM valid_windows
                GROUP BY item_id
                ORDER BY SUM(COALESCE(view_delta, 0)) DESC, COUNT(*) DESC
                LIMIT 50
            """, query_params)
            items = [{
                "item_id": value[0],
                "snapshot_count": int(value[1] or 0),
                "observation_window_count": int(value[1] or 0),
                "exposure_delta": int(value[2] or 0),
                "view_delta": int(value[3] or 0),
                "want_delta": int(value[4] or 0),
            } for value in cursor.fetchall()]

        sufficient_data = (
            recommendation_distinct_days >= 14
            and recommendation_window_count >= 20
        )
        best_window = max(
            observation_windows,
            key=lambda value: (value["view_delta"], value["exposure_delta"]),
            default=None,
        )
        has_positive_traffic = bool(
            best_window
            and (
                best_window["view_delta"] > 0
                or best_window["exposure_delta"] > 0
            )
        )
        recommendation = None
        if sufficient_data and has_positive_traffic:
            start_hour = int(best_window["start_hour"])
            end_hour = int(best_window["end_hour"])
            end_label = f"次日 {end_hour:02d}:00" if best_window[
                "crosses_midnight"
            ] else f"{end_hour:02d}:00"
            recommendation = {
                "type": "timing",
                "semantics": "observation_window",
                "hour": start_hour,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "crosses_midnight": best_window["crosses_midnight"],
                "average_duration_hours": best_window["average_duration_hours"],
                "precision": "approximate_observation_window",
                "message": (
                    f"流量增量较高的观测窗口约为 {start_hour:02d}:00-{end_label}，"
                    f"平均跨度 {best_window['average_duration_hours']:.1f} 小时。"
                    "增量只能归因到整个观测窗口，不能细分到某一小时；"
                    "可在窗口内分批试验擦亮或超级曝光，系统不会自动执行。"
                ),
            }
        reason = ""
        if not sufficient_data:
            reason = "至少需要 14 天且 20 个接近四小时采样的有效观测窗口"
        elif not has_positive_traffic:
            reason = "样本已达标，但尚无正向流量增量"
        return {
            "metric_source": "seller_backend_verified_snapshots",
            "aggregation_semantics": "counter_delta_between_consecutive_snapshots",
            "time_precision": "observation_window",
            "timezone": "Asia/Shanghai",
            "schedule_interval_hours": 4,
            "snapshot_count": snapshot_count,
            "valid_snapshot_count": valid_snapshot_count,
            "valid_observation_window_count": valid_snapshot_count,
            "recommendation_window_count": recommendation_window_count,
            "recommendation_distinct_days": recommendation_distinct_days,
            "irregular_window_count": max(
                valid_snapshot_count - recommendation_window_count,
                0,
            ),
            "distinct_days": distinct_days,
            "reset_count": reset_count,
            "totals": totals,
            "observation_windows": observation_windows,
            "hourly": hourly,
            "hourly_semantics": "legacy_observation_window_end_hour",
            "items": items,
            "sufficient_data": sufficient_data and has_positive_traffic,
            "data_requirement": {
                "minimum_days": 14,
                "minimum_snapshots": 20,
                "minimum_observation_windows": 20,
                "minimum_window_hours": (
                    ITEM_METRIC_RECOMMENDATION_MIN_INTERVAL_SECONDS / 3600
                ),
                "maximum_window_hours": (
                    ITEM_METRIC_RECOMMENDATION_MAX_INTERVAL_SECONDS / 3600
                ),
            },
            "insufficient_reason": reason,
            "recommendation": recommendation,
        }

    def get_item_performance_analytics(
        self,
        *,
        user_id: int,
        start_date: str = None,
        end_date: str = None,
    ) -> Dict[str, Any]:
        """Order-derived item performance. It intentionally does not claim traffic."""
        if user_id is None:
            raise ValueError("get_item_performance_analytics 必须提供 user_id")
        from_clause = "orders AS o JOIN cookies AS c ON c.id = o.cookie_id"
        where_clause, params = self._analytics_where(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            include_statuses=list(("pending_ship", "shipped", "completed")),
        )
        item_where = where_clause + " AND o.item_id IS NOT NULL AND o.item_id != ''"
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(f"""
                SELECT
                    COUNT(DISTINCT o.order_id),
                    COUNT(DISTINCT CASE WHEN o.paid_amount_fen IS NOT NULL
                        THEN o.order_id END)
                FROM {from_clause}
                {where_clause}
            """, params)
            coverage_row = cursor.fetchone() or (0, 0)
            total_orders = int(coverage_row[0] or 0)
            with_amount = int(coverage_row[1] or 0)
            cursor.execute(f"""
                SELECT
                    o.item_id,
                    MAX(CASE WHEN o.item_title != '' THEN o.item_title ELSE '' END),
                    COUNT(DISTINCT o.order_id),
                    SUM(o.paid_amount_fen) / 100.0,
                    AVG(o.paid_amount_fen) / 100.0,
                    COUNT(DISTINCT CASE WHEN o.paid_amount_fen IS NOT NULL
                        THEN o.order_id END)
                FROM {from_clause}
                {item_where}
                GROUP BY o.item_id
                ORDER BY COUNT(DISTINCT o.order_id) DESC,
                    SUM(COALESCE(o.paid_amount_fen, 0)) DESC
                LIMIT 50
            """, params)
            items = [{
                "item_id": row[0],
                "item_title": row[1] or "",
                "order_count": int(row[2] or 0),
                "total_amount": round(row[3] or 0, 2),
                "avg_amount": round(row[4] or 0, 2),
                "orders_with_amount": int(row[5] or 0),
            } for row in cursor.fetchall()]
        return {
            "metric_source": "order_transactions",
            "amount_coverage": {
                "total_orders": total_orders,
                "with_amount": with_amount,
                "coverage_rate": round(with_amount / total_orders, 4)
                if total_orders else 0.0,
            },
            "items": items,
        }

    def update_order_address(self, order_id: str, receiver_address: str = None, receiver_city: str = None):
        """
        更新订单的收货地址信息

        Args:
            order_id: 订单ID
            receiver_address: 收货地址
            receiver_city: 收货城市

        Returns:
            bool: 更新是否成功
        """
        with self.lock:
            try:
                cursor = self.conn.cursor()

                update_fields = []
                update_values = []

                if receiver_address is not None:
                    update_fields.append("receiver_address = ?")
                    update_values.append(receiver_address)

                if receiver_city is not None:
                    update_fields.append("receiver_city = ?")
                    update_values.append(receiver_city)

                if update_fields:
                    update_fields.append("updated_at = CURRENT_TIMESTAMP")
                    update_values.append(order_id)

                    sql = f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = ?"
                    cursor.execute(sql, update_values)
                    self.conn.commit()

                    return cursor.rowcount > 0

                return False

            except Exception as e:
                logger.error(f"更新订单地址失败: {order_id} - {e}")
                self.conn.rollback()
                return False

    def get_orders_for_analytics(self, start_date: str = None, end_date: str = None,
                                  user_id: int = None, include_statuses: list = None):
        """
        获取用于分析的订单列表

        Args:
            start_date: 开始日期
            end_date: 结束日期
            user_id: 用户ID (必填，只返回该用户自己的订单)
            include_statuses: 要包含的订单状态列表（如果指定则只返回这些状态的订单）

        Returns:
            订单列表
        """
        # 必须提供 user_id：只返回该用户自己的订单，禁止退化为全表扫描
        if user_id is None:
            raise ValueError("get_orders_for_analytics 必须提供 user_id")
        with self.lock:
            try:
                cursor = self.conn.cursor()

                where_conditions = []
                params = []
                from_clause = "orders AS o JOIN cookies AS c ON c.id = o.cookie_id"

                if start_date:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    where_conditions.append("o.created_at >= ?")
                    params.append(start.strftime("%Y-%m-%d 00:00:00"))

                if end_date:
                    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                    where_conditions.append("o.created_at < ?")
                    params.append(end.strftime("%Y-%m-%d 00:00:00"))

                where_conditions.append("c.user_id = ?")
                params.append(user_id)

                # 只包含指定状态
                if include_statuses:
                    placeholders = ','.join(['?' for _ in include_statuses])
                    where_conditions.append(f"o.order_status IN ({placeholders})")
                    params.extend(include_statuses)

                where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

                cursor.execute(f"""
                    SELECT
                        o.order_id,
                        o.item_id,
                        o.buyer_id,
                        o.amount,
                        o.order_status,
                        o.spec_name,
                        o.spec_value,
                        o.quantity,
                        o.created_at,
                        o.receiver_city
                    FROM {from_clause}
                    {where_clause}
                    ORDER BY o.created_at DESC
                    LIMIT 1000
                """, params)

                orders = []
                for row in cursor.fetchall():
                    orders.append({
                        'order_id': row[0],
                        'item_id': row[1],
                        'buyer_id': row[2],
                        'amount': row[3],
                        'order_status': row[4],
                        'spec_name': row[5],
                        'spec_value': row[6],
                        'quantity': row[7],
                        'created_at': row[8],
                        'receiver_city': row[9]
                    })

                return orders

            except Exception as e:
                logger.error(f"获取订单列表失败: {e}")
                return []

    # ==================== 技能中心方法 ====================







































    def get_skill_agent_prompts(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT prompt_type, title, content, enabled, updated_at
                    FROM skill_agent_prompts
                    WHERE user_id = ?
                ''', (user_id,))
                return {
                    row[0]: {
                        'prompt_type': row[0],
                        'title': row[1],
                        'content': row[2],
                        'enabled': bool(row[3]),
                        'updated_at': row[4],
                    }
                    for row in cursor.fetchall()
                }
            except Exception as e:
                logger.error(f"获取技能AI提示词失败: {e}")
                return {}

    def upsert_skill_agent_prompt(self, user_id: int, prompt_type: str, title: str, content: str, enabled: bool = True) -> bool:
        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT INTO skill_agent_prompts (user_id, prompt_type, title, content, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, prompt_type) DO UPDATE SET
                        title = excluded.title,
                        content = excluded.content,
                        enabled = excluded.enabled,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, prompt_type, title, content, 1 if enabled else 0))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"保存技能AI提示词失败: {e}")
                self.conn.rollback()
                return False

    def upsert_skill_agent_prompts_transaction(
        self,
        user_id: int,
        prompts: Dict[str, Dict[str, Any]],
    ) -> bool:
        """Save a complete reply-strategy set atomically.

        The caller validates the allowed prompt types. This method deliberately
        performs every upsert in one SQLite transaction so a partial strategy
        set is never visible to live replies.
        """
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute('BEGIN IMMEDIATE')
                for prompt_type, prompt in prompts.items():
                    cursor.execute('''
                        INSERT INTO skill_agent_prompts (
                            user_id, prompt_type, title, content, enabled, updated_at
                        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(user_id, prompt_type) DO UPDATE SET
                            title = excluded.title,
                            content = excluded.content,
                            enabled = excluded.enabled,
                            updated_at = CURRENT_TIMESTAMP
                    ''', (
                        int(user_id),
                        str(prompt_type),
                        str(prompt.get('title') or prompt_type),
                        str(prompt.get('content') or ''),
                        1 if prompt.get('enabled', True) else 0,
                    ))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"批量保存技能AI提示词失败: {type(e).__name__}")
                self.conn.rollback()
                return False



# 全局单例
db_manager = DBManager()

# 确保进程结束时关闭数据库连接
import atexit
atexit.register(db_manager.close)
