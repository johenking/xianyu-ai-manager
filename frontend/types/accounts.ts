// Accounts
export interface AccountDetail {
  id: string;
  value?: string; // cookie value from backend
  cookie?: string; // alias for value
  enabled: boolean;
  auto_confirm: boolean;
  auto_rate_enabled?: boolean;
  auto_rate_enabled_at?: number | null;
  auto_rate_pending_count?: number;
  auto_rate_success_count?: number;
  auto_rate_failed_count?: number;
  auto_rate_needs_reconcile_count?: number;
  remark?: string;
  note?: string; // alias for remark
  pause_duration?: number;
  // 登录信息
  username?: string;
  login_password?: string;
  has_login_password?: boolean;
  login_credentials_valid?: boolean;
  show_browser?: boolean;
  cookie_refresh_enabled?: boolean;
  cookie_refresh_interval_minutes?: number;
  login_method?: 'qr' | 'password' | 'sms_window' | 'chrome_extension' | 'manual_cookie' | 'unknown';
  login_method_label?: string;
  auto_refresh_supported?: boolean;
  reauth_required?: boolean;
  reauth_action?: 'qr_login' | 'password_login' | 'sms_login' | 'chrome_extension_import' | 'manual_cookie' | 'choose_login';
  last_login_at?: number | null;
  last_validated_at?: number | null;
  last_expired_at?: number | null;
  reauth_updated_at?: number | null;
  search_readiness?: {
    ready: boolean;
    state: string;
    blockers: string[];
  };
  // 平台侧账号资料（后端在账号在线后缓存）
  xianyu_nick?: string;
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

// 账号自动回复诊断与会话续期状态（原先误放在已删除的 types/skills.ts）
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
  | 'action_required'
  | 'refreshing'
  | 'verification_required'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'cancelled'
  | 'manual_reauth_required';

export interface AccountSessionRefreshStatus {
  state: AccountSessionRefreshState;
  trigger: string;
  message: string;
  error_code: string;
  verification_image_url: string;
  browser_active?: boolean;
  started_at?: number | null;
  last_attempt_at?: number | null;
  last_success_at?: number | null;
  expires_at?: number | null;
  updated_at?: number | null;
  last_expired_at?: number | null;
}
