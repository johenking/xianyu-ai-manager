import { AIItemKnowledge, AIKnowledgeEntry } from '../services/api';

export const emptyItemKnowledge = (): AIItemKnowledge => ({
  overview: {},
  pricing: [],
  process: [],
  after_sales: [],
  forbidden: [],
  faqs: [],
  notes: [],
});

export const normalizeItemKnowledge = (value?: Partial<AIItemKnowledge> | Record<string, never>): AIItemKnowledge => ({
  overview: value?.overview || {},
  pricing: Array.isArray(value?.pricing) ? value.pricing : [],
  process: Array.isArray(value?.process) ? value.process : [],
  after_sales: Array.isArray(value?.after_sales) ? value.after_sales : [],
  forbidden: Array.isArray(value?.forbidden) ? value.forbidden : [],
  faqs: Array.isArray(value?.faqs) ? value.faqs : [],
  notes: Array.isArray(value?.notes) ? value.notes : [],
});

export const newKnowledgeEntry = (text = ''): AIKnowledgeEntry => ({
  id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  text,
  source: 'user',
  status: 'confirmed',
});

export const countPendingKnowledge = (knowledge: AIItemKnowledge): number => {
  const entries = [
    ...(knowledge.overview?.text ? [knowledge.overview] : []),
    ...knowledge.pricing,
    ...knowledge.process,
    ...knowledge.after_sales,
    ...knowledge.forbidden,
    ...knowledge.faqs,
    ...knowledge.notes,
  ];
  return entries.filter((entry) => entry.status === 'pending').length;
};

export const hasKnowledgeContent = (knowledge: AIItemKnowledge): boolean => (
  Boolean(knowledge.overview?.text?.trim()) ||
  knowledge.pricing.length + knowledge.process.length + knowledge.after_sales.length +
  knowledge.forbidden.length + knowledge.faqs.length + knowledge.notes.length > 0
);

// 商品档案状态：已发布 > 仅草稿 > 无档案（数据来自商品列表接口聚合字段）
export type ItemKnowledgeState = 'published' | 'draft' | 'none';

export const knowledgeStateOf = (item: {
  knowledge_has_draft?: boolean;
  knowledge_published_version?: number;
}): ItemKnowledgeState => {
  if ((item.knowledge_published_version ?? 0) > 0) return 'published';
  if (item.knowledge_has_draft) return 'draft';
  return 'none';
};
