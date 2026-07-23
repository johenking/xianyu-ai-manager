// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import {
  getAccountDetails,
  getAllAISettings,
  getAccountSessionStatus,
  generateQRLogin,
  checkQRLoginStatus,
  continueQRLoginAfterVerification,
  createBrowserExtensionPairing,
  getBrowserExtensionPairing,
  addAccountCookie,
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
  createBrowserExtensionPairing: vi.fn(),
  getBrowserExtensionPairing: vi.fn(),
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
  let localStorageValues: Map<string, string>;

  beforeEach(() => {
    vi.useRealTimers();
    localStorageValues = new Map();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: vi.fn((key: string) => localStorageValues.get(key) ?? null),
        setItem: vi.fn((key: string, value: string) => {
          localStorageValues.set(key, value);
        }),
        removeItem: vi.fn((key: string) => {
          localStorageValues.delete(key);
        }),
        clear: vi.fn(() => localStorageValues.clear()),
      },
    });
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
        username: 'seller@example.com',
        has_login_password: true,
        login_credentials_valid: true,
        login_method: 'password',
        login_method_label: '账号密码',
        auto_refresh_supported: true,
        reauth_required: false,
        reauth_action: 'password_login',
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
        username: 'seller-2@example.com',
        has_login_password: true,
        login_credentials_valid: true,
        login_method: 'password',
        login_method_label: '账号密码',
        auto_refresh_supported: true,
        reauth_required: false,
        reauth_action: 'password_login',
      } as any,
    ]);
    vi.mocked(getAllAISettings).mockResolvedValue({});
    vi.mocked(generateQRLogin).mockResolvedValue({
      success: true,
      session_id: 'qr-session',
      qr_code_url: 'data:image/png;base64,qr',
    });
    vi.mocked(checkQRLoginStatus).mockResolvedValue({
      status: 'waiting',
      session_id: 'qr-session',
    });
    vi.mocked(continueQRLoginAfterVerification).mockResolvedValue({
      status: 'processing',
      session_id: 'qr-session',
    });
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

    await screen.findByText('可自动续期 · 定时关闭');
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

  it('explains that encrypted credentials are the fallback for automatic renewal', async () => {
    render(<AccountList />);

    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    expect(await screen.findByText('登录信息已加密保存；官方档案完全退出后可使用这些凭据自动续期。')).toBeInTheDocument();
    expect(screen.queryByText('尚未保存登录密码，Cookie 失效后需要人工重新登录。')).not.toBeInTheDocument();
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

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '账号密码' }));

    expect(screen.queryByText('账号ID')).not.toBeInTheDocument();
    expect(await screen.findByText('密码会使用独立密钥加密保存，仅在官方登录态失效时用于自动续期。')).toBeInTheDocument();

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

  it('offers SMS login through a visible official window without collecting the code', async () => {
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'sms-session',
      mode: 'sms',
      state: 'waiting_user',
      message: '请在官方窗口完成验证码登录',
      error_code: '',
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '手机号验证码' }));

    expect(await screen.findByText('在闲鱼官方窗口完成验证码登录')).toBeInTheDocument();
    expect(screen.queryByLabelText('短信验证码')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('用于在官方页面预填'), {
      target: { value: '13800138000' },
    });
    fireEvent.click(screen.getByRole('button', { name: '打开官方登录窗口' }));

    await waitFor(() => {
      expect(createOfficialLoginSession).toHaveBeenCalledWith({
        mode: 'sms',
        account: '13800138000',
        show_browser: true,
      });
    });
  });

  it('offers all five login entries and submits manual Cookie without an account id', async () => {
    vi.mocked(addAccountCookie).mockResolvedValue({
      success: true,
      message: 'Cookie 已保存',
      account_id: '9988',
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByRole('dialog', { name: '添加账号' });

    expect(screen.getByRole('button', { name: '扫码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '手机号验证码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '账号密码' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '高级方式' }));
    expect(screen.getByRole('button', { name: '本机 Chrome' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '手填 Cookie' }));

    await screen.findByPlaceholderText('粘贴从浏览器复制的 Cookie');
    expect(screen.queryByText('账号ID')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('例如闲鱼 userId / unb')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('粘贴从浏览器复制的 Cookie'), {
      target: { value: 'unb=9988; cookie2=session' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存 Cookie' }));

    await waitFor(() => {
      expect(addAccountCookie).toHaveBeenCalledWith({
        value: 'unb=9988; cookie2=session',
      });
    });
  });

  it('keeps API QR as the default and offers a one-time local Chrome extension pairing', async () => {
    vi.mocked(createBrowserExtensionPairing).mockResolvedValue({
      pairing_id: 'pairing-id',
      pairing_code: 'ABCD1234',
      status: 'waiting',
      message: '等待 Chrome 扩展导入',
      expires_at: 9_999_999_999,
      local_import_url: 'http://127.0.0.1:8091/api/browser-extension/import',
    });
    vi.mocked(getBrowserExtensionPairing).mockResolvedValue({
      pairing_id: 'pairing-id',
      status: 'waiting',
      message: '等待 Chrome 扩展导入',
      expires_at: 9_999_999_999,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByAltText('闲鱼登录二维码')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '高级方式' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome' }));
    expect(await screen.findByText('从日常 Chrome 主动导入')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载扩展 ZIP' })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-cookie-importer.zip',
    );
    fireEvent.click(screen.getByRole('button', { name: '创建一次性配对' }));

    await waitFor(() => {
      expect(createBrowserExtensionPairing).toHaveBeenCalledTimes(1);
      expect(screen.getByLabelText('扩展配对信息')).toHaveValue(
        '{"pairing_id":"pairing-id","pairing_code":"ABCD1234"}',
      );
    });
  });

  it('opens with an API QR and does not expose a browser action before verification', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,qr',
    );
    expect(generateQRLogin).toHaveBeenCalledTimes(1);
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '本机打开官方窗口' })).not.toBeInTheDocument();
  });

  it('does not create or cancel an official browser session for ordinary QR', async () => {
    const view = render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByAltText('闲鱼登录二维码');
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));

    view.unmount();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('retries a failed API QR generation in place', async () => {
    vi.mocked(generateQRLogin)
      .mockResolvedValueOnce({
        success: false,
        message: '二维码接口暂时不可用',
      })
      .mockResolvedValueOnce({
        success: true,
        session_id: 'retry-session',
        qr_code_url: '/static/uploads/images/retry-qr.png',
      });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByText('二维码接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/retry-qr.png',
    );
    expect(generateQRLogin).toHaveBeenCalledTimes(2);
  });

  it('regenerates an expired API QR session', async () => {
    vi.mocked(generateQRLogin)
      .mockResolvedValueOnce({
        success: false,
        message: '二维码已过期',
      })
      .mockResolvedValueOnce({
        success: true,
        session_id: 'fresh-session',
        qr_code_url: '/static/uploads/images/fresh-qr.png',
      });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByText('二维码已过期');
    fireEvent.click(screen.getByRole('button', { name: '重新生成二维码' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/fresh-qr.png',
    );
    expect(generateQRLogin).toHaveBeenCalledTimes(2);
  });

  it('starts the dedicated browser only after API QR requires verification', async () => {
    vi.mocked(checkQRLoginStatus).mockResolvedValue({
      status: 'verification_required',
      session_id: 'qr-session',
      message: '仍在等待人工验证',
      error_code: 'verification_required',
      verification_screenshot_path: '/static/uploads/images/verification.png',
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByAltText('闲鱼安全验证页面', {}, { timeout: 2500 })).toHaveAttribute(
      'src',
      '/static/uploads/images/verification.png',
    );
    expect(screen.getByText('请按官方页面提示完成验证')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '本机打开官方窗口' }));
    await waitFor(() => expect(continueQRLoginAfterVerification).toHaveBeenCalledWith('qr-session'));
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('stops polling and closes after the API QR session succeeds', async () => {
    vi.mocked(checkQRLoginStatus).mockResolvedValue({
      status: 'success',
      session_id: 'qr-session',
      message: '闲鱼官方登录成功',
      account_info: { account_id: 'account-1', is_new_account: false },
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByRole('heading', { name: '添加账号' });

    await waitFor(() => expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument(), {
      timeout: 4000,
    });
    expect(checkQRLoginStatus).toHaveBeenCalledTimes(1);
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
  });

  it.each([
    ['qr_login', '重新扫码', 'alt', '闲鱼登录二维码'],
    ['sms_login', '验证码登录', 'text', '在闲鱼官方窗口完成验证码登录'],
    ['password_login', '账号密码登录', 'text', '支持自动续期'],
    ['chrome_extension_import', '重新导入', 'text', '从日常 Chrome 主动导入'],
    ['manual_cookie', '重新填写', 'placeholder', '粘贴从浏览器复制的 Cookie'],
    ['choose_login', '重新登录', 'alt', '闲鱼登录二维码'],
  ] as const)(
    'routes the %s reminder CTA to its matching login entry',
    async (reauthAction, buttonName, queryKind, expectedContent) => {
      vi.mocked(getAccountDetails).mockResolvedValue([{
        id: `expired-${reauthAction}`,
        enabled: true,
        auto_confirm: false,
        remark: `过期 ${reauthAction}`,
        nickname: `过期 ${reauthAction}`,
        pause_duration: 0,
        login_method: reauthAction === 'password_login' ? 'password' : 'unknown',
        login_method_label: '测试登录',
        auto_refresh_supported: reauthAction === 'password_login',
        reauth_required: true,
        reauth_action: reauthAction,
        username: reauthAction === 'password_login' ? 'seller@example.com' : '',
        last_expired_at: 1234,
      } as any]);
      vi.mocked(getAccountSessionStatus).mockResolvedValue({
        state: 'manual_reauth_required',
        trigger: 'expired',
        message: '需要重新登录',
        error_code: 'manual_reauth_required',
        verification_image_url: '',
        last_expired_at: 1234,
        updated_at: 2000,
      });

      render(<AccountList />);
      const reminder = await screen.findByRole('dialog', { name: '账号登录已过期' });
      fireEvent.click(within(reminder).getByRole('button', { name: buttonName }));

      const target = queryKind === 'alt'
        ? await screen.findByAltText(expectedContent)
        : queryKind === 'placeholder'
          ? await screen.findByPlaceholderText(expectedContent)
          : await screen.findByText(expectedContent);
      expect(target).toBeInTheDocument();
    },
  );

  it('shows one reminder for the same account expiry and shows a new one when last_expired_at changes', async () => {
    let lastExpiredAt = 1234;
    const expiredAccount = () => ({
      id: 'expired-account',
      enabled: true,
      auto_confirm: false,
      remark: '扫码账号',
      pause_duration: 0,
      nickname: '扫码账号',
      login_method: 'qr',
      login_method_label: '扫码登录',
      auto_refresh_supported: false,
      cookie_refresh_enabled: false,
      reauth_required: true,
      reauth_action: 'qr_login',
      last_expired_at: lastExpiredAt,
    } as any);
    vi.mocked(getAccountDetails).mockImplementation(async () => [expiredAccount()]);
    vi.mocked(getAccountSessionStatus).mockImplementation(async () => ({
      state: 'manual_reauth_required',
      trigger: 'token_expired',
      message: '当前登录态需要重新扫码',
      error_code: 'manual_reauth_required',
      verification_image_url: '',
      last_expired_at: lastExpiredAt,
      updated_at: lastExpiredAt,
    }));

    const firstRender = render(<AccountList />);
    expect(await screen.findByRole('dialog', { name: '账号登录已过期' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭过期提醒' }));
    firstRender.unmount();

    const secondRender = render(<AccountList />);
    await screen.findByRole('heading', { name: '扫码账号' });
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '账号登录已过期' })).not.toBeInTheDocument();
    });
    secondRender.unmount();

    lastExpiredAt = 5678;
    render(<AccountList />);
    expect(await screen.findByRole('dialog', { name: '账号登录已过期' })).toBeInTheDocument();
    expect(window.localStorage.setItem).toHaveBeenCalledWith('xianyu-reauth:expired-account:5678', 'shown');
  });
});
