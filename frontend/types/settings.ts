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
  smtp_verified?: boolean;
  support_email?: string;
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
    verified?: boolean;
  }>;
  registration?: {
    enabled: boolean;
    ready: boolean;
    requested: boolean;
    smtp_verified: boolean;
    active_invite_available: boolean;
    terms_version?: string;
  };
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
