import { del, get, patch, post, put } from '../request';
import type {
  AIProviderListResponse,
  AIProviderProfile,
  AIReplySettings,
  ApiResponse,
} from '../../types';

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
  rule_context?: AITrainingRuleContext;
  rule_audit?: AIRuleAudit;
  regenerated?: boolean;
  guarded_by_rule?: boolean;
  guard_reason?: string;
  guarded_rule_ids?: Array<number | string>;
  knowledge_source?: 'draft' | 'published' | 'none';
  knowledge_version?: number;
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
  context?: AITrainingRuleContext;
}

export interface AITrainingRuleContext {
  applied_rules: AITrainingRule[];
  excluded_rules: (AITrainingRule & { reason?: string })[];
  disabled_rules: (AITrainingRule & { reason?: string })[];
  applied_count: number;
  excluded_count: number;
  disabled_count: number;
  total_count: number;
}

export interface AIRuleAuditEntry {
  rule_id?: number | string;
  text: string;
  status: 'followed' | 'violated' | 'not_relevant' | 'unknown';
  reason?: string;
}

export interface AIRuleAudit {
  results: AIRuleAuditEntry[];
  violation_count: number;
  unknown_count: number;
  conflicts: string[];
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

export const generateAIItemKnowledge = async (
  cookieId: string,
  itemId: string,
  data: { overview: string; profile: AIItemKnowledge }
): Promise<{ message: string; draft: AIItemKnowledge; source_detail_hash: string }> => {
  return post(`/ai-item-knowledge/${cookieId}/${itemId}/generate`, data);
}

export const copyAIItemKnowledge = async (
  cookieId: string,
  sourceItemId: string,
  targetItemIds: string[],
  overwrite = false
): Promise<{
  message: string;
  copied_item_ids: string[];
  skipped_item_ids: string[];
  missing_item_ids: string[];
  source_kind?: 'draft' | 'published';
  copied_count?: number;
  skipped_count?: number;
  missing_count?: number;
  skipped_reasons?: Record<string, string>;
}> => post(`/ai-item-knowledge/${cookieId}/${sourceItemId}/copy`, {
  target_item_ids: targetItemIds,
  overwrite,
});

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
