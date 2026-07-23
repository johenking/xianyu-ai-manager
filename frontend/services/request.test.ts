// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError, post } from './request';

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
});
