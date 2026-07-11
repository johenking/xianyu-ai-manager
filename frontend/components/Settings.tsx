import React, { useEffect, useMemo, useState } from 'react';
import { Bot, ChevronDown, Database, Eye, EyeOff, Loader2, Mail, RefreshCw, Save, Settings as SettingsIcon, TestTube2 } from 'lucide-react';
import { confirmSmtpVerification, getSettingsSummary, saveSettingsSection, verifySettingsSection } from '../services/api';
import { SettingsSectionKey, SettingsSummary, SystemSettings } from '../types';
import { getInitialOpenSection, isSectionDirty } from '../utils/settingsState';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
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

const Settings: React.FC = () => {
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

  const load = async () => {
    setNotice(null);
    try {
      const next = await getSettingsSummary();
      setSummary(next);
      setSaved(next.settings);
      setDraft({ ...next.settings, ai_api_key: '', smtp_password: '' });
      setSecretActions({ ai_api_key: 'keep', smtp_password: 'keep' });
      setVerificationState({});
      setSmtpChallenge(null);
      setSmtpVerificationCode('');
      setOpenSection(getInitialOpenSection(next.sections));
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '配置加载失败' });
    }
  };

  useEffect(() => { void load(); }, []);

  const dirty = useMemo(() => ({
    basic: isSectionDirty(pickSection(saved, 'basic'), pickSection(draft, 'basic'), {}),
    ai: isSectionDirty(pickSection(saved, 'ai'), pickSection(draft, 'ai'), { ai_api_key: secretActions.ai_api_key || 'keep' }),
    smtp: isSectionDirty(pickSection(saved, 'smtp'), pickSection(draft, 'smtp'), { smtp_password: secretActions.smtp_password || 'keep' }),
  }), [saved, draft, secretActions]);

  const update = (key: string, value: unknown) => {
    setDraft((current) => ({ ...current, [key]: value }));
    const section = (Object.keys(sectionFields) as SettingsSectionKey[]).find((candidate) => sectionFields[candidate].includes(key));
    if (section) setVerificationState((current) => ({ ...current, [section]: section === 'smtp' ? { state: 'missing', label: '待重新验证' } : undefined }));
    if (section === 'smtp') {
      setSmtpChallenge(null);
      setSmtpVerificationCode('');
    }
  };

  const save = async (section: SettingsSectionKey) => {
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
      if (section === 'smtp') {
        setSmtpChallenge(null);
        setSmtpVerificationCode('');
      }
      setNotice({ tone: 'success', text: `${sectionMeta[section].title}已保存并确认（${result.saved_at.replace('T', ' ')}）` });
      if (section === 'smtp') setRegistrationRefreshKey((value) => value + 1);
      setOpenSection(null);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '保存失败' });
    } finally {
      setSaving(null);
    }
  };

  const verify = async (section: 'ai' | 'smtp') => {
    setVerifying(section);
    setVerificationState((current) => ({ ...current, [section]: { state: 'checking', label: '验证中' } }));
    setNotice(null);
    try {
      const actions = section === 'ai' ? { ai_api_key: secretActions.ai_api_key || 'keep' } : { smtp_password: secretActions.smtp_password || 'keep' };
      const result = await verifySettingsSection(section, pickSection(draft, section) as Partial<SystemSettings>, actions);
      if (section === 'smtp') {
        if (!result.challenge_id) throw new Error(result.message || '服务器未返回 SMTP 验证挑战');
        setSmtpChallenge({ id: result.challenge_id, recipient: result.recipient || result.masked_recipient || '独立收件邮箱', expiresIn: result.expires_in });
        setSmtpVerificationCode('');
        setVerificationState((current) => ({ ...current, smtp: { state: 'checking', label: '待确认收件' } }));
        setNotice({ tone: 'info', text: result.message });
      } else {
        setVerificationState((current) => ({ ...current, ai: { state: 'ready', label: '可用' } }));
        setNotice({ tone: 'success', text: result.message });
      }
    } catch (error) {
      setVerificationState((current) => ({ ...current, [section]: { state: 'error', label: '不可用' } }));
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '验证失败' });
    } finally {
      setVerifying(null);
    }
  };

  const confirmSmtp = async () => {
    if (!smtpChallenge || !/^\d{6}$/.test(smtpVerificationCode)) {
      setNotice({ tone: 'error', text: '请输入邮件中收到的 6 位验证码' });
      return;
    }
    setConfirmingSmtp(true);
    setNotice(null);
    try {
      const result = await confirmSmtpVerification({
        challenge_id: smtpChallenge.id,
        verification_code: smtpVerificationCode,
      });
      if (!result.success) throw new Error(result.message || 'SMTP 实收验证失败');
      setVerificationState((current) => ({ ...current, smtp: { state: 'ready', label: '已验证' } }));
      setSmtpChallenge(null);
      setSmtpVerificationCode('');
      setNotice({ tone: 'success', text: result.message });
      setRegistrationRefreshKey((value) => value + 1);
    } catch (error) {
      setVerificationState((current) => ({ ...current, smtp: { state: 'error', label: '确认失败' } }));
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : 'SMTP 实收验证失败' });
    } finally {
      setConfirmingSmtp(false);
    }
  };

  const applyQqMailPreset = () => {
    setDraft((current) => ({
      ...current,
      smtp_server: 'smtp.qq.com',
      smtp_port: 465,
      smtp_use_ssl: true,
      smtp_use_tls: false,
    }));
    setVerificationState((current) => ({ ...current, smtp: { state: 'missing', label: '待重新验证' } }));
    setSmtpChallenge(null);
    setSmtpVerificationCode('');
  };

  if (!summary) return <div className="p-8 text-center text-gray-500">{notice ? <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice> : '加载配置中...'}</div>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-16">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-gray-900 text-[#FFE815]"><SettingsIcon className="h-5 w-5" /></div>
          <div><h2 className="text-2xl font-extrabold text-gray-900">系统与 AI</h2><p className="mt-1 text-sm text-gray-500">配置保存、连接验证和运行状态都在这里确认</p></div>
        </div>
        <button onClick={() => void load()} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-white px-4 text-sm font-bold text-gray-700 shadow-sm ring-1 ring-gray-200 hover:bg-gray-50"><RefreshCw className="h-4 w-4" />重新读取</button>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {(Object.keys(sectionMeta) as SettingsSectionKey[]).map((section) => {
          const meta = sectionMeta[section]; const Icon = meta.icon; const isOpen = openSection === section;
          const state = dirty[section]
            ? { state: 'dirty', label: '未保存' }
            : verificationState[section] || summary.sections[section];
          return <button key={section} aria-expanded={isOpen} onClick={() => setOpenSection(isOpen ? null : section)} className={`flex min-h-20 items-center gap-3 rounded-full border px-4 py-3 text-left transition ${isOpen ? 'border-yellow-400 bg-yellow-50 shadow-sm' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gray-900 text-[#FFE815]"><Icon className="h-5 w-5" /></span>
            <span className="min-w-0 flex-1"><span className="block font-extrabold text-gray-900">{meta.title}</span><span className="block truncate text-xs text-gray-500">{meta.detail}</span></span>
            <span className="flex shrink-0 items-center gap-2"><StatusBadge state={state.state} label={state.label} /><ChevronDown className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} /></span>
          </button>;
        })}
      </div>

      {notice && <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice>}

      {openSection && <section className="rounded-lg bg-white p-5 shadow-sm ring-1 ring-gray-200 sm:p-7">
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
          <label className="block text-sm font-bold text-gray-800">默认回复<textarea value={draft.default_reply || ''} onChange={(e) => update('default_reply', e.target.value)} className="mt-2 min-h-24 w-full rounded-lg border border-gray-200 px-3 py-2.5 font-normal outline-none focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100" /></label>
        </div>}

        {openSection === 'smtp' && <div className="space-y-4">
          <InlineNotice>注册和账号找回依赖已验证的 SMTP。支持邮箱必须是独立、可收信的地址，并实际收到邮件后输入验证码确认。修改任何连接配置后都需要重新验证。</InlineNotice>
          <button type="button" onClick={applyQqMailPreset} className="inline-flex h-10 items-center justify-center rounded-lg border border-gray-300 bg-white px-4 text-sm font-bold text-gray-800 hover:bg-gray-50">QQ 邮箱预设</button>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_140px]"><Field label="SMTP 服务器" value={draft.smtp_server || ''} onChange={(v) => update('smtp_server', v)} placeholder="smtp.qq.com" /><Field label="端口" type="number" value={draft.smtp_port || 587} onChange={(v) => update('smtp_port', Number(v))} /></div>
          <Field label="发件邮箱" value={draft.smtp_user || ''} onChange={(v) => update('smtp_user', v)} placeholder="name@example.com" />
          <Field label="支持邮箱" value={draft.support_email || ''} onChange={(v) => update('support_email', v)} placeholder="用于协议页联系与 SMTP 送达验证" />
          <SecretField label="邮箱授权码" name="smtp_password" configured={Boolean(saved.smtp_password_configured)} masked={saved.smtp_password_masked || ''} value={draft.smtp_password || ''} show={Boolean(showSecret.smtp_password)} onToggle={() => setShowSecret((s) => ({ ...s, smtp_password: !s.smtp_password }))} onChange={(v) => { update('smtp_password', v); setSecretActions((s) => ({ ...s, smtp_password: v ? 'set' : 'keep' })); }} onClear={() => { update('smtp_password', ''); setSecretActions((s) => ({ ...s, smtp_password: 'clear' })); }} />
          <Field label="发件人显示名" value={draft.smtp_from || ''} onChange={(v) => update('smtp_from', v)} placeholder="闲鱼商品管理" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2"><ToggleRow label="STARTTLS" detail="常用于587端口" checked={Boolean(draft.smtp_use_tls ?? true)} onChange={(v) => update('smtp_use_tls', v)} /><ToggleRow label="SSL" detail="常用于465端口" checked={Boolean(draft.smtp_use_ssl)} onChange={(v) => update('smtp_use_ssl', v)} /></div>
          {smtpChallenge ? <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
            <p className="text-sm font-bold text-blue-900">验证邮件已发送至 {smtpChallenge.recipient}</p>
            <p className="mt-1 text-xs text-blue-700">请检查该独立邮箱并输入 6 位收件码{smtpChallenge.expiresIn ? `，${Math.ceil(smtpChallenge.expiresIn / 60)} 分钟内有效` : ''}。</p>
            <div className="mt-3 flex flex-col gap-3 sm:flex-row">
              <label className="block flex-1 text-sm font-bold text-gray-800">SMTP 收件验证码<input aria-label="SMTP 收件验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={smtpVerificationCode} onChange={(event) => setSmtpVerificationCode(event.target.value.replace(/\D/g, ''))} placeholder="6 位数字" className="mt-2 h-10 w-full rounded-lg border border-blue-200 bg-white px-3 font-normal outline-none focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100" /></label>
              <button type="button" onClick={() => void confirmSmtp()} disabled={confirmingSmtp || !/^\d{6}$/.test(smtpVerificationCode)} className="mt-auto inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-900 px-4 text-sm font-bold text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50">{confirmingSmtp ? <Loader2 className="h-4 w-4 animate-spin" /> : null}确认收件码</button>
            </div>
          </div> : null}
        </div>}

        <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          {(openSection === 'ai' || openSection === 'smtp') && <button onClick={() => void verify(openSection)} disabled={verifying === openSection} className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-gray-100 px-5 text-sm font-bold text-gray-800 hover:bg-gray-200 disabled:opacity-50">{verifying === openSection ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}验证连接</button>}
          <button onClick={() => void save(openSection)} disabled={saving === openSection || !dirty[openSection]} className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-[#FFE815] px-6 text-sm font-extrabold text-black hover:bg-yellow-300 disabled:cursor-not-allowed disabled:opacity-50">{saving === openSection ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}保存并折叠</button>
        </div>
      </section>}

      <RegistrationManagement refreshKey={registrationRefreshKey} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3"><Metric label="账号管理器" value={summary.runtime.cookie_manager ? '运行中' : '未就绪'} /><Metric label="账号数量" value={`${summary.runtime.account_count}`} /><Metric label="监听任务" value={`${summary.runtime.active_tasks}`} /></div>
    </div>
  );
};

const ToggleRow: React.FC<{ label: string; detail: string; checked: boolean; onChange: (value: boolean) => void }> = ({ label, detail, checked, onChange }) => <div className="flex min-h-16 items-center justify-between gap-4 rounded-lg bg-gray-50 px-4 py-3"><div><div className="font-bold text-gray-900">{label}</div><div className="mt-0.5 text-xs text-gray-500">{detail}</div></div><ToggleControl checked={checked} onChange={onChange} label={label} /></div>;
const Field: React.FC<{ label: string; value: string | number; onChange: (value: string) => void; placeholder?: string; type?: string }> = ({ label, value, onChange, placeholder, type = 'text' }) => <label className="block text-sm font-bold text-gray-800">{label}<input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="mt-2 h-11 w-full rounded-lg border border-gray-200 px-3 font-normal outline-none focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100" /></label>;
const SecretField: React.FC<{ label: string; name: string; configured: boolean; masked: string; value: string; show: boolean; onToggle: () => void; onChange: (value: string) => void; onClear: () => void }> = ({ label, configured, masked, value, show, onToggle, onChange, onClear }) => <label className="block text-sm font-bold text-gray-800">{label}<div className="relative mt-2"><input type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)} placeholder={configured ? `已配置 ${masked}，留空保持不变` : '尚未配置'} className="h-11 w-full rounded-lg border border-gray-200 px-3 pr-11 font-mono font-normal outline-none focus:border-yellow-400 focus:ring-2 focus:ring-yellow-100" /><button type="button" aria-label={show ? '隐藏密钥' : '显示密钥'} onClick={onToggle} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-2 text-gray-500 hover:bg-gray-100">{show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div>{configured && <button type="button" onClick={onClear} className="mt-2 text-xs font-bold text-red-600 hover:underline">清除已保存密钥</button>}</label>;
const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => <div className="flex items-center justify-between rounded-lg bg-white px-4 py-3 text-sm shadow-sm ring-1 ring-gray-200"><span className="text-gray-500">{label}</span><span className="font-bold text-gray-900">{value}</span></div>;

export default Settings;
