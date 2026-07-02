import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Item } from '../types';
import {
  AIItemKnowledge,
  AIItemKnowledgeProfile,
  AIItemKnowledgeVersion,
  AIKnowledgeEntry,
  generateAIItemKnowledge,
  getAIItemKnowledge,
  getAIItemKnowledgeVersions,
  publishAIItemKnowledge,
  rollbackAIItemKnowledge,
  saveAIItemKnowledgeDraft,
} from '../services/api';
import {
  AlertTriangle, Bot, Check, ChevronDown, ChevronUp, Clock3,
  History, Loader2, Plus, RotateCcw, Save, Send, Trash2, X,
} from 'lucide-react';
import {
  countPendingKnowledge, emptyItemKnowledge, hasKnowledgeContent,
  newKnowledgeEntry, normalizeItemKnowledge,
} from '../utils/itemKnowledge';

type ListSection = 'pricing' | 'process' | 'after_sales' | 'forbidden' | 'faqs' | 'notes';

const SECTION_LABELS: Record<ListSection, string> = {
  pricing: '规格与价格',
  process: '操作流程',
  after_sales: '售后边界',
  forbidden: '禁止说法',
  faqs: '常见问答',
  notes: '其他补充',
};

interface ItemKnowledgeModalProps {
  item: Item;
  onClose: () => void;
  onTrain?: () => void;
}

const ItemKnowledgeModal: React.FC<ItemKnowledgeModalProps> = ({ item, onClose, onTrain }) => {
  const [profile, setProfile] = useState<AIItemKnowledgeProfile | null>(null);
  const [knowledge, setKnowledge] = useState<AIItemKnowledge>(emptyItemKnowledge());
  const [versions, setVersions] = useState<AIItemKnowledgeVersion[]>([]);
  const [showVersions, setShowVersions] = useState(false);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const pendingCount = useMemo(() => countPendingKnowledge(knowledge), [knowledge]);

  const loadProfile = async () => {
    setLoading(true);
    setError('');
    try {
      const result = await getAIItemKnowledge(item.cookie_id, item.item_id);
      setProfile(result);
      setKnowledge(normalizeItemKnowledge(result.draft));
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '商品知识档案加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadProfile(); }, [item.cookie_id, item.item_id]);

  const updateKnowledge = (updater: (current: AIItemKnowledge) => AIItemKnowledge) => {
    setKnowledge((current) => updater(current));
    setDirty(true);
    setMessage('');
  };

  const updateOverview = (text: string) => updateKnowledge((current) => ({
    ...current,
    overview: {
      ...(current.overview || {}),
      id: current.overview?.id || newKnowledgeEntry().id,
      text,
      source: current.overview?.source || 'user',
      status: current.overview?.status || 'confirmed',
    },
  }));

  const updateEntry = (section: ListSection, index: number, patch: Partial<AIKnowledgeEntry>) => {
    updateKnowledge((current) => ({
      ...current,
      [section]: current[section].map((entry, entryIndex) => entryIndex === index ? { ...entry, ...patch } : entry),
    }));
  };

  const addEntry = (section: ListSection) => {
    const entry = section === 'faqs'
      ? { ...newKnowledgeEntry(), text: undefined, question: '', answer: '' }
      : section === 'pricing'
        ? { ...newKnowledgeEntry(), text: '', label: '', amount: '' }
        : newKnowledgeEntry();
    updateKnowledge((current) => ({ ...current, [section]: [...current[section], entry] }));
  };

  const removeEntry = (section: ListSection, index: number) => {
    updateKnowledge((current) => ({
      ...current,
      [section]: current[section].filter((_, entryIndex) => entryIndex !== index),
    }));
  };

  const confirmAll = () => updateKnowledge((current) => {
    const confirmEntry = (entry: AIKnowledgeEntry) => ({ ...entry, status: 'confirmed' as const });
    return {
      ...current,
      overview: current.overview?.text ? confirmEntry(current.overview) : current.overview,
      pricing: current.pricing.map(confirmEntry),
      process: current.process.map(confirmEntry),
      after_sales: current.after_sales.map(confirmEntry),
      forbidden: current.forbidden.map(confirmEntry),
      faqs: current.faqs.map(confirmEntry),
      notes: current.notes.map(confirmEntry),
    };
  });

  const generateDraft = async () => {
    if (hasKnowledgeContent(knowledge) && !window.confirm('AI生成会替换当前未发布草稿，继续吗？')) return;
    setGenerating(true);
    setError('');
    setMessage('');
    try {
      const result = await generateAIItemKnowledge(item.cookie_id, item.item_id);
      setKnowledge(normalizeItemKnowledge(result.draft));
      setDirty(true);
      setMessage('AI草稿已生成，黄色内容需要确认');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AI草稿生成失败');
    } finally {
      setGenerating(false);
    }
  };

  const saveDraft = async () => {
    setSaving(true);
    setError('');
    try {
      const result = await saveAIItemKnowledgeDraft(item.cookie_id, item.item_id, knowledge);
      setProfile(result);
      setKnowledge(normalizeItemKnowledge(result.draft));
      setDirty(false);
      setMessage('草稿已保存，可进入训练测试');
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存草稿失败');
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (dirty) {
      setError('请先保存当前草稿，再发布到真实AI');
      return;
    }
    setPublishing(true);
    setError('');
    try {
      const result = await publishAIItemKnowledge(item.cookie_id, item.item_id);
      setProfile((current) => current ? { ...current, ...result } : current);
      setMessage(result.message || '知识档案已发布');
      if (showVersions) await loadVersions();
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布失败');
    } finally {
      setPublishing(false);
    }
  };

  const loadVersions = async () => {
    try {
      const result = await getAIItemKnowledgeVersions(item.cookie_id, item.item_id);
      setVersions(result.versions);
    } catch (err) {
      setError(err instanceof Error ? err.message : '版本记录加载失败');
    }
  };

  const toggleVersions = async () => {
    const next = !showVersions;
    setShowVersions(next);
    if (next) await loadVersions();
  };

  const rollback = async (version: number) => {
    if (!window.confirm(`确认回滚到第 ${version} 版并重新发布吗？`)) return;
    setPublishing(true);
    try {
      const result = await rollbackAIItemKnowledge(item.cookie_id, item.item_id, version);
      await loadProfile();
      await loadVersions();
      setMessage(result.message || '版本已回滚');
    } catch (err) {
      setError(err instanceof Error ? err.message : '回滚失败');
    } finally {
      setPublishing(false);
    }
  };

  const renderEntry = (section: ListSection, entry: AIKnowledgeEntry, index: number) => {
    const pending = entry.status === 'pending';
    return (
      <div key={entry.id || index} className={`border-l-4 px-3 py-3 bg-white ${pending ? 'border-yellow-400' : 'border-green-400'}`}>
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className={`text-[11px] font-bold ${pending ? 'text-yellow-700' : 'text-green-700'}`}>
            {pending ? 'AI推测 · 待确认' : entry.source === 'ai' ? 'AI生成 · 已确认' : '人工补充'}
          </span>
          <div className="flex items-center gap-1">
            {pending && <button onClick={() => updateEntry(section, index, { status: 'confirmed' })} className="p-1.5 text-green-600 hover:bg-green-50 rounded-md" title="确认"><Check className="w-4 h-4" /></button>}
            <button onClick={() => removeEntry(section, index)} className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-md" title="删除"><Trash2 className="w-4 h-4" /></button>
          </div>
        </div>
        {section === 'pricing' ? (
          <div className="grid grid-cols-[1fr_120px] gap-2">
            <input value={entry.label || ''} onChange={(event) => updateEntry(section, index, { label: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm" placeholder="规格，例如 Pro 5x" />
            <input value={entry.amount || ''} onChange={(event) => updateEntry(section, index, { amount: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm" placeholder="价格" />
            <textarea value={entry.text || entry.note || ''} onChange={(event) => updateEntry(section, index, { text: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm resize-none col-span-2 h-16" placeholder="价格说明或优惠边界" />
          </div>
        ) : section === 'faqs' ? (
          <div className="space-y-2">
            <input value={entry.question || ''} onChange={(event) => updateEntry(section, index, { question: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm w-full" placeholder="买家可能怎么问" />
            <textarea value={entry.answer || ''} onChange={(event) => updateEntry(section, index, { answer: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm resize-none w-full h-16" placeholder="建议回答" />
          </div>
        ) : (
          <textarea value={entry.text || ''} onChange={(event) => updateEntry(section, index, { text: event.target.value })} className="ios-input px-3 py-2 rounded-lg text-sm resize-none w-full h-16" placeholder={`补充${SECTION_LABELS[section]}`} />
        )}
      </div>
    );
  };

  return createPortal(
    <div className="modal-overlay-centered">
      <div className="modal-container" style={{ maxWidth: '1180px', width: '96vw', maxHeight: '94vh' }}>
        <div className="modal-header flex items-center justify-between w-full">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-xl font-extrabold text-gray-900">商品知识档案</h3>
              {profile?.published_version ? <span className="px-2 py-1 rounded-md bg-green-100 text-green-700 text-xs font-bold">已发布 v{profile.published_version}</span> : <span className="px-2 py-1 rounded-md bg-gray-100 text-gray-500 text-xs font-bold">未发布</span>}
            </div>
            <p className="text-sm text-gray-500 mt-1">{item.item_title || item.item_id}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100" title="关闭"><X className="w-5 h-5" /></button>
        </div>

        <div className="modal-body">
          {loading ? <div className="h-96 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-yellow-500" /></div> : (
            <div className="grid grid-cols-1 lg:grid-cols-[330px_1fr] gap-6">
              <aside className="lg:border-r lg:border-gray-200 lg:pr-6 space-y-5">
                <div>
                  <div className="text-xs font-bold text-gray-500 mb-2">闲鱼原始详情 · 只读</div>
                  <div className="border-l-4 border-gray-300 pl-3">
                    <div className="font-bold text-gray-900 text-sm">{profile?.item.title}</div>
                    <div className="text-sm text-red-500 font-bold mt-1">{profile?.item.price}</div>
                    <div className="text-xs text-gray-600 leading-6 mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap">{profile?.item.detail || '暂无商品详情'}</div>
                  </div>
                </div>
                {profile?.source_changed && <div className="border border-orange-200 bg-orange-50 rounded-lg px-3 py-3 text-xs text-orange-800 flex gap-2"><AlertTriangle className="w-4 h-4 shrink-0" />商品详情同步后发生变化，建议复核知识档案。</div>}
                <button onClick={generateDraft} disabled={generating} className="w-full px-4 py-3 rounded-lg bg-black text-white font-bold flex items-center justify-center gap-2 disabled:opacity-60">
                  {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Bot className="w-4 h-4" />}AI 生成结构化草稿
                </button>
                <div className="text-xs text-gray-500 leading-5">AI 生成的内容全部标为待确认，不会自动发布。</div>
                <button onClick={() => void toggleVersions()} className="w-full px-4 py-3 rounded-lg bg-gray-100 text-gray-700 font-bold flex items-center justify-between">
                  <span className="flex items-center gap-2"><History className="w-4 h-4" />版本记录</span>{showVersions ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>
                {showVersions && <div className="space-y-2 max-h-48 overflow-y-auto">{versions.map((version) => <div key={version.version} className="flex items-center justify-between border-b border-gray-100 py-2 text-xs"><div><b>v{version.version}</b><div className="text-gray-400 mt-1">{version.created_at}</div></div><button onClick={() => void rollback(version.version)} className="p-2 text-gray-500 hover:text-black" title="回滚"><RotateCcw className="w-4 h-4" /></button></div>)}{versions.length === 0 && <div className="text-xs text-gray-400">暂无发布记录</div>}</div>}
              </aside>

              <main className="space-y-6 min-w-0">
                <div className="flex items-center justify-between gap-3 border-b border-gray-200 pb-3">
                  <div><h4 className="font-bold text-gray-900">草稿档案</h4><p className="text-xs text-gray-500 mt-1">训练测试读取草稿，真实买家只读取已发布版本。</p></div>
                  {pendingCount > 0 && <button onClick={confirmAll} className="px-3 py-2 rounded-lg bg-yellow-100 text-yellow-800 text-xs font-bold flex items-center gap-1"><Check className="w-4 h-4" />确认全部 ({pendingCount})</button>}
                </div>

                <section>
                  <div className="flex items-center justify-between mb-2"><label className="text-sm font-bold text-gray-800">商品概况</label>{knowledge.overview?.status === 'pending' && <button onClick={() => updateKnowledge((current) => ({ ...current, overview: { ...current.overview, status: 'confirmed' } }))} className="text-xs font-bold text-yellow-700">确认AI概况</button>}</div>
                  <textarea value={knowledge.overview?.text || ''} onChange={(event) => updateOverview(event.target.value)} className={`ios-input w-full px-4 py-3 rounded-lg resize-none h-24 ${knowledge.overview?.status === 'pending' ? 'border-yellow-400 bg-yellow-50' : ''}`} placeholder="用自己的话描述这个商品大体是什么、适合谁、核心价值是什么..." />
                </section>

                {(Object.keys(SECTION_LABELS) as ListSection[]).map((section) => (
                  <section key={section} className="border-t border-gray-100 pt-4">
                    <div className="flex items-center justify-between mb-3"><h4 className="text-sm font-bold text-gray-800">{SECTION_LABELS[section]}</h4><button onClick={() => addEntry(section)} className="px-3 py-2 rounded-lg bg-gray-100 text-gray-700 text-xs font-bold flex items-center gap-1"><Plus className="w-3.5 h-3.5" />补充</button></div>
                    <div className="space-y-2">{knowledge[section].map((entry, index) => renderEntry(section, entry, index))}{knowledge[section].length === 0 && <div className="border border-dashed border-gray-200 rounded-lg px-3 py-4 text-xs text-gray-400">暂无内容，可手动补充或让 AI 生成草稿</div>}</div>
                  </section>
                ))}

                {error && <div className="border border-red-200 bg-red-50 rounded-lg px-4 py-3 text-sm font-bold text-red-800">{error}</div>}
                {message && <div className="border border-green-200 bg-green-50 rounded-lg px-4 py-3 text-sm font-bold text-green-800">{message}</div>}
              </main>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 w-full">
            <button onClick={onClose} className="px-4 py-3 rounded-lg bg-gray-100 text-gray-700 font-bold">关闭</button>
            <button onClick={saveDraft} disabled={saving || !dirty} className="px-4 py-3 rounded-lg bg-white border border-gray-200 text-gray-800 font-bold flex items-center justify-center gap-2 disabled:opacity-50">{saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}保存草稿</button>
            <button onClick={onTrain} disabled={!onTrain || dirty} className="px-4 py-3 rounded-lg bg-gray-900 text-white font-bold flex items-center justify-center gap-2 disabled:opacity-50"><Send className="w-4 h-4" />进入训练测试</button>
            <button onClick={() => void publish()} disabled={publishing || dirty || pendingCount > 0 || !hasKnowledgeContent(knowledge)} className="ios-btn-primary px-4 py-3 rounded-lg font-bold flex items-center justify-center gap-2 disabled:opacity-50">{publishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}发布到真实AI</button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ItemKnowledgeModal;
