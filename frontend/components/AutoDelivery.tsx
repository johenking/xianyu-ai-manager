import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Clock3,
  Library,
  PackageCheck,
  RefreshCw,
  RotateCw,
  Search,
  Settings2,
  ShieldAlert,
  ShoppingBag,
} from 'lucide-react';
import type {
  AccountDetail,
  Card,
  FulfillmentRecord,
  Item,
  ItemDeliveryMode,
} from '../types';
import {
  getAccountDetails,
  getCards,
  getFulfillmentRecords,
  getItems,
  getItemsByCookie,
  resendFulfillmentRecord,
  updateItemDeliveryMode,
  updateItemDeliveryModesBatch,
} from '../services/api';
import CardList from './CardList';
import RemoteImage from './ui/RemoteImage';
import {
  DeliveryMode,
  DeliveryModeBadge,
  DeliverySettingModal,
  deliveryModeOf,
} from './ui/DeliveryMode';

const ALL_ACCOUNTS_VALUE = '__all__';
const itemKey = (item: Item) => `${item.cookie_id}-${item.item_id}`;
type TabKey = 'products' | 'resources' | 'records';
type Notice = { tone: 'success' | 'error' | 'info'; text: string };
type RecordFilter = 'all' | 'succeeded' | 'failed' | 'pending' | 'manual_review' | 'ambiguous';

const recordStatus: Record<FulfillmentRecord['status'], { label: string; className: string; icon: React.ComponentType<{ className?: string }> }> = {
  succeeded: { label: '成功', className: 'bg-emerald-50 text-emerald-700', icon: CheckCircle2 },
  pending: { label: '待处理', className: 'bg-blue-50 text-blue-700', icon: Clock3 },
  failed: { label: '失败', className: 'bg-red-50 text-red-700', icon: AlertCircle },
  manual_review: { label: '人工复核', className: 'bg-orange-50 text-orange-700', icon: ShieldAlert },
  ambiguous: { label: '结果待确认', className: 'bg-orange-50 text-orange-700', icon: ShieldAlert },
};

const formatTimestamp = (value?: string) => {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
};

const AutoDelivery: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('products');
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [selectedAccount, setSelectedAccount] = useState('');
  const [items, setItems] = useState<Item[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [baseLoading, setBaseLoading] = useState(false);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [query, setQuery] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);
  const [records, setRecords] = useState<FulfillmentRecord[]>([]);
  const [recordTotal, setRecordTotal] = useState(0);
  const [recordFilter, setRecordFilter] = useState<RecordFilter>('all');
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [resendingId, setResendingId] = useState<string | number | null>(null);

  const loadItemsForAccount = useCallback(async (accountId: string) => {
    if (!accountId) {
      setItems([]);
      return;
    }
    setItemsLoading(true);
    try {
      const list = accountId === ALL_ACCOUNTS_VALUE
        ? await getItems()
        : await getItemsByCookie(accountId);
      setItems(list);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '商品加载失败' });
    } finally {
      setItemsLoading(false);
    }
  }, []);

  const loadBaseData = useCallback(async () => {
    setBaseLoading(true);
    try {
      const [accountList, cardList] = await Promise.all([getAccountDetails(), getCards()]);
      setAccounts(accountList);
      setCards(cardList);
      setSelectedAccount((current) => {
        const valid = current === ALL_ACCOUNTS_VALUE || accountList.some((account) => account.id === current);
        return current && valid ? current : (accountList[0]?.id || '');
      });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '自动发货工作台加载失败' });
    } finally {
      setBaseLoading(false);
    }
  }, []);

  const loadRecords = useCallback(async (filter: RecordFilter = recordFilter) => {
    setRecordsLoading(true);
    try {
      const result = await getFulfillmentRecords(filter);
      setRecords(result.items);
      setRecordTotal(result.total);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '发货记录加载失败' });
      setRecords([]);
      setRecordTotal(0);
    } finally {
      setRecordsLoading(false);
    }
  }, [recordFilter]);

  useEffect(() => {
    void loadBaseData();
  }, [loadBaseData]);

  useEffect(() => {
    setSelectedKeys([]);
    void loadItemsForAccount(selectedAccount);
  }, [selectedAccount, loadItemsForAccount]);

  useEffect(() => {
    if (activeTab === 'records') void loadRecords(recordFilter);
  }, [activeTab, recordFilter, loadRecords]);

  const accountNames = useMemo(
    () => Object.fromEntries(accounts.map((account) => [
      account.id,
      account.nickname || account.remark || account.note || account.id,
    ])),
    [accounts],
  );

  const summary = useMemo(() => {
    const counters = { resource: 0, invite: 0, off: 0, unconfigured: 0 };
    items.forEach((item) => {
      const mode = deliveryModeOf(item);
      if (mode === 'card') counters.resource += 1;
      else if (mode === 'invite') counters.invite += 1;
      else if (item.delivery_mode === 'off') counters.off += 1;
      else counters.unconfigured += 1;
    });
    return counters;
  }, [items]);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return items;
    return items.filter((item) => [
      item.item_title,
      item.item_id,
      accountNames[item.cookie_id],
    ].some((value) => String(value || '').toLowerCase().includes(normalized)));
  }, [items, query, accountNames]);

  const applyLocalMode = useCallback((successfulKeys: Set<string>, mode: ItemDeliveryMode, cardId: number | null) => {
    setItems((current) => current.map((item) => (
      successfulKeys.has(itemKey(item))
        ? {
            ...item,
            delivery_mode: mode,
            delivery_card_id: mode === 'resource' ? cardId : null,
            delivery_resource_id: mode === 'resource' ? cardId : null,
            invite_auto_fulfillment: mode === 'invite',
          }
        : item
    )));
  }, []);

  const applyMode = async (targets: Item[], mode: DeliveryMode, cardId: number | null) => {
    if (targets.length === 0) return;
    const apiMode: ItemDeliveryMode = mode === 'card' ? 'resource' : mode;
    if (apiMode === 'resource' && cardId === null) return;
    setSaving(true);
    setNotice(null);
    const successfulKeys = new Set<string>();
    const failedKeys = new Set<string>();
    const failures: Array<{ item_id: string; error: string }> = [];
    try {
      if (targets.length === 1) {
        const item = targets[0];
        try {
          await updateItemDeliveryMode(item.cookie_id, item.item_id, apiMode, cardId);
          successfulKeys.add(itemKey(item));
        } catch (error) {
          failedKeys.add(itemKey(item));
          failures.push({
            item_id: item.item_id,
            error: error instanceof Error ? error.message : 'update_failed',
          });
        }
      } else {
        const byAccount = new Map<string, Item[]>();
        targets.forEach((item) => byAccount.set(
          item.cookie_id,
          [...(byAccount.get(item.cookie_id) || []), item],
        ));
        for (const [cookieId, group] of byAccount) {
          try {
            const result = await updateItemDeliveryModesBatch(
              cookieId,
              group.map((item) => item.item_id),
              apiMode,
              cardId,
            );
            const updatedIds = new Set(result.updated);
            group.forEach((item) => {
              if (updatedIds.has(item.item_id)) successfulKeys.add(itemKey(item));
              else failedKeys.add(itemKey(item));
            });
            failures.push(...result.failed);
          } catch (error) {
            const message = error instanceof Error ? error.message : 'update_failed';
            group.forEach((item) => {
              failedKeys.add(itemKey(item));
              failures.push({ item_id: item.item_id, error: message });
            });
          }
        }
      }

      applyLocalMode(successfulKeys, apiMode, cardId);
      setSelectedKeys([...failedKeys]);
      setEditingItem(null);
      setBatchOpen(false);
      const label = apiMode === 'resource' ? '资源发货' : apiMode === 'invite' ? '邀请重置' : '关闭自动发货';
      if (failures.length) {
        setNotice({
          tone: successfulKeys.size ? 'info' : 'error',
          text: `已更新 ${successfulKeys.size} 个商品，${failures.length} 个失败；失败项已保留勾选。`,
        });
      } else {
        setNotice({ tone: 'success', text: `已将 ${successfulKeys.size} 个商品设为「${label}」` });
      }
    } finally {
      setSaving(false);
    }
  };

  const toggleSelected = (item: Item) => {
    const key = itemKey(item);
    setSelectedKeys((current) => (
      current.includes(key) ? current.filter((value) => value !== key) : [...current, key]
    ));
  };

  const selectedItems = items.filter((item) => selectedKeys.includes(itemKey(item)));

  const handleRefresh = async () => {
    setNotice(null);
    if (activeTab === 'records') {
      await loadRecords(recordFilter);
      return;
    }
    if (activeTab === 'resources') {
      try {
        setCards(await getCards());
      } catch (error) {
        setNotice({ tone: 'error', text: error instanceof Error ? error.message : '资源库刷新失败' });
      }
      return;
    }
    await Promise.all([loadBaseData(), loadItemsForAccount(selectedAccount)]);
  };

  const handleResend = async (record: FulfillmentRecord) => {
    if (!record.can_resend) return;
    if (!window.confirm('确认按原始已保存内容重发？本操作不会换卡、扣库存或再次调用供应方。')) return;
    setResendingId(record.id);
    setNotice(null);
    try {
      const result = await resendFulfillmentRecord(record.id);
      setNotice({
        tone: result.status === 'succeeded' ? 'success' : 'info',
        text: result.status === 'succeeded'
          ? '原始内容已收到平台发送确认'
          : result.status === 'ambiguous'
            ? '平台结果暂不明确，已进入人工复核，请勿再次重发'
            : '重发未成功，记录已保留',
      });
      await loadRecords(recordFilter);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '重发失败' });
    } finally {
      setResendingId(null);
    }
  };

  const tabs: Array<{ key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }> = [
    { key: 'products', label: '商品配置', icon: PackageCheck },
    { key: 'resources', label: '资源库', icon: Library },
    { key: 'records', label: '发货记录', icon: ClipboardList },
  ];

  return (
    <div className="delivery-workbench animate-fade-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-gray-400">Delivery Center</p>
          <h2 className="mt-2 text-2xl font-extrabold tracking-tight text-gray-900 sm:text-3xl">自动发货</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-500">
            先准备可交付资源，再为商品选择唯一发货方式；每次交付都留下可追溯记录。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={baseLoading || itemsLoading || recordsLoading}
          className="delivery-icon-button border border-gray-200 bg-white shadow-sm"
          aria-label="刷新当前页面"
        >
          <RefreshCw className={`h-5 w-5 ${(baseLoading || itemsLoading || recordsLoading) ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <nav className="delivery-tabs" aria-label="自动发货工作台">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            aria-current={activeTab === key ? 'page' : undefined}
            onClick={() => { setActiveTab(key); setNotice(null); }}
            className={`delivery-tab ${activeTab === key ? 'is-active' : ''}`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      {notice && (
        <div
          role={notice.tone === 'error' ? 'alert' : 'status'}
          className={`delivery-notice ${notice.tone === 'error' ? 'is-error' : notice.tone === 'success' ? 'is-success' : 'is-info'}`}
        >
          {notice.tone === 'error' ? <AlertCircle className="h-4 w-4 shrink-0" /> : <CheckCircle2 className="h-4 w-4 shrink-0" />}
          <span>{notice.text}</span>
        </div>
      )}

      {activeTab === 'resources' && (
        <CardList onChanged={(nextCards) => setCards(nextCards)} />
      )}

      {activeTab === 'products' && (
        <section aria-label="商品自动发货配置" className="space-y-4">
          <div className="delivery-toolbar">
            <select
              aria-label="发货商品账号"
              className="ios-input min-h-11 min-w-0 rounded-xl px-4 text-sm sm:w-[240px]"
              value={selectedAccount}
              onChange={(event) => setSelectedAccount(event.target.value)}
            >
              {accounts.length === 0 && <option value="">{baseLoading ? '正在加载账号…' : '暂无账号'}</option>}
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{accountNames[account.id]}</option>
              ))}
              {accounts.length > 0 && <option value={ALL_ACCOUNTS_VALUE}>全部账号</option>}
            </select>
            <label className="delivery-search-field">
              <Search className="h-4 w-4 text-gray-400" aria-hidden="true" />
              <span className="sr-only">搜索商品</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索商品名称或 ID"
              />
            </label>
            <div className="delivery-summary" aria-label="商品配置统计">
              <span>资源 {summary.resource}</span>
              <span>邀请 {summary.invite}</span>
              <span>关闭 {summary.off}</span>
              <span>未配置 {summary.unconfigured}</span>
            </div>
          </div>

          {selectedKeys.length > 0 && (
            <div className="delivery-selection-bar">
              <span className="text-sm font-bold text-gray-900">已选择 {selectedKeys.length} 个商品</span>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => setSelectedKeys([])} className="delivery-secondary-button">取消选择</button>
                <button type="button" onClick={() => setBatchOpen(true)} className="ios-btn-primary min-h-11 rounded-xl px-4 text-sm font-bold">
                  <Settings2 className="mr-2 inline h-4 w-4" />批量设置
                </button>
              </div>
            </div>
          )}

          <div className="delivery-list-surface">
            {itemsLoading ? (
              <div className="delivery-empty-state" role="status">正在加载商品…</div>
            ) : visibleItems.length === 0 ? (
              <div className="delivery-empty-state">
                <ShoppingBag className="h-10 w-10 text-gray-300" />
                <p className="font-bold text-gray-700">{query ? '没有匹配的商品' : '暂无商品'}</p>
                <p className="text-sm text-gray-400">{query ? '换个关键词再试' : '请先在商品列表同步账号商品'}</p>
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {visibleItems.map((item) => (
                  <li key={itemKey(item)} className="delivery-product-row">
                    <label className="flex h-11 w-8 shrink-0 cursor-pointer items-center justify-start">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${item.item_title || item.item_id}`}
                        checked={selectedKeys.includes(itemKey(item))}
                        onChange={() => toggleSelected(item)}
                        className="h-4 w-4 accent-[#111111]"
                      />
                    </label>
                    <div className="h-12 w-12 shrink-0 overflow-hidden rounded-xl bg-gray-100">
                      <RemoteImage
                        src={item.item_image}
                        alt={item.item_title || '商品图片'}
                        className="h-full w-full object-cover"
                        fallback={<div className="flex h-full w-full items-center justify-center text-gray-300"><ShoppingBag className="h-5 w-5" /></div>}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-bold text-gray-950">{item.item_title || item.item_id}</p>
                      <p className="mt-1 truncate text-xs text-gray-400">
                        {selectedAccount === ALL_ACCOUNTS_VALUE ? `${accountNames[item.cookie_id] || item.cookie_id} · ` : ''}
                        ID {item.item_id}{item.item_price ? ` · ¥${item.item_price}` : ''}
                      </p>
                    </div>
                    <div className="delivery-product-status"><DeliveryModeBadge item={item} cards={cards} /></div>
                    <button
                      type="button"
                      onClick={() => setEditingItem(item)}
                      className="delivery-row-action"
                      aria-label={`设置 ${item.item_title || item.item_id}`}
                    >
                      <span>设置</span><ChevronRight className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {activeTab === 'records' && (
        <section aria-label="发货记录" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="delivery-filter-row" role="group" aria-label="记录状态筛选">
              {([
                ['all', '全部'],
                ['succeeded', '成功'],
                ['failed', '失败'],
                ['pending', '待处理'],
                ['manual_review', '人工复核'],
              ] as Array<[RecordFilter, string]>).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRecordFilter(value)}
                  className={recordFilter === value ? 'is-active' : ''}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-xs font-medium text-gray-400">共 {recordTotal} 条 · 交付内容默认遮罩</span>
          </div>

          <div className="delivery-list-surface">
            {recordsLoading ? (
              <div className="delivery-empty-state" role="status">正在加载发货记录…</div>
            ) : records.length === 0 ? (
              <div className="delivery-empty-state">
                <ClipboardList className="h-10 w-10 text-gray-300" />
                <p className="font-bold text-gray-700">暂无发货记录</p>
                <p className="text-sm text-gray-400">符合当前筛选条件的记录会显示在这里</p>
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {records.map((record) => {
                  const meta = recordStatus[record.status] || recordStatus.pending;
                  const StatusIcon = meta.icon;
                  return (
                    <li key={record.id} className="delivery-record-row">
                      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${meta.className}`}>
                        <StatusIcon className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-bold text-gray-950">订单 {record.order_id || '未知'}</p>
                          <span className={`rounded-lg px-2 py-1 text-xs font-bold ${meta.className}`}>{meta.label}</span>
                        </div>
                        <p className="mt-1 truncate text-xs text-gray-500">
                          {record.resource_name || '历史资源'} · {record.payload_preview || `已保存 ${record.quantity || 0} 条交付内容`}
                        </p>
                        <p className="mt-1 text-xs text-gray-400">
                          {formatTimestamp(record.created_at)}{record.item_id ? ` · 商品 ${record.item_id}` : ''}
                        </p>
                      </div>
                      <button
                        type="button"
                        disabled={!record.can_resend || resendingId === record.id}
                        onClick={() => void handleResend(record)}
                        className="delivery-resend-button"
                        title={record.can_resend ? '按原始内容重发' : '当前记录不可重发'}
                      >
                        <RotateCw className={`h-4 w-4 ${resendingId === record.id ? 'animate-spin' : ''}`} />
                        <span>{resendingId === record.id ? '重发中' : '原样重发'}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      )}

      {editingItem && (
        <DeliverySettingModal
          title="设置自动发货"
          subtitle={editingItem.item_title || editingItem.item_id}
          cards={cards}
          initialMode={deliveryModeOf(editingItem)}
          initialCardId={editingItem.delivery_card_id ?? editingItem.delivery_resource_id ?? null}
          saving={saving}
          onClose={() => setEditingItem(null)}
          onSubmit={(mode, cardId) => void applyMode([editingItem], mode, cardId)}
        />
      )}

      {batchOpen && (
        <DeliverySettingModal
          title="批量设置自动发货"
          subtitle={`统一应用到已选择的 ${selectedItems.length} 个商品`}
          cards={cards}
          initialMode="card"
          saving={saving}
          onClose={() => setBatchOpen(false)}
          onSubmit={(mode, cardId) => void applyMode(selectedItems, mode, cardId)}
        />
      )}
    </div>
  );
};

export default AutoDelivery;
