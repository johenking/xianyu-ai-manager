-- 生产等价库固件：schema 状态截至迁移 2026072301（2026-07-26 从生产库只读采集）。
-- 用于迁移测试模拟“生产旧库”路径：不含任何快照列，账本恰为 11 行。
-- 仅保留与订单域相关的表；其余生产表对本迁移无关，省略不影响被测行为。

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    password_hash_v2 TEXT,
    password_hash_version INTEGER NOT NULL DEFAULT 1,
    username_normalized TEXT,
    email_normalized TEXT,
    terms_version TEXT,
    terms_accepted_at REAL
);
CREATE UNIQUE INDEX idx_users_username_normalized ON users(username_normalized);
CREATE UNIQUE INDEX idx_users_email_normalized ON users(email_normalized);

CREATE TABLE cookies (
    id TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    auto_confirm INTEGER DEFAULT 1,
    remark TEXT DEFAULT '',
    pause_duration INTEGER DEFAULT 10,
    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    show_browser INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    xianyu_unb TEXT,
    cookie_refresh_enabled INTEGER DEFAULT 0,
    cookie_refresh_interval_minutes INTEGER DEFAULT 1440,
    password_encrypted TEXT NOT NULL DEFAULT '',
    password_encryption_version INTEGER NOT NULL DEFAULT 0,
    browser_user_agent TEXT NOT NULL DEFAULT '',
    cookie_revision INTEGER NOT NULL DEFAULT 0,
    login_method TEXT NOT NULL DEFAULT 'unknown',
    last_login_at REAL,
    last_validated_at REAL,
    last_expired_at REAL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_cookies_user_unb
    ON cookies(user_id, xianyu_unb)
    WHERE xianyu_unb IS NOT NULL AND xianyu_unb <> '';

CREATE TABLE orders (
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
    receiver_name TEXT DEFAULT '',
    receiver_phone TEXT DEFAULT '',
    receiver_address TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    chat_id TEXT DEFAULT '',
    system_shipped INTEGER DEFAULT 0,
    receiver_city TEXT DEFAULT '',
    platform_status_code TEXT DEFAULT '',
    platform_status_text TEXT DEFAULT '',
    status_source TEXT DEFAULT '',
    status_synced_at TIMESTAMP,
    last_sync_error TEXT DEFAULT '',
    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
);
CREATE INDEX idx_orders_cookie_created_at ON orders(cookie_id, created_at);
CREATE INDEX idx_orders_status_created_at ON orders(order_status, created_at);

CREATE TABLE item_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cookie_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_title TEXT,
    item_description TEXT,
    item_category TEXT,
    item_price TEXT,
    item_detail TEXT,
    is_multi_spec BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    multi_quantity_delivery BOOLEAN DEFAULT FALSE,
    item_image TEXT NOT NULL DEFAULT '',
    platform_item_status INTEGER,
    catalog_active BOOLEAN NOT NULL DEFAULT FALSE,
    catalog_last_seen_at TIMESTAMP,
    catalog_metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE,
    UNIQUE(cookie_id, item_id)
);
CREATE INDEX idx_item_info_catalog_active ON item_info(cookie_id, catalog_active, updated_at DESC);

CREATE TABLE order_status_events (
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
);
CREATE INDEX idx_order_status_events_pending
    ON order_status_events(cookie_id, match_state, occurred_at);

CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_migrations (version, name) VALUES
    ('2026070501', 'security_credentials_v1'),
    ('2026070502', 'runtime_sessions_v1'),
    ('2026071101', 'registration_security_v1'),
    ('2026071102', 'registration_identity_nfkc_v2'),
    ('2026071103', 'direct_registration_v1'),
    ('2026071104', 'order_analysis_indexes_v1'),
    ('2026071701', 'official_session_identity_v1'),
    ('2026071801', 'skill_monitor_durable_workflows_v1'),
    ('2026071802', 'skill_monitor_mtop_offline_v1'),
    ('2026072001', 'item_catalog_state_v1'),
    ('2026072301', 'account_login_metadata_v1');
