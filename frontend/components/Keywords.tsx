import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, ShippingRule, ReplyRule, DefaultReply } from '../types';
import { getAccountDetails, getReplyRules, updateReplyRule, deleteReplyRule, getShippingRules, updateShippingRule, deleteShippingRule, getCards, getDefaultReplies, getDefaultReply, updateDefaultReply, deleteDefaultReply, clearDefaultReplyRecords } from '../services/api';
import { Plus, Trash2, MessageSquare, X, Save, Loader2, Key, Truck, Power, PowerOff, Edit2, RefreshCw, Sparkles, Bot, AlertCircle } from 'lucide-react';
import { InlineNotice, ToggleControl } from './ui/StatusControls';
import { PANEL_CLASS } from './ui/dashboardParts';

type TabType = 'reply' | 'delivery' | 'default';

/** 各读取路径的错误信息按资源分开保存，避免互相覆盖 */
type LoadErrorKey = 'accounts' | 'keywords' | 'shipping' | 'cards' | 'defaults';

/** 读取失败时优先展示后端返回的 message，否则退回资源级默认文案 */
const loadErrorText = (error: unknown, fallback: string): string => (
  error instanceof Error && error.message ? error.message : fallback
);

/** 表单控件统一样式：细边、连续表面，聚焦时描边加深 */
const FIELD_CLASS = 'w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-medium text-gray-700 outline-none transition-colors focus:border-gray-900';

/** 行内状态徽章：与仪表盘徽章同一套语法（小圆角 + 语义底色） */
const RowChip: React.FC<{ tone?: 'neutral' | 'good'; children: React.ReactNode }> = ({ tone = 'neutral', children }) => (
  <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${tone === 'good' ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
    {children}
  </span>
);

/** 行尾图标操作按钮：默认中性，删除态才用红色 */
const RowAction: React.FC<{
  title: string;
  tone?: 'neutral' | 'danger';
  onClick: () => void;
  children: React.ReactNode;
}> = ({ title, tone = 'neutral', onClick, children }) => (
  <button
    type="button"
    title={title}
    onClick={onClick}
    className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 transition-colors ${
      tone === 'danger' ? 'hover:bg-red-50 hover:text-red-600' : 'hover:bg-gray-100 hover:text-gray-900'
    }`}
  >
    {children}
  </button>
);

/** 面板内的居中状态块：空态与错误态共用，避免再堆一层虚线大卡 */
const PanelState: React.FC<{
  icon: React.ReactNode;
  title: string;
  description: string;
  tone?: 'neutral' | 'error';
  action?: React.ReactNode;
  role?: string;
}> = ({ icon, title, description, tone = 'neutral', action, role }) => (
  <div role={role} className="flex flex-col items-center justify-center gap-3 px-6 py-20 text-center">
    <div className={`flex h-12 w-12 items-center justify-center rounded-xl ${tone === 'error' ? 'bg-red-50 text-red-500' : 'bg-gray-100 text-gray-400'}`}>
      {icon}
    </div>
    <h3 className="text-[15px] font-bold text-gray-900">{title}</h3>
    <p className="max-w-md text-sm text-gray-500">{description}</p>
    {action}
  </div>
);

/** 读路径失败的可见错误态：与保存路径一样诚实，提供重试入口而不是静默空列表 */
const LoadErrorState: React.FC<{ title: string; message: string; onRetry: () => void }> = ({ title, message, onRetry }) => (
  <PanelState
    role="alert"
    tone="error"
    icon={<AlertCircle className="h-6 w-6" />}
    title={title}
    description={message}
    action={(
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 inline-flex min-h-11 items-center gap-2 rounded-xl bg-gray-900 px-5 text-sm font-bold text-white transition-colors hover:bg-gray-800"
      >
        <RefreshCw className="h-4 w-4" />
        重试
      </button>
    )}
  />
);

interface Keyword {
  id: string;
  keyword: string;
  reply_content: string;
  match_type: 'exact' | 'fuzzy';
  enabled: boolean;
}

interface DeliveryRuleForm {
  keyword: string;
  card_id: string;
  description: string;
  enabled: boolean;
}

interface DefaultReplyForm {
  cookie_id: string;
  enabled: boolean;
  reply_content: string;
  reply_once: boolean;
  reply_image_url: string;
}

const Keywords: React.FC = () => {
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [activeTab, setActiveTab] = useState<TabType>('reply');

  // 关键词回复相关状态
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [showReplyModal, setShowReplyModal] = useState(false);
  const [editingKeyword, setEditingKeyword] = useState<Keyword | null>(null);
  const [replyForm, setReplyForm] = useState({
    keyword: '',
    reply_content: ''
  });

  // 关键词发货相关状态
  const [shippingRules, setShippingRules] = useState<ShippingRule[]>([]);
  const [cards, setCards] = useState<any[]>([]);
  const [showDeliveryModal, setShowDeliveryModal] = useState(false);
  const [editingDeliveryRule, setEditingDeliveryRule] = useState<ShippingRule | null>(null);
  const [deliveryForm, setDeliveryForm] = useState<DeliveryRuleForm>({
    keyword: '',
    card_id: '',
    description: '',
    enabled: true
  });

  // 账号默认回复相关状态
  const [defaultReplies, setDefaultReplies] = useState<Record<string, DefaultReply>>({});
  const [showDefaultModal, setShowDefaultModal] = useState(false);
  const [editingDefaultReply, setEditingDefaultReply] = useState<DefaultReply | null>(null);
  const [defaultForm, setDefaultForm] = useState<DefaultReplyForm>({
    cookie_id: '',
    enabled: false,
    reply_content: '',
    reply_once: false,
    reply_image_url: ''
  });

  const [loading, setLoading] = useState(false);
  const [pageNotice, setPageNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  // 各读取路径的错误态：读失败不再静默呈现空列表，而是展示可见错误并提供重试
  const [loadErrors, setLoadErrors] = useState<Partial<Record<LoadErrorKey, string>>>({});
  // 各读取路径的请求代际号：快速切换账号或连续刷新时只允许最新一次请求写入视图（同 Dashboard 的做法）
  const requestGenerations = useRef<Record<LoadErrorKey, number>>({ accounts: 0, keywords: 0, shipping: 0, cards: 0, defaults: 0 });
  // 默认回复编辑弹窗的请求代际号：连续点击不同账号时只允许最新一次响应填充表单
  const defaultReplyEditGeneration = useRef(0);

  const setLoadError = (key: LoadErrorKey, message?: string) => {
    setLoadErrors((prev) => ({ ...prev, [key]: message }));
  };

  const loadAccounts = async () => {
    const generation = ++requestGenerations.current.accounts;
    try {
      const data = await getAccountDetails();
      if (requestGenerations.current.accounts !== generation) return;
      setAccounts(data);
      setLoadError('accounts', undefined);
      // 默认选择第一个账号
      if (data && data.length > 0) {
        setSelectedAccount((prev) => prev || data[0].id);
      }
    } catch (e) {
      if (requestGenerations.current.accounts !== generation) return;
      console.error('加载账号列表失败', e);
      setLoadError('accounts', loadErrorText(e, '账号列表加载失败'));
    }
  };

  useEffect(() => {
    loadAccounts();
  }, []);

  useEffect(() => {
    if (selectedAccount) {
      loadKeywords();
      loadShippingRules();
      loadCards();
      loadDefaultReplies();
    }
  }, [selectedAccount]);

  const loadDefaultReplies = async () => {
    const generation = ++requestGenerations.current.defaults;
    try {
      const data = await getDefaultReplies();
      if (requestGenerations.current.defaults !== generation) return;
      setDefaultReplies(data);
      setLoadError('defaults', undefined);
    } catch (e) {
      if (requestGenerations.current.defaults !== generation) return;
      console.error('加载默认回复失败', e);
      setDefaultReplies({});
      setLoadError('defaults', loadErrorText(e, '默认回复加载失败'));
    }
  };

  const loadShippingRules = async () => {
    const generation = ++requestGenerations.current.shipping;
    try {
      const data = await getShippingRules();
      if (requestGenerations.current.shipping !== generation) return;
      setShippingRules(data);
      setLoadError('shipping', undefined);
    } catch (e) {
      if (requestGenerations.current.shipping !== generation) return;
      console.error('加载发货规则失败', e);
      setShippingRules([]);
      setLoadError('shipping', loadErrorText(e, '发货规则加载失败'));
    }
  };

  const loadCards = async () => {
    const generation = ++requestGenerations.current.cards;
    try {
      const data = await getCards();
      if (requestGenerations.current.cards !== generation) return;
      setCards(data);
      setLoadError('cards', undefined);
    } catch (e) {
      if (requestGenerations.current.cards !== generation) return;
      console.error('加载卡券失败', e);
      setCards([]);
      setLoadError('cards', loadErrorText(e, '卡券列表加载失败'));
    }
  };

  const loadKeywords = async () => {
    if (!selectedAccount) return;
    const generation = ++requestGenerations.current.keywords;
    setLoading(true);
    try {
      const data = await getReplyRules(selectedAccount);
      if (requestGenerations.current.keywords !== generation) return;
      setKeywords(data as Keyword[]);
      setLoadError('keywords', undefined);
    } catch (e) {
      if (requestGenerations.current.keywords !== generation) return;
      console.error('加载关键词失败', e);
      setKeywords([]);
      setLoadError('keywords', loadErrorText(e, '关键词加载失败'));
    } finally {
      if (requestGenerations.current.keywords === generation) setLoading(false);
    }
  };

  const handleAdd = () => {
    if (activeTab === 'reply') {
      setEditingKeyword(null);
      setReplyForm({ keyword: '', reply_content: '' });
      setShowReplyModal(true);
    } else if (activeTab === 'delivery') {
      setEditingDeliveryRule(null);
      setDeliveryForm({ keyword: '', card_id: '', description: '', enabled: true });
      setShowDeliveryModal(true);
    } else {
      // default tab - 编辑选中账号的默认回复
      if (!selectedAccount) return;
      loadDefaultReplyForEdit(selectedAccount);
    }
  };

  const loadDefaultReplyForEdit = async (cookieId: string) => {
    const generation = ++defaultReplyEditGeneration.current;
    try {
      const data = await getDefaultReply(cookieId);
      if (defaultReplyEditGeneration.current !== generation) return;
      setEditingDefaultReply(data);
      setDefaultForm({
        cookie_id: cookieId,
        enabled: data.enabled,
        reply_content: data.reply_content,
        reply_once: data.reply_once,
        reply_image_url: data.reply_image_url || ''
      });
      setShowDefaultModal(true);
    } catch (e) {
      if (defaultReplyEditGeneration.current !== generation) return;
      // 后端对「未设置」返回 200 和默认值，走到这里必然是请求失败；
      // 不能再当作「没有设置」打开空表单，否则保存会把已有配置覆盖为空
      console.error('加载默认回复失败', e);
      setPageNotice({ tone: 'error', text: `加载默认回复失败：${loadErrorText(e, '请求失败')}，请重试` });
    }
  };

  const handleEdit = (keyword: Keyword) => {
    if (activeTab === 'reply') {
      setEditingKeyword(keyword);
      setReplyForm({
        keyword: keyword.keyword,
        reply_content: keyword.reply_content
      });
      setShowReplyModal(true);
    }
  };

  const handleEditDelivery = (rule: ShippingRule) => {
    setEditingDeliveryRule(rule);
    setDeliveryForm({
      keyword: rule.item_keyword,
      card_id: String(rule.card_group_id),
      description: rule.name,
      enabled: rule.enabled
    });
    setShowDeliveryModal(true);
  };

  const handleSave = async () => {
    if (!selectedAccount) {
      setPageNotice({ tone: 'error', text: '请先选择账号' });
      return;
    }
    if (!replyForm.keyword.trim() || !replyForm.reply_content.trim()) {
      setPageNotice({ tone: 'error', text: '请填写关键词和回复内容' });
      return;
    }

    try {
      await updateReplyRule(
        {
          id: editingKeyword?.id,
          keyword: replyForm.keyword,
          reply_content: replyForm.reply_content,
          match_type: 'exact',
          enabled: true
        },
        selectedAccount
      );
      setShowReplyModal(false);
      await loadKeywords();
      setPageNotice({ tone: 'success', text: '关键词回复已保存' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `保存失败：${(e as Error).message}` });
    }
  };

  const handleSaveDelivery = async () => {
    if (!deliveryForm.keyword.trim()) {
      setPageNotice({ tone: 'error', text: '请填写触发关键词' });
      return;
    }
    if (!deliveryForm.card_id) {
      setPageNotice({ tone: 'error', text: '请选择卡券' });
      return;
    }

    try {
      await updateShippingRule({
        id: editingDeliveryRule?.id,
        item_keyword: deliveryForm.keyword,
        card_group_id: parseInt(deliveryForm.card_id),
        name: deliveryForm.description,
        priority: 1,
        enabled: deliveryForm.enabled
      });
      setShowDeliveryModal(false);
      await loadShippingRules();
      setPageNotice({ tone: 'success', text: '关键词发货规则已保存' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `保存失败：${(e as Error).message}` });
    }
  };

  const handleDelete = async (id: string) => {
    if (!selectedAccount || !confirm('确认删除该关键词吗？')) return;
    try {
      await deleteReplyRule(id, selectedAccount);
      await loadKeywords();
      setPageNotice({ tone: 'success', text: '关键词回复已删除' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `删除失败：${(e as Error).message}` });
    }
  };

  const handleDeleteDelivery = async (id: string) => {
    if (!confirm('确认删除该发货规则吗？')) return;
    try {
      await deleteShippingRule(id);
      await loadShippingRules();
      setPageNotice({ tone: 'success', text: '关键词发货规则已删除' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `删除失败：${(e as Error).message}` });
    }
  };

  const handleToggleDelivery = async (rule: ShippingRule) => {
    try {
      await updateShippingRule({
        id: rule.id,
        item_keyword: rule.item_keyword,
        card_group_id: rule.card_group_id,
        name: rule.name,
        priority: rule.priority,
        enabled: !rule.enabled
      });
      await loadShippingRules();
      setPageNotice({ tone: 'success', text: `规则已${rule.enabled ? '停用' : '启用'}` });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `操作失败：${(e as Error).message}` });
    }
  };

  const handleSaveDefault = async () => {
    if (!defaultForm.cookie_id) {
      setPageNotice({ tone: 'error', text: '请先选择账号' });
      return;
    }

    try {
      await updateDefaultReply(defaultForm.cookie_id, {
        enabled: defaultForm.enabled,
        reply_content: defaultForm.reply_content,
        reply_once: defaultForm.reply_once,
        reply_image_url: defaultForm.reply_image_url
      });
      setShowDefaultModal(false);
      await loadDefaultReplies();
      setPageNotice({ tone: 'success', text: '账号默认回复已保存' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `保存失败：${(e as Error).message}` });
    }
  };

  const handleDeleteDefault = async (cookieId: string) => {
    if (!confirm('确认删除该默认回复吗？')) return;
    try {
      await deleteDefaultReply(cookieId);
      await loadDefaultReplies();
      setPageNotice({ tone: 'success', text: '账号默认回复已删除' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `删除失败：${(e as Error).message}` });
    }
  };

  const handleClearRecords = async (cookieId: string) => {
    if (!confirm('确认清空该账号的回复记录吗？清空后可以重新对所有对话使用默认回复。')) return;
    try {
      await clearDefaultReplyRecords(cookieId);
      setPageNotice({ tone: 'success', text: '默认回复记录已清空' });
    } catch (e) {
      setPageNotice({ tone: 'error', text: `清空失败：${(e as Error).message}` });
    }
  };

  const tabs: Array<{ key: TabType; label: string; icon: React.ReactNode; count: number }> = [
    { key: 'reply', label: '关键词回复', icon: <MessageSquare className="h-4 w-4" />, count: keywords.length },
    { key: 'delivery', label: '关键词发货', icon: <Truck className="h-4 w-4" />, count: shippingRules.length },
    {
      key: 'default',
      label: '账号默认回复',
      icon: <Bot className="h-4 w-4" />,
      count: Object.values(defaultReplies).filter((reply: DefaultReply) => reply.enabled).length,
    },
  ];

  return (
    <div className="space-y-5 animate-fade-in">
      {pageNotice && <div className="fixed right-4 top-4 z-[120] w-[calc(100%-2rem)] max-w-sm"><InlineNotice tone={pageNotice.tone}>{pageNotice.text}</InlineNotice></div>}
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">关键词管理</h2>
          <p className="mt-1.5 text-sm text-gray-500">配置自动回复和关键词发货规则</p>
        </div>
      </div>

      {/* 单一面板：分段控件 + 账号筛选 + 连续列表，避免多层漂浮卡片堆叠 */}
      <section className={`${PANEL_CLASS} overflow-hidden`}>
        <div className="space-y-3 border-b border-gray-100 bg-gray-50/70 p-4">
          <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-4 py-2 text-[13px] font-bold transition-colors ${
                  activeTab === tab.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {tab.icon}
                {tab.label}
                {activeTab === tab.key && (
                  <span className="rounded-md bg-gray-100 px-1.5 py-0.5 text-[11px] font-bold text-gray-500">{tab.count}</span>
                )}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <label htmlFor="keyword-account" className="whitespace-nowrap text-sm font-bold text-gray-700">选择账号</label>
              <select
                id="keyword-account"
                className={`${FIELD_CLASS} min-h-11 sm:w-56`}
                value={selectedAccount}
                onChange={(e) => setSelectedAccount(e.target.value)}
              >
                <option value="">请选择账号</option>
                {accounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.nickname}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  if (activeTab === 'reply') loadKeywords();
                  else if (activeTab === 'delivery') loadShippingRules();
                  else loadDefaultReplies();
                }}
                className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 transition-colors hover:bg-gray-50 sm:flex-none"
              >
                <RefreshCw className="h-4 w-4" />
                刷新
              </button>
              <button
                onClick={handleAdd}
                disabled={!selectedAccount}
                className="ios-btn-primary inline-flex min-h-11 flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-xl px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none"
              >
                <Plus className="h-4 w-4" />
                {activeTab === 'reply' ? '添加关键词' : activeTab === 'delivery' ? '添加发货规则' : '编辑默认回复'}
              </button>
            </div>
          </div>
        </div>

        {/* 内容区域 */}
        {!selectedAccount ? (
          loadErrors.accounts ? (
            <LoadErrorState title="账号列表加载失败" message={loadErrors.accounts} onRetry={() => { loadAccounts(); }} />
          ) : (
            <PanelState
              icon={<MessageSquare className="h-6 w-6" />}
              title="请选择账号"
              description="选择一个账号以管理其关键词规则"
            />
          )
        ) : activeTab === 'reply' ? (
          // 关键词回复列表
          loading ? (
            <div className="flex flex-col items-center justify-center gap-3 px-6 py-20 text-center">
              <Loader2 className="h-8 w-8 animate-spin text-gray-300" />
              <p className="text-sm text-gray-500">加载中...</p>
            </div>
          ) : loadErrors.keywords ? (
            <LoadErrorState title="关键词加载失败" message={loadErrors.keywords} onRetry={() => { loadKeywords(); }} />
          ) : keywords.length === 0 ? (
            <PanelState
              icon={<MessageSquare className="h-6 w-6" />}
              title="暂无关键词"
              description="点击右上角添加新的关键词规则"
            />
          ) : (
            <div className="divide-y divide-gray-100">
              {keywords.map((keyword) => (
                <div key={keyword.id} className="flex items-start gap-4 px-5 py-4 transition-colors hover:bg-gray-50/60">
                  <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
                    <Key className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-[15px] font-bold text-gray-900">{keyword.keyword}</h3>
                      <RowChip>精确匹配</RowChip>
                    </div>
                    <p className="mt-1 line-clamp-2 text-sm text-gray-500">{keyword.reply_content || '无回复内容'}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <RowAction title="编辑" onClick={() => handleEdit(keyword)}>
                      <Edit2 className="h-4 w-4" />
                    </RowAction>
                    <RowAction title="删除" tone="danger" onClick={() => handleDelete(keyword.id)}>
                      <Trash2 className="h-4 w-4" />
                    </RowAction>
                  </div>
                </div>
              ))}
            </div>
          )
        ) : activeTab === 'delivery' ? (
          // 关键词发货列表
          loadErrors.shipping ? (
            <LoadErrorState title="发货规则加载失败" message={loadErrors.shipping} onRetry={() => { loadShippingRules(); }} />
          ) : shippingRules.length === 0 ? (
            <PanelState
              icon={<Truck className="h-6 w-6" />}
              title="暂无发货规则"
              description="点击右上角添加新的发货规则"
            />
          ) : (
            <div className="divide-y divide-gray-100">
              {shippingRules.map((rule) => (
                <div key={rule.id} className="flex items-start gap-4 px-5 py-4 transition-colors hover:bg-gray-50/60">
                  <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
                    <Truck className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className={`text-[15px] font-bold ${rule.enabled ? 'text-gray-900' : 'text-gray-500'}`}>{rule.item_keyword}</h3>
                      <RowChip tone={rule.enabled ? 'good' : 'neutral'}>{rule.enabled ? '已启用' : '已禁用'}</RowChip>
                    </div>
                    <p className="mt-1 text-sm text-gray-500">
                      卡券：{rule.card_group_name || `ID: ${rule.card_group_id}`}
                      {rule.name && (
                        <>
                          <span className="mx-2 text-gray-300">|</span>
                          {rule.name}
                        </>
                      )}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <RowAction title={rule.enabled ? '禁用' : '启用'} onClick={() => handleToggleDelivery(rule)}>
                      {rule.enabled ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
                    </RowAction>
                    <RowAction title="编辑" onClick={() => handleEditDelivery(rule)}>
                      <Edit2 className="h-4 w-4" />
                    </RowAction>
                    <RowAction title="删除" tone="danger" onClick={() => handleDeleteDelivery(rule.id)}>
                      <Trash2 className="h-4 w-4" />
                    </RowAction>
                  </div>
                </div>
              ))}
            </div>
          )
        ) : activeTab === 'default' ? (
          // 账号默认回复列表
          loadErrors.defaults ? (
            <LoadErrorState title="默认回复加载失败" message={loadErrors.defaults} onRetry={() => { loadDefaultReplies(); }} />
          ) : accounts.length === 0 ? (
            <PanelState
              icon={<Bot className="h-6 w-6" />}
              title="暂无账号"
              description="请先添加账号"
            />
          ) : (
            <div className="divide-y divide-gray-100">
              {accounts.map((account) => {
                const defaultReply = defaultReplies[account.id];
                const hasDefaultReply = defaultReply && defaultReply.enabled;
                return (
                  <div key={account.id} className="flex items-start gap-4 px-5 py-4 transition-colors hover:bg-gray-50/60">
                    <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
                      <Bot className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className={`text-[15px] font-bold ${hasDefaultReply ? 'text-gray-900' : 'text-gray-500'}`}>{account.nickname}</h3>
                        <RowChip tone={hasDefaultReply ? 'good' : 'neutral'}>{hasDefaultReply ? '已启用' : '未设置'}</RowChip>
                        {defaultReply?.reply_once && <RowChip>只回复一次</RowChip>}
                      </div>
                      {hasDefaultReply && (
                        <p className="mt-1 line-clamp-2 text-sm text-gray-500">{defaultReply.reply_content || '无回复内容'}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <RowAction title="编辑" onClick={() => loadDefaultReplyForEdit(account.id)}>
                        <Edit2 className="h-4 w-4" />
                      </RowAction>
                      {hasDefaultReply && (
                        <>
                          <RowAction title="清空回复记录" onClick={() => handleClearRecords(account.id)}>
                            <RefreshCw className="h-4 w-4" />
                          </RowAction>
                          <RowAction title="删除" tone="danger" onClick={() => handleDeleteDefault(account.id)}>
                            <Trash2 className="h-4 w-4" />
                          </RowAction>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )
        ) : null}
      </section>

      {/* 关键词回复弹窗 */}
      {showReplyModal && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <h3 className="text-xl font-extrabold text-gray-900">
                {editingKeyword ? '编辑关键词' : '添加关键词'}
              </h3>
              <button
                onClick={() => setShowReplyModal(false)}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-900"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="modal-body space-y-5">
              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <Key className="h-4 w-4 text-gray-400" />
                  触发关键词
                </label>
                <input
                  type="text"
                  value={replyForm.keyword}
                  onChange={(e) => setReplyForm({ ...replyForm, keyword: e.target.value })}
                  placeholder="例如：价格、包邮、怎么样"
                  className={`${FIELD_CLASS} py-3`}
                />
                <p className="mt-2 text-xs text-gray-500">买家消息中包含此关键词时自动回复</p>
              </div>

              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <MessageSquare className="h-4 w-4 text-gray-400" />
                  回复内容
                </label>
                <textarea
                  value={replyForm.reply_content}
                  onChange={(e) => setReplyForm({ ...replyForm, reply_content: e.target.value })}
                  placeholder="输入自动回复的内容..."
                  rows={6}
                  className={`${FIELD_CLASS} resize-none py-3`}
                />
                <p className="mt-2 text-xs text-gray-500">支持换行，系统将自动发送此内容给买家</p>
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex w-full gap-3">
                <button
                  onClick={() => setShowReplyModal(false)}
                  className="flex-1 rounded-xl border border-gray-200 bg-white px-6 py-3 font-bold text-gray-700 transition-colors hover:bg-gray-50"
                >
                  取消
                </button>
                <button
                  onClick={handleSave}
                  className="ios-btn-primary flex flex-1 items-center justify-center gap-2 rounded-xl px-6 py-3 font-bold"
                >
                  <Save className="h-4 w-4" />
                  保存关键词
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* 关键词发货弹窗 */}
      {showDeliveryModal && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <h3 className="text-xl font-extrabold text-gray-900">
                {editingDeliveryRule ? '编辑发货规则' : '添加发货规则'}
              </h3>
              <button
                onClick={() => setShowDeliveryModal(false)}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-900"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="modal-body space-y-5">
              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <Key className="h-4 w-4 text-gray-400" />
                  触发关键词
                </label>
                <input
                  type="text"
                  value={deliveryForm.keyword}
                  onChange={(e) => setDeliveryForm({ ...deliveryForm, keyword: e.target.value })}
                  placeholder="例如：发货卡密、自动发货"
                  className={`${FIELD_CLASS} py-3`}
                />
                <p className="mt-2 text-xs text-gray-500">买家消息中包含此关键词时自动发货</p>
              </div>

              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <Sparkles className="h-4 w-4 text-gray-400" />
                  关联卡券
                </label>
                <select
                  value={deliveryForm.card_id}
                  onChange={(e) => setDeliveryForm({ ...deliveryForm, card_id: e.target.value })}
                  className={`${FIELD_CLASS} py-3`}
                >
                  <option value="">请选择卡券</option>
                  {cards.map((card) => (
                    <option key={card.id} value={card.id}>
                      {card.name || card.text_content?.substring(0, 30) || `卡券 ${card.id}`}
                      {card.is_multi_spec && ` [${card.spec_name}: ${card.spec_value}]`}
                    </option>
                  ))}
                </select>
                {loadErrors.cards ? (
                  <div className="mt-2">
                    <InlineNotice tone="error">
                      <span className="min-w-0 flex-1">卡券列表加载失败：{loadErrors.cards}</span>
                      <button type="button" onClick={() => { loadCards(); }} className="shrink-0 font-bold underline underline-offset-2">
                        重试
                      </button>
                    </InlineNotice>
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-gray-500">选择触发关键词时发送的卡券</p>
                )}
              </div>

              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <MessageSquare className="h-4 w-4 text-gray-400" />
                  描述（可选）
                </label>
                <input
                  type="text"
                  value={deliveryForm.description}
                  onChange={(e) => setDeliveryForm({ ...deliveryForm, description: e.target.value })}
                  placeholder="规则描述，方便识别"
                  className={`${FIELD_CLASS} py-3`}
                />
              </div>

              <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-5">
                <div className="flex items-center gap-2">
                  <Power className="h-4 w-4 text-gray-400" />
                  <span className="text-sm font-bold text-gray-700">启用此规则</span>
                </div>
                <ToggleControl
                  checked={deliveryForm.enabled}
                  onChange={(checked) => setDeliveryForm({ ...deliveryForm, enabled: checked })}
                  label="启用此规则"
                />
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex w-full gap-3">
                <button
                  onClick={() => setShowDeliveryModal(false)}
                  className="flex-1 rounded-xl border border-gray-200 bg-white px-6 py-3 font-bold text-gray-700 transition-colors hover:bg-gray-50"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveDelivery}
                  className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-gray-900 px-6 py-3 font-bold text-white transition-colors hover:bg-gray-800"
                >
                  <Save className="h-4 w-4" />
                  保存发货规则
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* 账号默认回复弹窗 */}
      {showDefaultModal && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <h3 className="text-xl font-extrabold text-gray-900">账号默认回复</h3>
              <button
                onClick={() => setShowDefaultModal(false)}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-900"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="modal-body space-y-5">
              <div>
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <Bot className="h-4 w-4 text-gray-400" />
                  账号
                </label>
                <select
                  value={defaultForm.cookie_id}
                  onChange={(e) => setDefaultForm({ ...defaultForm, cookie_id: e.target.value })}
                  className={`${FIELD_CLASS} py-3`}
                >
                  <option value="">请选择账号</option>
                  {accounts.map((acc) => (
                    <option key={acc.id} value={acc.id}>
                      {acc.nickname}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs text-gray-500">为此账号设置默认回复内容</p>
              </div>

              <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-5">
                <div className="flex items-center gap-2">
                  <Power className="h-4 w-4 text-gray-400" />
                  <span className="text-sm font-bold text-gray-700">启用默认回复</span>
                </div>
                <ToggleControl
                  checked={defaultForm.enabled}
                  onChange={(checked) => setDefaultForm({ ...defaultForm, enabled: checked })}
                  label="启用默认回复"
                />
              </div>

              <div className="border-t border-gray-100 pt-5">
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <MessageSquare className="h-4 w-4 text-gray-400" />
                  回复内容
                </label>
                <textarea
                  value={defaultForm.reply_content}
                  onChange={(e) => setDefaultForm({ ...defaultForm, reply_content: e.target.value })}
                  placeholder="输入默认回复的内容..."
                  rows={6}
                  className={`${FIELD_CLASS} resize-none py-3`}
                />
                <p className="mt-2 text-xs text-gray-500">当没有匹配的关键词时，系统将自动发送此内容</p>
              </div>

              <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-5">
                <div className="min-w-0">
                  <div className="text-sm font-bold text-gray-700">只回复一次</div>
                  <div className="mt-0.5 text-xs text-gray-500">启用后，每个对话只使用一次默认回复</div>
                </div>
                <ToggleControl
                  checked={defaultForm.reply_once}
                  onChange={(checked) => setDefaultForm({ ...defaultForm, reply_once: checked })}
                  label="只回复一次"
                />
              </div>

              <div className="border-t border-gray-100 pt-5">
                <label className="mb-2 flex items-center gap-2 text-sm font-bold text-gray-700">
                  <Sparkles className="h-4 w-4 text-gray-400" />
                  回复图片URL（可选）
                </label>
                <input
                  type="text"
                  value={defaultForm.reply_image_url}
                  onChange={(e) => setDefaultForm({ ...defaultForm, reply_image_url: e.target.value })}
                  placeholder="https://example.com/image.jpg"
                  className={`${FIELD_CLASS} py-3`}
                />
                <p className="mt-2 text-xs text-gray-500">可选：添加图片URL一起发送</p>
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex w-full gap-3">
                <button
                  onClick={() => setShowDefaultModal(false)}
                  className="flex-1 rounded-xl border border-gray-200 bg-white px-6 py-3 font-bold text-gray-700 transition-colors hover:bg-gray-50"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveDefault}
                  className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-gray-900 px-6 py-3 font-bold text-white transition-colors hover:bg-gray-800"
                >
                  <Save className="h-4 w-4" />
                  保存默认回复
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default Keywords;
