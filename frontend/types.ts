
// API Response Bases
export interface ApiResponse {
  success?: boolean;
  message?: string;
  msg?: string;
  reply?: string;
}

export interface PaginatedResponse<T> {
  success: boolean;
  data: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// Auth
export interface LoginResponse {
  success: boolean;
  token?: string;
  message?: string;
  user_id?: number;
  username?: string;
  is_admin?: boolean;
}

// Accounts
export interface AccountDetail {
  id: string;
  value?: string; // cookie value from backend
  cookie?: string; // alias for value
  enabled: boolean;
  auto_confirm: boolean;
  remark?: string;
  note?: string; // alias for remark
  pause_duration?: number;
  // 登录信息
  username?: string;
  login_password?: string;
  has_login_password?: boolean;
  login_credentials_valid?: boolean;
  show_browser?: boolean;
  // Frontend helpers
  nickname?: string;
  avatar_url?: string;
  // AI设置
  ai_enabled?: boolean;
  max_discount_percent?: number;
  max_discount_amount?: number;
  max_bargain_rounds?: number;
  custom_prompts?: string;
}

// Orders
export type OrderStatus =
  | 'unknown'
  | 'processing'
  | 'pending_ship'
  | 'shipped'
  | 'completed'
  | 'cancelled'
  | 'refunding'
  | 'refunded'
  | 'refund_cancelled';

export interface Order {
  id: string;
  order_id: string;
  cookie_id: string;
  item_id: string;
  item_title?: string;
  item_image?: string;
  item_price?: string;
  buyer_id: string;
  quantity: number;
  amount: string;
  status: OrderStatus;
  receiver_name?: string;
  receiver_phone?: string;
  receiver_address?: string;
  receiver_city?: string;
  platform_status_code?: string;
  platform_status_text?: string;
  status_source?: string;
  status_synced_at?: string;
  last_sync_error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface OrderSyncSummary {
  total_seen: number;
  discovered: number;
  status_updated: number;
  details_updated: number;
  unchanged: number;
  failed: number;
}

export interface OrderSyncResponse {
  success: boolean;
  partial?: boolean;
  message: string;
  days: number;
  summary: OrderSyncSummary;
  requires_login: string[];
  accounts: Array<{ cookie_id: string; success: boolean; message?: string }>;
}

// Cards
export interface Card {
  id: number;
  name: string;
  type: 'api' | 'text' | 'data' | 'image';
  description?: string;
  enabled: boolean;
  // 文本类型
  text_content?: string;
  // 批量数据类型
  data_content?: string;
  // API 类型配置
  api_config?: {
    url: string;
    method: 'GET' | 'POST';
    timeout?: number;
    headers?: string;
    params?: string;
  };
  // 图片类型
  image_url?: string;
  // 通用配置
  delay_seconds?: number;
  // 多规格配置
  is_multi_spec?: boolean;
  spec_name?: string;
  spec_value?: string;
  created_at: string;
  updated_at: string;
}

// Items
export interface Item {
  id: string | number;
  cookie_id: string;
  item_id: string;
  item_title?: string;
  item_description?: string;
  item_price?: string;
  item_image?: string; // Inferred from common usage, though not explicitly in list model sometimes
  item_category?: string;
  item_detail?: string;
  is_multi_spec?: number | boolean;
  multi_quantity_delivery?: number | boolean;
  created_at?: string;
  updated_at?: string;
}

// Rules
export interface ShippingRule {
  id: string;
  name: string;
  item_keyword: string; // Matches item title
  card_group_id: number; // ID from Card list
  card_group_name?: string; // UI helper
  priority: number;
  enabled: boolean;
}

export interface ReplyRule {
  id: string;
  keyword: string;
  reply_content: string;
  match_type: 'exact' | 'fuzzy';
  enabled: boolean;
}

// Stats
export interface AdminStats {
  total_users: number;
  total_cookies: number;
  active_cookies: number;
  total_cards: number;
  total_keywords: number;
  total_orders: number;
}

export interface OrderAnalytics {
  revenue_stats: {
    total_amount: number;
    total_orders: number;
  };
  daily_stats: Array<{ date: string; amount: number }>;
  item_stats?: Array<{
    item_id: string;
    order_count: number;
    total_amount: number;
    avg_amount: number;
  }>;
}

// Settings
export interface SystemSettings {
  ai_model?: string;
  ai_api_url?: string;
  ai_api_key?: string;
  ai_api_key_configured?: boolean;
  ai_api_key_masked?: string;
  default_reply?: string;
  registration_enabled?: boolean;
  show_default_login_info?: boolean;
  login_captcha_enabled?: boolean;
  item_sync_enabled?: boolean;
  item_sync_interval?: number;
  item_sync_max_pages?: number;
  smtp_server?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_password?: string;
  smtp_password_configured?: boolean;
  smtp_password_masked?: string;
  smtp_from?: string;
  smtp_use_tls?: boolean;
  smtp_use_ssl?: boolean;
  [key: string]: any;
}

export type SettingsSectionKey = 'basic' | 'ai' | 'smtp';

export interface SettingsSummary {
  settings: SystemSettings;
  sections: Record<SettingsSectionKey, {
    state: string;
    label: string;
    configured: boolean;
    model?: string;
  }>;
  runtime: {
    cookie_manager: boolean;
    account_count: number;
    active_tasks: number;
  };
}

export interface AIReplySettings {
  ai_enabled: boolean;
  provider_profile_id?: number | null;
  provider_name?: string;
  provider_type?: 'openai_compatible' | 'gemini';
  provider_status?: 'unverified' | 'verified' | 'failed';
  model_name: string;
  api_key: string;
  base_url: string;
  api_key_source?: 'provider' | 'account' | 'global' | 'missing';
  api_key_masked?: string;
  has_effective_api_key?: boolean;
  max_discount_percent: number;
  max_discount_amount?: number;
  max_bargain_rounds: number;
  custom_prompts: string;
  api_key_action?: 'keep' | 'set' | 'clear';
  provider_test_token?: string;
}

export interface AIProviderPreset {
  label: string;
  provider_type: 'openai_compatible' | 'gemini';
  base_url: string;
  default_model: string;
}

export interface AIProviderProfile {
  id: number;
  name: string;
  provider_type: 'openai_compatible' | 'gemini';
  preset: string;
  base_url: string;
  default_model: string;
  models: string[];
  models_cached_at?: number | null;
  models_cache_fresh?: boolean;
  verification_status: 'unverified' | 'verified' | 'failed';
  verification_message?: string;
  last_verified_at?: number | null;
  is_default: boolean;
  api_key_configured: boolean;
  api_key_masked: string;
}

export interface AIProviderListResponse {
  providers: AIProviderProfile[];
  presets: Record<string, AIProviderPreset>;
}

// Default Reply
export interface DefaultReply {
  cookie_id: string;
  enabled: boolean;
  reply_content: string;
  reply_once: boolean;
  reply_image_url?: string;
}

// Skill Center
export interface SkillMonitorTask {
  id: number;
  user_id?: number;
  name: string;
  keyword: string;
  min_price?: number | null;
  max_price?: number | null;
  region?: string;
  published_within_hours: number;
  ai_filter?: string;
  notify_enabled: boolean;
  account_id?: string;
  enabled: boolean;
  last_run_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface SkillMonitorResult {
  id: number;
  task_id: number;
  title: string;
  price?: number | null;
  region?: string;
  item_url?: string;
  item_image?: string;
  seller_name?: string;
  ai_score: number;
  ai_reason?: string;
  notify_status: string;
  raw_data?: {
    source?: string;
    is_real_data?: boolean;
    error?: string;
    filter_reason?: string;
    published_within_hours?: number;
    publish_time?: string;
    [key: string]: any;
  };
  created_at?: string;
}

export interface SkillCapability {
  available: boolean;
  label: string;
  detail: string;
}

export interface AutoReplyDiagnostics {
  cookie_id: string;
  ready: boolean;
  issues: string[];
  diagnosed_at?: number;
  account: {
    enabled: boolean;
    cookie_length: number;
    has_login_username: boolean;
    has_login_password: boolean;
    login_credentials_valid?: boolean;
    show_browser: boolean;
  };
  runtime: {
    manager_ready: boolean;
    manager_has_cookie: boolean;
    task_running: boolean;
    task_done: boolean;
    task_error?: string;
    recent_runtime_error?: string;
    task_status?: {
      running?: boolean;
      last_start_time?: number;
      last_end_time?: number | null;
      last_error?: string;
      last_exit_reason?: string;
      [key: string]: any;
    };
    latest_risk_control?: {
      event_type?: string;
      event_description?: string;
      processing_result?: string;
      processing_status?: string;
      error_message?: string;
      created_at?: string;
      updated_at?: string;
    } | null;
  };
  session: AccountSessionRefreshStatus;
  reply: {
    keyword_count: number;
    default_reply_count: number;
    default_reply_enabled: boolean;
    ai_enabled: boolean;
    ai_model?: string;
    ai_base_url?: string;
    has_ai_key: boolean;
    conversation_count: number;
    recent_conversations: Array<{ role: string; content: string; created_at: string }>;
  };
}

export type AccountSessionRefreshState =
  | 'idle'
  | 'refreshing'
  | 'verification_required'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'cancelled';

export interface AccountSessionRefreshStatus {
  state: AccountSessionRefreshState;
  trigger: string;
  message: string;
  error_code: string;
  verification_image_url: string;
  started_at?: number | null;
  last_attempt_at?: number | null;
  last_success_at?: number | null;
  expires_at?: number | null;
  updated_at?: number | null;
}

export interface SkillAgentPrompt {
  prompt_type: 'classify' | 'price' | 'tech' | 'default';
  title: string;
  content: string;
  enabled: boolean;
  updated_at?: string;
}

export interface SkillOpsHealth {
  api: string;
  database: {
    path: string;
    exists: boolean;
    writable: boolean;
  };
  cookie_manager: string;
  accounts: {
    total: number;
    listening: number;
    listener_state: 'running' | 'stopped';
  };
  ai: {
    global_configured: boolean;
    enabled_accounts: number;
    ready_accounts: number;
    model: string;
  };
  skills: {
    monitor_tasks: number;
    monitor_results: number;
    logs: number;
  };
  recent_logs: Array<{
    id: number;
    module: string;
    level: string;
    message: string;
    created_at: string;
  }>;
}

export interface SkillBrowserStatus {
  playwright_importable: boolean;
  playwright_launchable: boolean;
  browser_path?: string;
  active_cookie_tasks: number;
  account_count: number;
  playwright_error?: string;
}

export interface SkillDeliveryDiagnostics {
  cards_total: number;
  delivery_rules_total: number;
  pending_orders_sample: number;
  auto_delivery_ready: boolean;
  recommendations: string[];
}
