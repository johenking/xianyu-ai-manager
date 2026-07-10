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
    nickname: item.remark || `Account ${item.id.substring(0,6)}`, // Fallback for UI
    avatar_url: `https://api.dicebear.com/7.x/avataaars/svg?seed=${item.id}`, // Placeholder avatar
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

export const addAccountCookie = async (data: { id: string; value: string }): Promise<ApiResponse> => {
  return post('/cookies', data);
};

export const generateQRLogin = async (): Promise<{ success: boolean; session_id?: string; qr_code_url?: string; message?: string }> => {
  return post('/qr-login/generate');
};

export const checkQRLoginStatus = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return get(`/qr-login/check/${sessionId}`);
};

export const continueQRLoginAfterVerification = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return post(`/qr-login/continue/${sessionId}`, {});
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

export const getAccountSessionStatus = async (cookieId: string): Promise<AccountSessionRefreshStatus> => {
  const res = await get<{ success: boolean; data: AccountSessionRefreshStatus }>(`/api/accounts/${cookieId}/session-status`);
  return res.data;
};

export const refreshAccountSession = async (cookieId: string): Promise<{ success: boolean; message: string; data: AccountSessionRefreshStatus }> => {
  return post(`/api/accounts/${cookieId}/session-refresh`, {});
};

export const cancelAccountSessionRefresh = async (cookieId: string): Promise<ApiResponse> => {
  return post(`/api/accounts/${cookieId}/session-refresh/cancel`, {});
};
