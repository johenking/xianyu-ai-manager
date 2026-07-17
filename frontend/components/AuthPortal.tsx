import React, { forwardRef, useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  FileLock2,
  KeyRound,
  Loader2,
  Lock,
  LogIn,
  Mail,
  RefreshCw,
  ShieldCheck,
  User,
  UserPlus,
} from 'lucide-react';
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
import type { PasswordResetVerifyResponse, RegistrationConfig } from '../types';
import BrandLockup from './BrandLockup';

type PublicAuthPath = '/login' | '/register' | '/forgot-password' | '/terms' | '/privacy';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME_PATTERN = /^[\p{L}\p{N}_-]{3,24}$/u;
const OTP_PATTERN = /^\d{6}$/;
const CAPTCHA_RESTART_CODES = new Set([
  'CHALLENGE_CONSUMED',
  'CHALLENGE_EXPIRED',
  'CHALLENGE_LOCKED',
  'CHALLENGE_NOT_FOUND',
  'CHALLENGE_UNAVAILABLE',
  'EMAIL_SEND_FAILED',
]);
const FALLBACK_CONFIG: RegistrationConfig = {
  enabled: false,
  ready: false,
  invite_required: false,
  terms_version: 'v2',
  terms_url: '/terms',
  privacy_url: '/privacy',
  support_email: '',
  message: '注册暂未开放',
};

const AUTH_ITEMS = [
  { path: '/login' as const, label: '账号登录', icon: LogIn },
  { path: '/register' as const, label: '注册账号', icon: UserPlus },
  { path: '/forgot-password' as const, label: '忘记密码', icon: KeyRound },
];

const currentAuthPath = (): PublicAuthPath => {
  const path = window.location.pathname;
  if (path === '/register' || path === '/forgot-password' || path === '/terms' || path === '/privacy') {
    return path;
  }
  return '/login';
};

const errorMessage = (error: unknown, fallback: string) => (
  error instanceof Error && error.message ? error.message : fallback
);

const validatePassword = (password: string, username = ''): string => {
  if (password.length < 8) return '密码至少需要 8 个字符';
  if (new TextEncoder().encode(password).length > 72) return '密码不能超过 72 字节';
  if (username && password.toLocaleLowerCase().includes(username.toLocaleLowerCase())) {
    return '密码不能包含用户名';
  }
  return '';
};

const normalizeOtp = (value: string) => value.replace(/\D/g, '').slice(0, 6);

const maskEmail = (email: string) => {
  const [localPart, domain] = email.split('@');
  if (!localPart || !domain) return email;
  if (localPart.length === 1) return `${localPart}***@${domain}`;
  return `${localPart[0]}***${localPart.at(-1)}@${domain}`;
};

interface EmailChallengeState {
  email: string;
  setEmail: (value: string) => void;
  emailLocked: boolean;
  captchaCode: string;
  setCaptchaCode: (value: string) => void;
  captchaImage: string;
  captchaLoading: boolean;
  captchaRequired: boolean;
  emailChallengeId: string;
  cooldown: number;
  sending: boolean;
  notice: string;
  error: string;
  refreshCaptcha: () => Promise<void>;
  sendCode: () => Promise<boolean>;
  beginResend: () => void;
  changeEmail: () => void;
  restartVerification: () => void;
}

const useEmailChallenge = (
  purpose: 'register' | 'password_reset',
  enabled: boolean,
): EmailChallengeState => {
  const [email, setEmailValue] = useState('');
  const [emailLocked, setEmailLocked] = useState(false);
  const [captchaCode, setCaptchaCode] = useState('');
  const [captchaChallengeId, setCaptchaChallengeId] = useState('');
  const [captchaImage, setCaptchaImage] = useState('');
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [captchaRequired, setCaptchaRequired] = useState(true);
  const [emailChallengeId, setEmailChallengeId] = useState('');
  const [cooldown, setCooldown] = useState(0);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const requestGeneration = useRef(0);

  const refreshCaptcha = useCallback(async () => {
    if (!enabled) return;
    const generation = ++requestGeneration.current;
    setCaptchaRequired(true);
    setCaptchaLoading(true);
    setError('');
    try {
      const response = await createAuthCaptcha();
      if (generation !== requestGeneration.current) return;
      setCaptchaChallengeId(response.challenge_id);
      setCaptchaImage(response.captcha_image);
      setCaptchaCode('');
    } catch (caught) {
      if (generation !== requestGeneration.current) return;
      setCaptchaChallengeId('');
      setCaptchaImage('');
      setError(errorMessage(caught, '图形验证码加载失败'));
    } finally {
      if (generation === requestGeneration.current) setCaptchaLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (enabled) void refreshCaptcha();
  }, [enabled, refreshCaptcha]);

  useEffect(() => () => {
    requestGeneration.current += 1;
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = window.setInterval(() => {
      setCooldown((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldown > 0]);

  const setEmail = (value: string) => {
    if (emailLocked || sending) return;
    setEmailValue(value);
    setNotice('');
    setError('');
  };

  const resetChallenge = useCallback(() => {
    requestGeneration.current += 1;
    setEmailLocked(false);
    setEmailChallengeId('');
    setCooldown(0);
    setSending(false);
    setCaptchaLoading(false);
    setNotice('');
    setError('');
    setCaptchaChallengeId('');
    setCaptchaImage('');
    setCaptchaCode('');
    setCaptchaRequired(true);
    if (enabled) void refreshCaptcha();
  }, [enabled, refreshCaptcha]);

  const sendCode = async () => {
    setError('');
    setNotice('');
    if (!enabled) {
      setError('邮件验证当前不可用');
      return false;
    }
    if (!EMAIL_PATTERN.test(email.trim())) {
      setError('请输入有效邮箱');
      return false;
    }
    if (!captchaChallengeId || !captchaCode.trim()) {
      setError('请输入图形验证码');
      return false;
    }

    const generation = ++requestGeneration.current;
    setSending(true);
    try {
      const response = await sendAuthEmailCode({
        purpose,
        email: email.trim(),
        captcha_challenge_id: captchaChallengeId,
        captcha_code: captchaCode.trim(),
      });
      if (generation !== requestGeneration.current) return false;
      setEmailChallengeId(response.challenge_id);
      setEmailLocked(true);
      setCooldown(response.cooldown_seconds ?? 60);
      setNotice(response.message || '验证码已发送，请查收邮件');
      setCaptchaRequired(false);
      setCaptchaChallengeId('');
      setCaptchaImage('');
      setCaptchaCode('');
      return true;
    } catch (caught) {
      if (generation !== requestGeneration.current) return false;
      const code = caught instanceof ApiRequestError ? caught.code : undefined;
      setError(errorMessage(caught, '验证码发送失败'));
      setCaptchaCode('');
      if (code && code !== 'CHALLENGE_SECRET_INVALID' && CAPTCHA_RESTART_CODES.has(code)) {
        setCaptchaChallengeId('');
        setCaptchaImage('');
      }
      return false;
    } finally {
      if (generation === requestGeneration.current) setSending(false);
    }
  };

  const beginResend = () => {
    if (cooldown > 0 || sending) return;
    setNotice('');
    setError('');
    void refreshCaptcha();
  };

  return {
    email,
    setEmail,
    emailLocked,
    captchaCode,
    setCaptchaCode,
    captchaImage,
    captchaLoading,
    captchaRequired,
    emailChallengeId,
    cooldown,
    sending,
    notice,
    error,
    refreshCaptcha,
    sendCode,
    beginResend,
    changeEmail: resetChallenge,
    restartVerification: resetChallenge,
  };
};

interface InputFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  autoComplete?: string;
  placeholder?: string;
  icon: React.ComponentType<{ className?: string }>;
  maxLength?: number;
  inputMode?: React.HTMLAttributes<HTMLInputElement>['inputMode'];
  disabled?: boolean;
}

const InputField = forwardRef<HTMLInputElement, InputFieldProps>(({
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  placeholder,
  icon: Icon,
  maxLength,
  inputMode,
  disabled,
}, ref) => (
  <label className="block text-sm font-bold text-gray-800">
    {label}
    <span className="relative mt-2 block">
      <Icon className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
      <input
        ref={ref}
        aria-label={label}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        maxLength={maxLength}
        inputMode={inputMode}
        disabled={disabled}
        className="h-12 w-full rounded-xl border border-gray-200 bg-[#F7F8FA] pl-11 pr-4 font-medium text-gray-900 outline-none transition focus:border-yellow-400 focus:bg-white focus:ring-4 focus:ring-yellow-100 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-500"
      />
    </span>
  </label>
));
InputField.displayName = 'InputField';

interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
}

const PasswordField = forwardRef<HTMLInputElement, PasswordFieldProps>(({
  label,
  value,
  onChange,
  autoComplete,
}, ref) => {
  const [visible, setVisible] = useState(false);
  return (
    <label className="block text-sm font-bold text-gray-800">
      {label}
      <span className="relative mt-2 block">
        <Lock className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          ref={ref}
          aria-label={label}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          className="h-12 w-full rounded-xl border border-gray-200 bg-[#F7F8FA] pl-11 pr-12 font-medium text-gray-900 outline-none transition focus:border-yellow-400 focus:bg-white focus:ring-4 focus:ring-yellow-100"
        />
        <button
          type="button"
          aria-label={visible ? `隐藏${label}` : `显示${label}`}
          title={visible ? `隐藏${label}` : `显示${label}`}
          onClick={() => setVisible((value) => !value)}
          className="absolute right-2 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-200"
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </span>
    </label>
  );
});
PasswordField.displayName = 'PasswordField';

const Notice: React.FC<{ tone?: 'success' | 'error' | 'info'; children: React.ReactNode }> = ({ tone = 'info', children }) => {
  const style = tone === 'success'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-blue-200 bg-blue-50 text-blue-800';
  return <div role="status" aria-live="polite" className={`rounded-xl border px-4 py-3 text-sm font-medium ${style}`}>{children}</div>;
};

const VerificationFields: React.FC<{
  state: EmailChallengeState;
  verificationCode: string;
  onVerificationCodeChange: (value: string) => void;
  disabled?: boolean;
  codeBusy?: boolean;
}> = ({ state, verificationCode, onVerificationCodeChange, disabled, codeBusy }) => {
  const resendReady = Boolean(state.emailChallengeId) && !state.captchaRequired && state.cooldown <= 0;
  const actionLabel = state.sending
    ? '发送中'
    : state.cooldown > 0
      ? `${state.cooldown} 秒后重试`
      : state.emailChallengeId
        ? state.captchaRequired ? '重新发送验证码' : '重新发送'
        : '发送邮件验证码';

  return (
    <div className="space-y-4">
      <div className="relative">
        <InputField
          label="邮箱"
          value={state.email}
          onChange={state.setEmail}
          type="email"
          autoComplete="email"
          placeholder="name@example.com"
          icon={Mail}
          disabled={Boolean(disabled) || state.emailLocked || state.sending || Boolean(codeBusy)}
        />
        {state.emailLocked ? (
          <button
            type="button"
            onClick={() => {
              onVerificationCodeChange('');
              state.changeEmail();
            }}
            disabled={Boolean(codeBusy) || state.sending || state.captchaLoading}
            className="absolute right-3 top-8 rounded-lg px-2.5 py-1.5 text-xs font-bold text-gray-600 hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            修改邮箱
          </button>
        ) : null}
      </div>

      {state.captchaRequired ? (
        <div className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-[minmax(0,1fr)_136px]">
          <InputField
            label="图形验证码"
            value={state.captchaCode}
            onChange={(value) => state.setCaptchaCode(value.toUpperCase())}
            autoComplete="off"
            placeholder="输入图片字符"
            icon={ShieldCheck}
            maxLength={12}
            disabled={Boolean(disabled) || state.sending || Boolean(codeBusy)}
          />
          <div className="min-[360px]:pt-7">
            <button
              type="button"
              aria-label="刷新图形验证码"
              title="刷新图形验证码"
              onClick={() => void state.refreshCaptcha()}
              disabled={state.captchaLoading || state.sending || Boolean(codeBusy) || disabled}
              className="relative flex h-12 w-full items-center justify-center overflow-hidden rounded-xl border border-gray-200 bg-white disabled:opacity-50"
            >
              {state.captchaLoading ? <Loader2 className="h-4 w-4 animate-spin text-gray-500" /> : state.captchaImage ? (
                <img src={state.captchaImage} alt="图形验证码" className="h-full max-w-full object-contain" />
              ) : <><RefreshCw className="mr-2 h-4 w-4 text-gray-500" /><span className="text-xs font-bold text-gray-500">重新获取</span></>}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-bold text-emerald-800">
          <CheckCircle2 className="h-5 w-5 shrink-0" />
          <span>图形验证已通过，邮件已发送</span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-[minmax(0,1fr)_136px]">
        <InputField
          label="邮件验证码"
          value={verificationCode}
          onChange={(value) => onVerificationCodeChange(normalizeOtp(value))}
          autoComplete="one-time-code"
          placeholder="6 位数字"
          icon={KeyRound}
          maxLength={6}
          inputMode="numeric"
          disabled={Boolean(disabled) || !state.emailChallengeId || Boolean(codeBusy)}
        />
        <div className="min-[360px]:pt-7">
          <button
            type="button"
            onClick={() => {
              if (resendReady) {
                state.beginResend();
                return;
              }
              void state.sendCode().then((sent) => {
                if (sent) onVerificationCodeChange('');
              });
            }}
            disabled={Boolean(disabled) || Boolean(codeBusy) || state.sending || state.cooldown > 0 || state.captchaLoading || (!resendReady && (!state.captchaCode || !state.captchaImage))}
            className="h-12 w-full rounded-xl bg-gray-900 px-3 text-xs font-bold text-white hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {actionLabel}
          </button>
        </div>
      </div>
      {state.notice ? <Notice tone="success">{state.notice}</Notice> : null}
      {state.error ? <Notice tone="error">{state.error}</Notice> : null}
    </div>
  );
};

const FormHeading: React.FC<{ title: string; description: string }> = ({ title, description }) => (
  <div>
    <h1 className="text-2xl font-extrabold text-gray-950 sm:text-3xl">{title}</h1>
    <p className="mt-2 text-sm font-medium text-gray-500">{description}</p>
  </div>
);

const PrimaryButton: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ children, className = '', ...props }) => (
  <button {...props} className={`flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#FFE815] px-5 text-sm font-extrabold text-black shadow-lg shadow-yellow-100 transition hover:-translate-y-0.5 hover:bg-yellow-300 disabled:cursor-not-allowed disabled:translate-y-0 disabled:bg-gray-200 disabled:text-gray-500 disabled:shadow-none ${className}`}>
    {children}
  </button>
);

const LoginForm: React.FC<{
  flash: string;
  onAuthenticated: (token: string) => void;
}> = ({ flash, onAuthenticated }) => {
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (!identifier.trim() || !password) {
      setError('请输入用户名或邮箱及密码');
      return;
    }
    setLoading(true);
    try {
      const response = await login({ identifier: identifier.trim(), password });
      if (!response.success || !response.token) throw new Error(response.message || '登录失败');
      onAuthenticated(response.token);
    } catch (caught) {
      setError(errorMessage(caught, '无法连接服务器'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <FormHeading title="登录控制台" description="使用用户名或已验证邮箱登录" />
      {flash ? <Notice tone="success">{flash}</Notice> : null}
      <InputField label="用户名或邮箱" value={identifier} onChange={setIdentifier} autoComplete="username" placeholder="用户名或邮箱" icon={User} />
      <PasswordField label="密码" value={password} onChange={setPassword} autoComplete="current-password" />
      {error ? <Notice tone="error">{error}</Notice> : null}
      <PrimaryButton type="submit" disabled={loading}>
        {loading ? <><Loader2 className="h-4 w-4 animate-spin" />登录中</> : <>登录<ArrowRight className="h-4 w-4" /></>}
      </PrimaryButton>
    </form>
  );
};

const RegistrationForm: React.FC<{
  config: RegistrationConfig;
  loadingConfig: boolean;
  onAuthenticated: (token: string) => void;
}> = ({ config, loadingConfig, onAuthenticated }) => {
  const [verificationCode, setVerificationCode] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const verification = useEmailChallenge('register', config.enabled);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (!config.enabled) return setError(config.message || '注册暂未开放');
    if (!verification.emailChallengeId || !OTP_PATTERN.test(verificationCode)) return setError('请先完成邮箱验证');
    if (!USERNAME_PATTERN.test(username)) return setError('用户名需为 3–24 位字母、数字、下划线或短横线');
    const passwordError = validatePassword(password, username);
    if (passwordError) return setError(passwordError);
    if (password !== confirmPassword) return setError('两次输入的密码不一致');
    if (!accepted) return setError('请先同意服务条款和隐私说明');

    setSubmitting(true);
    try {
      const response = await registerAccount({
        email: verification.email.trim(),
        challenge_id: verification.emailChallengeId,
        verification_code: verificationCode,
        username,
        password,
        terms_version: config.terms_version,
        terms_accepted: true,
      });
      if (!response.success || !response.token) throw new Error(response.message || '注册失败');
      onAuthenticated(response.token);
    } catch (caught) {
      setError(errorMessage(caught, '注册失败，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <FormHeading title="创建账号" description="通过邮箱验证码确认身份" />
      {loadingConfig ? <Notice>正在检查注册状态...</Notice> : !config.enabled ? <Notice tone="error">{config.message}</Notice> : <Notice tone="success">注册已开放，请完成邮箱验证。</Notice>}
      <VerificationFields state={verification} verificationCode={verificationCode} onVerificationCodeChange={setVerificationCode} disabled={!config.enabled} />
      <InputField label="用户名" value={username} onChange={setUsername} autoComplete="username" placeholder="3–24 位字母、数字、_ 或 -" icon={User} maxLength={24} />
      <div className="grid gap-4 sm:grid-cols-2">
        <PasswordField label="密码" value={password} onChange={setPassword} autoComplete="new-password" />
        <PasswordField label="确认密码" value={confirmPassword} onChange={setConfirmPassword} autoComplete="new-password" />
      </div>
      <label className="flex cursor-pointer items-start gap-3 text-sm leading-6 text-gray-600">
        <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} className="mt-1 h-4 w-4 accent-yellow-400" aria-label="同意服务条款和隐私说明" />
        <span>我已阅读并同意 <a href={config.terms_url} target="_blank" rel="noreferrer" className="font-bold text-gray-900 underline">服务条款</a> 和 <a href={config.privacy_url} target="_blank" rel="noreferrer" className="font-bold text-gray-900 underline">隐私说明</a>（{config.terms_version}）</span>
      </label>
      {error ? <Notice tone="error">{error}</Notice> : null}
      <PrimaryButton type="submit" disabled={submitting || !config.enabled}>
        {submitting ? <><Loader2 className="h-4 w-4 animate-spin" />注册中</> : <>完成注册<CheckCircle2 className="h-4 w-4" /></>}
      </PrimaryButton>
    </form>
  );
};

const PasswordResetForm: React.FC<{ onComplete: (message: string) => void }> = ({ onComplete }) => {
  const [verificationCode, setVerificationCode] = useState('');
  const [grant, setGrant] = useState<PasswordResetVerifyResponse | null>(null);
  const [grantExpiresAt, setGrantExpiresAt] = useState(0);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const verification = useEmailChallenge('password_reset', true);
  const lastAttempt = useRef('');
  const verificationInFlight = useRef(false);
  const passwordInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (grant) passwordInput.current?.focus();
  }, [grant]);

  const restartVerification = useCallback((message: string) => {
    setGrant(null);
    setGrantExpiresAt(0);
    setPassword('');
    setConfirmPassword('');
    setVerificationCode('');
    lastAttempt.current = '';
    verification.restartVerification();
    setError(message);
  }, [verification.restartVerification]);

  useEffect(() => {
    if (!grant || grantExpiresAt <= 0) return undefined;
    const remaining = grantExpiresAt - Date.now();
    if (remaining <= 0) {
      restartVerification('邮箱验证已过期，请重新验证');
      return undefined;
    }
    const timer = window.setTimeout(() => {
      restartVerification('邮箱验证已过期，请重新验证');
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [grant, grantExpiresAt, restartVerification]);

  const verifyCode = async (code: string) => {
    const attemptKey = `${verification.emailChallengeId}:${code}`;
    if (!verification.emailChallengeId || verificationInFlight.current || grant || lastAttempt.current === attemptKey) return;
    lastAttempt.current = attemptKey;
    verificationInFlight.current = true;
    setVerifying(true);
    setError('');
    try {
      const response = await verifyPasswordResetCode({
        email: verification.email.trim(),
        challenge_id: verification.emailChallengeId,
        verification_code: code,
      });
      setGrant(response);
      setGrantExpiresAt(Date.now() + Math.max(1, response.expires_in) * 1000);
      setVerificationCode('');
    } catch (caught) {
      setVerificationCode('');
      lastAttempt.current = '';
      setError(errorMessage(caught, '邮箱验证码校验失败'));
      const codeValue = caught instanceof ApiRequestError ? caught.code : undefined;
      if (codeValue && ['CHALLENGE_CONSUMED', 'CHALLENGE_EXPIRED', 'CHALLENGE_LOCKED', 'CHALLENGE_NOT_FOUND'].includes(codeValue)) {
        verification.restartVerification();
      }
    } finally {
      verificationInFlight.current = false;
      setVerifying(false);
    }
  };

  const changeVerificationCode = (value: string) => {
    const normalized = normalizeOtp(value);
    setVerificationCode(normalized);
    if (normalized.length < 6) lastAttempt.current = '';
    if (OTP_PATTERN.test(normalized)) void verifyCode(normalized);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (!grant) return setError('请先完成邮箱验证');
    if (Date.now() >= grantExpiresAt) return restartVerification('邮箱验证已过期，请重新验证');
    const passwordError = validatePassword(password);
    if (passwordError) return setError(passwordError);
    if (password !== confirmPassword) return setError('两次输入的密码不一致');

    setSubmitting(true);
    try {
      const response = await requestPasswordReset({
        email: verification.email.trim(),
        reset_grant_id: grant.reset_grant_id,
        reset_grant_token: grant.reset_grant_token,
        new_password: password,
      });
      if (!response.success) throw new Error(response.message || '密码重置失败');
      onComplete(response.message || '密码已重置，请重新登录');
    } catch (caught) {
      const code = caught instanceof ApiRequestError ? caught.code : undefined;
      if (code && ['CHALLENGE_CONSUMED', 'CHALLENGE_EXPIRED', 'CHALLENGE_LOCKED', 'CHALLENGE_NOT_FOUND'].includes(code)) {
        restartVerification('重置授权已失效，请重新验证邮箱');
      } else {
        setError(errorMessage(caught, '密码重置失败，请稍后重试'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <FormHeading title="找回密码" description="验证注册邮箱后设置新密码，旧会话将全部失效" />
      {!grant ? (
        <>
          <div className="flex items-center gap-3 text-xs font-bold text-gray-500" aria-label="找回密码步骤">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#FFE815] text-black">1</span>
            <span>验证注册邮箱</span>
            <span className="h-px flex-1 bg-gray-200" />
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gray-200 text-gray-500">2</span>
            <span>设置新密码</span>
          </div>
          <VerificationFields state={verification} verificationCode={verificationCode} onVerificationCodeChange={changeVerificationCode} codeBusy={verifying} />
          {verifying ? <Notice><span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />正在校验邮箱验证码...</span></Notice> : null}
          {error ? <Notice tone="error">{error}</Notice> : null}
        </>
      ) : (
        <>
          <div className="flex items-center gap-3 text-xs font-bold text-gray-500" aria-label="找回密码步骤">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-100 text-emerald-700"><CheckCircle2 className="h-4 w-4" /></span>
            <span>邮箱已验证</span>
            <span className="h-px flex-1 bg-yellow-300" />
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#FFE815] text-black">2</span>
            <span>设置新密码</span>
          </div>
          <Notice tone="success"><span className="inline-flex items-center gap-2"><CheckCircle2 className="h-4 w-4" />邮箱验证成功</span></Notice>
          <div className="flex items-center justify-between rounded-xl bg-gray-100 px-4 py-3 text-sm">
            <span className="min-w-0 truncate font-bold text-gray-700">{maskEmail(verification.email)}</span>
            <button type="button" onClick={() => restartVerification('请重新完成邮箱验证')} className="ml-3 shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-bold text-gray-600 hover:bg-white">更换邮箱</button>
          </div>
          <PasswordField ref={passwordInput} label="新密码" value={password} onChange={setPassword} autoComplete="new-password" />
          <PasswordField label="确认新密码" value={confirmPassword} onChange={setConfirmPassword} autoComplete="new-password" />
          <p className="text-xs font-medium leading-5 text-gray-500">至少 8 个字符，不能包含用户名，且不能超过 72 字节。</p>
          {error ? <Notice tone="error">{error}</Notice> : null}
          <PrimaryButton type="submit" disabled={submitting}>
            {submitting ? <><Loader2 className="h-4 w-4 animate-spin" />重置中</> : <>重置密码<KeyRound className="h-4 w-4" /></>}
          </PrimaryButton>
        </>
      )}
    </form>
  );
};

const LegalPage: React.FC<{
  kind: 'terms' | 'privacy';
  supportEmail: string;
  version: string;
  onBack: () => void;
}> = ({ kind, supportEmail, version, onBack }) => {
  const contact = supportEmail || '请联系系统管理员';
  return (
    <article className="space-y-6 text-sm leading-7 text-gray-700">
      <button type="button" onClick={onBack} className="inline-flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm font-bold text-gray-700 hover:bg-gray-100 hover:text-black"><ArrowLeft className="h-4 w-4" />返回登录</button>
      <div>
        <p className="text-xs font-bold uppercase text-gray-400">协议版本 {version}</p>
        <h1 className="mt-1 text-2xl font-extrabold text-gray-950 sm:text-3xl">{kind === 'terms' ? '服务条款' : '隐私说明'}</h1>
      </div>
      {kind === 'terms' ? (
        <>
          <section><h2 className="font-extrabold text-gray-950">账号与使用</h2><p>用户应妥善保管登录凭据，不得尝试绕过闲鱼平台风控、冒用他人身份或将系统用于违法用途。</p></section>
          <section><h2 className="font-extrabold text-gray-950">自动化边界</h2><p>系统会按用户配置执行监听、回复、通知和 Cookie 续期。平台要求短信、扫码、人脸或其他人工验证时，用户必须自行完成，系统不承诺持续绕过或永久免验证。</p></section>
          <section><h2 className="font-extrabold text-gray-950">账号处置</h2><p>管理员可在安全、滥用或运营需要下停用普通账号。密码重置会撤销该账号的全部旧登录会话。</p></section>
        </>
      ) : (
        <>
          <section><h2 className="font-extrabold text-gray-950">收集的数据</h2><p>系统保存注册用户名、邮箱、密码摘要、协议同意记录、脱敏网络标识、认证安全事件，以及用户主动配置的闲鱼账号、规则、消息、商品和通知资料。密码、SMTP 授权码及平台凭据按用途加密或摘要保存。</p></section>
          <section><h2 className="font-extrabold text-gray-950">用途与保存</h2><p>这些数据用于登录、账号找回、安全限流、自动化任务和故障排查。认证挑战在过期或消费后不可再次使用；业务数据按系统运营和用户配置需要保存。</p></section>
          <section><h2 className="font-extrabold text-gray-950">访问与删除</h2><p>当前版本不提供自助销户。需要查询、更正或删除账号资料时，请通过支持邮箱联系管理员；管理员核验身份后处理相关请求。</p></section>
        </>
      )}
      <section><h2 className="font-extrabold text-gray-950">联系方式</h2><p>{supportEmail ? <>支持邮箱：<a className="font-bold text-gray-950 underline" href={`mailto:${supportEmail}`}>{contact}</a></> : contact}</p></section>
      <Notice>本页记录当前产品的技术处理方式，不构成法律意见，也未声称经过法律审查。</Notice>
    </article>
  );
};

const AuthNavigation: React.FC<{
  path: PublicAuthPath;
  onNavigate: (path: PublicAuthPath) => void;
  mobile?: boolean;
}> = ({ path, onNavigate, mobile }) => (
  <nav aria-label={mobile ? '移动认证导航' : '认证导航'} className={mobile ? 'grid grid-cols-3 gap-2' : 'space-y-2'}>
    {AUTH_ITEMS.map((item) => {
      const Icon = item.icon;
      const active = path === item.path;
      return (
        <button
          key={item.path}
          type="button"
          aria-label={item.label}
          title={item.label}
          aria-current={active ? 'page' : undefined}
          onClick={() => onNavigate(item.path)}
          className={`${mobile ? 'min-h-11 justify-center px-2' : 'w-full px-4 py-3.5'} flex items-center gap-3 rounded-2xl text-sm font-bold transition ${active ? 'bg-[#FFE815] text-black shadow-lg shadow-yellow-100' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'}`}
        >
          <Icon className="h-5 w-5 shrink-0" />
          <span className={mobile ? 'hidden min-[390px]:inline' : ''}>{item.label}</span>
        </button>
      );
    })}
  </nav>
);

const AuthPortal: React.FC<{ onAuthenticated: (token: string) => void }> = ({ onAuthenticated }) => {
  const [path, setPath] = useState<PublicAuthPath>(currentAuthPath);
  const [config, setConfig] = useState<RegistrationConfig>(FALLBACK_CONFIG);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [flash, setFlash] = useState('');

  useEffect(() => {
    let active = true;
    getRegistrationConfig()
      .then((value) => { if (active) setConfig(value); })
      .catch(() => { if (active) setConfig(FALLBACK_CONFIG); })
      .finally(() => { if (active) setLoadingConfig(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const handlePopState = () => setPath(currentAuthPath());
    if (!['/login', '/register', '/forgot-password', '/terms', '/privacy'].includes(window.location.pathname)) {
      window.history.replaceState({}, '', '/login');
    }
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    const labels: Record<PublicAuthPath, string> = {
      '/login': '登录',
      '/register': '注册',
      '/forgot-password': '找回密码',
      '/terms': '服务条款',
      '/privacy': '隐私说明',
    };
    document.title = `${labels[path]} - 闲鱼智控`;
  }, [path]);

  const navigate = useCallback((nextPath: PublicAuthPath, replace = false) => {
    if (replace) window.history.replaceState({}, '', nextPath);
    else window.history.pushState({}, '', nextPath);
    setPath(nextPath);
  }, []);

  const completeAuthentication = (token: string) => {
    localStorage.setItem('auth_token', token);
    window.history.replaceState({}, '', '/');
    document.title = '闲鱼智控 - 自动化控制台';
    onAuthenticated(token);
  };

  const isLegal = path === '/terms' || path === '/privacy';
  const panelWidth = path === '/register' || isLegal ? 'max-w-3xl' : 'max-w-xl';

  let content: React.ReactNode;
  if (path === '/register') {
    content = <RegistrationForm config={config} loadingConfig={loadingConfig} onAuthenticated={completeAuthentication} />;
  } else if (path === '/forgot-password') {
    content = <PasswordResetForm onComplete={(message) => { setFlash(message); navigate('/login', true); }} />;
  } else if (path === '/terms' || path === '/privacy') {
    content = <LegalPage kind={path === '/terms' ? 'terms' : 'privacy'} supportEmail={config.support_email} version={config.terms_version} onBack={() => navigate('/login')} />;
  } else {
    content = <LoginForm flash={flash} onAuthenticated={completeAuthentication} />;
  }

  return (
    <div className="min-h-screen bg-[#F4F5F7] text-gray-950">
      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col border-r border-gray-100 bg-white p-6 shadow-[4px_0_24px_rgba(0,0,0,0.08)] lg:flex">
        <BrandLockup className="px-2" subtitle="自动化控制台" />
        <div className="mt-12 flex-1">
          <AuthNavigation path={path} onNavigate={navigate} />
        </div>
        <div className="space-y-3 border-t border-gray-100 pt-5 text-xs font-medium text-gray-500">
          <div className="flex gap-4">
            <button type="button" onClick={() => navigate('/terms')} className="hover:text-gray-900">服务条款</button>
            <button type="button" onClick={() => navigate('/privacy')} className="hover:text-gray-900">隐私说明</button>
          </div>
          <p>闲鱼智控 v{__APP_VERSION__}</p>
        </div>
      </aside>

      <div className="min-h-screen lg:ml-64">
        <header className="border-b border-gray-100 bg-white px-4 py-4 shadow-sm lg:hidden">
          <BrandLockup subtitle="自动化控制台" />
          {!isLegal ? <div className="mt-4"><AuthNavigation path={path} onNavigate={navigate} mobile /></div> : null}
        </header>

        <main className="flex min-h-[calc(100vh-138px)] items-center px-4 py-8 sm:px-6 lg:min-h-screen lg:px-10 lg:py-12">
          <div className={`mx-auto w-full ${panelWidth}`}>
            <section className="ios-card rounded-2xl border border-gray-100 bg-white p-5 shadow-[0_16px_45px_rgba(0,0,0,0.06)] sm:p-8">
              {content}
            </section>
            <footer className="mt-5 flex items-center justify-center gap-2 text-center text-xs font-medium text-gray-400">
              <FileLock2 className="h-3.5 w-3.5" />
              <span>Xianyu AI Manager v{__APP_VERSION__}</span>
            </footer>
          </div>
        </main>
      </div>
    </div>
  );
};

export default AuthPortal;
