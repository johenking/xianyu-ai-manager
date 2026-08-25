import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Code2,
  Eye,
  EyeOff,
  FileKey2,
  FileText,
  Image as ImageIcon,
  Library,
  Loader2,
  PackagePlus,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import type { Card, StockImportResult } from '../types';
import {
  createCard,
  deleteCard,
  getCards,
  importCardStock,
  updateCard,
  validateCardApi,
} from '../services/api';
import { resourceHealthOf } from './ui/DeliveryMode';

type ResourceType = Card['type'];
type StockFormat = 'lines' | 'txt' | 'csv';
type Notice = { tone: 'success' | 'error' | 'info'; text: string };

interface ResourceForm {
  name: string;
  type: ResourceType;
  description: string;
  delaySeconds: number;
  enabled: boolean;
  lowStockThreshold: number;
  textContent: string;
  initialStock: string;
  imageUrl: string;
  apiUrl: string;
  apiToken: string;
  apiTimeout: number;
  apiSpec: string;
  isMultiSpec: boolean;
  specName: string;
  specValue: string;
}

const emptyForm = (): ResourceForm => ({
  name: '',
  type: 'text',
  description: '',
  delaySeconds: 0,
  enabled: true,
  lowStockThreshold: 5,
  textContent: '',
  initialStock: '',
  imageUrl: '',
  apiUrl: '',
  apiToken: '',
  apiTimeout: 10,
  apiSpec: '{}',
  isMultiSpec: false,
  specName: '',
  specValue: '',
});

const formFromCard = (card: Card): ResourceForm => ({
  ...emptyForm(),
  name: card.name || '',
  type: card.type,
  description: card.description || '',
  delaySeconds: Number(card.delay_seconds || 0),
  enabled: card.enabled !== false,
  lowStockThreshold: Number(card.low_stock_threshold ?? 5),
  textContent: card.text_content || '',
  imageUrl: card.image_url || '',
  apiUrl: card.api_config?.url || '',
  apiTimeout: Number(card.api_config?.timeout || 10),
  apiSpec: JSON.stringify(card.api_config?.spec || {}, null, 2),
  isMultiSpec: Boolean(card.is_multi_spec),
  specName: card.spec_name || '',
  specValue: card.spec_value || '',
});

const typeMeta: Record<ResourceType, {
  label: string;
  short: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = {
  text: { label: '固定资料', short: '固定资料', description: '网盘链接、提取码和使用说明', icon: FileText },
  data: { label: '一次一密', short: '一次一密', description: '每个买家只领取一条独立内容', icon: FileKey2 },
  image: { label: '图片', short: '图片', description: '发送一张已配置的交付图片', icon: ImageIcon },
  api: { label: '幂等 API', short: 'API', description: '按稳定幂等键向供应方分配内容', icon: Code2 },
};

const stockInputStats = (content: string, format: StockFormat) => {
  const lines = content.split(/\r?\n/);
  const candidates = format === 'csv' ? lines.slice(1) : lines;
  const normalized = candidates
    .map((line) => format === 'csv' ? String(line.split(',')[0] || '').trim() : line.trim())
    .filter(Boolean);
  return {
    lines: normalized.length,
    localDuplicates: normalized.length - new Set(normalized).size,
  };
};

const resourcePreview = (card: Card) => {
  const stats = card.stats || card.stock_stats;
  if (card.type === 'data') return `可用 ${Number(stats?.available || 0)} 条 · 已用 ${Number(stats?.used || 0)} 条`;
  if (card.type === 'api') return card.api_validation_status === 'validated' ? '连接已验证 · Token 已遮罩' : '等待连接验证';
  if (card.type === 'image') return card.image_url ? '图片已配置' : '图片未配置';
  return card.text_content ? '固定内容已配置' : '内容未配置';
};

interface CardListProps {
  onChanged?: (cards: Card[]) => void;
}

const CardList: React.FC<CardListProps> = ({ onChanged }) => {
  const headingId = useId();
  const onChangedRef = useRef(onChanged);
  const listGeneration = useRef(0);
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [query, setQuery] = useState('');
  const [sheetMode, setSheetMode] = useState<'create' | 'edit' | null>(null);
  const [selectedCard, setSelectedCard] = useState<Card | null>(null);
  const [form, setForm] = useState<ResourceForm>(emptyForm);
  const [showSensitive, setShowSensitive] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [stockFormat, setStockFormat] = useState<StockFormat>('lines');
  const [stockContent, setStockContent] = useState('');
  const [stockImporting, setStockImporting] = useState(false);
  const [stockResult, setStockResult] = useState<StockImportResult | null>(null);

  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

  const refreshCards = useCallback(async (): Promise<Card[]> => {
    const generation = ++listGeneration.current;
    setLoading(true);
    try {
      const data = await getCards();
      if (listGeneration.current !== generation) return data;
      setCards(data);
      setListError(null);
      onChangedRef.current?.(data);
      return data;
    } catch (error) {
      if (listGeneration.current === generation) {
        setCards([]);
        setListError(error instanceof Error && error.message ? error.message : '资源库加载失败');
      }
      return [];
    } finally {
      if (listGeneration.current === generation) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshCards();
  }, [refreshCards]);

  useEffect(() => {
    if (!sheetMode) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving && !stockImporting && !validating) setSheetMode(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [sheetMode, saving, stockImporting, validating]);

  const visibleCards = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return cards;
    return cards.filter((card) => [card.name, card.description, typeMeta[card.type].label]
      .some((value) => String(value || '').toLowerCase().includes(normalized)));
  }, [cards, query]);

  const overview = useMemo(() => ({
    enabled: cards.filter((card) => card.enabled !== false).length,
    attention: cards.filter((card) => !resourceHealthOf(card).ready).length,
    bound: cards.reduce((total, card) => total + Number((card.stats || card.stock_stats)?.bound || 0), 0),
  }), [cards]);

  const openCreate = () => {
    setSelectedCard(null);
    setForm(emptyForm());
    setShowSensitive(true);
    setStockContent('');
    setStockResult(null);
    setNotice(null);
    setSheetMode('create');
  };

  const openEdit = (card: Card) => {
    setSelectedCard(card);
    setForm(formFromCard(card));
    setShowSensitive(false);
    setStockContent('');
    setStockResult(null);
    setNotice(null);
    setSheetMode('edit');
  };

  const parseApiSpec = () => {
    try {
      const value = JSON.parse(form.apiSpec || '{}');
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error();
      return value as Record<string, unknown>;
    } catch {
      throw new Error('API 规格必须是 JSON 对象');
    }
  };

  const validateForm = () => {
    if (!form.name.trim()) throw new Error('请输入资源名称');
    if (form.delaySeconds < 0) throw new Error('延时不能小于 0 秒');
    if (form.lowStockThreshold < 0) throw new Error('低库存阈值不能小于 0');
    if (form.isMultiSpec && (!form.specName.trim() || !form.specValue.trim())) {
      throw new Error('启用规格后请填写规格名称和值');
    }
    if (form.type === 'text' && !form.textContent.trim()) throw new Error('固定资料内容不能为空');
    if (form.type === 'data' && sheetMode === 'create' && !form.initialStock.trim()) throw new Error('请先填写初始库存');
    if (form.type === 'image' && !form.imageUrl.trim()) throw new Error('图片地址不能为空');
    if (form.type === 'api') {
      if (!form.apiUrl.trim().toLowerCase().startsWith('https://')) throw new Error('API 地址必须使用 HTTPS');
      if (sheetMode === 'create' && !form.apiToken.trim()) throw new Error('请输入 API Token');
      if (sheetMode === 'edit' && !selectedCard?.api_token_configured && !form.apiToken.trim()) throw new Error('请输入 API Token');
      parseApiSpec();
    }
  };

  const saveResource = async () => {
    setNotice(null);
    try {
      validateForm();
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '请检查表单' });
      return;
    }
    setSaving(true);
    try {
      const payload: Partial<Card> & { api_token?: string } = {
        name: form.name.trim(),
        description: form.description.trim(),
        delay_seconds: Number(form.delaySeconds || 0),
        enabled: form.enabled,
        low_stock_threshold: Number(form.lowStockThreshold || 0),
        is_multi_spec: form.isMultiSpec,
        spec_name: form.isMultiSpec ? form.specName.trim() : '',
        spec_value: form.isMultiSpec ? form.specValue.trim() : '',
      };
      if (sheetMode === 'create') payload.type = form.type;
      if (form.type === 'text') payload.text_content = form.textContent.trim();
      if (form.type === 'data' && sheetMode === 'create') payload.data_content = form.initialStock;
      if (form.type === 'image') payload.image_url = form.imageUrl.trim();
      if (form.type === 'api') {
        const previousSpec = selectedCard?.api_config?.spec || {};
        const nextSpec = parseApiSpec();
        const configChanged = sheetMode === 'create'
          || selectedCard?.api_config?.url !== form.apiUrl.trim()
          || Number(selectedCard?.api_config?.timeout || 10) !== Number(form.apiTimeout)
          || JSON.stringify(previousSpec) !== JSON.stringify(nextSpec)
          || Boolean(form.apiToken.trim());
        if (configChanged) {
          payload.api_config = {
            protocol: 'fulfillment_api_v1',
            url: form.apiUrl.trim(),
            method: 'POST',
            timeout: Number(form.apiTimeout || 10),
            spec: nextSpec,
          };
          if (form.apiToken.trim()) payload.api_token = form.apiToken.trim();
        }
      }

      let targetId = selectedCard?.id;
      if (sheetMode === 'create') {
        const created = await createCard(payload);
        targetId = created.id;
      } else if (selectedCard) {
        await updateCard(selectedCard.id, payload);
      }
      const nextCards = await refreshCards();
      const nextCard = nextCards.find((card) => card.id === targetId) || null;
      if (nextCard) {
        setSelectedCard(nextCard);
        setForm(formFromCard(nextCard));
        setShowSensitive(false);
        setSheetMode('edit');
      } else {
        setSheetMode(null);
      }
      setNotice({
        tone: 'success',
        text: form.type === 'api' && nextCard?.api_validation_status !== 'validated'
          ? '资源已保存，请完成连接验证后再绑定商品'
          : '资源已保存',
      });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '资源保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const toggleResource = async (card: Card) => {
    setNotice(null);
    try {
      await updateCard(card.id, { enabled: card.enabled === false });
      await refreshCards();
      setNotice({ tone: 'success', text: `资源已${card.enabled === false ? '启用' : '停用'}` });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '资源状态更新失败' });
    }
  };

  const removeResource = async () => {
    if (!selectedCard) return;
    if (!window.confirm('确认永久删除这个从未绑定、也没有履约历史的资源？')) return;
    setSaving(true);
    try {
      await deleteCard(selectedCard.id);
      setSheetMode(null);
      await refreshCards();
      setNotice({ tone: 'success', text: '资源已删除' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '资源删除失败' });
    } finally {
      setSaving(false);
    }
  };

  const validateConnection = async () => {
    if (!selectedCard || selectedCard.type !== 'api') return;
    if (form.apiToken && !form.apiToken.trim()) return;
    setValidating(true);
    setNotice(null);
    try {
      await validateCardApi(selectedCard.id, form.apiToken.trim() || undefined);
      const nextCards = await refreshCards();
      const refreshed = nextCards.find((card) => card.id === selectedCard.id) || selectedCard;
      setSelectedCard(refreshed);
      setForm(formFromCard(refreshed));
      setNotice({ tone: 'success', text: '连接验证通过，现在可以绑定商品' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '连接验证失败' });
      await refreshCards();
    } finally {
      setValidating(false);
    }
  };

  const importStock = async () => {
    if (!selectedCard || !stockContent.trim()) return;
    setStockImporting(true);
    setStockResult(null);
    setNotice(null);
    try {
      const result = await importCardStock(selectedCard.id, {
        format: stockFormat,
        content: stockContent,
      });
      setStockResult(result);
      setStockContent('');
      const nextCards = await refreshCards();
      const refreshed = nextCards.find((card) => card.id === selectedCard.id) || selectedCard;
      setSelectedCard(refreshed);
      setForm(formFromCard(refreshed));
      setNotice({ tone: 'success', text: `补货完成：新增 ${result.added} 条，重复 ${result.duplicates} 条` });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '补货失败' });
    } finally {
      setStockImporting(false);
    }
  };

  const stockPreview = stockInputStats(stockContent, stockFormat);
  const selectedStats = selectedCard?.stats || selectedCard?.stock_stats;
  const apiConfigurationDirty = Boolean(selectedCard?.type === 'api' && (
    selectedCard.api_config?.url !== form.apiUrl.trim()
    || Number(selectedCard.api_config?.timeout || 10) !== Number(form.apiTimeout)
    || JSON.stringify(selectedCard.api_config?.spec || {}, null, 2) !== form.apiSpec.trim()
    || Boolean(form.apiToken.trim())
  ));
  const hasDeleteBlockers = Boolean(selectedStats && (
    selectedStats.bound || selectedStats.reserved || selectedStats.used || selectedStats.review
  ));

  return (
    <section className="space-y-4" aria-label="自动发货资源库">
      {notice && !sheetMode && (
        <div
          role={notice.tone === 'error' ? 'alert' : 'status'}
          className={`delivery-notice ${notice.tone === 'error' ? 'is-error' : notice.tone === 'success' ? 'is-success' : 'is-info'}`}
        >
          {notice.tone === 'error' ? <AlertCircle className="h-4 w-4 shrink-0" /> : <CheckCircle2 className="h-4 w-4 shrink-0" />}
          <span>{notice.text}</span>
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-extrabold text-gray-950">资源库</h3>
          <p className="mt-1 text-sm text-gray-500">固定资料、一次一密、图片与通过验证的幂等 API。</p>
        </div>
        <button type="button" onClick={openCreate} className="ios-btn-primary inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-bold">
          <Plus className="h-4 w-4" />新建资源
        </button>
      </div>

      <div className="delivery-toolbar">
        <label className="delivery-search-field sm:max-w-sm">
          <Search className="h-4 w-4 text-gray-400" />
          <span className="sr-only">搜索资源</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索资源名称或类型" />
        </label>
        <div className="delivery-summary">
          <span>启用 {overview.enabled}</span>
          <span>需处理 {overview.attention}</span>
          <span>已绑定 {overview.bound}</span>
        </div>
      </div>

      <div className="delivery-list-surface">
        {listError ? (
          <div role="alert" className="delivery-empty-state">
            <AlertCircle className="h-10 w-10 text-red-300" />
            <p className="font-bold text-gray-800">资源库加载失败</p>
            <p className="text-sm text-gray-500">{listError}</p>
            <button type="button" onClick={() => void refreshCards()} className="delivery-secondary-button mt-2">
              <RefreshCw className="mr-2 inline h-4 w-4" />重试
            </button>
          </div>
        ) : loading ? (
          <div className="delivery-empty-state" role="status"><Loader2 className="h-6 w-6 animate-spin" />正在加载资源…</div>
        ) : visibleCards.length === 0 ? (
          <div className="delivery-empty-state">
            <Library className="h-10 w-10 text-gray-300" />
            <p className="font-bold text-gray-700">{query ? '没有匹配的资源' : '资源库还是空的'}</p>
            <p className="text-sm text-gray-400">{query ? '换个关键词再试' : '新建第一份可交付资料'}</p>
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {visibleCards.map((card) => {
              const meta = typeMeta[card.type];
              const Icon = meta.icon;
              const health = resourceHealthOf(card);
              const stats = card.stats || card.stock_stats;
              return (
                <li key={card.id} className="delivery-resource-row">
                  <button type="button" onClick={() => openEdit(card)} className="delivery-resource-main">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-600">
                      <Icon className="h-5 w-5" />
                    </span>
                    <span className="min-w-0 flex-1 text-left">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-bold text-gray-950">{card.name}</span>
                        <span className="rounded-md bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-500">{meta.short}</span>
                      </span>
                      <span className="mt-1 block truncate text-xs text-gray-500">{resourcePreview(card)}</span>
                    </span>
                    <span className="hidden text-right text-xs text-gray-400 sm:block">
                      <span className="block">绑定 {Number(stats?.bound || 0)}</span>
                      <span className="mt-1 block">{card.delay_seconds ? `延时 ${card.delay_seconds}s` : '即时发送'}</span>
                    </span>
                    <span className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-bold ${health.ready ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                      {health.label}
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
                  </button>
                  <button
                    type="button"
                    onClick={() => void toggleResource(card)}
                    className={`delivery-state-button ${card.enabled === false ? '' : 'is-enabled'}`}
                    aria-label={`${card.enabled === false ? '启用' : '停用'} ${card.name}`}
                  >
                    <span aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {sheetMode && createPortal(
        <div className="delivery-sheet-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !saving && !validating && !stockImporting) setSheetMode(null);
        }}>
          <section className="delivery-sheet-panel delivery-resource-sheet" role="dialog" aria-modal="true" aria-labelledby={headingId}>
            <header className="delivery-sheet-header">
              <div className="min-w-0">
                <h3 id={headingId} className="text-xl font-extrabold text-gray-950 sm:text-2xl">
                  {sheetMode === 'create' ? '新建交付资源' : '资源详情'}
                </h3>
                <p className="mt-1 truncate text-sm text-gray-500">
                  {sheetMode === 'create' ? '只填写当前类型真正需要的内容' : selectedCard?.name}
                </p>
              </div>
              <button type="button" onClick={() => setSheetMode(null)} className="delivery-icon-button" aria-label="关闭">
                <X className="h-5 w-5" />
              </button>
            </header>

            <div className="delivery-sheet-body space-y-6">
              {notice && (
                <div role={notice.tone === 'error' ? 'alert' : 'status'} className={`delivery-notice ${notice.tone === 'error' ? 'is-error' : notice.tone === 'success' ? 'is-success' : 'is-info'}`}>
                  {notice.text}
                </div>
              )}

              <div className="space-y-2">
                <label className="delivery-field-label" htmlFor="resource-name">资源名称</label>
                <input id="resource-name" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：课程资料包" />
              </div>

              <fieldset>
                <legend className="delivery-field-label mb-2">资源类型</legend>
                {sheetMode === 'edit' ? (
                  <div className="flex items-center gap-3 rounded-xl bg-gray-100 px-4 py-3">
                    {React.createElement(typeMeta[form.type].icon, { className: 'h-5 w-5 text-gray-600' })}
                    <span className="text-sm font-bold text-gray-900">{typeMeta[form.type].label}</span>
                    <span className="ml-auto text-xs text-gray-400">类型创建后保持不变</span>
                  </div>
                ) : (
                  <div className="resource-type-strip">
                    {(Object.keys(typeMeta) as ResourceType[]).map((type) => {
                      const meta = typeMeta[type];
                      const Icon = meta.icon;
                      return (
                        <button key={type} type="button" onClick={() => setForm({ ...form, type })} className={form.type === type ? 'is-active' : ''}>
                          <Icon className="h-4 w-4" /><span>{meta.label}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
                <p className="mt-2 text-xs leading-5 text-gray-500">{typeMeta[form.type].description}</p>
              </fieldset>

              {form.type === 'text' && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <label className="delivery-field-label" htmlFor="fixed-content">发货内容</label>
                    {sheetMode === 'edit' && (
                      <button type="button" onClick={() => setShowSensitive((value) => !value)} className="delivery-text-button">
                        {showSensitive ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        {showSensitive ? '隐藏内容' : '显示并编辑'}
                      </button>
                    )}
                  </div>
                  {showSensitive || sheetMode === 'create' ? (
                    <textarea id="fixed-content" className="ios-input min-h-40 w-full resize-y rounded-xl px-4 py-3 text-sm leading-6" value={form.textContent} onChange={(event) => setForm({ ...form, textContent: event.target.value })} placeholder={'百度网盘：https://pan.example/share\n提取码：1234\n使用说明：复制链接后打开'} />
                  ) : (
                    <button type="button" onClick={() => setShowSensitive(true)} className="sensitive-content-mask">••••••••　内容默认遮罩，点击显示</button>
                  )}
                </div>
              )}

              {form.type === 'data' && sheetMode === 'create' && (
                <div className="space-y-2">
                  <label className="delivery-field-label" htmlFor="initial-stock">初始库存（每行一条）</label>
                  <textarea id="initial-stock" className="ios-input min-h-40 w-full resize-y rounded-xl px-4 py-3 font-mono text-sm" value={form.initialStock} onChange={(event) => setForm({ ...form, initialStock: event.target.value })} placeholder={'CODE-001\nCODE-002\nCODE-003'} />
                  <p className="text-xs text-gray-500">创建时自动去除空行和本批重复项；后续补货还会与历史使用记录去重。</p>
                </div>
              )}

              {form.type === 'data' && sheetMode === 'edit' && selectedCard && (
                <div className="space-y-4">
                  <div className="stock-stat-line">
                    <span><strong>{Number(selectedStats?.available || 0)}</strong>可用</span>
                    <span><strong>{Number(selectedStats?.reserved || 0)}</strong>预留</span>
                    <span><strong>{Number(selectedStats?.used || 0)}</strong>已用</span>
                    <span><strong>{Number(selectedStats?.review || 0)}</strong>复核</span>
                  </div>
                  <div className="rounded-2xl border border-gray-200 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-sm font-bold text-gray-900">补充库存</p>
                        <p className="mt-1 text-xs text-gray-500">服务端会与当前库存及全部历史预留做最终去重。</p>
                      </div>
                      <select aria-label="补货格式" value={stockFormat} onChange={(event) => setStockFormat(event.target.value as StockFormat)} className="ios-input min-h-11 rounded-xl px-3 text-sm">
                        <option value="lines">逐行粘贴</option>
                        <option value="txt">TXT 文件</option>
                        <option value="csv">CSV（secret 列）</option>
                      </select>
                    </div>
                    <label className="mt-3 inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border border-gray-200 px-4 text-sm font-bold text-gray-700 hover:bg-gray-50">
                      <Upload className="h-4 w-4" />选择文件
                      <input type="file" className="sr-only" accept={stockFormat === 'csv' ? '.csv,text/csv' : '.txt,text/plain'} onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void file.text().then(setStockContent);
                      }} />
                    </label>
                    <textarea aria-label="补货内容" className="ios-input mt-3 min-h-32 w-full resize-y rounded-xl px-4 py-3 font-mono text-sm" value={stockContent} onChange={(event) => setStockContent(event.target.value)} placeholder={stockFormat === 'csv' ? 'secret,note\nCODE-001,第一批' : '每行一条库存内容'} />
                    <div className="mt-3 flex flex-col gap-3 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
                      <span>预检：{stockPreview.lines} 条，输入内重复 {stockPreview.localDuplicates} 条</span>
                      <button type="button" disabled={!stockContent.trim() || stockImporting} onClick={() => void importStock()} className="ios-btn-primary min-h-11 rounded-xl px-4 text-sm font-bold">
                        {stockImporting ? '补货中…' : <><PackagePlus className="mr-2 inline h-4 w-4" />确认补货</>}
                      </button>
                    </div>
                    {stockResult && <p className="mt-3 text-xs font-medium text-emerald-700">最近结果：新增 {stockResult.added}，重复 {stockResult.duplicates}，空行 {stockResult.blank}，无效 {stockResult.invalid}</p>}
                  </div>
                </div>
              )}

              {form.type === 'image' && (
                <div className="space-y-2">
                  <label className="delivery-field-label" htmlFor="image-url">图片地址</label>
                  <input id="image-url" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.imageUrl} onChange={(event) => setForm({ ...form, imageUrl: event.target.value })} placeholder="https://example.com/guide.png 或已上传图片路径" />
                  {form.imageUrl && <div className="rounded-xl bg-gray-100 px-4 py-3 text-xs text-gray-500">预览地址：{form.imageUrl}</div>}
                </div>
              )}

              {form.type === 'api' && (
                <div className="space-y-4">
                  {sheetMode === 'edit' && (
                    <div className={`flex items-start gap-3 rounded-xl px-4 py-3 ${selectedCard?.api_validation_status === 'validated' ? 'bg-emerald-50 text-emerald-800' : 'bg-orange-50 text-orange-800'}`}>
                      <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
                      <div className="text-sm">
                        <p className="font-bold">{selectedCard?.api_validation_status === 'validated' ? '连接已验证' : '连接尚未验证'}</p>
                        <p className="mt-1 text-xs opacity-80">只有固定 HTTPS POST、严格响应和稳定幂等键会被接受。</p>
                      </div>
                    </div>
                  )}
                  <div className="space-y-2">
                    <label className="delivery-field-label" htmlFor="api-url">HTTPS 地址</label>
                    <input id="api-url" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.apiUrl} onChange={(event) => setForm({ ...form, apiUrl: event.target.value })} placeholder="https://provider.example/v1/allocate" />
                  </div>
                  <div className="space-y-2">
                    <label className="delivery-field-label" htmlFor="api-token">API Token</label>
                    <input id="api-token" type="password" autoComplete="new-password" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.apiToken} onChange={(event) => setForm({ ...form, apiToken: event.target.value })} placeholder={selectedCard?.api_token_configured ? `已保存 ${selectedCard.token_preview || '••••'}，留空保持不变` : '输入供应方 Token'} />
                    <p className="text-xs text-gray-500">Token 加密保存，不会显示在资源列表、响应或发货记录中。</p>
                  </div>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-[120px_1fr]">
                    <div className="space-y-2">
                      <label className="delivery-field-label" htmlFor="api-timeout">超时（秒）</label>
                      <input id="api-timeout" type="number" min="1" max="30" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.apiTimeout} onChange={(event) => setForm({ ...form, apiTimeout: Number(event.target.value) })} />
                    </div>
                    <div className="space-y-2">
                      <label className="delivery-field-label" htmlFor="api-spec">规格 JSON</label>
                      <textarea id="api-spec" className="ios-input min-h-24 w-full resize-y rounded-xl px-4 py-3 font-mono text-xs" value={form.apiSpec} onChange={(event) => setForm({ ...form, apiSpec: event.target.value })} />
                    </div>
                  </div>
                  {sheetMode === 'edit' && (
                    <button type="button" disabled={validating || saving || apiConfigurationDirty} onClick={() => void validateConnection()} className="delivery-secondary-button w-full">
                      {apiConfigurationDirty
                        ? '先保存更改，再验证连接'
                        : validating
                          ? <><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />验证中…</>
                          : <><ShieldCheck className="mr-2 inline h-4 w-4" />验证连接</>}
                    </button>
                  )}
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label className="delivery-field-label" htmlFor="delay-seconds">延时发货（秒）</label>
                  <input id="delay-seconds" type="number" min="0" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.delaySeconds} onChange={(event) => setForm({ ...form, delaySeconds: Number(event.target.value) })} />
                </div>
                <div className="space-y-2">
                  <label className="delivery-field-label" htmlFor="low-stock">低库存提醒阈值</label>
                  <input id="low-stock" type="number" min="0" className="ios-input min-h-11 w-full rounded-xl px-4 text-sm" value={form.lowStockThreshold} onChange={(event) => setForm({ ...form, lowStockThreshold: Number(event.target.value) })} />
                </div>
              </div>

              <div className="space-y-2">
                <label className="delivery-field-label" htmlFor="resource-description">附加说明（可选）</label>
                <textarea id="resource-description" className="ios-input min-h-24 w-full resize-y rounded-xl px-4 py-3 text-sm" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="可使用 {DELIVERY_CONTENT} 指定内容插入位置" />
              </div>

              <div className="rounded-2xl border border-gray-200">
                <label className="flex min-h-14 cursor-pointer items-center justify-between gap-4 px-4">
                  <span>
                    <span className="block text-sm font-bold text-gray-900">启用资源</span>
                    <span className="mt-0.5 block text-xs text-gray-500">停用后已绑定商品会失败关闭，不会换资源</span>
                  </span>
                  <input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} className="h-5 w-5 accent-[#111111]" />
                </label>
                <label className="flex min-h-14 cursor-pointer items-center justify-between gap-4 border-t border-gray-100 px-4">
                  <span>
                    <span className="block text-sm font-bold text-gray-900">指定规格</span>
                    <span className="mt-0.5 block text-xs text-gray-500">仅与订单里同名同值的规格匹配</span>
                  </span>
                  <input type="checkbox" checked={form.isMultiSpec} onChange={(event) => setForm({ ...form, isMultiSpec: event.target.checked })} className="h-5 w-5 accent-[#111111]" />
                </label>
                {form.isMultiSpec && (
                  <div className="grid grid-cols-1 gap-3 border-t border-gray-100 p-4 sm:grid-cols-2">
                    <input aria-label="规格名称" className="ios-input min-h-11 rounded-xl px-4 text-sm" value={form.specName} onChange={(event) => setForm({ ...form, specName: event.target.value })} placeholder="规格名称，例如套餐" />
                    <input aria-label="规格值" className="ios-input min-h-11 rounded-xl px-4 text-sm" value={form.specValue} onChange={(event) => setForm({ ...form, specValue: event.target.value })} placeholder="规格值，例如年度版" />
                  </div>
                )}
              </div>

              {sheetMode === 'edit' && selectedCard && (
                <div className="border-t border-gray-100 pt-5">
                  <button type="button" disabled={hasDeleteBlockers || saving} onClick={() => void removeResource()} className="inline-flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm font-bold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-gray-300">
                    <Trash2 className="h-4 w-4" />永久删除
                  </button>
                  <p className="mt-1 text-xs text-gray-400">{hasDeleteBlockers ? '已有商品绑定或履约历史，只能停用并保留审计记录。' : '仅从未绑定且没有履约历史的资源可删除。'}</p>
                </div>
              )}
            </div>

            <footer className="delivery-sheet-footer">
              <button type="button" onClick={() => setSheetMode(null)} disabled={saving} className="delivery-secondary-button">取消</button>
              <button type="button" onClick={() => void saveResource()} disabled={saving || validating || stockImporting} className="ios-btn-primary min-h-11 flex-1 rounded-xl px-5 text-sm font-bold">
                {saving ? '保存中…' : sheetMode === 'create' ? '创建资源' : '保存更改'}
              </button>
            </footer>
          </section>
        </div>,
        document.body,
      )}
    </section>
  );
};

export default CardList;
