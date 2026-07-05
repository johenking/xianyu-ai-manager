// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import {
  getAccountDetails,
  getAllAISettings,
  getAccountSessionStatus,
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
      } as any,
    ]);
    vi.mocked(getAllAISettings).mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('lets the user manually check a completed face verification without clearing other account statuses', async () => {
    vi.mocked(getAccountSessionStatus).mockImplementation(async (accountId: string) => {
      if (accountId === 'account-1') {
        const callsForAccount1 = vi.mocked(getAccountSessionStatus).mock.calls.filter(([id]) => id === 'account-1').length;
        return callsForAccount1 >= 2
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
    fireEvent.click(within(verificationCard as HTMLElement).getByRole('button', { name: '我已完成验证，立即检查' }));

    await waitFor(() => {
      expect(screen.getByText('Cookie 已刷新')).toBeInTheDocument();
    });
    expect(screen.getByText('其他账号刷新失败')).toBeInTheDocument();
  });
});
