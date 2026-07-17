// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import {
  getAccountDetails,
  getAllAISettings,
  getAccountSessionStatus,
  cancelOfficialLoginSession,
  createOfficialLoginSession,
  getOfficialLoginSession,
  refreshAccountSession,
  showAccountSessionRefreshBrowser,
  showOfficialLoginBrowser,
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
  createOfficialLoginSession: vi.fn(),
  getOfficialLoginSession: vi.fn(),
  showOfficialLoginBrowser: vi.fn(),
  cancelOfficialLoginSession: vi.fn(),
  showAccountSessionRefreshBrowser: vi.fn(),
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
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'official-session',
      mode: 'qr',
      state: 'waiting_user',
      message: '请使用闲鱼 App 扫码',
      error_code: '',
      qr_image_url: '/static/uploads/images/official-qr.png',
      verification_image_url: '',
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 1,
      expires_at: 9999999999,
    });
    vi.mocked(cancelOfficialLoginSession).mockResolvedValue({ success: true });
    vi.mocked(showOfficialLoginBrowser).mockResolvedValue({ success: true });
    vi.mocked(showAccountSessionRefreshBrowser).mockResolvedValue({ success: true });
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

  it('shows same-session controls only while a verification browser is active', async () => {
    vi.mocked(getAccountSessionStatus).mockImplementation(async (accountId: string) => {
      if (accountId === 'account-1') {
        return {
          state: 'verification_required',
          trigger: 'manual',
          message: '需要完成闲鱼身份验证',
          error_code: '',
          verification_image_url: '/static/uploads/images/face_verify_account-1.jpg',
          browser_active: true,
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
    fireEvent.click(within(verificationCard as HTMLElement).getByRole('button', { name: '本机打开' }));
    await waitFor(() => expect(showAccountSessionRefreshBrowser).toHaveBeenCalledWith('account-1'));
    expect(within(verificationCard as HTMLElement).getByText('后台正在自动检测，完成验证后会自动保存并恢复监听。')).toBeInTheDocument();
    expect(within(verificationCard as HTMLElement).queryByRole('button', { name: '我已完成验证，立即检查' })).not.toBeInTheDocument();
    expect(screen.getByText('其他账号刷新失败')).toBeInTheDocument();
  });

  it('offers one explicit start action when no verification browser exists', async () => {
    vi.mocked(getAccountSessionStatus).mockImplementation(async (accountId: string) => ({
      state: accountId === 'account-1' ? 'action_required' : 'idle',
      trigger: 'message_token_probe',
      message: accountId === 'account-1' ? '请手动开始一次验证' : '',
      error_code: accountId === 'account-1' ? 'human_verification_required' : '',
      verification_image_url: '',
      browser_active: false,
      started_at: null,
      last_attempt_at: null,
      last_success_at: null,
      expires_at: null,
      updated_at: 1,
    }));
    vi.mocked(refreshAccountSession).mockResolvedValue({
      success: true,
      message: '已开始一次验证',
      data: {
        state: 'refreshing',
        trigger: 'manual',
        message: '正在启动官方会话',
        error_code: '',
        verification_image_url: '',
        browser_active: false,
      },
    });

    render(<AccountList />);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    const startButton = await within(accountCard as HTMLElement).findByRole(
      'button',
      { name: '开始一次验证' },
    );
    expect(within(accountCard as HTMLElement).queryByRole('button', { name: '本机打开' })).not.toBeInTheDocument();
    expect(within(accountCard as HTMLElement).queryByRole('button', { name: '取消' })).not.toBeInTheDocument();

    fireEvent.click(startButton);
    await waitFor(() => expect(refreshAccountSession).toHaveBeenCalledTimes(1));
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

  it('explains that saved login details are not used by automatic renewal', async () => {
    render(<AccountList />);

    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    expect(await screen.findByText('自动刷新只复用官方浏览器档案，不会读取或提交这里保存的密码。')).toBeInTheDocument();
    expect(screen.queryByText('尚未保存登录密码，Cookie 失效后无法自动登录刷新。')).not.toBeInTheDocument();
  });

  it('starts only one manual refresh when the button is double-clicked', async () => {
    vi.mocked(refreshAccountSession).mockResolvedValue({
      success: true,
      message: '已开始刷新 Cookie',
      data: {
        state: 'refreshing',
        trigger: 'manual',
        message: '正在刷新闲鱼登录状态',
        error_code: '',
        verification_image_url: '',
        started_at: 10,
        last_attempt_at: 10,
        last_success_at: null,
        expires_at: null,
        updated_at: 10,
      },
    });
    render(<AccountList />);

    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    const refreshButton = within(accountCard as HTMLElement).getByTitle('立即刷新 Cookie');
    act(() => {
      fireEvent.click(refreshButton);
      fireEvent.click(refreshButton);
    });

    await waitFor(() => expect(refreshAccountSession).toHaveBeenCalledTimes(1));
  });

  it('submits official password login without a client supplied account id', async () => {
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'password-session',
      mode: 'password',
      state: 'preparing',
      message: '正在打开官方登录页',
      error_code: '',
      qr_image_url: '',
      verification_image_url: '',
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 1,
      expires_at: 9999999999,
    });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '账号密码' }));

    expect(screen.queryByText('账号ID')).not.toBeInTheDocument();
    expect(await screen.findByText('密码只在本次手动登录中提交，登录成功后加密保存；后台续期不会自动填写。')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('用于登录闲鱼官方网站'), {
      target: { value: 'seller@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('登录成功后加密保存'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '开始账号密码登录' }));

    await waitFor(() => {
      expect(createOfficialLoginSession).toHaveBeenCalledWith({
        mode: 'password',
        account: 'seller@example.com',
        password: 'secret',
        show_browser: false,
      });
    });
  });

  it('opens with an official QR session and exposes the same-session browser action', async () => {
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/official-qr.png',
    );
    expect(createOfficialLoginSession).toHaveBeenCalledWith({ mode: 'qr', show_browser: false });

    fireEvent.click(screen.getByRole('button', { name: '本机打开官方窗口' }));
    await waitFor(() => expect(showOfficialLoginBrowser).toHaveBeenCalledWith('official-session'));
  });

  it('cancels the unused official session when switching methods and unmounting', async () => {
    const view = render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByAltText('闲鱼登录二维码');
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));

    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));
    view.unmount();
    expect(cancelOfficialLoginSession).toHaveBeenCalledTimes(1);
  });

  it('retries a failed official QR session in place', async () => {
    vi.mocked(createOfficialLoginSession)
      .mockResolvedValueOnce({
        success: true,
        session_id: 'failed-session',
        mode: 'qr',
        state: 'failed',
        message: '官方页面加载失败',
        error_code: 'browser_error',
        qr_image_url: '',
        verification_image_url: '',
        account_id: '',
        is_new_account: false,
        created_at: 1,
        updated_at: 1,
        expires_at: 2,
      })
      .mockResolvedValueOnce({
        success: true,
        session_id: 'retry-session',
        mode: 'qr',
        state: 'waiting_user',
        message: '等待扫码',
        error_code: '',
        qr_image_url: '/static/uploads/images/retry-qr.png',
        verification_image_url: '',
        account_id: '',
        is_new_account: false,
        created_at: 2,
        updated_at: 2,
        expires_at: 9999999999,
      });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByText('官方页面加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/retry-qr.png',
    );
    expect(createOfficialLoginSession).toHaveBeenCalledTimes(2);
  });

  it('refreshes an expired official QR session', async () => {
    vi.mocked(createOfficialLoginSession)
      .mockResolvedValueOnce({
        success: true,
        session_id: 'expired-session',
        mode: 'qr',
        state: 'expired',
        message: '闲鱼官方登录会话已过期',
        error_code: 'session_expired',
        qr_image_url: '',
        verification_image_url: '',
        account_id: '',
        is_new_account: false,
        created_at: 1,
        updated_at: 2,
        expires_at: 2,
      })
      .mockResolvedValueOnce({
        success: true,
        session_id: 'fresh-session',
        mode: 'qr',
        state: 'waiting_user',
        message: '请使用闲鱼 App 扫码',
        error_code: '',
        qr_image_url: '/static/uploads/images/fresh-qr.png',
        verification_image_url: '',
        account_id: '',
        is_new_account: false,
        created_at: 3,
        updated_at: 3,
        expires_at: 9999999999,
      });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByText('闲鱼官方登录会话已过期');
    fireEvent.click(screen.getByRole('button', { name: '重新生成二维码' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/fresh-qr.png',
    );
    expect(createOfficialLoginSession).toHaveBeenCalledTimes(2);
  });

  it('shows official human verification without changing sessions', async () => {
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'verification-session',
      mode: 'qr',
      state: 'verification_required',
      message: '需要完成闲鱼身份验证',
      error_code: 'verification_required',
      qr_image_url: '',
      verification_image_url: '/static/uploads/images/verification.png',
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 2,
      expires_at: 9999999999,
    });
    vi.mocked(getOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'verification-session',
      mode: 'qr',
      state: 'verification_required',
      message: '仍在等待人工验证',
      error_code: 'verification_required',
      qr_image_url: '',
      verification_image_url: '/static/uploads/images/verification.png',
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 3,
      expires_at: 9999999999,
    });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByAltText('闲鱼安全验证页面')).toHaveAttribute(
      'src',
      '/static/uploads/images/verification.png',
    );
    expect(screen.getByText('请按官方页面提示完成验证')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '我已完成验证，立即检查' })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(getOfficialLoginSession).toHaveBeenCalledWith('verification-session');
      expect(screen.getByText('仍在等待人工验证')).toBeInTheDocument();
    }, { timeout: 2500 });
    expect(createOfficialLoginSession).toHaveBeenCalledTimes(1);
  });

  it('stops polling and closes after the official session succeeds', async () => {
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'polling-session',
      mode: 'qr',
      state: 'preparing',
      message: '正在打开官方页面',
      error_code: '',
      qr_image_url: '',
      verification_image_url: '',
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 1,
      expires_at: 9999999999,
    });
    vi.mocked(getOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'polling-session',
      mode: 'qr',
      state: 'success',
      message: '闲鱼官方登录成功',
      error_code: '',
      qr_image_url: '',
      verification_image_url: '',
      account_id: 'account-1',
      is_new_account: false,
      created_at: 1,
      updated_at: 2,
      expires_at: 9999999999,
    });
    render(<AccountList />);

    await screen.findByText('定时刷新关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByRole('heading', { name: '添加账号' });

    await waitFor(() => expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument(), {
      timeout: 4000,
    });
    expect(getOfficialLoginSession).toHaveBeenCalledTimes(1);
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
  });
});
