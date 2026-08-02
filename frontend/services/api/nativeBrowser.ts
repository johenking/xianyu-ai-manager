export interface NativeBrowserDevice {
  deviceId: string;
  browserFamily: 'chrome' | 'edge';
  clientType: 'native_helper';
  helperVersion: string;
  protocolVersion: number;
  signingPublicJwk: Record<string, unknown>;
  encryptionPublicJwk: Record<string, unknown>;
}

export type NativeBrowserLoginState =
  | 'opening_browser'
  | 'waiting_user'
  | 'validating'
  | 'awaiting_confirmation'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'expired';

export interface NativeBrowserLoginStatus {
  session_id: string;
  device_id: string;
  state: NativeBrowserLoginState;
  message: string;
  error_code?: string;
  account_id?: string;
  expires_at: number;
}

export class NativeBrowserRequestError extends Error {
  readonly code?: string;
  readonly status: number;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'NativeBrowserRequestError';
    this.status = status;
    this.code = code;
  }
}

const getHelperOrigin = () => {
  const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
  const configured = env?.VITE_NATIVE_BROWSER_HELPER_ORIGIN?.trim();
  return (configured || 'http://127.0.0.1:17890').replace(/\/$/, '');
};

const helperRequest = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${getHelperOrigin()}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers || {}),
      },
    });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json() : {};
    if (!response.ok) {
      const error = data?.error || {};
      throw new NativeBrowserRequestError(
        String(error.message || '本机浏览器助手请求失败'),
        response.status,
        error.code ? String(error.code) : undefined,
      );
    }
    return data as T;
  } catch (error) {
    if (error instanceof NativeBrowserRequestError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new NativeBrowserRequestError('本机浏览器助手响应超时', 408, 'helper_timeout');
    }
    throw new NativeBrowserRequestError('未检测到本机浏览器助手，请先启动助手', 0, 'helper_unavailable');
  } finally {
    window.clearTimeout(timer);
  }
};

export const getNativeBrowserDevice = async (): Promise<NativeBrowserDevice> => {
  const response = await helperRequest<{ success: boolean; data: NativeBrowserDevice }>('/v1/device');
  if (!response.data || response.data.clientType !== 'native_helper') {
    throw new NativeBrowserRequestError('本机浏览器助手版本不兼容', 409, 'helper_outdated');
  }
  return response.data;
};

export const startNativeBrowserLogin = async (payload: {
  session_id: string;
  device_id: string;
  mode: 'qr' | 'sms' | 'password';
  server_origin: string;
  expires_at: number;
  official_url?: string;
}): Promise<NativeBrowserLoginStatus> => {
  const response = await helperRequest<{ success: boolean; data: NativeBrowserLoginStatus }>(
    '/v1/login/start',
    { method: 'POST', body: JSON.stringify(payload) },
  );
  return response.data;
};

export const getNativeBrowserLoginStatus = async (
  sessionId: string,
): Promise<NativeBrowserLoginStatus> => {
  const response = await helperRequest<{ success: boolean; data: NativeBrowserLoginStatus }>(
    `/v1/login/status?session_id=${encodeURIComponent(sessionId)}`,
  );
  return response.data;
};

export const cancelNativeBrowserLogin = async (sessionId: string): Promise<NativeBrowserLoginStatus> => {
  const response = await helperRequest<{ success: boolean; data: NativeBrowserLoginStatus }>(
    '/v1/login/cancel',
    { method: 'POST', body: JSON.stringify({ session_id: sessionId }) },
  );
  return response.data;
};

export const closeNativeBrowserLogin = async (
  sessionId: string,
  accountId?: string,
): Promise<NativeBrowserLoginStatus> => {
  const response = await helperRequest<{ success: boolean; data: NativeBrowserLoginStatus }>(
    '/v1/login/close',
    { method: 'POST', body: JSON.stringify({ session_id: sessionId, account_id: accountId || '' }) },
  );
  return response.data;
};
