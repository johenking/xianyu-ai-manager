import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, AIProviderProfile, AIReplySettings, AutoReplyDiagnostics } from '../types';
import AITrainingLab from './AITrainingLab';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import {
  getAccountDetails,
  updateAccountStatus,
  deleteAccount,
  generateQRLogin,
  checkQRLoginStatus,
  continueQRLoginAfterVerification,
  addAccountCookie,
  passwordLogin,
  checkPasswordLoginStatus,
  updateAccountRemark,
  updateAccountAutoConfirm,
  updateAccountPauseDuration,
  updateAccountCookie,
  updateAccountLoginInfo,
  updateAccountAISettings,
  getAllAISettings,
  getAccountAISettings,
  getAutoReplyDiagnostics,
  getAIProviders,
  refreshAIProviderModels,
  testAIProvider
} from '../services/api';
import {
  Plus, Power, Edit2, Trash2, QrCode, X, Check, Loader2,
  MessageSquare, RefreshCw, Save, User, Clock, MessageCircle,
  Upload, Key, Eye, EyeOff, Bot, Settings, ExternalLink
} from 'lucide-react';

type ModalType = 'edit' | 'ai-settings' | null;
type AddLoginMethod = 'qr' | 'password' | 'cookie';
type AddLoginStatus = 'idle' | 'processing' | 'success' | 'failed' | 'verification_required';

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
  const [activeModal, setActiveModal] = useState<ModalType>(null);
  const [editingAccount, setEditingAccount] = useState<AccountDetail | null>(null);
  const [trainingAccount, setTrainingAccount] = useState<AccountDetail | null>(null);
  const [diagnostics, setDiagnostics] = useState<Record<string, AutoReplyDiagnostics>>({});
  const [diagnosingId, setDiagnosingId] = useState<string>('');
  const [passwordForm, setPasswordForm] = useState({
    account_id: '',
    account: '',
    password: '',
    show_browser: true,
    showPassword: false,
  });
  const [passwordStatus, setPasswordStatus] = useState<AddLoginStatus>('idle');
  const [passwordMessage, setPasswordMessage] = useState('');
  const [passwordVerificationUrl, setPasswordVerificationUrl] = useState('');
  const [passwordVerificationImage, setPasswordVerificationImage] = useState('');
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const [manualCookieForm, setManualCookieForm] = useState({ id: '', value: '' });
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

  const closeAddModal = () => {
    clearQRPolling();
    clearPasswordPolling();
    setShowAddModal(false);
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationUrl('');
    setPasswordVerificationImage('');
    setPasswordForm({
      account_id: '',
      account: '',
      password: '',
      show_browser: true,
      showPassword: false,
    });
    setManualCookieStatus('idle');
    setManualCookieMessage('');
    setManualCookieForm({ id: '', value: '' });
  };

  const resetPasswordStatus = () => {
    clearPasswordPolling();
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationUrl('');
    setPasswordVerificationImage('');
  };

  const resetManualCookieStatus = () => {
    setManualCookieStatus('idle');
    setManualCookieMessage('');
  };

  const getReachableVerificationImage = (imageUrl?: string | null, screenshotPath?: string | null) => {
    if (imageUrl) return imageUrl;
    if (!screenshotPath) return '';
    if (screenshotPath.startsWith('http') || screenshotPath.startsWith('/static/')) {
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
    };
  }, []);

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

      await Promise.all(promises);
      setActiveModal(null);
      await loadAccounts();
      setPageNotice({ tone: 'success', text: '账号设置已保存' });
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
    verificationMessage?: string
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
        statusRes.verification_screenshot_path
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
          ? '请用手机版闲鱼扫描图中的身份验证二维码，完成后系统会自动检测。'
          : '正在打开闲鱼安全验证页面，请稍候。')
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
      setQrMessage(statusRes.message || (qrHadVerificationRef.current ? '安全验证会话已过期，请重新生成二维码' : '二维码已过期，请重新扫码'));
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
    setQrMessage('正在从后台浏览器会话检查安全验证结果');
    try {
      const result = await continueQRLoginAfterVerification(qrSessionId);
      handleQRStatusResult(result);
      if (result.status === 'processing' || result.status === 'scanned') {
        startQRStatusPolling(qrSessionId);
      }
    } catch (error) {
      clearQRPolling();
      setQrStatus('verification_required');
      setQrMessage(error instanceof Error ? error.message : '继续检查安全验证结果失败，请重试');
    }
  };

  const handleAddMethodChange = (method: AddLoginMethod) => {
    setActiveAddMethod(method);
    if (method === 'qr' && !qrSessionId && qrStatus !== 'loading') {
      startQRLogin();
    }
  };

  const startPasswordStatusPolling = (sessionId: string) => {
    clearPasswordPolling();
    passwordPollingRef.current = setInterval(async () => {
      try {
        const statusRes = await checkPasswordLoginStatus(sessionId);
        if (statusRes.status === 'processing') {
          setPasswordStatus('processing');
          setPasswordMessage(statusRes.message || '登录处理中，请稍候');
        } else if (statusRes.status === 'verification_required') {
          setPasswordStatus('verification_required');
          setPasswordMessage(statusRes.message || '需要完成闲鱼安全验证');
          setPasswordVerificationUrl(statusRes.verification_url || '');
          setPasswordVerificationImage(getReachableVerificationImage(statusRes.qr_code_url, statusRes.screenshot_path));
        } else if (statusRes.status === 'success') {
          clearPasswordPolling();
          setPasswordStatus('success');
          setPasswordMessage(statusRes.message || '账号密码登录成功，正在刷新账号列表');
          setPasswordForm((current) => ({ ...current, password: '', showPassword: false }));
          setTimeout(() => {
            closeAddModal();
            loadAccounts();
          }, 1000);
        } else if (
          statusRes.status === 'failed' ||
          statusRes.status === 'error' ||
          statusRes.status === 'not_found' ||
          statusRes.status === 'forbidden'
        ) {
          clearPasswordPolling();
          setPasswordStatus('failed');
          setPasswordMessage(statusRes.message || statusRes.error || '账号密码登录失败');
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
    const accountId = passwordForm.account_id.trim() || account;
    if (!accountId || !account || !passwordForm.password) {
      setPasswordStatus('failed');
      setPasswordMessage('请填写账号ID、登录账号和密码');
      return;
    }

    setPasswordSubmitting(true);
    setPasswordStatus('processing');
    setPasswordMessage('正在启动账号密码登录');
    try {
      const result = await passwordLogin({
        account_id: accountId,
        account,
        password: passwordForm.password,
        show_browser: passwordForm.show_browser,
      });
      if (!result.success || !result.session_id) {
        setPasswordStatus('failed');
        setPasswordMessage(result.message || '账号密码登录任务启动失败');
        return;
      }
      setPasswordMessage(result.message || '登录任务已启动，请等待');
      startPasswordStatusPolling(result.session_id);
    } catch (error) {
      setPasswordStatus('failed');
      setPasswordMessage(error instanceof Error ? error.message : '账号密码登录请求失败');
    } finally {
      setPasswordSubmitting(false);
    }
  };

  const handleManualCookieSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    resetManualCookieStatus();
    const id = manualCookieForm.id.trim();
    const value = manualCookieForm.value.trim();
    if (!id || !value) {
      setManualCookieStatus('failed');
      setManualCookieMessage('请填写账号ID和 Cookie');
      return;
    }

    setManualCookieSubmitting(true);
    setManualCookieStatus('processing');
    setManualCookieMessage('正在保存 Cookie');
    try {
      await addAccountCookie({ id, value });
      setManualCookieStatus('success');
      setManualCookieMessage('Cookie 已保存，正在刷新账号列表');
      setManualCookieForm({ id: '', value: '' });
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
          return (
          <div key={account.id} className="ios-card p-4 sm:p-6 rounded-2xl group hover:border-[#FFE815] transition-all duration-300">
          <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-5">
            <div className="flex min-w-0 items-start sm:items-center gap-4 sm:gap-6">
              <div className="relative">
                <img
                  src={account.avatar_url}
                  alt="avatar"
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
                   {account.pause_duration > 0 && <span className="text-xs bg-blue-50 text-blue-700 px-3 py-1.5 rounded-lg font-bold flex items-center gap-1.5"><Clock className="w-3 h-3"/> 暂停{account.pause_duration}分钟</span>}
                   {diagnosis && (
                    <span className={`text-xs px-3 py-1.5 rounded-lg font-bold ${diagnosis.ready ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                      {diagnosis.ready ? '自动回复就绪' : `${diagnosis.issues.length} 个问题`}
                    </span>
                   )}
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

      {/* 添加账号弹窗 */}
      {showAddModal && createPortal(
          <div className="modal-overlay-centered">
              <div className="modal-container" style={{maxWidth: '720px'}}>
                  <div className="modal-header">
                    <div>
                      <h3 className="text-2xl font-extrabold text-gray-900">添加账号</h3>
                      <p className="text-sm text-gray-500 mt-1">扫码优先，账号密码和 Cookie 可作为备用方式。</p>
                    </div>
                    <button
                      onClick={closeAddModal}
                      className="p-2 rounded-xl hover:bg-gray-100 transition-colors flex-shrink-0"
                    >
                      <X className="w-5 h-5 text-gray-500" />
                    </button>
                  </div>

                  <div className="modal-body space-y-6">
                    <div className="grid grid-cols-3 gap-2 rounded-2xl bg-gray-100 p-1">
                      <button
                        type="button"
                        onClick={() => handleAddMethodChange('qr')}
                        className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'qr' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <QrCode className="w-4 h-4" />
                        扫码
                      </button>
                      <button
                        type="button"
                        onClick={() => handleAddMethodChange('password')}
                        className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'password' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <Key className="w-4 h-4" />
                        账号密码
                      </button>
                      <button
                        type="button"
                        onClick={() => handleAddMethodChange('cookie')}
                        className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-bold transition-colors ${
                          activeAddMethod === 'cookie' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
                        }`}
                      >
                        <Upload className="w-4 h-4" />
                        Cookie
                      </button>
                    </div>

                    {activeAddMethod === 'qr' && (
                      <div className="text-center">
                        <div className={`${
                          qrStatus === 'verification_required' && qrVerificationImage
                            ? 'w-full max-w-xl h-[360px] rounded-2xl'
                            : 'w-64 h-64 rounded-[2rem]'
                        } bg-[#F7F8FA] mx-auto flex items-center justify-center overflow-hidden border-4 border-white shadow-inner mb-6 relative`}>
                          {qrStatus === 'loading' && <Loader2 className="w-10 h-10 text-[#FFE815] animate-spin" />}
                          {qrStatus === 'waiting' && <img src={qrCodeUrl} alt="闲鱼登录二维码" className="w-full h-full p-2" />}
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
                                <span className="absolute bottom-3 rounded-full bg-white/95 px-3 py-1 text-xs font-bold text-orange-600 shadow-sm">用手机版闲鱼扫描图中的二维码</span>
                              </div>
                            ) : (
                              <div className="absolute inset-0 bg-white/95 flex flex-col items-center justify-center text-orange-600 animate-fade-in p-6">
                                <Key className="w-10 h-10 mb-4" />
                                <span className="font-bold text-lg">需要安全验证</span>
                                <span className="text-xs text-gray-500 mt-2 text-center">完成验证后回到这里继续检查。</span>
                              </div>
                            )
                          )}
                          {qrStatus === 'error' && (
                            <div className="flex flex-col items-center">
                              <span className="text-red-500 font-bold mb-2">获取失败</span>
                              <button onClick={startQRLogin} className="text-xs bg-gray-200 px-3 py-1 rounded-full flex items-center gap-1 hover:bg-gray-300">
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
                              onClick={handleContinueQRVerification}
                              className="inline-flex items-center justify-center gap-2 text-sm font-bold bg-[#FFE815] text-gray-900 px-4 py-2 rounded-full hover:bg-yellow-300 transition-colors"
                            >
                              <RefreshCw className="w-4 h-4" />
                              我已完成验证，立即检查
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={startQRLogin}
                            className="inline-flex items-center justify-center gap-2 text-sm font-bold bg-gray-100 text-gray-700 px-4 py-2 rounded-full hover:bg-gray-200 transition-colors"
                          >
                            <RefreshCw className="w-4 h-4" />
                            重新生成二维码
                          </button>
                        </div>
                        <p className="text-xs text-gray-400 font-medium bg-gray-50 py-2 rounded-xl mt-4">二维码有效期为5分钟；二次验证由后台浏览器最多等待7.5分钟。</p>
                      </div>
                    )}

                    {activeAddMethod === 'password' && (
                      <form onSubmit={handlePasswordLoginSubmit} className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <div>
                            <label className="block text-sm font-bold text-gray-700 mb-2">账号ID</label>
                            <input
                              type="text"
                              value={passwordForm.account_id}
                              onChange={(e) => setPasswordForm({ ...passwordForm, account_id: e.target.value })}
                              placeholder="留空时使用登录账号"
                              className="w-full ios-input px-4 py-3 rounded-xl"
                            />
                          </div>
                          <div>
                            <label className="block text-sm font-bold text-gray-700 mb-2">闲鱼账号/手机号</label>
                            <input
                              type="text"
                              value={passwordForm.account}
                              onChange={(e) => setPasswordForm({ ...passwordForm, account: e.target.value })}
                              placeholder="用于登录闲鱼"
                              className="w-full ios-input px-4 py-3 rounded-xl"
                            />
                          </div>
                        </div>
                        <div>
                          <label className="block text-sm font-bold text-gray-700 mb-2">登录密码</label>
                          <div className="relative">
                            <input
                              type={passwordForm.showPassword ? 'text' : 'password'}
                              value={passwordForm.password}
                              onChange={(e) => setPasswordForm({ ...passwordForm, password: e.target.value })}
                              placeholder="仅用于本次登录请求"
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
                        </div>
                        <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                          <div>
                            <div className="font-bold text-gray-900">登录时显示浏览器</div>
                            <div className="text-xs text-gray-500">需要安全验证时，打开浏览器更容易完成操作。</div>
                          </div>
                          <button
                            type="button"
                            onClick={() => setPasswordForm({ ...passwordForm, show_browser: !passwordForm.show_browser })}
                            className={`w-14 h-8 rounded-full transition-colors duration-300 relative ${
                              passwordForm.show_browser ? 'bg-[#FFE815]' : 'bg-gray-300'
                            }`}
                          >
                            <span
                              className={`absolute top-1 w-6 h-6 bg-white rounded-full shadow-md transition-transform duration-300 ${
                                passwordForm.show_browser ? 'translate-x-7' : 'translate-x-1'
                              }`}
                            />
                          </button>
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
                            {passwordVerificationUrl && (
                              <a
                                href={passwordVerificationUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center justify-center gap-2 text-sm font-bold bg-black text-white px-4 py-2 rounded-full hover:bg-gray-800 transition-colors"
                              >
                                <ExternalLink className="w-4 h-4" />
                                打开安全验证
                              </a>
                            )}
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
                          <label className="block text-sm font-bold text-gray-700 mb-2">账号ID</label>
                          <input
                            type="text"
                            value={manualCookieForm.id}
                            onChange={(e) => setManualCookieForm({ ...manualCookieForm, id: e.target.value })}
                            placeholder="例如闲鱼 userId / unb"
                            className="w-full ios-input px-4 py-3 rounded-xl"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-bold text-gray-700 mb-2">Cookie</label>
                          <textarea
                            value={manualCookieForm.value}
                            onChange={(e) => setManualCookieForm({ ...manualCookieForm, value: e.target.value })}
                            placeholder="粘贴从浏览器复制的 Cookie"
                            className="w-full ios-input px-4 py-3 rounded-xl h-36 resize-none font-mono text-xs"
                          />
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
                <textarea
                  value={editForm.cookie}
                  onChange={(e) => setEditForm({ ...editForm, cookie: e.target.value })}
                  placeholder="更新账号Cookie"
                  className="w-full ios-input px-4 py-3 rounded-xl h-32 resize-none font-mono text-xs"
                />
                <p className="text-xs text-gray-500 mt-1">当前Cookie长度: {editForm.cookie.length} 字符</p>
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
                <button
                  type="button"
                  onClick={() => setEditForm({ ...editForm, auto_confirm: !editForm.auto_confirm })}
                  className={`w-14 h-8 rounded-full transition-colors duration-300 relative ${
                    editForm.auto_confirm ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-6 h-6 bg-white rounded-full shadow-md transition-transform duration-300 ${
                      editForm.auto_confirm ? 'translate-x-7' : 'translate-x-1'
                    }`}
                  />
                </button>
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
                        placeholder="用于自动登录"
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
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-bold text-gray-900">登录时显示浏览器</div>
                      <div className="text-xs text-gray-500">调试时可开启查看登录过程</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setEditForm({ ...editForm, show_browser: !editForm.show_browser })}
                      className={`w-14 h-8 rounded-full transition-colors duration-300 relative ${
                        editForm.show_browser ? 'bg-[#FFE815]' : 'bg-gray-300'
                      }`}
                    >
                      <span
                        className={`absolute top-1 w-6 h-6 bg-white rounded-full shadow-md transition-transform duration-300 ${
                          editForm.show_browser ? 'translate-x-7' : 'translate-x-1'
                        }`}
                      />
                    </button>
                  </div>
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
                    <input
                      id="account-ai-model"
                      list="account-ai-model-options"
                      type="search"
                      value={aiSettings.model_name}
                      onChange={(e) => setAiSettings({ ...aiSettings, model_name: e.target.value, provider_test_token: '' })}
                      className="w-full ios-input px-4 py-3 rounded-xl"
                      placeholder="选择或手填模型 ID"
                    />
                    <datalist id="account-ai-model-options">
                      {(aiProviders.find((item) => item.id === aiSettings.provider_profile_id)?.models || []).map((model) => <option key={model} value={model} />)}
                    </datalist>
                    <p className="text-xs text-gray-500 mt-1">模型列表读取失败时可手填 ID，但仍需生成测试回复才能应用。</p>
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
