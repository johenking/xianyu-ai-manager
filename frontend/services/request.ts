type QueryParams = Record<string, string | number | boolean | null | undefined>;

type ErrorPayload = Record<string, unknown>;

export class ApiRequestError extends Error {
  readonly code?: string;
  readonly status: number;
  readonly retryAfter?: number;
  readonly requestId?: string;

  constructor(message: string, options: {
    code?: string;
    status: number;
    retryAfter?: number;
    requestId?: string;
  }) {
    super(message);
    this.name = 'ApiRequestError';
    this.code = options.code;
    this.status = options.status;
    this.retryAfter = options.retryAfter;
    this.requestId = options.requestId;
  }
}

const asPayload = (value: unknown): ErrorPayload | null => (
  typeof value === 'object' && value !== null ? value as ErrorPayload : null
);

const asOptionalString = (value: unknown): string | undefined => (
  typeof value === 'string' && value.trim() ? value.trim() : undefined
);

const parseRequestError = (data: unknown, status: number): ApiRequestError => {
  const payload = asPayload(data);
  const detail = payload?.detail;
  const detailPayload = asPayload(detail);
  const message = asOptionalString(detailPayload?.message)
    || asOptionalString(payload?.message)
    || asOptionalString(detail)
    || `Request failed with status ${status}`;
  const retryValue = detailPayload?.retry_after ?? payload?.retry_after;
  const retryAfter = typeof retryValue === 'number' && Number.isFinite(retryValue)
    ? retryValue
    : undefined;

  return new ApiRequestError(message, {
    code: asOptionalString(detailPayload?.code) || asOptionalString(payload?.code),
    status,
    retryAfter,
    requestId: asOptionalString(payload?.request_id),
  });
};

const buildUrl = (path: string, params?: QueryParams) => {
  const search = new URLSearchParams();

  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        search.set(key, String(value));
      }
    });
  }

  const query = search.toString();
  return query ? `${path}?${query}` : path;
};

const request = async <T>(
  method: string,
  path: string,
  body?: unknown,
  params?: QueryParams
): Promise<T> => {
  const token = localStorage.getItem('auth_token');
  const isFormData = body instanceof FormData;
  const headers: HeadersInit = {
    Accept: 'application/json',
  };

  if (!isFormData && body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(buildUrl(path, params), {
    method,
    headers,
    body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
  });

  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('auth_token');
      window.dispatchEvent(new Event('auth:logout'));
    }

    throw parseRequestError(data, response.status);
  }

  return data as T;
};

export const get = <T>(path: string, params?: QueryParams) =>
  request<T>('GET', path, undefined, params);

export const post = <T>(path: string, body?: unknown) =>
  request<T>('POST', path, body);

export const put = <T>(path: string, body?: unknown) =>
  request<T>('PUT', path, body);

export const patch = <T>(path: string, body?: unknown) =>
  request<T>('PATCH', path, body);

export const del = <T>(path: string) =>
  request<T>('DELETE', path);
