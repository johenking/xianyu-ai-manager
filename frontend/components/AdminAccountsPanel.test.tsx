// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getAdminAccountsOverview } from '../services/api';
import AdminAccountsPanel from './AdminAccountsPanel';

vi.mock('../services/api', () => ({
  getAdminAccountsOverview: vi.fn(),
}));

const baseAccount = {
  remark: '',
  login_method: 'qr_code',
  login_method_label: '扫码登录',
  has_l3_memory: false,
  last_login_at: null,
  last_expired_at: null,
  refresh_state: 'idle',
};

describe('AdminAccountsPanel', () => {
  beforeEach(() => {
    vi.mocked(getAdminAccountsOverview).mockResolvedValue({
      success: true,
      total: 3,
      expired_count: 1,
      accounts: [
        {
          ...baseAccount,
          cookie_id: 'acc-expired',
          user_id: 2,
          username: 'agent-one',
          user_is_active: true,
          xianyu_nick: '鱼铺一号',
          last_validated_at: 1_690_000_000,
          last_expired_at: 1_690_100_000,
          enabled: true,
          refresh_state: 'manual_reauth_required',
          session_expired: true,
        },
        {
          ...baseAccount,
          cookie_id: 'acc-healthy',
          user_id: 3,
          username: 'agent-two',
          user_is_active: true,
          xianyu_nick: '鱼铺二号',
          last_validated_at: 1_690_200_000,
          enabled: true,
          session_expired: false,
        },
        {
          ...baseAccount,
          cookie_id: 'acc-paused',
          user_id: 3,
          username: 'agent-two',
          user_is_active: false,
          xianyu_nick: '',
          last_validated_at: null,
          enabled: false,
          session_expired: false,
        },
      ],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('lists every account with owner, health badge, and expired counter', async () => {
    render(<AdminAccountsPanel />);

    expect(await screen.findByText('鱼铺一号')).toBeInTheDocument();
    expect(screen.getByText('已掉线')).toBeInTheDocument();
    expect(screen.getByText('鱼铺二号')).toBeInTheDocument();
    expect(screen.getByText('在线')).toBeInTheDocument();
    // 没有昵称的账号回退展示 cookie_id；停用用户带徽章
    expect(screen.getAllByText('acc-paused').length).toBeGreaterThan(0);
    expect(screen.getByText('用户已停用')).toBeInTheDocument();
    expect(screen.getByText('已暂停')).toBeInTheDocument();
    expect(screen.getByText('agent-one')).toBeInTheDocument();
    expect(screen.getAllByText('agent-two')).toHaveLength(2);
    // 掉线计数（徽章"已掉线"+ 计数器"掉线 1 个"共存）
    expect(screen.getAllByText(/掉线/).length).toBeGreaterThanOrEqual(2);
  });

  it('shows a readable error when the overview api fails', async () => {
    vi.mocked(getAdminAccountsOverview).mockRejectedValue(new Error('权限不足'));
    render(<AdminAccountsPanel />);

    expect(await screen.findByText('权限不足')).toBeInTheDocument();
  });
});
