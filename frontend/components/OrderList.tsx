import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Order, OrderStatus, Item, OrderSyncResponse, AccountDetail } from '../types';
import { getOrders, getOrderDetail, syncOrders, syncSingleOrder, manualShipOrder, updateOrder, deleteOrder, importOrders, getItems, getAccountDetails } from '../services/api';
import { Search, Truck, RefreshCw, ChevronLeft, ChevronRight, PackageCheck, Edit, Eye, Plus, Save, X, ExternalLink, Trash2, Upload, LogIn } from 'lucide-react';
import { InlineNotice } from './ui/StatusControls';
import OrderItemImage from './ui/OrderItemImage';
import BuyerAvatar from './ui/BuyerAvatar';

// 买家身份来源标注：让「历史未保存」如实呈现，不伪装成成交时信息
const BUYER_IDENTITY_LABELS: Record<string, string> = {
  history_unsaved: '身份历史未保存',
  missing: '身份待采集',
};

const SEARCH_DEBOUNCE_MS = 280;

const SYNC_FIELD_LABELS = {
  status: '订单状态',
  item_image: '商品图片',
  buyer_nickname: '买家昵称',
  buyer_avatar: '买家头像',
  amount: '实付金额',
  time: '成交时间',
} as const;

const buyerListLabel = (order: Order): string => {
  if (order.buyer_display_name?.trim()) return order.buyer_display_name.trim();
  const buyerId = String(order.buyer_id || '').trim();
  return buyerId ? `买家 · ID 尾号 ${buyerId.slice(-4)}` : '买家 · ID 待补充';
};

const orderAmountLabel = (order: Order): string => {
  if (typeof order.paid_amount_fen === 'number' && Number.isFinite(order.paid_amount_fen)) {
    return `¥${(order.paid_amount_fen / 100).toFixed(2)}`;
  }
  const amount = String(order.amount ?? '').trim();
  if (!amount) return '未记录';
  return /^[¥￥]/.test(amount) ? amount : `¥${amount}`;
};

const orderTimeLabel = (order: Order): string => {
  if (typeof order.ordered_at_utc === 'number' && Number.isFinite(order.ordered_at_utc)) {
    const value = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(order.ordered_at_utc * 1000));
    return `${value}（成交时间 · 上海）`;
  }
  if (order.created_at) return `${order.created_at}（创建时间回退）`;
  return '时间未记录';
};

const StatusBadge: React.FC<{ status: OrderStatus }> = ({ status }) => {
  const styles = {
    unknown: 'bg-amber-50 text-amber-700 border border-amber-200',
    processing: 'bg-yellow-100 text-yellow-800',
    pending_ship: 'bg-[#FFE815] text-black',
    shipped: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    cancelled: 'bg-gray-100 text-gray-500',
    refunding: 'bg-red-100 text-red-600',
    refunded: 'bg-red-100 text-red-700',
    refund_cancelled: 'bg-gray-100 text-gray-600',
  };

  const labels = {
    unknown: '待核对',
    processing: '处理中',
    pending_ship: '待发货',
    shipped: '已发货',
    completed: '已完成',
    cancelled: '已取消',
    refunding: '退款中',
    refunded: '已退款',
    refund_cancelled: '退款已关闭',
  };

  return (
    <span className={`px-3 py-1.5 rounded-lg text-xs font-bold ${styles[status] || styles.cancelled}`}>
      {labels[status] || status}
    </span>
  );
};

const OrderList: React.FC<{ onNavigateAccounts?: () => void }> = ({ onNavigateAccounts }) => {
  const [orders, setOrders] = useState<Order[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [accountFilter, setAccountFilter] = useState('');
  const [startDate, setStartDate] = useState(''); // 成交时间区间起（YYYY-MM-DD，含）
  const [endDate, setEndDate] = useState('');       // 成交时间区间止（YYYY-MM-DD，含）
  const [dateError, setDateError] = useState('');   // 前端日期区间校验错误
  const [filter, setFilter] = useState('all');
  const [searchText, setSearchText] = useState(''); // 输入框即时值
  const [debouncedSearch, setDebouncedSearch] = useState(''); // 防抖后的请求值
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editingOrder, setEditingOrder] = useState<Partial<Order> | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [showShipModal, setShowShipModal] = useState(false);
  const [shipOrderId, setShipOrderId] = useState<string>('');
  const [shipLoading, setShipLoading] = useState(false);
  const [shipResult, setShipResult] = useState<{success: boolean; message: string} | null>(null);
  const [syncingOrderId, setSyncingOrderId] = useState<string | null>(null);
  const [deletingOrderId, setDeletingOrderId] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [pageNotice, setPageNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [syncResult, setSyncResult] = useState<OrderSyncResponse | null>(null);
  const [loginRecoveryAccounts, setLoginRecoveryAccounts] = useState<string[]>([]);
  // 请求代际号：旧响应到达时直接丢弃，避免慢请求覆盖新筛选结果
  const requestGeneration = useRef(0);
  const listAbortController = useRef<AbortController | null>(null);
  const detailGeneration = useRef(0);
  const detailAbortController = useRef<AbortController | null>(null);
  const itemNames = useMemo(
    () => Object.fromEntries(
      items
        .filter((item) => Boolean(item.item_id))
        .map((item) => [item.item_id, item.item_title || item.item_id])
    ),
    [items]
  );

  // 账号 cookie_id → 展示名映射（备注优先），供多账号用户在列表行内识别订单归属
  const accountNames = useMemo(
    () => Object.fromEntries(
      accounts.map((account) => [account.id, account.remark || account.note || account.id])
    ),
    [accounts]
  );

  // 仅多账号用户在行内显示账号标识（与筛选下拉一致）；单账号无歧义则不占用视觉空间
  const accountLabelOf = (order: Order): string => {
    if (accounts.length <= 1) return '';
    const cookieId = String(order.cookie_id || '').trim();
    if (!cookieId) return '';
    return accountNames[cookieId] || cookieId;
  };

  // 搜索防抖：输入停顿后才发起服务端搜索
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearch(searchText.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchText]);

  const loadOrders = async () => {
      // 前端拦截「开始晚于结束」，与后端 422 同语义，避免无谓请求
      if (startDate && endDate && startDate > endDate) {
          setDateError('开始日期不得晚于结束日期');
          return;
      }
      setDateError('');
      const generation = ++requestGeneration.current;
      listAbortController.current?.abort();
      const controller = new AbortController();
      listAbortController.current = controller;
      setLoading(true);
      try {
          const res = await getOrders({
              cookieId: accountFilter || undefined,
              status: filter,
              search: debouncedSearch || undefined,
              startDate: startDate || undefined,
              endDate: endDate || undefined,
              page,
              pageSize: 20,
          }, controller.signal);
          if (generation !== requestGeneration.current) return; // 过期响应
          setOrders(res.data);
          setTotal(res.total);
          setTotalPages(Math.max(1, res.total_pages));
      } catch (e) {
          if (generation !== requestGeneration.current || controller.signal.aborted) return;
          console.error('加载订单失败:', e);
          setPageNotice({ tone: 'error', text: e instanceof Error ? e.message : '订单加载失败' });
      } finally {
          if (generation === requestGeneration.current) {
              setLoading(false);
          }
      }
  };

  // 从订单的 item_id 查找对应的商品名称（通过标题匹配）
  const getItemNameById = (orderId: string, orderItemTitle?: string): string => {
      // 如果订单有 item_title，优先使用
      if (orderItemTitle && orderItemTitle.trim()) {
          return orderItemTitle;
      }

      // 尝试通过 item_id 直接匹配
      if (itemNames[orderId]) {
          return itemNames[orderId];
      }

      // 尝试在商品列表中查找相似标题的商品
      const matchingItem = items.find(item => {
          // 如果订单有标题，尝试匹配商品标题
          if (orderItemTitle && item.item_title) {
              // 检查是否包含关键词
              const orderTitleLower = orderItemTitle.toLowerCase();
              const itemTitleLower = item.item_title.toLowerCase();
              return itemTitleLower.includes(orderTitleLower) || orderTitleLower.includes(itemTitleLower);
          }
          return false;
      });

      if (matchingItem?.item_title) {
          return matchingItem.item_title;
      }

      return '未知商品';
  };

  useEffect(() => {
    void loadOrders();
  }, [filter, page, debouncedSearch, accountFilter, startDate, endDate]);

  useEffect(() => {
    return () => {
      listAbortController.current?.abort();
      detailAbortController.current?.abort();
    };
  }, []);

  useEffect(() => {
    getItems().then((itemsList) => {
      setItems(itemsList);
    }).catch((e) => {
      console.error('加载商品列表失败:', e);
    });
    getAccountDetails().then(setAccounts).catch((e) => {
      console.error('加载账号列表失败:', e);
    });
  }, []);

  const handleSync = async () => {
      setLoading(true);
      setSyncResult(null);
      setLoginRecoveryAccounts([]);
      setPageNotice({ tone: 'info', text: '正在发现并核对近 90 天订单' });
      try {
        const result = await syncOrders(undefined, 90);
        setSyncResult(result);
        setLoginRecoveryAccounts(result.requires_login);
        await loadOrders();
        setPageNotice({
          tone: result.requires_login.length > 0 || (!result.success && !result.partial)
            ? 'error'
            : result.partial ? 'info' : 'success',
          text: result.message || '订单同步完成',
        });
      } catch (error) {
        setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '订单同步失败' });
      } finally {
        setLoading(false);
      }
  };

  const handleShip = (id: string) => {
      setShipOrderId(id);
      setShipResult(null);
      setShowShipModal(true);
  };

  const executeShip = async (mode: 'status_only' | 'full_delivery') => {
      setShipLoading(true);
      setShipResult(null);
      try {
          const res = await manualShipOrder([shipOrderId], mode);
          const result = res?.results?.[0];
          if (result?.success) {
              setShipResult({ success: true, message: result.message });
              loadOrders();
          } else {
              setShipResult({ success: false, message: result?.message || '发货失败' });
          }
      } catch (e: any) {
          setShipResult({ success: false, message: e?.message || '请求失败' });
      } finally {
          setShipLoading(false);
      }
  };

  const handleViewDetail = async (order: Order) => {
    const generation = ++detailGeneration.current;
    detailAbortController.current?.abort();
    const controller = new AbortController();
    detailAbortController.current = controller;
    // 先用列表行立即呈现，再取真实详情（收货信息只在详情接口返回）
    setSelectedOrder(order);
    setConfirmingDelete(false);
    setShowDetailModal(true);
    setDetailLoading(true);
    try {
      const res = await getOrderDetail(order.order_id, controller.signal);
      if (generation === detailGeneration.current && !controller.signal.aborted && res.data) {
        setSelectedOrder(prev => (prev && prev.order_id === order.order_id ? { ...prev, ...res.data } : prev));
      }
    } catch (e) {
      if (generation !== detailGeneration.current || controller.signal.aborted) return;
      console.error('加载订单详情失败:', e);
      setPageNotice({ tone: 'error', text: '订单详情加载失败，展示的是列表缓存数据' });
    } finally {
      if (generation === detailGeneration.current) {
        setDetailLoading(false);
      }
    }
  };

  const closeDetail = () => {
    detailGeneration.current += 1;
    detailAbortController.current?.abort();
    detailAbortController.current = null;
    setDetailLoading(false);
    setShowDetailModal(false);
  };

  const handleEdit = (order: Order) => {
    setEditingOrder({ ...order });
    setShowEditModal(true);
  };

  const handleSaveEdit = async () => {
    if (!editingOrder || !editingOrder.order_id) return;
    try {
      // 映射前端字段到后端期望的字段名
      const updateData: Record<string, any> = {};

      if (editingOrder.status !== undefined) {
        updateData.order_status = editingOrder.status;
      }
      if (editingOrder.buyer_id !== undefined) {
        updateData.buyer_id = editingOrder.buyer_id;
      }
      if (editingOrder.amount !== undefined) {
        updateData.amount = editingOrder.amount;
      }
      if (editingOrder.receiver_name !== undefined) {
        updateData.receiver_name = editingOrder.receiver_name;
      }
      if (editingOrder.receiver_phone !== undefined) {
        updateData.receiver_phone = editingOrder.receiver_phone;
      }
      if (editingOrder.receiver_address !== undefined) {
        updateData.receiver_address = editingOrder.receiver_address;
      }
      if (editingOrder.item_id !== undefined) {
        updateData.item_id = editingOrder.item_id;
      }
      if (editingOrder.quantity !== undefined) {
        updateData.quantity = editingOrder.quantity;
      }

      await updateOrder(editingOrder.order_id, updateData);
      setShowEditModal(false);
      setEditingOrder(null);
      await loadOrders();
      setPageNotice({ tone: 'success', text: '订单修改已保存' });
    } catch (error) {
      console.error('更新订单失败:', error);
      setPageNotice({ tone: 'error', text: error instanceof Error ? error.message : '更新失败，请重试' });
    }
  };

  const selectImportFile = (file: File | null) => {
    if (file && !file.name.toLowerCase().endsWith('.xlsx')) {
      setImportFile(null);
      setPageNotice({ tone: 'error', text: '仅支持 .xlsx 文件，请重新选择' });
      return;
    }
    setImportFile(file);
  };

  const handleImportOrders = async () => {
    if (!importFile) return;
    try {
      const formData = new FormData();
      formData.append('file', importFile);
      await importOrders(formData);
      setShowImportModal(false);
      setImportFile(null);
      await loadOrders();
      setPageNotice({ tone: 'success', text: '订单导入成功' });
    } catch (error) {
      setPageNotice({ tone: 'error', text: error instanceof Error ? `导入失败：${error.message}` : '导入失败，请检查 Excel 文件' });
    }
  };

  const handleSyncSingle = async (orderId: string) => {
    setSyncingOrderId(orderId);
    setLoginRecoveryAccounts([]);
    try {
      const result = await syncSingleOrder(orderId);
      if (result.success || result.partial) {
        await loadOrders();
      }
      if (result.requires_login && selectedOrder?.cookie_id) {
        setLoginRecoveryAccounts([selectedOrder.cookie_id]);
      }
      setPageNotice({
        tone: result.success ? 'success' : result.partial ? 'info' : 'error',
        text: result.message || (result.partial ? '订单已获取部分字段' : '同步失败'),
      });
    } catch (error: any) {
      console.error('同步订单失败:', error);
      setPageNotice({ tone: 'error', text: error?.message || '同步失败，请重试' });
    } finally {
      setSyncingOrderId(null);
    }
  };

  // 删除仅入口于详情弹窗危险区，且需先点一次「确认删除」二次确认
  const handleDelete = async (orderId: string) => {
    setDeletingOrderId(orderId);
    try {
      await deleteOrder(orderId);
      closeDetail();
      setSelectedOrder(null);
      setConfirmingDelete(false);
      setPageNotice({ tone: 'success', text: '订单已删除' });
      await loadOrders();
    } catch (error: any) {
      console.error('删除订单失败:', error);
      setPageNotice({ tone: 'error', text: error?.message || '删除失败，请重试' });
    } finally {
      setDeletingOrderId(null);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      {pageNotice && <div className="fixed right-4 top-4 z-[120] w-[calc(100%-2rem)] max-w-sm"><InlineNotice tone={pageNotice.tone}>{pageNotice.text}</InlineNotice></div>}
      <div className="flex flex-col md:flex-row justify-between md:items-end gap-4">
        <div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">订单中心</h2>
          <p className="text-gray-500 mt-2 font-medium">查看所有闲鱼交易记录与状态。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <button aria-label="刷新订单列表" onClick={loadOrders} className="p-3 rounded-2xl bg-white border border-gray-100 text-gray-600 hover:bg-gray-50 hover:text-black transition-colors shadow-sm">
                <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setShowImportModal(true)}
              className="px-5 py-3 rounded-2xl font-bold bg-gray-900 text-white hover:bg-gray-800 transition-colors text-sm flex items-center gap-2 shadow-lg"
            >
              <Plus className="w-4 h-4" />
              插入订单
            </button>
            <button
              onClick={handleSync}
              disabled={loading}
              className="ios-btn-primary px-6 py-3 rounded-2xl font-bold shadow-lg shadow-yellow-200 text-sm flex items-center gap-2 disabled:opacity-60"
            >
                <Truck className="w-5 h-5" />
                同步近90天订单
            </button>
        </div>
      </div>

      {syncResult && (
        <div className={`border px-4 py-3 rounded-lg text-sm ${syncResult.requires_login.length ? 'bg-red-50 border-red-200 text-red-800' : syncResult.partial ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-emerald-50 border-emerald-200 text-emerald-900'}`}>
          <div className="font-bold">{syncResult.message}</div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span>发现 {syncResult.summary.discovered}</span>
            <span>状态更新 {syncResult.summary.status_updated}</span>
            <span>详情更新 {syncResult.summary.details_updated}</span>
            <span>无变化 {syncResult.summary.unchanged}</span>
            <span>失败 {syncResult.summary.failed}</span>
            <span>状态待确认 {syncResult.summary.status_unconfirmed}</span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-current/10 pt-3 sm:grid-cols-3 lg:grid-cols-6">
            {Object.entries(SYNC_FIELD_LABELS).map(([field, label]) => {
              const coverage = syncResult.summary.field_coverage[field as keyof typeof SYNC_FIELD_LABELS];
              return (
                <div key={field} className="min-w-0">
                  <div className="text-[11px] opacity-70">{label}</div>
                  <div className="mt-0.5 font-bold tabular-nums">
                    {coverage.total ? `${coverage.covered}/${coverage.total}` : '0/0'}
                    <span className="ml-1 text-[11px] font-medium opacity-70">
                      {Math.round(coverage.rate * 100)}%
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          {syncResult.accounts.some((account) => !account.success) && (
            <div className="mt-3 border-t border-current/10 pt-3 text-xs">
              {syncResult.accounts.filter((account) => !account.success).map((account) => (
                <div key={account.cookie_id}>
                  {accountNames[account.cookie_id] || account.cookie_id}：{account.message || account.error_code || '同步未完成'}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {loginRecoveryAccounts.length > 0 && (
        <div className="flex flex-col gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="font-bold">账号登录状态需要恢复</div>
            <div className="mt-1 text-xs">{loginRecoveryAccounts.map((id) => accountNames[id] || id).join('、')}</div>
          </div>
          <button
            type="button"
            onClick={onNavigateAccounts}
            className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-bold text-white"
          >
            <LogIn className="h-4 w-4" />前往账号管理
          </button>
        </div>
      )}

      <div className="ios-card rounded-[2rem] overflow-hidden shadow-lg border-0 bg-white">
        {/* Toolbar */}
        <div className="p-4 border-b border-gray-50 flex flex-col md:flex-row gap-4 justify-between items-center bg-[#FAFAFA]">
          <div className="flex gap-1 p-1 bg-gray-200/50 rounded-xl overflow-x-auto max-w-full">
             {[
                 {k:'all', v:'全部'},
                 {k:'pending_ship', v:'待发货'},
                 {k:'shipped', v:'已发货'},
                 {k:'completed', v:'已完成'},
                 {k:'refunding', v:'退款中'},
                 {k:'refunded', v:'已退款'},
                 {k:'cancelled', v:'已关闭'},
                 {k:'unknown', v:'待核对'}
             ].map(opt => (
                 <button
                    key={opt.k}
                    onClick={() => {
                      setFilter(opt.k);
                      setPage(1);
                      setSearchText('');
                      setDebouncedSearch('');
                    }}
                    className={`px-5 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap ${filter === opt.k ? 'bg-white text-black shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                 >
                    {opt.v}
                 </button>
             ))}
          </div>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full md:w-auto">
            {accounts.length > 1 && (
              <select
                value={accountFilter}
                onChange={(e) => {
                  setAccountFilter(e.target.value);
                  setPage(1);
                }}
                className="ios-input px-3 py-2.5 rounded-xl bg-white border-none shadow-sm text-sm font-medium text-gray-700 min-h-11"
                aria-label="按账号筛选"
              >
                <option value="">全部账号</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.remark || account.note || account.id}
                  </option>
                ))}
              </select>
            )}
            <div className="flex items-center gap-1.5 text-sm">
              <input
                type="date"
                value={startDate}
                max={endDate || undefined}
                onChange={(e) => {
                  setStartDate(e.target.value);
                  setPage(1);
                }}
                className="ios-input px-3 py-2.5 rounded-xl bg-white border-none shadow-sm text-sm font-medium text-gray-700 min-h-11"
                aria-label="成交开始日期"
              />
              <span className="text-gray-400">至</span>
              <input
                type="date"
                value={endDate}
                min={startDate || undefined}
                onChange={(e) => {
                  setEndDate(e.target.value);
                  setPage(1);
                }}
                className="ios-input px-3 py-2.5 rounded-xl bg-white border-none shadow-sm text-sm font-medium text-gray-700 min-h-11"
                aria-label="成交结束日期"
              />
              {(startDate || endDate) && (
                <button
                  type="button"
                  onClick={() => {
                    setStartDate('');
                    setEndDate('');
                    setDateError('');
                    setPage(1);
                  }}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors min-h-11"
                  aria-label="清除日期筛选"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
            <div className="relative w-full md:w-auto group">
               <Search className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-[#FFE815] transition-colors" />
               <input
                   type="text"
                   placeholder="搜索订单号/商品/买家..."
                   value={searchText}
                   onChange={(e) => setSearchText(e.target.value)}
                   className="ios-input pl-10 pr-4 py-2.5 rounded-xl w-full md:w-64 bg-white border-none shadow-sm focus:ring-0"
               />
            </div>
          </div>
        </div>

        {dateError && (
          <div className="px-4 py-2 bg-red-50 border-b border-red-100 text-sm font-medium text-red-600">
            {dateError}
          </div>
        )}

        {/* Mobile order list */}
        <div className="md:hidden divide-y divide-gray-100 min-h-[320px]">
          {orders.map((order) => (
            <div key={`mobile-${order.id}`} className="p-4 space-y-3">
              <div className="flex items-start gap-3">
                <div className="w-16 h-16 rounded-xl bg-gray-100 overflow-hidden border border-gray-100 flex-shrink-0">
                  <OrderItemImage
                    orderId={order.order_id}
                    directSrc={order.item_image}
                    alt={order.item_title || '订单商品图片'}
                    className="w-full h-full object-cover"
                    fallback={<div className="w-full h-full flex items-center justify-center text-gray-300"><PackageCheck /></div>}
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="font-bold text-gray-900 text-sm line-clamp-2">
                    {getItemNameById(order.item_id, order.item_title)}
                  </div>
                  <div className="mt-1 text-xs text-gray-500 break-all">订单号：{order.order_id}</div>
                  {accountLabelOf(order) && (
                    <div className="mt-1 inline-flex items-center px-2 py-0.5 rounded-md bg-gray-100 text-[11px] font-semibold text-gray-600">
                      {accountLabelOf(order)}
                    </div>
                  )}
                </div>
                <StatusBadge status={order.status} />
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-gray-400">买家</div>
                  <div className="mt-1 flex items-center gap-2">
                    <BuyerAvatar
                      src={order.buyer_avatar_url}
                      className="w-8 h-8 rounded-full object-cover border border-gray-100 flex-shrink-0"
                    />
                    <div className="font-semibold text-gray-700 break-all">
                      {buyerListLabel(order)}
                    </div>
                  </div>
                  {!order.buyer_display_name && BUYER_IDENTITY_LABELS[order.buyer_identity || ''] && (
                    <div className="mt-0.5 text-[10px] text-gray-400">{BUYER_IDENTITY_LABELS[order.buyer_identity || '']}</div>
                  )}
                </div>
                <div className="text-right">
                  <div className="text-gray-400">实付金额</div>
                  <div className="mt-1 text-base font-extrabold text-gray-900">{orderAmountLabel(order)}</div>
                </div>
              </div>
              <div className="flex items-center justify-between gap-2 text-xs text-gray-400">
                <span>数量 {order.quantity || 1}</span>
                <span>{orderTimeLabel(order)}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                {order.status === 'pending_ship' && (
                  <button onClick={() => handleShip(order.order_id)} className="px-3 py-2 min-h-11 rounded-lg bg-black text-white text-xs font-bold">
                    立即发货
                  </button>
                )}
                <button onClick={() => handleViewDetail(order)} className="px-3 py-2 min-h-11 rounded-lg bg-blue-50 text-blue-700 text-xs font-bold">详情</button>
              </div>
            </div>
          ))}
        </div>

        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto min-h-[400px]">
          <table className="w-full min-w-[900px] text-left border-collapse table-fixed">
            <thead>
              <tr className="bg-white text-gray-400 text-xs font-bold uppercase tracking-wider border-b border-gray-50">
                <th className="px-6 py-5" style={{width: '28%'}}>订单信息</th>
                <th className="px-6 py-5" style={{width: '26%'}}>买家信息</th>
                <th className="px-6 py-5" style={{width: '11%'}}>实付金额</th>
                <th className="px-6 py-5" style={{width: '13%'}}>当前状态</th>
                <th className="px-6 py-5 text-right" style={{width: '22%'}}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {orders.map((order) => (
                <tr key={order.id} className="hover:bg-[#FFFDE7]/50 transition-colors group">
                  <td className="px-6 py-5">
                    <div className="flex items-center gap-5">
                      <div className="w-16 h-16 rounded-xl bg-gray-100 overflow-hidden shadow-sm border border-gray-100 flex-shrink-0">
                        <OrderItemImage
                          orderId={order.order_id}
                          directSrc={order.item_image}
                          alt={order.item_title || '订单商品图片'}
                          className="w-full h-full object-cover"
                          fallback={<div className="w-full h-full flex items-center justify-center text-gray-300"><PackageCheck /></div>}
                        />
                      </div>
                      <div className="min-w-0">
                        <div className="font-bold text-gray-900 line-clamp-1 text-sm">
                          {getItemNameById(order.item_id, order.item_title)}
                        </div>
                        <div className="text-xs text-gray-500 mt-1 font-medium">订单ID: {order.order_id}</div>
                        <div className="text-xs text-gray-400 mt-0.5">数量: {order.quantity} • {orderTimeLabel(order)}</div>
                        {accountLabelOf(order) && (
                          <div className="mt-1 inline-flex items-center px-2 py-0.5 rounded-md bg-gray-100 text-[11px] font-semibold text-gray-600">
                            {accountLabelOf(order)}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-5">
                      <div className="flex items-center gap-3">
                          <BuyerAvatar
                            src={order.buyer_avatar_url}
                            className="w-9 h-9 rounded-full object-cover border border-gray-100 flex-shrink-0"
                          />
                          <div className="min-w-0">
                              <div className="text-sm font-bold text-gray-800 line-clamp-1">
                                {buyerListLabel(order)}
                              </div>
                              <div className="text-xs text-gray-400 line-clamp-1">
                                {order.buyer_display_name
                                  ? `ID 尾号: ${String(order.buyer_id || '').slice(-4) || '待补充'}`
                                  : (BUYER_IDENTITY_LABELS[order.buyer_identity || ''] || 'ID')}
                              </div>
                          </div>
                      </div>
                  </td>
                  <td className="px-6 py-5 text-base font-extrabold text-gray-900 font-feature-settings-tnum">{orderAmountLabel(order)}</td>
                  <td className="px-6 py-5">
                    <StatusBadge status={order.status} />
                  </td>
                  <td className="px-6 py-5 text-right">
                    {order.status === 'pending_ship' && (
                        <button
                            onClick={() => handleShip(order.order_id)}
                            className="mr-2 text-white bg-black hover:bg-gray-800 shadow-lg shadow-gray-200 text-xs font-bold px-3 py-2 rounded-xl transition-all active:scale-95"
                        >
                            立即发货
                        </button>
                    )}
                    <button
                      onClick={() => handleViewDetail(order)}
                      className="text-gray-400 hover:text-blue-600 p-2 rounded-xl hover:bg-blue-50 transition-colors"
                      title="查看详情（编辑、同步、删除入口在详情内）"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="p-4 border-t border-gray-50 flex items-center justify-between bg-white">
            <div className="text-sm text-gray-500 font-medium pl-2">
                第 {page} 页 / 共 {totalPages} 页 · {total} 条
            </div>
            <div className="flex gap-2">
                <button
                    aria-label="上一页"
                    disabled={page <= 1}
                    onClick={() => setPage(p => p - 1)}
                    className="p-2.5 rounded-xl bg-gray-50 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed text-gray-600 transition-colors"
                >
                    <ChevronLeft className="w-5 h-5" />
                </button>
                <button
                    aria-label="下一页"
                    disabled={page >= totalPages}
                    onClick={() => setPage(p => p + 1)}
                    className="p-2.5 rounded-xl bg-gray-50 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed text-gray-600 transition-colors"
                >
                    <ChevronRight className="w-5 h-5" />
                </button>
            </div>
        </div>
      </div>

      {/* 订单详情弹窗 - 使用 Portal */}
      {showDetailModal && selectedOrder && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <div className="flex items-center justify-between w-full">
                <h3 className="text-2xl font-extrabold text-gray-900">订单详情</h3>
                <button
                  onClick={closeDetail}
                  className="p-2 bg-gray-100 rounded-full hover:bg-gray-200 transition-colors"
                >
                  <X className="w-5 h-5 text-gray-600" />
                </button>
              </div>
            </div>

            <div className="modal-body space-y-6">
              {/* Order Info */}
              <div className="space-y-4">
                <h4 className="text-lg font-bold text-gray-800">订单信息</h4>
                <div className="grid grid-cols-2 gap-4 p-4 bg-gray-50 rounded-xl">
                  <div>
                    <div className="text-xs text-gray-500 mb-1">订单号</div>
                    <div className="font-mono text-sm font-bold text-gray-900">{selectedOrder.order_id}</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 mb-1">状态</div>
                    <StatusBadge status={selectedOrder.status} />
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 mb-1">实付金额</div>
                    <div className="text-lg font-extrabold text-gray-900">{orderAmountLabel(selectedOrder)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 mb-1">数量</div>
                    <div className="font-bold text-gray-900">{selectedOrder.quantity}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-xs text-gray-500 mb-1">订单时间</div>
                    <div className="text-sm font-medium text-gray-700">{orderTimeLabel(selectedOrder)}</div>
                  </div>
                  <div className="col-span-2 border-t border-gray-200 pt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <div className="text-xs text-gray-500 mb-1">平台原始状态</div>
                      <div className="text-sm font-medium text-gray-700">
                        {selectedOrder.platform_status_text || selectedOrder.platform_status_code || '尚未取得'}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 mb-1">同步来源</div>
                      <div className="text-sm font-medium text-gray-700">{selectedOrder.status_source || '历史记录'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 mb-1">最近同步</div>
                      <div className="text-sm font-medium text-gray-700">{selectedOrder.status_synced_at || '尚未同步'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 mb-1">最后同步结果</div>
                      <div className={`text-sm font-medium ${selectedOrder.last_sync_error ? 'text-red-600' : 'text-green-700'}`}>
                        {selectedOrder.last_sync_error || '无错误'}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Item Info */}
              <div className="space-y-4">
                <h4 className="text-lg font-bold text-gray-800">商品信息</h4>
                <div className="p-4 bg-gray-50 rounded-xl flex items-center gap-4">
                  <OrderItemImage
                    orderId={selectedOrder.order_id}
                    directSrc={selectedOrder.item_image}
                    alt={selectedOrder.item_title || '订单商品图片'}
                    className="w-20 h-20 rounded-xl object-cover border border-gray-200"
                    fallback={(
                      <div className="w-20 h-20 rounded-xl border border-gray-200 bg-white flex items-center justify-center text-gray-300">
                        <PackageCheck className="w-7 h-7" />
                      </div>
                    )}
                  />
                  <div className="flex-1">
                    <div className="font-bold text-gray-900 mb-1">
                      {getItemNameById(selectedOrder.item_id, selectedOrder.item_title)}
                    </div>
                    <div className="text-sm text-gray-500">商品ID: {selectedOrder.item_id}</div>
                    {selectedOrder.item_price && (
                      <div className="text-sm text-gray-500 mt-1">标价: ¥{selectedOrder.item_price}</div>
                    )}
                    {selectedOrder.item_identity === 'catalog_fallback' && (
                      <div className="text-xs text-amber-600 mt-1">展示信息来自商品目录兜底，非成交时快照</div>
                    )}
                  </div>
                </div>
              </div>

              {/* Buyer Info */}
              <div className="space-y-4">
                <h4 className="text-lg font-bold text-gray-800">买家信息</h4>
                <div className="p-4 bg-gray-50 rounded-xl space-y-3">
                  <div className="flex items-center gap-3">
                    <BuyerAvatar
                      src={selectedOrder.buyer_avatar_url}
                      className="w-10 h-10 rounded-full object-cover border border-gray-200 flex-shrink-0"
                    />
                    <div>
                      <div className="font-bold text-gray-900">
                        {selectedOrder.buyer_display_name || selectedOrder.buyer_id || '待补充'}
                      </div>
                      <div className="text-xs text-gray-500">
                        {selectedOrder.buyer_display_name
                          ? `买家ID: ${selectedOrder.buyer_id}`
                          : (BUYER_IDENTITY_LABELS[selectedOrder.buyer_identity || ''] || `买家ID: ${selectedOrder.buyer_id || '未知'}`)}
                      </div>
                    </div>
                  </div>
                  {detailLoading && (
                    <div className="text-xs text-gray-400">正在加载收货信息…</div>
                  )}
                  {selectedOrder.receiver_name && (
                    <div>
                      <div className="text-xs text-gray-500 mb-1">收货人</div>
                      <div className="font-medium text-gray-700">{selectedOrder.receiver_name}</div>
                    </div>
                  )}
                  {selectedOrder.receiver_phone && (
                    <div>
                      <div className="text-xs text-gray-500 mb-1">联系电话</div>
                      <div className="font-mono text-sm text-gray-700">{selectedOrder.receiver_phone}</div>
                    </div>
                  )}
                  {selectedOrder.receiver_address && (
                    <div>
                      <div className="text-xs text-gray-500 mb-1">收货地址</div>
                      <div className="text-sm text-gray-700">{selectedOrder.receiver_address}</div>
                    </div>
                  )}
                </div>
              </div>

              {/* 更多操作 */}
              <div className="space-y-4">
                <h4 className="text-lg font-bold text-gray-800">更多操作</h4>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => {
                      closeDetail();
                      handleEdit(selectedOrder);
                    }}
                    className="px-4 py-2.5 min-h-11 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-800 text-sm font-bold transition-colors flex items-center gap-2"
                  >
                    <Edit className="w-4 h-4" /> 编辑订单
                  </button>
                  <button
                    onClick={() => handleSyncSingle(selectedOrder.order_id)}
                    disabled={syncingOrderId === selectedOrder.order_id}
                    className="px-4 py-2.5 min-h-11 rounded-xl bg-green-50 hover:bg-green-100 text-green-700 text-sm font-bold transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    <RefreshCw className={`w-4 h-4 ${syncingOrderId === selectedOrder.order_id ? 'animate-spin' : ''}`} /> 同步此订单
                  </button>
                  <a
                    href={`https://www.goofish.com/order-detail?orderId=${selectedOrder.order_id}&role=seller`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-4 py-2.5 min-h-11 rounded-xl bg-amber-50 hover:bg-amber-100 text-amber-700 text-sm font-bold transition-colors flex items-center gap-2"
                  >
                    <ExternalLink className="w-4 h-4" /> 闲鱼详情
                  </a>
                </div>
                {/* 危险区：删除需二次确认 */}
                <div className="border border-red-100 bg-red-50/50 rounded-xl p-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-xs text-red-600 font-medium">删除订单后无法恢复。</div>
                  {confirmingDelete ? (
                    <div className="flex gap-2">
                      <button
                        onClick={() => setConfirmingDelete(false)}
                        className="px-4 py-2 min-h-11 rounded-xl bg-white border border-gray-200 text-gray-700 text-sm font-bold"
                      >
                        取消
                      </button>
                      <button
                        onClick={() => handleDelete(selectedOrder.order_id)}
                        disabled={deletingOrderId === selectedOrder.order_id}
                        className="px-4 py-2 min-h-11 rounded-xl bg-red-600 hover:bg-red-700 text-white text-sm font-bold disabled:opacity-50"
                      >
                        确认删除
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmingDelete(true)}
                      className="px-4 py-2 min-h-11 rounded-xl bg-white border border-red-200 text-red-600 hover:bg-red-50 text-sm font-bold flex items-center gap-2"
                    >
                      <Trash2 className="w-4 h-4" /> 删除订单
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={closeDetail}
                  className="flex-1 px-6 py-3 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-800 font-bold transition-colors"
                >
                  关闭
                </button>
                {selectedOrder.status === 'pending_ship' && (
                  <button
                    onClick={() => {
                      closeDetail();
                      handleShip(selectedOrder.order_id);
                    }}
                    className="flex-1 px-6 py-3 rounded-xl ios-btn-primary font-bold shadow-lg shadow-yellow-200"
                  >
                    立即发货
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Import Modal - 使用 Portal */}
      {showImportModal && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <div className="flex items-center justify-between w-full">
                <h3 className="text-2xl font-extrabold text-gray-900">插入订单</h3>
                <button
                  onClick={() => setShowImportModal(false)}
                  className="p-2 bg-gray-100 rounded-full hover:bg-gray-200 transition-colors"
                >
                  <X className="w-5 h-5 text-gray-600" />
                </button>
              </div>
            </div>

            <div className="modal-body space-y-5">
              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">选择Excel文件</label>
                <input
                  type="file"
                  accept=".xlsx"
                  onChange={(e) => selectImportFile(e.target.files?.[0] || null)}
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                />
                <p className="text-xs text-gray-500 mt-2">仅支持 .xlsx 格式</p>
              </div>
              {importFile && (
                <div className="p-3 bg-blue-50 rounded-xl">
                  <div className="flex items-center gap-2">
                    <Upload className="w-4 h-4 text-blue-600" />
                    <span className="text-sm font-medium text-blue-900">{importFile.name}</span>
                  </div>
                </div>
              )}
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={() => setShowImportModal(false)}
                  className="flex-1 px-6 py-3 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-800 font-bold transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleImportOrders}
                  disabled={!importFile}
                  className="flex-1 px-6 py-3 rounded-xl ios-btn-primary font-bold shadow-lg shadow-yellow-200 disabled:opacity-50"
                >
                  导入订单
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Ship Modal - 发货方式选择 */}
      {showShipModal && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container" style={{ maxWidth: '480px' }}>
            <div className="modal-header">
              <div className="flex items-center justify-between w-full">
                <h3 className="text-2xl font-extrabold text-gray-900">立即发货</h3>
                <button
                  onClick={() => { setShowShipModal(false); setShipResult(null); }}
                  className="p-2 bg-gray-100 rounded-full hover:bg-gray-200 transition-colors"
                >
                  <X className="w-5 h-5 text-gray-600" />
                </button>
              </div>
            </div>

            <div className="modal-body space-y-4">
              <p className="text-sm text-gray-600">请选择发货方式：</p>

              {/* 选项A: 仅修改发货状态 */}
              <button
                onClick={() => executeShip('status_only')}
                disabled={shipLoading}
                className="w-full text-left p-4 rounded-xl border-2 border-gray-200 hover:border-gray-400 hover:bg-gray-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Truck className="w-5 h-5 text-blue-600" />
                  </div>
                  <div>
                    <div className="font-bold text-gray-900 text-sm">仅修改闲鱼发货状态</div>
                    <div className="text-xs text-gray-500 mt-1 leading-relaxed">
                      不实际扣除或发送卡券，仅在闲鱼平台将订单标记为"已发货"。
                      适用于已经给客户发过货、只是忘记在闲鱼修改状态的情况。
                    </div>
                  </div>
                </div>
              </button>

              {/* 选项B: 完整发货流程 */}
              <button
                onClick={() => executeShip('full_delivery')}
                disabled={shipLoading}
                className="w-full text-left p-4 rounded-xl border-2 border-gray-200 hover:border-[#FFE815] hover:bg-yellow-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-xl bg-yellow-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <PackageCheck className="w-5 h-5 text-yellow-700" />
                  </div>
                  <div>
                    <div className="font-bold text-gray-900 text-sm">完整发货（匹配卡券并发送）</div>
                    <div className="text-xs text-gray-500 mt-1 leading-relaxed">
                      自动匹配发货规则、获取卡券、发送卡券信息给买家，并修改发货状态。
                      适用于订单既没有发送卡券给买家、也没有修改发货状态的情况。
                    </div>
                  </div>
                </div>
              </button>

              {/* 加载状态 */}
              {shipLoading && (
                <div className="flex items-center justify-center gap-2 py-3">
                  <RefreshCw className="w-4 h-4 animate-spin text-gray-500" />
                  <span className="text-sm text-gray-500">正在处理中...</span>
                </div>
              )}

              {/* 结果显示 */}
              {shipResult && (
                <div className={`p-3 rounded-xl text-sm ${shipResult.success ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
                  {shipResult.success ? '✓ ' : '✗ '}{shipResult.message}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                onClick={() => { setShowShipModal(false); setShipResult(null); }}
                className="w-full px-6 py-3 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-800 font-bold transition-colors"
              >
                {shipResult?.success ? '完成' : '取消'}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Edit Modal - 使用 Portal */}
      {showEditModal && editingOrder && createPortal(
        <div className="modal-overlay-centered">
          <div className="modal-container">
            <div className="modal-header">
              <div className="flex items-center justify-between w-full">
                <h3 className="text-2xl font-extrabold text-gray-900">编辑订单</h3>
                <button
                  onClick={() => setShowEditModal(false)}
                  className="p-2 bg-gray-100 rounded-full hover:bg-gray-200 transition-colors"
                >
                  <X className="w-5 h-5 text-gray-600" />
                </button>
              </div>
            </div>

            <div className="modal-body space-y-5">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">订单号</label>
                  <input
                    type="text"
                    value={editingOrder.order_id}
                    disabled
                    className="w-full ios-input px-4 py-3 rounded-xl bg-gray-50 text-gray-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">订单状态</label>
                  <select
                    value={editingOrder.status}
                    onChange={(e) => setEditingOrder({ ...editingOrder, status: e.target.value as OrderStatus })}
                    className="w-full ios-input px-4 py-3 rounded-xl"
                  >
                    <option value="processing">处理中</option>
                    <option value="pending_ship">待发货</option>
                    <option value="shipped">已发货</option>
                    <option value="completed">已完成</option>
                    <option value="cancelled">已取消</option>
                    <option value="refunding">退款中</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">买家ID</label>
                  <input
                    type="text"
                    value={editingOrder.buyer_id}
                    onChange={(e) => setEditingOrder({ ...editingOrder, buyer_id: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl"
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">实付金额</label>
                  <input
                    type="number"
                    value={editingOrder.amount}
                    onChange={(e) => setEditingOrder({ ...editingOrder, amount: parseFloat(e.target.value) })}
                    className="w-full ios-input px-4 py-3 rounded-xl"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">收货人</label>
                  <input
                    type="text"
                    value={editingOrder.receiver_name || ''}
                    onChange={(e) => setEditingOrder({ ...editingOrder, receiver_name: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl"
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-2">联系电话</label>
                  <input
                    type="text"
                    value={editingOrder.receiver_phone || ''}
                    onChange={(e) => setEditingOrder({ ...editingOrder, receiver_phone: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">收货地址</label>
                <textarea
                  value={editingOrder.receiver_address || ''}
                  onChange={(e) => setEditingOrder({ ...editingOrder, receiver_address: e.target.value })}
                  rows={2}
                  className="w-full ios-input px-4 py-3 rounded-xl resize-none"
                />
              </div>

              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">商品标题</label>
                <input
                  type="text"
                  value={editingOrder.item_title || ''}
                  onChange={(e) => setEditingOrder({ ...editingOrder, item_title: e.target.value })}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                />
              </div>
            </div>

            <div className="modal-footer">
              <div className="flex gap-3 w-full">
                <button
                  onClick={() => setShowEditModal(false)}
                  className="flex-1 px-6 py-3 rounded-xl font-bold bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveEdit}
                  className="flex-1 ios-btn-primary px-6 py-3 rounded-xl font-bold flex items-center justify-center gap-2"
                >
                  <Save className="w-4 h-4" />
                  保存更改
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

export default OrderList;
