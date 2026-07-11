import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  Mail,
  RefreshCw,
  ShieldCheck,
  User,
} from 'lucide-react';
import {
  createAuthCaptcha,
  getRegistrationConfig,
  login,
  registerAccount,
  requestPasswordReset,
  sendAuthEmailCode,
} from '../services/api';
import type { RegistrationConfig } from '../types';

type PublicAuthPath = '/login' | '/register' | '/forgot-password' | '/terms' | '/privacy';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME_PATTERN = /^[\p{L}\p{N}_-]{3,24}$/u;
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

interface EmailChallengeState {
  email: string;
  setEmail: (value: string) => void;
  captchaCode: string;
  setCaptchaCode: (value: string) => void;
  captchaImage: string;
  captchaLoading: boolean;
  emailChallengeId: string;
  cooldown: number;
  sending: boolean;
  notice: string;
  error: string;
  refreshCaptcha: () => Promise<void>;
  sendCode: () => Promise<void>;
}

const useEmailChallenge = (
  purpose: 'register' | 'password_reset',
  enabled: boolean,
): EmailChallengeState => {
  const [email, setEmail] = useState('');
  const [captchaCode, setCaptchaCode] = useState('');
  const [captchaChallengeId, setCaptchaChallengeId] = useState('');
  const [captchaImage, setCaptchaImage] = useState('');
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [emailChallengeId, setEmailChallengeId] = useState('');
  const [cooldown, setCooldown] = useState(0);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const refreshCaptcha = useCallback(async () => {
    if (!enabled) return;
    setCaptchaLoading(true);
    setError('');
    try {
      const response = await createAuthCaptcha();
      setCaptchaChallengeId(response.challenge_id);
      setCaptchaImage(response.captcha_image);
      setCaptchaCode('');
    } catch (caught) {
      setCaptchaChallengeId('');
      setCaptchaImage('');
      setError(errorMessage(caught, '图形验证码加载失败'));
    } finally {
      setCaptchaLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (enabled) void refreshCaptcha();
  }, [enabled, refreshCaptcha]);

  useEffect(() => {
    setEmailChallengeId('');
    setNotice('');
  }, [email]);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = window.setInterval(() => {
      setCooldown((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldown > 0]);

  const sendCode = async () => {
    setError('');
    setNotice('');
    if (!enabled) {
      setError('邮件验证当前不可用');
      return;
    }
    if (!EMAIL_PATTERN.test(email.trim())) {
      setError('请输入有效邮箱');
      return;
    }
    if (!captchaChallengeId || !captchaCode.trim()) {
      setError('请输入图形验证码');
      return;
    }

    setSending(true);
    try {
      const response = await sendAuthEmailCode({
        purpose,
        email: email.trim(),
        captcha_challenge_id: captchaChallengeId,
        captcha_code: captchaCode.trim(),
      });
      setEmailChallengeId(response.challenge_id);
      setCooldown(response.cooldown_seconds || 60);
      setNotice(response.message || '验证码已发送，请查收邮件');
      await refreshCaptcha();
    } catch (caught) {
      setError(errorMessage(caught, '验证码发送失败'));
      await refreshCaptcha();
    } finally {
      setSending(false);
    }
  };

  return {
    email,
    setEmail,
    captchaCode,
    setCaptchaCode,
    captchaImage,
    captchaLoading,
    emailChallengeId,
    cooldown,
    sending,
    notice,
    error,
    refreshCaptcha,
    sendCode,
  };
};

const InputField: React.FC<{
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  autoComplete?: string;
  placeholder?: string;
  icon: React.ComponentType<{ className?: string }>;
  maxLength?: number;
}> = ({ label, value, onChange, type = 'text', autoComplete, placeholder, icon: Icon, maxLength }) => (
  <label className="block text-sm font-bold text-gray-800">
    {label}
    <span className="relative mt-2 block">
      <Icon className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
      <input
        aria-label={label}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        maxLength={maxLength}
        className="h-11 w-full rounded-lg border border-gray-200 bg-white pl-10 pr-3 font-normal text-gray-900 outline-none transition focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100"
      />
    </span>
  </label>
);

const PasswordField: React.FC<{
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
}> = ({ label, value, onChange, autoComplete }) => {
  const [visible, setVisible] = useState(false);
  return (
    <label className="block text-sm font-bold text-gray-800">
      {label}
      <span className="relative mt-2 block">
        <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          aria-label={label}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          className="h-11 w-full rounded-lg border border-gray-200 bg-white pl-10 pr-11 font-normal text-gray-900 outline-none transition focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100"
        />
        <button
          type="button"
          aria-label={visible ? `隐藏${label}` : `显示${label}`}
          title={visible ? `隐藏${label}` : `显示${label}`}
          onClick={() => setVisible((value) => !value)}
          className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100"
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </span>
    </label>
  );
};

const Notice: React.FC<{ tone?: 'success' | 'error' | 'info'; children: React.ReactNode }> = ({ tone = 'info', children }) => {
  const style = tone === 'success'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-blue-200 bg-blue-50 text-blue-800';
  return <div role="status" className={`rounded-lg border px-3 py-2.5 text-sm font-medium ${style}`}>{children}</div>;
};

const VerificationFields: React.FC<{
  state: EmailChallengeState;
  verificationCode: string;
  onVerificationCodeChange: (value: string) => void;
  disabled?: boolean;
}> = ({ state, verificationCode, onVerificationCodeChange, disabled }) => (
  <div className="space-y-4">
    <InputField label="邮箱" value={state.email} onChange={state.setEmail} type="email" autoComplete="email" placeholder="name@example.com" icon={Mail} />
    <div className="grid grid-cols-[minmax(0,1fr)_132px] gap-3">
      <InputField label="图形验证码" value={state.captchaCode} onChange={(value) => state.setCaptchaCode(value.toUpperCase())} autoComplete="off" placeholder="验证码" icon={ShieldCheck} maxLength={12} />
      <div className="pt-7">
        <button
          type="button"
          aria-label="刷新图形验证码"
          title="刷新图形验证码"
          onClick={() => void state.refreshCaptcha()}
          disabled={state.captchaLoading || disabled}
          className="relative flex h-11 w-full items-center justify-center overflow-hidden rounded-lg border border-gray-200 bg-gray-50 disabled:opacity-50"
        >
          {state.captchaLoading ? <Loader2 className="h-4 w-4 animate-spin text-gray-500" /> : state.captchaImage ? (
            <img src={state.captchaImage} alt="图形验证码" className="h-full max-w-full object-contain" />
          ) : <RefreshCw className="h-4 w-4 text-gray-500" />}
        </button>
      </div>
    </div>
    <div className="grid grid-cols-[minmax(0,1fr)_132px] gap-3">
      <InputField label="邮件验证码" value={verificationCode} onChange={onVerificationCodeChange} autoComplete="one-time-code" placeholder="6 位数字" icon={KeyRound} maxLength={6} />
      <div className="pt-7">
        <button
          type="button"
          onClick={() => void state.sendCode()}
          disabled={Boolean(disabled) || state.sending || state.cooldown > 0 || state.captchaLoading}
          className="h-11 w-full rounded-lg bg-gray-900 px-3 text-xs font-bold text-white hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {state.sending ? '发送中' : state.cooldown > 0 ? `${state.cooldown} 秒后重试` : '发送邮件验证码'}
        </button>
      </div>
    </div>
    {state.notice ? <Notice tone="success">{state.notice}</Notice> : null}
    {state.error ? <Notice tone="error">{state.error}</Notice> : null}
  </div>
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
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold text-gray-950">登录控制台</h1>
        <p className="mt-1.5 text-sm text-gray-500">使用用户名或已验证邮箱登录</p>
      </div>
      {flash ? <Notice tone="success">{flash}</Notice> : null}
      <InputField label="用户名或邮箱" value={identifier} onChange={setIdentifier} autoComplete="username" placeholder="用户名或邮箱" icon={User} />
      <PasswordField label="密码" value={password} onChange={setPassword} autoComplete="current-password" />
      {error ? <Notice tone="error">{error}</Notice> : null}
      <button type="submit" disabled={loading} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#FFE815] px-5 text-sm font-extrabold text-black hover:bg-yellow-300 disabled:opacity-50">
        {loading ? <><Loader2 className="h-4 w-4 animate-spin" />登录中</> : <>登录<ArrowRight className="h-4 w-4" /></>}
      </button>
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
    if (!verification.emailChallengeId || !/^\d{6}$/.test(verificationCode)) return setError('请先完成邮箱验证');
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
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold text-gray-950">创建账号</h1>
        <p className="mt-1.5 text-sm text-gray-500">通过邮箱验证码确认身份</p>
      </div>
      {loadingConfig ? <Notice>正在检查注册状态...</Notice> : !config.enabled ? <Notice tone="error">{config.message}</Notice> : <Notice tone="success">注册已开放，请完成邮箱验证。</Notice>}
      <VerificationFields state={verification} verificationCode={verificationCode} onVerificationCodeChange={setVerificationCode} disabled={!config.enabled} />
      <InputField label="用户名" value={username} onChange={setUsername} autoComplete="username" placeholder="3–24 位字母、数字、_ 或 -" icon={User} maxLength={24} />
      <div className="grid gap-4 sm:grid-cols-2">
        <PasswordField label="密码" value={password} onChange={setPassword} autoComplete="new-password" />
        <PasswordField label="确认密码" value={confirmPassword} onChange={setConfirmPassword} autoComplete="new-password" />
      </div>
      <label className="flex cursor-pointer items-start gap-3 text-sm text-gray-600">
        <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} className="mt-0.5 h-4 w-4 accent-yellow-400" aria-label="同意服务条款和隐私说明" />
        <span>我已阅读并同意 <a href={config.terms_url} target="_blank" rel="noreferrer" className="font-bold text-gray-900 underline">服务条款</a> 和 <a href={config.privacy_url} target="_blank" rel="noreferrer" className="font-bold text-gray-900 underline">隐私说明</a>（{config.terms_version}）</span>
      </label>
      {error ? <Notice tone="error">{error}</Notice> : null}
      <button type="submit" disabled={submitting || !config.enabled} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#FFE815] px-5 text-sm font-extrabold text-black hover:bg-yellow-300 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-500">
        {submitting ? <><Loader2 className="h-4 w-4 animate-spin" />注册中</> : <>完成注册<CheckCircle2 className="h-4 w-4" /></>}
      </button>
    </form>
  );
};

const PasswordResetForm: React.FC<{ onComplete: (message: string) => void }> = ({ onComplete }) => {
  const [verificationCode, setVerificationCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const verification = useEmailChallenge('password_reset', true);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (!verification.emailChallengeId || !/^\d{6}$/.test(verificationCode)) return setError('请先完成邮箱验证');
    const passwordError = validatePassword(password);
    if (passwordError) return setError(passwordError);
    if (password !== confirmPassword) return setError('两次输入的密码不一致');

    setSubmitting(true);
    try {
      const response = await requestPasswordReset({
        email: verification.email.trim(),
        challenge_id: verification.emailChallengeId,
        verification_code: verificationCode,
        new_password: password,
      });
      if (!response.success) throw new Error(response.message || '密码重置失败');
      onComplete(response.message || '密码已重置，请重新登录');
    } catch (caught) {
      setError(errorMessage(caught, '密码重置失败，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold text-gray-950">找回账号</h1>
        <p className="mt-1.5 text-sm text-gray-500">验证注册邮箱后设置新密码，旧会话将全部失效</p>
      </div>
      <VerificationFields state={verification} verificationCode={verificationCode} onVerificationCodeChange={setVerificationCode} />
      <div className="grid gap-4 sm:grid-cols-2">
        <PasswordField label="新密码" value={password} onChange={setPassword} autoComplete="new-password" />
        <PasswordField label="确认新密码" value={confirmPassword} onChange={setConfirmPassword} autoComplete="new-password" />
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      <button type="submit" disabled={submitting} className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#FFE815] px-5 text-sm font-extrabold text-black hover:bg-yellow-300 disabled:opacity-50">
        {submitting ? <><Loader2 className="h-4 w-4 animate-spin" />重置中</> : <>重置密码<KeyRound className="h-4 w-4" /></>}
      </button>
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
      <button type="button" onClick={onBack} className="inline-flex items-center gap-2 text-sm font-bold text-gray-700 hover:text-black"><ArrowLeft className="h-4 w-4" />返回登录</button>
      <div>
        <p className="text-xs font-bold uppercase text-gray-400">版本 {version}</p>
        <h1 className="mt-1 text-2xl font-extrabold text-gray-950">{kind === 'terms' ? '服务条款' : '隐私说明'}</h1>
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
    onAuthenticated(token);
  };

  const isLegal = path === '/terms' || path === '/privacy';
  const panelWidth = path === '/register' || isLegal ? 'max-w-2xl' : 'max-w-lg';

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
    <div className="min-h-screen bg-[#F4F5F7] px-4 py-8 text-gray-950 sm:py-12">
      <div className={`mx-auto w-full ${panelWidth}`}>
        <header className="mb-5 flex items-center justify-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-[#FFE815] text-xl font-extrabold shadow-sm">闲</div>
          <div><div className="text-lg font-extrabold">闲鱼智控</div><div className="text-xs text-gray-500">自动化控制台</div></div>
        </header>

        <main className="rounded-lg border border-gray-200 bg-white p-5 shadow-[0_16px_45px_rgba(0,0,0,0.06)] sm:p-7">
          {!isLegal ? (
            <nav aria-label="认证方式" className="mb-7 grid grid-cols-3 rounded-lg bg-gray-100 p-1">
              {([
                ['/login', '账号登录'],
                ['/register', '注册账号'],
                ['/forgot-password', '忘记密码'],
              ] as const).map(([target, label]) => (
                <button
                  key={target}
                  type="button"
                  aria-current={path === target ? 'page' : undefined}
                  onClick={() => navigate(target)}
                  className={`min-h-9 rounded-md px-2 text-sm font-bold transition ${path === target ? 'bg-white text-black shadow-sm' : 'text-gray-500 hover:text-gray-900'}`}
                >
                  {label}
                </button>
              ))}
            </nav>
          ) : null}
          {content}
        </main>

        <footer className="mt-4 text-center text-xs text-gray-400">Xianyu AI Manager v1.7.0</footer>
      </div>
    </div>
  );
};

export default AuthPortal;
