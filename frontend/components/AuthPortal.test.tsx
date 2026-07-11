// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createAuthCaptcha,
  getRegistrationConfig,
  login,
  registerAccount,
  requestPasswordReset,
  sendAuthEmailCode,
} from '../services/api';
import AuthPortal from './AuthPortal';

vi.mock('../services/api', () => ({
  createAuthCaptcha: vi.fn(),
  getRegistrationConfig: vi.fn(),
  login: vi.fn(),
  registerAccount: vi.fn(),
  requestPasswordReset: vi.fn(),
  sendAuthEmailCode: vi.fn(),
}));

const readyConfig = {
  enabled: true,
  ready: true,
  invite_required: true,
  terms_version: 'v1',
  terms_url: '/terms',
  privacy_url: '/privacy',
  support_email: 'support@example.com',
  message: '邀请注册已开放',
};

describe('AuthPortal', () => {
  beforeEach(() => {
    const stored = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
      clear: () => stored.clear(),
    });
    window.history.replaceState({}, '', '/login');
    vi.mocked(getRegistrationConfig).mockResolvedValue(readyConfig);
    vi.mocked(createAuthCaptcha).mockResolvedValue({
      success: true,
      challenge_id: 'captcha-1',
      captcha_image: 'data:image/png;base64,c3ludGhldGlj',
      expires_in: 600,
    });
    vi.mocked(sendAuthEmailCode).mockResolvedValue({
      success: true,
      challenge_id: 'email-1',
      expires_in: 600,
      cooldown_seconds: 60,
      message: '验证码已发送，请查收邮件',
    });
    vi.mocked(login).mockResolvedValue({ success: true, token: 'login-token' });
    vi.mocked(registerAccount).mockResolvedValue({ success: true, token: 'register-token' });
    vi.mocked(requestPasswordReset).mockResolvedValue({ success: true, message: '密码已重置，请重新登录' });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('logs in with a unified username or email identifier', async () => {
    const onAuthenticated = vi.fn();
    render(<AuthPortal onAuthenticated={onAuthenticated} />);

    fireEvent.change(screen.getByLabelText('用户名或邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(login).toHaveBeenCalledWith({
      identifier: 'pilot@example.com',
      password: 'Pilot-pass-2026!',
    }));
    expect(onAuthenticated).toHaveBeenCalledWith('login-token');
    expect(window.location.pathname).toBe('/');
  });

  it('sends a registration email code and automatically logs in after registration', async () => {
    window.history.replaceState({}, '', '/register');
    const onAuthenticated = vi.fn();
    render(<AuthPortal onAuthenticated={onAuthenticated} />);

    expect(await screen.findByAltText('图形验证码')).toHaveAttribute('src', expect.stringContaining('data:image/png'));
    fireEvent.change(screen.getByLabelText('邀请码'), { target: { value: 'INVITE-ONE' } });
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'Pilot@Example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));

    await waitFor(() => expect(sendAuthEmailCode).toHaveBeenCalledWith({
      purpose: 'register',
      email: 'Pilot@Example.com',
      invite_code: 'INVITE-ONE',
      captcha_challenge_id: 'captcha-1',
      captcha_code: 'AB12',
    }));
    expect(screen.getByRole('button', { name: /秒后重试/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '482615' } });
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'pilot-user' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /服务条款和隐私说明/ }));
    fireEvent.click(screen.getByRole('button', { name: '完成注册' }));

    await waitFor(() => expect(registerAccount).toHaveBeenCalledWith({
      invite_code: 'INVITE-ONE',
      email: 'Pilot@Example.com',
      challenge_id: 'email-1',
      verification_code: '482615',
      username: 'pilot-user',
      password: 'Pilot-pass-2026!',
      terms_version: 'v1',
      terms_accepted: true,
    }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith('register-token'));
  });

  it('shows a fail-closed registration state when registration is unavailable', async () => {
    window.history.replaceState({}, '', '/register');
    vi.mocked(getRegistrationConfig).mockResolvedValue({
      ...readyConfig,
      enabled: false,
      ready: false,
      message: '邀请注册暂未开放',
    });

    render(<AuthPortal onAuthenticated={vi.fn()} />);

    expect(await screen.findByText('邀请注册暂未开放')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送邮件验证码' })).toBeDisabled();
    expect(createAuthCaptcha).not.toHaveBeenCalled();
  });

  it('resets a password and returns to the login route', async () => {
    window.history.replaceState({}, '', '/forgot-password');
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'CD34' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('验证码已发送，请查收邮件');

    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '654321' } });
    fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'Changed-pass-2026!' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'Changed-pass-2026!' } });
    fireEvent.click(screen.getByRole('button', { name: '重置密码' }));

    await waitFor(() => expect(requestPasswordReset).toHaveBeenCalledWith({
      email: 'pilot@example.com',
      challenge_id: 'email-1',
      verification_code: '654321',
      new_password: 'Changed-pass-2026!',
    }));
    expect(await screen.findByText('密码已重置，请重新登录')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
  });

  it('supports direct legal routes and history navigation between auth views', async () => {
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '邀请注册' }));
    expect(window.location.pathname).toBe('/register');
    expect(await screen.findByRole('heading', { name: '创建受邀账号' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '忘记密码' }));
    expect(window.location.pathname).toBe('/forgot-password');
    expect(await screen.findByRole('heading', { name: '找回账号' })).toBeInTheDocument();

    window.history.pushState({}, '', '/privacy');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(await screen.findByRole('heading', { name: '隐私说明' })).toBeInTheDocument();
    expect(screen.getByText(/support@example.com/)).toBeInTheDocument();
  });
});
