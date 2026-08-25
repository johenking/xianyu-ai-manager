// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError, get, post } from './request';

describe('request error handling', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('preserves structured authentication errors without exposing request input', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: false,
      code: 'AUTH_RATE_LIMITED',
      message: '操作过于频繁，请稍后重试',
      retry_after: 60,
      request_id: 'request-1234',
    }), {
      status: 429,
      headers: { 'content-type': 'application/json' },
    })));

    const error = await post('/login', {
      identifier: 'pilot@example.com',
      password: 'private-password',
    }).catch((caught) => caught);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({
      message: '操作过于频繁，请稍后重试',
      code: 'AUTH_RATE_LIMITED',
      status: 429,
      retryAfter: 60,
      requestId: 'request-1234',
    });
    expect(String(error)).not.toContain('private-password');
  });

  it('uses nested FastAPI detail messages when available', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { message: '邀请码不可用', code: 'INVITE_INVALID' },
    }), {
      status: 400,
      headers: { 'content-type': 'application/json' },
    })));

    await expect(post('/register', {})).rejects.toMatchObject({
      message: '邀请码不可用',
      code: 'INVITE_INVALID',
      status: 400,
    });
  });

  it('aborts a bounded GET and reports a stable client-timeout error', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
      })
    ));
    vi.stubGlobal('fetch', fetchMock);

    const boundedGet = get as unknown as (
      path: string,
      params?: undefined,
      signal?: AbortSignal,
      timeoutMs?: number,
    ) => Promise<unknown>;
    const pending = boundedGet('/api/orders', undefined, undefined, 5_000);
    const requestSignal = fetchMock.mock.calls[0]?.[1]?.signal;
    expect(requestSignal).toBeInstanceOf(AbortSignal);

    const rejection = expect(pending).rejects.toMatchObject({
      message: '请求超时，请重试',
      code: 'CLIENT_TIMEOUT',
      status: 408,
    });
    await vi.advanceTimersByTimeAsync(5_000);
    await rejection;
  });
});
