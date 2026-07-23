// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createAuthCaptcha,
  getRegistrationConfig,
  login,
  registerAccount,
  requestPasswordReset,
  sendAuthEmailCode,
  verifyPasswordResetCode,
} from '../services/api';
import { ApiRequestError } from '../services/request';
import type { PasswordResetVerifyResponse } from '../types';
import AuthPortal from './AuthPortal';

vi.mock('../services/api', () => ({
  createAuthCaptcha: vi.fn(),
  getRegistrationConfig: vi.fn(),
  login: vi.fn(),
  registerAccount: vi.fn(),
  requestPasswordReset: vi.fn(),
  sendAuthEmailCode: vi.fn(),
  verifyPasswordResetCode: vi.fn(),
}));

const readyConfig = {
  enabled: true,
  ready: true,
  invite_required: false,
  terms_version: 'v2',
  terms_url: '/terms',
  privacy_url: '/privacy',
  support_email: 'support@example.com',
  message: '注册已开放',
};

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
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
    vi.mocked(verifyPasswordResetCode).mockResolvedValue({
      success: true,
      reset_grant_id: 'grant-1',
      reset_grant_token: 'grant-token-1',
      expires_in: 600,
      message: '邮箱验证成功',
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
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
    expect(document.title).toBe('闲鱼智控 - 自动化控制台');
  });

  it('sends a registration email code and automatically logs in after registration', async () => {
    window.history.replaceState({}, '', '/register');
    const onAuthenticated = vi.fn();
    render(<AuthPortal onAuthenticated={onAuthenticated} />);

    expect(await screen.findByAltText('图形验证码')).toHaveAttribute('src', expect.stringContaining('data:image/png'));
    expect(screen.queryByLabelText('邀请码')).not.toBeInTheDocument();
    expect(screen.queryByText(/邀请/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'Pilot@Example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));

    await waitFor(() => expect(sendAuthEmailCode).toHaveBeenCalledWith({
      purpose: 'register',
      email: 'Pilot@Example.com',
      captcha_challenge_id: 'captcha-1',
      captcha_code: 'AB12',
    }));
    expect(createAuthCaptcha).toHaveBeenCalledTimes(1);
    expect(screen.getByText('图形验证已通过，邮件已发送')).toBeInTheDocument();
    expect(screen.queryByAltText('图形验证码')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /秒后重试/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '482615' } });
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'pilot-user' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /服务条款和隐私说明/ }));
    fireEvent.click(screen.getByRole('button', { name: '完成注册' }));

    await waitFor(() => expect(registerAccount).toHaveBeenCalledWith({
      email: 'Pilot@Example.com',
      challenge_id: 'email-1',
      verification_code: '482615',
      username: 'pilot-user',
      password: 'Pilot-pass-2026!',
      terms_version: 'v2',
      terms_accepted: true,
    }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith('register-token'));
  });

  it('keeps the current CAPTCHA after a wrong answer and clears only its input', async () => {
    window.history.replaceState({}, '', '/register');
    vi.mocked(sendAuthEmailCode).mockRejectedValueOnce(new ApiRequestError('图形验证码错误', {
      code: 'CHALLENGE_SECRET_INVALID',
      status: 400,
    }));
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    const captchaImage = await screen.findByAltText('图形验证码');
    const originalSource = captchaImage.getAttribute('src');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'WRONG' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));

    expect(await screen.findByText('图形验证码错误')).toBeInTheDocument();
    expect(screen.getByAltText('图形验证码')).toHaveAttribute('src', originalSource);
    expect(screen.getByLabelText('图形验证码')).toHaveValue('');
    expect(createAuthCaptcha).toHaveBeenCalledTimes(1);
  });

  it('requires an explicit fresh CAPTCHA for resend and preserves the old email challenge until resend succeeds', async () => {
    window.history.replaceState({}, '', '/register');
    vi.mocked(createAuthCaptcha)
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'captcha-1',
        captcha_image: 'data:image/png;base64,Zmlyc3Q=',
        expires_in: 600,
      })
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'captcha-2',
        captcha_image: 'data:image/png;base64,c2Vjb25k',
        expires_in: 600,
      });
    vi.mocked(sendAuthEmailCode)
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'email-1',
        expires_in: 600,
        cooldown_seconds: 0,
        message: '验证码已发送，请查收邮件',
      })
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'email-2',
        expires_in: 600,
        cooldown_seconds: 60,
        message: '新验证码已发送',
      });
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '111111' } });

    fireEvent.click(screen.getByRole('button', { name: '重新发送' }));
    expect(await screen.findByAltText('图形验证码')).toHaveAttribute('src', expect.stringContaining('c2Vjb25k'));
    expect(screen.getByLabelText('邮件验证码')).toHaveValue('111111');
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'CD34' } });
    fireEvent.click(screen.getByRole('button', { name: '重新发送验证码' }));

    await waitFor(() => expect(sendAuthEmailCode).toHaveBeenCalledTimes(2));
    expect(sendAuthEmailCode).toHaveBeenLastCalledWith({
      purpose: 'register',
      email: 'pilot@example.com',
      captcha_challenge_id: 'captcha-2',
      captcha_code: 'CD34',
    });
    await waitFor(() => expect(screen.getByLabelText('邮件验证码')).toHaveValue(''));
  });

  it('keeps the previous email challenge usable when an explicit resend fails', async () => {
    window.history.replaceState({}, '', '/register');
    const onAuthenticated = vi.fn();
    vi.mocked(sendAuthEmailCode)
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'email-original',
        expires_in: 600,
        cooldown_seconds: 0,
        message: '验证码已发送，请查收邮件',
      })
      .mockRejectedValueOnce(new ApiRequestError('邮件发送暂时失败', {
        code: 'EMAIL_SEND_FAILED',
        status: 502,
      }));
    render(<AuthPortal onAuthenticated={onAuthenticated} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '111111' } });
    fireEvent.click(screen.getByRole('button', { name: '重新发送' }));
    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'CD34' } });
    fireEvent.click(screen.getByRole('button', { name: '重新发送验证码' }));

    expect(await screen.findByText('邮件发送暂时失败')).toBeInTheDocument();
    expect(screen.getByLabelText('邮件验证码')).toHaveValue('111111');
    expect(screen.getByLabelText('邮件验证码')).not.toBeDisabled();
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'pilot-user' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'Pilot-pass-2026!' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /服务条款和隐私说明/ }));
    fireEvent.click(screen.getByRole('button', { name: '完成注册' }));

    await waitFor(() => expect(registerAccount).toHaveBeenCalledWith(expect.objectContaining({
      challenge_id: 'email-original',
      verification_code: '111111',
    })));
    expect(onAuthenticated).toHaveBeenCalledWith('register-token');
  });

  it('requires an explicit CAPTCHA reload after a terminal CAPTCHA error', async () => {
    window.history.replaceState({}, '', '/register');
    vi.mocked(sendAuthEmailCode).mockRejectedValueOnce(new ApiRequestError('图形验证码已过期', {
      code: 'CHALLENGE_EXPIRED',
      status: 400,
    }));
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));

    expect(await screen.findByText('图形验证码已过期')).toBeInTheDocument();
    expect(screen.queryByAltText('图形验证码')).not.toBeInTheDocument();
    expect(createAuthCaptcha).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新图形验证码' }));
    expect(await screen.findByAltText('图形验证码')).toBeInTheDocument();
    expect(createAuthCaptcha).toHaveBeenCalledTimes(2);
  });

  it('locks email controls while a resend request is in flight', async () => {
    window.history.replaceState({}, '', '/register');
    const pending = deferred<Awaited<ReturnType<typeof sendAuthEmailCode>>>();
    vi.mocked(sendAuthEmailCode)
      .mockResolvedValueOnce({
        success: true,
        challenge_id: 'email-original',
        expires_in: 600,
        cooldown_seconds: 0,
        message: '验证码已发送，请查收邮件',
      })
      .mockReturnValueOnce(pending.promise);
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.click(screen.getByRole('button', { name: '重新发送' }));
    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'CD34' } });
    fireEvent.click(screen.getByRole('button', { name: '重新发送验证码' }));

    expect(screen.getByRole('button', { name: '修改邮箱' })).toBeDisabled();
    expect(screen.getByLabelText('邮箱')).toBeDisabled();
    expect(screen.getByRole('button', { name: '刷新图形验证码' })).toBeDisabled();
    await act(async () => {
      pending.resolve({
        success: true,
        challenge_id: 'email-new',
        expires_in: 600,
        cooldown_seconds: 60,
        message: '新验证码已发送',
      });
      await pending.promise;
    });
    expect(await screen.findByText('新验证码已发送')).toBeInTheDocument();
  });

  it('clears verification state and loads a fresh CAPTCHA when the email is changed', async () => {
    window.history.replaceState({}, '', '/register');
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'first@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: '修改邮箱' }));

    expect(await screen.findByAltText('图形验证码')).toBeInTheDocument();
    expect(screen.getByLabelText('邮箱')).not.toBeDisabled();
    expect(screen.getByLabelText('邮件验证码')).toHaveValue('');
    expect(screen.getByLabelText('邮件验证码')).toBeDisabled();
    expect(createAuthCaptcha).toHaveBeenCalledTimes(2);
  });

  it('shows a fail-closed registration state when registration is unavailable', async () => {
    window.history.replaceState({}, '', '/register');
    vi.mocked(getRegistrationConfig).mockResolvedValue({
      ...readyConfig,
      enabled: false,
      ready: false,
      message: '注册暂未开放',
    });

    render(<AuthPortal onAuthenticated={vi.fn()} />);

    expect(await screen.findByText('注册暂未开放')).toBeInTheDocument();
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
    expect(createAuthCaptcha).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText('新密码')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('确认新密码')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '654321' } });
    await waitFor(() => expect(verifyPasswordResetCode).toHaveBeenCalledWith({
      email: 'pilot@example.com',
      challenge_id: 'email-1',
      verification_code: '654321',
    }));
    expect(await screen.findByText('邮箱验证成功')).toBeInTheDocument();
    const newPassword = screen.getByLabelText('新密码');
    await waitFor(() => expect(newPassword).toHaveFocus());
    expect(screen.getByText('p***t@example.com')).toBeInTheDocument();
    expect(window.location.href).not.toContain('grant-token-1');
    expect(localStorage.getItem('reset_grant_token')).toBeNull();
    fireEvent.change(newPassword, { target: { value: 'Changed-pass-2026!' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'Changed-pass-2026!' } });
    fireEvent.click(screen.getByRole('button', { name: '重置密码' }));

    await waitFor(() => expect(requestPasswordReset).toHaveBeenCalledWith({
      email: 'pilot@example.com',
      reset_grant_id: 'grant-1',
      reset_grant_token: 'grant-token-1',
      new_password: 'Changed-pass-2026!',
    }));
    expect(await screen.findByText('密码已重置，请重新登录')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
  });

  it('stays on email verification when the reset code is wrong', async () => {
    window.history.replaceState({}, '', '/forgot-password');
    vi.mocked(verifyPasswordResetCode).mockRejectedValueOnce(new ApiRequestError('邮件验证码错误', {
      code: 'CHALLENGE_SECRET_INVALID',
      status: 400,
    }));
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '000000' } });

    expect(await screen.findByText('邮件验证码错误')).toBeInTheDocument();
    expect(screen.getByLabelText('邮件验证码')).toHaveValue('');
    expect(screen.queryByLabelText('新密码')).not.toBeInTheDocument();
    expect(screen.getByText('图形验证已通过，邮件已发送')).toBeInTheDocument();
    expect(createAuthCaptcha).toHaveBeenCalledTimes(1);
  });

  it('suppresses overlapping six-digit reset-code verification requests', async () => {
    window.history.replaceState({}, '', '/forgot-password');
    const pending = deferred<PasswordResetVerifyResponse>();
    vi.mocked(verifyPasswordResetCode).mockReturnValueOnce(pending.promise);
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    const codeInput = screen.getByLabelText('邮件验证码');

    act(() => {
      fireEvent.change(codeInput, { target: { value: '123456' } });
      fireEvent.change(codeInput, { target: { value: '12345' } });
      fireEvent.change(codeInput, { target: { value: '654321' } });
    });
    expect(verifyPasswordResetCode).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve({
        success: true,
        reset_grant_id: 'grant-1',
        reset_grant_token: 'grant-token-1',
        expires_in: 600,
        message: '邮箱验证成功',
      });
      await pending.promise;
    });
    expect(await screen.findByLabelText('新密码')).toBeInTheDocument();
  });

  it('returns to verification and drops entered passwords when a reset grant expires', async () => {
    window.history.replaceState({}, '', '/forgot-password');
    vi.mocked(verifyPasswordResetCode).mockResolvedValueOnce({
      success: true,
      reset_grant_id: 'short-grant',
      reset_grant_token: 'short-token',
      expires_in: 1,
      message: '邮箱验证成功',
    });
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    await screen.findByAltText('图形验证码');
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'pilot@example.com' } });
    fireEvent.change(screen.getByLabelText('图形验证码'), { target: { value: 'AB12' } });
    fireEvent.click(screen.getByRole('button', { name: '发送邮件验证码' }));
    await screen.findByText('图形验证已通过，邮件已发送');
    fireEvent.change(screen.getByLabelText('邮件验证码'), { target: { value: '123456' } });
    const passwordInput = await screen.findByLabelText('新密码');
    fireEvent.change(passwordInput, { target: { value: 'Never-persist-2026!' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'Never-persist-2026!' } });

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1100));
    });
    expect(await screen.findByText('邮箱验证已过期，请重新验证')).toBeInTheDocument();
    expect(screen.queryByLabelText('新密码')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('Never-persist-2026!')).not.toBeInTheDocument();
  });

  it('supports direct legal routes and history navigation between auth views', async () => {
    render(<AuthPortal onAuthenticated={vi.fn()} />);

    const desktopNavigation = screen.getByRole('navigation', { name: '认证导航' });
    expect(screen.getByRole('navigation', { name: '移动认证导航' })).toBeInTheDocument();
    expect(screen.getByText('闲鱼智控 v1.8.0')).toBeInTheDocument();
    expect(screen.getByText('Xianyu AI Manager v1.8.0')).toBeInTheDocument();

    fireEvent.click(within(desktopNavigation).getByRole('button', { name: '注册账号' }));
    expect(window.location.pathname).toBe('/register');
    expect(await screen.findByRole('heading', { name: '创建账号' })).toBeInTheDocument();

    fireEvent.click(within(desktopNavigation).getByRole('button', { name: '忘记密码' }));
    expect(window.location.pathname).toBe('/forgot-password');
    expect(await screen.findByRole('heading', { name: '找回密码' })).toBeInTheDocument();

    window.history.pushState({}, '', '/privacy');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(await screen.findByRole('heading', { name: '隐私说明' })).toBeInTheDocument();
    expect(screen.getByText(/support@example.com/)).toBeInTheDocument();
  });
});
