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
  schedule_enabled?: boolean;
  schedule_interval_minutes?: number;
  next_run_at?: string | null;
  last_status?: 'idle' | 'running' | 'success' | 'failed' | 'interrupted' | 'action_required';
  last_error?: string | null;
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
  notify_status: 'pending' | 'queued' | 'disabled' | 'skipped_no_channel' | 'sent' | 'sent_unconfirmed' | 'partial' | 'partial_unknown' | 'unknown' | 'failed';
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
  state: string;
  badge_state: 'ready' | 'warning' | 'missing';
  label: string;
  detail: string;
  observed_at?: number | null;
  evidence?: {
    offline_mtop_adapter?: SkillMTopOfflineContract;
    ready_accounts?: number;
    runnable_tasks?: number;
    ready_account_ids?: string[];
    runnable_task_ids?: number[];
    global_enabled?: boolean;
    scheduler_enabled?: boolean;
    delivery_enabled?: boolean;
    mtop_enabled?: boolean;
    operation_gates?: {
      manual_run?: SkillOperationGate;
      schedule_activation?: SkillOperationGate;
      delivery_activation?: SkillOperationGate;
    };
    [key: string]: unknown;
  } | null;
}

export interface SkillOperationGate {
  enabled: boolean;
  reason_code: string;
}

export interface SkillMTopOfflineContract {
  contract_version: string;
  code_present: boolean;
  evidence_scope: 'code_and_configuration_only';
  gate: {
    master_enabled: boolean;
    mtop_enabled: boolean;
    network_allowed: boolean;
    executable: boolean;
    fail_closed: boolean;
  };
  limits: {
    page_size: number;
    max_pages: number;
    max_results: number;
    max_runtime_seconds: number;
    request_timeout_seconds: number;
    max_attempts_per_page: number;
    max_response_bytes: number;
    global_requests_per_window: number;
    account_requests_per_window: number;
    budget_window_seconds: number;
    base_backoff_seconds: number;
    max_backoff_seconds: number;
    failure_threshold: number;
    failure_cooldown_seconds: number;
    probe_lease_seconds: number;
  };
  canary: {
    keyword: string;
    sort: 'latest';
    region: string;
    min_price: number | null;
    max_price: number | null;
    pages: number;
    verification: 'unverified';
  };
  real_acceptance: {
    state: 'blocked';
    blocker_code: 'dedicated_test_account_required';
    shadow_verified: boolean;
    value_verified: boolean;
  };
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
  | 'action_required'
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
  browser_active?: boolean;
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
