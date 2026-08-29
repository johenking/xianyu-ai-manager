import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, AccountSessionRefreshStatus, AIProviderProfile, AIReplySettings, AutoReplyDiagnostics } from '../types';
import AITrainingLab from './AITrainingLab';
import ModelSelector from './ModelSelector';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { confirmDialog } from './ui/ConfirmDialog';
import { AccountAvatar, CookieEditor } from './ui/AccountVisuals';
import AuthenticatedImage from './ui/AuthenticatedImage';
import BrowserInteractionSurface from './ui/BrowserInteractionSurface';
import {
  getAccountDetails,
  updateAccountStatus,
  deleteAccount,
  generateQRLogin,
  checkQRLoginStatus,
  cancelQRLogin,
  createBrowserExtensionPairing,
  getBrowserExtensionPairing,
  registerClientBrowserDevice,
  createClientBrowserLoginSession,
  getClientBrowserLoginSession,
  confirmClientBrowserLoginSession,
  cancelClientBrowserLoginSession,
  bindAccountRenewalDevice,
  createOfficialLoginSession,
  getOfficialLoginSession,
  showOfficialLoginBrowser,
  cancelOfficialLoginSession,
  interactWithOfficialLogin,
  interactWithQRLogin,
  addAccountCookie,
  updateAccountRemark,
  updateAccountAutoConfirm,
  updateAccountAutoRate,
  updateAccountPauseDuration,
  updateAccountCookie,
  updateAccountCookieRefreshSettings,
  updateAccountProxy,
  testAccountProxy,
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
  testAIProvider,
  getAiReplyStrategies,
  updateAiReplyStrategies
} from '../services/api';
import type { BrowserInteractionAction, ReplyStrategy, ProxyProbeResult } from '../services/api';
import type {
  ClientBrowserDevicePublic,
  ClientBrowserLoginSession,
} from '../services/api';
import { ApiRequestError } from '../services/request';
import {
  Plus, Power, Edit2, Trash2, QrCode, X, Check, Loader2,
  MessageSquare, RefreshCw, Save, User, Clock, MessageCircle,
  Upload, Key, Bot, Settings, ExternalLink, Chrome, Copy,
  Smartphone, ChevronDown, ChevronUp, AlertTriangle, ShieldCheck, Globe
} from 'lucide-react';

type ModalType = 'edit' | 'ai-settings' | null;
type AddLoginMethod = 'qr' | 'sms' | 'extension' | 'password' | 'cookie';
type AddLoginStatus = 'idle' | 'processing' | 'success' | 'failed' | 'verification_required';
type QRLoginEntryMode = 'api' | 'client' | 'browser' | null;
type InteractiveOfficialLoginMode = 'qr' | 'sms';
type ClientBrowserLoginStep =
  | 'idle'
  | 'opening_browser'
  | 'waiting_user'
  | 'validating'
  | 'awaiting_confirmation'
  | 'success'
  | 'retryable_error';

interface BrowserInteractionDescriptor {
  imageUrl: string;
  frameRevision: number;
}

const DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES = 1440;
const CLIENT_BROWSER_EXTENSION_VERSION = '1.2.3';
const CLIENT_BROWSER_PROTOCOL_VERSION = 1;
const CLIENT_BROWSER_EXTENSION_URL = '/static/downloads/xianyu-browser-bridge-1.2.3.zip';

type ClientBrowserConnectionState = {
  state:
    | 'idle'
    | 'detecting'
    | 'extension_missing'
    | 'extension_outdated'
    | 'extension_not_injected'
    | 'device_initialization_failed'
    | 'auth_expired'
    | 'device_registration_conflict'
    | 'device_registration_failed'
    | 'connected';
  title: string;
  detail: string;
  extensionVersion?: string;
};

class ClientBrowserBridgeError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = 'ClientBrowserBridgeError';
    this.code = code;
  }
}

const compareVersions = (left: string, right: string) => {
  const leftParts = left.split('.').map(Number);
  const rightParts = right.split('.').map(Number);
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
    const difference = (leftParts[index] || 0) - (rightParts[index] || 0);
    if (difference !== 0) return difference;
  }
  return 0;
};
const ACTIVE_SESSION_REFRESH_STATES = new Set(['refreshing', 'verification_required']);
const ACTIVE_SESSION_POLL_INTERVAL_MS = 3_000;
const STABLE_SESSION_POLL_INTERVAL_MS = 15_000;
const ACTIVE_BROWSER_QR_VIEW_STATES = new Set([
  'loading',
  'waiting',
  'verification_required',
]);
const ACTIVE_API_QR_STATES = new Set([
  'loading',
  'waiting',
  'scanned',
  'verification_required',
  'processing',
]);
const ACTIVE_EXTENSION_PAIRING_STATES = new Set(['waiting', 'received', 'validating']);
const COOKIE_REFRESH_INTERVAL_OPTIONS = [
  { value: 60, label: '1 小时' },
  { value: 360, label: '6 小时' },
  { value: 720, label: '12 小时' },
  { value: 1440, label: '24 小时' },
  { value: 4320, label: '3 天' },
  { value: 10080, label: '7 天' },
];

interface AccountListProps {
  isAdmin?: boolean;
}

const isLoopbackHostname = (hostname: string) => {
  const normalized = hostname.trim().toLowerCase().replace(/^\[|\]$/g, '');
  return normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1';
};

// 正式控制台域名使用服务端 Chrome，但与回环的窗口呈现方式不同：回环弹出本机
// 窗口，正式域名在网页内显示云端画面；陌生域名继续使用浏览器扩展。
// 与后端 browser_extension_pairing.PUBLIC_CONSOLE_ORIGIN 保持一致。
const SERVER_BROWSER_CONSOLE_HOSTS = ['xianyu.cxywjx.top'];

const isServerBrowserHostname = (hostname: string) => (
  isLoopbackHostname(hostname)
  || SERVER_BROWSER_CONSOLE_HOSTS.includes(hostname.trim().toLowerCase())
);

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

const RETRYABLE_SESSION_ERROR_CODES = new Set([
  'token_probe_exception',
  'token_probe_failed',
  'token_probe_retry_exception',
  'session_probe_retryable',
  'cookie_persist_failed',
  'listener_handoff_failed',
]);

const isRetryableSessionStatus = (status?: AccountSessionRefreshStatus | null) => (
  status?.state === 'failed' && RETRYABLE_SESSION_ERROR_CODES.has(status.error_code)
);

const sessionStatusMessage = (status: AccountSessionRefreshStatus) => (
  isRetryableSessionStatus(status)
    ? '平台连接暂时异常，系统会自动重试；原登录态已保留。'
    : status.message
);

const AccountList: React.FC<AccountListProps> = () => {
  const consoleHostname = window.location.hostname;
  const isLoopbackConsole = isLoopbackHostname(consoleHostname);
  const canUseServerBrowser = isServerBrowserHostname(consoleHostname);
  const usesEmbeddedCloudBrowser = canUseServerBrowser && !isLoopbackConsole;
  const serverBrowserLabel = usesEmbeddedCloudBrowser ? '云端 Chrome' : '本机 Chrome';
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [activeAddMethod, setActiveAddMethod] = useState<AddLoginMethod>('qr');
  const [qrEntryMode, setQrEntryMode] = useState<QRLoginEntryMode>(null);
  const [qrCodeUrl, setQrCodeUrl] = useState<string>('');
  const [qrSessionId, setQrSessionId] = useState<string>('');
  const [qrStatus, setQrStatus] = useState<string>('pending');
  const [qrMessage, setQrMessage] = useState<string>('');
  const [qrVerificationImage, setQrVerificationImage] = useState<string>('');
  const qrPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const qrPollingInFlightRef = useRef(false);
  const qrHadVerificationRef = useRef(false);
  const [qrInteraction, setQrInteraction] = useState<BrowserInteractionDescriptor | null>(null);
  const passwordPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const officialPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const extensionPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const extensionPollingInFlightRef = useRef(false);
  const clientBrowserPollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const clientBrowserCommandRef = useRef<Map<string, {
    resolve: (value: unknown) => void;
    reject: (reason?: unknown) => void;
    timer: number;
  }>>(new Map());
  const clientBrowserContentReadyRef = useRef(false);
  const clientBrowserErrorRef = useRef('请先安装并启用浏览器连接扩展');
  const clientBrowserDetectionRef = useRef<Promise<ClientBrowserDevicePublic | null> | null>(null);
  const activeClientBrowserSessionRef = useRef('');
  const clientBrowserStartInFlightRef = useRef(false);
  const loginFlowGenerationRef = useRef(0);
  const [clientBrowserDevice, setClientBrowserDevice] = useState<ClientBrowserDevicePublic | null>(null);
  const [clientBrowserConnection, setClientBrowserConnection] = useState<ClientBrowserConnectionState>({
    state: 'idle',
    title: '本机 Chrome 登录已就绪',
    detail: '点击主登录按钮后，会打开当前设备浏览器的官方登录页。',
  });
  const [clientBrowserStep, setClientBrowserStep] = useState<ClientBrowserLoginStep>('idle');
  const [showClientBrowserInstallGuide, setShowClientBrowserInstallGuide] = useState(false);
  const [clientBrowserSession, setClientBrowserSession] = useState<ClientBrowserLoginSession | null>(null);
  const [renewalSetup, setRenewalSetup] = useState<{
    accountId: string;
    deviceId: string;
    loginSessionId: string;
    username: string;
    password: string;
    authorized: boolean;
    busy: boolean;
    message: string;
  } | null>(null);
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
  const [passwordInteraction, setPasswordInteraction] = useState<BrowserInteractionDescriptor | null>(null);
  const [passwordSubmitting, setPasswordSubmitting] = useState(false);
  const [officialWindowAccount, setOfficialWindowAccount] = useState('');
  const [officialWindowStatus, setOfficialWindowStatus] = useState<AddLoginStatus>('idle');
  const [officialWindowMessage, setOfficialWindowMessage] = useState('');
  const [officialInteraction, setOfficialInteraction] = useState<BrowserInteractionDescriptor | null>(null);
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
    auto_rate_enabled: false,
    pause_duration: 0,
    cookie_refresh_enabled: false,
    cookie_refresh_interval_minutes: DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES,
    proxy_enabled: false,
    proxy_server: '',
    proxy_username: '',
    proxy_password: '',
    proxy_region: '',
  });
  // 代理密码是否已保存过（已保存时占位符提示"留空不修改"）
  const [proxyPasswordSaved, setProxyPasswordSaved] = useState(false);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState<ProxyProbeResult | null>(null);

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
  // 高级回复策略（用户级、跨账号共享，归并自原「AI 专家客服」）
  const [replyStrategies, setReplyStrategies] = useState<ReplyStrategy[]>([]);
  const [replyStrategiesExpanded, setReplyStrategiesExpanded] = useState(false);
  const [replyStrategiesLoading, setReplyStrategiesLoading] = useState(false);
  const [replyStrategiesError, setReplyStrategiesError] = useState('');
  const [savingReplyStrategies, setSavingReplyStrategies] = useState(false);
  const replyStrategiesBaselineRef = useRef('');

  const loadSessionStatuses = async (
    targetAccounts: AccountDetail[] = accounts,
    signal?: AbortSignal,
  ): Promise<AccountSessionRefreshStatus[]> => {
    const results = await Promise.all(targetAccounts.map(async (account) => {
      try {
        return [account.id, await getAccountSessionStatus(account.id, signal)] as const;
      } catch {
        return null;
      }
    }));
    if (signal?.aborted) return [];
    const next = Object.fromEntries(results.filter((entry): entry is readonly [string, AccountSessionRefreshStatus] => Boolean(entry)));
    Object.entries(next).forEach(([accountId, status]) => {
      if (!ACTIVE_SESSION_REFRESH_STATES.has(status.state)) {
        manualRefreshFlightsRef.current.delete(accountId);
      }
    });
    if (Object.keys(next).length) {
      setSessionStatuses((current) => ({ ...current, ...next }));
    }
    return Object.values(next);
  };

  const clearQRPolling = () => {
    if (qrPollingRef.current) {
      clearInterval(qrPollingRef.current);
      qrPollingRef.current = null;
    }
    qrPollingInFlightRef.current = false;
  };

  const clearPasswordPolling = () => {
    if (passwordPollingRef.current) {
      clearInterval(passwordPollingRef.current);
      passwordPollingRef.current = null;
    }
  };

  const clearOfficialPolling = () => {
    if (officialPollingRef.current) {
      clearInterval(officialPollingRef.current);
      officialPollingRef.current = null;
    }
  };

  const clearExtensionPolling = () => {
    if (extensionPollingRef.current) {
      clearInterval(extensionPollingRef.current);
      extensionPollingRef.current = null;
    }
    extensionPollingInFlightRef.current = false;
  };

  const clearClientBrowserPolling = () => {
    if (clientBrowserPollingRef.current) {
      clearInterval(clientBrowserPollingRef.current);
      clientBrowserPollingRef.current = null;
    }
  };

  const sendClientBrowserCommand = <T,>(
    type: 'XMC_GET_DEVICE' | 'XMC_START_LOGIN' | 'XMC_CONFIRM_LOGIN' | 'XMC_CANCEL_LOGIN',
    payload: Record<string, unknown> = {},
  ): Promise<T> => new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID();
    const timer = window.setTimeout(() => {
      clientBrowserCommandRef.current.delete(requestId);
      reject(new ClientBrowserBridgeError(
        clientBrowserContentReadyRef.current ? 'extension_not_injected' : 'extension_missing',
        clientBrowserContentReadyRef.current
          ? '扩展已注入页面，但后台连接没有响应'
          : '当前浏览器没有安装登录连接扩展',
      ));
    }, 3500);
    clientBrowserCommandRef.current.set(requestId, {
      resolve: resolve as (value: unknown) => void,
      reject,
      timer,
    });
    window.postMessage({ type, requestId, ...payload }, window.location.origin);
  });

  const detectClientBrowser = async (): Promise<ClientBrowserDevicePublic | null> => {
    if (clientBrowserDetectionRef.current) return clientBrowserDetectionRef.current;
    const detection = (async () => {
      setClientBrowserConnection({
        state: 'detecting',
        title: '正在连接本机浏览器',
        detail: '登录按钮正在建立一次性浏览器握手。',
      });
      try {
        const device = await sendClientBrowserCommand<ClientBrowserDevicePublic>('XMC_GET_DEVICE');
        if (!device.extensionVersion || compareVersions(device.extensionVersion, CLIENT_BROWSER_EXTENSION_VERSION) < 0) {
          const actual = device.extensionVersion || '未知';
          clientBrowserErrorRef.current = `扩展版本过旧（当前 ${actual}，需要 ${CLIENT_BROWSER_EXTENSION_VERSION}），请安装新版后继续`;
          setClientBrowserDevice(null);
          setClientBrowserConnection({
            state: 'extension_outdated',
            title: '需要更新浏览器连接扩展',
            detail: `当前版本 ${actual}，需要 ${CLIENT_BROWSER_EXTENSION_VERSION}。更新后再次点击登录即可。`,
            extensionVersion: actual,
          });
          return null;
        }
        if (device.protocolVersion !== CLIENT_BROWSER_PROTOCOL_VERSION) {
          throw new ClientBrowserBridgeError('extension_not_injected', '扩展协议与当前页面不匹配，请刷新扩展和页面');
        }
        try {
          await registerClientBrowserDevice(device);
        } catch (error) {
          if (error instanceof ApiRequestError && error.status === 401) {
            clientBrowserErrorRef.current = '监控台登录已失效，请重新登录后再连接浏览器';
            setClientBrowserConnection({
              state: 'auth_expired',
              title: '监控台登录已失效',
              detail: clientBrowserErrorRef.current,
              extensionVersion: device.extensionVersion,
            });
          } else if (error instanceof ApiRequestError && (
            error.status === 409 || ['device_owner_mismatch', 'device_key_mismatch'].includes(error.code || '')
          )) {
            clientBrowserErrorRef.current = error.message || '设备注册冲突，请重新加载浏览器连接扩展';
            setClientBrowserConnection({
              state: 'device_registration_conflict',
              title: '浏览器设备注册冲突',
              detail: clientBrowserErrorRef.current,
              extensionVersion: device.extensionVersion,
            });
          } else {
            clientBrowserErrorRef.current = error instanceof Error ? error.message : '设备注册失败，请稍后重试';
            setClientBrowserConnection({
              state: 'device_registration_failed',
              title: '浏览器设备注册失败',
              detail: clientBrowserErrorRef.current,
              extensionVersion: device.extensionVersion,
            });
          }
          setClientBrowserDevice(null);
          return null;
        }
        setClientBrowserDevice(device);
        clientBrowserErrorRef.current = '';
        setClientBrowserConnection({
          state: 'connected',
          title: '本机浏览器已连接',
          detail: `已连接当前 ${device.browserFamily === 'edge' ? 'Edge' : 'Chrome'}，点击登录会直接打开官方页面。`,
          extensionVersion: device.extensionVersion,
        });
        return device;
      } catch (error) {
        const bridgeError = error instanceof ClientBrowserBridgeError ? error : null;
        const state = bridgeError?.code === 'device_initialization_failed'
          ? 'device_initialization_failed'
          : bridgeError?.code === 'extension_missing'
            ? 'extension_missing'
            : 'extension_not_injected';
        const title = state === 'device_initialization_failed'
          ? '浏览器设备初始化失败'
          : state === 'extension_missing'
            ? '首次使用需要安装浏览器连接扩展'
            : '浏览器连接需要刷新';
        const detail = state === 'extension_missing'
          ? '安装一次扩展后，登录按钮会直接打开本机官方页面。'
          : error instanceof Error ? error.message : '浏览器连接暂时不可用，请再次点击登录';
        clientBrowserErrorRef.current = detail;
        setClientBrowserDevice(null);
        setClientBrowserConnection({ state, title, detail });
        return null;
      }
    })();
    clientBrowserDetectionRef.current = detection;
    try {
      return await detection;
    } finally {
      if (clientBrowserDetectionRef.current === detection) clientBrowserDetectionRef.current = null;
    }
  };

  const ensureClientBrowserBridge = async () => {
    if (clientBrowserDevice) return clientBrowserDevice;
    const device = await detectClientBrowser();
    if (device) return device;
    return null;
  };

  const cancelActiveOfficialSession = async () => {
    const sessionId = activeOfficialSessionRef.current;
    if (!sessionId) return true;
    try {
      await cancelOfficialLoginSession(sessionId);
      if (activeOfficialSessionRef.current === sessionId) {
        activeOfficialSessionRef.current = '';
      }
      return true;
    } catch {
      return false;
    }
  };

  const cancelActiveClientBrowserSession = async () => {
    const sessionId = activeClientBrowserSessionRef.current;
    if (!sessionId) return true;
    activeClientBrowserSessionRef.current = '';
    clearClientBrowserPolling();
    try {
      await cancelClientBrowserLoginSession(sessionId);
      await sendClientBrowserCommand('XMC_CANCEL_LOGIN', { sessionId });
      return true;
    } catch {
      return false;
    }
  };

  const cancelOfficialSessionById = async (sessionId?: string) => {
    if (!sessionId) return;
    try {
      await cancelOfficialLoginSession(sessionId);
    } catch {
      // The session may already be terminal or expired.
    }
  };

  const closeAddModal = () => {
    // 关闭弹窗时主动结束正在进行的登录会话，避免“看似取消了、后台仍在轮询并占用浏览器”。
    void cancelActiveOfficialSession();
    void cancelActiveClientBrowserSession();
    const activeApiQRSessionId = qrSessionId && ACTIVE_API_QR_STATES.has(qrStatus) ? qrSessionId : '';
    if (activeApiQRSessionId) void cancelQRSessionById(activeApiQRSessionId, 'user_cancelled');
    finishAddFlow();
  };

  const finishAddFlow = () => {
    loginFlowGenerationRef.current += 1;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    clearExtensionPolling();
    clearClientBrowserPolling();
    activeOfficialSessionRef.current = '';
    setShowAddModal(false);
    setActiveAddMethod('qr');
    setQrEntryMode(null);
    setQrStatus('pending');
    setQrCodeUrl('');
    setQrSessionId('');
    setQrMessage('');
    setQrVerificationImage('');
    setQrInteraction(null);
    qrHadVerificationRef.current = false;
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationImage('');
    setPasswordInteraction(null);
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
    setOfficialInteraction(null);
    setOfficialWindowSubmitting(false);
    setShowAdvancedLogin(false);
    setExtensionPairing(null);
    setExtensionMessage('');
    setExtensionCopied(false);
    setExtensionBusy(false);
    setClientBrowserSession(null);
    activeClientBrowserSessionRef.current = '';
    setClientBrowserStep('idle');
    setRenewalSetup(null);
  };

  const openAddAccountModal = () => {
    setReauthReminderAccounts([]);
    setShowAddModal(true);
  };

  const resetPasswordStatus = () => {
    clearPasswordPolling();
    setPasswordStatus('idle');
    setPasswordMessage('');
    setPasswordVerificationImage('');
    setPasswordInteraction(null);
  };

  const resetManualCookieStatus = () => {
    setManualCookieStatus('idle');
    setManualCookieMessage('');
  };

  const getReachableVerificationImage = (imageUrl?: string | null, screenshotPath?: string | null) => {
    if (imageUrl) return imageUrl;
    if (!screenshotPath) return '';
    if (screenshotPath.startsWith('/')) {
      return screenshotPath;
    }
    return '';
  };

  const loadAccounts = async (): Promise<AccountDetail[] | null> => {
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
      return accountsWithAI;
    } catch (error) {
      console.error('Failed to load accounts:', error);
      return null;
    } finally {
      setLoading(false);
    }
  };

  const refreshAndConfirmAccount = async (
    accountId: string | undefined,
    flowGeneration: number,
  ): Promise<'confirmed' | 'missing' | 'refresh_failed' | 'stale'> => {
    const refreshedAccounts = await loadAccounts();
    if (flowGeneration !== loginFlowGenerationRef.current) return 'stale';
    if (!refreshedAccounts) return 'refresh_failed';
    if (!accountId || !refreshedAccounts.some((account) => String(account.id) === String(accountId))) {
      return 'missing';
    }
    return 'confirmed';
  };

  const accountConfirmationMessage = (result: 'missing' | 'refresh_failed') => (
    result === 'refresh_failed'
      ? '账号列表刷新失败，请保持此窗口并重试'
      : '账号保存结果尚未在列表中确认'
  );

  const setClientBrowserFlowMessage = (
    mode: 'qr' | 'sms' | 'password',
    state: AddLoginStatus,
    message: string,
  ) => {
    if (mode === 'qr') {
      setQrStatus(state === 'failed' ? 'error' : state);
      setQrMessage(message);
    } else if (mode === 'sms') {
      setOfficialWindowStatus(state);
      setOfficialWindowMessage(message);
    } else {
      setPasswordStatus(state);
      setPasswordMessage(message);
    }
  };

  const startClientBrowserPolling = (
    initial: ClientBrowserLoginSession,
    flowGeneration: number,
    device: ClientBrowserDevicePublic,
  ) => {
    clearClientBrowserPolling();
    clientBrowserPollingRef.current = setInterval(async () => {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      try {
        const current = await getClientBrowserLoginSession(initial.session_id);
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        setClientBrowserSession(current);
        if (current.state === 'awaiting_confirmation' && current.account_id) {
          clearClientBrowserPolling();
          setClientBrowserStep('awaiting_confirmation');
          setClientBrowserFlowMessage(current.mode, 'processing', '登录已验证，正在确认账号列表');
          const confirmation = await refreshAndConfirmAccount(current.account_id, flowGeneration);
          if (confirmation !== 'confirmed') {
            setClientBrowserFlowMessage(
              current.mode,
              'failed',
              confirmation === 'stale' ? '登录流程已切换' : accountConfirmationMessage(confirmation),
            );
            return;
          }
          await confirmClientBrowserLoginSession(current.session_id, current.account_id);
          await sendClientBrowserCommand('XMC_CONFIRM_LOGIN', {
            sessionId: current.session_id,
            accountId: current.account_id,
          });
          setClientBrowserStep('success');
          setClientBrowserFlowMessage(current.mode, 'success', '当前设备浏览器登录成功');
          if (current.mode === 'password') {
            clearClientBrowserPolling();
            setRenewalSetup({
              accountId: current.account_id,
              deviceId: device.deviceId,
              loginSessionId: current.session_id,
              username: '',
              password: '',
              authorized: false,
              busy: false,
              message: '',
            });
            setPasswordStatus('success');
            setPasswordMessage('登录已成功。保存密码用于自动续期是独立授权，可选择跳过。');
          } else {
            finishAddFlow();
          }
        } else if (current.state === 'failed' || current.state === 'expired' || current.state === 'cancelled') {
          clearClientBrowserPolling();
          setClientBrowserStep('retryable_error');
          setClientBrowserFlowMessage(current.mode, 'failed', current.message || '当前设备浏览器登录未完成');
        } else {
          setClientBrowserStep(
            current.state === 'validating'
              ? 'validating'
              : current.state === 'waiting_device' || current.state === 'waiting_user'
                ? 'waiting_user'
                : 'opening_browser',
          );
          setClientBrowserFlowMessage(current.mode, 'processing', current.message || '请在当前设备浏览器继续');
        }
      } catch (error) {
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        clearClientBrowserPolling();
        setClientBrowserStep('retryable_error');
        setClientBrowserFlowMessage(
          initial.mode,
          'failed',
          error instanceof Error ? error.message : '当前设备登录状态检查失败',
        );
      }
    }, 1500);
  };

  const startExtensionClientBrowserLogin = async (mode: 'qr' | 'sms' | 'password') => {
    if (clientBrowserStartInFlightRef.current) return;
    clientBrowserStartInFlightRef.current = true;
    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    let session: ClientBrowserLoginSession | null = null;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    clearExtensionPolling();
    clearClientBrowserPolling();
    if (mode === 'qr') {
      setActiveAddMethod('qr');
      setQrEntryMode('client');
    } else {
      setActiveAddMethod(mode);
    }
    setClientBrowserDevice(null);
    setClientBrowserStep('opening_browser');
    setClientBrowserFlowMessage(mode, 'processing', '正在打开本机浏览器官方登录页');
    try {
      const previousSessionId = activeClientBrowserSessionRef.current;
      if (previousSessionId) {
        activeClientBrowserSessionRef.current = '';
        await cancelClientBrowserLoginSession(previousSessionId).catch(() => undefined);
        await sendClientBrowserCommand('XMC_CANCEL_LOGIN', { sessionId: previousSessionId }).catch(() => undefined);
      }
      const device = await ensureClientBrowserBridge();
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      if (!device) {
        setClientBrowserStep('retryable_error');
        setClientBrowserFlowMessage(mode, 'failed', clientBrowserErrorRef.current);
        setShowClientBrowserInstallGuide(true);
        return;
      }
      session = await createClientBrowserLoginSession(device.deviceId, mode, 'extension');
      activeClientBrowserSessionRef.current = session.session_id;
      if (flowGeneration !== loginFlowGenerationRef.current) {
        await cancelClientBrowserLoginSession(session.session_id);
        activeClientBrowserSessionRef.current = '';
        return;
      }
      setClientBrowserSession(session);
      setClientBrowserStep('opening_browser');
      await sendClientBrowserCommand('XMC_START_LOGIN', {
        sessionId: session.session_id,
        deviceId: session.device_id,
        mode: session.mode,
        expiresAt: session.expires_at,
      });
      setClientBrowserStep('waiting_user');
      setClientBrowserFlowMessage(mode, 'processing', '请在刚打开的当前设备浏览器中完成登录和全部验证');
      startClientBrowserPolling(session, flowGeneration, device);
    } catch (error) {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      if (session) {
        await cancelClientBrowserLoginSession(session.session_id).catch(() => undefined);
        activeClientBrowserSessionRef.current = '';
      }
      setClientBrowserStep('retryable_error');
      if (error instanceof ClientBrowserBridgeError && ['extension_missing', 'extension_outdated', 'extension_not_injected'].includes(error.code)) {
        setShowClientBrowserInstallGuide(true);
      }
      setClientBrowserFlowMessage(
        mode,
        'failed',
        error instanceof Error ? error.message : '当前设备浏览器登录启动失败',
      );
    } finally {
      clientBrowserStartInFlightRef.current = false;
    }
  };

  // 本机助手已彻底移除：陌生域名的“当前设备浏览器”通道由扩展承接；回环弹出
  // 服务端本机 Chrome，正式域名则在网页内嵌同一个服务端 Chrome 会话。
  const startClientBrowserLogin = (mode: 'qr' | 'sms' | 'password') => (
    startExtensionClientBrowserLogin(mode)
  );

  const saveRenewalBinding = async () => {
    if (!renewalSetup) return;
    if (!renewalSetup.authorized || !renewalSetup.username.trim() || !renewalSetup.password) {
      setRenewalSetup({ ...renewalSetup, message: '请填写账号和密码，并勾选明确授权' });
      return;
    }
    setRenewalSetup({ ...renewalSetup, busy: true, message: '' });
    try {
      await bindAccountRenewalDevice(renewalSetup.accountId, {
        login_session_id: renewalSetup.loginSessionId,
        device_id: renewalSetup.deviceId,
        username: renewalSetup.username.trim(),
        password: renewalSetup.password,
        authorized: true,
        authorized_at: Date.now() / 1000,
      });
      setRenewalSetup({ ...renewalSetup, password: '', busy: false, message: '已加密保存并绑定当前设备' });
      finishAddFlow();
    } catch (error) {
      setRenewalSetup({
        ...renewalSetup,
        password: '',
        busy: false,
        message: error instanceof Error ? error.message : '续期设备绑定失败',
      });
    }
  };

  useEffect(() => {
    const handleClientBrowserMessage = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== window.location.origin || !event.data) return;
      if (event.data.type === 'XMC_CLIENT_BROWSER_CONTENT_READY') {
        clientBrowserContentReadyRef.current = true;
        return;
      }
      if (event.data.type === 'XMC_CLIENT_BROWSER_RESULT') {
        const pending = clientBrowserCommandRef.current.get(String(event.data.requestId || ''));
        if (!pending) return;
        window.clearTimeout(pending.timer);
        clientBrowserCommandRef.current.delete(String(event.data.requestId || ''));
        if (event.data.response?.ok) pending.resolve(event.data.response.data);
        else pending.reject(new ClientBrowserBridgeError(
          String(event.data.response?.code || 'browser_command_failed'),
          event.data.response?.error || '当前设备浏览器命令失败',
        ));
        return;
      }
      if (event.data.type === 'XMC_CLIENT_BROWSER_PROGRESS') {
        setQrMessage(String(event.data.message || '请在当前设备浏览器继续'));
      }
    };
    window.addEventListener('message', handleClientBrowserMessage);
    return () => {
      window.removeEventListener('message', handleClientBrowserMessage);
      clientBrowserCommandRef.current.forEach((pending) => window.clearTimeout(pending.timer));
      clientBrowserCommandRef.current.clear();
      void cancelActiveClientBrowserSession();
    };
  }, []);

  useEffect(() => {
    void loadAccounts();
    return () => {
      loginFlowGenerationRef.current += 1;
      clearQRPolling();
      clearPasswordPolling();
      clearOfficialPolling();
      clearExtensionPolling();
      clearClientBrowserPolling();
      void cancelActiveOfficialSession();
      void cancelActiveClientBrowserSession();
    };
  }, []);

  const accountIds = accounts.map((account) => account.id).join('|');
  useEffect(() => {
    if (!accounts.length) return undefined;

    let stopped = false;
    let inFlight = false;
    let restartWhenVisible = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;

    const clearScheduledPoll = () => {
      if (timer === null) return;
      window.clearTimeout(timer);
      timer = null;
    };
    const poll = async () => {
      if (stopped || inFlight || document.visibilityState !== 'visible') return;
      inFlight = true;
      controller = new AbortController();
      const statuses = await loadSessionStatuses(accounts, controller.signal);
      controller = null;
      inFlight = false;
      if (stopped || document.visibilityState !== 'visible') return;
      if (restartWhenVisible) {
        restartWhenVisible = false;
        void poll();
        return;
      }
      const interval = statuses.some((status) => ACTIVE_SESSION_REFRESH_STATES.has(status.state))
        ? ACTIVE_SESSION_POLL_INTERVAL_MS
        : STABLE_SESSION_POLL_INTERVAL_MS;
      timer = window.setTimeout(() => {
        timer = null;
        void poll();
      }, interval);
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') {
        clearScheduledPoll();
        controller?.abort();
        return;
      }
      if (inFlight) {
        restartWhenVisible = true;
        return;
      }
      void poll();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    if (document.visibilityState === 'visible') void poll();
    return () => {
      stopped = true;
      clearScheduledPoll();
      controller?.abort();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
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
    const confirmed = await confirmDialog({
      title: '删除账号',
      message: '确认删除该闲鱼账号吗？删除后将停止其消息监听。',
      confirmText: '删除',
      tone: 'danger',
    });
    if (confirmed) {
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
      auto_rate_enabled: Boolean(account.auto_rate_enabled),
      pause_duration: account.pause_duration || 0,
      cookie_refresh_enabled: account.cookie_refresh_enabled || false,
      cookie_refresh_interval_minutes: account.cookie_refresh_interval_minutes || DEFAULT_COOKIE_REFRESH_INTERVAL_MINUTES,
      proxy_enabled: Boolean(account.proxy_enabled),
      proxy_server: account.proxy_server || '',
      proxy_username: account.proxy_username || '',
      proxy_password: '',
      proxy_region: account.proxy_region || '',
    });
    setProxyPasswordSaved(Boolean(account.proxy_password_set));
    setProxyTestResult(null);
    setActiveModal('edit');
  };

  const openAIModal = async (account: AccountDetail) => {
    setEditingAccount(account);
    setAiSaveNotice(null);
    setReplyStrategies([]);
    setReplyStrategiesError('');
    setReplyStrategiesLoading(true);
    setReplyStrategiesExpanded(false);
    replyStrategiesBaselineRef.current = '';
    setSaving(true);
    try {
      const [settings, providerResult, strategyResult] = await Promise.all([
        getAccountAISettings(account.id),
        getAIProviders(),
        getAiReplyStrategies()
          .then((data) => ({ data, error: '' }))
          .catch((error) => ({
            data: [] as ReplyStrategy[],
            error: error instanceof Error ? error.message : '高级回复策略加载失败',
          })),
      ]);
      setAiProviders(providerResult.providers);
      setReplyStrategies(strategyResult.data);
      setReplyStrategiesError(strategyResult.error);
      const strategiesComplete = ['price', 'tech', 'default'].every((promptType) => (
        strategyResult.data.some((item) => item.prompt_type === promptType && item.content.trim())
      ));
      setReplyStrategiesExpanded(Boolean(strategyResult.error) || !strategiesComplete);
      replyStrategiesBaselineRef.current = JSON.stringify(
        strategyResult.data.map(({ prompt_type, content, enabled }) => ({ prompt_type, content, enabled })),
      );
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
      replyStrategiesBaselineRef.current = JSON.stringify([]);
      setReplyStrategiesError(e instanceof Error ? e.message : 'AI 设置加载失败');
      setReplyStrategiesExpanded(true);
    } finally {
      setReplyStrategiesLoading(false);
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
    loginFlowGenerationRef.current += 1;
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
      setQrEntryMode(null);
      setQrStatus('pending');
      setQrCodeUrl('');
      setQrSessionId('');
      setQrMessage('');
      setQrVerificationImage('');
      setQrInteraction(null);
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
    if (!canUseServerBrowser) return;
    try {
      const result = await showAccountSessionRefreshBrowser(account.id);
      setPageNotice({ tone: 'info', text: result.message || '已在本机显示闲鱼官方窗口' });
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '打开官方窗口失败' });
    }
  };

  const proxyConfigDirty = () => {
    if (!editingAccount) return false;
    return (
      editForm.proxy_enabled !== Boolean(editingAccount.proxy_enabled) ||
      editForm.proxy_server.trim() !== (editingAccount.proxy_server || '') ||
      editForm.proxy_username.trim() !== (editingAccount.proxy_username || '') ||
      editForm.proxy_region.trim() !== (editingAccount.proxy_region || '') ||
      editForm.proxy_password.length > 0
    );
  };

  // 测试前先落库当前代理配置（后端测试端点读的是已存配置），再实测出口 IP
  const handleTestProxy = async () => {
    if (!editingAccount) return;
    if (!editForm.proxy_server.trim()) {
      setProxyTestResult({ ok: false, ip: '', status: 'not_configured', error: '请先填写代理服务器地址' });
      return;
    }
    setProxyTesting(true);
    setProxyTestResult(null);
    try {
      if (proxyConfigDirty()) {
        await updateAccountProxy(editingAccount.id, {
          proxy_enabled: true,
          proxy_server: editForm.proxy_server.trim(),
          proxy_username: editForm.proxy_username.trim(),
          ...(editForm.proxy_password || !proxyPasswordSaved
            ? { proxy_password: editForm.proxy_password }
            : {}),
          proxy_region: editForm.proxy_region.trim(),
        });
        // 已落库：密码视为已保存，清空输入框避免重复提交明文
        setProxyPasswordSaved(true);
        setEditForm((prev) => ({ ...prev, proxy_password: '', proxy_enabled: true }));
      }
      const result = await testAccountProxy(editingAccount.id);
      setProxyTestResult(result.data);
      await loadAccounts();
    } catch (error) {
      setProxyTestResult({
        ok: false,
        ip: '',
        status: 'error',
        error: error instanceof Error ? error.message : '连通性测试失败',
      });
    } finally {
      setProxyTesting(false);
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

      if (editForm.auto_rate_enabled !== Boolean(editingAccount.auto_rate_enabled)) {
        promises.push(updateAccountAutoRate(editingAccount.id, editForm.auto_rate_enabled));
      }

      // 更新暂停时长
      if (editForm.pause_duration !== (editingAccount.pause_duration || 0)) {
        promises.push(updateAccountPauseDuration(editingAccount.id, editForm.pause_duration));
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

      if (proxyConfigDirty()) {
        promises.push(updateAccountProxy(editingAccount.id, {
          proxy_enabled: editForm.proxy_enabled,
          proxy_server: editForm.proxy_server.trim(),
          proxy_username: editForm.proxy_username.trim(),
          // 留空且已存过密码 => 不传，保留原密码
          ...(editForm.proxy_password || !proxyPasswordSaved
            ? { proxy_password: editForm.proxy_password }
            : {}),
          proxy_region: editForm.proxy_region.trim(),
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
    if (replyStrategiesDirty) {
      setReplyStrategiesExpanded(true);
      setAiSaveNotice({ tone: 'error', text: '请先保存或放弃高级回复策略的修改' });
      return;
    }
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

  const handleReplyStrategyChange = (promptType: ReplyStrategy['prompt_type'], content: string) => {
    setReplyStrategiesExpanded(true);
    setReplyStrategiesError('');
    setReplyStrategies((current) =>
      current.map((item) => (item.prompt_type === promptType ? { ...item, content } : item)),
    );
  };

  const handleReplyStrategyEnabledChange = (promptType: ReplyStrategy['prompt_type'], enabled: boolean) => {
    setReplyStrategiesExpanded(true);
    setReplyStrategiesError('');
    setReplyStrategies((current) => (
      current.map((item) => (item.prompt_type === promptType ? { ...item, enabled } : item))
    ));
  };

  const replyStrategiesDirty = JSON.stringify(
    replyStrategies.map(({ prompt_type, content, enabled }) => ({ prompt_type, content, enabled })),
  ) !== replyStrategiesBaselineRef.current;

  const closeAIModal = async () => {
    if (replyStrategiesDirty) {
      const confirmed = await confirmDialog({
        title: '放弃未保存修改',
        message: '高级回复策略有未保存修改，确定放弃并关闭吗？',
        confirmText: '放弃修改',
      });
      if (!confirmed) return;
    }
    setActiveModal(null);
  };

  const handleSaveReplyStrategies = async () => {
    const requiredTypes: ReplyStrategy['prompt_type'][] = ['price', 'tech', 'default'];
    if (!requiredTypes.every((promptType) => (
      replyStrategies.some((strategy) => strategy.prompt_type === promptType && strategy.content.trim())
    ))) {
      setReplyStrategiesExpanded(true);
      setReplyStrategiesError('议价、技术和默认三类策略内容均不能为空');
      setAiSaveNotice({ tone: 'error', text: '请补齐三类高级回复策略' });
      return;
    }
    setSavingReplyStrategies(true);
    setReplyStrategiesError('');
    try {
      const result = await updateAiReplyStrategies(replyStrategies);
      const savedStrategies = result.data || await getAiReplyStrategies();
      setReplyStrategies(savedStrategies);
      replyStrategiesBaselineRef.current = JSON.stringify(
        savedStrategies.map(({ prompt_type, content, enabled }) => ({ prompt_type, content, enabled })),
      );
      setReplyStrategiesExpanded(false);
      setAiSaveNotice({ tone: 'success', text: '三类高级回复策略已统一保存（所有账号共享）' });
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存回复策略失败';
      setReplyStrategiesError(message);
      setReplyStrategiesExpanded(true);
      setAiSaveNotice({ tone: 'error', text: message });
    } finally {
      setSavingReplyStrategies(false);
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

  const cancelQRSessionById = async (
    sessionId: string,
    endedBy: 'user_cancelled' | 'switched_method' | 'switched_to_extension',
  ) => {
    if (!sessionId) return true;
    try {
      await cancelQRLogin(sessionId, endedBy);
      return true;
    } catch {
      return false;
    }
  };

  const buildExtensionPairingBundle = (
    pairing: Awaited<ReturnType<typeof createBrowserExtensionPairing>>,
  ) => {
    if ((pairing.protocol_version || 1) < 2) {
      return {
        pairing_id: pairing.pairing_id,
        pairing_code: pairing.pairing_code,
      };
    }
    return {
      protocol_version: pairing.protocol_version,
      pairing_id: pairing.pairing_id,
      pairing_token: pairing.pairing_token,
      import_url: pairing.import_url,
      console_origin: pairing.console_origin,
      expires_at: pairing.expires_at,
    };
  };

  const startExtensionPairingPolling = (pairingId: string, flowGeneration: number) => {
    clearExtensionPolling();
    extensionPollingRef.current = setInterval(async () => {
      if (
        flowGeneration !== loginFlowGenerationRef.current
        || extensionPollingInFlightRef.current
      ) return;
      extensionPollingInFlightRef.current = true;
      try {
        const pairing = await getBrowserExtensionPairing(pairingId);
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        setExtensionPairing((current) => current ? { ...current, ...pairing } : pairing);
        setExtensionMessage(pairing.message || '等待扩展导入');
        if (pairing.status === 'success') {
          clearExtensionPolling();
          const confirmation = await refreshAndConfirmAccount(pairing.account_id, flowGeneration);
          if (confirmation === 'confirmed') {
            finishAddFlow();
          } else if (confirmation === 'missing' || confirmation === 'refresh_failed') {
            setExtensionMessage(accountConfirmationMessage(confirmation));
          }
        } else if (pairing.status === 'failed' || pairing.status === 'expired') {
          clearExtensionPolling();
        }
      } catch (error) {
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        clearExtensionPolling();
        setExtensionMessage(error instanceof Error ? error.message : '配对状态检查失败');
      } finally {
        extensionPollingInFlightRef.current = false;
      }
    }, 1500);
  };

  const createExtensionPairingForFlow = async (flowGeneration: number) => {
    clearExtensionPolling();
    setExtensionBusy(true);
    setExtensionPairing(null);
    setExtensionCopied(false);
    setExtensionMessage('正在创建一次性配对');
    try {
      const pairing = await createBrowserExtensionPairing();
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      setExtensionPairing(pairing);
      setExtensionMessage('配对已创建，请复制到 Chrome 扩展；五分钟内有效且只能使用一次。');
      startExtensionPairingPolling(pairing.pairing_id, flowGeneration);
    } catch (error) {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      setExtensionMessage(error instanceof Error ? error.message : '创建配对失败');
    } finally {
      if (flowGeneration === loginFlowGenerationRef.current) {
        setExtensionBusy(false);
      }
    }
  };

  const handleQRStatusResult = async (
    statusRes: Awaited<ReturnType<typeof checkQRLoginStatus>>,
    flowGeneration: number,
    verificationMessage?: string,
  ) => {
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    if (statusRes.status === 'success' || statusRes.status === 'already_processed') {
      clearQRPolling();
      setQrStatus('success');
      setQrMessage('登录成功，正在刷新账号列表');
      const confirmation = await refreshAndConfirmAccount(
        statusRes.account_info?.account_id,
        flowGeneration,
      );
      if (confirmation === 'confirmed') {
        finishAddFlow();
      } else if (confirmation === 'missing' || confirmation === 'refresh_failed') {
        setQrMessage(accountConfirmationMessage(confirmation));
      }
    } else if (statusRes.status === 'scanned' || statusRes.status === 'processing') {
      setQrStatus('scanned');
      setQrMessage(statusRes.message || '正在检查登录状态');
    } else if (statusRes.status === 'verification_required') {
      qrHadVerificationRef.current = true;
      setQrStatus('verification_required');
      const isMobileScanVerification = (
        statusRes.required_action === 'scan_image'
        || statusRes.verification_kind === 'mobile_scan'
      );
      const verificationImage = isMobileScanVerification
        ? getReachableVerificationImage(
          statusRes.verification_qr_code_url,
          statusRes.verification_screenshot_path,
        )
        : '';
      setQrVerificationImage(verificationImage);
      setQrInteraction(null);
      if (statusRes.verification_browser_status === 'failed') {
        clearQRPolling();
      }
      setQrMessage(
        verificationMessage ||
        statusRes.message ||
        (verificationImage
          ? '请按官方页面提示完成验证，系统会自动检测'
          : '正在识别闲鱼安全验证方式，请稍候')
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

  const startQRStatusPolling = (
    sessionId: string,
    flowGeneration: number,
    verificationMessage?: string,
  ) => {
    clearQRPolling();
    qrPollingRef.current = setInterval(async () => {
      if (flowGeneration !== loginFlowGenerationRef.current || qrPollingInFlightRef.current) return;
      qrPollingInFlightRef.current = true;
      try {
        const statusRes = await checkQRLoginStatus(sessionId);
        await handleQRStatusResult(statusRes, flowGeneration, verificationMessage);
      } catch (error) {
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        clearQRPolling();
        setQrStatus('error');
        setQrMessage(error instanceof Error ? error.message : '检查二维码状态失败，请重试');
      } finally {
        qrPollingInFlightRef.current = false;
      }
    }, 2000);
  };

  const startApiQRLogin = async () => {
    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    clearExtensionPolling();
    const previousQRSessionId = qrSessionId && ACTIVE_API_QR_STATES.has(qrStatus)
      ? qrSessionId
      : '';
    if (previousQRSessionId) {
      await cancelQRSessionById(previousQRSessionId, 'switched_method');
    }
    await cancelActiveOfficialSession();
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    setShowAddModal(true);
    setActiveAddMethod('qr');
    setQrEntryMode('api');
    setQrStatus('loading');
    setQrCodeUrl('');
    setQrSessionId('');
    setQrMessage('');
    setQrVerificationImage('');
    setQrInteraction(null);
    qrHadVerificationRef.current = false;
    try {
      const res = await generateQRLogin();
      if (flowGeneration !== loginFlowGenerationRef.current) {
        if (res.session_id) {
          await cancelQRSessionById(res.session_id, 'switched_method');
        }
        return;
      }
      if (res.success && res.qr_code_url && res.session_id) {
        setQrCodeUrl(res.qr_code_url);
        setQrSessionId(res.session_id);
        setQrStatus('waiting');
        setQrMessage('请打开闲鱼 APP 扫码并在手机上确认登录');
        startQRStatusPolling(res.session_id, flowGeneration);
      } else {
        setQrStatus('error');
        setQrMessage(res.message || '二维码生成失败，请重试');
      }
    } catch (e) {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      setQrStatus('error');
      setQrMessage(e instanceof Error ? e.message : '扫码登录请求失败，请重试');
    }
  };

  const handleCreateExtensionPairing = async () => {
    await createExtensionPairingForFlow(loginFlowGenerationRef.current);
  };

  const handleCopyExtensionPairing = async () => {
    if (!extensionPairing?.pairing_token && !extensionPairing?.pairing_code) return;
    const pairingBundle = JSON.stringify(buildExtensionPairingBundle(extensionPairing));
    try {
      await navigator.clipboard.writeText(pairingBundle);
      setExtensionCopied(true);
      setExtensionMessage('配对信息已复制，请打开扩展并粘贴。');
    } catch {
      setExtensionCopied(false);
      setExtensionMessage('浏览器未允许自动复制，请手动选择下方配对信息。');
    }
  };

  const handleCancelApiQRLogin = async () => {
    const sessionId = qrSessionId;
    if (!sessionId) return;
    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    clearQRPolling();
    setQrInteraction(null);
    setQrMessage('正在取消本次扫码');
    const cancelled = await cancelQRSessionById(sessionId, 'user_cancelled');
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    setQrStatus('error');
    if (cancelled) {
      setQrSessionId('');
      setQrMessage('本次扫码已取消');
    } else {
      setQrMessage('取消扫码失败，请重试');
    }
  };

  const handoffWebQRToClientBrowser = async () => {
    const sessionId = qrSessionId;
    clearQRPolling();
    setQrInteraction(null);
    if (sessionId) {
      const cancelled = await cancelQRSessionById(
        sessionId,
        canUseServerBrowser ? 'switched_method' : 'switched_to_extension',
      );
      if (!cancelled) {
        setQrStatus('error');
        setQrMessage('结束网页二维码会话失败，请重试');
        return;
      }
      setQrSessionId('');
    }
    // 回环弹本机窗口，正式域名显示云端画面；陌生域名继续走扩展桥接。
    if (canUseServerBrowser) {
      await startBrowserQRLogin();
    } else {
      await startClientBrowserLogin('qr');
    }
  };

  const handleAddMethodChange = async (method: AddLoginMethod) => {
    if (method === activeAddMethod) return;
    const activeApiQRSessionId = (
      activeAddMethod === 'qr'
      && qrEntryMode === 'api'
      && qrSessionId
      && ACTIVE_API_QR_STATES.has(qrStatus)
    ) ? qrSessionId : '';
    const hasActiveOfficialSession = Boolean(activeOfficialSessionRef.current);
    const hasActiveExtensionPairing = Boolean(
      activeAddMethod === 'extension'
      && extensionPairing
      && ACTIVE_EXTENSION_PAIRING_STATES.has(extensionPairing.status),
    );
    if (activeApiQRSessionId || hasActiveOfficialSession || hasActiveExtensionPairing) {
      const confirmed = await confirmDialog({
        title: '结束当前登录会话',
        message: '当前登录仍在进行，切换方式会结束本次会话。是否继续？',
        confirmText: '继续切换',
      });
      if (!confirmed) return;
    }

    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    clearExtensionPolling();
    if (activeApiQRSessionId) {
      const cancelled = await cancelQRSessionById(activeApiQRSessionId, 'switched_method');
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      if (!cancelled) {
        setQrStatus('error');
        setQrMessage('结束原扫码会话失败，请重试');
        return;
      }
    }
    if (hasActiveOfficialSession) {
      const cancelled = await cancelActiveOfficialSession();
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      if (!cancelled) {
        if (activeAddMethod === 'qr') {
          setQrStatus('error');
          setQrMessage('结束服务器 Chrome 会话失败，请重试');
        } else if (activeAddMethod === 'sms') {
          setOfficialWindowStatus('failed');
          setOfficialWindowMessage('结束验证码登录会话失败，请重试');
        } else {
          setPasswordStatus('failed');
          setPasswordMessage('结束账号密码登录会话失败，请重试');
        }
        return;
      }
    }
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    setActiveAddMethod(method);
    setQrInteraction(null);
    setOfficialInteraction(null);
    setPasswordInteraction(null);
    setExtensionCopied(false);
    if (method !== 'extension') {
      setExtensionPairing(null);
      setExtensionMessage('');
    }
    if (method === 'qr') {
      setQrEntryMode(null);
      setQrStatus('pending');
      setQrCodeUrl('');
      setQrSessionId('');
      setQrMessage('');
      setQrVerificationImage('');
      setQrInteraction(null);
      qrHadVerificationRef.current = false;
    }
  };

  const applyInteractiveOfficialStatus = async (
    mode: InteractiveOfficialLoginMode,
    status: Awaited<ReturnType<typeof getOfficialLoginSession>>,
    flowGeneration: number,
  ): Promise<boolean> => {
    if (flowGeneration !== loginFlowGenerationRef.current) return false;
    const interactionImage = status.verification_image_url || status.qr_image_url || '';
    const interaction = (
      (status.required_action === 'interact_in_console' || usesEmbeddedCloudBrowser)
      && status.interaction_supported
      && status.frame_revision
      && interactionImage
    )
      ? {
        imageUrl: interactionImage,
        frameRevision: status.frame_revision,
      }
      : null;
    if (mode === 'qr') setQrInteraction(interaction);
    else setOfficialInteraction(interaction);
    const activeStates = ['preparing', 'waiting_user', 'persisting', 'restarting_listener'];
    const terminalStates = ['failed', 'expired', 'cancelled', 'interrupted'];
    if (activeStates.includes(status.state)) {
      if (mode === 'qr') {
        setQrStatus('waiting');
        setQrMessage(status.message || (
          usesEmbeddedCloudBrowser
            ? '请扫描网页中的云端 Chrome 二维码'
            : '请在本机 Chrome 窗口内扫码'
        ));
        if (status.qr_image_url) setQrCodeUrl(status.qr_image_url);
      } else {
        setOfficialWindowStatus('processing');
        setOfficialWindowMessage(status.message || '请在监控台完成验证码登录');
      }
      return true;
    }
    if (status.state === 'verification_required') {
      if (mode === 'qr') {
        setQrStatus('verification_required');
        setQrMessage(status.message || (
          usesEmbeddedCloudBrowser
            ? '请在网页内完成云端 Chrome 身份验证'
            : '请在本机 Chrome 窗口完成身份验证'
        ));
        setQrVerificationImage(status.verification_image_url || '');
      } else {
        setOfficialWindowStatus('verification_required');
        setOfficialWindowMessage(status.message || '请在监控台完成身份验证');
      }
      return true;
    }
    if (status.state === 'success') {
      clearOfficialPolling();
      activeOfficialSessionRef.current = '';
      if (mode === 'qr') {
        setQrInteraction(null);
        setQrStatus('success');
        setQrMessage(status.message || `${serverBrowserLabel} 登录成功`);
      } else {
        setOfficialInteraction(null);
        setOfficialWindowStatus('success');
        setOfficialWindowMessage(status.message || '手机号验证码登录成功');
      }
      const confirmation = await refreshAndConfirmAccount(status.account_id, flowGeneration);
      if (confirmation === 'confirmed') {
        finishAddFlow();
      } else if (confirmation === 'missing' || confirmation === 'refresh_failed') {
        const message = accountConfirmationMessage(confirmation);
        if (mode === 'qr') setQrMessage(message);
        else setOfficialWindowMessage(message);
      }
      return false;
    }
    if (terminalStates.includes(status.state)) {
      clearOfficialPolling();
      activeOfficialSessionRef.current = '';
      if (mode === 'qr') {
        setQrInteraction(null);
        setQrStatus('error');
        setQrMessage(status.message || `${serverBrowserLabel} 登录未完成，请重新发起`);
      } else {
        setOfficialInteraction(null);
        setOfficialWindowStatus('failed');
        setOfficialWindowMessage(status.message || '手机号验证码登录未完成，请重新发起');
      }
      return false;
    }
    return false;
  };

  const startInteractiveOfficialPolling = (
    sessionId: string,
    mode: InteractiveOfficialLoginMode,
    flowGeneration: number,
  ) => {
    clearOfficialPolling();
    officialPollingRef.current = setInterval(async () => {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      try {
        const status = await getOfficialLoginSession(sessionId);
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        if (!await applyInteractiveOfficialStatus(mode, status, flowGeneration)) {
          clearOfficialPolling();
        }
      } catch (error) {
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        clearOfficialPolling();
        activeOfficialSessionRef.current = '';
        const message = error instanceof Error ? error.message : '官方登录状态检查失败';
        if (mode === 'qr') {
          setQrStatus('error');
          setQrMessage(message);
        } else {
          setOfficialWindowStatus('failed');
          setOfficialWindowMessage(message);
        }
      }
    }, 2500);
  };

  const startBrowserQRLogin = async () => {
    if (!canUseServerBrowser) {
      setQrStatus('error');
      setQrMessage('服务端 Chrome 登录仅在回环地址或正式控制台域名可用；请改用网页二维码或浏览器扩展');
      return;
    }
    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    clearExtensionPolling();
    const previousSessionCancelled = await cancelActiveOfficialSession();
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    if (!previousSessionCancelled) {
      setQrStatus('error');
      setQrMessage('结束已有服务器 Chrome 会话失败，请重试');
      return;
    }
    setShowAddModal(true);
    setActiveAddMethod('qr');
    setQrEntryMode('browser');
    setQrStatus('loading');
    setQrCodeUrl('');
    setQrSessionId('');
    setQrMessage(
      usesEmbeddedCloudBrowser
        ? '正在网页内启动云端 Chrome'
        : '正在本机打开 Chrome 登录窗口',
    );
    setQrVerificationImage('');
    setQrInteraction(null);
    try {
      const result = await createOfficialLoginSession({
        mode: 'qr',
        show_browser: isLoopbackConsole,
      });
      if (flowGeneration !== loginFlowGenerationRef.current) {
        await cancelOfficialSessionById(result.session_id);
        return;
      }
      if (!result.success || !result.session_id) {
        setQrStatus('error');
        setQrMessage(result.message || `${serverBrowserLabel} 登录启动失败`);
        return;
      }
      activeOfficialSessionRef.current = result.session_id;
      setQrSessionId(result.session_id);
      if (await applyInteractiveOfficialStatus('qr', result, flowGeneration)) {
        startInteractiveOfficialPolling(result.session_id, 'qr', flowGeneration);
      }
    } catch (error) {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      setQrStatus('error');
      setQrMessage(error instanceof Error ? error.message : `${serverBrowserLabel} 登录请求失败`);
    }
  };

  const returnToQRChooser = async () => {
    const activeApiQRSessionId = qrSessionId && ACTIVE_API_QR_STATES.has(qrStatus)
      ? qrSessionId
      : '';
    const activeClientSessionId = qrEntryMode === 'client' ? activeClientBrowserSessionRef.current : '';
    if (activeApiQRSessionId || activeOfficialSessionRef.current || activeClientSessionId) {
      const confirmed = await confirmDialog({
        title: '结束当前登录会话',
        message: '返回扫码方式会结束本次登录会话。是否继续？',
        confirmText: '继续返回',
      });
      if (!confirmed) return;
    }
    loginFlowGenerationRef.current += 1;
    const flowGeneration = loginFlowGenerationRef.current;
    clearQRPolling();
    clearOfficialPolling();
    if (activeApiQRSessionId) {
      await cancelQRSessionById(activeApiQRSessionId, 'switched_method');
    }
    await cancelActiveOfficialSession();
    await cancelActiveClientBrowserSession();
    if (flowGeneration !== loginFlowGenerationRef.current) return;
    setQrEntryMode(null);
    setQrStatus('pending');
    setQrCodeUrl('');
    setQrSessionId('');
    setQrMessage('');
    setQrVerificationImage('');
    setQrInteraction(null);
  };

  const handleOfficialWindowLogin = async () => {
    setOfficialWindowSubmitting(true);
    try {
      // 回环弹本机窗口，正式域名显示云端画面；陌生域名走扩展。
      if (canUseServerBrowser) {
        await startBrowserQRLogin();
      } else {
        await startClientBrowserLogin('sms');
      }
    } finally {
      setOfficialWindowSubmitting(false);
    }
  };

  const startPasswordStatusPolling = (sessionId: string, flowGeneration: number) => {
    clearPasswordPolling();
    passwordPollingRef.current = setInterval(async () => {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      try {
        const statusRes = await getOfficialLoginSession(sessionId);
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        const interactionImage = (
          statusRes.verification_image_url
          || statusRes.qr_image_url
          || ''
        );
        setPasswordInteraction(
          statusRes.required_action === 'interact_in_console'
          && statusRes.interaction_supported
          && statusRes.frame_revision
          && interactionImage
            ? {
              imageUrl: interactionImage,
              frameRevision: statusRes.frame_revision,
            }
            : null,
        );
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
          setPasswordInteraction(null);
          setPasswordStatus('success');
          setPasswordMessage(statusRes.message || '账号密码登录成功，正在刷新账号列表');
          setPasswordForm((current) => ({ ...current, password: '', showPassword: false }));
          const confirmation = await refreshAndConfirmAccount(statusRes.account_id, flowGeneration);
          if (confirmation === 'confirmed') {
            finishAddFlow();
          } else if (confirmation === 'missing' || confirmation === 'refresh_failed') {
            setPasswordMessage(accountConfirmationMessage(confirmation));
          }
        } else if (
          statusRes.state === 'failed' ||
          statusRes.state === 'expired' ||
          statusRes.state === 'cancelled' ||
          statusRes.state === 'interrupted'
        ) {
          clearPasswordPolling();
          activeOfficialSessionRef.current = '';
          setPasswordInteraction(null);
          setPasswordStatus('failed');
          setPasswordMessage(statusRes.message || '账号密码登录失败');
        }
      } catch (error) {
        if (flowGeneration !== loginFlowGenerationRef.current) return;
        clearPasswordPolling();
        setPasswordStatus('failed');
        setPasswordMessage(error instanceof Error ? error.message : '检查账号密码登录状态失败');
      }
    }, 2500);
  };

  const handlePasswordLoginSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    resetPasswordStatus();
    setPasswordSubmitting(true);
    try {
      // 回环弹本机窗口，正式域名显示云端画面；陌生域名走扩展。
      if (canUseServerBrowser) {
        await startBrowserQRLogin();
      } else {
        await startClientBrowserLogin('password');
      }
    } finally {
      setPasswordSubmitting(false);
    }
  };

  const handleQRInteraction = async (action: BrowserInteractionAction) => {
    const sessionId = qrSessionId;
    if (!sessionId) throw new Error('登录会话已结束，请重新发起');
    if (qrEntryMode === 'api') {
      return interactWithQRLogin(sessionId, action);
    }
    return interactWithOfficialLogin(sessionId, action);
  };

  const handleOfficialInteraction = async (action: BrowserInteractionAction) => {
    const sessionId = activeOfficialSessionRef.current;
    if (!sessionId) throw new Error('登录会话已结束，请重新发起');
    return interactWithOfficialLogin(sessionId, action);
  };

  const clientBrowserStepLabel = {
    idle: '等待开始',
    opening_browser: '正在打开官方页面',
    waiting_user: '等待你完成登录',
    validating: '正在验证登录态',
    awaiting_confirmation: '正在确认账号',
    success: '登录成功',
    retryable_error: '可以重新尝试',
  }[clientBrowserStep];

  const clientBrowserConnectionPanel = (allowWebQR = false) => (
    <div className={'rounded-xl border p-4 text-sm ' + (
      clientBrowserConnection.state === 'connected'
        ? 'border-green-200 bg-green-50 text-green-800'
        : clientBrowserConnection.state === 'detecting'
          ? 'border-blue-200 bg-blue-50 text-blue-800'
          : clientBrowserConnection.state === 'idle'
            ? 'border-gray-200 bg-gray-50 text-gray-700'
            : 'border-amber-200 bg-amber-50 text-gray-700'
    )} data-testid="client-browser-connection">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-bold">{clientBrowserConnection.title}</p>
        <span className="rounded-full bg-white/80 px-2.5 py-1 text-xs font-bold">{clientBrowserStepLabel}</span>
      </div>
      <p className="mt-1">{clientBrowserConnection.detail}</p>
      {!clientBrowserDevice && (
        <>
          <p className="mt-2 text-xs text-gray-500">当前设备浏览器登录由扩展承接：安装并启用扩展后，重新点击登录入口即可。</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setShowClientBrowserInstallGuide(true)} className="min-h-11 rounded-lg bg-gray-900 px-4 py-3 font-bold text-white">安装浏览器扩展</button>
            <button type="button" onClick={() => { setShowAdvancedLogin(true); setActiveAddMethod('extension'); }} className="min-h-11 rounded-lg border border-gray-300 bg-white px-4 py-3 font-bold text-gray-700">打开扩展导入入口</button>
            {allowWebQR && <button type="button" onClick={() => void startApiQRLogin()} className="min-h-11 rounded-lg border border-gray-300 bg-white px-4 font-bold">改用网页二维码</button>}
          </div>
        </>
      )}
    </div>
  );

  const handleShowOfficialBrowser = async () => {
    if (!canUseServerBrowser) return;
    const flowGeneration = loginFlowGenerationRef.current;
    const sessionId = activeOfficialSessionRef.current;
    if (!sessionId) return;
    try {
      const result = await showOfficialLoginBrowser(sessionId);
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      const message = result.message || '已在本机显示闲鱼官方窗口';
      if (activeAddMethod === 'qr') setQrMessage(message);
      else if (activeAddMethod === 'sms') setOfficialWindowMessage(message);
      else setPasswordMessage(message);
    } catch (error) {
      if (flowGeneration !== loginFlowGenerationRef.current) return;
      const message = error instanceof Error ? error.message : '打开官方窗口失败';
      if (activeAddMethod === 'qr') setQrMessage(message);
      else if (activeAddMethod === 'sms') setOfficialWindowMessage(message);
      else setPasswordMessage(message);
    }
  };

  const handleCancelOfficialLogin = async () => {
    loginFlowGenerationRef.current += 1;
    clearQRPolling();
    clearPasswordPolling();
    clearOfficialPolling();
    const cancelled = await cancelActiveOfficialSession();
    if (!cancelled) {
      if (activeAddMethod === 'qr') {
        setQrStatus('error');
        setQrMessage('取消服务器 Chrome 会话失败，请重试');
      } else if (activeAddMethod === 'sms') {
        setOfficialWindowStatus('failed');
        setOfficialWindowMessage('取消验证码登录失败，请重试');
      } else {
        setPasswordStatus('failed');
        setPasswordMessage('取消登录会话失败，请重试');
      }
      return;
    }
    if (activeAddMethod === 'qr') {
      setQrInteraction(null);
      setQrStatus('error');
      setQrMessage('登录会话已取消');
    } else if (activeAddMethod === 'sms') {
      setOfficialInteraction(null);
      setOfficialWindowStatus('failed');
      setOfficialWindowMessage('手机号验证码登录已取消');
    } else {
      setPasswordInteraction(null);
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
      const result = await addAccountCookie({ value });
      setManualCookieStatus('success');
      setManualCookieMessage('Cookie 已保存，正在刷新账号列表');
      setManualCookieForm({ value: '' });
      const flowGeneration = loginFlowGenerationRef.current;
      const confirmation = await refreshAndConfirmAccount(result.account_id, flowGeneration);
      if (confirmation === 'confirmed') {
        finishAddFlow();
      } else if (confirmation === 'missing' || confirmation === 'refresh_failed') {
        setManualCookieMessage(accountConfirmationMessage(confirmation));
      }
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
            onClick={openAddAccountModal}
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
                   <StatusBadge
                    state={account.auto_rate_enabled ? 'ready' : 'idle'}
                    label={account.auto_rate_enabled
                      ? `自动好评 已成功 ${account.auto_rate_success_count || 0} 条`
                      : '自动好评关闭'}
                   />
                   {(account.auto_rate_needs_reconcile_count || 0) > 0 && (
                    <StatusBadge state="warning" label={`评价待核对 ${account.auto_rate_needs_reconcile_count}`} />
                   )}
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
                   {(sessionStatus?.state === 'failed' || sessionStatus?.state === 'timeout') && <StatusBadge state={isRetryableSessionStatus(sessionStatus) ? 'warning' : 'error'} label={isRetryableSessionStatus(sessionStatus) ? '平台连接暂时异常' : 'Cookie 刷新失败'} />}
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
            <div className={`mt-5 rounded-2xl border p-4 ${sessionStatus.state === 'action_required' || sessionStatus.state === 'verification_required' || sessionStatus.state === 'manual_reauth_required' ? 'border-amber-200 bg-amber-50' : sessionStatus.state === 'refreshing' ? 'border-blue-200 bg-blue-50' : isRetryableSessionStatus(sessionStatus) ? 'border-amber-200 bg-amber-50' : 'border-red-200 bg-red-50'}`}>
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
                            : isRetryableSessionStatus(sessionStatus)
                              ? '平台连接暂时异常'
                              : 'Cookie 刷新未完成'}
                  </div>
                  <div className="mt-1 text-sm text-gray-700">{sessionStatusMessage(sessionStatus)}</div>
                  {sessionStatus.updated_at && <div className="mt-1 text-xs text-gray-500">更新于 {new Date(sessionStatus.updated_at * 1000).toLocaleTimeString()}</div>}
                </div>
                <div className="flex shrink-0 gap-2">
                  {canUseServerBrowser && sessionStatus.state === 'verification_required' && sessionStatus.browser_active && (
                    <button type="button" onClick={() => void handleCancelSessionRefresh(account)} className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-bold text-gray-700">取消</button>
                  )}
                  {canUseServerBrowser && sessionStatus.state === 'verification_required' && sessionStatus.browser_active && (
                    <button
                      type="button"
                      onClick={() => void handleShowAccountSessionBrowser(account)}
                      className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-bold text-gray-700"
                    >
                      <ExternalLink className="h-4 w-4" />
                      显示服务器运维窗口
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
                  <AuthenticatedImage src={sessionStatus.verification_image_url} alt="闲鱼身份验证" className="mx-auto max-h-[520px] w-auto max-w-full object-contain" />
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
                      <p className="text-sm text-gray-500 mt-1">
                        {canUseServerBrowser
                          ? usesEmbeddedCloudBrowser
                            ? '推荐用手机扫“网页二维码”登录；也可在本页直接操作“云端 Chrome”作为备选。'
                            : '推荐用手机扫“网页二维码”登录，零安装最稳定；也可用“本机 Chrome 登录”窗口作为备选。'
                          : '登录和安全验证在你当前的 Chrome 或 Edge 中完成。'}
                      </p>
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
                        <span>高级与运维方式</span>
                        <ChevronDown className={`h-4 w-4 transition-transform ${showAdvancedLogin ? 'rotate-180' : ''}`} />
                      </button>
                      {showAdvancedLogin && (
                        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                          <button
                            type="button"
                            onClick={() => void handleAddMethodChange('extension')}
                            className={`flex min-h-11 items-center justify-center gap-2 rounded-lg border px-3 text-sm font-bold ${activeAddMethod === 'extension' ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 text-gray-600'}`}
                          >
                            <Chrome className="h-4 w-4" /> 你的 Chrome
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

                    {activeAddMethod === 'qr' && qrEntryMode === null && (
                      <div className="space-y-4" data-testid="qr-chooser">
                        <div className="rounded-2xl border border-yellow-200 bg-yellow-50 p-4 text-left">
                          <div className="flex items-start gap-3">
                            <QrCode className="mt-0.5 h-5 w-5 shrink-0 text-yellow-700" />
                            <div>
	                              <h4 className="font-bold text-gray-900">选择扫码方式</h4>
	                              <p className="mt-1 text-sm leading-6 text-gray-600">
                                  {usesEmbeddedCloudBrowser
                                    ? '推荐用手机扫网页二维码；云端 Chrome 会直接显示在本页，可继续点击、拖动滑块、输入文字或按键。'
                                    : '推荐用手机扫网页二维码，零安装、最稳定，不受浏览器风控影响；本机 Chrome 登录作为备选（适合账号密码，遇滑块/人脸可能不稳）。'}
                                </p>
                            </div>
                          </div>
                        </div>
	                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <button
                            type="button"
                            aria-label="网页二维码"
                            onClick={() => void startApiQRLogin()}
                            className="ios-btn-primary flex min-h-11 items-start gap-3 rounded-2xl p-4 text-left"
                          >
                            <Smartphone className="mt-0.5 h-5 w-5 shrink-0" />
                            <span>
                              <span className="block font-bold">网页二维码（推荐）</span>
                              <span className="mt-1 block text-xs font-medium opacity-70">用手机闲鱼 App 扫一下即可，零安装、最稳定，不受浏览器风控影响</span>
                            </span>
                          </button>
                          <button
                            type="button"
                            aria-label={`${serverBrowserLabel} 登录`}
                            onClick={() => void (canUseServerBrowser ? startBrowserQRLogin() : startClientBrowserLogin('qr'))}
                            className="flex min-h-11 items-start gap-3 rounded-2xl border border-gray-200 bg-white p-4 text-left text-gray-900 transition-colors hover:bg-gray-50"
                          >
                            <Chrome className="mt-0.5 h-5 w-5 shrink-0 text-gray-600" />
                            <span>
                              <span className="block font-bold">{serverBrowserLabel} 登录（备选）</span>
                              <span className="mt-1 block text-xs font-medium text-gray-500">
                                {usesEmbeddedCloudBrowser
                                  ? '在本页显示云端 Chrome 官方登录页，扫码和全部验证都在当前网页处理'
                                  : canUseServerBrowser
                                  ? '在本机打开 Chrome 官方登录页；适合账号密码，遇滑块 / 人脸时可能不稳定'
                                  : '打开你电脑上的 Chrome 或 Edge，完成扫码和全部验证'}
                              </span>
                            </span>
                          </button>
                        </div>
                      </div>
                    )}

                    {activeAddMethod === 'qr' && qrEntryMode === 'api' && (
                      <div className="text-center">
                        <div className="relative mx-auto mb-4 flex h-[260px] w-full max-w-[420px] items-center justify-center overflow-hidden rounded-xl border border-gray-200 bg-[#F7F8FA] shadow-inner sm:mb-6 sm:h-[360px]">
                          {qrStatus === 'loading' && <Loader2 className="w-10 h-10 text-[#FFE815] animate-spin" />}
                          {qrStatus === 'waiting' && qrCodeUrl && <AuthenticatedImage src={qrCodeUrl} alt="闲鱼登录二维码" className="h-full w-full object-contain p-3" />}
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
                                <AuthenticatedImage src={qrVerificationImage} alt="闲鱼安全验证页面" className="w-full h-full object-contain p-2" />
                                <span className="absolute bottom-3 rounded-full bg-white/95 px-3 py-1 text-xs font-bold text-orange-600 shadow-sm">请按官方页面提示完成验证</span>
                              </div>
                            ) : (
                              <div className="absolute inset-0 bg-white/95 flex flex-col items-center justify-center text-orange-600 animate-fade-in p-6">
                                <Key className="w-10 h-10 mb-4" />
                                <span className="font-bold text-lg">需要安全验证</span>
                                <span className="text-xs text-gray-500 mt-2 text-center">请在当前设备 Chrome 或 Edge 完成后续验证。</span>
                              </div>
                            )
                          )}
                          {qrStatus === 'error' && (
                            <div className="flex flex-col items-center">
                              <span className="text-red-500 font-bold mb-2">获取失败</span>
                              <button onClick={() => void startApiQRLogin()} className="flex min-h-11 items-center gap-1 rounded-lg bg-gray-200 px-3 py-2 text-xs hover:bg-gray-300">
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
                          {qrStatus === 'verification_required' && !qrVerificationImage && (
                            <button
                              type="button"
                              onClick={() => void handoffWebQRToClientBrowser()}
                              className="ios-btn-primary inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-bold"
                            >
                              <Chrome className="h-4 w-4" />
                              {usesEmbeddedCloudBrowser
                                ? '在本页云端 Chrome 继续'
                                : canUseServerBrowser ? '在本机 Chrome 窗口继续' : '在当前设备浏览器继续'}
                            </button>
                          )}
	                          {qrSessionId && ACTIVE_API_QR_STATES.has(qrStatus) && (
	                            <button
	                              type="button"
	                              onClick={() => void handleCancelApiQRLogin()}
	                              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-red-200 bg-white px-4 py-2 text-sm font-bold text-red-600 hover:bg-red-50"
	                            >
	                              <X className="h-4 w-4" />
	                              取消本次扫码
	                            </button>
	                          )}
                          <button
                            type="button"
                            onClick={() => void startApiQRLogin()}
                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-gray-100 px-4 py-2 text-sm font-bold text-gray-700 transition-colors hover:bg-gray-200"
                          >
                            <RefreshCw className="w-4 h-4" />
                            重新生成二维码
                          </button>
                          <button
                            type="button"
                            onClick={() => void returnToQRChooser()}
                            className="inline-flex min-h-11 items-center justify-center rounded-lg px-4 py-2 text-sm font-bold text-gray-500 hover:bg-gray-50 hover:text-gray-900"
                          >
                            返回扫码方式
                          </button>
                        </div>
                        <p className="mt-4 rounded-xl bg-gray-50 py-2 text-xs font-medium text-gray-400">
                          {canUseServerBrowser
                            ? usesEmbeddedCloudBrowser
                              ? '手机扫码型验证继续显示图片；滑块、短信或其他交互验证会直接显示在本页云端 Chrome 中。'
                              : '手机扫码型验证继续显示图片；滑块、人脸、短信或其他交互验证会在本机自动弹出的 Chrome 窗口中完成。'
                            : '手机扫码型验证继续显示图片；滑块、人脸、短信或其他交互验证转到当前设备浏览器。'}
                        </p>
                      </div>
                    )}

                    {activeAddMethod === 'qr' && qrEntryMode === 'client' && (
                      <div className="space-y-4">
                        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                          <div className="flex items-start gap-3">
                            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
                            <div>
                              <h4 className="font-bold text-gray-900">当前设备浏览器登录</h4>
                              <p className="mt-1 text-sm leading-6 text-gray-600">请在刚打开的 Chrome 或 Edge 标签页扫码，并完成滑块、短信、人脸或其他官方验证。标签页会在账号落库并由本页确认后关闭。</p>
                            </div>
                          </div>
                        </div>
                        <div className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-bold ${
                          qrStatus === 'error' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'
                        }`}>
                          {['processing', 'waiting', 'loading'].includes(qrStatus) && <Loader2 className="inline h-4 w-4 animate-spin" />}
                          {qrMessage || '正在等待当前设备浏览器'}
                        </div>
                        {clientBrowserConnectionPanel(true)}
                        <button type="button" onClick={() => void returnToQRChooser()} className="min-h-11 w-full rounded-lg border border-gray-300 bg-white px-4 font-bold text-gray-700">返回扫码方式</button>
                      </div>
                    )}

                    {activeAddMethod === 'qr' && qrEntryMode === 'browser' && (
                      <div className="space-y-4">
                        <div className="rounded-2xl border border-yellow-200 bg-yellow-50 p-4">
                          <div className="flex items-start gap-3">
                            <Chrome className="mt-0.5 h-5 w-5 shrink-0 text-yellow-700" />
                            <div>
                              <h4 className="font-bold text-gray-900">
                                {usesEmbeddedCloudBrowser ? '云端 Chrome 登录' : '本机 Chrome 登录窗口'}
                              </h4>
                              <p className="mt-1 text-sm leading-6 text-gray-600">
                                {usesEmbeddedCloudBrowser
                                  ? '云端 Chrome 画面会持续显示在本页。请用闲鱼 App 扫码，或直接点击、拖动、输入文字和发送按键完成官方验证；账号落库后会话自动退出。'
                                  : 'Chrome 窗口会在运行服务的这台 Mac 上打开。请在该窗口用闲鱼 App 扫码（或在官方页切换短信 / 密码），并按提示完成滑块、人脸等验证；账号落库并确认后窗口会自动关闭。'}
                              </p>
                            </div>
                          </div>
                        </div>

                        {qrInteraction ? (
                          <BrowserInteractionSurface
                            imageUrl={qrInteraction.imageUrl}
                            frameRevision={qrInteraction.frameRevision}
                            onInteract={handleQRInteraction}
                          />
                        ) : (qrCodeUrl || qrVerificationImage) && (
                          <div className="flex max-h-[360px] min-h-[220px] items-center justify-center overflow-hidden rounded-2xl border border-gray-200 bg-gray-50 p-3">
                            <AuthenticatedImage
                              src={qrVerificationImage || qrCodeUrl}
                              alt={qrVerificationImage ? `${serverBrowserLabel} 闲鱼验证页面` : `${serverBrowserLabel} 闲鱼二维码`}
                              className="max-h-[330px] w-full object-contain"
                            />
                          </div>
                        )}

                        <div className={`rounded-2xl px-4 py-3 text-sm font-bold ${
                          qrStatus === 'success'
                            ? 'bg-green-50 text-green-700'
                            : qrStatus === 'error'
                              ? 'bg-red-50 text-red-700'
                              : qrStatus === 'verification_required'
                                ? 'bg-orange-50 text-orange-700'
                                : 'bg-blue-50 text-blue-700'
                        }`}>
                          {qrStatus === 'loading' && <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />}
                          {qrMessage || `正在准备${serverBrowserLabel}登录会话`}
                        </div>

                        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                          {ACTIVE_BROWSER_QR_VIEW_STATES.has(qrStatus) && (
                            <>
                              {isLoopbackConsole && (
                                <button
                                  type="button"
                                  onClick={() => void handleShowOfficialBrowser()}
                                  className="ios-btn-primary inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold"
                                >
                                  <ExternalLink className="h-4 w-4" />
                                  重新显示 Chrome 窗口
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => void handleCancelOfficialLogin()}
                                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-red-200 bg-white px-4 text-sm font-bold text-red-600 hover:bg-red-50"
                              >
                                <X className="h-4 w-4" />
                                {usesEmbeddedCloudBrowser ? '取消云端扫码' : '取消服务器扫码'}
                              </button>
                            </>
                          )}
                          {qrStatus === 'error' && (
                            <button
                              type="button"
                              onClick={() => void startBrowserQRLogin()}
                              className="ios-btn-primary inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold"
                            >
                              <RefreshCw className="h-4 w-4" />
                              {usesEmbeddedCloudBrowser ? '重新发起云端扫码' : '重新发起服务器扫码'}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void returnToQRChooser()}
                            className="inline-flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-bold text-gray-500 hover:bg-gray-50 hover:text-gray-900"
                          >
                            返回扫码方式
                          </button>
                        </div>
                      </div>
                    )}

                    {activeAddMethod === 'sms' && (
                      <div className="space-y-4">
                        <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                          <div className="flex items-start gap-3">
                            <Smartphone className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
                            <div>
                              <h4 className="font-bold text-gray-900">{usesEmbeddedCloudBrowser ? '在本页云端 Chrome 完成手机号验证码登录' : canUseServerBrowser ? '在本机 Chrome 窗口完成手机号验证码登录' : '在当前设备浏览器完成手机号验证码登录'}</h4>
                              <p className="mt-1 text-sm leading-6 text-gray-600">
                                {usesEmbeddedCloudBrowser
                                  ? '云端 Chrome 官方页面会显示在本页；手机号、验证码、滑块和后续验证都通过下方画面完成。'
                                  : canUseServerBrowser
                                  ? '会在运行服务的这台 Mac 上弹出 Chrome 官方登录页；手机号、验证码、滑块和后续验证都只在该窗口输入。'
                                  : '将在你的 Chrome 或 Edge 打开官方页面；手机号、验证码、滑块和后续验证都只在该页面输入。'}
                              </p>
                            </div>
                          </div>
                        </div>
                        {officialWindowMessage && (
                          <div className={`rounded-lg px-4 py-3 text-sm font-bold ${officialWindowStatus === 'success' ? 'bg-emerald-50 text-emerald-700' : officialWindowStatus === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
                            {officialWindowMessage}
                          </div>
                        )}
                        {officialInteraction && (
                          <BrowserInteractionSurface
                            imageUrl={officialInteraction.imageUrl}
                            frameRevision={officialInteraction.frameRevision}
                            onInteract={handleOfficialInteraction}
                          />
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
                              : <Smartphone className="h-4 w-4" />}
                            {['processing', 'verification_required'].includes(officialWindowStatus)
                              ? '等待登录完成'
                              : usesEmbeddedCloudBrowser ? '打开云端 Chrome' : canUseServerBrowser ? '打开本机 Chrome 登录窗口' : '在当前设备浏览器继续'}
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
	                              <h4 className="font-bold text-gray-900">从你的 Chrome 导入</h4>
	                              <p className="mt-1 text-sm leading-6 text-gray-600">
	                                扩展读取当前 Chrome 里已登录的闲鱼 Cookie（不必把闲鱼保持为当前标签页），并用五分钟一次性配对发送到监控台。
                              </p>
                            </div>
                          </div>
                        </div>

                        <div className="flex flex-wrap gap-3">
                          <a
                            href={CLIENT_BROWSER_EXTENSION_URL}
                            download
                            className="inline-flex min-h-11 items-center gap-2 rounded-full bg-gray-100 px-4 py-2 text-sm font-bold text-gray-700 hover:bg-gray-200"
                          >
                            <Upload className="h-4 w-4" />
                            下载扩展 {CLIENT_BROWSER_EXTENSION_VERSION}
                          </a>
                          <button
                            type="button"
                            onClick={handleCreateExtensionPairing}
                            disabled={extensionBusy}
                            className="inline-flex min-h-11 items-center gap-2 rounded-full bg-[#FFE815] px-4 py-2 text-sm font-bold text-gray-900 hover:bg-yellow-300 disabled:opacity-60"
                          >
                            {extensionBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Key className="h-4 w-4" />}
                            创建一次性配对
                          </button>
                          <button
                            type="button"
                            onClick={() => void startExtensionClientBrowserLogin('password')}
                            className="inline-flex min-h-11 items-center gap-2 rounded-full border border-gray-300 bg-white px-4 py-2 text-sm font-bold text-gray-700 hover:bg-gray-50"
                          >
                            <ExternalLink className="h-4 w-4" />
                            用扩展打开官方登录页
                          </button>
                        </div>

	                        {(extensionPairing?.pairing_token || extensionPairing?.pairing_code) && (
                          <div className="space-y-2">
                            <label className="block text-sm font-bold text-gray-700">复制到扩展的配对信息</label>
                            <div className="flex gap-2">
                              <textarea
                                readOnly
                                rows={3}
	                                value={JSON.stringify(buildExtensionPairingBundle(extensionPairing))}
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
                          <li>在你的 Chrome 登录闲鱼官网；导入时不必把闲鱼保持为当前标签页。</li>
                          <li>创建配对、复制到扩展，然后点击“导入到咸鱼监控台”。</li>
                        </ol>
                      </div>
                    )}

                    {activeAddMethod === 'password' && (
                      <form onSubmit={handlePasswordLoginSubmit} className="space-y-4">
                        <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
                          <div>
                            <h4 className="font-bold text-gray-900">{usesEmbeddedCloudBrowser ? '在本页云端 Chrome 完成账号密码登录' : canUseServerBrowser ? '在本机 Chrome 窗口完成账号密码登录' : '在当前设备浏览器完成账号密码登录'}</h4>
                            <p className="mt-1 text-sm text-gray-600">
                              {usesEmbeddedCloudBrowser
                                ? '云端 Chrome 官方页面会显示在本页；账号、密码、滑块和后续验证都通过下方画面完成。登录成功后不会自动保存密码。'
                                : canUseServerBrowser
                                ? '会在运行服务的这台 Mac 上弹出 Chrome 官方登录页；账号、密码、滑块和人脸验证只在该窗口输入。登录成功后不会自动保存密码。'
                                : '普通用户的账号、密码、滑块和人脸验证只在你的 Chrome 或 Edge 官方页面输入。登录成功后不会自动保存密码。'}
                            </p>
                          </div>
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
                        {renewalSetup && (
                          <div className="space-y-3 rounded-xl border border-cyan-200 bg-cyan-50 p-4">
                            <div>
                              <h4 className="font-bold text-gray-900">是否在此设备启用自动续期</h4>
                              <p className="mt-1 text-xs leading-5 text-gray-600">这是登录成功后的第二次独立授权。密码会加密保存，只通过 60 秒一次性加密任务发给这一台绑定设备。</p>
                            </div>
                            <input
                              value={renewalSetup.username}
                              onChange={(event) => setRenewalSetup({ ...renewalSetup, username: event.target.value })}
                              placeholder="闲鱼账号或手机号"
                              className="ios-input w-full rounded-lg px-3 py-2"
                            />
                            <input
                              type="password"
                              value={renewalSetup.password}
                              onChange={(event) => setRenewalSetup({ ...renewalSetup, password: event.target.value })}
                              placeholder="再次输入用于续期的密码"
                              className="ios-input w-full rounded-lg px-3 py-2"
                            />
                            <label className="flex items-start gap-2 text-sm text-gray-700">
                              <input
                                type="checkbox"
                                checked={renewalSetup.authorized}
                                onChange={(event) => setRenewalSetup({ ...renewalSetup, authorized: event.target.checked })}
                                className="mt-1"
                              />
                              <span>我明确授权加密保存该密码，并仅向当前绑定设备下发一次性续期任务。</span>
                            </label>
                            {renewalSetup.message && <p className="text-sm font-bold text-cyan-800">{renewalSetup.message}</p>}
                            <div className="flex gap-2">
                              <button type="button" onClick={() => void saveRenewalBinding()} disabled={renewalSetup.busy} className="min-h-11 flex-1 rounded-lg bg-gray-900 px-4 font-bold text-white disabled:opacity-60">保存并绑定</button>
                              <button type="button" onClick={finishAddFlow} className="min-h-11 rounded-lg border border-gray-300 bg-white px-4 font-bold text-gray-700">暂不保存</button>
                            </div>
                          </div>
                        )}
                        {passwordStatus === 'verification_required' && (
                          <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-800">
                            {usesEmbeddedCloudBrowser ? '请在本页云端 Chrome 画面完成官方验证。' : canUseServerBrowser ? '请在本机弹出的 Chrome 窗口完成官方验证。' : '请回到当前设备浏览器完成官方验证。'}
                          </p>
                        )}
                        {(passwordStatus === 'processing' || passwordStatus === 'verification_required') && (
                          <div className="flex flex-wrap gap-2">
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
                          {passwordSubmitting || passwordStatus === 'processing' ? '等待登录完成' : usesEmbeddedCloudBrowser ? '打开云端 Chrome' : canUseServerBrowser ? '打开本机 Chrome 登录窗口' : '在当前设备浏览器继续'}
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
                onClick={closeAIModal}
                className="min-h-11 min-w-11 p-2 rounded-xl hover:bg-gray-100 transition-colors flex-shrink-0 flex items-center justify-center"
                aria-label="关闭 AI 设置"
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

              {/* 卖家自动好评 */}
              <div className="flex items-center justify-between gap-4 p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900 flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-500" />
                    卖家自动好评
                  </div>
                  <div className="text-xs text-gray-500">仅处理开启后新增且平台显示可评价的订单，随机延迟 5–15 分钟</div>
                </div>
                <ToggleControl
                  checked={editForm.auto_rate_enabled}
                  onChange={(checked) => setEditForm({ ...editForm, auto_rate_enabled: checked })}
                  label="卖家自动好评"
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

              {/* 当前设备续期绑定 */}
              <div className="border-t border-gray-200 pt-6">
                <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <Key className="w-5 h-5 text-amber-500" />
                  当前设备续期
                </h3>
                <div className={`rounded-lg border p-4 ${editingAccount.auto_refresh_supported ? 'border-emerald-200 bg-emerald-50' : 'border-amber-200 bg-amber-50'}`}>
                  <p className="font-bold text-gray-900">{
                    editingAccount.has_l3_memory
                      ? '已建立浏览器登录记忆'
                      : editingAccount.auto_refresh_supported
                        ? '已绑定一个当前设备浏览器'
                        : '尚未绑定当前设备浏览器'
                  }</p>
                  <p className="mt-1 text-sm leading-6 text-gray-600">
                    {editingAccount.has_l3_memory
                      ? '扫码留下的浏览器记忆可用于免密自动续签，账号密码不会在此处展示或修改。'
                      : editingAccount.auto_refresh_supported
                        ? '续期凭据只会通过一次性加密任务发给绑定设备；账号密码不会在此处展示或修改。'
                        : '先完成一次扫码或账号密码登录以建立浏览器记忆；也可以在当前设备浏览器完成账号密码登录后授权绑定。'}
                  </p>
                  <button
                    type="button"
                    onClick={() => openReauthMethod({ ...editingAccount, reauth_action: 'password_login' })}
                    className="mt-3 min-h-11 rounded-lg bg-gray-900 px-4 text-sm font-bold text-white"
                  >
                    {editingAccount.auto_refresh_supported ? '重新登录并更换绑定' : '登录并绑定当前设备'}
                  </button>
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
                      <p className="mt-1 text-sm text-gray-600">扫码成功并留下浏览器记忆后，或在当前设备完成账号密码登录并绑定后，即可开启自动定时续期。</p>
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
                      <div className="text-xs text-gray-500">{editingAccount.auto_refresh_supported ? '关闭后仍可手动刷新，可降低频繁触发验证的概率。' : '当前还没有可用的浏览器登录记忆或续期绑定。'}</div>
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

                  <div className="border-t border-gray-100 pt-4 space-y-3">
                    <div className="flex items-center justify-between p-4 bg-indigo-50 rounded-xl">
                      <div className="pr-3">
                        <div className="font-bold text-gray-900 flex items-center gap-2">
                          <Globe className="w-4 h-4 text-indigo-600" />
                          住宅代理（专属出口 IP）
                        </div>
                        <div className="text-xs text-gray-500 mt-1">
                          为该账号绑定独立住宅 IP，可显著降低云端机房 IP 触发滑块的概率。每个账号建议用不同的出口 IP。
                        </div>
                      </div>
                      <ToggleControl
                        checked={editForm.proxy_enabled}
                        onChange={(checked) => setEditForm({ ...editForm, proxy_enabled: checked })}
                        label="启用住宅代理"
                      />
                    </div>

                    {editForm.proxy_enabled && (
                      <div className="space-y-3">
                        <div>
                          <label htmlFor="proxy-server" className="block text-sm font-bold text-gray-700 mb-2">
                            代理服务器
                          </label>
                          <input
                            id="proxy-server"
                            type="text"
                            value={editForm.proxy_server}
                            onChange={(e) => setEditForm({ ...editForm, proxy_server: e.target.value })}
                            placeholder="http://host:port（支持 http/https，不支持 socks5）"
                            className="w-full ios-input px-4 py-3 rounded-xl"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label htmlFor="proxy-username" className="block text-sm font-bold text-gray-700 mb-2">
                              账号
                            </label>
                            <input
                              id="proxy-username"
                              type="text"
                              autoComplete="off"
                              value={editForm.proxy_username}
                              onChange={(e) => setEditForm({ ...editForm, proxy_username: e.target.value })}
                              placeholder="代理用户名（可选）"
                              className="w-full ios-input px-4 py-3 rounded-xl"
                            />
                          </div>
                          <div>
                            <label htmlFor="proxy-password" className="block text-sm font-bold text-gray-700 mb-2">
                              密码
                            </label>
                            <input
                              id="proxy-password"
                              type="password"
                              autoComplete="new-password"
                              value={editForm.proxy_password}
                              onChange={(e) => setEditForm({ ...editForm, proxy_password: e.target.value })}
                              placeholder={proxyPasswordSaved ? '已保存（留空不修改）' : '代理密码（可选）'}
                              className="w-full ios-input px-4 py-3 rounded-xl"
                            />
                          </div>
                        </div>
                        <div>
                          <label htmlFor="proxy-region" className="block text-sm font-bold text-gray-700 mb-2">
                            归属地备注
                          </label>
                          <input
                            id="proxy-region"
                            type="text"
                            value={editForm.proxy_region}
                            onChange={(e) => setEditForm({ ...editForm, proxy_region: e.target.value })}
                            placeholder="如：上海·电信（仅用于自查，便于让指纹与归属地对齐）"
                            className="w-full ios-input px-4 py-3 rounded-xl"
                          />
                        </div>
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={handleTestProxy}
                            disabled={proxyTesting || !editForm.proxy_server.trim()}
                            className="min-h-11 rounded-xl bg-indigo-600 px-4 text-sm font-bold text-white flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {proxyTesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                            {proxyTesting ? '测试中...' : '保存并测试连通性'}
                          </button>
                          {editingAccount.proxy_last_ip && (
                            <span className="text-xs text-gray-500">
                              上次出口 IP：{editingAccount.proxy_last_ip}（{editingAccount.proxy_last_status || '未知'}）
                            </span>
                          )}
                        </div>
                        {proxyTestResult && (
                          <div
                            className={`rounded-xl px-4 py-3 text-sm ${proxyTestResult.ok ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700'}`}
                          >
                            {proxyTestResult.ok
                              ? `连通正常，出口 IP：${proxyTestResult.ip}`
                              : `连通失败：${proxyTestResult.error || proxyTestResult.status}`}
                          </div>
                        )}
                        <p className="text-xs text-gray-400">
                          代理密码加密存储，仅在登录/续期时于服务端解密使用；此处永不回显明文。
                        </p>
                      </div>
                    )}
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
                type="button"
                onClick={closeAIModal}
                className="flex min-h-11 min-w-11 flex-shrink-0 items-center justify-center rounded-xl hover:bg-gray-100 transition-colors"
                aria-label="关闭 AI 设置"
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

              {/* 该账号补充说明（账号级，仅影响当前账号的整体风格） */}
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">该账号补充说明（可选）</label>
                <textarea
                  value={aiSettings.custom_prompts}
                  onChange={(e) => setAiSettings({ ...aiSettings, custom_prompts: e.target.value })}
                  placeholder="仅对当前账号生效的风格补充...&#10;&#10;例如：回复时保持礼貌专业、使用简洁的语言、强调产品质量等"
                  className="w-full ios-input px-4 py-3 rounded-xl h-40 resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">只控制该账号整体风格，不覆盖商品事实；跨账号的议价/技术话术请用下方「高级回复策略」。</p>
              </div>

              {/* 高级回复策略（用户级、跨账号共享，归并自原「AI 专家客服」）*/}
              <div className="border border-gray-200 rounded-xl overflow-hidden">
                <button
                  type="button"
                  onClick={() => setReplyStrategiesExpanded((prev) => !prev)}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Bot className="w-4 h-4 text-purple-500" />
                    <span className="font-bold text-gray-900">高级回复策略</span>
                    <span className="text-xs font-bold text-purple-600 bg-purple-50 rounded-full px-2 py-0.5">所有账号共享</span>
                  </div>
                  {replyStrategiesExpanded
                    ? <ChevronUp className="w-4 h-4 text-gray-500" />
                    : <ChevronDown className="w-4 h-4 text-gray-500" />}
                </button>
                {replyStrategiesExpanded && (
                  <div className="px-4 py-4 space-y-4 border-t border-gray-200">
                    <p className="text-xs text-gray-500">
                      按买家意图（议价 / 技术 / 默认）路由的回复话术，对当前用户所有账号生效。优先级低于商品事实、硬性价格规则与商品训练规则。
                    </p>
                    {replyStrategiesLoading && <p className="text-xs text-blue-600">正在读取三类策略…</p>}
                    {replyStrategiesError && <InlineNotice tone="error">{replyStrategiesError}</InlineNotice>}
                    {!replyStrategiesLoading && replyStrategies.length === 0 && !replyStrategiesError && <p className="text-xs text-gray-400">暂无可配置策略。</p>}
                    {replyStrategies.map((strategy) => (
                      <div key={strategy.prompt_type} className="rounded-xl border border-gray-100 p-3">
                        <div className="flex items-center justify-between mb-2">
                          <span className="font-bold text-gray-900 text-sm">{strategy.title}</span>
                          <ToggleControl
                            checked={strategy.enabled}
                            onChange={(enabled) => handleReplyStrategyEnabledChange(strategy.prompt_type, enabled)}
                            label={`启用${strategy.title}`}
                            disabled={savingReplyStrategies}
                          />
                        </div>
                        <textarea
                          value={strategy.content}
                          onChange={(e) => handleReplyStrategyChange(strategy.prompt_type, e.target.value)}
                          className="w-full ios-input px-3 py-2 rounded-lg h-28 resize-none text-sm"
                          disabled={savingReplyStrategies}
                        />
                      </div>
                    ))}
                    {replyStrategies.length > 0 && (
                      <button
                        type="button"
                        onClick={() => void handleSaveReplyStrategies()}
                        disabled={savingReplyStrategies || !replyStrategiesDirty}
                        className="ios-btn-primary flex min-h-11 w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {savingReplyStrategies ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                        {savingReplyStrategies ? '保存中…' : '保存全部策略'}
                      </button>
                    )}
                  </div>
                )}
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
                  <li>• 按买家意图套用「高级回复策略」（议价 / 技术 / 默认）</li>
                  <li>• 账号补充说明只控制整体风格，不覆盖商品事实</li>
                </ul>
              </div>
              {aiSaveNotice && <InlineNotice tone={aiSaveNotice.tone}>{aiSaveNotice.text}</InlineNotice>}
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={closeAIModal}
                  className="flex-1 px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors"
                  disabled={saving || savingReplyStrategies}
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

      {showClientBrowserInstallGuide && createPortal(
        <div className="modal-overlay-centered" role="dialog" aria-modal="true" aria-label="安装浏览器扩展">
          <div className="modal-card-centered max-w-xl">
            <div className="modal-header">
              <div>
                <h3 className="text-lg font-bold text-gray-900">安装浏览器扩展</h3>
                <p className="mt-1 text-sm text-gray-500">版本 {CLIENT_BROWSER_EXTENSION_VERSION} · 安装在你自己的 Chrome 或 Edge</p>
              </div>
              <button type="button" onClick={() => setShowClientBrowserInstallGuide(false)} aria-label="关闭安装引导" className="icon-button">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="modal-body space-y-4">
              <ol className="list-decimal space-y-3 pl-5 text-sm leading-6 text-gray-700">
                <li>下载并解压扩展包。</li>
                <li>在 Chrome 打开 chrome://extensions（Edge 打开 edge://extensions），开启“开发者模式”，点击“加载已解压的扩展程序”并选择解压后的目录。</li>
                <li>回到此页面重新点击登录入口；扩展只把登录 Cookie 交给监控台，密码和验证码不经过本页面。</li>
              </ol>
              <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
                <p className="font-bold text-gray-900">当前状态：{clientBrowserConnection.title}</p>
                <p className="mt-1">{clientBrowserConnection.detail}</p>
              </div>
              <a href={CLIENT_BROWSER_EXTENSION_URL} download className="ios-btn-primary inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl px-4 font-bold">
                <Chrome className="h-4 w-4" />
                下载浏览器扩展 {CLIENT_BROWSER_EXTENSION_VERSION}
              </a>
            </div>
            <div className="modal-footer flex gap-3">
              <button type="button" onClick={() => setShowClientBrowserInstallGuide(false)} className="min-h-11 flex-1 rounded-xl border border-gray-300 bg-white px-4 font-bold">返回登录</button>
              <button type="button" onClick={() => setShowClientBrowserInstallGuide(false)} className="min-h-11 flex-1 rounded-xl bg-gray-900 px-4 font-bold text-white">已安装，继续</button>
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
