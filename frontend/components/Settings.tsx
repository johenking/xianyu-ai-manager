import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Bot, Database, Eye, EyeOff, Loader2, Mail, RefreshCw, Save, Settings as SettingsIcon, TestTube2 } from 'lucide-react';
import {
  confirmSmtpVerification,
  getSettingsSummary,
  getUserSettingsSummary,
  saveSettingsSection,
  saveUserBasicSettings,
  verifySettingsSection,
} from '../services/api';
import {
  SettingsSectionKey,
  SettingsSummary,
  SystemSettings,
  UserBasicSettings,
  UserSettingKey,
  UserSettingSource,
  UserSettingsSummary,
} from '../types';
import { getInitialOpenSection, isSectionDirty } from '../utils/settingsState';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { IconAction, PageHeader, SegmentedNav, WorkSurface } from './ui/ProtectedPage';
import AIProviderManager from './AIProviderManager';
import RegistrationManagement from './RegistrationManagement';

const sectionFields: Record<SettingsSectionKey, string[]> = {
  basic: ['show_default_login_info', 'login_captcha_enabled', 'item_sync_enabled', 'item_sync_interval', 'item_sync_max_pages'],
  ai: ['ai_api_url', 'ai_model', 'default_reply', 'ai_api_key'],
  smtp: ['smtp_server', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_use_tls', 'smtp_use_ssl', 'support_email'],
};

const sectionMeta = {
  basic: { title: '基础设置', detail: '登录与商品同步', icon: Database },
  ai: { title: 'AI 配置', detail: '模型、地址与全局密钥', icon: Bot },
  smtp: { title: 'SMTP 配置', detail: '注册验证与账号找回邮件', icon: Mail },
};

const pickSection = (settings: SystemSettings, section: SettingsSectionKey) => sectionFields[section].reduce<Record<string, unknown>>((result, key) => {
  result[key] = settings[key] ?? '';
  return result;
}, {});

type SmtpOperation = 'verify' | 'confirm' | 'save' | 'reload';

const AdminSettings: React.FC = () => {
  const [summary, setSummary] = useState<SettingsSummary | null>(null);
  const [saved, setSaved] = useState<SystemSettings>({});
  const [draft, setDraft] = useState<SystemSettings>({});
  const [openSection, setOpenSection] = useState<SettingsSectionKey | null>(null);
  const [secretActions, setSecretActions] = useState<Record<string, 'keep' | 'set' | 'clear'>>({});
  const [saving, setSaving] = useState<SettingsSectionKey | null>(null);
  const [verifying, setVerifying] = useState<SettingsSectionKey | null>(null);
  const [verificationState, setVerificationState] = useState<Partial<Record<SettingsSectionKey, { state: string; label: string }>>>({});
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [showSecret, setShowSecret] = useState<Record<string, boolean>>({});
  const [registrationRefreshKey, setRegistrationRefreshKey] = useState(0);
  const [smtpChallenge, setSmtpChallenge] = useState<{ id: string; recipient: string; expiresIn?: number } | null>(null);
  const [smtpVerificationCode, setSmtpVerificationCode] = useState('');
  const [confirmingSmtp, setConfirmingSmtp] = useState(false);
  const smtpVerificationGeneration = useRef(0);
  const smtpOperationRef = useRef<SmtpOperation | null>(null);
  const settingsSaveRef = useRef<SettingsSectionKey | null>(null);
  const [smtpOperation, setSmtpOperation] = useState<SmtpOperation | null>(null);

  const beginSmtpOperation = (operation: SmtpOperation) => {
    if (smtpOperationRef.current) return false;
    smtpOperationRef.current = operation;
    setSmtpOperation(operation);
    return true;
  };

  const endSmtpOperation = (operation: SmtpOperation) => {
    if (smtpOperationRef.current !== operation) return;
    smtpOperationRef.current = null;
    setSmtpOperation(null);
  };

  const invalidateSmtpVerification = () => {
    const generation = smtpVerificationGeneration.current + 1;
    smtpVerificationGeneration.current = generation;
    setSmtpChallenge(null);
    setSmtpVerificationCode('');
    return generation;
  };

  const applySmtpSummary = (next: SettingsSummary) => {
    setSummary(next);
    setSaved(next.settings);
    setDraft((current) => ({
      ...current,
      ...pickSection(next.settings, 'smtp'),
      smtp_password: '',
    }));
    setSecretActions((current) => ({ ...current, smtp_password: 'keep' }));
    setVerificationState((current) => ({ ...current, smtp: undefined }));
  };

  const synchronizeSmtpState = async (): Promise<string> => {
    try {
      applySmtpSummary(await getSettingsSummary());
      return '';
    } catch (error) {
      return error instanceof Error && error.message ? error.message : '设置状态读取失败';
    } finally {
      setRegistrationRefreshKey((value) => value + 1);
    }
  };

  const withSynchronizationWarning = (message: string, synchronizationError: string) => (
    synchronizationError ? `${message}；状态可能未同步：${synchronizationError}` : message
  );

  const load = async () => {
    if (!beginSmtpOperation('reload')) return;
    invalidateSmtpVerification();
    setNotice(null);
    try {
      const next = await getSettingsSummary();
      setSummary(next);
      setSaved(next.settings);
      setDraft({ ...next.settings, ai_api_key: '', smtp_password: '' });
      setSecretActions({ ai_api_key: 'keep', smtp_password: 'keep' });
      setVerificationState({});
      setOpenSection(getInitialOpenSection(next.sections));
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '配置加载失败' });
    } finally {
      endSmtpOperation('reload');
    }
  };

  useEffect(() => { void load(); }, []);

  const dirty = useMemo(() => ({
    basic: isSectionDirty(pickSection(saved, 'basic'), pickSection(draft, 'basic'), {}),
    ai: isSectionDirty(pickSection(saved, 'ai'), pickSection(draft, 'ai'), { ai_api_key: secretActions.ai_api_key || 'keep' }),
    smtp: isSectionDirty(pickSection(saved, 'smtp'), pickSection(draft, 'smtp'), { smtp_password: secretActions.smtp_password || 'keep' }),
  }), [saved, draft, secretActions]);

  const update = (key: string, value: unknown) => {
    const section = (Object.keys(sectionFields) as SettingsSectionKey[]).find((candidate) => sectionFields[candidate].includes(key));
    if (section === 'smtp' && smtpOperationRef.current) return;
    setDraft((current) => ({ ...current, [key]: value }));
    if (section) setVerificationState((current) => ({ ...current, [section]: section === 'smtp' ? { state: 'missing', label: '待重新验证' } : undefined }));
    if (section === 'smtp') invalidateSmtpVerification();
  };

  const save = async (section: SettingsSectionKey) => {
    if (smtpOperationRef.current || settingsSaveRef.current) return;
    if (section === 'smtp') {
      if (!beginSmtpOperation('save')) return;
      invalidateSmtpVerification();
    }
    settingsSaveRef.current = section;
    setSaving(section);
    setNotice(null);
    try {
      const payload = pickSection(draft, section) as Partial<SystemSettings>;
      const actions = section === 'ai' ? { ai_api_key: secretActions.ai_api_key || 'keep' }
        : section === 'smtp' ? { smtp_password: secretActions.smtp_password || 'keep' } : {};
      const result = await saveSettingsSection(section, payload, actions);
      setSummary(result);
      setSaved(result.settings);
      setDraft({ ...result.settings, ai_api_key: '', smtp_password: '' });
      setSecretActions({ ai_api_key: 'keep', smtp_password: 'keep' });
      setVerificationState((current) => ({ ...current, [section]: undefined }));
      setNotice({ tone: 'success', text: `${sectionMeta[section].title}已保存并确认（${result.saved_at.replace('T', ' ')}）` });
      if (section === 'smtp') setRegistrationRefreshKey((value) => value + 1);
      setOpenSection(null);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '保存失败' });
    } finally {
      if (settingsSaveRef.current === section) settingsSaveRef.current = null;
      setSaving(null);
      if (section === 'smtp') endSmtpOperation('save');
    }
  };

  const verify = async (section: 'ai' | 'smtp') => {
    if (section === 'smtp' && (settingsSaveRef.current || !beginSmtpOperation('verify'))) return;
    const smtpGeneration = section === 'smtp' ? invalidateSmtpVerification() : null;
    setVerifying(section);
    setVerificationState((current) => ({ ...current, [section]: { state: 'checking', label: '验证中' } }));
    setNotice(null);
    const actions = section === 'ai' ? { ai_api_key: secretActions.ai_api_key || 'keep' } : { smtp_password: secretActions.smtp_password || 'keep' };
    const payload = pickSection(draft, section) as Partial<SystemSettings>;

    if (section === 'smtp') {
      try {
        let result: Awaited<ReturnType<typeof verifySettingsSection>>;
        try {
          result = await verifySettingsSection(section, payload, actions);
        } catch (error) {
          const originalError = error instanceof Error ? error.message : '验证失败';
          const synchronizationError = await synchronizeSmtpState();
          if (smtpVerificationGeneration.current !== smtpGeneration) return;
          setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '不可用' } }));
          setNotice({ tone: 'error', text: withSynchronizationWarning(originalError, synchronizationError) });
          return;
        }

        const synchronizationError = await synchronizeSmtpState();
        if (smtpVerificationGeneration.current !== smtpGeneration) return;
        if (!result.challenge_id) {
          setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '不可用' } }));
          setNotice({ tone: 'error', text: withSynchronizationWarning(result.message || '服务器未返回 SMTP 验证挑战', synchronizationError) });
          return;
        }
        setSmtpChallenge({ id: result.challenge_id, recipient: result.recipient || result.masked_recipient || '独立收件邮箱', expiresIn: result.expires_in });
        setSmtpVerificationCode('');
        setVerificationState((current) => ({ ...current, smtp: { state: 'checking', label: '待确认收件' } }));
        setNotice({ tone: synchronizationError ? 'error' : 'info', text: withSynchronizationWarning(result.message, synchronizationError) });
      } finally {
        if (smtpVerificationGeneration.current === smtpGeneration) setVerifying(null);
        endSmtpOperation('verify');
      }
      return;
    }

    try {
      const result = await verifySettingsSection(section, payload, actions);
      setVerificationState((current) => ({ ...current, ai: { state: 'ready', label: '可用' } }));
      setNotice({ tone: 'success', text: result.message });
    } catch (error) {
      setVerificationState((current) => ({ ...current, [section]: { state: 'error', label: '不可用' } }));
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '验证失败' });
    } finally {
      setVerifying(null);
    }
  };

  const confirmSmtp = async () => {
    if (settingsSaveRef.current) return;
    if (!beginSmtpOperation('confirm')) return;
    if (!smtpChallenge || !/^\d{6}$/.test(smtpVerificationCode)) {
      setNotice({ tone: 'error', text: '请输入邮件中收到的 6 位验证码' });
      endSmtpOperation('confirm');
      return;
    }
    const smtpGeneration = smtpVerificationGeneration.current;
    setConfirmingSmtp(true);
    setNotice(null);
    let synchronizationError = '';
    try {
      let result: Awaited<ReturnType<typeof confirmSmtpVerification>>;
      try {
        result = await confirmSmtpVerification({
          challenge_id: smtpChallenge.id,
          verification_code: smtpVerificationCode,
        });
      } catch (error) {
        const originalError = error instanceof Error ? error.message : 'SMTP 实收验证失败';
        const synchronizationError = await synchronizeSmtpState();
        if (smtpVerificationGeneration.current !== smtpGeneration) return;
        setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '确认失败' } }));
        setNotice({ tone: 'error', text: withSynchronizationWarning(originalError, synchronizationError) });
        return;
      }

      synchronizationError = await synchronizeSmtpState();
      if (smtpVerificationGeneration.current !== smtpGeneration) return;
      if (!result.success) {
        setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '确认失败' } }));
        setNotice({ tone: 'error', text: withSynchronizationWarning(result.message || 'SMTP 实收验证失败', synchronizationError) });
        return;
      }
      setVerificationState((current) => ({ ...current, smtp: { state: 'ready', label: '已验证' } }));
      setSmtpChallenge(null);
      setSmtpVerificationCode('');
      setNotice({ tone: synchronizationError ? 'error' : 'success', text: withSynchronizationWarning(result.message, synchronizationError) });
    } catch (error) {
      if (smtpVerificationGeneration.current !== smtpGeneration) return;
      setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '确认失败' } }));
      setNotice({ tone: 'error', text: withSynchronizationWarning(error instanceof Error ? error.message : 'SMTP 实收验证失败', synchronizationError) });
    } finally {
      if (smtpVerificationGeneration.current === smtpGeneration) setConfirmingSmtp(false);
      endSmtpOperation('confirm');
    }
  };

  const applyQqMailPreset = () => {
    if (smtpOperationRef.current) return;
    setDraft((current) => ({
      ...current,
      smtp_server: 'smtp.qq.com',
      smtp_port: 465,
      smtp_use_ssl: true,
      smtp_use_tls: false,
    }));
    setVerificationState((current) => ({ ...current, smtp: { state: 'missing', label: '待重新验证' } }));
    invalidateSmtpVerification();
  };

  if (!summary) return <div className="p-8 text-center text-gray-500">{notice ? <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice> : '加载配置中...'}</div>;
  const smtpBusy = smtpOperation !== null;

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-16">
      <PageHeader
        icon={SettingsIcon}
        title="系统与 AI"
        description="配置保存、连接验证和运行状态"
        actions={<IconAction icon={smtpBusy ? Loader2 : RefreshCw} label="重新读取" busy={smtpBusy} onClick={() => void load()} disabled={smtpBusy} />}
      />

      <SegmentedNav
        value={openSection || ''}
        expandedValue={openSection}
        onChange={(value) => setOpenSection((current) => current === value ? null : value as SettingsSectionKey)}
        items={(Object.keys(sectionMeta) as SettingsSectionKey[]).map((section) => {
          const meta = sectionMeta[section]; const Icon = meta.icon;
          const state = dirty[section]
            ? { state: 'dirty', label: '未保存' }
            : verificationState[section] || summary.sections[section];
          return { id: section, label: meta.title, detail: meta.detail, icon: Icon, trailing: <StatusBadge state={state.state} label={state.label} /> };
        })}
      />

      {notice && <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice>}

      {openSection && <WorkSurface className="p-5 sm:p-7">
        <div className="mb-6 flex items-center justify-between"><div><h3 className="text-lg font-extrabold text-gray-900">{sectionMeta[openSection].title}</h3><p className="mt-1 text-sm text-gray-500">{dirty[openSection] ? '有尚未保存的修改' : '当前内容与数据库一致'}</p></div><StatusBadge state={dirty[openSection] ? 'dirty' : (verificationState[openSection]?.state || summary.sections[openSection].state)} label={dirty[openSection] ? '未保存' : (verificationState[openSection]?.label || summary.sections[openSection].label)} /></div>

        {openSection === 'basic' && <div className="space-y-4">
          <ToggleRow label="显示默认登录信息" detail="登录页显示默认账号提示" checked={Boolean(draft.show_default_login_info)} onChange={(v) => update('show_default_login_info', v)} />
          <ToggleRow label="登录滑动验证码" detail="账号密码登录时启用验证流程" checked={Boolean(draft.login_captcha_enabled)} onChange={(v) => update('login_captcha_enabled', v)} />
          <ToggleRow label="商品自动同步" detail="定时同步当前账号的商品资料" checked={Boolean(draft.item_sync_enabled)} onChange={(v) => update('item_sync_enabled', v)} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2"><Field label="同步间隔（分钟）" type="number" value={Math.round(Number(draft.item_sync_interval || 600) / 60)} onChange={(v) => update('item_sync_interval', Number(v) * 60)} /><Field label="最多同步页数" type="number" value={draft.item_sync_max_pages || 5} onChange={(v) => update('item_sync_max_pages', Number(v))} /></div>
        </div>}

        {openSection === 'ai' && <div className="space-y-4">
          <AIProviderManager />
          <InlineNotice>下面是旧版全局配置，仅用于尚未绑定平台库的兼容账号。新账号请使用上方平台配置库。</InlineNotice>
          <Field label="API 地址" value={draft.ai_api_url || ''} onChange={(v) => update('ai_api_url', v)} placeholder="https://api.deepseek.com" />
          <Field label="模型" value={draft.ai_model || ''} onChange={(v) => update('ai_model', v)} placeholder="deepseek-chat" />
          <SecretField label="API Key" name="ai_api_key" configured={Boolean(saved.ai_api_key_configured)} masked={saved.ai_api_key_masked || ''} value={draft.ai_api_key || ''} show={Boolean(showSecret.ai_api_key)} onToggle={() => setShowSecret((s) => ({ ...s, ai_api_key: !s.ai_api_key }))} onChange={(v) => { update('ai_api_key', v); setSecretActions((s) => ({ ...s, ai_api_key: v ? 'set' : 'keep' })); }} onClear={() => { update('ai_api_key', ''); setSecretActions((s) => ({ ...s, ai_api_key: 'clear' })); }} />
          <label className="block text-sm font-bold text-gray-800">默认回复<textarea value={draft.default_reply || ''} onChange={(e) => update('default_reply', e.target.value)} className="ios-input mt-2 min-h-24 w-full rounded-xl px-3 py-2.5 font-normal" /></label>
        </div>}

        {openSection === 'smtp' && <div className="space-y-4">
          <InlineNotice>注册和账号找回依赖已验证的 SMTP。支持邮箱必须是独立、可收信的地址，并实际收到邮件后输入验证码确认。修改任何连接配置后都需要重新验证。</InlineNotice>
          <button type="button" onClick={applyQqMailPreset} disabled={smtpBusy} className="inline-flex h-10 items-center justify-center rounded-lg border border-gray-300 bg-white px-4 text-sm font-bold text-gray-800 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50">QQ 邮箱预设</button>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_140px]"><Field label="SMTP 服务器" value={draft.smtp_server || ''} onChange={(v) => update('smtp_server', v)} placeholder="smtp.qq.com" disabled={smtpBusy} /><Field label="端口" type="number" value={draft.smtp_port || 587} onChange={(v) => update('smtp_port', Number(v))} disabled={smtpBusy} /></div>
          <Field label="发件邮箱" value={draft.smtp_user || ''} onChange={(v) => update('smtp_user', v)} placeholder="name@example.com" disabled={smtpBusy} />
          <Field label="支持邮箱" value={draft.support_email || ''} onChange={(v) => update('support_email', v)} placeholder="用于协议页联系与 SMTP 送达验证" disabled={smtpBusy} />
          <SecretField label="邮箱授权码" name="smtp_password" configured={Boolean(saved.smtp_password_configured)} masked={saved.smtp_password_masked || ''} value={draft.smtp_password || ''} show={Boolean(showSecret.smtp_password)} onToggle={() => setShowSecret((s) => ({ ...s, smtp_password: !s.smtp_password }))} onChange={(v) => { if (smtpOperationRef.current) return; update('smtp_password', v); setSecretActions((s) => ({ ...s, smtp_password: v ? 'set' : 'keep' })); }} onClear={() => { if (smtpOperationRef.current) return; update('smtp_password', ''); setSecretActions((s) => ({ ...s, smtp_password: 'clear' })); }} disabled={smtpBusy} />
          <Field label="发件人显示名" value={draft.smtp_from || ''} onChange={(v) => update('smtp_from', v)} placeholder="闲鱼商品管理" disabled={smtpBusy} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2"><ToggleRow label="STARTTLS" detail="常用于587端口" checked={Boolean(draft.smtp_use_tls ?? true)} onChange={(v) => update('smtp_use_tls', v)} disabled={smtpBusy} /><ToggleRow label="SSL" detail="常用于465端口" checked={Boolean(draft.smtp_use_ssl)} onChange={(v) => update('smtp_use_ssl', v)} disabled={smtpBusy} /></div>
          {smtpChallenge ? <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4">
            <p className="text-sm font-bold text-gray-900">验证邮件已发送至 {smtpChallenge.recipient}</p>
            <p className="mt-1 text-xs text-gray-600">请检查该独立邮箱并输入 6 位收件码{smtpChallenge.expiresIn ? `，${Math.ceil(smtpChallenge.expiresIn / 60)} 分钟内有效` : ''}。</p>
            <div className="mt-3 flex flex-col gap-3 sm:flex-row">
              <label className="block flex-1 text-sm font-bold text-gray-800">SMTP 收件验证码<input aria-label="SMTP 收件验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={smtpVerificationCode} onChange={(event) => setSmtpVerificationCode(event.target.value.replace(/\D/g, ''))} placeholder="6 位数字" disabled={smtpBusy} className="ios-input mt-2 h-10 w-full rounded-xl px-3 font-normal disabled:cursor-not-allowed disabled:bg-gray-100" /></label>
              <button type="button" onClick={() => void confirmSmtp()} disabled={confirmingSmtp || saving !== null || !/^\d{6}$/.test(smtpVerificationCode)} className="ios-btn-primary mt-auto inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold">{confirmingSmtp ? <Loader2 className="h-4 w-4 animate-spin" /> : null}确认收件码</button>
            </div>
          </div> : null}
        </div>}

        <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          {(openSection === 'ai' || openSection === 'smtp') && <button onClick={() => void verify(openSection)} disabled={verifying === openSection || (openSection === 'smtp' && (smtpBusy || saving !== null))} className="ios-btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50">{verifying === openSection ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}验证连接</button>}
          <button onClick={() => void save(openSection)} disabled={saving !== null || smtpBusy || !dirty[openSection]} className="ios-btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-6 text-sm font-extrabold">{saving === openSection ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}保存并折叠</button>
        </div>
      </WorkSurface>}

      <RegistrationManagement refreshKey={registrationRefreshKey} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3"><Metric label="账号管理器" value={summary.runtime.cookie_manager ? '运行中' : '未就绪'} /><Metric label="账号数量" value={`${summary.runtime.account_count}`} /><Metric label="监听任务" value={`${summary.runtime.active_tasks}`} /></div>
    </div>
  );
};

const sourceLabels: Record<UserSettingSource, string> = {
  user: '个人设置',
  global: '继承系统默认',
};

const SettingSource: React.FC<{ source: UserSettingSource }> = ({ source }) => (
  <span className="inline-flex rounded-md border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs font-bold text-gray-600">
    {sourceLabels[source]}
  </span>
);

const UserSettings: React.FC = () => {
  const [summary, setSummary] = useState<UserSettingsSummary | null>(null);
  const [draft, setDraft] = useState<UserBasicSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [validationErrors, setValidationErrors] = useState<Partial<Record<UserSettingKey, string>>>({});
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const changedSettings = useMemo(() => {
    if (!summary || !draft) return {};
    return (Object.keys(draft) as UserSettingKey[]).reduce<Partial<UserBasicSettings>>((changes, key) => {
      if (draft[key] !== summary.settings[key]) {
        Object.assign(changes, { [key]: draft[key] });
      }
      return changes;
    }, {});
  }, [draft, summary]);
  const dirty = Object.keys(changedSettings).length > 0;

  const load = async () => {
    setLoading(true);
    setLoadError('');
    setNotice(null);
    try {
      const result = await getUserSettingsSummary();
      setSummary(result);
      setDraft(result.settings);
      setValidationErrors({});
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '个人设置读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const update = <K extends UserSettingKey>(key: K, value: UserBasicSettings[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setValidationErrors((current) => ({ ...current, [key]: undefined }));
    setNotice(null);
  };

  const save = async () => {
    if (!draft || saving) return;
    const errors: Partial<Record<UserSettingKey, string>> = {};
    if (!Number.isInteger(draft.item_sync_interval) || draft.item_sync_interval < 60 || draft.item_sync_interval > 86400) {
      errors.item_sync_interval = '同步间隔需为 60 到 86400 秒';
    }
    if (!Number.isInteger(draft.item_sync_max_pages) || draft.item_sync_max_pages < 1 || draft.item_sync_max_pages > 50) {
      errors.item_sync_max_pages = '最多同步页数需为 1 到 50';
    }
    setValidationErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSaving(true);
    setNotice(null);
    try {
      const result = await saveUserBasicSettings(changedSettings);
      setSummary(result);
      setDraft(result.settings);
      setNotice({ tone: 'success', text: result.message || '个人设置已保存' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '个人设置保存失败' });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-center text-gray-500">加载个人设置中...</div>;
  }

  if (loadError || !summary || !draft) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-8 text-center">
        <InlineNotice tone="error">{loadError || '个人设置读取失败'}</InlineNotice>
        <button type="button" onClick={() => void load()} className="ios-btn-primary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold">
          <RefreshCw className="h-4 w-4" />重试
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-16">
      <PageHeader icon={SettingsIcon} title="个人设置与 AI" description="管理自己的同步频率和 AI 平台配置" />

      {notice ? <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice> : null}

      <WorkSurface className="p-5 sm:p-7">
        <div className="mb-6">
          <h3 className="text-lg font-extrabold text-gray-900">商品自动同步</h3>
          <p className="mt-1 text-sm text-gray-500">设置你的闲鱼账号商品同步节奏</p>
        </div>

        <div className="space-y-4">
          <div className="flex min-h-16 items-center justify-between gap-4 rounded-lg bg-gray-50 px-4 py-3">
            <div>
              <div className="flex flex-wrap items-center gap-2"><span className="font-bold text-gray-900">商品自动同步</span><SettingSource source={summary.sources.item_sync_enabled} /></div>
              <div className="mt-0.5 text-xs text-gray-500">定时同步你名下账号的商品资料</div>
            </div>
            <ToggleControl checked={draft.item_sync_enabled} onChange={(value) => update('item_sync_enabled', value)} label="商品自动同步" />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="block text-sm font-bold text-gray-800">
              <span className="flex flex-wrap items-center gap-2">同步间隔（秒）<SettingSource source={summary.sources.item_sync_interval} /></span>
              <input aria-label="同步间隔（秒）" aria-invalid={Boolean(validationErrors.item_sync_interval)} aria-describedby={validationErrors.item_sync_interval ? 'item-sync-interval-error' : undefined} type="number" min={60} max={86400} step={1} value={draft.item_sync_interval} onChange={(event) => update('item_sync_interval', Number(event.target.value))} className="ios-input mt-2 h-11 w-full rounded-xl px-3 font-normal" />
              {validationErrors.item_sync_interval ? <span id="item-sync-interval-error" className="mt-1.5 block text-xs font-medium text-red-600">{validationErrors.item_sync_interval}</span> : null}
            </label>
            <label className="block text-sm font-bold text-gray-800">
              <span className="flex flex-wrap items-center gap-2">最多同步页数<SettingSource source={summary.sources.item_sync_max_pages} /></span>
              <input aria-label="最多同步页数" aria-invalid={Boolean(validationErrors.item_sync_max_pages)} aria-describedby={validationErrors.item_sync_max_pages ? 'item-sync-pages-error' : undefined} type="number" min={1} max={50} step={1} value={draft.item_sync_max_pages} onChange={(event) => update('item_sync_max_pages', Number(event.target.value))} className="ios-input mt-2 h-11 w-full rounded-xl px-3 font-normal" />
              {validationErrors.item_sync_max_pages ? <span id="item-sync-pages-error" className="mt-1.5 block text-xs font-medium text-red-600">{validationErrors.item_sync_max_pages}</span> : null}
            </label>
          </div>
        </div>

        <div className="mt-7 flex justify-end">
          <button type="button" onClick={() => void save()} disabled={saving || !dirty} className="ios-btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-6 text-sm font-extrabold">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}保存设置
          </button>
        </div>
      </WorkSurface>

      <WorkSurface className="space-y-4 p-5 sm:p-7">
        <div><h3 className="text-lg font-extrabold text-gray-900">AI 平台</h3><p className="mt-1 text-sm text-gray-500">管理自己的模型平台和密钥</p></div>
        <AIProviderManager />
      </WorkSurface>
    </div>
  );
};

const Settings: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => (
  isAdmin ? <AdminSettings /> : <UserSettings />
);

const ToggleRow: React.FC<{ label: string; detail: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean }> = ({ label, detail, checked, onChange, disabled }) => <div className="flex min-h-16 items-center justify-between gap-4 rounded-xl bg-gray-50 px-4 py-3"><div><div className="font-bold text-gray-900">{label}</div><div className="mt-0.5 text-xs text-gray-500">{detail}</div></div><ToggleControl checked={checked} onChange={onChange} label={label} disabled={disabled} /></div>;
const Field: React.FC<{ label: string; value: string | number; onChange: (value: string) => void; placeholder?: string; type?: string; disabled?: boolean }> = ({ label, value, onChange, placeholder, type = 'text', disabled }) => <label className="block text-sm font-bold text-gray-800">{label}<input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} disabled={disabled} className="ios-input mt-2 h-11 w-full rounded-xl px-3 font-normal disabled:cursor-not-allowed disabled:bg-gray-100" /></label>;
const SecretField: React.FC<{ label: string; name: string; configured: boolean; masked: string; value: string; show: boolean; onToggle: () => void; onChange: (value: string) => void; onClear: () => void; disabled?: boolean }> = ({ label, configured, masked, value, show, onToggle, onChange, onClear, disabled }) => <label className="block text-sm font-bold text-gray-800">{label}<div className="relative mt-2"><input type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)} placeholder={configured ? `已配置 ${masked}，留空保持不变` : '尚未配置'} disabled={disabled} className="ios-input h-11 w-full rounded-xl px-3 pr-11 font-mono font-normal disabled:cursor-not-allowed disabled:bg-gray-100" /><button type="button" aria-label={show ? '隐藏密钥' : '显示密钥'} onClick={onToggle} disabled={disabled} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-2 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50">{show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div>{configured && <button type="button" onClick={onClear} disabled={disabled} className="mt-2 text-xs font-bold text-red-600 hover:underline disabled:cursor-not-allowed disabled:opacity-50">清除已保存密钥</button>}</label>;
const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => <WorkSurface as="div" className="flex items-center justify-between px-4 py-3 text-sm"><span className="text-gray-500">{label}</span><span className="font-bold text-gray-900">{value}</span></WorkSurface>;

export default Settings;
