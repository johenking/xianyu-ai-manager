import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, PackageCheck, RefreshCw, Settings2, ShoppingBag } from 'lucide-react';
import type { AccountDetail, Card, Item } from '../types';
import {
  getAccountDetails,
  getCards,
  getItems,
  getItemsByCookie,
  updateItemDeliveryBinding,
  updateItemDeliveryBindingsBatch,
  updateItemInviteAutoFulfillment,
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

type TabKey = 'items' | 'cards';

const AutoDelivery: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('items');
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [items, setItems] = useState<Item[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  // 单个商品设置；batch 模式下为“对已勾选商品统一设置”
  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);

  const loadItemsForAccount = useCallback(async (accountId: string) => {
    if (!accountId) {
      setItems([]);
      return;
    }
    const list = accountId === ALL_ACCOUNTS_VALUE
      ? await getItems()
      : await getItemsByCookie(accountId);
    setItems(list);
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setStatusText('');
    try {
      const [accountList, cardList] = await Promise.all([getAccountDetails(), getCards()]);
      setAccounts(accountList);
      setCards(cardList);
      setSelectedAccount((current) => {
        const stillValid = current === ALL_ACCOUNTS_VALUE || accountList.some((a) => a.id === current);
        return stillValid && current ? current : (accountList[0]?.id || '');
      });
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '加载自动发货配置失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!selectedAccount) return;
    setSelectedKeys([]);
    void (async () => {
      setLoading(true);
      try {
        await loadItemsForAccount(selectedAccount);
      } catch (error) {
        setStatusText(error instanceof Error ? error.message : '加载商品失败');
      } finally {
        setLoading(false);
      }
    })();
  }, [selectedAccount, loadItemsForAccount]);

  const summary = useMemo(() => {
    const counters = { card: 0, invite: 0, off: 0 };
    items.forEach((item) => { counters[deliveryModeOf(item)] += 1; });
    return counters;
  }, [items]);

  const applyMode = async (targets: Item[], mode: DeliveryMode, cardId: number | null) => {
    if (targets.length === 0) return;
    setSaving(true);
    setStatusText('');
    try {
      if (mode === 'invite') {
        for (const item of targets) {
          // 邀请与卡密互斥：先清卡密绑定，再开邀请，避免两套库存同时命中
          if (item.delivery_card_id) {
            await updateItemDeliveryBinding(item.cookie_id, item.item_id, null);
          }
          await updateItemInviteAutoFulfillment(item.cookie_id, item.item_id, true);
        }
      } else {
        const byAccount = new Map<string, string[]>();
        targets.forEach((item) => {
          byAccount.set(item.cookie_id, [...(byAccount.get(item.cookie_id) || []), item.item_id]);
        });
        for (const [cookieId, itemIds] of byAccount) {
          await updateItemDeliveryBindingsBatch(cookieId, itemIds, mode === 'card' ? cardId : null);
        }
        for (const item of targets) {
          if (item.invite_auto_fulfillment) {
            await updateItemInviteAutoFulfillment(item.cookie_id, item.item_id, false);
          }
        }
      }

      const touched = new Set(targets.map(itemKey));
      setItems((prev) => prev.map((item) => (
        touched.has(itemKey(item))
          ? {
              ...item,
              delivery_card_id: mode === 'card' ? cardId : null,
              invite_auto_fulfillment: mode === 'invite',
            }
          : item
      )));
      const label = mode === 'card' ? '发送卡密' : mode === 'invite' ? '邀请重置' : '关键词兜底';
      setStatusText(`已将 ${targets.length} 个商品的发货方式设为「${label}」`);
      setSelectedKeys([]);
      setEditingItem(null);
      setBatchOpen(false);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '保存发货设置失败');
    } finally {
      setSaving(false);
    }
  };

  const toggleSelected = (item: Item) => {
    const key = itemKey(item);
    setSelectedKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const selectedItems = items.filter((item) => selectedKeys.includes(itemKey(item)));

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 sm:text-3xl">自动发货</h2>
          <p className="mt-2 text-sm text-gray-500">
            给每个商品指定买家付款后发什么：发送卡密、邀请重置，或回落关键词兜底规则。
          </p>
        </div>
        <button
          onClick={() => void loadData()}
          disabled={loading}
          className="self-start rounded-xl border border-gray-100 bg-white p-3 text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-black disabled:opacity-50"
          title="刷新"
        >
          <RefreshCw className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="flex gap-2 rounded-2xl bg-gray-100 p-1">
        {([['items', '商品发货', PackageCheck], ['cards', '卡密资源库', Box]] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl text-sm font-bold transition-colors ${
              activeTab === key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'cards' ? (
        <CardList />
      ) : (
        <>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <select
              aria-label="发货商品账号"
              className="ios-input min-w-0 rounded-xl px-4 py-3 text-sm sm:min-w-[220px]"
              value={selectedAccount}
              onChange={(event) => setSelectedAccount(event.target.value)}
            >
              {accounts.length === 0 && <option value="">暂无账号</option>}
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{account.nickname || account.remark || account.id}</option>
              ))}
              {accounts.length > 0 && <option value={ALL_ACCOUNTS_VALUE}>全部账号</option>}
            </select>
            <div className="flex items-center gap-3 text-xs text-gray-500">
              <span>发送卡密 {summary.card}</span>
              <span>邀请重置 {summary.invite}</span>
              <span>关键词兜底 {summary.off}</span>
            </div>
          </div>

          {statusText && (
            <div className="rounded-2xl border border-gray-100 bg-white px-4 py-3 text-sm text-gray-600 shadow-sm">
              {statusText}
            </div>
          )}

          {selectedKeys.length > 0 && (
            <div className="flex flex-col gap-3 rounded-2xl border border-yellow-200 bg-yellow-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <span className="text-sm font-bold text-gray-900">已选择 {selectedKeys.length} 个商品</span>
              <div className="flex gap-2">
                <button
                  onClick={() => setBatchOpen(true)}
                  className="ios-btn-primary inline-flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold"
                >
                  <Settings2 className="h-4 w-4" />批量设置发货
                </button>
                <button
                  onClick={() => setSelectedKeys([])}
                  className="min-h-11 rounded-xl border border-gray-200 bg-white px-4 text-sm font-bold text-gray-600 hover:bg-gray-50"
                >
                  取消选择
                </button>
              </div>
            </div>
          )}

          <div className="overflow-hidden rounded-3xl border border-gray-100 bg-white shadow-sm">
            {items.length === 0 ? (
              <div className="py-20 text-center text-gray-400">
                <ShoppingBag className="mx-auto mb-4 h-12 w-12 opacity-30" />
                {loading ? '正在加载商品…' : '暂无商品，请先在「商品列表」同步'}
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {items.map((item) => (
                  <li key={itemKey(item)} className="flex items-center gap-4 px-4 py-3 hover:bg-gray-50">
                    <input
                      type="checkbox"
                      aria-label={`选择 ${item.item_title || item.item_id}`}
                      checked={selectedKeys.includes(itemKey(item))}
                      onChange={() => toggleSelected(item)}
                      className="h-4 w-4 shrink-0"
                    />
                    <div className="h-12 w-12 shrink-0 overflow-hidden rounded-xl bg-gray-100">
                      <RemoteImage
                        src={item.item_image}
                        alt={item.item_title || '商品图片'}
                        className="h-full w-full object-cover"
                        fallback={(
                          <div className="flex h-full w-full items-center justify-center text-gray-300">
                            <Box className="h-5 w-5" />
                          </div>
                        )}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-bold text-gray-900">{item.item_title || item.item_id}</p>
                      <p className="mt-0.5 truncate text-xs text-gray-400">ID: {item.item_id} · ¥{item.item_price}</p>
                    </div>
                    <DeliveryModeBadge item={item} cards={cards} />
                    <button
                      onClick={() => setEditingItem(item)}
                      className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 text-sm font-bold text-gray-700 hover:bg-gray-100"
                    >
                      <Settings2 className="h-4 w-4" />设置
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {editingItem && (
        <DeliverySettingModal
          title="设置自动发货"
          subtitle={editingItem.item_title || editingItem.item_id}
          cards={cards}
          initialMode={deliveryModeOf(editingItem)}
          initialCardId={editingItem.delivery_card_id ?? null}
          saving={saving}
          onClose={() => setEditingItem(null)}
          onSubmit={(mode, cardId) => void applyMode([editingItem], mode, cardId)}
        />
      )}

      {batchOpen && (
        <DeliverySettingModal
          title="批量设置自动发货"
          subtitle={`将统一应用到已选择的 ${selectedItems.length} 个商品`}
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
