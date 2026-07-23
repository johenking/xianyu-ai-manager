// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { verifyToken } from './services/api';
import App from './App';

vi.mock('./services/api', () => ({
  verifyToken: vi.fn(),
  logout: vi.fn(),
}));
vi.mock('./components/AuthPortal', () => ({ default: () => <div>登录入口</div> }));
vi.mock('./components/Sidebar', () => ({
  default: ({ setActiveTab }: { setActiveTab: (tab: string) => void }) => (
    <button type="button" onClick={() => setActiveTab('settings')}>打开设置</button>
  ),
}));
vi.mock('./components/Dashboard', () => ({ default: () => <div>仪表盘内容</div> }));
vi.mock('./components/Settings', () => ({
  default: ({ isAdmin }: { isAdmin: boolean }) => <div>{isAdmin ? '管理员设置' : '普通用户设置'}</div>,
}));

describe('App identity hydration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const stored = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
      clear: () => stored.clear(),
    });
    localStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  afterEach(() => cleanup());

  it('keeps a valid token after a transient verify failure and offers retry', async () => {
    localStorage.setItem('auth_token', 'valid-token');
    vi.mocked(verifyToken)
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce({ authenticated: true, user_id: 2, username: 'user', is_admin: false });
    render(<App />);

    expect(await screen.findByText('身份验证暂时不可用')).toBeInTheDocument();
    expect(localStorage.getItem('auth_token')).toBe('valid-token');
    fireEvent.click(screen.getByRole('button', { name: '重试身份验证' }));

    expect(await screen.findByText('仪表盘内容')).toBeInTheDocument();
    expect(verifyToken).toHaveBeenCalledTimes(2);
  });

  it('rehydrates role when another tab changes the authentication token', async () => {
    localStorage.setItem('auth_token', 'admin-token');
    vi.mocked(verifyToken)
      .mockResolvedValueOnce({ authenticated: true, user_id: 1, username: 'admin', is_admin: true })
      .mockResolvedValueOnce({ authenticated: true, user_id: 2, username: 'user', is_admin: false });
    render(<App />);
    await screen.findByText('仪表盘内容');
    fireEvent.click(screen.getByRole('button', { name: '打开设置' }));
    expect(await screen.findByText('管理员设置')).toBeInTheDocument();

    localStorage.setItem('auth_token', 'user-token');
    window.dispatchEvent(new StorageEvent('storage', {
      key: 'auth_token',
      oldValue: 'admin-token',
      newValue: 'user-token',
    }));

    await waitFor(() => expect(screen.getByText('普通用户设置')).toBeInTheDocument());
    expect(verifyToken).toHaveBeenCalledTimes(2);
  });
});
