import { get, post, put, patch, del } from './request';
import {
  LoginResponse, AccountDetail, Order, PaginatedResponse,
  AdminStats, Card, SystemSettings, ApiResponse, OrderAnalytics,
  Item, AIReplySettings, ShippingRule, ReplyRule, DefaultReply,
  SkillMonitorTask, SkillMonitorResult, SkillAgentPrompt,
  SkillOpsHealth, SkillBrowserStatus, SkillDeliveryDiagnostics,
  AutoReplyDiagnostics, SettingsSectionKey, SettingsSummary, SkillCapability,
  AIProviderListResponse, AIProviderProfile
} from '../types';

// Auth
export const login = async (data: { username?: string; password?: string; email?: string; verification_code?: string }): Promise<LoginResponse> => {
  return post('/login', data);
};

export const verifyToken = async (): Promise<{ authenticated: boolean; user_id?: number; username?: string }> => {
  return get('/verify');
};

export const logout = async (): Promise<ApiResponse> => {
  return post('/logout', {});
};

export const changePassword = async (currentPassword: string, newPassword: string): Promise<ApiResponse> => {
  return post('/change-password', { current_password: currentPassword, new_password: newPassword });
};

// Accounts
export const getAccountDetails = async (): Promise<AccountDetail[]> => {
  const data = await get<any[]>('/cookies/details');
  // Map backend fields to UI fields if necessary
  return data.map(item => ({
    id: item.id,
    value: item.value,
    cookie: item.value,
    enabled: item.enabled,
    auto_confirm: item.auto_confirm,
    remark: item.remark,
    note: item.remark,
    pause_duration: item.pause_duration,
    username: item.username,
    login_password: item.login_password,
    show_browser: item.show_browser,
    nickname: item.remark || `Account ${item.id.substring(0,6)}`, // Fallback for UI
    avatar_url: `https://api.dicebear.com/7.x/avataaars/svg?seed=${item.id}`, // Placeholder avatar
    ai_enabled: false, // 需要从AI设置API获取
  }));
};

export type QRLoginStatus =
  | 'pending'
  | 'waiting'
  | 'scanned'
  | 'success'
  | 'expired'
  | 'cancelled'
  | 'verification_required'
  | 'processing'
  | 'already_processed'
  | 'not_found'
  | 'error';

export interface QRLoginStatusResponse {
  status: QRLoginStatus;
  session_id?: string;
  message?: string;
  verification_url?: string;
  verification_qr_code_url?: string;
  verification_screenshot_path?: string | null;
  verification_browser_status?: 'starting' | 'waiting' | 'success' | 'failed' | 'timeout' | 'cancelled' | null;
  account_info?: {
    account_id: string;
    is_new_account: boolean;
  };
}

export interface PasswordLoginStatusResponse {
  status: 'processing' | 'success' | 'failed' | 'verification_required' | 'not_found' | 'forbidden' | 'error';
  message?: string;
  error?: string;
  account_id?: string;
  is_new_account?: boolean;
  cookie_count?: number;
  verification_url?: string | null;
  screenshot_path?: string | null;
  qr_code_url?: string | null;
}

export const addAccountCookie = async (data: { id: string; value: string }): Promise<ApiResponse> => {
  return post('/cookies', data);
};

export const generateQRLogin = async (): Promise<{ success: boolean; session_id?: string; qr_code_url?: string; message?: string }> => {
  return post('/qr-login/generate');
};

export const checkQRLoginStatus = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return get(`/qr-login/check/${sessionId}`);
};

export const continueQRLoginAfterVerification = async (sessionId: string): Promise<QRLoginStatusResponse> => {
  return post(`/qr-login/continue/${sessionId}`, {});
};

export const passwordLogin = async (data: {
  account_id: string;
  account: string;
  password: string;
  show_browser?: boolean;
}): Promise<{ success: boolean; session_id?: string; status?: string; message?: string }> => {
  return post('/password-login', data);
};

export const checkPasswordLoginStatus = async (sessionId: string): Promise<PasswordLoginStatusResponse> => {
  return get(`/password-login/check/${sessionId}`);
};

export const updateAccountStatus = async (id: string, enabled: boolean): Promise<any> => {
  return put(`/cookies/${id}/status`, { enabled });
};

export const deleteAccount = async (id: string): Promise<any> => {
  return del(`/cookies/${id}`);
};

export const updateAccountRemark = async (id: string, remark: string): Promise<any> => {
  return put(`/cookies/${id}/remark`, { remark });
};

export const updateAccountAutoConfirm = async (id: string, autoConfirm: boolean): Promise<any> => {
  return put(`/cookies/${id}/auto-confirm`, { auto_confirm: autoConfirm });
};

export const updateAccountPauseDuration = async (id: string, pauseDuration: number): Promise<any> => {
  return put(`/cookies/${id}/pause-duration`, { pause_duration: pauseDuration });
};

export const updateAccountCookie = async (id: string, value: string): Promise<any> => {
  return put(`/cookies/${id}`, { id, value });
};

export const updateAccountLoginInfo = async (id: string, data: {
  username?: string;
  login_password?: string;
  show_browser?: boolean;
}): Promise<any> => {
  return put(`/cookies/${id}/login-info`, data);
};

export const getAllAISettings = async (): Promise<Record<string, AIReplySettings>> => {
  return get('/ai-reply-settings');
};

export const getAutoReplyDiagnostics = async (cookieId: string): Promise<AutoReplyDiagnostics> => {
  const res = await get<{ success: boolean; data: AutoReplyDiagnostics }>(`/api/diagnostics/auto-reply/${cookieId}`);
  return res.data;
};

// Orders
export const getOrders = async (
  cookieId?: string,
  status?: string,
  page: number = 1,
  pageSize: number = 20
): Promise<PaginatedResponse<Order>> => {
  const params: any = { page, page_size: pageSize };
  if (cookieId) params.cookie_id = cookieId;
  if (status && status !== 'all') params.status = status;

  const res = await get<any>('/api/orders', params);

  // Handle backend response variations
  const orders = res.orders || res.data || [];
  return {
    success: true,
    data: orders,
    total: res.total || orders.length,
    page: res.page || page,
    page_size: res.page_size || pageSize,
    total_pages: res.total_pages || 1
  };
};

export const getOrderDetail = async (orderId: string): Promise<{ success: boolean; data?: Order }> => {
  const result = await get<{ order?: Order; data?: Order }>(`/api/orders/${orderId}`);
  return {
    success: true,
    data: result.order || result.data
  };
};

export const updateOrder = async (orderId: string, data: Partial<Order>): Promise<ApiResponse> => {
  return put(`/api/orders/${orderId}`, data);
};

export const deleteOrder = async (orderId: string): Promise<ApiResponse> => {
  return del(`/api/orders/${orderId}`);
};

export const syncOrders = async (cookieId?: string, status?: string): Promise<any> => {
  const formData = new FormData();
  if (cookieId) formData.append('cookie_id', cookieId);
  if (status) formData.append('status', status);

  // 使用 fetch 来发送 FormData
  const token = localStorage.getItem('auth_token');
  const response = await fetch('/api/orders/refresh', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`
    },
    body: formData
  });
  return response.json();
};

export const syncSingleOrder = async (orderId: string): Promise<any> => {
  return post(`/api/orders/${orderId}/refresh`);
};

export const manualShipOrder = async (orderIds: string[], shipMode: 'status_only' | 'full_delivery', content?: string): Promise<any> => {
    return post('/api/orders/manual-ship', {
        order_ids: orderIds,
        ship_mode: shipMode,
        custom_content: content
    });
}

export const importOrders = async (data: Partial<Order>[] | FormData): Promise<any> => {
  const isFormData = data instanceof FormData;
  const response = await fetch('/api/orders/import', {
    method: 'POST',
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
    },
    body: isFormData ? data : JSON.stringify(data)
  });
  return response.json();
}

// Stats
export const getAdminStats = async (): Promise<AdminStats> => {
  return get('/admin/stats');
};

export const getOrderAnalytics = async (daysOrParams: number | {start_date: string; end_date: string} = 7): Promise<OrderAnalytics> => {
    let params: {start_date: string; end_date: string};

    if (typeof daysOrParams === 'number') {
        const endDate = new Date();
        const startDate = new Date();
        startDate.setDate(startDate.getDate() - daysOrParams);
        params = {
            start_date: startDate.toISOString().split('T')[0],
            end_date: endDate.toISOString().split('T')[0]
        };
    } else {
        params = daysOrParams;
    }

    return get('/analytics/orders', params);
}

export const getValidOrders = async (dateRange: {start_date: string; end_date: string}): Promise<Order[]> => {
    const res = await get<any>('/analytics/orders/valid', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date
    });
    return res.orders || [];
}

// Cards
export const getCards = async (): Promise<Card[]> => {
  const res = await get<any>('/cards');
  return Array.isArray(res) ? res : (res.cards || []);
};

export const createCard = async (data: Partial<Card>): Promise<{ id: number; message: string }> => {
  return post('/cards', data);
};

export const updateCard = async (cardId: string | number, data: Partial<Card>): Promise<ApiResponse> => {
  return put(`/cards/${cardId}`, data);
};

export const deleteCard = async (cardId: string | number): Promise<ApiResponse> => {
  return del(`/cards/${cardId}`);
};

export const getCardDetails = async (cardId: string | number): Promise<any> => {
  return get(`/cards/${cardId}`);
};

// Items
export const getItems = async (): Promise<Item[]> => {
    const res = await get<any>('/items');
    return Array.isArray(res) ? res : (res.items || []);
}

export const getItemsByCookie = async (cookieId: string): Promise<Item[]> => {
    const res = await get<any>(`/items/cookie/${cookieId}`);
    return Array.isArray(res) ? res : (res.items || []);
}

export const syncItemsFromAccount = async (cookieId: string): Promise<any> => {
    return post('/items/get-all-from-account', { cookie_id: cookieId });
}

export const deleteItem = async (cookieId: string, itemId: string): Promise<any> => {
    return del(`/items/${cookieId}/${itemId}`);
}

export const updateItem = async (cookieId: string, itemId: string, data: any): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}`, data);
}

export const updateItemMultiSpec = async (cookieId: string, itemId: string, enabled: boolean): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/multi-spec`, { is_multi_spec: enabled });
}

export const updateItemMultiQuantityDelivery = async (cookieId: string, itemId: string, enabled: boolean): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/multi-quantity-delivery`, { multi_quantity_delivery: enabled });
}

// Rules - 发货规则 (使用正确的后端API)
export const getShippingRules = async (): Promise<ShippingRule[]> => {
    const res = await get<any>('/delivery-rules');
    const rules = Array.isArray(res) ? res : (res.data || res.rules || []);
    // 转换后端数据格式到前端格式
    return rules.map((item: any) => ({
        id: String(item.id),
        name: item.description || item.keyword || '',
        item_keyword: item.keyword || '',
        card_group_id: item.card_id || 0,
        card_group_name: item.card_name || '',
        priority: item.delivery_count || 1,
        enabled: item.enabled || false
    }));
}

export const updateShippingRule = async (rule: Partial<ShippingRule>): Promise<any> => {
    const payload = {
        keyword: rule.item_keyword,
        card_id: rule.card_group_id,
        delivery_count: rule.priority,
        enabled: rule.enabled ?? true,
        description: rule.name
    };
    return rule.id ? put(`/delivery-rules/${rule.id}`, payload) : post('/delivery-rules', payload);
}

export const deleteShippingRule = async (id: string): Promise<any> => del(`/delivery-rules/${id}`);

// Rules - 关键词回复规则 (使用关键词API)
export const getReplyRules = async (cookieId?: string): Promise<ReplyRule[]> => {
    if (!cookieId) return [];
    const res = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(res) ? res : [];
    return keywords.map((item: any, index: number) => ({
        id: String(index),
        keyword: item.keyword || '',
        reply_content: item.reply || '',
        match_type: 'exact' as const,
        enabled: true
    }));
}

export const updateReplyRule = async (rule: Partial<ReplyRule>, cookieId: string): Promise<any> => {
    // 获取现有关键词
    const existing = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(existing) ? existing : [];

    // 更新或添加关键词
    if (rule.id) {
        const index = parseInt(rule.id);
        if (index >= 0 && index < keywords.length) {
            keywords[index] = {
                keyword: rule.keyword,
                reply: rule.reply_content,
                item_id: ''
            };
        }
    } else {
        keywords.push({
            keyword: rule.keyword,
            reply: rule.reply_content,
            item_id: ''
        });
    }

    return post(`/keywords-with-item-id/${cookieId}`, { keywords });
}

export const deleteReplyRule = async (id: string, cookieId: string): Promise<any> => {
    const existing = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(existing) ? existing : [];
    const index = parseInt(id);
    if (index >= 0 && index < keywords.length) {
        keywords.splice(index, 1);
    }
    return post(`/keywords-with-item-id/${cookieId}`, { keywords });
}

// Settings
export const getSystemSettings = async (): Promise<SystemSettings> => {
    return get<SystemSettings>('/system-settings');
};

export const getSettingsSummary = async (): Promise<SettingsSummary> => {
  const result = await get<{ success: boolean } & SettingsSummary>('/api/settings/summary');
  return result;
};

export const saveSettingsSection = async (
  section: SettingsSectionKey,
  settings: Partial<SystemSettings>,
  secretActions: Record<string, 'keep' | 'set' | 'clear'> = {},
): Promise<ApiResponse & SettingsSummary & { saved_at: string }> => {
  return put(`/api/settings/sections/${section}`, { settings, secret_actions: secretActions });
};

export const verifySettingsSection = async (
  section: 'ai' | 'smtp',
  settings: Partial<SystemSettings>,
  secretActions: Record<string, 'keep' | 'set' | 'clear'> = {},
): Promise<{ success: boolean; state: string; message: string }> => {
  return post(`/api/settings/verify/${section}`, { settings, secret_actions: secretActions });
};

export const updateSystemSettings = async (settings: Partial<SystemSettings>): Promise<ApiResponse> => {
    // API expects individual PUTs, but we'll loop in the service for convenience or assume bulk endpoint if updated
    // Based on docs 12.2, we iterate.
    const promises = Object.entries(settings).map(([key, value]) => {
         return put(`/system-settings/${key}`, { value: String(value) });
    });
    await Promise.all(promises);
    return { success: true, message: 'Settings saved' };
};

const CUSTOM_PROMPT_MARKER = '额外商品/回复规则：';

const decodeCustomPromptsForEditor = (value?: string): string => {
  const raw = value || '';
  if (!raw.trim()) return '';

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return raw;
    }

    const promptMap = parsed as Record<string, unknown>;
    const values = ['default', 'price', 'tech']
      .map((key) => promptMap[key])
      .filter((item): item is string => typeof item === 'string' && item.trim().length > 0);

    if (values.length === 0) return raw;

    const markerIndex = values[0].lastIndexOf(CUSTOM_PROMPT_MARKER);
    if (markerIndex >= 0) {
      const customText = values[0].slice(markerIndex + CUSTOM_PROMPT_MARKER.length).trim();
      if (customText) return customText;
    }

    if (values.every((item) => item === values[0])) {
      return values[0];
    }

    return raw;
  } catch {
    return raw;
  }
};

const encodeCustomPromptsForBackend = (value?: string): string => {
  const raw = (value || '').trim();
  if (!raw) return '';

  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      return raw;
    }
  } catch {
    // Plain text is the normal editor format.
  }

  return JSON.stringify({
    default: raw,
    price: raw,
    tech: raw,
  });
};

export const getAccountAISettings = async (cookieId: string): Promise<AIReplySettings> => {
    const settings = await get<AIReplySettings>(`/ai-reply-settings/${cookieId}`);
    return {
      ...settings,
      custom_prompts: decodeCustomPromptsForEditor(settings.custom_prompts),
    };
}

export const updateAccountAISettings = async (cookieId: string, settings: Partial<AIReplySettings>): Promise<ApiResponse> => {
  const payload = {
    ai_enabled: settings.ai_enabled ?? false,
    provider_profile_id: settings.provider_profile_id ?? null,
    model_name: settings.model_name ?? 'deepseek-v4-flash',
    api_key: settings.api_key ?? '',
    base_url: settings.base_url ?? 'https://api.deepseek.com',
    max_discount_percent: settings.max_discount_percent ?? 10,
    max_discount_amount: settings.max_discount_amount ?? 100,
    max_bargain_rounds: settings.max_bargain_rounds ?? 3,
    custom_prompts: encodeCustomPromptsForBackend(settings.custom_prompts),
    api_key_action: settings.api_key_action ?? 'keep',
    provider_test_token: settings.provider_test_token ?? '',
  };
  return put(`/ai-reply-settings/${cookieId}`, payload);
}

export const getAIProviders = async (): Promise<AIProviderListResponse> => get('/api/ai/providers');

export const createAIProvider = async (data: {
  name: string;
  provider_type: 'openai_compatible' | 'gemini';
  preset: string;
  base_url: string;
  api_key: string;
  default_model: string;
  is_default?: boolean;
}): Promise<AIProviderProfile> => post('/api/ai/providers', data);

export const updateAIProvider = async (id: number, data: Partial<{
  name: string;
  provider_type: 'openai_compatible' | 'gemini';
  preset: string;
  base_url: string;
  api_key: string;
  api_key_action: 'keep' | 'set' | 'clear';
  default_model: string;
  is_default: boolean;
}>): Promise<AIProviderProfile> => put(`/api/ai/providers/${id}`, data);

export const deleteAIProvider = async (id: number): Promise<ApiResponse> => del(`/api/ai/providers/${id}`);

export const refreshAIProviderModels = async (id: number): Promise<{ models: string[]; cached_at: number }> => (
  post(`/api/ai/providers/${id}/models/refresh`, {})
);

export const testAIProvider = async (id: number, modelName: string): Promise<{
  message: string;
  reply: string;
  test_token: string;
  model_name: string;
}> => post(`/api/ai/providers/${id}/test`, { model_name: modelName });

export const testAIConnection = async (cookieId: string, data?: {
  message?: string;
  item_title?: string;
  item_price?: number;
  item_desc?: string;
}): Promise<ApiResponse & { reply?: string }> => {
  const result = await post<{ success?: boolean; message?: string; reply?: string }>(`/ai-reply-test/${cookieId}`, {
    message: data?.message || '你好，这是一条测试消息',
    item_title: data?.item_title || '测试商品',
    item_price: data?.item_price ?? 100,
    item_desc: data?.item_desc || '这是一个测试商品',
  });
  if (result.reply) {
    return { success: true, message: `AI 回复: ${result.reply}`, reply: result.reply };
  }
  return { success: result.success ?? true, message: result.message || 'AI 连接测试成功' };
}

export interface AIReplyLabMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AIReplyLabResponse {
  session_id: string;
  reply: string;
  warnings: string[];
  history?: AIReplyLabMessage[];
}

export interface AITrainingRule {
  id?: number;
  item_id?: string;
  scope: 'global' | 'item';
  text: string;
  enabled?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface AITrainingRulesResponse {
  global_rules: AITrainingRule[];
  item_rules: AITrainingRule[];
}

export type AIKnowledgeStatus = 'confirmed' | 'pending';
export type AIKnowledgeSource = 'user' | 'item_detail' | 'ai';

export interface AIKnowledgeEntry {
  id?: string;
  text?: string;
  label?: string;
  amount?: string;
  note?: string;
  question?: string;
  answer?: string;
  source?: AIKnowledgeSource;
  status?: AIKnowledgeStatus;
}

export interface AIItemKnowledge {
  overview?: AIKnowledgeEntry;
  pricing: AIKnowledgeEntry[];
  process: AIKnowledgeEntry[];
  after_sales: AIKnowledgeEntry[];
  forbidden: AIKnowledgeEntry[];
  faqs: AIKnowledgeEntry[];
  notes: AIKnowledgeEntry[];
}

export interface AIItemKnowledgeProfile {
  cookie_id: string;
  item_id: string;
  draft: AIItemKnowledge | Record<string, never>;
  published: AIItemKnowledge | Record<string, never>;
  source_detail_hash: string;
  current_source_hash: string;
  source_changed: boolean;
  published_version: number;
  draft_updated_at?: string | null;
  published_at?: string | null;
  item: {
    item_id: string;
    title: string;
    price: string;
    detail: string;
    updated_at?: string;
  };
}

export interface AIItemKnowledgeVersion {
  version: number;
  profile: AIItemKnowledge;
  source_detail_hash: string;
  created_at: string;
}

export const sendAITrainingMessage = async (cookieId: string, data: {
  session_id?: string;
  message: string;
  item_id?: string;
  item_title?: string;
  item_price?: string | number;
  item_desc?: string;
  training_rules?: AITrainingRule[];
  prompt_override?: string;
}): Promise<AIReplyLabResponse> => {
  return post(`/ai-reply-lab/reply/${cookieId}`, data);
}

export const getAITrainingRules = async (cookieId: string, itemId: string): Promise<AITrainingRulesResponse> => {
  return get(`/ai-training-rules/${cookieId}?item_id=${encodeURIComponent(itemId)}`);
}

export const saveAITrainingRules = async (
  cookieId: string,
  itemId: string,
  trainingRules: AITrainingRule[]
): Promise<ApiResponse & { rules?: AITrainingRule[] }> => {
  return post(`/ai-training-rules/${cookieId}`, { item_id: itemId, training_rules: trainingRules });
}

export const deleteAITrainingRule = async (cookieId: string, ruleId: number): Promise<ApiResponse> => {
  return del(`/ai-training-rules/${cookieId}/${ruleId}`);
}

export const setAITrainingRuleEnabled = async (cookieId: string, ruleId: number, enabled: boolean): Promise<ApiResponse> => {
  return patch(`/ai-training-rules/${cookieId}/${ruleId}`, { enabled });
}

export const getAIItemKnowledge = async (cookieId: string, itemId: string): Promise<AIItemKnowledgeProfile> => {
  return get(`/ai-item-knowledge/${cookieId}/${itemId}`);
}

export const generateAIItemKnowledge = async (cookieId: string, itemId: string): Promise<{ message: string; draft: AIItemKnowledge; source_detail_hash: string }> => {
  return post(`/ai-item-knowledge/${cookieId}/${itemId}/generate`, {});
}

export const saveAIItemKnowledgeDraft = async (cookieId: string, itemId: string, profile: AIItemKnowledge): Promise<ApiResponse & AIItemKnowledgeProfile> => {
  return put(`/ai-item-knowledge/${cookieId}/${itemId}/draft`, { profile });
}

export const publishAIItemKnowledge = async (cookieId: string, itemId: string): Promise<ApiResponse & AIItemKnowledgeProfile & { version: number }> => {
  return post(`/ai-item-knowledge/${cookieId}/${itemId}/publish`, {});
}

export const getAIItemKnowledgeVersions = async (cookieId: string, itemId: string): Promise<{ versions: AIItemKnowledgeVersion[] }> => {
  return get(`/ai-item-knowledge/${cookieId}/${itemId}/versions`);
}

export const rollbackAIItemKnowledge = async (cookieId: string, itemId: string, version: number): Promise<ApiResponse & AIItemKnowledgeProfile & { version: number }> => {
  return post(`/ai-item-knowledge/${cookieId}/${itemId}/rollback/${version}`, {});
}

// Notification Channels
export const getNotificationChannels = async (): Promise<{ success: boolean; data?: any[] }> => {
  const result = await get<any[]>('/notification-channels');
  const channels = (result || []).map((item: any) => {
    let parsedConfig;
    try {
      parsedConfig = JSON.parse(item.config);
    } catch {
      parsedConfig = undefined;
    }
    return {
      id: String(item.id),
      name: item.name,
      type: item.type,
      config: parsedConfig,
      enabled: item.enabled,
      created_at: item.created_at,
      updated_at: item.updated_at,
    };
  });
  return { success: true, data: channels };
}

export const createNotificationChannel = async (data: { name: string; type: string; config: Record<string, unknown> }): Promise<ApiResponse> => {
  return post('/notification-channels', {
    ...data,
    config: JSON.stringify(data.config)
  });
}

export const updateNotificationChannel = async (channelId: string, data: { name?: string; config?: Record<string, unknown>; enabled?: boolean }): Promise<ApiResponse> => {
  const payload: Record<string, unknown> = { ...data };
  if ('config' in data) {
    payload.config = JSON.stringify(data.config);
  }
  return put(`/notification-channels/${channelId}`, payload);
}

export const deleteNotificationChannel = async (channelId: string): Promise<ApiResponse> => {
  return del(`/notification-channels/${channelId}`);
}

// Message Notifications
export const getMessageNotifications = async (): Promise<{ success: boolean; data?: any[] }> => {
  const result = await get<Record<string, any[]>>('/message-notifications');
  const notifications = [];
  for (const [cookieId, channelList] of Object.entries(result || {})) {
    if (Array.isArray(channelList)) {
      for (const item of channelList) {
        notifications.push({
          cookie_id: cookieId,
          channel_id: item.channel_id,
          channel_name: item.channel_name,
          enabled: item.enabled,
        });
      }
    }
  }
  return { success: true, data: notifications };
}

export const setMessageNotification = async (cookieId: string, channelId: number, enabled: boolean): Promise<ApiResponse> => {
  return post(`/message-notifications/${cookieId}`, { channel_id: channelId, enabled });
}

export const deleteMessageNotification = async (notificationId: string): Promise<ApiResponse> => {
  return del(`/message-notifications/${notificationId}`);
}

export const deleteAccountNotifications = async (cookieId: string): Promise<ApiResponse> => {
  return del(`/message-notifications/account/${cookieId}`);
}

// Skill Center
export const getSkillMonitorTasks = async (): Promise<SkillMonitorTask[]> => {
  const res = await get<{ success: boolean; data: SkillMonitorTask[] }>('/api/skills/monitor/tasks');
  return res.data || [];
};

export const createSkillMonitorTask = async (data: Partial<SkillMonitorTask>): Promise<{ success: boolean; id: number; message: string }> => {
  return post('/api/skills/monitor/tasks', data);
};

export const runSkillMonitorTask = async (taskId: number): Promise<{
  success: boolean;
  message: string;
  result_ids: number[];
  created_count: number;
  raw_count: number;
  source?: string;
  is_real_data?: boolean;
}> => {
  return post(`/api/skills/monitor/tasks/${taskId}/run`, {});
};

export const getSkillMonitorResults = async (taskId?: number): Promise<SkillMonitorResult[]> => {
  const res = await get<{ success: boolean; data: SkillMonitorResult[] }>('/api/skills/monitor/results', taskId ? { task_id: taskId } : undefined);
  return res.data || [];
};

export const getSkillAgentPrompts = async (): Promise<SkillAgentPrompt[]> => {
  const res = await get<{ success: boolean; data: SkillAgentPrompt[] }>('/api/skills/agent/prompts');
  return res.data || [];
};

export const updateSkillAgentPrompt = async (prompt: SkillAgentPrompt): Promise<ApiResponse> => {
  return put(`/api/skills/agent/prompts/${prompt.prompt_type}`, prompt);
};

export const testSkillAgentReply = async (data: {
  message: string;
  cookie_id?: string;
  item_id?: string;
}): Promise<{
  success: boolean;
  intent: string;
  expert: string;
  reply: string;
  used_prompt?: SkillAgentPrompt;
  cookie_id?: string;
  model_name?: string;
  base_url?: string;
  is_real_ai?: boolean;
}> => {
  return post('/api/skills/agent/test-reply', data);
};

export const getSkillCapabilities = async (): Promise<Record<string, SkillCapability>> => {
  const result = await get<{ success: boolean; data: Record<string, SkillCapability> }>('/api/skills/capabilities');
  return result.data || {};
};

export const getSkillOpsHealth = async (): Promise<SkillOpsHealth> => {
  const res = await get<{ success: boolean; data: SkillOpsHealth }>('/api/skills/ops/health');
  return res.data;
};

export const getSkillBrowserStatus = async (): Promise<SkillBrowserStatus> => {
  const res = await get<{ success: boolean; data: SkillBrowserStatus }>('/api/skills/ops/browser-status');
  return res.data;
};

export const getSkillDeliveryDiagnostics = async (): Promise<SkillDeliveryDiagnostics> => {
  const res = await get<{ success: boolean; data: SkillDeliveryDiagnostics }>('/api/skills/ops/delivery-diagnostics');
  return res.data;
};

// Default Reply
export const getDefaultReplies = async (): Promise<Record<string, DefaultReply>> => {
  const result = await get<Record<string, Partial<DefaultReply>>>('/default-replies');
  return Object.fromEntries(
    Object.entries(result || {}).map(([cookieId, reply]) => [
      cookieId,
      {
        cookie_id: reply.cookie_id || cookieId,
        enabled: reply.enabled ?? false,
        reply_content: reply.reply_content || '',
        reply_once: reply.reply_once ?? false,
        reply_image_url: reply.reply_image_url || ''
      }
    ])
  );
};

export const getDefaultReply = async (cookieId: string): Promise<DefaultReply> => {
  const result = await get<any>(`/api/default-reply/${cookieId}`);
  return {
    cookie_id: cookieId,
    enabled: result.enabled || false,
    reply_content: result.reply_content || '',
    reply_once: result.reply_once || false,
    reply_image_url: result.reply_image_url || ''
  };
};

export const updateDefaultReply = async (cookieId: string, data: Partial<DefaultReply>): Promise<ApiResponse> => {
  return put(`/api/default-reply/${cookieId}`, {
    enabled: data.enabled ?? false,
    reply_content: data.reply_content || '',
    reply_once: data.reply_once ?? false,
    reply_image_url: data.reply_image_url || ''
  });
};

export const deleteDefaultReply = async (cookieId: string): Promise<ApiResponse> => {
  return del(`/api/default-reply/${cookieId}`);
};

export const clearDefaultReplyRecords = async (cookieId: string): Promise<ApiResponse> => {
  return post(`/api/default-reply/${cookieId}/clear-records`, {});
};
