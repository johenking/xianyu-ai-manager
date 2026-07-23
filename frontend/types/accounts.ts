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
