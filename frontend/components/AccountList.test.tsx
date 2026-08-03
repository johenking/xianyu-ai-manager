// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import { ApiRequestError } from '../services/request';
import {
  getAccountDetails,
  getAllAISettings,
  getAccountSessionStatus,
  generateQRLogin,
  checkQRLoginStatus,
  continueQRLoginAfterVerification,
  cancelQRLogin,
  createBrowserExtensionPairing,
  getBrowserExtensionPairing,
  registerClientBrowserDevice,
  createClientBrowserLoginSession,
  getClientBrowserLoginSession,
  confirmClientBrowserLoginSession,
  cancelClientBrowserLoginSession,
  getNativeBrowserDevice,
  startNativeBrowserLogin,
  getNativeBrowserLoginStatus,
  cancelNativeBrowserLogin,
  closeNativeBrowserLogin,
  bindAccountRenewalDevice,
  addAccountCookie,
  cancelOfficialLoginSession,
  createOfficialLoginSession,
  getOfficialLoginSession,
  interactWithOfficialLogin,
  interactWithQRLogin,
  refreshAccountSession,
  showAccountSessionRefreshBrowser,
  showOfficialLoginBrowser,
  updateAccountCookieRefreshSettings,
  getAccountAISettings,
  getAIProviders,
  getAiReplyStrategies,
  updateAiReplyStrategies,
} from '../services/api';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  updateAccountStatus: vi.fn(),
  deleteAccount: vi.fn(),
  generateQRLogin: vi.fn(),
  checkQRLoginStatus: vi.fn(),
  continueQRLoginAfterVerification: vi.fn(),
  cancelQRLogin: vi.fn(),
  createBrowserExtensionPairing: vi.fn(),
  getBrowserExtensionPairing: vi.fn(),
  registerClientBrowserDevice: vi.fn(),
  createClientBrowserLoginSession: vi.fn(),
  getClientBrowserLoginSession: vi.fn(),
  confirmClientBrowserLoginSession: vi.fn(),
  cancelClientBrowserLoginSession: vi.fn(),
  getNativeBrowserDevice: vi.fn(),
  startNativeBrowserLogin: vi.fn(),
  getNativeBrowserLoginStatus: vi.fn(),
  cancelNativeBrowserLogin: vi.fn(),
  closeNativeBrowserLogin: vi.fn(),
  bindAccountRenewalDevice: vi.fn(),
  addAccountCookie: vi.fn(),
  passwordLogin: vi.fn(),
  checkPasswordLoginStatus: vi.fn(),
  createOfficialLoginSession: vi.fn(),
  getOfficialLoginSession: vi.fn(),
  interactWithOfficialLogin: vi.fn(),
  interactWithQRLogin: vi.fn(),
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
  getAiReplyStrategies: vi.fn(),
  updateAiReplyStrategies: vi.fn(),
}));

describe('AccountList session verification UI', () => {
  let localStorageValues: Map<string, string>;
  let clientBridgeEnabled: boolean;
  let clientBridgeListener: (event: MessageEvent) => void;
  let clientBridgeRequests: string[];

  beforeEach(() => {
    vi.useRealTimers();
    clientBridgeEnabled = true;
    clientBridgeRequests = [];
    clientBridgeListener = (event: MessageEvent) => {
      if (!clientBridgeEnabled || !event.data?.requestId) return;
      if (!['XMC_GET_DEVICE', 'XMC_START_LOGIN', 'XMC_CONFIRM_LOGIN', 'XMC_CANCEL_LOGIN'].includes(event.data.type)) return;
      clientBridgeRequests.push(String(event.data.type));
      const data = event.data.type === 'XMC_GET_DEVICE'
        ? {
          deviceId: 'device_fixture_1234',
          browserFamily: 'chrome',
          extensionVersion: '1.2.1',
          protocolVersion: 1,
          signingPublicJwk: { kty: 'EC', crv: 'P-256', x: 'fixture-x', y: 'fixture-y' },
          encryptionPublicJwk: { kty: 'EC', crv: 'P-256', x: 'fixture-ex', y: 'fixture-ey' },
        }
        : { accepted: true };
      queueMicrotask(() => window.dispatchEvent(new MessageEvent('message', {
        source: window,
        origin: window.location.origin,
        data: {
          type: 'XMC_CLIENT_BROWSER_RESULT',
          requestId: event.data.requestId,
          response: { ok: true, data },
        },
      })));
    };
    window.addEventListener('message', clientBridgeListener);
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
    vi.mocked(getAccountAISettings).mockResolvedValue({
      ai_enabled: false,
      model_name: 'deepseek-v4-flash',
      base_url: 'https://api.deepseek.com',
      api_key_source: 'missing',
      api_key_masked: '',
      has_effective_api_key: false,
      custom_prompts: '',
    } as any);
    vi.mocked(getAIProviders).mockResolvedValue({ providers: [] } as any);
    vi.mocked(getAiReplyStrategies).mockResolvedValue([
      { prompt_type: 'price', title: '议价专家', content: '议价话术', enabled: true },
      { prompt_type: 'tech', title: '技术专家', content: '技术话术', enabled: true },
      { prompt_type: 'default', title: '默认客服', content: '默认话术', enabled: true },
    ]);
    vi.mocked(updateAiReplyStrategies).mockImplementation(async (strategies) => ({ success: true, data: strategies } as any));
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
    vi.mocked(cancelQRLogin).mockResolvedValue({
      status: 'cancelled',
      session_id: 'qr-session',
      ended_by: 'user_cancelled',
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
    vi.mocked(interactWithOfficialLogin).mockResolvedValue({
      success: true,
      accepted: true,
      frame_revision: 1,
    });
    vi.mocked(interactWithQRLogin).mockResolvedValue({
      success: true,
      accepted: true,
      frame_revision: 1,
    });
    vi.mocked(showOfficialLoginBrowser).mockResolvedValue({ success: true });
    vi.mocked(showAccountSessionRefreshBrowser).mockResolvedValue({ success: true });
    vi.mocked(registerClientBrowserDevice).mockResolvedValue();
    vi.mocked(getNativeBrowserDevice).mockResolvedValue({
      deviceId: 'helper_device_123456',
      browserFamily: 'chrome',
      clientType: 'native_helper',
      helperVersion: '1.0.2',
      protocolVersion: 1,
      signingPublicJwk: { kty: 'EC', crv: 'P-256', x: 'fixture-x', y: 'fixture-y' },
      encryptionPublicJwk: { kty: 'EC', crv: 'P-256', x: 'fixture-ex', y: 'fixture-ey' },
    });
    vi.mocked(startNativeBrowserLogin).mockResolvedValue({
      session_id: 'native-login-session',
      device_id: 'helper_device_123456',
      state: 'opening_browser',
      message: '正在打开本机 Chrome',
      expires_at: 9_999_999_999,
    });
    vi.mocked(getNativeBrowserLoginStatus).mockResolvedValue({
      session_id: 'native-login-session',
      device_id: 'helper_device_123456',
      state: 'waiting_user',
      message: '请在本机 Chrome 完成登录',
      expires_at: 9_999_999_999,
    });
    vi.mocked(cancelNativeBrowserLogin).mockResolvedValue({
      session_id: 'native-login-session', device_id: 'helper_device_123456',
      state: 'cancelled', message: '已取消', expires_at: 9_999_999_999,
    });
    vi.mocked(closeNativeBrowserLogin).mockResolvedValue({
      session_id: 'native-login-session', device_id: 'helper_device_123456',
      state: 'success', message: '成功', expires_at: 9_999_999_999,
    });
    vi.mocked(createClientBrowserLoginSession).mockImplementation(async (_deviceId, mode) => ({
      session_id: `${mode}-client-session`,
      device_id: 'helper_device_123456',
      mode,
      state: 'waiting_user',
      message: '请在当前设备浏览器继续',
      expires_at: 9_999_999_999,
    }));
    vi.mocked(getClientBrowserLoginSession).mockImplementation(async (sessionId) => ({
      session_id: sessionId,
      device_id: 'device_fixture_1234',
      mode: sessionId.split('-')[0] as 'qr' | 'sms' | 'password',
      state: 'waiting_user',
      message: '请在当前设备浏览器继续',
      expires_at: 9_999_999_999,
    }));
    vi.mocked(confirmClientBrowserLoginSession).mockImplementation(async (sessionId, accountId) => ({
      session_id: sessionId,
      device_id: 'device_fixture_1234',
      mode: sessionId.split('-')[0] as 'qr' | 'sms' | 'password',
      state: 'success',
      message: '登录成功',
      account_id: accountId,
      expires_at: 9_999_999_999,
    }));
    vi.mocked(cancelClientBrowserLoginSession).mockResolvedValue();
    vi.mocked(bindAccountRenewalDevice).mockResolvedValue();
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
    window.removeEventListener('message', clientBridgeListener);
    cleanup();
    vi.clearAllMocks();
    vi.restoreAllMocks();
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

    render(<AccountList isAdmin />);

    await waitFor(() => {
      expect(screen.getAllByText('需要完成闲鱼身份验证').length).toBeGreaterThan(0);
    });
    expect(screen.getByText('其他账号刷新失败')).toBeInTheDocument();

    const verificationCard = screen.getByRole('heading', { name: '验证账号' }).closest('.ios-card');
    expect(verificationCard).not.toBeNull();
    fireEvent.click(within(verificationCard as HTMLElement).getByRole('button', { name: '显示服务器运维窗口' }));
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
    expect(within(accountCard as HTMLElement).queryByRole('button', { name: '显示服务器运维窗口' })).not.toBeInTheDocument();
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

  it('shows the bound-device renewal state without exposing stored credentials', async () => {
    render(<AccountList />);

    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    expect(await screen.findByText('已绑定一个当前设备浏览器')).toBeInTheDocument();
    expect(screen.getByText(/账号密码不会在此处展示或修改/)).toBeInTheDocument();
    expect(screen.queryByLabelText('登录密码')).not.toBeInTheDocument();
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

  it('starts password login in the current device without collecting credentials in the console', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '账号密码' }));

    expect(screen.queryByText('账号ID')).not.toBeInTheDocument();
    expect(await screen.findByText(/账号、密码、滑块和人脸验证只在你的 Chrome 或 Edge/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('用于登录闲鱼官方网站')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('登录成功后加密保存')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '在当前设备浏览器继续' }));

    await waitFor(() => {
      expect(createClientBrowserLoginSession).toHaveBeenCalledWith(
        'helper_device_123456', 'password', 'native_helper',
      );
    });
    await waitFor(() => expect(startNativeBrowserLogin).toHaveBeenCalledWith(expect.objectContaining({
      device_id: 'helper_device_123456',
      mode: 'password',
      server_origin: window.location.origin,
      official_url: 'https://www.goofish.com/login',
    })));
    expect(clientBridgeRequests).not.toContain('XMC_GET_DEVICE');
    expect(clientBridgeRequests).not.toContain('XMC_START_LOGIN');
    expect(registerClientBrowserDevice).toHaveBeenCalledTimes(1);
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('labels a transient message Token probe failure as retryable', async () => {
    vi.mocked(getAccountSessionStatus).mockImplementation(async (accountId: string) => ({
      state: accountId === 'account-1' ? 'failed' : 'idle',
      trigger: 'message_token_probe',
      message: accountId === 'account-1' ? '消息 Token 探测出现临时异常' : '',
      error_code: accountId === 'account-1' ? 'token_probe_exception' : '',
      verification_image_url: '',
      browser_active: false,
      started_at: null,
      last_attempt_at: 1,
      last_success_at: null,
      expires_at: null,
      updated_at: 1,
    }));

    render(<AccountList />);

    expect((await screen.findAllByText('平台连接暂时异常')).length).toBeGreaterThan(0);
    expect(screen.getByText('平台连接暂时异常，系统会自动重试；原登录态已保留。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新刷新' })).toBeInTheDocument();
    expect(screen.queryByText('需要手动验证')).not.toBeInTheDocument();
  });

  it('confirms the persisted account before allowing the current-device tab to close', async () => {
    vi.mocked(getClientBrowserLoginSession).mockResolvedValue({
      session_id: 'password-client-session',
      device_id: 'device_fixture_1234',
      mode: 'password',
      state: 'awaiting_confirmation',
      message: '等待前端确认',
      account_id: 'account-1',
      expires_at: 9_999_999_999,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    fireEvent.click(screen.getByRole('button', { name: '在当前设备浏览器继续' }));

    await waitFor(() => expect(confirmClientBrowserLoginSession).toHaveBeenCalledWith(
      'password-client-session',
      'account-1',
    ), { timeout: 3500 });
    expect(closeNativeBrowserLogin).toHaveBeenCalledWith('password-client-session', 'account-1');
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.queryByText('是否在此设备启用自动续期')).not.toBeInTheDocument();
    expect(bindAccountRenewalDevice).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: '添加账号' })).not.toBeInTheDocument();
  });

  it('stores extension renewal credentials only after post-login explicit authorization', async () => {
    vi.mocked(getClientBrowserLoginSession).mockResolvedValue({
      session_id: 'password-client-session',
      device_id: 'device_fixture_1234',
      mode: 'password',
      state: 'awaiting_confirmation',
      message: '等待前端确认',
      account_id: 'account-1',
      expires_at: 9_999_999_999,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '你的 Chrome' }));
    fireEvent.click(screen.getByRole('button', { name: '用扩展打开官方登录页' }));

    await screen.findByText('是否在此设备启用自动续期', {}, { timeout: 3500 });
    fireEvent.change(screen.getByPlaceholderText('闲鱼账号或手机号'), {
      target: { value: 'seller@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('再次输入用于续期的密码'), {
      target: { value: 'fixture-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存并绑定' }));
    expect(await screen.findByText('请填写账号和密码，并勾选明确授权')).toBeInTheDocument();
    expect(bindAccountRenewalDevice).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '保存并绑定' }));
    await waitFor(() => expect(bindAccountRenewalDevice).toHaveBeenCalledWith('account-1', {
      login_session_id: 'password-client-session',
      device_id: 'device_fixture_1234',
      username: 'seller@example.com',
      password: 'fixture-password',
      authorized: true,
      authorized_at: expect.any(Number),
    }));
  });

  it('does not create any server login session when the native helper is missing', async () => {
    vi.mocked(getNativeBrowserDevice).mockRejectedValue(Object.assign(
      new Error('未启动本机浏览器助手'),
      { code: 'helper_unavailable', status: 0 },
    ));
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    expect((await screen.findAllByText(
      '首次使用安装并启动一次本机助手；后续点击本机 Chrome 登录即可打开你电脑上的官方页面。',
      {},
      { timeout: 4500 },
    )).length).toBeGreaterThan(0);
    expect(screen.getAllByText('未启动本机浏览器助手').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '安装本机助手' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '改用网页二维码' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '安装本机助手' }));
    expect(screen.getByRole('link', { name: '下载 macOS 助手（Apple 芯片）' })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-native-browser-helper-macos-arm64-1.0.2.zip',
    );
    expect(screen.getByRole('link', { name: '下载 Windows 助手（x64）' })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-native-browser-helper-windows-x64-1.0.2.zip',
    );
    expect(createClientBrowserLoginSession).not.toHaveBeenCalled();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(createBrowserExtensionPairing).not.toHaveBeenCalled();
  });

  it('classifies an outdated native helper without creating a login session', async () => {
    vi.mocked(getNativeBrowserDevice).mockResolvedValue({
      deviceId: 'helper_device_123456',
      browserFamily: 'chrome',
      clientType: 'native_helper',
      helperVersion: '0.9.0',
      protocolVersion: 1,
      signingPublicJwk: {},
      encryptionPublicJwk: {},
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    expect(await screen.findByText('本机浏览器助手需要更新')).toBeInTheDocument();
    expect(screen.getAllByText(/当前 0.9.0，需要 1.0.2/).length).toBeGreaterThan(0);
    expect(registerClientBrowserDevice).not.toHaveBeenCalled();
    expect(createClientBrowserLoginSession).not.toHaveBeenCalled();
  });

  it.each([
    [401, 'auth_expired', '账号登录已失效'],
    [409, 'device_key_mismatch', '设备注册冲突'],
  ])('classifies device registration status %s', async (status, code, title) => {
    vi.mocked(registerClientBrowserDevice).mockRejectedValue(new ApiRequestError(
      status === 401 ? '请重新登录' : '设备密钥不匹配',
      { status, code },
    ));
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    expect(await screen.findByText(title === '账号登录已失效' ? '监控台登录已失效' : '浏览器设备注册冲突')).toBeInTheDocument();
    expect(createClientBrowserLoginSession).not.toHaveBeenCalled();
  });

  it('offers SMS login in the current device without collecting the code', async () => {
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '手机号验证码' }));

    expect(await screen.findByText('在当前设备浏览器完成手机号验证码登录')).toBeInTheDocument();
    expect(screen.queryByLabelText('短信验证码')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('用于在官方页面预填')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '在当前设备浏览器继续' }));

    await waitFor(() => {
    expect(createClientBrowserLoginSession).toHaveBeenCalledWith(
      'helper_device_123456', 'sms', 'native_helper',
    );
    });
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('offers all five local-admin login entries and submits manual Cookie without an account id', async () => {
    vi.mocked(addAccountCookie).mockResolvedValue({
      success: true,
      message: 'Cookie 已保存',
      account_id: '9988',
    });
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByRole('dialog', { name: '添加账号' });

    expect(screen.getByRole('button', { name: '扫码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '手机号验证码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '账号密码' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    expect(screen.getByRole('button', { name: '你的 Chrome' })).toBeInTheDocument();
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

  it('offers current-device QR, web QR, SMS, and advanced manual import to remote users', async () => {
    vi.mocked(createBrowserExtensionPairing).mockResolvedValue({
      pairing_id: 'pairing-id',
      protocol_version: 2,
      pairing_token: 'T'.repeat(43),
      status: 'waiting',
      message: '等待 Chrome 扩展导入',
      expires_at: 9_999_999_999,
      import_url: 'https://xianyu.cxywjx.top/api/browser-extension/import',
      console_origin: 'https://xianyu.cxywjx.top',
    });
    vi.mocked(getBrowserExtensionPairing).mockResolvedValue({
      pairing_id: 'pairing-id',
      protocol_version: 2,
      status: 'waiting',
      message: '等待 Chrome 扩展导入',
      expires_at: 9_999_999_999,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByRole('button', { name: '手机号验证码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '本机 Chrome 登录' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '网页二维码' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '服务器运维登录' })).not.toBeInTheDocument();
    expect(generateQRLogin).not.toHaveBeenCalled();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '你的 Chrome' }));
    expect(await screen.findByText('从你的 Chrome 导入')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载扩展 1.2.1' })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-browser-bridge-1.2.1.zip',
    );
    fireEvent.click(screen.getByRole('button', { name: '创建一次性配对' }));

    await waitFor(() => {
      expect(createBrowserExtensionPairing).toHaveBeenCalledTimes(1);
      expect(screen.getByLabelText('扩展配对信息')).toHaveValue(
        JSON.stringify({
          protocol_version: 2,
          pairing_id: 'pairing-id',
          pairing_token: 'T'.repeat(43),
          import_url: 'https://xianyu.cxywjx.top/api/browser-extension/import',
          console_origin: 'https://xianyu.cxywjx.top',
          expires_at: 9_999_999_999,
        }),
      );
    });
  });

  it('starts the API QR only after the user chooses the web QR entry', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(generateQRLogin).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,qr',
    );
    expect(generateQRLogin).toHaveBeenCalledTimes(1);
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '本机打开官方窗口' })).not.toBeInTheDocument();
  });

  it('hides an active web QR without cancelling until the user explicitly cancels', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByAltText('闲鱼登录二维码');

    fireEvent.click(screen.getByRole('button', { name: '关闭添加账号' }));
    expect(cancelQRLogin).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByAltText('闲鱼登录二维码')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '取消本次扫码' }));
    await waitFor(() => expect(cancelQRLogin).toHaveBeenCalledWith(
      'qr-session',
      'user_cancelled',
    ));
  });

  it('lets a local administrator start, show, and explicitly cancel server Chrome', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '服务器运维登录' }));

    await waitFor(() => {
      expect(createOfficialLoginSession).toHaveBeenCalledWith({
        mode: 'qr',
        show_browser: true,
      });
    });
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(generateQRLogin).not.toHaveBeenCalled();
    expect(await screen.findByText('请使用闲鱼 App 扫码')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新显示 Chrome 窗口' }));
    await waitFor(() => expect(showOfficialLoginBrowser).toHaveBeenCalledWith('official-session'));

    fireEvent.click(screen.getByRole('button', { name: '取消服务器扫码' }));
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));
  });

  it('hides the add modal without cancelling an active server Chrome session', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '服务器运维登录' }));
    await waitFor(() => expect(createOfficialLoginSession).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: '关闭添加账号' }));
    expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument();
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByText('请使用闲鱼 App 扫码')).toBeInTheDocument();
  });

  it('keeps a server Chrome session whose create response arrives after the modal hides', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    let resolveSession!: (value: Awaited<ReturnType<typeof createOfficialLoginSession>>) => void;
    vi.mocked(createOfficialLoginSession).mockImplementation(() => new Promise((resolve) => {
      resolveSession = resolve;
    }));
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '服务器运维登录' }));
    await waitFor(() => expect(createOfficialLoginSession).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '关闭添加账号' }));

    resolveSession({
      success: true,
      session_id: 'late-official-session',
      mode: 'qr',
      state: 'waiting_user',
      message: '迟到的扫码会话',
      error_code: '',
    });

    await waitFor(() => expect(cancelOfficialLoginSession).not.toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByText('迟到的扫码会话')).toBeInTheDocument();
  });

  it('requires confirmation before ending server Chrome to switch methods', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '服务器运维登录' }));
    await waitFor(() => expect(createOfficialLoginSession).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.getByText('请使用闲鱼 App 扫码')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));
    expect(await screen.findByText(/普通用户的账号、密码、滑块和人脸验证/)).toBeInTheDocument();
    expect(confirmSpy).toHaveBeenCalledTimes(4);
  });

  it('stops Chrome QR polling and refreshes accounts after success', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(getOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'official-session',
      mode: 'qr',
      state: 'success',
      message: '官方 Chrome 扫码成功',
      error_code: '',
      account_id: 'account-1',
      is_new_account: false,
    });
    render(<AccountList isAdmin />);

    await screen.findByText('可自动续期 · 定时关闭');
    const initialAccountLoads = vi.mocked(getAccountDetails).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '服务器运维登录' }));

    await waitFor(() => expect(getOfficialLoginSession).toHaveBeenCalledTimes(1), {
      timeout: 3500,
    });
    await waitFor(() => expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument(), {
      timeout: 2500,
    });
    expect(getOfficialLoginSession).toHaveBeenCalledTimes(1);
    expect(vi.mocked(getAccountDetails).mock.calls.length).toBeGreaterThan(initialAccountLoads);
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('does not create or cancel an official browser session for ordinary QR', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const view = render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByAltText('闲鱼登录二维码');
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));

    view.unmount();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
    expect(cancelQRLogin).toHaveBeenCalledWith('qr-session', 'switched_method');
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
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
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
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByText('二维码已过期');
    fireEvent.click(screen.getByRole('button', { name: '重新生成二维码' }));

    expect(await screen.findByAltText('闲鱼登录二维码')).toHaveAttribute(
      'src',
      '/static/uploads/images/fresh-qr.png',
    );
    expect(generateQRLogin).toHaveBeenCalledTimes(2);
  });

  it('keeps a mobile-scan verification image in the web QR flow', async () => {
    vi.mocked(checkQRLoginStatus).mockResolvedValue({
      status: 'verification_required',
      session_id: 'qr-session',
      message: '仍在等待人工验证',
      error_code: 'verification_required',
      verification_screenshot_path: '/static/uploads/images/verification.png',
      verification_kind: 'mobile_scan',
      required_action: 'scan_image',
      browser_active: true,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    expect(await screen.findByAltText('闲鱼安全验证页面', {}, { timeout: 2500 })).toHaveAttribute(
      'src',
      '/static/uploads/images/verification.png',
    );
    expect(screen.getByText('请按官方页面提示完成验证')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '本机打开官方窗口' })).not.toBeInTheDocument();
    expect(continueQRLoginAfterVerification).not.toHaveBeenCalled();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
  });

  it.each(['interactive', 'unknown'] as const)(
    'hands %s verification to the current-device browser and ends web QR',
    async (verificationKind) => {
      vi.mocked(checkQRLoginStatus).mockResolvedValue({
        status: 'verification_required',
        session_id: 'qr-session',
        message: '需要页面交互',
        verification_kind: verificationKind,
        required_action: 'interact_in_console',
        verification_screenshot_path: '/qr-login/verification-image/qr-session',
        interaction_supported: true,
        frame_revision: 1,
        browser_active: true,
      });
      render(<AccountList />);

      await screen.findByText('可自动续期 · 定时关闭');
      fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
      fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));

      const continueButton = await screen.findByRole(
        'button',
        { name: '在当前设备浏览器继续' },
        { timeout: 3500 },
      );
      expect(screen.queryByRole('region', { name: '闲鱼登录页面远程操作' })).not.toBeInTheDocument();
      fireEvent.click(continueButton);
      await waitFor(() => expect(cancelQRLogin).toHaveBeenCalledWith(
        'qr-session',
        'switched_to_extension',
      ));
      await waitFor(() => expect(createClientBrowserLoginSession).toHaveBeenCalledWith(
        'helper_device_123456',
        'qr',
        'native_helper',
      ), { timeout: 5000 });
      expect(startNativeBrowserLogin).toHaveBeenCalledWith(expect.objectContaining({ mode: 'qr' }));
      expect(clientBridgeRequests).not.toContain('XMC_GET_DEVICE');
      expect(clientBridgeRequests).not.toContain('XMC_START_LOGIN');
      expect(interactWithQRLogin).not.toHaveBeenCalled();
      expect(createOfficialLoginSession).not.toHaveBeenCalled();
      expect(createBrowserExtensionPairing).not.toHaveBeenCalled();
    },
  );

  it('keeps the success dialog open when the returned account is absent from the list', async () => {
    vi.mocked(checkQRLoginStatus).mockResolvedValue({
      status: 'success',
      session_id: 'qr-session',
      message: '闲鱼官方登录成功',
      account_info: { account_id: 'missing-account', is_new_account: true },
      ended_by: 'validated_and_persisted',
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));

    expect(await screen.findByText('账号保存结果尚未在列表中确认', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '添加账号' })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByRole('heading', { name: '添加账号' });

    await waitFor(() => expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument(), {
      timeout: 4000,
    });
    expect(checkQRLoginStatus).toHaveBeenCalledTimes(1);
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
  });

  it.each([
    ['qr_login', '重新扫码', 'button', '网页二维码'],
    ['sms_login', '验证码登录', 'text', '在当前设备浏览器完成手机号验证码登录'],
    ['password_login', '账号密码登录', 'text', '在当前设备浏览器完成账号密码登录'],
    ['chrome_extension_import', '重新导入', 'text', '从你的 Chrome 导入'],
    ['manual_cookie', '重新填写', 'placeholder', '粘贴从浏览器复制的 Cookie'],
    ['choose_login', '重新登录', 'button', '网页二维码'],
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

      const target = queryKind === 'button'
          ? await screen.findByRole('button', { name: expectedContent })
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

  it('loads shared reply strategies and saves all three in one request', async () => {
    render(<AccountList />);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('AI设置'));

    // 打开弹窗即加载共享策略（跨账号）
    await waitFor(() => expect(getAiReplyStrategies).toHaveBeenCalledTimes(1));

    // 折叠面板展开后才渲染策略内容
    const toggle = await screen.findByRole('button', { name: /高级回复策略/ });
    fireEvent.click(toggle);

    const priceTextarea = (await screen.findByDisplayValue('议价话术')) as HTMLTextAreaElement;
    fireEvent.change(priceTextarea, { target: { value: '新的议价话术' } });

    fireEvent.click(screen.getByRole('switch', { name: '启用技术专家' }));
    fireEvent.click(screen.getByRole('button', { name: '保存全部策略' }));

    await waitFor(() =>
      expect(updateAiReplyStrategies).toHaveBeenCalledWith(expect.arrayContaining([
        expect.objectContaining({ prompt_type: 'price', content: '新的议价话术', enabled: true }),
        expect.objectContaining({ prompt_type: 'tech', enabled: false }),
        expect.objectContaining({ prompt_type: 'default', enabled: true }),
      ])),
    );
    expect(await screen.findByText(/三类高级回复策略已统一保存/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue('新的议价话术')).not.toBeInTheDocument();
  });

  it('uses the unsaved-strategy confirmation from the AI modal close control', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<AccountList />);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('AI设置'));

    const strategyToggle = await screen.findByRole('button', { name: /高级回复策略/ });
    fireEvent.click(strategyToggle);
    fireEvent.change(await screen.findByDisplayValue('议价话术'), { target: { value: '尚未保存的话术' } });

    const closeButton = screen.getByRole('button', { name: '关闭 AI 设置' });
    expect(closeButton).toHaveClass('min-h-11', 'min-w-11');
    fireEvent.click(closeButton);

    expect(confirmSpy).toHaveBeenCalledWith('高级回复策略有未保存修改，确定放弃并关闭吗？');
    expect(screen.getByRole('heading', { name: 'AI助手设置' })).toBeInTheDocument();
  });
});
