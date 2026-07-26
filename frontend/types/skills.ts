// Skill Center

// 任务就绪度：后端基于账号身份状态计算，只读不触发真实动作
export interface SkillTaskReadiness {
  configured: boolean;
  runnable: boolean;
  blockers: string[];
}

export interface SkillOperationGate {
  enabled: boolean;
  reason_code: string;
  message: string;
  blockers?: Array<{
    reason_code: string;
    message: string;
  }>;
}

export interface SkillOperationGates {
  manual_run: SkillOperationGate;
  schedule_activation: SkillOperationGate;
  delivery: SkillOperationGate;
  mtop: SkillOperationGate;
}

// 任务最近一次真实运行证据：无运行记录时为 null
export interface SkillTaskRunEvidence {
  status: string;
  trigger_type: string;
  source_adapter: string;
  raw_result_count: number;
  accepted_result_count: number;
  observed_at?: number | null;
}

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
  last_status?: 'idle' | 'running' | 'success' | 'failed';
  last_error?: string | null;
  last_run_at?: string | null;
  created_at?: string;
  updated_at?: string;
  // 后端 GET /api/skills/monitor/tasks 附带字段
  readiness?: SkillTaskReadiness;
  latest_run_evidence?: SkillTaskRunEvidence | null;
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
  notify_status: 'pending' | 'disabled' | 'skipped_no_channel' | 'sent' | 'partial' | 'failed';
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

export type SkillCapabilityBadgeState = 'ready' | 'warning' | 'missing' | 'checking';

export interface SkillCapability {
  available: boolean;
  label: string;
  detail: string;
  // 后端已返回的真实状态字段（此前前端未接收）
  state?: string;
  badge_state?: SkillCapabilityBadgeState;
  observed_at?: string | null;
  evidence?: Record<string, any> | null;
}

export interface SkillCapabilitiesResponse {
  runtime_mode: 'preview' | 'live';
  operation_gates: SkillOperationGates;
  data: Record<string, SkillCapability>;
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
    write_probe_status?: 'ok' | 'failed';
    write_probe_observed_at?: number | null;
    write_probe_error?: string;
  };
  cookie_manager: string;
  accounts: {
    total: number;
    enabled?: number;
    listening: number;
    listener_state: 'running' | 'degraded' | 'not_required' | 'stopped';
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
  status: 'checking' | 'ready' | 'unavailable';
  playwright_importable: boolean;
  playwright_launchable: boolean;
  browser_path?: string;
  active_cookie_tasks: number;
  account_count: number;
  playwright_error?: string;
  observed_at?: number | null;
  stale: boolean;
  checking: boolean;
}

export interface SkillDeliveryDiagnostics {
  cards_total: number;
  delivery_rules_total: number;
  pending_orders_sample: number;
  auto_delivery_ready: boolean;
  recommendations: string[];
}
