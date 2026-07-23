import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, AccountSessionRefreshStatus, AIProviderProfile, AIReplySettings, AutoReplyDiagnostics } from '../types';
import AITrainingLab from './AITrainingLab';
import ModelSelector from './ModelSelector';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { AccountAvatar, CookieEditor } from './ui/AccountVisuals';
import {
  getAccountDetails,
  updateAccountStatus,
  deleteAccount,
  generateQRLogin,
  checkQRLoginStatus,
  continueQRLoginAfterVerification,
  createBrowserExtensionPairing,
  getBrowserExtensionPairing,
  createOfficialLoginSession,
  getOfficialLoginSession,
  showOfficialLoginBrowser,
  cancelOfficialLoginSession,
  addAccountCookie,
  updateAccountRemark,
  updateAccountAutoConfirm,
  updateAccountPauseDuration,
  updateAccountCookie,
  updateAccountLoginInfo,
  updateAccountCookieRefreshSettings,
  updateAccountAISettings,
  getAllAISettings,
  getAccountAISettings,
  getAutoReplyDiagnostics,
  getAccountSessionStatus,
  refreshAccountSession,
  cancelAccountSessionRefresh,
  showAccountSessionRefreshBrowser,
  getAIProviders,
  refreshAIProviderModels,
  testAIProvider
} from '../services/api';
import {
  Plus, Power, Edit2, Trash2, QrCode, X, Check, Loader2,
  MessageSquare, RefreshCw, Save, User, Clock, MessageCircle,
  Upload, Key, Eye, EyeOff, Bot, Settings, ExternalLink, Chrome, Copy,
  Smartphone, ChevronDown, AlertTriangle, ShieldCheck
} from 'lucide-react';

type ModalType = 'edit' | 'ai-settings' | null;
type AddLoginMethod = 'qr' | 'sms' | 'extension' | 'password' | 'cookie';
type AddLoginStatus = 'idle' | 'processing' | 'success' | 'failed' | 'verification_required';

const DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES = 1440;
const ACTIVE_SESSION_REFRESH_STATES = new Set(['refreshing', 'verification_required']);
const COOKIE_REFRESH_INTERVAL_OPTIONS = [
  { value: 60, label: '1 小时' },
  { value: 360, label: '6 小时' },
  { value: 720, label: '12 小时' },
  { value: 1440, label: '24 小时' },
  { value: 4320, label: '3 天' },
  { value: 10080, label: '7 天' },
];

const formatCookieRefreshInterval = (minutes?: number) => {
  const value = minutes || DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES;
  if (value % 1440 === 0) return `${value / 1440} 天`;
  if (value % 60 === 0) return `${value / 60} 小时`;
  return `${value} 分钟`;
};

const reauthActionLabel = (account: AccountDetail) => {
  if (account.reauth_action === 'qr_login') return '重新扫码';
  if (account.reauth_action === 'sms_login') return '验证码登录';
  if (account.reauth_action === 'password_login') return '账号密码登录';
  if (account.reauth_action === 'chrome_extension_import') return '重新导入';
  if (account.reauth_action === 'manual_cookie') return '重新填写';
  return '重新登录';
};

const AccountList: React.FC = () => {
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [activeAddMethod, setActiveAddMethod] = useState<AddLoginMethod>('qr');
  const [qrCodeUrl, setQrCodeUrl] = useState<string>('');
  const [qrSessionId, setQrSessionId] = useState<string>('');
  const [qrStatus, setQrStatus] = useState<string>('pending');
  const [qrMessage, setQrMessage] = useState<string>('');
  const [qrVerificationImage, setQrVerificationImage] = useState<string>('');
  const qrPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const qrHadVerificationRef = useRef(false);
  const passwordPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const smsPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const extensionPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [extensionPairing, setExtensionPairing] = useState<Awaited<ReturnType<typeof createBrowserExtensionPairing>> | null>(null);
  const [extensionMessage, setExtensionMessage] = useState('');
  const [extensionBusy, setExtensionBusy] = useState(false);
  const [extensionCopied, setExtensionCopied] = useState(false);
  const [showAdvancedLogin, setShowAdvancedLogin] = useState(false);
  const activeOfficialSessionRef = useRef<string>('');
  const [activeModal, setActiveModal] = useState<ModalType>(null);
  const [editingAccount, setEditingAccount] = useState<AccountDetail | null>(null);
  const [trainingAccount, setTrainingAccount] = useState<AccountDetail | null>(null);
  const [diagnostics, setDiagnostics] = useState<Record<string, AutoReplyDiagnostics>>({});
  const [diagnosingId, setDiagnosingId] = useState<string>('');
  const [sessionStatuses, setSessionStatuses] = useState<Record<string, AccountSessionRefreshStatus>>({});
  const [refreshingSessionId, setRefreshingSessionId] = useState<string>('');
  const [reauthReminderAccounts, setReauthReminderAccounts] = useState<AccountDetail[]>([]);
  const manualRefreshFlightsRef = useRef<Set<string>>(new Set());
  const [passwordForm, setPasswordForm] = useState({
    account: '',
    password: '',
    show_browser: false,
    showPassword: false,
  });
  const [passwordStatus, setPasswordStatus] = useState<AddLoginStatus>('idle');
  const [passwordMessage, setPasswordMessage] = useState('');
  const [passwordVerificationImage, setPasswordVerificationImage] = useState('');
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const [officialWindowAccount, setOfficialWindowAccount] = useState('');
  const [officialWindowStatus, setOfficialWindowStatus] = useState<AddLoginStatus>('idle');
  const [officialWindowMessage, setOfficialWindowMessage] = useState('');
  const [officialWindowSubmitting, setOfficialWindowSubmitting] = useState(false);
  const [manualCookieForm, setManualCookieForm] = useState({ value: '' });
  const [manualCookieStatus, setManualCookieStatus] = useState<AddLoginStatus>('idle');
  const [manualCookieMessage, setManualCookieMessage] = useState('');
  const [manualCookieSubmitting, setManualCookieSubmitting] = useState(false);

  // 编辑表单状态
  const [editForm, setEditForm] = useState({
    remark: '',
    cookie: '',
    auto_confirm: false,
    pause_duration: 0,
    username: '',
    login_password: '',
    show_browser: false,
    cookie_refresh_enabled: false,
    cookie_refresh_interval_minutes: DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES,
    showLoginPassword: false,
  });

  // AI设置表单状态
  const [aiSettings, setAiSettings] = useState<AIReplySettings>({
    ai_enabled: false,
    model_name: 'deepseek-v4-flash',
    api_key: '',
    base_url: 'https://api.deepseek.com',
    api_key_source: 'missing',
    api_key_masked: '',
    has_effective_api_key: false,
    max_discount_percent: 10,
    max_discount_amount: 100,
    max_bargain_rounds: 3,
    custom_prompts: '',
  });
  const [saving, setSaving] = useState(false);
  const [aiProviders, setAiProviders] = useState<AIProviderProfile[]>([]);
  const [testingProvider, setTestingProvider] = useState(false);
  const [refreshingModels, setRefreshingModels] = useState(false);
  const [pageNotice, setPageNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [aiSaveNotice, setAiSaveNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);

  const loadSessionStatuses = async (targetAccounts: AccountDetail[] = accounts) => {
    const results = await Promise.all(targetAccounts.map(async (account) => {
      try {
        return [account.id, await getAccountSessionStatus(account.id)] as const;
      } catch {
        return null;
      }
    }));
    const next = Object.fromEntries(results.filter((entry): entry is readonly [string, AccountSessionRefreshStatus] => Boolean(entry)));
    Object.entries(next).forEach(([accountId, status]) => {
      if (!ACTIVE_SESSION_REFRESH_STATES.has(status.state)) {
        manualRefreshFlightsRef.current.delete(accountId);
      }
    });
    setSessionStatuses((current) => ({ ...current, ...next }));
  };

  const clearQRPolling = () => {
    if (qrPollingRef.current) {
      clearInterval(qrPollingRef.current);
      qrPollingRef.current = null;
    }
  };

  const clearPasswordPolling = () => {
    if (passwordPollingRef.current) {
      clearInterval(passwordPollingRef.current);
      passwordPollingRef.current = null;
    }
  };

  const clearSmsPolling = () => {
    if (smsPollingRef.current) {
      clearInterval(smsPollingRef.current);
      smsPollingRef.current = null;
    }
  };

  const clearExtensionPolling = () => {
    if (extensionPollingRef.current) {
      clearInterval(extensionPollingRef.current);
      extensionPollingRef.current = null;
    }
  };

  const cancelActiveOfficialSession = async () => {
    const sessionId = activeOfficialSessionRef.current;
    activeOfficialSessionRef.current = '';
    if (!sessionId) return;
    try {
      await cancelOfficialLoginSession(sessionId);
    } catch {
      // The session may already be terminal or expired.
    }
  };

  const closeAddModal = () => {
    clearQRPolling();
    clearPasswordPolling();
    clearSmsPolling();
    clearExtensionPolling();
    void cancelActiveOfficialSession();
    setShowAddModal(false);
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationImage('');
    setPasswordForm({
      account: '',
      password: '',
      show_browser: false,
      showPassword: false,
    });
    setManualCookieStatus('idle');
    setManualCookieMessage('');
    setManualCookieForm({ value: '' });
    setOfficialWindowAccount('');
    setOfficialWindowStatus('idle');
    setOfficialWindowMessage('');
    setShowAdvancedLogin(false);
    setExtensionPairing(null);
    setExtensionMessage('');
    setExtensionCopied(false);
  };

  const resetPasswordStatus = () => {
    clearPasswordPolling();
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationImage('');
  };

  const resetManualCookieStatus = () => {
    setManualCookieStatus('idle');
    setManualCookieMessage('');
  };

  const getReachableVerificationImage = (imageUrl?: string | null, screenshotPath?: string | null) => {
    if (imageUrl) return imageUrl;
    if (!screenshotPath) return '';
    if (screenshotPath.startsWith('/static/')) {
      return screenshotPath;
    }
    if (screenshotPath.startsWith('static/')) {
      return `/${screenshotPath}`;
    }
    return '';
  };

  const loadAccounts = async () => {
    setLoading(true);
    try {
      const data = await getAccountDetails();

      // 获取所有账号的AI设置
      let allAISettings: Record<string, AIReplySettings> = {};
      try {
        allAISettings = await getAllAISettings();
      } catch (e) {
        console.error('Failed to load AI settings:', e);
      }

      // 合并AI设置到账号数据
      const accountsWithAI = data.map(account => ({
        ...account,
        ai_enabled: allAISettings[account.id]?.ai_enabled ?? false,
        max_discount_percent: allAISettings[account.id]?.max_discount_percent ?? 10,
        max_discount_amount: allAISettings[account.id]?.max_discount_amount ?? 100,
        max_bargain_rounds: allAISettings[account.id]?.max_bargain_rounds ?? 3,
        custom_prompts: allAISettings[account.id]?.custom_prompts ?? '',
      }));

      setAccounts(accountsWithAI);
    } catch (error) {
      console.error('Failed to load accounts:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAccounts();
    return () => {
      clearQRPolling();
      clearPasswordPolling();
      clearSmsPolling();
      clearExtensionPolling();
      void cancelActiveOfficialSession();
    };
  }, []);

  const accountIds = accounts.map((account) => account.id).join('|');
  useEffect(() => {
    if (!accounts.length) return undefined;
    void loadSessionStatuses(accounts);
    const timer = window.setInterval(() => void loadSessionStatuses(accounts), 3000);
    return () => window.clearInterval(timer);
  }, [accountIds]);

  useEffect(() => {
    const expiredAccounts = accounts.filter((account) => (
      account.reauth_required
      || sessionStatuses[account.id]?.state === 'manual_reauth_required'
    ));
    if (!expiredAccounts.length) return;
    const unseen = expiredAccounts.filter((account) => {
      const key = `xianyu-reauth:${account.id}:${account.last_expired_at ?? sessionStatuses[account.id]?.last_expired_at ?? account.reauth_updated_at ?? 0}`;
      return window.localStorage.getItem(key) !== 'shown';
    });
    if (!unseen.length) return;
    unseen.forEach((account) => {
      const key = `xianyu-reauth:${account.id}:${account.last_expired_at ?? sessionStatuses[account.id]?.last_expired_at ?? account.reauth_updated_at ?? 0}`;
      window.localStorage.setItem(key, 'shown');
    });
    setReauthReminderAccounts(unseen);
  }, [accounts, sessionStatuses]);

  const handleToggle = async (id: string, currentStatus: boolean) => {
    try {
      await updateAccountStatus(id, !currentStatus);
      await loadAccounts();
      setPageNotice({ tone: 'success', text: `账号监听已${currentStatus ? '暂停' : '开启'}` });
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '账号状态更新失败' });
    }
  };

  const handleDelete = async (id: string) => {
    if (confirm('确认删除该账号吗？')) {
      await deleteAccount(id);
      loadAccounts();
    }
  };

  const openEditModal = (account: AccountDetail) => {
    setEditingAccount(account);
    setEditForm({
      remark: account.remark || account.note || '',
      cookie: account.cookie || account.value || '',
      auto_confirm: account.auto_confirm || false,
      pause_duration: account.pause_duration || 0,
      username: account.username || '',
      login_password: account.login_password || '',
      show_browser: account.show_browser || false,
      cookie_refresh_enabled: account.cookie_refresh_enabled || false,
      cookie_refresh_interval_minutes: account.cookie_refresh_interval_minutes || DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES,
      showLoginPassword: false,
    });
    setActiveModal('edit');
  };

  const openAIModal = async (account: AccountDetail) => {
    setEditingAccount(account);
    setAiSaveNotice(null);
    setSaving(true);
    try {
      const [settings, providerResult] = await Promise.all([
        getAccountAISettings(account.id),
        getAIProviders(),
      ]);
      setAiProviders(providerResult.providers);
      setAiSettings({
        ai_enabled: settings.ai_enabled ?? false,
        provider_profile_id: settings.provider_profile_id ?? providerResult.providers.find((item) => item.is_default)?.id ?? providerResult.providers[0]?.id ?? null,
        provider_name: settings.provider_name,
        provider_type: settings.provider_type,
        provider_status: settings.provider_status,
        model_name: settings.model_name || 'deepseek-v4-flash',
        api_key: '',
        base_url: settings.base_url || 'https://api.deepseek.com',
        api_key_source: settings.api_key_source || 'missing',
        api_key_masked: settings.api_key_masked || '',
        has_effective_api_key: settings.has_effective_api_key ?? Boolean(settings.api_key_masked),
        max_discount_percent: settings.max_discount_percent ?? 10,
        max_discount_amount: settings.max_discount_amount ?? 100,
        max_bargain_rounds: settings.max_bargain_rounds ?? 3,
        custom_prompts: settings.custom_prompts ?? '',
        api_key_action: 'keep',
        provider_test_token: '',
      });
    } catch (e) {
      console.error('Failed to load AI settings:', e);
    } finally {
      setSaving(false);
    }
    setActiveModal('ai-settings');
  };

  const handleDiagnose = async (account: AccountDetail) => {
    setDiagnosingId(account.id);
    try {
      const result = await getAutoReplyDiagnostics(account.id);
      setDiagnostics((current) => ({ ...current, [account.id]: result }));
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '诊断失败' });
    } finally {
      setDiagnosingId('');
    }
  };

  const handleRefreshSession = async (account: AccountDetail) => {
    if (!account.auto_refresh_supported || account.reauth_required || sessionStatuses[account.id]?.state === 'manual_reauth_required') {
      openReauthMethod(account);
      return;
    }
    if (manualRefreshFlightsRef.current.has(account.id)) return;

    manualRefreshFlightsRef.current.add(account.id);
    setRefreshingSessionId(account.id);
    try {
      const result = await refreshAccountSession(account.id);
      if (!ACTIVE_SESSION_REFRESH_STATES.has(result.data.state)) {
        manualRefreshFlightsRef.current.delete(account.id);
      }
      setSessionStatuses((current) => ({ ...current, [account.id]: result.data }));
      setPageNotice({ tone: 'info', text: result.message || '已开始刷新 Cookie' });
    } catch (error) {
      manualRefreshFlightsRef.current.delete(account.id);
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : 'Cookie 刷新启动失败' });
    } finally {
      setRefreshingSessionId('');
    }
  };

  const openReauthMethod = (account: AccountDetail) => {
    setReauthReminderAccounts([]);
    setActiveModal(null);
    setShowAddModal(true);
    if (account.reauth_action === 'sms_login') {
      setActiveAddMethod('sms');
      setOfficialWindowAccount(account.username || '');
    } else if (account.reauth_action === 'password_login') {
      setActiveAddMethod('password');
      setPasswordForm((current) => ({ ...current, account: account.username || '' }));
    } else if (account.reauth_action === 'chrome_extension_import') {
      setActiveAddMethod('extension');
      setShowAdvancedLogin(true);
    } else if (account.reauth_action === 'manual_cookie') {
      setActiveAddMethod('cookie');
      setShowAdvancedLogin(true);
    } else {
      setActiveAddMethod('qr');
      void startQRLogin();
    }
  };

  const handleCancelSessionRefresh = async (account: AccountDetail) => {
    try {
      const result = await cancelAccountSessionRefresh(account.id);
      await loadSessionStatuses([account]);
      setPageNotice({ tone: 'info', text: result.message || 'Cookie 刷新已取消' });
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '取消刷新失败' });
    }
  };

  const handleShowAccountSessionBrowser = async (account: AccountDetail) => {
    try {
      const result = await showAccountSessionRefreshBrowser(account.id);
      setPageNotice({ tone: 'info', text: result.message || '已在本机显示闲鱼官方窗口' });
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '打开官方窗口失败' });
    }
  };

  const handleSaveEdit = async () => {
    if (!editingAccount) return;
    setSaving(true);

    try {
      const promises: Promise<any>[] = [];

      // 更新备注
      if (editForm.remark !== (editingAccount.remark || editingAccount.note || '')) {
        promises.push(updateAccountRemark(editingAccount.id, editForm.remark));
      }

      // 更新Cookie
      if (editForm.cookie && editForm.cookie !== (editingAccount.cookie || editingAccount.value || '')) {
        promises.push(updateAccountCookie(editingAccount.id, editForm.cookie));
      }

      // 更新自动确认
      if (editForm.auto_confirm !== editingAccount.auto_confirm) {
        promises.push(updateAccountAutoConfirm(editingAccount.id, editForm.auto_confirm));
      }

      // 更新暂停时长
      if (editForm.pause_duration !== (editingAccount.pause_duration || 0)) {
        promises.push(updateAccountPauseDuration(editingAccount.id, editForm.pause_duration));
      }

      // 更新登录信息
      if (
        editForm.username !== (editingAccount.username || '') ||
        editForm.login_password !== (editingAccount.login_password || '') ||
        editForm.show_browser !== (editingAccount.show_browser || false)
      ) {
        promises.push(updateAccountLoginInfo(editingAccount.id, {
          username: editForm.username,
          login_password: editForm.login_password,
          show_browser: editForm.show_browser,
        }));
      }

      if (
        editForm.cookie_refresh_enabled !== (editingAccount.cookie_refresh_enabled || false) ||
        editForm.cookie_refresh_interval_minutes !== (
          editingAccount.cookie_refresh_interval_minutes || DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES
        )
      ) {
        promises.push(updateAccountCookieRefreshSettings(editingAccount.id, {
          cookie_refresh_enabled: editForm.cookie_refresh_enabled,
          cookie_refresh_interval_minutes: editForm.cookie_refresh_interval_minutes,
        }));
      }

      await Promise.all(promises);
      setActiveModal(null);
      await loadAccounts();
      const refreshedDiagnosis = await getAutoReplyDiagnostics(editingAccount.id);
      setDiagnostics((current) => ({ ...current, [editingAccount.id]: refreshedDiagnosis }));
      await loadSessionStatuses([editingAccount]);
      setPageNotice({ tone: 'success', text: '账号设置已保存，诊断状态已更新' });
    } catch (error) {
      console.error('更新账号失败:', error);
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '更新失败，请重试' });
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAISettings = async () => {
    if (!editingAccount) return;
    if (!aiSettings.provider_profile_id) {
      setAiSaveNotice({ tone: 'error', text: '请先在“系统与 AI”中添加平台配置' });
      return;
    }
    setSaving(true);
    setTestingProvider(true);
    setAiSaveNotice({ tone: 'info', text: '正在用所选模型生成测试回复，成功后才会应用' });

    try {
      const testResult = await testAIProvider(aiSettings.provider_profile_id, aiSettings.model_name);
      setTestingProvider(false);
      setAiSaveNotice({ tone: 'info', text: `测试回复：${testResult.reply}。正在保存并复读确认。` });
      await updateAccountAISettings(editingAccount.id, {
        ...aiSettings,
        api_key_action: 'keep',
        provider_test_token: testResult.test_token,
      });
      const saved = await getAccountAISettings(editingAccount.id);
      const confirmed = saved.ai_enabled === aiSettings.ai_enabled
        && saved.provider_profile_id === aiSettings.provider_profile_id
        && saved.model_name === aiSettings.model_name
        && Boolean(saved.has_effective_api_key);
      if (!confirmed) {
        throw new Error('服务器返回的配置与刚保存的值不一致，请重试');
      }
      setActiveModal(null);
      await loadAccounts();
      setPageNotice({ tone: 'success', text: `AI 自动回复已${saved.ai_enabled ? '开启并保存' : '关闭并保存'}` });
    } catch (error) {
      console.error('更新AI设置失败:', error);
      setAiSaveNotice({ tone: 'error', text: error instanceof Error ? error.message : '更新失败，请重试' });
    } finally {
      setTestingProvider(false);
      setSaving(false);
    }
  };

  const handleRefreshProviderModels = async () => {
    if (!aiSettings.provider_profile_id) return;
    setRefreshingModels(true);
    setAiSaveNotice(null);
    try {
      const result = await refreshAIProviderModels(aiSettings.provider_profile_id);
      const providers = await getAIProviders();
      setAiProviders(providers.providers);
      setAiSaveNotice({ tone: 'success', text: `已读取 ${result.models.length} 个模型，也可以继续手填模型 ID。` });
    } catch (error) {
      setAiSaveNotice({ tone: 'error', text: error instanceof Error ? error.message : '模型列表刷新失败，可直接手填模型 ID' });
    } finally {
      setRefreshingModels(false);
    }
  };

  const handleQRStatusResult = (
    statusRes: Awaited<ReturnType<typeof checkQRLoginStatus>>,
    verificationMessage?: string,
  ) => {
    if (statusRes.status === 'success' || statusRes.status === 'already_processed') {
      clearQRPolling();
      setQrStatus('success');
      setQrMessage('登录成功，正在刷新账号列表');
      setTimeout(() => {
        closeAddModal();
        loadAccounts();
      }, 1000);
    } else if (statusRes.status === 'scanned' || statusRes.status === 'processing') {
      setQrStatus('scanned');
      setQrMessage(statusRes.message || '正在检查登录状态');
    } else if (statusRes.status === 'verification_required') {
      qrHadVerificationRef.current = true;
      setQrStatus('verification_required');
      const verificationImage = getReachableVerificationImage(
        statusRes.verification_qr_code_url,
        statusRes.verification_screenshot_path,
      );
      if (verificationImage) {
        setQrVerificationImage(verificationImage);
      }
      if (statusRes.verification_browser_status === 'failed') {
        clearQRPolling();
      }
      setQrMessage(
        verificationMessage ||
        statusRes.message ||
        (verificationImage
          ? '请在本机官方窗口完成身份验证，系统会自动检测。'
          : '闲鱼要求安全验证，请点击“本机打开官方窗口”。')
      );
    } else if (statusRes.status === 'not_found') {
      clearQRPolling();
      setQrStatus('error');
      setQrMessage('二维码会话已失效，请重新生成二维码');
    } else if (statusRes.status === 'cancelled') {
      clearQRPolling();
      setQrStatus('error');
      setQrMessage('你已取消登录，请重新扫码');
    } else if (statusRes.status === 'expired' || statusRes.status === 'error') {
      clearQRPolling();
      setQrStatus('error');
      setQrMessage(
        statusRes.message || (
          qrHadVerificationRef.current
            ? '安全验证会话已过期，请重新生成二维码'
            : '二维码已过期，请重新扫码'
        ),
      );
    }
  };

  const startQRStatusPolling = (sessionId: string, verificationMessage?: string) => {
    clearQRPolling();
    qrPollingRef.current = setInterval(async () => {
      try {
        const statusRes = await checkQRLoginStatus(sessionId);
        handleQRStatusResult(statusRes, verificationMessage);
      } catch (error) {
        clearQRPolling();
        setQrStatus('error');
        setQrMessage(error instanceof Error ? error.message : '检查二维码状态失败，请重试');
      }
    }, 2000);
  };

  const startQRLogin = async () => {
    clearQRPolling();
    clearPasswordPolling();
    clearExtensionPolling();
    await cancelActiveOfficialSession();
    setShowAddModal(true);
    setActiveAddMethod('qr');
    setQrStatus('loading');
    setQrCodeUrl('');
    setQrSessionId('');
    setQrMessage('');
    setQrVerificationImage('');
    qrHadVerificationRef.current = false;
    try {
      const res = await generateQRLogin();
      if (res.success && res.qr_code_url && res.session_id) {
        setQrCodeUrl(res.qr_code_url);
        setQrSessionId(res.session_id);
        setQrStatus('waiting');
        setQrMessage('请打开闲鱼 APP 扫码并在手机上确认登录');
        startQRStatusPolling(res.session_id);
      } else {
        setQrStatus('error');
        setQrMessage(res.message || '二维码生成失败，请重试');
      }
    } catch (e) {
      setQrStatus('error');
      setQrMessage(e instanceof Error ? e.message : '扫码登录请求失败，请重试');
    }
  };

  const handleContinueQRVerification = async () => {
    if (!qrSessionId) {
      setQrStatus('error');
      setQrMessage('二维码会话不存在，请重新生成二维码');
      return;
    }
    clearQRPolling();
    setQrStatus('verification_required');
    setQrMessage('正在启动本机闲鱼官方窗口，请在窗口内完成验证');
    try {
      const result = await continueQRLoginAfterVerification(qrSessionId);
      handleQRStatusResult(result);
      if (result.status === 'processing' || result.status === 'scanned' || result.status === 'verification_required') {
        startQRStatusPolling(qrSessionId);
      }
    } catch (error) {
      setQrStatus('verification_required');
      setQrMessage(error instanceof Error ? error.message : '打开安全验证窗口失败，请重试');
    }
  };

  const startExtensionPairingPolling = (pairingId: string) => {
    clearExtensionPolling();
    extensionPollingRef.current = setInterval(async () => {
      try {
        const pairing = await getBrowserExtensionPairing(pairingId);
        setExtensionPairing((current) => current ? { ...current, ...pairing } : pairing);
        setExtensionMessage(pairing.message || '等待扩展导入');
        if (pairing.status === 'success') {
          clearExtensionPolling();
          setTimeout(() => {
            closeAddModal();
            loadAccounts();
          }, 800);
        } else if (pairing.status === 'failed' || pairing.status === 'expired') {
          clearExtensionPolling();
        }
      } catch (error) {
        clearExtensionPolling();
        setExtensionMessage(error instanceof Error ? error.message : '配对状态检查失败');
      }
    }, 1500);
  };

  const handleCreateExtensionPairing = async () => {
    clearExtensionPolling();
    setExtensionBusy(true);
    setExtensionPairing(null);
    setExtensionCopied(false);
    setExtensionMessage('正在创建本机一次性配对');
    try {
      const pairing = await createBrowserExtensionPairing();
      setExtensionPairing(pairing);
      setExtensionMessage('配对已创建，请复制到 Chrome 扩展；五分钟内有效且只能使用一次。');
      startExtensionPairingPolling(pairing.pairing_id);
    } catch (error) {
      setExtensionMessage(error instanceof Error ? error.message : '创建配对失败');
    } finally {
      setExtensionBusy(false);
    }
  };

  const handleCopyExtensionPairing = async () => {
    if (!extensionPairing?.pairing_code) return;
    const pairingBundle = JSON.stringify({
      pairing_id: extensionPairing.pairing_id,
      pairing_code: extensionPairing.pairing_code,
    });
    try {
      await navigator.clipboard.writeText(pairingBundle);
      setExtensionCopied(true);
      setExtensionMessage('配对信息已复制，请打开扩展并粘贴。');
    } catch {
      setExtensionCopied(false);
      setExtensionMessage('浏览器未允许自动复制，请手动选择下方配对信息。');
    }
  };

  const handleAddMethodChange = async (method: AddLoginMethod) => {
    if (method === activeAddMethod) return;
    clearQRPolling();
    clearPasswordPolling();
    clearSmsPolling();
    clearExtensionPolling();
    await cancelActiveOfficialSession();
    setActiveAddMethod(method);
    if (method === 'qr' && !qrSessionId && qrStatus !== 'loading') {
      await startQRLogin();
    }
  };

  const startOfficialWindowStatusPolling = (sessionId: string) => {
    clearSmsPolling();
    smsPollingRef.current = setInterval(async () => {
      try {
        const status = await getOfficialLoginSession(sessionId);
        if (['preparing', 'waiting_user', 'persisting', 'restarting_listener'].includes(status.state)) {
          setOfficialWindowStatus('processing');
          setOfficialWindowMessage(status.message || '请在官方窗口完成验证码登录');
        } else if (status.state === 'verification_required') {
          setOfficialWindowStatus('verification_required');
          setOfficialWindowMessage(status.message || '请在官方窗口完成身份验证');
        } else if (status.state === 'success') {
          clearSmsPolling();
          activeOfficialSessionRef.current = '';
          setOfficialWindowStatus('success');
          setOfficialWindowMessage(status.message || '手机号验证码登录成功');
          setTimeout(() => {
            closeAddModal();
            loadAccounts();
          }, 1000);
        } else if (['failed', 'expired', 'cancelled', 'interrupted'].includes(status.state)) {
          clearSmsPolling();
          activeOfficialSessionRef.current = '';
          setOfficialWindowStatus('failed');
          setOfficialWindowMessage(status.message || '手机号验证码登录未完成，请重新发起');
        }
      } catch (error) {
        clearSmsPolling();
        activeOfficialSessionRef.current = '';
        setOfficialWindowStatus('failed');
        setOfficialWindowMessage(error instanceof Error ? error.message : '验证码登录状态检查失败');
      }
    }, 2500);
  };

  const handleOfficialWindowLogin = async () => {
    setOfficialWindowSubmitting(true);
    setOfficialWindowStatus('processing');
    setOfficialWindowMessage('正在打开本机 Chrome');
    try {
      await cancelActiveOfficialSession();
      const result = await createOfficialLoginSession({
        mode: 'sms',
        account: officialWindowAccount.trim(),
        show_browser: true,
      });
      if (!result.success || !result.session_id) {
        setOfficialWindowStatus('failed');
        setOfficialWindowMessage(result.message || '手机号验证码登录任务启动失败');
        return;
      }
      activeOfficialSessionRef.current = result.session_id;
      setOfficialWindowMessage(result.message || '请在官方窗口完成验证码登录');
      startOfficialWindowStatusPolling(result.session_id);
    } catch (error) {
      setOfficialWindowStatus('failed');
      setOfficialWindowMessage(error instanceof Error ? error.message : '手机号验证码登录请求失败');
    } finally {
      setOfficialWindowSubmitting(false);
    }
  };

  const startPasswordStatusPolling = (sessionId: string) => {
    clearPasswordPolling();
    passwordPollingRef.current = setInterval(async () => {
      try {
        const statusRes = await getOfficialLoginSession(sessionId);
        if (
          statusRes.state === 'preparing'
          || statusRes.state === 'waiting_user'
          || statusRes.state === 'persisting'
          || statusRes.state === 'restarting_listener'
        ) {
          setPasswordStatus('processing');
          setPasswordMessage(statusRes.message || '登录处理中，请稍候');
        } else if (statusRes.state === 'verification_required') {
          setPasswordStatus('verification_required');
          setPasswordMessage(statusRes.message || '需要完成闲鱼安全验证');
          setPasswordVerificationImage(statusRes.verification_image_url || '');
        } else if (statusRes.state === 'success') {
          clearPasswordPolling();
          activeOfficialSessionRef.current = '';
          setPasswordStatus('success');
          setPasswordMessage(statusRes.message || '账号密码登录成功，正在刷新账号列表');
          setPasswordForm((current) => ({ ...current, password: '', showPassword: false }));
          setTimeout(() => {
            closeAddModal();
            loadAccounts();
          }, 1000);
        } else if (
          statusRes.state === 'failed' ||
          statusRes.state === 'expired' ||
          statusRes.state === 'cancelled' ||
          statusRes.state === 'interrupted'
        ) {
          clearPasswordPolling();
          activeOfficialSessionRef.current = '';
          setPasswordStatus('failed');
          setPasswordMessage(statusRes.message || '账号密码登录失败');
        }
      } catch (error) {
        clearPasswordPolling();
        setPasswordStatus('failed');
        setPasswordMessage(error instanceof Error ? error.message : '检查账号密码登录状态失败');
      }
    }, 2500);
  };

  const handlePasswordLoginSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    resetPasswordStatus();
    const account = passwordForm.account.trim();
    if (!account || !passwordForm.password) {
      setPasswordStatus('failed');
      setPasswordMessage('请填写闲鱼账号和密码');
      return;
    }

    setPasswordSubmitting(true);
    setPasswordStatus('processing');
    setPasswordMessage('正在启动账号密码登录');
    try {
      await cancelActiveOfficialSession();
      const result = await createOfficialLoginSession({
        mode: 'password',
        account,
        password: passwordForm.password,
        show_browser: passwordForm.show_browser,
      });
      if (!result.success || !result.session_id) {
        setPasswordStatus('failed');
        setPasswordMessage(result.message || '账号密码登录任务启动失败');
        return;
      }
      activeOfficialSessionRef.current = result.session_id;
      setPasswordMessage(result.message || '登录任务已启动，请等待');
      startPasswordStatusPolling(result.session_id);
    } catch (error) {
      setPasswordStatus('failed');
      setPasswordMessage(error instanceof Error ? error.message : '账号密码登录请求失败');
    } finally {
      setPasswordSubmitting(false);
    }
  };

  const handleShowOfficialBrowser = async () => {
    const sessionId = activeOfficialSessionRef.current;
    if (!sessionId) return;
    try {
      const result = await showOfficialLoginBrowser(sessionId);
      const message = result.message || '已在本机显示闲鱼官方窗口';
      if (activeAddMethod === 'qr') setQrMessage(message);
      else if (activeAddMethod === 'sms') setOfficialWindowMessage(message);
      else setPasswordMessage(message);
    } catch (error) {
      const message = error instanceof Error ? error.message : '打开官方窗口失败';
      if (activeAddMethod === 'qr') setQrMessage(message);
      else if (activeAddMethod === 'sms') setOfficialWindowMessage(message);
      else setPasswordMessage(message);
    }
  };

  const handleCancelOfficialLogin = async () => {
    clearQRPolling();
    clearPasswordPolling();
    clearSmsPolling();
    await cancelActiveOfficialSession();
    if (activeAddMethod === 'qr') {
      setQrStatus('error');
      setQrMessage('登录会话已取消');
    } else if (activeAddMethod === 'sms') {
      setOfficialWindowStatus('failed');
      setOfficialWindowMessage('手机号验证码登录已取消');
    } else {
      setPasswordStatus('failed');
      setPasswordMessage('登录会话已取消');
    }
  };

  const handleManualCookieSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    resetManualCookieStatus();
    const value = manualCookieForm.value.trim();
    if (!value) {
      setManualCookieStatus('failed');
      setManualCookieMessage('请填写 Cookie');
      return;
    }

    setManualCookieSubmitting(true);
    setManualCookieStatus('processing');
    setManualCookieMessage('正在保存 Cookie');
    try {
      await addAccountCookie({ value });
      setManualCookieStatus('success');
      setManualCookieMessage('Cookie 已保存，正在刷新账号列表');
      setManualCookieForm({ value: '' });
      setTimeout(() => {
        closeAddModal();
        loadAccounts();
      }, 800);
    } catch (error) {
      setManualCookieStatus('failed');
      setManualCookieMessage(error instanceof Error ? error.message : 'Cookie 保存失败');
    } finally {
      setManualCookieSubmitting(false);
    }
  };

  if (loading) return <div className="p-20 flex justify-center"><Loader2 className="w-8 h-8 text-[#FFE815] animate-spin"/></div>;

  return (
    <div className="space-y-8 animate-fade-in relative">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">账号管理</h2>
          <p className="text-gray-500 mt-2 font-medium">管理您的闲鱼授权账号及设置。</p>
        </div>
        <button
            onClick={startQRLogin}
            className="ios-btn-primary flex items-center gap-2 px-6 py-3 rounded-2xl font-bold shadow-lg shadow-yellow-200 transition-transform hover:scale-105 active:scale-95"
        >
          <Plus className="w-5 h-5" />
          添加账号
        </button>
      </div>

      {pageNotice && <InlineNotice tone={pageNotice.tone}>{pageNotice.text}</InlineNotice>}

      {/* Account Grid */}
      <div className="grid grid-cols-1 gap-6">
        {accounts.map((account) => {
          const diagnosis = diagnostics[account.id];
          const sessionStatus = sessionStatuses[account.id];
          return (
          <div key={account.id} className="ios-card p-4 sm:p-6 rounded-2xl group hover:border-[#FFE815] transition-all duration-300">
          <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-5">
            <div className="flex min-w-0 items-start sm:items-center gap-4 sm:gap-6">
              <div className="relative">
                <AccountAvatar
                  src={account.avatar_url}
                  label={account.nickname || account.remark || `账号 ${account.id}`}
                  className="w-16 h-16 sm:w-20 sm:h-20 rounded-2xl object-cover shadow-md ring-4 ring-white"
                />
                <div className={`absolute -bottom-1 -right-1 w-6 h-6 rounded-full border-4 border-white flex items-center justify-center ${account.enabled ? 'bg-green-500' : 'bg-gray-300'}`}>
                    {account.enabled && <Check className="w-3 h-3 text-white" />}
                </div>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 mb-1">
                    <h3 className="text-lg sm:text-xl font-extrabold text-gray-900 break-words">{account.nickname || account.remark || `账号 ${account.id.substring(0,6)}...`}</h3>
                    {account.enabled ? (
                        <StatusBadge state="ready" label="监听开启" />
                    ) : (
                        <StatusBadge state="idle" label="监听暂停" />
                    )}
                    {account.ai_enabled && (
                        <StatusBadge state="ready" label="AI 已开启" />
                    )}
                </div>
                <p className="text-sm text-gray-500 font-medium mb-3">{account.remark || account.note || '暂无备注'}</p>
                <div className="flex flex-wrap gap-2">
                   <StatusBadge state={account.auto_confirm ? 'ready' : 'idle'} label={account.auto_confirm ? '自动确认开启' : '自动确认关闭'} />
                   <StatusBadge state="idle" label={`登录：${account.login_method_label || '历史登录'}`} />
                   {account.pause_duration > 0 && <span className="text-xs bg-blue-50 text-blue-700 px-3 py-1.5 rounded-lg font-bold flex items-center gap-1.5"><Clock className="w-3 h-3"/> 暂停{account.pause_duration}分钟</span>}
                   <StatusBadge
                    state={account.auto_refresh_supported ? (account.cookie_refresh_enabled ? 'ready' : 'idle') : 'warning'}
                    label={account.auto_refresh_supported
                      ? account.cookie_refresh_enabled
                        ? `每 ${formatCookieRefreshInterval(account.cookie_refresh_interval_minutes)}自动续期`
                        : '可自动续期 · 定时关闭'
                      : '到期需人工登录'}
                   />
                   {diagnosis && (
                    <span className={`text-xs px-3 py-1.5 rounded-lg font-bold ${diagnosis.ready ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                      {diagnosis.ready ? '自动回复就绪' : `${diagnosis.issues.length} 个问题`}
                    </span>
                   )}
                   {sessionStatus?.state === 'refreshing' && <StatusBadge state="checking" label="Cookie 刷新中" />}
                   {sessionStatus?.state === 'action_required' && <StatusBadge state="warning" label="需要手动验证" />}
                   {sessionStatus?.state === 'verification_required' && <StatusBadge state="warning" label="等待身份验证" />}
                   {sessionStatus?.state === 'success' && <StatusBadge state="ready" label="Cookie 已刷新" />}
                   {(sessionStatus?.state === 'failed' || sessionStatus?.state === 'timeout') && <StatusBadge state="error" label="Cookie 刷新失败" />}
                   {(account.reauth_required || sessionStatus?.state === 'manual_reauth_required') && <StatusBadge state="error" label="登录已过期" />}
                   {account.has_login_password && account.login_credentials_valid === false && <StatusBadge state="error" label="登录信息异常" />}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1 sm:gap-2 xl:justify-end">
                <button
                    onClick={() => handleDiagnose(account)}
                    className="p-3 rounded-xl hover:bg-blue-100 transition-colors text-blue-600"
                    title="自动回复诊断"
                >
                    {diagnosingId === account.id ? <Loader2 className="w-5 h-5 animate-spin" /> : <MessageCircle className="w-5 h-5" />}
                </button>
                <button
                    onClick={() => void handleRefreshSession(account)}
                    disabled={refreshingSessionId === account.id || sessionStatus?.state === 'refreshing' || sessionStatus?.state === 'verification_required'}
                    className={`p-3 rounded-xl transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${account.auto_refresh_supported ? 'text-cyan-700 hover:bg-cyan-100' : 'text-amber-700 hover:bg-amber-100'}`}
                    title={account.auto_refresh_supported ? '立即刷新 Cookie' : reauthActionLabel(account)}
                >
                    {account.auto_refresh_supported
                      ? <RefreshCw className={`w-5 h-5 ${refreshingSessionId === account.id || sessionStatus?.state === 'refreshing' ? 'animate-spin' : ''}`} />
                      : <Key className="h-5 w-5" />}
                </button>
                <button
                    onClick={() => openEditModal(account)}
                    className="p-3 rounded-xl hover:bg-gray-100 transition-colors text-gray-600"
                    title="编辑账号"
                >
                    <Edit2 className="w-5 h-5" />
                </button>
                <button
                    onClick={() => openAIModal(account)}
                    className="p-3 rounded-xl hover:bg-purple-100 transition-colors text-purple-600"
                    title="AI设置"
                >
                    <Bot className="w-5 h-5" />
                </button>
                <button
                    onClick={() => setTrainingAccount(account)}
                    className="p-3 rounded-xl hover:bg-yellow-100 transition-colors text-yellow-700"
                    title="训练AI"
                >
                    <MessageSquare className="w-5 h-5" />
                </button>
                <button
                    onClick={() => handleToggle(account.id, account.enabled)}
                    className={`p-3 rounded-xl transition-colors ${account.enabled ? 'text-green-600 hover:bg-green-50' : 'text-gray-400 hover:bg-gray-100'}`}
                >
                    <Power className="w-5 h-5" />
                </button>
                <button
                    onClick={() => handleDelete(account.id)}
                    className="p-3 rounded-xl hover:bg-red-100 transition-colors text-red-500"
                >
                    <Trash2 className="w-5 h-5" />
                </button>
            </div>
          </div>
          {diagnosis && (
            <div className="mt-5 border border-gray-100 rounded-2xl bg-gray-50 p-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                <div className="font-bold text-gray-700">监听：{diagnosis.runtime.task_running ? '运行中' : '未运行'}</div>
                <div className="font-bold text-gray-700">AI：{diagnosis.reply.ai_enabled ? diagnosis.reply.ai_model : '未启用'}</div>
                <div className="font-bold text-gray-700">关键词：{diagnosis.reply.keyword_count}</div>
                <div className="font-bold text-gray-700">对话：{diagnosis.reply.conversation_count}</div>
              </div>
              {diagnosis.issues.length > 0 && (
                <div className="mt-3 space-y-1">
                  {diagnosis.issues.map((issue) => (
                    <div key={issue} className="text-xs text-red-600 font-bold">- {issue}</div>
                  ))}
                </div>
              )}
              {diagnosis.diagnosed_at && <div className="mt-3 text-[11px] font-medium text-gray-400">诊断更新于 {new Date(diagnosis.diagnosed_at * 1000).toLocaleTimeString()}</div>}
            </div>
          )}
          {sessionStatus && ['action_required', 'refreshing', 'verification_required', 'failed', 'timeout', 'manual_reauth_required'].includes(sessionStatus.state) && (
            <div className={`mt-5 rounded-2xl border p-4 ${sessionStatus.state === 'action_required' || sessionStatus.state === 'verification_required' || sessionStatus.state === 'manual_reauth_required' ? 'border-amber-200 bg-amber-50' : sessionStatus.state === 'refreshing' ? 'border-blue-200 bg-blue-50' : 'border-red-200 bg-red-50'}`}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="font-bold text-gray-900">
                    {sessionStatus.state === 'action_required'
                      ? '需要开始一次验证'
                      : sessionStatus.state === 'verification_required'
                        ? '需要完成闲鱼身份验证'
                        : sessionStatus.state === 'manual_reauth_required'
                          ? '登录状态已过期'
                          : sessionStatus.state === 'refreshing'
                            ? '正在刷新 Cookie'
                            : 'Cookie 刷新未完成'}
                  </div>
                  <div className="mt-1 text-sm text-gray-700">{sessionStatus.message}</div>
                  {sessionStatus.updated_at && <div className="mt-1 text-xs text-gray-500">更新于 {new Date(sessionStatus.updated_at * 1000).toLocaleTimeString()}</div>}
                </div>
                <div className="flex shrink-0 gap-2">
                  {sessionStatus.state === 'verification_required' && sessionStatus.browser_active && (
                    <button type="button" onClick={() => void handleCancelSessionRefresh(account)} className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-bold text-gray-700">取消</button>
                  )}
                  {sessionStatus.state === 'verification_required' && sessionStatus.browser_active && (
                    <button
                      type="button"
                      onClick={() => void handleShowAccountSessionBrowser(account)}
                      className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-bold text-gray-700"
                    >
                      <ExternalLink className="h-4 w-4" />
                      本机打开
                    </button>
                  )}
                  {sessionStatus.state === 'action_required' && (
                    <button type="button" onClick={() => void handleRefreshSession(account)} className="rounded-lg bg-[#FFE815] px-3 py-2 text-xs font-bold text-gray-900">开始一次验证</button>
                  )}
                  {(sessionStatus.state === 'failed' || sessionStatus.state === 'timeout') && (
                    <button type="button" onClick={() => void handleRefreshSession(account)} className="rounded-lg bg-black px-3 py-2 text-xs font-bold text-white">重新刷新</button>
                  )}
                  {sessionStatus.state === 'manual_reauth_required' && (
                    <button type="button" onClick={() => openReauthMethod(account)} className="rounded-lg bg-black px-3 py-2 text-xs font-bold text-white">{reauthActionLabel(account)}</button>
                  )}
                </div>
              </div>
              {sessionStatus.state === 'verification_required' && sessionStatus.browser_active && (
                <div className="mt-3 text-xs font-bold text-amber-800">后台正在自动检测，完成验证后会自动保存并恢复监听。</div>
              )}
              {sessionStatus.state === 'verification_required' && sessionStatus.verification_image_url && (
                <div className="mt-4 overflow-hidden rounded-xl border border-amber-200 bg-white p-2">
                  <img src={`${sessionStatus.verification_image_url}?t=${sessionStatus.updated_at || Date.now()}`} alt="闲鱼身份验证" className="mx-auto max-h-[520px] w-auto max-w-full object-contain" />
                </div>
              )}
            </div>
          )}
          </div>
        );})}

        {accounts.length === 0 && (
            <div className="ios-card p-12 text-center">
                <div className="w-20 h-20 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <User className="w-10 h-10 text-gray-400" />
                </div>
                <h3 className="text-lg font-bold text-gray-900">暂无账号</h3>
                <p className="text-gray-500 mt-1">请点击右上角添加闲鱼账号</p>
            </div>
        )}
      </div>

      {reauthReminderAccounts.length > 0 && createPortal(
        <div className="modal-overlay-centered" role="dialog" aria-modal="true" aria-labelledby="reauth-reminder-title">
          <div className="modal-container" style={{ maxWidth: '520px' }}>
            <div className="modal-header">
              <div className="min-w-0">
                <h3 id="reauth-reminder-title" className="text-xl font-extrabold text-gray-900 sm:text-2xl">账号登录已过期</h3>
                <p className="mt-1 text-sm text-gray-500">完成对应登录后，账号监听会更新到新的登录状态。</p>
              </div>
              <button
                type="button"
                onClick={() => setReauthReminderAccounts([])}
                className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg hover:bg-gray-100"
                aria-label="关闭过期提醒"
              >
                <X className="h-5 w-5 text-gray-500" />
              </button>
            </div>
            <div className="modal-body space-y-3">
              {reauthReminderAccounts.map((account) => (
                <div key={account.id} className="flex flex-col gap-3 border-b border-gray-100 py-3 last:border-0 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                      <p className="break-words font-bold text-gray-900">{account.nickname || account.remark || account.id}</p>
                    </div>
                    <p className="mt-1 text-sm text-gray-500">{account.login_method_label || '历史登录'} · 到期后需人工重新登录</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => openReauthMethod(account)}
                    className="min-h-11 shrink-0 rounded-lg bg-gray-900 px-4 text-sm font-bold text-white"
                  >
                    {reauthActionLabel(account)}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* 添加账号弹窗 */}
      {showAddModal && createPortal(
          <div className="modal-overlay-centered" role="dialog" aria-modal="true" aria-labelledby="add-account-title">
              <div className="modal-container" style={{maxWidth: '720px'}}>
                  <div className="modal-header">
                    <div>
                      <h3 id="add-account-title" className="text-2xl font-extrabold text-gray-900">添加账号</h3>
                      <p className="text-sm text-gray-500 mt-1">扫码最简单；账号密码支持自动续期。</p>
                    </div>
                    <button
                      type="button"
                      onClick={closeAddModal}
                      className="flex min-h-11 min-w-11 flex-shrink-0 items-center justify-center rounded-lg hover:bg-gray-100 transition-colors"
                      aria-label="关闭添加账号"
                    >
                      <X className="w-5 h-5 text-gray-500" />
                    </button>
                  </div>

                  <div className="modal-body space-y-6">
                    <div className="grid grid-cols-1 gap-2 rounded-2xl bg-gray-100 p-1 sm:grid-cols-3">
                      <button
                        type="button"
                        onClick={() => void handleAddMethodChange('qr')}
                        className={`flex min-h-11 items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'qr' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <QrCode className="w-4 h-4" />
                        扫码
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleAddMethodChange('sms')}
                        className={`flex min-h-11 items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'sms' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <Smartphone className="w-4 h-4" />
                        手机号验证码
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleAddMethodChange('password')}
                        className={`flex min-h-11 items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'password' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <Key className="w-4 h-4" />
                        账号密码
                      </button>
                    </div>

                    <div className="border-t border-gray-200 pt-4">
                      <button
                        type="button"
                        onClick={() => setShowAdvancedLogin((current) => !current)}
                        className="flex min-h-11 w-full items-center justify-between rounded-lg px-2 text-sm font-bold text-gray-600 hover:bg-gray-50"
                        aria-expanded={showAdvancedLogin}
                      >
                        <span>高级方式</span>
                        <ChevronDown className={`h-4 w-4 transition-transform ${showAdvancedLogin ? 'rotate-180' : ''}`} />
                      </button>
                      {showAdvancedLogin && (
                        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                          <button
                            type="button"
                            onClick={() => void handleAddMethodChange('extension')}
                            className={`flex min-h-11 items-center justify-center gap-2 rounded-lg border px-3 text-sm font-bold ${activeAddMethod === 'extension' ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 text-gray-600'}`}
                          >
                            <Chrome className="h-4 w-4" /> 本机 Chrome
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleAddMethodChange('cookie')}
                            className={`flex min-h-11 items-center justify-center gap-2 rounded-lg border px-3 text-sm font-bold ${activeAddMethod === 'cookie' ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 text-gray-600'}`}
                          >
                            <Upload className="h-4 w-4" /> 手填 Cookie
                          </button>
                        </div>
                      )}
                    </div>

                    {activeAddMethod === 'qr' && (
                      <div className="text-center">
                        <div className="relative mx-auto mb-4 flex h-[260px] w-full max-w-[420px] items-center justify-center overflow-hidden rounded-xl border border-gray-200 bg-[#F7F8FA] shadow-inner sm:mb-6 sm:h-[360px]">
                          {qrStatus === 'loading' && <Loader2 className="w-10 h-10 text-[#FFE815] animate-spin" />}
                          {qrStatus === 'waiting' && qrCodeUrl && <img src={qrCodeUrl} alt="闲鱼登录二维码" className="h-full w-full object-contain p-3" />}
                          {qrStatus === 'scanned' && (
                            <div className="absolute inset-0 bg-white/95 flex flex-col items-center justify-center text-blue-600 animate-fade-in">
                              <Loader2 className="w-10 h-10 mb-4 animate-spin" />
                              <span className="font-bold text-lg">等待手机确认</span>
                            </div>
                          )}
                          {qrStatus === 'success' && (
                            <div className="absolute inset-0 bg-white/95 flex flex-col items-center justify-center text-green-600 animate-fade-in">
                              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mb-4">
                                <Check className="w-8 h-8" />
                              </div>
                              <span className="font-bold text-lg">登录成功</span>
                            </div>
                          )}
                          {qrStatus === 'verification_required' && (
                            qrVerificationImage ? (
                              <div className="absolute inset-0 bg-white flex flex-col items-center justify-center animate-fade-in p-3">
                                <img src={qrVerificationImage} alt="闲鱼安全验证页面" className="w-full h-full object-contain p-2" />
                                <span className="absolute bottom-3 rounded-full bg-white/95 px-3 py-1 text-xs font-bold text-orange-600 shadow-sm">请按官方页面提示完成验证</span>
                              </div>
                            ) : (
                              <div className="absolute inset-0 bg-white/95 flex flex-col items-center justify-center text-orange-600 animate-fade-in p-6">
                                <Key className="w-10 h-10 mb-4" />
                                <span className="font-bold text-lg">需要安全验证</span>
                                <span className="text-xs text-gray-500 mt-2 text-center">点击下方按钮，在官方窗口完成验证。</span>
                              </div>
                            )
                          )}
                          {qrStatus === 'error' && (
                            <div className="flex flex-col items-center">
                              <span className="text-red-500 font-bold mb-2">获取失败</span>
                              <button onClick={() => void startQRLogin()} className="flex items-center gap-1 rounded-lg bg-gray-200 px-3 py-1 text-xs hover:bg-gray-300">
                                <RefreshCw className="w-3 h-3"/>
                                重试
                              </button>
                            </div>
                          )}
                        </div>

                        {qrMessage && (
                          <p className="text-sm text-gray-600 font-medium bg-gray-50 px-4 py-3 rounded-2xl mb-3">
                            {qrMessage}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center justify-center gap-3">
                          {qrStatus === 'verification_required' && (
                            <button
                              type="button"
                              onClick={() => void handleContinueQRVerification()}
                              className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#FFE815] px-4 py-2 text-sm font-bold text-gray-900 hover:bg-yellow-300"
                            >
                              <Chrome className="h-4 w-4" />
                              本机打开官方窗口
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void startQRLogin()}
                            className="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-100 px-4 py-2 text-sm font-bold text-gray-700 transition-colors hover:bg-gray-200"
                          >
                            <RefreshCw className="w-4 h-4" />
                            重新生成二维码
                          </button>
                        </div>
                        <p className="mt-4 rounded-xl bg-gray-50 py-2 text-xs font-medium text-gray-400">
                          二维码生成和扫码阶段不启动浏览器；只有二次验证时由你主动打开专用 Chrome。
                        </p>
                      </div>
                    )}

                    {activeAddMethod === 'sms' && (
                      <div className="space-y-4">
                        <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                          <div className="flex items-start gap-3">
                            <Smartphone className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
                            <div>
                              <h4 className="font-bold text-gray-900">在闲鱼官方窗口完成验证码登录</h4>
                              <p className="mt-1 text-sm leading-6 text-gray-600">系统只等待官方页面返回登录状态，不接收或保存短信验证码。此方式到期后需要再次登录。</p>
                            </div>
                          </div>
                        </div>
                        <div>
                          <label className="mb-2 block text-sm font-bold text-gray-700">手机号（可选）</label>
                          <input
                            type="tel"
                            value={officialWindowAccount}
                            onChange={(event) => setOfficialWindowAccount(event.target.value)}
                            placeholder="用于在官方页面预填"
                            disabled={['processing', 'verification_required'].includes(officialWindowStatus)}
                            className="ios-input w-full rounded-xl px-4 py-3"
                          />
                        </div>
                        {officialWindowMessage && (
                          <div className={`rounded-lg px-4 py-3 text-sm font-bold ${officialWindowStatus === 'success' ? 'bg-emerald-50 text-emerald-700' : officialWindowStatus === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
                            {officialWindowMessage}
                          </div>
                        )}
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <button
                            type="button"
                            onClick={() => void handleOfficialWindowLogin()}
                            disabled={officialWindowSubmitting || ['processing', 'verification_required'].includes(officialWindowStatus)}
                            className="ios-btn-primary inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl px-5 font-bold disabled:opacity-60"
                          >
                            {officialWindowSubmitting || ['processing', 'verification_required'].includes(officialWindowStatus)
                              ? <Loader2 className="h-4 w-4 animate-spin" />
                              : <Chrome className="h-4 w-4" />}
                            {['processing', 'verification_required'].includes(officialWindowStatus) ? '等待官方窗口' : '打开官方登录窗口'}
                          </button>
                          {['processing', 'verification_required'].includes(officialWindowStatus) && (
                            <button
                              type="button"
                              onClick={() => void handleCancelOfficialLogin()}
                              className="min-h-11 rounded-xl border border-gray-300 px-5 font-bold text-gray-700"
                            >
                              取消
                            </button>
                          )}
                        </div>
                      </div>
                    )}

                    {activeAddMethod === 'extension' && (
                      <div className="space-y-4">
                        <div className="rounded-2xl border border-yellow-200 bg-yellow-50 p-4">
                          <div className="flex items-start gap-3">
                            <Chrome className="mt-0.5 h-5 w-5 text-yellow-700" />
                            <div>
                              <h4 className="font-bold text-gray-900">从日常 Chrome 主动导入</h4>
                              <p className="mt-1 text-sm leading-6 text-gray-600">
                                扩展只在你点击时读取当前 Cookie Store，并只发送到本机 127.0.0.1；不参与后台自动续期。
                              </p>
                            </div>
                          </div>
                        </div>

                        <div className="flex flex-wrap gap-3">
                          <a
                            href="/static/downloads/xianyu-cookie-importer.zip"
                            download
                            className="inline-flex items-center gap-2 rounded-full bg-gray-100 px-4 py-2 text-sm font-bold text-gray-700 hover:bg-gray-200"
                          >
                            <Upload className="h-4 w-4" />
                            下载扩展 ZIP
                          </a>
                          <button
                            type="button"
                            onClick={handleCreateExtensionPairing}
                            disabled={extensionBusy}
                            className="inline-flex items-center gap-2 rounded-full bg-[#FFE815] px-4 py-2 text-sm font-bold text-gray-900 hover:bg-yellow-300 disabled:opacity-60"
                          >
                            {extensionBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Key className="h-4 w-4" />}
                            创建一次性配对
                          </button>
                        </div>

                        {extensionPairing?.pairing_code && (
                          <div className="space-y-2">
                            <label className="block text-sm font-bold text-gray-700">复制到扩展的配对信息</label>
                            <div className="flex gap-2">
                              <textarea
                                readOnly
                                rows={3}
                                value={JSON.stringify({
                                  pairing_id: extensionPairing.pairing_id,
                                  pairing_code: extensionPairing.pairing_code,
                                })}
                                className="min-w-0 flex-1 resize-none rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs"
                                aria-label="扩展配对信息"
                              />
                              <button
                                type="button"
                                onClick={handleCopyExtensionPairing}
                                className="inline-flex h-11 items-center gap-2 rounded-xl bg-gray-900 px-3 text-sm font-bold text-white"
                              >
                                <Copy className="h-4 w-4" />
                                {extensionCopied ? '已复制' : '复制'}
                              </button>
                            </div>
                          </div>
                        )}

                        {extensionMessage && (
                          <p className={`rounded-2xl px-4 py-3 text-sm font-medium ${
                            extensionPairing?.status === 'success'
                              ? 'bg-green-50 text-green-700'
                              : extensionPairing?.status === 'failed' || extensionPairing?.status === 'expired'
                                ? 'bg-red-50 text-red-700'
                                : 'bg-gray-50 text-gray-600'
                          }`}>
                            {extensionMessage}
                          </p>
                        )}

                        <ol className="list-decimal space-y-1 pl-5 text-xs leading-5 text-gray-500">
                          <li>解压 ZIP，在 chrome://extensions 开启开发者模式并加载已解压扩展。</li>
                          <li>在本机 Chrome 登录闲鱼官网，并保持官方页面为当前标签页。</li>
                          <li>创建配对、复制到扩展，然后点击“导入到咸鱼监控台”。</li>
                        </ol>
                      </div>
                    )}

                    {activeAddMethod === 'password' && (
                      <form onSubmit={handlePasswordLoginSubmit} className="space-y-4">
                        <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
                          <div>
                            <h4 className="font-bold text-gray-900">支持自动续期</h4>
                            <p className="mt-1 text-sm text-gray-600">使用每账号独立 Chrome 档案；密码加密保存，仅在官方登录态完全失效后使用。</p>
                          </div>
                        </div>
                        <div>
                          <label className="block text-sm font-bold text-gray-700 mb-2">闲鱼账号/手机号</label>
                          <input
                            type="text"
                            value={passwordForm.account}
                            onChange={(e) => setPasswordForm({ ...passwordForm, account: e.target.value })}
                            placeholder="用于登录闲鱼官方网站"
                            className="w-full ios-input px-4 py-3 rounded-xl"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-bold text-gray-700 mb-2">登录密码</label>
                          <div className="relative">
                            <input
                              type={passwordForm.showPassword ? 'text' : 'password'}
                              value={passwordForm.password}
                              onChange={(e) => setPasswordForm({ ...passwordForm, password: e.target.value })}
                              placeholder="登录成功后加密保存"
                              className="w-full ios-input px-4 py-3 rounded-xl pr-12"
                            />
                            <button
                              type="button"
                              onClick={() => setPasswordForm({ ...passwordForm, showPassword: !passwordForm.showPassword })}
                              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                            >
                              {passwordForm.showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                            </button>
                          </div>
                          <p className="mt-2 text-xs text-gray-500">
                            密码会使用独立密钥加密保存，仅在官方登录态失效时用于自动续期。
                          </p>
                        </div>
                        <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                          <div>
                            <div className="font-bold text-gray-900">登录时显示浏览器</div>
                            <div className="text-xs text-gray-500">需要安全验证时，打开浏览器更容易完成操作。</div>
                          </div>
                          <ToggleControl
                            checked={passwordForm.show_browser}
                            onChange={(checked) => setPasswordForm({ ...passwordForm, show_browser: checked })}
                            label="登录时显示浏览器"
                          />
                        </div>
                        {passwordMessage && (
                          <div className={`text-sm font-bold rounded-2xl px-4 py-3 ${
                            passwordStatus === 'failed' ? 'bg-red-50 text-red-600' :
                            passwordStatus === 'success' ? 'bg-green-50 text-green-700' :
                            passwordStatus === 'verification_required' ? 'bg-orange-50 text-orange-700' :
                            'bg-gray-50 text-gray-600'
                          }`}>
                            {passwordMessage}
                          </div>
                        )}
                        {passwordStatus === 'verification_required' && (
                          <div className="space-y-3">
                            {passwordVerificationImage && (
                              <img
                                src={passwordVerificationImage}
                                alt="闲鱼安全验证截图"
                                className="w-full max-h-80 object-contain rounded-2xl bg-gray-50 border border-gray-100"
                              />
                            )}
                          </div>
                        )}
                        {(passwordStatus === 'processing' || passwordStatus === 'verification_required') && (
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => void handleShowOfficialBrowser()}
                              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-bold text-gray-700"
                            >
                              <ExternalLink className="h-4 w-4" />
                              本机打开官方窗口
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleCancelOfficialLogin()}
                              className="inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-bold text-gray-600"
                            >
                              <X className="h-4 w-4" />
                              取消
                            </button>
                          </div>
                        )}
                        <button
                          type="submit"
                          disabled={passwordSubmitting || passwordStatus === 'processing'}
                          className="w-full ios-btn-primary px-6 py-3 rounded-xl font-bold flex items-center justify-center gap-2 disabled:opacity-60"
                        >
                          {passwordSubmitting || passwordStatus === 'processing' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Key className="w-4 h-4" />}
                          {passwordSubmitting || passwordStatus === 'processing' ? '登录中...' : '开始账号密码登录'}
                        </button>
                      </form>
                    )}

                    {activeAddMethod === 'cookie' && (
                      <form onSubmit={handleManualCookieSubmit} className="space-y-4">
                        <div>
                          <label className="block text-sm font-bold text-gray-700 mb-2">Cookie</label>
                          <textarea
                            value={manualCookieForm.value}
                            onChange={(e) => setManualCookieForm({ ...manualCookieForm, value: e.target.value })}
                            placeholder="粘贴从浏览器复制的 Cookie"
                            className="w-full ios-input px-4 py-3 rounded-xl h-36 resize-none font-mono text-xs"
                          />
                          <p className="mt-2 text-xs text-gray-500">账号身份从 Cookie 内的 unb 读取，需同时包含至少一个核心会话字段。</p>
                        </div>
                        {manualCookieMessage && (
                          <div className={`text-sm font-bold rounded-2xl px-4 py-3 ${
                            manualCookieStatus === 'failed' ? 'bg-red-50 text-red-600' :
                            manualCookieStatus === 'success' ? 'bg-green-50 text-green-700' :
                            'bg-gray-50 text-gray-600'
                          }`}>
                            {manualCookieMessage}
                          </div>
                        )}
                        <button
                          type="submit"
                          disabled={manualCookieSubmitting}
                          className="w-full ios-btn-primary px-6 py-3 rounded-xl font-bold flex items-center justify-center gap-2 disabled:opacity-60"
                        >
                          {manualCookieSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                          {manualCookieSubmitting ? '保存中...' : '保存 Cookie'}
                        </button>
                      </form>
                    )}
                  </div>
              </div>
          </div>,
          document.body
      )}

      {/* 编辑账号弹窗 */}
      {activeModal === 'edit' && editingAccount && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container" style={{maxWidth: '600px'}}>
            <div className="modal-header">
              <div>
                <h3 className="text-2xl font-extrabold text-gray-900">编辑账号</h3>
                <p className="text-sm text-gray-500 mt-1">{editingAccount.nickname || editingAccount.remark || editingAccount.id}</p>
              </div>
              <button
                onClick={() => setActiveModal(null)}
                className="p-2 rounded-xl hover:bg-gray-100 transition-colors flex-shrink-0"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="modal-body space-y-6">
              {/* 账号ID */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">账号ID</label>
                <input
                  type="text"
                  value={editingAccount.id}
                  disabled
                  className="w-full ios-input px-4 py-3 rounded-xl bg-gray-50 text-gray-500"
                />
              </div>

              {/* 备注 */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">备注</label>
                <input
                  type="text"
                  value={editForm.remark}
                  onChange={(e) => setEditForm({ ...editForm, remark: e.target.value })}
                  placeholder="为账号添加备注"
                  className="w-full ios-input px-4 py-3 rounded-xl"
                />
              </div>

              {/* Cookie */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">Cookie</label>
                <CookieEditor
                  value={editForm.cookie}
                  onChange={(cookie) => setEditForm({ ...editForm, cookie })}
                />
              </div>

              {/* 自动确认收货 */}
              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900 flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-500" />
                    自动确认收货
                  </div>
                  <div className="text-xs text-gray-500">自动点击确认收货按钮</div>
                </div>
                <ToggleControl
                  checked={editForm.auto_confirm}
                  onChange={(checked) => setEditForm({ ...editForm, auto_confirm: checked })}
                  label="自动确认收货"
                />
              </div>

              {/* 暂停时长 */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2 flex items-center gap-2">
                  <Clock className="w-4 h-4 text-blue-500" />
                  暂停处理时长（分钟）
                </label>
                <input
                  type="number"
                  value={editForm.pause_duration}
                  onChange={(e) => setEditForm({ ...editForm, pause_duration: parseInt(e.target.value) || 0 })}
                  placeholder="0"
                  min="0"
                  max="1440"
                  className="w-full ios-input px-4 py-3 rounded-xl"
                />
                <p className="text-xs text-gray-500 mt-1">设置后会暂停处理该账号的订单，到时间后自动恢复</p>
              </div>

              {/* 登录信息 */}
              <div className="border-t border-gray-200 pt-6">
                <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <Key className="w-5 h-5 text-amber-500" />
                  登录信息
                </h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">用户名</label>
                    <input
                      type="text"
                      value={editForm.username}
                      onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
                      placeholder="闲鱼账号/手机号"
                      className="w-full ios-input px-4 py-3 rounded-xl"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">登录密码</label>
                    <div className="relative">
                      <input
                        type={editForm.showLoginPassword ? 'text' : 'password'}
                        value={editForm.login_password}
                        onChange={(e) => setEditForm({ ...editForm, login_password: e.target.value })}
                        placeholder={editingAccount.has_login_password ? '密码已保存，留空表示不修改' : '用于自动登录'}
                        className="w-full ios-input px-4 py-3 rounded-xl pr-12"
                      />
                      <button
                        type="button"
                        onClick={() => setEditForm({ ...editForm, showLoginPassword: !editForm.showLoginPassword })}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                      >
                        {editForm.showLoginPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                      </button>
                    </div>
                    <p className={`mt-1 text-xs font-medium ${editingAccount.login_credentials_valid ? 'text-emerald-600' : 'text-amber-600'}`}>
                      {editingAccount.login_credentials_valid
                        ? '登录信息已加密保存；官方档案完全退出后可使用这些凭据自动续期。'
                        : editingAccount.has_login_password
                          ? '已保存的信息格式异常，请重新填写正确的闲鱼登录账号和密码。'
                          : '尚未保存登录密码，Cookie 失效后需要人工重新登录。'}
                    </p>
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-bold text-gray-900">登录时显示浏览器</div>
                      <div className="text-xs text-gray-500">调试时可开启查看登录过程</div>
                    </div>
                    <ToggleControl
                      checked={editForm.show_browser}
                      onChange={(checked) => setEditForm({ ...editForm, show_browser: checked })}
                      label="编辑账号时显示登录浏览器"
                    />
                  </div>
                </div>
              </div>

              {/* Cookie 刷新 */}
              <div className="border-t border-gray-200 pt-6">
                <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <RefreshCw className="w-5 h-5 text-cyan-500" />
                  Cookie 刷新
                </h3>
                <div className="space-y-4">
                  {!editingAccount.auto_refresh_supported && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                      <p className="font-bold text-gray-900">当前方式到期后需要人工重新登录</p>
                      <p className="mt-1 text-sm text-gray-600">只有通过账号密码官方登录并保存有效凭据后，才可开启自动定时续期。</p>
                      <button
                        type="button"
                        onClick={() => openReauthMethod({ ...editingAccount, reauth_action: 'password_login' })}
                        className="mt-3 min-h-11 rounded-lg bg-gray-900 px-4 text-sm font-bold text-white"
                      >
                        使用账号密码重新登录
                      </button>
                    </div>
                  )}
                  <div className="flex items-center justify-between p-4 bg-cyan-50 rounded-xl">
                    <div>
                      <div className="font-bold text-gray-900">自动定时 Cookie 刷新</div>
                      <div className="text-xs text-gray-500">{editingAccount.auto_refresh_supported ? '关闭后仍可手动刷新，可降低频繁触发验证的概率。' : '当前登录方式不提供后台自动续期。'}</div>
                    </div>
                    <ToggleControl
                      checked={editForm.cookie_refresh_enabled}
                      onChange={(checked) => setEditForm({ ...editForm, cookie_refresh_enabled: checked })}
                      label="自动定时 Cookie 刷新"
                      disabled={!editingAccount.auto_refresh_supported}
                    />
                  </div>
                  {editForm.cookie_refresh_enabled && editingAccount.auto_refresh_supported && (
                    <div>
                      <label htmlFor="cookie-refresh-interval" className="block text-sm font-bold text-gray-700 mb-2">
                        刷新间隔
                      </label>
                      <select
                        id="cookie-refresh-interval"
                        value={editForm.cookie_refresh_interval_minutes}
                        onChange={(e) => setEditForm({
                          ...editForm,
                          cookie_refresh_interval_minutes: parseInt(e.target.value, 10) || DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES,
                        })}
                        className="w-full ios-input px-4 py-3 rounded-xl"
                      >
                        {COOKIE_REFRESH_INTERVAL_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                      <p className="text-xs text-gray-500 mt-1">建议使用 24 小时或更长间隔，减少账号风控压力。</p>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={() => setActiveModal(null)}
                  className="flex-1 px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors"
                  disabled={saving}
                >
                  取消
                </button>
                <button
                  onClick={handleSaveEdit}
                  className="flex-1 ios-btn-primary px-6 py-3 rounded-xl font-bold flex items-center justify-center gap-2"
                  disabled={saving}
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {saving ? '保存中...' : '保存'}
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* AI设置弹窗 */}
      {activeModal === 'ai-settings' && editingAccount && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container" style={{maxWidth: '600px'}}>
            <div className="modal-header">
              <div>
                <h3 className="text-2xl font-extrabold text-gray-900 flex items-center gap-2">
                  <Bot className="w-6 h-6 text-purple-500" />
                  AI助手设置
                </h3>
                <p className="text-sm text-gray-500 mt-1">{editingAccount.nickname || editingAccount.remark || editingAccount.id}</p>
              </div>
              <button
                onClick={() => setActiveModal(null)}
                className="p-2 rounded-xl hover:bg-gray-100 transition-colors flex-shrink-0"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="modal-body space-y-6">
              {/* 启用AI */}
              <div className="flex items-center justify-between gap-4 p-4 bg-purple-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900 flex items-center gap-2">
                    <Bot className="w-4 h-4 text-purple-500" />
                    启用AI自动回复
                  </div>
                  <div className="text-xs text-gray-500">关键词未命中时，AI 按当前商品资料处理买家咨询</div>
                </div>
                <ToggleControl
                  checked={aiSettings.ai_enabled}
                  onChange={(checked) => setAiSettings({ ...aiSettings, ai_enabled: checked })}
                  label="启用 AI 自动回复"
                />
              </div>

              <div className="border-t border-gray-200 pt-6">
                <h3 className="text-lg font-bold text-gray-900 mb-4">实际 AI 服务</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">AI 平台</label>
                    <select
                      value={aiSettings.provider_profile_id || ''}
                      onChange={(e) => {
                        const provider = aiProviders.find((item) => item.id === Number(e.target.value));
                        if (!provider) return;
                        setAiSettings({
                          ...aiSettings,
                          provider_profile_id: provider.id,
                          provider_name: provider.name,
                          provider_type: provider.provider_type,
                          provider_status: provider.verification_status,
                          base_url: provider.base_url,
                          model_name: provider.default_model || provider.models[0] || '',
                          api_key_source: 'provider',
                          api_key_masked: provider.api_key_masked,
                          has_effective_api_key: provider.api_key_configured,
                          provider_test_token: '',
                        });
                      }}
                      className="w-full ios-input px-4 py-3 rounded-xl bg-white"
                    >
                      {aiProviders.length === 0 && <option value="">请先到“系统与 AI”添加平台</option>}
                      {aiProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}{provider.is_default ? '（默认）' : ''}</option>)}
                    </select>
                  </div>
                  <div>
                    <div className="mb-2 flex items-center justify-between gap-3"><label htmlFor="account-ai-model" className="block text-sm font-bold text-gray-700">模型</label><button type="button" onClick={() => void handleRefreshProviderModels()} disabled={refreshingModels || !aiSettings.provider_profile_id} className="inline-flex items-center gap-1.5 text-xs font-bold text-gray-600 hover:text-black disabled:opacity-50">{refreshingModels ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}刷新模型</button></div>
                    <ModelSelector
                      models={aiProviders.find((item) => item.id === aiSettings.provider_profile_id)?.models || []}
                      value={aiSettings.model_name}
                      onChange={(modelName) => setAiSettings({ ...aiSettings, model_name: modelName, provider_test_token: '' })}
                      disabled={!aiSettings.provider_profile_id}
                    />
                    <p className="text-xs text-gray-500 mt-1">选择后必须点击“测试并应用”；测试失败不会改变当前生效模型。</p>
                  </div>
                  <div className="grid grid-cols-1 gap-2 rounded-xl bg-gray-50 px-3 py-3 text-xs text-gray-600 sm:grid-cols-2">
                    <div><span className="font-bold text-gray-800">Key 来源：</span>{aiSettings.api_key_source === 'provider' ? '平台配置库' : aiSettings.api_key_source === 'account' ? '旧版账号专属' : aiSettings.api_key_source === 'global' ? '旧版系统全局' : '未配置'} {aiSettings.api_key_masked || ''}</div>
                    <div><span className="font-bold text-gray-800">连接状态：</span>{aiProviders.find((item) => item.id === aiSettings.provider_profile_id)?.verification_status === 'verified' ? '已验证' : '待测试'}</div>
                  </div>
                </div>
              </div>

              {/* 砍价策略 */}
              <div className="border-t border-gray-200 pt-6">
                <h3 className="text-lg font-bold text-gray-900 mb-4">砍价策略</h3>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">最大折扣比例 (%)</label>
                    <input
                      type="number"
                      value={aiSettings.max_discount_percent}
                      onChange={(e) => setAiSettings({ ...aiSettings, max_discount_percent: parseInt(e.target.value) || 0 })}
                      className="w-full ios-input px-4 py-3 rounded-xl"
                      min="0"
                      max="100"
                    />
                    <p className="text-xs text-gray-500 mt-1">例如：10表示最多降价10%</p>
                  </div>
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">最大折扣金额 (元)</label>
                    <input
                      type="number"
                      value={aiSettings.max_discount_amount}
                      onChange={(e) => setAiSettings({ ...aiSettings, max_discount_amount: parseInt(e.target.value) || 0 })}
                      className="w-full ios-input px-4 py-3 rounded-xl"
                      min="0"
                    />
                    <p className="text-xs text-gray-500 mt-1">例如：100表示最多降价100元</p>
                  </div>
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-2">最大砍价轮次</label>
                    <input
                      type="number"
                      value={aiSettings.max_bargain_rounds}
                      onChange={(e) => setAiSettings({ ...aiSettings, max_bargain_rounds: parseInt(e.target.value) || 1 })}
                      className="w-full ios-input px-4 py-3 rounded-xl"
                      min="1"
                      max="10"
                    />
                    <p className="text-xs text-gray-500 mt-1">买家最多可以砍价的次数</p>
                  </div>
                </div>
              </div>

              {/* 自定义提示词 */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">自定义提示词（可选）</label>
                <textarea
                  value={aiSettings.custom_prompts}
                  onChange={(e) => setAiSettings({ ...aiSettings, custom_prompts: e.target.value })}
                  placeholder="输入自定义的AI回复规则或风格指引...&#10;&#10;例如：回复时保持礼貌专业、使用简洁的语言、强调产品质量等"
                  className="w-full ios-input px-4 py-3 rounded-xl h-40 resize-none"
                />
              </div>

              {/* AI如何工作 */}
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <h4 className="font-bold text-blue-900 mb-2 flex items-center gap-2">
                  <Settings className="w-4 h-4" />
                  AI如何工作
                </h4>
                <ul className="text-xs text-blue-800 space-y-1">
                  <li>• 商品知识与详情优先，避免跨商品套用话术</li>
                  <li>• 关键词规则优先命中，未命中时才调用 AI</li>
                  <li>• 价格、技术和默认专家按买家意图选择</li>
                  <li>• 账号通用提示词只控制整体风格，不覆盖商品事实</li>
                </ul>
              </div>
              {aiSaveNotice && <InlineNotice tone={aiSaveNotice.tone}>{aiSaveNotice.text}</InlineNotice>}
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={() => setActiveModal(null)}
                  className="flex-1 px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors"
                  disabled={saving}
                >
                  取消
                </button>
                <button
                  onClick={handleSaveAISettings}
                  className="flex-1 ios-btn-primary px-4 py-3 rounded-xl text-sm font-bold whitespace-nowrap flex items-center justify-center gap-1.5"
                  disabled={saving}
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  {testingProvider ? '测试中...' : saving ? '应用中...' : '测试并应用'}
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {trainingAccount && (
        <AITrainingLab
          account={trainingAccount}
          onClose={() => setTrainingAccount(null)}
          onSaved={loadAccounts}
        />
      )}
    </div>
  );
};

export default AccountList;
