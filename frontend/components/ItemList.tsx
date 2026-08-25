import React, { useEffect, useState } from 'react';
import { Item, AccountDetail, Card } from '../types';
import {
  getItems,
  getItemsByCookie,
  getAccountDetails,
  getCards,
  syncItemsFromAccount,
  deleteItem,
  updateItemMultiSpec,
  updateItemMultiQuantityDelivery,
  updateItemDeliveryBinding,
  updateItemInviteAutoFulfillment
} from '../services/api';
import { BookOpen, Box, RefreshCw, ShoppingBag, Trash2 } from 'lucide-react';
import ItemKnowledgeModal from './ItemKnowledgeModal';
import AITrainingLab from './AITrainingLab';
import RemoteImage from './ui/RemoteImage';
import {
  DeliveryMode,
  DeliveryModeBadge,
  DeliverySettingModal,
  deliveryModeOf,
} from './ui/DeliveryMode';
import { knowledgeStateOf } from '../utils/itemKnowledge';
import { pushToast } from './ui/Toast';

const toBool = (value: unknown) => value === true || value === 1 || value === '1';
const itemKey = (item: Item) => `${item.cookie_id}-${item.item_id}`;
const ALL_ACCOUNTS_VALUE = '__all__';

type KnowledgeFilter = 'all' | 'has' | 'none';

const ItemList: React.FC = () => {
  const [items, setItems] = useState<Item[]>([]);
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [actionKey, setActionKey] = useState<string>('');
  const [statusText, setStatusText] = useState('');
  const [knowledgeItem, setKnowledgeItem] = useState<Item | null>(null);
  const [trainingItem, setTrainingItem] = useState<Item | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [deliveryItem, setDeliveryItem] = useState<Item | null>(null);
  const [savingDelivery, setSavingDelivery] = useState(false);
  const [knowledgeFilter, setKnowledgeFilter] = useState<KnowledgeFilter>('all');

  useEffect(() => {
    loadData();
  }, []);

  const loadItemsForAccount = async (accountId: string) => {
    if (!accountId) {
      setItems([]);
      return;
    }
    const itemList = accountId === ALL_ACCOUNTS_VALUE
      ? await getItems()
      : await getItemsByCookie(accountId);
    setItems(itemList);
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const [accountList, cardList] = await Promise.all([getAccountDetails(), getCards()]);
      setAccounts(accountList);
      setCards(cardList);
      const selectionStillValid =
        selectedAccount === ALL_ACCOUNTS_VALUE ||
        accountList.some((account) => account.id === selectedAccount);
      const nextSelectedAccount = selectionStillValid
        ? selectedAccount
        : (accountList[0]?.id || '');
      setSelectedAccount(nextSelectedAccount);
      await loadItemsForAccount(nextSelectedAccount);
    } catch (error) {
      const message = error instanceof Error ? error.message : '加载商品数据失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setLoading(false);
    }
  };

  const handleAccountChange = async (accountId: string) => {
    setSelectedAccount(accountId);
    setLoading(true);
    setStatusText('');
    try {
      await loadItemsForAccount(accountId);
      if (accountId === ALL_ACCOUNTS_VALUE) {
        setStatusText('已显示全部账号商品；同步商品请先选择单个账号。');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '加载商品数据失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    if (!selectedAccount || selectedAccount === ALL_ACCOUNTS_VALUE) {
      setStatusText('请先选择单个账号再同步商品');
      pushToast('error', '请先选择单个账号再同步商品');
      return;
    }
    setLoading(true);
    setStatusText('');
    try {
      const result = await syncItemsFromAccount(selectedAccount);
      if (result?.success === false) {
        throw new Error(result.message || '同步商品失败');
      }
      await loadItemsForAccount(selectedAccount);
      const message = result?.message || '商品同步完成';
      setStatusText(message);
      pushToast('success', message);
    } catch (error) {
      const message = error instanceof Error ? error.message : '同步商品失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (item: Item) => {
    if (confirm(`确认删除商品"${item.item_title}"吗？`)) {
      const key = `delete-${itemKey(item)}`;
      setActionKey(key);
      try {
        await deleteItem(item.cookie_id, item.item_id);
        setItems(prev => prev.filter(i =>
          !(i.cookie_id === item.cookie_id && i.item_id === item.item_id)
        ));
        setStatusText('商品已删除');
        pushToast('success', `商品「${item.item_title || item.item_id}」已删除`);
      } catch (error) {
        const message = error instanceof Error ? error.message : '删除失败，请重试';
        setStatusText(message);
        pushToast('error', message);
      } finally {
        setActionKey('');
      }
    }
  };

  const toggleMultiSpec = async (item: Item) => {
    const nextValue = !toBool(item.is_multi_spec);
    const key = `spec-${itemKey(item)}`;
    setActionKey(key);
    setStatusText('');
    try {
      await updateItemMultiSpec(item.cookie_id, item.item_id, nextValue);
      setItems(prev => prev.map(i =>
        i.cookie_id === item.cookie_id && i.item_id === item.item_id
          ? { ...i, is_multi_spec: nextValue }
          : i
      ));
      setStatusText(`多规格已${nextValue ? '开启' : '关闭'}`);
      pushToast('success', `多规格已${nextValue ? '开启' : '关闭'}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : '切换多规格失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setActionKey('');
    }
  };

  const toggleMultiQty = async (item: Item) => {
    const nextValue = !toBool(item.multi_quantity_delivery);
    const key = `qty-${itemKey(item)}`;
    setActionKey(key);
    setStatusText('');
    try {
      await updateItemMultiQuantityDelivery(item.cookie_id, item.item_id, nextValue);
      setItems(prev => prev.map(i =>
        i.cookie_id === item.cookie_id && i.item_id === item.item_id
          ? { ...i, multi_quantity_delivery: nextValue }
          : i
      ));
      setStatusText(`多数量发货已${nextValue ? '开启' : '关闭'}`);
      pushToast('success', `多数量发货已${nextValue ? '开启' : '关闭'}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : '切换多数量发货失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setActionKey('');
    }
  };

  // 与「自动发货」页共用同一套发货方式语义：卡密 / 邀请重置 / 关键词兜底，三选一互斥。
  const applyDeliveryMode = async (item: Item, mode: DeliveryMode, cardId: number | null) => {
    setSavingDelivery(true);
    setStatusText('');
    try {
      if (mode === 'invite') {
        if (item.delivery_card_id) {
          await updateItemDeliveryBinding(item.cookie_id, item.item_id, null);
        }
        await updateItemInviteAutoFulfillment(item.cookie_id, item.item_id, true);
      } else {
        await updateItemDeliveryBinding(item.cookie_id, item.item_id, mode === 'card' ? cardId : null);
        if (toBool(item.invite_auto_fulfillment)) {
          await updateItemInviteAutoFulfillment(item.cookie_id, item.item_id, false);
        }
      }
      setItems(prev => prev.map(i =>
        i.cookie_id === item.cookie_id && i.item_id === item.item_id
          ? { ...i, delivery_card_id: mode === 'card' ? cardId : null, invite_auto_fulfillment: mode === 'invite' }
          : i
      ));
      const message = `发货方式已更新：${mode === 'card' ? '发送卡密' : mode === 'invite' ? '邀请重置' : '关键词兜底'}`;
      setStatusText(message);
      pushToast('success', message);
      setDeliveryItem(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存发货设置失败';
      setStatusText(message);
      pushToast('error', message);
    } finally {
      setSavingDelivery(false);
    }
  };

  // 知识档案弹窗关闭后静默刷新，让档案标识立即反映最新状态
  const refreshItemsSilently = async () => {
    try {
      await loadItemsForAccount(selectedAccount);
    } catch {
      // 静默刷新失败不打断当前视图，下一次手动刷新会重试
    }
  };

  const selectedAccountLabel = selectedAccount === ALL_ACCOUNTS_VALUE
    ? null
    : accounts.find(account => account.id === selectedAccount);
  const canSyncSelectedAccount = Boolean(selectedAccount && selectedAccount !== ALL_ACCOUNTS_VALUE);

  const knowledgeCount = items.filter(item => knowledgeStateOf(item) !== 'none').length;
  const visibleItems = items.filter(item => {
    if (knowledgeFilter === 'all') return true;
    const hasKnowledge = knowledgeStateOf(item) !== 'none';
    return knowledgeFilter === 'has' ? hasKnowledge : !hasKnowledge;
  });

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-bold text-gray-900">商品管理</h2>
          <p className="text-gray-500 mt-2 text-sm">从闲鱼账号同步商品，并管理自动发货相关状态。</p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 w-full lg:w-auto">
          <button
            onClick={loadData}
            disabled={loading}
            className="p-3 rounded-xl bg-white border border-gray-100 text-gray-600 hover:bg-gray-50 hover:text-black transition-colors shadow-sm disabled:opacity-50 self-start"
            title="刷新"
          >
            <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <select
            aria-label="商品账号"
            className="ios-input px-4 py-3 rounded-xl text-sm min-w-0 sm:min-w-[220px]"
            value={selectedAccount}
            onChange={e => void handleAccountChange(e.target.value)}
          >
            {accounts.length === 0 && <option value="">暂无账号</option>}
            {accounts.map(acc => (
              <option key={acc.id} value={acc.id}>{acc.nickname || acc.remark || acc.id}</option>
            ))}
            {accounts.length > 0 && <option value={ALL_ACCOUNTS_VALUE}>全部账号</option>}
          </select>
          <button
            onClick={handleSync}
            disabled={loading || !canSyncSelectedAccount}
            className="ios-btn-primary flex items-center gap-2 px-6 py-3 rounded-2xl font-bold shadow-lg shadow-yellow-200 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            同步商品
          </button>
        </div>
      </div>

      {statusText && (
        <div className="rounded-2xl bg-white border border-gray-100 px-4 py-3 text-sm text-gray-600 shadow-sm">
          {statusText}
        </div>
      )}

      {selectedAccountLabel && (
        <div className="text-xs text-gray-400">
          当前查看账号：{selectedAccountLabel.nickname || selectedAccountLabel.remark || selectedAccountLabel.id}
        </div>
      )}
      {selectedAccount === ALL_ACCOUNTS_VALUE && (
        <div className="text-xs text-gray-400">
          当前查看：全部账号商品
        </div>
      )}

      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="知识档案筛选">
          {([
            ['all', `全部 (${items.length})`],
            ['has', `有档案 (${knowledgeCount})`],
            ['none', `无档案 (${items.length - knowledgeCount})`],
          ] as Array<[KnowledgeFilter, string]>).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setKnowledgeFilter(value)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                knowledgeFilter === value
                  ? 'bg-gray-900 text-white'
                  : 'bg-white border border-gray-200 text-gray-500 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {visibleItems.map(item => (
              <div
                key={`${item.cookie_id}-${item.item_id}`}
                className={`ios-card p-4 rounded-3xl hover:shadow-lg transition-all group relative ${
                  knowledgeStateOf(item) === 'published'
                    ? 'ring-2 ring-green-300/70'
                    : knowledgeStateOf(item) === 'draft'
                      ? 'ring-2 ring-yellow-300/70'
                      : ''
                }`}
              >
                  <div className="absolute top-3 right-3 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                      <button
                        onClick={() => handleDelete(item)}
                        disabled={actionKey === `delete-${itemKey(item)}`}
                        className="p-2 bg-white/90 backdrop-blur rounded-lg shadow-md hover:bg-red-100 text-red-500 transition-colors disabled:opacity-50"
                        title="删除"
                      >
                        <Trash2 className={`w-4 h-4 ${actionKey === `delete-${itemKey(item)}` ? 'animate-pulse' : ''}`} />
                      </button>
                  </div>
                  <div className="aspect-[16/10] sm:aspect-square bg-gray-100 rounded-2xl mb-4 overflow-hidden relative">
                      <RemoteImage
                        src={item.item_image}
                        alt={item.item_title || '商品图片'}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                        fallback={(
                          <div className="w-full h-full flex items-center justify-center text-gray-300">
                              <Box className="w-10 h-10" />
                          </div>
                        )}
                      />
                      <div className="absolute top-2 left-2 bg-black/50 backdrop-blur-md text-white text-xs font-bold px-2 py-1 rounded-lg">
                          ¥{item.item_price}
                      </div>
                      {knowledgeStateOf(item) === 'published' && (
                        <div className="absolute bottom-2 left-2 bg-green-500/90 backdrop-blur-md text-white text-[11px] font-bold px-2 py-1 rounded-lg flex items-center gap-1">
                            <BookOpen className="w-3 h-3" />档案 v{item.knowledge_published_version}
                        </div>
                      )}
                      {knowledgeStateOf(item) === 'draft' && (
                        <div className="absolute bottom-2 left-2 bg-yellow-400/90 backdrop-blur-md text-yellow-950 text-[11px] font-bold px-2 py-1 rounded-lg flex items-center gap-1">
                            <BookOpen className="w-3 h-3" />档案草稿
                        </div>
                      )}
                  </div>
                  <h3 className="font-bold text-gray-900 line-clamp-2 text-sm mb-2 h-10">{item.item_title}</h3>
                  <div className="flex justify-between items-center text-xs text-gray-500 mb-2">
                      <span className="bg-gray-100 px-2 py-1 rounded-md truncate max-w-[100px]">ID: {item.item_id}</span>
                  </div>
                  <button
                    onClick={() => setDeliveryItem(item)}
                    className="w-full mb-2 flex items-center justify-between gap-2 rounded-lg border border-gray-200 px-3 py-2 text-xs font-bold text-gray-600 hover:bg-gray-50"
                    title="设置自动发货"
                  >
                    <span className="shrink-0">自动发货</span>
                    <DeliveryModeBadge item={item} cards={cards} />
                  </button>
                  <button
                    onClick={() => setKnowledgeItem(item)}
                    className={`w-full mb-2 px-3 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-2 ${
                      knowledgeStateOf(item) === 'published'
                        ? 'bg-green-100 text-green-900 hover:bg-green-200'
                        : knowledgeStateOf(item) === 'draft'
                          ? 'bg-yellow-100 text-yellow-900 hover:bg-yellow-200'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    <BookOpen className="w-4 h-4" />
                    {knowledgeStateOf(item) === 'published'
                      ? `知识档案 · 已发布 v${item.knowledge_published_version}`
                      : knowledgeStateOf(item) === 'draft'
                        ? '知识档案 · 草稿未发布'
                        : '知识档案 · 未建档'}
                  </button>
                  <div className="flex gap-2">
                      <button
                        onClick={() => toggleMultiSpec(item)}
                        disabled={actionKey === `spec-${itemKey(item)}`}
                        className={`flex-1 text-xs font-bold px-2 py-1.5 rounded-lg transition-colors ${
                          toBool(item.is_multi_spec)
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                        } disabled:opacity-50`}
                      >
                        多规格
                      </button>
                      <button
                        onClick={() => toggleMultiQty(item)}
                        disabled={actionKey === `qty-${itemKey(item)}`}
                        className={`flex-1 text-xs font-bold px-2 py-1.5 rounded-lg transition-colors ${
                          toBool(item.multi_quantity_delivery)
                            ? 'bg-green-100 text-green-700'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                        } disabled:opacity-50`}
                      >
                        多数量发货
                      </button>
                  </div>
              </div>
          ))}
          {items.length === 0 && (
             <div className="col-span-full py-20 text-center text-gray-400">
                 <ShoppingBag className="w-12 h-12 mx-auto mb-4 opacity-30" />
                 暂无商品数据，请选择账号进行同步
             </div>
          )}
          {items.length > 0 && visibleItems.length === 0 && (
             <div className="col-span-full py-20 text-center text-gray-400">
                 <BookOpen className="w-12 h-12 mx-auto mb-4 opacity-30" />
                 当前筛选下没有商品，可切换回“全部”查看
             </div>
          )}
      </div>

      {deliveryItem && (
        <DeliverySettingModal
          title="设置自动发货"
          subtitle={deliveryItem.item_title || deliveryItem.item_id}
          cards={cards}
          initialMode={deliveryModeOf(deliveryItem)}
          initialCardId={deliveryItem.delivery_card_id ?? null}
          saving={savingDelivery}
          onClose={() => setDeliveryItem(null)}
          onSubmit={(mode, cardId) => void applyDeliveryMode(deliveryItem, mode, cardId)}
        />
      )}

      {knowledgeItem && (
        <ItemKnowledgeModal
          item={knowledgeItem}
          onClose={() => {
            setKnowledgeItem(null);
            void refreshItemsSilently();
          }}
          onTrain={() => {
            setTrainingItem(knowledgeItem);
            setKnowledgeItem(null);
            void refreshItemsSilently();
          }}
        />
      )}

      {trainingItem && accounts.find((account) => account.id === trainingItem.cookie_id) && (
        <AITrainingLab
          account={accounts.find((account) => account.id === trainingItem.cookie_id)!}
          initialItemId={trainingItem.item_id}
          onClose={() => setTrainingItem(null)}
        />
      )}
    </div>
  );
};

export default ItemList;
