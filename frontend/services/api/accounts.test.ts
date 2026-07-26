// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from '../request';
import { getAccountDetails } from './accounts';

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
      remark: '测试账号',
      pause_duration: 0,
    }]);

    const [account] = await getAccountDetails();

    expect(account.avatar_url).toBeUndefined();
  });
});
