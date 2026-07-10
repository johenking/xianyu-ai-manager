// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import {
  getAccountDetails,
  getAllAISettings,
  getAccountSessionStatus,
  passwordLogin,
  updateAccountCookieRefreshSettings,
} from '../services/api';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  updateAccountStatus: vi.fn(),
  deleteAccount: vi.fn(),
  generateQRLogin: vi.fn(),
  checkQRLoginStatus: vi.fn(),
  continueQRLoginAfterVerification: vi.fn(),
  addAccountCookie: vi.fn(),
  passwordLogin: vi.fn(),
  checkPasswordLoginStatus: vi.fn(),
  updateAccountRemark: vi.fn(),
  updateAccountAutoConfirm: vi.fn(),
  updateAccountPauseDuration: vi.fn(),
  updateAccountCookie: vi.fn(),
  updateAccountLoginInfo: vi.fn(),
  updateAccountCookieRefreshSettings: vi.fn(),
  updateAccountAISettings: vi.fn(),
  getAllAISettings: vi.fn(),
  getAccountAISettings: vi.fn(),
  getAutoReplyDiagnostics: vi.fn(),
  getAccountSessionStatus: vi.fn(),
  refreshAccountSession: vi.fn(),
  cancelAccountSessionRefresh: vi.fn(),
  getAIProviders: vi.fn(),
  refreshAIProviderModels: vi.fn(),
  testAIProvider: vi.fn(),
}));

describe('AccountList session verification UI', () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.mocked(getAccountDetails).mockResolvedValue([
      {
        id: 'account-1',
        value: 'unb=account-1',
        cookie: 'unb=account-1',
        enabled: true,
        auto_confirm: false,
        remark: '验证账号',
        note: '验证账号',
        pause_duration: 0,
        nickname: '验证账号',
        avatar_url: '',
        ai_enabled: false,
        cookie_refresh_enabled: false,
        cookie_refresh_interval_minutes: 1440,
      } as any,
      {
        id: 'account-2',
        value: 'unb=account-2',
        cookie: 'unb=account-2',
        enabled: true,
        auto_confirm: false,
        remark: '其他账号',
        note: '其他账号',
        pause_duration: 0,
        nickname: '其他账号',
        avatar_url: '',
        ai_enabled: false,
        cookie_refresh_enabled: true,
        cookie_refresh_interval_minutes: 360,
      } as any,
    ]);
    vi.mocked(getAllAISettings).mockResolvedValue({});
    vi.mocked(getAccountSessionStatus).mockResolvedValue({
      state: 'idle',
      trigger: '',
      message: '',
      error_code: '',
      verification_image_url: '',
      started_at: null,
      last_attempt_at: null,
      last_success_at: null,
      expires_at: null,
      updated_at: null,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('lets the user manually check a completed face verification without clearing other account statuses', async () => {
    let verificationCompleted = false;
    vi.mocked(getAccountSessionStatus).mockImplementation(async (accountId: string) => {
      if (accountId === 'account-1') {
        return verificationCompleted
          ? {
              state: 'success',
              trigger: 'manual',
              message: 'Cookie 已刷新，账号监听已恢复',
              error_code: '',
              verification_image_url: '',
              started_at: null,
              last_attempt_at: null,
              last_success_at: 100,
              expires_at: null,
              updated_at: 100,
            }
          : {
              state: 'verification_required',
              trigger: 'manual',
              message: '需要完成闲鱼身份验证',
              error_code: '',
              verification_image_url: '/static/uploads/images/face_verify_account-1.jpg',
              started_at: 1,
              last_attempt_at: 1,
              last_success_at: null,
              expires_at: 9999999999,
              updated_at: 1,
            };
      }
      return {
        state: 'failed',
        trigger: 'manual',
        message: '其他账号刷新失败',
        error_code: 'login_failed',
        verification_image_url: '',
        started_at: 1,
        last_attempt_at: 1,
        last_success_at: null,
        expires_at: null,
        updated_at: 1,
      };
    });

    render(<AccountList />);

    await waitFor(() => {
      expect(screen.getAllByText('需要完成闲鱼身份验证').length).toBeGreaterThan(0);
    });
    expect(screen.getByText('其他账号刷新失败')).toBeInTheDocument();

    const verificationCard = screen.getByRole('heading', { name: '验证账号' }).closest('.ios-card');
    expect(verificationCard).not.toBeNull();
    verificationCompleted = true;
    fireEvent.click(within(verificationCard as HTMLElement).getByRole('button', { name: '我已完成验证，立即检查' }));

    await waitFor(() => {
      expect(screen.getByText('Cookie 已刷新')).toBeInTheDocument();
    });
    expect(screen.getByText('其他账号刷新失败')).toBeInTheDocument();
  });

  it('shows scheduled cookie refresh off by default and saves interval settings without hiding manual refresh', async () => {
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    expect(screen.getAllByTitle('立即刷新 Cookie').length).toBeGreaterThan(0);

    const accountCard = screen.getByRole('heading', { name: '验证账号' }).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    await screen.findByText('自动定时 Cookie 刷新');
    fireEvent.click(screen.getByLabelText('自动定时 Cookie 刷新'));
    fireEvent.change(screen.getByLabelText('刷新间隔'), { target: { value: '360' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(updateAccountCookieRefreshSettings).toHaveBeenCalledWith('account-1', {
        cookie_refresh_enabled: true,
        cookie_refresh_interval_minutes: 360,
      });
    });
  });

  it('submits official password login without a client supplied account id', async () => {
    vi.mocked(passwordLogin).mockResolvedValue({
      success: false,
      message: '测试已接收请求',
    });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '账号密码' }));

    expect(screen.queryByText('账号ID')).not.toBeInTheDocument();
    expect(screen.getByText('密码会使用独立密钥加密保存，仅在官方登录态失效时用于自动续期。')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('用于登录闲鱼官方网站'), {
      target: { value: 'seller@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('登录成功后加密保存'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '开始账号密码登录' }));

    await waitFor(() => {
      expect(passwordLogin).toHaveBeenCalledWith({
        account: 'seller@example.com',
        password: 'secret',
        show_browser: true,
      });
    });
  });
});
