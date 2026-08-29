// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { get, post, put } from '../request';
import { getAccountDetails, testAccountProxy, updateAccountProxy } from './accounts';

vi.mock('../request', () => ({
  del: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

describe('getAccountDetails', () => {
  afterEach(() => vi.clearAllMocks());

  it('uses the local avatar placeholder when the backend does not provide an avatar', async () => {
    vi.mocked(get).mockResolvedValue([{
      id: 'account-1',
      value: 'cookie',
      enabled: false,
      auto_confirm: false,
      auto_rate_enabled: true,
      auto_rate_success_count: 2,
      remark: '测试账号',
      pause_duration: 0,
    }]);

    const [account] = await getAccountDetails();

    expect(account.avatar_url).toBeUndefined();
    expect(account.auto_rate_enabled).toBe(true);
    expect(account.auto_rate_success_count).toBe(2);
  });

  it('maps the proxy summary fields without exposing a password', async () => {
    vi.mocked(get).mockResolvedValue([{
      id: 'account-1',
      value: 'cookie',
      enabled: true,
      auto_confirm: false,
      proxy_enabled: true,
      proxy_server: 'http://gw:1000',
      proxy_username: 'u1',
      proxy_password_set: true,
      proxy_region: '上海',
      proxy_last_ip: '1.2.3.4',
      proxy_last_status: 'ok',
      proxy_last_check_at: 1700,
    }]);

    const [account] = await getAccountDetails();

    expect(account.proxy_enabled).toBe(true);
    expect(account.proxy_server).toBe('http://gw:1000');
    expect(account.proxy_password_set).toBe(true);
    expect(account.proxy_last_ip).toBe('1.2.3.4');
    expect(account).not.toHaveProperty('proxy_password');
  });
});

describe('account proxy endpoints', () => {
  afterEach(() => vi.clearAllMocks());

  it('PUTs proxy config to the per-account route', async () => {
    vi.mocked(put).mockResolvedValue({ success: true });
    await updateAccountProxy('acct-1', {
      proxy_enabled: true,
      proxy_server: 'http://gw:1000',
      proxy_password: 'secret',
    });
    expect(put).toHaveBeenCalledWith('/cookies/acct-1/proxy', {
      proxy_enabled: true,
      proxy_server: 'http://gw:1000',
      proxy_password: 'secret',
    });
  });

  it('POSTs to the proxy test route', async () => {
    vi.mocked(post).mockResolvedValue({ success: true, data: { ok: true, ip: '1.2.3.4', status: 'ok', error: '' } });
    const result = await testAccountProxy('acct-1');
    expect(post).toHaveBeenCalledWith('/cookies/acct-1/proxy/test', {});
    expect(result.data.ip).toBe('1.2.3.4');
  });
});
