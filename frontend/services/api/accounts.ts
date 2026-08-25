import { del, get, post, put } from '../request';
import type {
  AccountDetail,
  AccountSessionRefreshStatus,
  AIReplySettings,
  ApiResponse,
  AutoReplyDiagnostics,
} from '../../types';

// Accounts
export const getAccountDetails = async (): Promise<AccountDetail[]> => {
  const data = await get<any[]>('/cookies/details');
  // Map backend fields to UI fields if necessary
  return data.map(item => ({
    id: item.id,
    value: item.value,
    cookie: item.value,
    enabled: item.enabled,
    auto_confirm: item.auto_confirm,
    auto_rate_enabled: Boolean(item.auto_rate_enabled),
    auto_rate_enabled_at: item.auto_rate_enabled_at ?? null,
    auto_rate_pending_count: Number(item.auto_rate_pending_count || 0),
    auto_rate_success_count: Number(item.auto_rate_success_count || 0),
    auto_rate_failed_count: Number(item.auto_rate_failed_count || 0),
    auto_rate_needs_reconcile_count: Number(item.auto_rate_needs_reconcile_count || 0),
    remark: item.remark,
    note: item.remark,
    pause_duration: item.pause_duration,
    username: item.username,
    login_password: item.login_password,
    has_login_password: item.has_login_password,
    login_credentials_valid: item.login_credentials_valid,
    show_browser: item.show_browser,
    cookie_refresh_enabled: item.cookie_refresh_enabled,
    cookie_refresh_interval_minutes: item.cookie_refresh_interval_minutes,
    login_method: item.login_method,
    login_method_label: item.login_method_label,
    auto_refresh_supported: Boolean(item.auto_refresh_supported),
    has_l3_memory: Boolean(item.has_l3_memory),
    reauth_required: Boolean(item.reauth_required),
    reauth_action: item.reauth_action,
    last_login_at: item.last_login_at ?? null,
    last_validated_at: item.last_validated_at ?? null,
    last_expired_at: item.last_expired_at ?? null,
    reauth_updated_at: item.reauth_updated_at ?? null,
    search_readiness: item.search_readiness || {
      ready: false,
      state: 'error',
      blockers: ['账号身份状态尚未确认'],
    },
    // 备注优先（用户自己起的名），其次平台昵称，最后才退回账号编号
    nickname: item.remark || item.xianyu_nick || `Account ${item.id.substring(0,6)}`,
    avatar_url: item.avatar_url || undefined,
    ai_enabled: false, // 需要从AI设置API获取
  }));
};

export type QRLoginStatus =
  | 'pending'
  | 'waiting'
  | 'scanned'
  | 'success'
  | 'expired'
  | 'cancelled'
  | 'verification_required'
  | 'processing'
  | 'already_processed'
  | 'not_found'
  | 'error';

export interface QRLoginStatusResponse {
  status: QRLoginStatus;
  session_id?: string;
  message?: string;
  error_code?: string;
  verification_kind?: '' | 'mobile_scan' | 'interactive' | 'unknown';
  required_action?: '' | 'render_verification' | 'scan_image' | 'interact_in_console';
  browser_active?: boolean;
  interaction_supported?: boolean;
  frame_revision?: number;
  viewport_width?: number;
  viewport_height?: number;
  ended_by?: string;
  verification_url?: string;
  verification_qr_code_url?: string;
  verification_screenshot_path?: string | null;
  verification_browser_status?: 'starting' | 'waiting' | 'success' | 'failed' | 'timeout' | 'cancelled' | null;
  account_info?: {
    account_id: string;
    is_new_account: boolean;
  };
}

export interface PasswordLoginStatusResponse {
  status:
    | 'processing'
    | 'success'
    | 'failed'
    | 'verification_required'
    | 'timeout'
    | 'cancelled'
    | 'interrupted'
    | 'not_found'
    | 'forbidden'
    | 'error';
  message?: string;
  error?: string;
  error_code?: string;
  account_id?: string;
  is_new_account?: boolean;
  cookie_count?: number;
  screenshot_path?: string | null;
  qr_code_url?: string | null;
}

export type BrowserExtensionPairingStatus =
  | 'waiting'
  | 'received'
  | 'validating'
  | 'success'
  | 'failed'
  | 'expired';

export interface BrowserExtensionPairing {
  pairing_id: string;
  protocol_version?: number;
  pairing_token?: string;
  pairing_code?: string;
  status: BrowserExtensionPairingStatus;
  message: string;
  error_code?: string;
  account_id?: string;
  ended_by?: string;
  expires_at: number;
  import_url?: string;
  console_origin?: string;
  local_import_url?: string;
}

export interface ClientBrowserDevicePublic {
  deviceId: string;
  browserFamily: 'chrome' | 'edge';
  clientType?: 'extension';
  extensionVersion?: string;
  protocolVersion: number;
  signingPublicJwk: Record<string, unknown>;
  encryptionPublicJwk: Record<string, unknown>;
}

export interface ClientBrowserLoginSession {
  session_id: string;
  device_id: string;
  mode: 'qr' | 'sms' | 'password';
  client_type?: 'extension';
  state:
    | 'waiting_device'
    | 'waiting_user'
    | 'validating'
    | 'awaiting_confirmation'
    | 'success'
    | 'failed'
    | 'expired'
    | 'cancelled';
  message: string;
  error_code?: string;
  account_id?: string;
  expires_at: number;
  ended_by?: string;
}

export const registerClientBrowserDevice = async (
  device: ClientBrowserDevicePublic,
): Promise<void> => {
  await post('/api/client-browser/devices', {
    device_id: device.deviceId,
    browser_family: device.browserFamily,
    client_type: device.clientType || 'extension',
    display_name: device.browserFamily === 'edge' ? '当前 Edge' : '当前 Chrome',
    signing_public_jwk: device.signingPublicJwk,
    encryption_public_jwk: device.encryptionPublicJwk,
  });
};

export const createClientBrowserLoginSession = async (
  deviceId: string,
  mode: 'qr' | 'sms' | 'password',
  clientType: 'extension' = 'extension',
): Promise<ClientBrowserLoginSession> => {
  const response = await post<{ success: boolean; data: ClientBrowserLoginSession }>(
    '/api/client-browser/sessions',
    { device_id: deviceId, mode, client_type: clientType },
  );
  return response.data;
};

export const getClientBrowserLoginSession = async (
  sessionId: string,
): Promise<ClientBrowserLoginSession> => {
  const response = await get<{ success: boolean; data: ClientBrowserLoginSession }>(
    '/api/client-browser/sessions/' + encodeURIComponent(sessionId),
  );
  return response.data;
};

export const confirmClientBrowserLoginSession = async (
  sessionId: string,
  accountId: string,
): Promise<ClientBrowserLoginSession> => {
  const response = await post<{ success: boolean; data: ClientBrowserLoginSession }>(
    '/api/client-browser/sessions/' + encodeURIComponent(sessionId) + '/confirm',
    { account_id: accountId },
  );
  return response.data;
};

export const cancelClientBrowserLoginSession = async (sessionId: string): Promise<void> => {
  await post('/api/client-browser/sessions/' + encodeURIComponent(sessionId) + '/cancel', {});
};

export const bindAccountRenewalDevice = async (
  accountId: string,
  data: {
    login_session_id: string;
    device_id: string;
    username: string;
    password: string;
    authorized: true;
    authorized_at: number;
  },
): Promise<void> => {
  await post('/api/accounts/' + encodeURIComponent(accountId) + '/renewal-binding', data);
};

export type OfficialLoginState =
  | 'preparing'
  | 'waiting_user'
  | 'verification_required'
  | 'persisting'
  | 'restarting_listener'
  | 'success'
  | 'expired'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

export interface OfficialLoginSessionResponse {
  success?: boolean;
  session_id: string;
  mode?: 'qr' | 'password' | 'sms';
  state: OfficialLoginState;
  message: string;
  error_code: string;
  verification_kind?: '' | 'mobile_scan' | 'interactive' | 'unknown';
  required_action?: '' | 'render_verification' | 'scan_image' | 'interact_in_console';
  browser_active?: boolean;
  interaction_supported?: boolean;
  frame_revision?: number;
  viewport_width?: number;
  viewport_height?: number;
  ended_by?: string;
  qr_image_url?: string;
  verification_image_url?: string;
  account_id?: string;
  is_new_account?: boolean;
  created_at?: number;
  updated_at?: number;
  expires_at?: number;
}

export type BrowserInteractionAction =
  | {
    kind: 'gesture';
    frame_revision: number;
    points: Array<{ x: number; y: number }>;
    duration_ms: number;
  }
  | {
    kind: 'text';
    frame_revision: number;
    text: string;
  }
  | {
    kind: 'key';
    frame_revision: number;
    key: 'Enter' | 'Backspace' | 'Tab' | 'Escape';
  }
  | {
    kind: 'wheel';
    frame_revision: number;
    delta_x: number;
    delta_y: number;
  };

export const createOfficialLoginSession = async (data: {
  mode: 'qr' | 'password' | 'sms';
  account?: string;
  password?: string;
  show_browser?: boolean;
}): Promise<OfficialLoginSessionResponse> => {
  return post('/api/official-login/sessions', data);
};

export const getOfficialLoginSession = async (sessionId: string): Promise<OfficialLoginSessionResponse> => {
  return get(`/api/official-login/sessions/${sessionId}`);
};

export const showOfficialLoginBrowser = async (sessionId: string): Promise<ApiResponse> => {
  return post(`/api/official-login/sessions/${sessionId}/show-browser`, {});
};

export const cancelOfficialLoginSession = async (sessionId: string): Promise<ApiResponse> => {
  return post(`/api/official-login/sessions/${sessionId}/cancel`, {});
};

export const interactWithOfficialLogin = async (
  sessionId: string,
  action: BrowserInteractionAction,
): Promise<{ success: boolean; accepted: boolean; frame_revision: number }> => {
  return post(`/api/official-login/sessions/${sessionId}/interact`, action);
};

export const addAccountCookie = async (data: { id?: string; value: string }): Promise<ApiResponse & { account_id?: string }> => {
  return post('/cookies', data);
};

export const generateQRLogin = async (): Promise<{ success: boolean; session_id?: string; qr_code_url?: string; message?: string; error_code?: string; retryable?: boolean }> => {
  return post('/qr-login/generate');
};

export const checkQRLoginStatus = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return get(`/qr-login/check/${sessionId}`);
};

export const continueQRLoginAfterVerification = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return post(`/qr-login/continue/${sessionId}`, {});
};

export const cancelQRLogin = async (
  sessionId: string,
  endedBy: 'user_cancelled' | 'switched_method' | 'switched_to_extension' = 'user_cancelled',
): Promise<QRLoginStatusResponse> => {
  return post(`/qr-login/cancel/${sessionId}`, { ended_by: endedBy });
};

export const interactWithQRLogin = async (
  sessionId: string,
  action: BrowserInteractionAction,
): Promise<{ success: boolean; accepted: boolean; frame_revision: number }> => {
  return post(`/qr-login/interact/${sessionId}`, action);
};

export const createBrowserExtensionPairing = async (): Promise<BrowserExtensionPairing> => {
  const response = await post<{ success: boolean; data: BrowserExtensionPairing }>('/api/browser-extension/pairings', {});
  return response.data;
};

export const getBrowserExtensionPairing = async (pairingId: string): Promise<BrowserExtensionPairing> => {
  const response = await get<{ success: boolean; data: BrowserExtensionPairing }>(`/api/browser-extension/pairings/${pairingId}`);
  return response.data;
};

export const passwordLogin = async (data: {
  account: string;
  password: string;
  show_browser?: boolean;
}): Promise<{ success: boolean; session_id?: string; status?: string; message?: string }> => {
  return post('/password-login', data);
};

export const checkPasswordLoginStatus = async (sessionId: string): Promise<PasswordLoginStatusResponse> => {
  return get(`/password-login/check/${sessionId}`);
};

export const updateAccountStatus = async (id: string, enabled: boolean): Promise<any> => {
  return put(`/cookies/${id}/status`, { enabled });
};

export const deleteAccount = async (id: string): Promise<any> => {
  return del(`/cookies/${id}`);
};

export const updateAccountRemark = async (id: string, remark: string): Promise<any> => {
  return put(`/cookies/${id}/remark`, { remark });
};

export const updateAccountAutoConfirm = async (id: string, autoConfirm: boolean): Promise<any> => {
  return put(`/cookies/${id}/auto-confirm`, { auto_confirm: autoConfirm });
};

export const updateAccountAutoRate = async (id: string, enabled: boolean): Promise<any> => {
  return put(`/cookies/${id}/auto-rate`, { auto_rate_enabled: enabled });
};

export const updateAccountPauseDuration = async (id: string, pauseDuration: number): Promise<any> => {
  return put(`/cookies/${id}/pause-duration`, { pause_duration: pauseDuration });
};

export const updateAccountCookie = async (id: string, value: string): Promise<any> => {
  return put(`/cookies/${id}`, { id, value });
};

export const updateAccountLoginInfo = async (id: string, data: {
  username?: string;
  login_password?: string;
  show_browser?: boolean;
}): Promise<any> => {
  return put(`/cookies/${id}/login-info`, data);
};

export const updateAccountCookieRefreshSettings = async (id: string, data: {
  cookie_refresh_enabled: boolean;
  cookie_refresh_interval_minutes: number;
}): Promise<any> => {
  return put(`/cookies/${id}/cookie-refresh-settings`, data);
};

export const getAllAISettings = async (): Promise<Record<string, AIReplySettings>> => {
  return get('/ai-reply-settings');
};

export const getAutoReplyDiagnostics = async (cookieId: string): Promise<AutoReplyDiagnostics> => {
  const res = await get<{ success: boolean; data: AutoReplyDiagnostics }>(`/api/diagnostics/auto-reply/${cookieId}`);
  return res.data;
};

export const getAccountSessionStatus = async (
  cookieId: string,
  signal?: AbortSignal,
): Promise<AccountSessionRefreshStatus> => {
  const res = await get<{ success: boolean; data: AccountSessionRefreshStatus }>(
    `/api/accounts/${cookieId}/session-status`,
    undefined,
    signal,
    12_000,
  );
  return res.data;
};

export const refreshAccountSession = async (cookieId: string): Promise<{ success: boolean; message: string; data: AccountSessionRefreshStatus }> => {
  return post(`/api/accounts/${cookieId}/session-refresh`, {});
};

export const cancelAccountSessionRefresh = async (cookieId: string): Promise<ApiResponse> => {
  return post(`/api/accounts/${cookieId}/session-refresh/cancel`, {});
};

export const showAccountSessionRefreshBrowser = async (cookieId: string): Promise<ApiResponse> => {
  return post(`/api/accounts/${cookieId}/session-refresh/show-browser`, {});
};
