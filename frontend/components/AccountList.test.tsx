// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AccountList from './AccountList';
import ConfirmDialogHost, { clearConfirmDialogs } from './ui/ConfirmDialog';
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
  updateAccountAutoRate,
  updateAccountCookieRefreshSettings,
  updateAccountProxy,
  testAccountProxy,
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
  updateAccountAutoRate: vi.fn(),
  updateAccountPauseDuration: vi.fn(),
  updateAccountCookie: vi.fn(),
  updateAccountLoginInfo: vi.fn(),
  updateAccountCookieRefreshSettings: vi.fn(),
  updateAccountProxy: vi.fn(),
  testAccountProxy: vi.fn(),
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

// canUseServerBrowser 由 window.location 决定：回环弹本机窗口，正式控制台域名
// （xianyu.cxywjx.top）显示云端嵌入画面。jsdom 默认 hostname 是 localhost（回环）；
// 构造“远程用户”场景必须改写 location 为陌生域名，afterEach 用 vi.unstubAllGlobals() 恢复。
const stubRemoteConsoleHostname = (hostname = 'remote.example.com') => {
  vi.stubGlobal('location', { ...window.location, hostname, host: hostname });
};

// 正式控制台域名场景：使用服务端 Chrome，但画面只在当前网页内显示。
const stubOfficialConsoleHostname = () => {
  vi.stubGlobal('location', { ...window.location, hostname: 'xianyu.cxywjx.top', host: 'xianyu.cxywjx.top' });
};

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
          extensionVersion: '1.2.3',
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
        auto_rate_enabled: true,
        auto_rate_success_count: 3,
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
    vi.mocked(createClientBrowserLoginSession).mockImplementation(async (_deviceId, mode) => ({
      session_id: `${mode}-client-session`,
      device_id: 'device_fixture_1234',
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
    clearConfirmDialogs();
    cleanup();
    vi.clearAllMocks();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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
    expect(screen.getAllByTitle('通知绑定设备续期').length).toBeGreaterThan(0);

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

  it('saves a per-account residential proxy from the edit modal', async () => {
    vi.mocked(updateAccountProxy).mockResolvedValue({
      success: true,
      message: '代理配置已保存',
      data: {
        proxy_enabled: true,
        proxy_server: 'http://gw:1000',
        proxy_username: 'u1',
        proxy_password_set: true,
        proxy_region: '上海',
        proxy_last_ip: '',
        proxy_last_status: '',
        proxy_last_check_at: null,
      },
    });

    render(<AccountList />);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    fireEvent.click(await screen.findByLabelText('启用住宅代理'));
    fireEvent.change(screen.getByLabelText('代理服务器'), { target: { value: 'http://gw:1000' } });
    fireEvent.change(screen.getByLabelText('账号'), { target: { value: 'u1' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(updateAccountProxy).toHaveBeenCalledWith('account-1', {
        proxy_enabled: true,
        proxy_server: 'http://gw:1000',
        proxy_username: 'u1',
        proxy_password: 'secret',
        proxy_region: '',
      });
    });
  });

  it('tests proxy connectivity and shows the egress IP', async () => {
    vi.mocked(updateAccountProxy).mockResolvedValue({
      success: true,
      message: '代理配置已保存',
      data: {
        proxy_enabled: true,
        proxy_server: 'http://gw:1000',
        proxy_username: '',
        proxy_password_set: false,
        proxy_region: '',
        proxy_last_ip: '',
        proxy_last_status: '',
        proxy_last_check_at: null,
      },
    });
    vi.mocked(testAccountProxy).mockResolvedValue({
      success: true,
      data: { ok: true, ip: '203.0.113.9', status: 'ok', error: '' },
    });

    render(<AccountList />);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    fireEvent.click(await screen.findByLabelText('启用住宅代理'));
    fireEvent.change(screen.getByLabelText('代理服务器'), { target: { value: 'http://gw:1000' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并测试连通性' }));

    await waitFor(() => expect(testAccountProxy).toHaveBeenCalledWith('account-1'));
    expect(await screen.findByText(/出口 IP：203\.0\.113\.9/)).toBeInTheDocument();
  });

  it('lets a QR account with browser memory enable scheduled refresh', async () => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      {
        id: 'qr-l3',
        enabled: true,
        auto_confirm: false,
        remark: '扫码记忆账号',
        note: '扫码记忆账号',
        pause_duration: 0,
        nickname: '扫码记忆账号',
        login_method: 'qr',
        login_method_label: '扫码登录',
        auto_refresh_supported: true,
        has_l3_memory: true,
        cookie_refresh_enabled: false,
        cookie_refresh_interval_minutes: 1440,
        reauth_required: false,
        reauth_action: 'qr_login',
      } as any,
    ]);

    render(<AccountList />);
    await screen.findByText('可自动续期 · 定时关闭');

    const accountCard = screen.getByRole('heading', { name: '扫码记忆账号' }).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));

    await screen.findByText('已建立浏览器登录记忆');
    expect(screen.getByLabelText('自动定时 Cookie 刷新')).not.toBeDisabled();
  });

  it('saves seller auto-review as an explicit per-account opt-in', async () => {
    render(<AccountList />);

    const accountCard = (await screen.findByRole('heading', { name: '其他账号' })).closest('.ios-card');
    expect(accountCard).not.toBeNull();
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('编辑账号'));
    fireEvent.click(await screen.findByLabelText('卖家自动好评'));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(updateAccountAutoRate).toHaveBeenCalledWith('account-2', true);
    });
  });

  it('shows completed auto-reviews without a fixed denominator', async () => {
    render(<AccountList />);

    expect(await screen.findByText('自动好评 已成功 3 条')).toBeInTheDocument();
    expect(screen.queryByText('自动好评 3/3')).not.toBeInTheDocument();
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
    const refreshButton = within(accountCard as HTMLElement).getByTitle('通知绑定设备续期');
    act(() => {
      fireEvent.click(refreshButton);
      fireEvent.click(refreshButton);
    });

    await waitFor(() => expect(refreshAccountSession).toHaveBeenCalledTimes(1));
  });

  it('starts password login in the current device without collecting credentials in the console', async () => {
    // 扩展桥接的当前设备通道属于陌生域名远程场景；本机场景走服务端 Chrome。
    stubRemoteConsoleHostname();
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
        'device_fixture_1234', 'password', 'extension',
      );
    });
    // 本机助手已移除：当前设备通道由扩展桥接承接
    await waitFor(() => expect(clientBridgeRequests).toContain('XMC_START_LOGIN'));
    expect(clientBridgeRequests).toContain('XMC_GET_DEVICE');
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
    stubRemoteConsoleHostname();
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
    expect(clientBridgeRequests).toContain('XMC_CONFIRM_LOGIN');
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    // 扩展密码登录成功后提供独立的续期授权面板；未显式授权前不保存任何凭据。
    expect(await screen.findByText('是否在此设备启用自动续期')).toBeInTheDocument();
    expect(bindAccountRenewalDevice).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: '添加账号' })).toBeInTheDocument();
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

  it('does not create any login session when the extension bridge is missing on a remote console', async () => {
    // 回环下主按钮直接走服务端 Chrome；远程用户的当前设备通道由扩展桥接承接。
    // 本机助手已彻底移除：扩展缺失时只弹扩展安装引导。
    stubRemoteConsoleHostname();
    clientBridgeEnabled = false;
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    expect((await screen.findAllByText(
      '当前设备浏览器登录由扩展承接：安装并启用扩展后，重新点击登录入口即可。',
      {},
      { timeout: 6500 },
    )).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '安装浏览器扩展' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '改用网页二维码' })).toBeInTheDocument();
    // 安装引导弹窗在扩展桥 3.5 秒超时后才自动弹出，需要更长等待
    expect(await screen.findByRole('link', { name: /下载浏览器扩展/ }, { timeout: 6500 })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-browser-bridge-1.2.3.zip',
    );
    expect(createClientBrowserLoginSession).not.toHaveBeenCalled();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(createBrowserExtensionPairing).not.toHaveBeenCalled();
  });

  it.each([
    [401, 'auth_expired', '账号登录已失效'],
    [409, 'device_key_mismatch', '设备注册冲突'],
  ])('classifies device registration status %s', async (status, code, title) => {
    vi.mocked(registerClientBrowserDevice).mockRejectedValue(new ApiRequestError(
      status === 401 ? '请重新登录' : '设备密钥不匹配',
      { status, code },
    ));
    // 设备注册发生在扩展桥接通道：需要非回环 hostname 才会走到这一步
    stubRemoteConsoleHostname();
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    expect(await screen.findByText(title === '账号登录已失效' ? '监控台登录已失效' : '浏览器设备注册冲突')).toBeInTheDocument();
    expect(createClientBrowserLoginSession).not.toHaveBeenCalled();
  });

  it('offers SMS login in the current device without collecting the code', async () => {
    stubRemoteConsoleHostname();
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(await screen.findByRole('button', { name: '手机号验证码' }));

    expect(await screen.findByText('在当前设备浏览器完成手机号验证码登录')).toBeInTheDocument();
    expect(screen.queryByLabelText('短信验证码')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('用于在官方页面预填')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '在当前设备浏览器继续' }));

    await waitFor(() => {
    expect(createClientBrowserLoginSession).toHaveBeenCalledWith(
      'device_fixture_1234', 'sms', 'extension',
    );
    });
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
  });

  it('offers the loopback login entries without a server ops button and submits manual Cookie without an account id', async () => {
    vi.mocked(addAccountCookie).mockResolvedValue({
      success: true,
      message: 'Cookie 已保存',
      account_id: '9988',
    });
    // isAdmin 已无效果：回环控制台对任何角色都提供同一组登录入口
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    await screen.findByRole('dialog', { name: '添加账号' });

    expect(screen.getByText('推荐用手机扫“网页二维码”登录，零安装最稳定；也可用“本机 Chrome 登录”窗口作为备选。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '扫码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '手机号验证码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '账号密码' })).toBeInTheDocument();
    // 网页二维码是回环下的推荐主入口；本机 Chrome 登录降为备选按钮
    expect(screen.getByRole('button', { name: '网页二维码' })).toBeInTheDocument();
    expect(screen.getByText('网页二维码（推荐）')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '本机 Chrome 登录' })).toBeInTheDocument();
    expect(screen.getByText('本机 Chrome 登录（备选）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    // 高级区只剩“你的 Chrome”和“手填 Cookie”，红色“服务器运维登录”按钮已移除
    expect(screen.getByRole('button', { name: '你的 Chrome' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '服务器运维登录' })).not.toBeInTheDocument();
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

  it('starts embedded cloud Chrome on the official hostname without exposing a server window', async () => {
    stubOfficialConsoleHostname();
    vi.mocked(createOfficialLoginSession).mockResolvedValue({
      success: true,
      session_id: 'official-session',
      mode: 'qr',
      state: 'waiting_user',
      message: '请扫描网页中的云端 Chrome 二维码',
      error_code: '',
      qr_image_url: '/api/official-login/sessions/official-session/image',
      verification_image_url: '',
      verification_kind: 'mobile_scan',
      required_action: 'scan_image',
      interaction_supported: true,
      frame_revision: 4,
      account_id: '',
      is_new_account: false,
      created_at: 1,
      updated_at: 1,
      expires_at: 9999999999,
    });
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '云端 Chrome 登录' }));

    await waitFor(() => expect(createOfficialLoginSession).toHaveBeenCalledWith({
      mode: 'qr',
      show_browser: false,
    }));
    expect(clientBridgeRequests).not.toContain('XMC_GET_DEVICE');
    expect(await screen.findByText('云端 Chrome 登录')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '闲鱼登录页面远程操作' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新显示 Chrome 窗口' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('向闲鱼页面输入文字'), {
      target: { value: '482615' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送到闲鱼页面' }));
    await waitFor(() => expect(interactWithOfficialLogin).toHaveBeenCalledWith(
      'official-session',
      { kind: 'text', frame_revision: 4, text: '482615' },
    ));
  });

  it('offers current-device QR, web QR, SMS, and advanced manual import to remote users', async () => {
    // 远程用户 = 非回环 hostname（不再靠 isAdmin=false 构造）
    stubRemoteConsoleHostname();
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
    // 非回环副标题保持旧文案，也不出现回环专属的“推荐 · 免安装”
    expect(screen.getByText('登录和安全验证在你当前的 Chrome 或 Edge 中完成。')).toBeInTheDocument();
    expect(screen.queryByText(/推荐 · 免安装/)).not.toBeInTheDocument();
    expect(generateQRLogin).not.toHaveBeenCalled();
    expect(createOfficialLoginSession).not.toHaveBeenCalled();

    // 远程用户点主按钮走扩展桥接通道，不创建服务端 Chrome 会话
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));
    await waitFor(() => expect(createClientBrowserLoginSession).toHaveBeenCalledWith(
      'device_fixture_1234', 'qr', 'extension',
    ), { timeout: 5000 });
    await waitFor(() => expect(clientBridgeRequests).toContain('XMC_START_LOGIN'));
    expect(clientBridgeRequests).toContain('XMC_GET_DEVICE');
    expect(createOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.queryByText('本机 Chrome 登录窗口')).not.toBeInTheDocument();
    expect(screen.queryByText('正在本机打开 Chrome 登录窗口')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '高级与运维方式' }));
    fireEvent.click(screen.getByRole('button', { name: '你的 Chrome' }));
    expect(await screen.findByText('从你的 Chrome 导入')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载扩展 1.2.3' })).toHaveAttribute(
      'href',
      '/static/downloads/xianyu-browser-bridge-1.2.3.zip',
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

  it('cancels an active web QR when the modal closes and on explicit cancel', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByAltText('闲鱼登录二维码');

    // 关闭弹窗（右上角 X）现在会主动取消进行中的网页二维码会话并完整清理
    fireEvent.click(screen.getByRole('button', { name: '关闭添加账号' }));
    await waitFor(() => expect(cancelQRLogin).toHaveBeenCalledWith(
      'qr-session',
      'user_cancelled',
    ));
    expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument();

    // 重新打开回到扫码方式选择器，不会复用已取消的二维码会话
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByRole('button', { name: '本机 Chrome 登录' })).toBeInTheDocument();
    expect(screen.queryByAltText('闲鱼登录二维码')).not.toBeInTheDocument();
    expect(generateQRLogin).toHaveBeenCalledTimes(1);

    // 显式“取消本次扫码”仍以 user_cancelled 结束会话
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByAltText('闲鱼登录二维码');
    fireEvent.click(screen.getByRole('button', { name: '取消本次扫码' }));
    await waitFor(() => expect(cancelQRLogin).toHaveBeenCalledTimes(2));
    expect(cancelQRLogin).toHaveBeenLastCalledWith('qr-session', 'user_cancelled');
  });

  it('lets a loopback user start, show, and explicitly cancel server Chrome without any confirm', async () => {
    // 启动服务端 Chrome 不再需要任何确认弹窗
    render(<><AccountList /><ConfirmDialogHost /></>);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

    await waitFor(() => {
      expect(createOfficialLoginSession).toHaveBeenCalledWith({
        mode: 'qr',
        show_browser: true,
      });
    });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(generateQRLogin).not.toHaveBeenCalled();
    expect(await screen.findByText('请使用闲鱼 App 扫码')).toBeInTheDocument();
    expect(screen.getByText('本机 Chrome 登录窗口')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新显示 Chrome 窗口' }));
    await waitFor(() => expect(showOfficialLoginBrowser).toHaveBeenCalledWith('official-session'));

    fireEvent.click(screen.getByRole('button', { name: '取消服务器扫码' }));
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));
  });

  it('cancels an active server Chrome session when the add modal is closed', async () => {
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));
    expect(await screen.findByText('请使用闲鱼 App 扫码')).toBeInTheDocument();

    // 关闭弹窗（右上角 X）现在会主动取消活跃的服务端 Chrome 会话并完整清理
    fireEvent.click(screen.getByRole('button', { name: '关闭添加账号' }));
    expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument();
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));

    // 重新打开回到扫码方式选择器，不会复用已取消的会话
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByRole('button', { name: '本机 Chrome 登录' })).toBeInTheDocument();
    expect(screen.queryByText('请使用闲鱼 App 扫码')).not.toBeInTheDocument();
  });

  it('cancels a server Chrome session whose create response arrives after the modal closes', async () => {
    let resolveSession!: (value: Awaited<ReturnType<typeof createOfficialLoginSession>>) => void;
    vi.mocked(createOfficialLoginSession).mockImplementation(() => new Promise((resolve) => {
      resolveSession = resolve;
    }));
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));
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

    // 弹窗已关闭并整体重置：迟到才建立的会话会被立即取消，而不是保留复用
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('late-official-session'));
    expect(screen.queryByRole('heading', { name: '添加账号' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    expect(await screen.findByRole('button', { name: '本机 Chrome 登录' })).toBeInTheDocument();
    expect(screen.queryByText('迟到的扫码会话')).not.toBeInTheDocument();
  });

  it('requires confirmation before ending server Chrome to switch methods', async () => {
    // 启动不弹确认；只有「切换方式会结束本次会话」的确认弹窗仍然保留
    render(<><AccountList /><ConfirmDialogHost /></>);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));
    expect(await screen.findByText('请使用闲鱼 App 扫码')).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

    // 第一次切换：在确认弹窗里点「取消」，会话保持不动
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('当前登录仍在进行，切换方式会结束本次会话。是否继续？');
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(cancelOfficialLoginSession).not.toHaveBeenCalled();
    expect(screen.getByText('请使用闲鱼 App 扫码')).toBeInTheDocument();

    // 第二次切换：确认后结束原会话并进入账号密码方式
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    const secondDialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(secondDialog).getByRole('button', { name: '继续切换' }));
    await waitFor(() => expect(cancelOfficialLoginSession).toHaveBeenCalledWith('official-session'));
    expect(await screen.findByText(/账号、密码、滑块和人脸验证只在该窗口输入/)).toBeInTheDocument();
  });

  it('stops Chrome QR polling and refreshes accounts after success', async () => {
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
    render(<AccountList />);

    await screen.findByText('可自动续期 · 定时关闭');
    const initialAccountLoads = vi.mocked(getAccountDetails).mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    // 回环下服务端 Chrome 由扫码选择器主按钮直接触发，无需 confirm
    fireEvent.click(screen.getByRole('button', { name: '本机 Chrome 登录' }));

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
    const view = render(<><AccountList /><ConfirmDialogHost /></>);

    await screen.findByText('可自动续期 · 定时关闭');
    fireEvent.click(screen.getByRole('button', { name: '添加账号' }));
    fireEvent.click(screen.getByRole('button', { name: '网页二维码' }));
    await screen.findByAltText('闲鱼登录二维码');
    fireEvent.click(screen.getByRole('button', { name: '账号密码' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '继续切换' }));
    await waitFor(() => expect(cancelQRLogin).toHaveBeenCalledWith('qr-session', 'switched_method'));

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
      // 交互验证交给扩展桥接属于陌生域名远程场景；本机场景改弹服务端 Chrome。
      stubRemoteConsoleHostname();
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
        'device_fixture_1234',
        'qr',
        'extension',
      ), { timeout: 5000 });
      await waitFor(() => expect(clientBridgeRequests).toContain('XMC_START_LOGIN'));
      expect(clientBridgeRequests).toContain('XMC_GET_DEVICE');
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
    ['sms_login', '验证码登录', 'text', '在本机 Chrome 窗口完成手机号验证码登录'],
    ['password_login', '账号密码登录', 'text', '在本机 Chrome 窗口完成账号密码登录'],
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
    render(<><AccountList /><ConfirmDialogHost /></>);
    const accountCard = (await screen.findByRole('heading', { name: '验证账号' })).closest('.ios-card');
    fireEvent.click(within(accountCard as HTMLElement).getByTitle('AI设置'));

    const strategyToggle = await screen.findByRole('button', { name: /高级回复策略/ });
    fireEvent.click(strategyToggle);
    fireEvent.change(await screen.findByDisplayValue('议价话术'), { target: { value: '尚未保存的话术' } });

    const closeButton = screen.getByRole('button', { name: '关闭 AI 设置' });
    expect(closeButton).toHaveClass('min-h-11', 'min-w-11');
    fireEvent.click(closeButton);

    // 未保存修改 → 弹出统一确认弹窗；点「取消」后弹窗关闭、AI 设置保持打开
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('高级回复策略有未保存修改，确定放弃并关闭吗？');
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'AI助手设置' })).toBeInTheDocument();

    // 再次关闭并确认「放弃修改」→ AI 设置弹窗关闭
    fireEvent.click(screen.getByRole('button', { name: '关闭 AI 设置' }));
    const secondDialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(secondDialog).getByRole('button', { name: '放弃修改' }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'AI助手设置' })).not.toBeInTheDocument());
  });

  it('does not poll account session status while the document is hidden', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');

    render(<AccountList />);
    await screen.findByRole('heading', { name: '验证账号' });
    await act(async () => {
      await Promise.resolve();
    });

    expect(getAccountSessionStatus).not.toHaveBeenCalled();

    visibility.mockReturnValue('visible');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await waitFor(() => expect(getAccountSessionStatus).toHaveBeenCalledTimes(2));
  });

  it('uses a fifteen-second stable-state interval instead of polling every three seconds', async () => {
    vi.useFakeTimers();
    render(<AccountList />);
    await act(async () => {
      for (let index = 0; index < 6; index += 1) await Promise.resolve();
    });

    expect(getAccountSessionStatus).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(getAccountSessionStatus).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(12_000);
    });
    expect(getAccountSessionStatus).toHaveBeenCalledTimes(4);
  });

  it('never starts another status poll while the previous poll is in flight', async () => {
    vi.useFakeTimers();
    const resolvers: Array<(status: any) => void> = [];
    vi.mocked(getAccountSessionStatus).mockImplementation(() => new Promise((resolve) => {
      resolvers.push(resolve);
    }));

    render(<AccountList />);
    await act(async () => {
      for (let index = 0; index < 6; index += 1) await Promise.resolve();
    });

    expect(getAccountSessionStatus).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getAccountSessionStatus).toHaveBeenCalledTimes(2);

    await act(async () => {
      resolvers.forEach((resolve) => resolve({
        state: 'idle',
        trigger: '',
        message: '',
        error_code: '',
        verification_image_url: '',
      }));
      await Promise.resolve();
    });
  });
});
