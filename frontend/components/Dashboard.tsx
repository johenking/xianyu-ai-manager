import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  DollarSign,
  ExternalLink,
  Package,
  PackageCheck,
  RefreshCw,
  ShoppingCart,
  TrendingUp,
  Users,
} from 'lucide-react';
import type { DashboardSummary, Order, OrderStatus } from '../types';
import { getDashboardSummary, getValidOrders } from '../services/api';

const DashboardCharts = lazy(() => import('./DashboardCharts'));

type TimeRange = 'today' | 'yesterday' | '3days' | '7days' | '30days' | 'custom';

const TIME_RANGES: Array<{ key: TimeRange; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: '3days', label: '三天内' },
  { key: '7days', label: '7天内' },
  { key: '30days', label: '一个月内' },
  { key: 'custom', label: '自定义' },
];

const STATUS_STYLES: Record<string, string> = {
  processing: 'bg-yellow-100 text-yellow-800',
  pending_ship: 'bg-[#FFE815] text-black',
  shipped: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  cancelled: 'bg-gray-100 text-gray-500',
  refunding: 'bg-red-100 text-red-600',
};

const STATUS_LABELS: Record<string, string> = {
  processing: '处理中',
  pending_ship: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunding: '退款中',
};

const StatusBadge: React.FC<{ status: OrderStatus }> = ({ status }) => (
  <span className={`inline-flex rounded-md px-2.5 py-1 text-xs font-bold ${STATUS_STYLES[status] || STATUS_STYLES.cancelled}`}>
    {STATUS_LABELS[status] || status}
  </span>
);

const StatCard: React.FC<{
  title: string;
  value: string | number;
  icon: React.ElementType;
  colorClass: string;
  trend?: string;
}> = ({ title, value, icon: Icon, colorClass, trend }) => (
  <div className="ios-card flex min-h-40 flex-col justify-between rounded-2xl border border-gray-100 p-5 sm:p-6">
    <div className="flex items-start justify-between">
      <div className={`flex h-12 w-12 items-center justify-center rounded-xl ${colorClass}`}><Icon className="h-5 w-5" /></div>
      {trend && <span className="flex items-center gap-1 rounded-full bg-[#FFE815] px-2.5 py-1 text-xs font-bold"><TrendingUp className="h-3 w-3" />{trend}</span>}
    </div>
    <div>
      <h3 className="text-3xl font-extrabold text-gray-900">{value}</h3>
      <p className="mt-1 text-sm font-medium text-gray-500">{title}</p>
    </div>
  </div>
);

const Dashboard: React.FC = () => {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>('7days');
  const [customStartDate, setCustomStartDate] = useState('');
  const [customEndDate, setCustomEndDate] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [validOrders, setValidOrders] = useState<Order[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const summaryRequestGeneration = useRef(0);

  const loadSummary = useCallback(async (range: TimeRange = timeRange) => {
    if (range === 'custom' && (!customStartDate || !customEndDate)) return;
    const generation = summaryRequestGeneration.current + 1;
    summaryRequestGeneration.current = generation;
    setLoading(true);
    setError('');
    try {
      const result = await getDashboardSummary({
        range,
        ...(range === 'custom' ? { start_date: customStartDate, end_date: customEndDate } : {}),
      });
      if (summaryRequestGeneration.current === generation) setSummary(result);
    } catch (loadError) {
      if (summaryRequestGeneration.current === generation) {
        setSummary(null);
        setError(loadError instanceof Error ? loadError.message : '仪表盘加载失败');
      }
    } finally {
      if (summaryRequestGeneration.current === generation) setLoading(false);
    }
  }, [customEndDate, customStartDate, timeRange]);

  useEffect(() => {
    if (timeRange !== 'custom') void loadSummary(timeRange);
  }, [loadSummary, timeRange]);

  useEffect(() => {
    if (!summary) return undefined;
    setOrdersLoading(true);
    let cancelled = false;
    const loadOrders = () => {
      void getValidOrders({
        start_date: summary.range.start_date,
        end_date: summary.range.end_date,
      }).then((orders) => {
        if (!cancelled) setValidOrders(orders);
      }).catch(() => {
        if (!cancelled) setValidOrders([]);
      }).finally(() => {
        if (!cancelled) setOrdersLoading(false);
      });
    };
    const idleWindow = window as Window & {
      requestIdleCallback?: (callback: () => void) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    const handle = idleWindow.requestIdleCallback
      ? idleWindow.requestIdleCallback(loadOrders)
      : window.setTimeout(loadOrders, 0);
    return () => {
      cancelled = true;
      if (idleWindow.cancelIdleCallback) idleWindow.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, [summary]);

  const trend = useMemo(() => {
    if (!summary) return undefined;
    const current = summary.current.revenue_stats.total_amount;
    const previous = summary.previous.revenue_stats.total_amount;
    if (previous === 0) return current > 0 ? '+100%' : '0%';
    const value = ((current - previous) / previous) * 100;
    return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
  }, [summary]);

  const filteredOrders = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return validOrders;
    return validOrders.filter((order) => [order.order_id, order.item_id, order.buyer_id]
      .some((value) => String(value || '').toLowerCase().includes(term)));
  }, [searchTerm, validOrders]);

  if (loading && !summary) {
    return <div className="flex min-h-[50vh] items-center justify-center text-gray-400" role="status" aria-label="仪表盘加载中"><Activity className="h-8 w-8 animate-spin text-[#D6B500]" /></div>;
  }

  if (error || !summary) {
    return (
      <div className="mx-auto flex min-h-[50vh] max-w-lg flex-col items-center justify-center text-center">
        <AlertCircle className="mb-4 h-10 w-10 text-red-500" />
        <h2 className="text-xl font-bold text-gray-900">仪表盘暂时不可用</h2>
        <p className="mt-2 text-sm text-gray-500">{error || '未能读取统计数据'}</p>
        <button type="button" onClick={() => void loadSummary(timeRange)} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-bold text-white"><RefreshCw className="h-4 w-4" />重试</button>
      </div>
    );
  }

  const isEmpty = summary.stats.total_cookies === 0
    && summary.current.revenue_stats.total_orders === 0;

  return (
    <div className="space-y-6 animate-fade-in">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-2xl font-extrabold text-gray-900 sm:text-3xl">运营概览</h2>
          <p className="mt-1 text-sm text-gray-500">{summary.scope === 'system' ? '系统业务汇总' : '你的闲鱼业务数据'}</p>
        </div>
        <span className="w-fit rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-600">
          {summary.range.start_date} 至 {summary.range.end_date}
        </span>
      </header>

      <div className="flex flex-wrap gap-2 rounded-xl bg-gray-100/70 p-2">
        {TIME_RANGES.map((option) => (
          <button key={option.key} type="button" onClick={() => setTimeRange(option.key)} className={`rounded-lg px-4 py-2 text-sm font-bold ${timeRange === option.key ? 'bg-[#FFE815] text-black shadow-sm' : 'bg-white text-gray-600'}`}>
            {option.label}
          </button>
        ))}
        {timeRange === 'custom' && (
          <div className="flex flex-wrap items-center gap-2">
            <input aria-label="开始日期" type="date" value={customStartDate} onChange={(event) => setCustomStartDate(event.target.value)} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm" />
            <input aria-label="结束日期" type="date" value={customEndDate} onChange={(event) => setCustomEndDate(event.target.value)} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm" />
            <button type="button" onClick={() => void loadSummary('custom')} disabled={!customStartDate || !customEndDate} className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-bold text-white disabled:opacity-40">应用</button>
          </div>
        )}
      </div>

      {isEmpty && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-white px-5 py-6 text-center">
          <p className="font-bold text-gray-800">还没有经营数据</p>
          <p className="mt-1 text-sm text-gray-500">添加闲鱼账号后，订单和营收会显示在这里。</p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard title="累计营收 (CNY)" value={`¥${summary.current.revenue_stats.total_amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`} icon={DollarSign} colorClass="bg-yellow-100 text-yellow-700" trend={trend} />
        <StatCard title="活跃账号 / 总数" value={`${summary.stats.active_cookies} / ${summary.stats.total_cookies}`} icon={Users} colorClass="bg-blue-100 text-blue-700" />
        <StatCard title="订单数" value={summary.current.revenue_stats.total_orders.toLocaleString()} icon={ShoppingCart} colorClass="bg-orange-100 text-orange-700" />
        <StatCard title="库存卡密" value={summary.stats.total_cards} icon={Package} colorClass="bg-rose-100 text-rose-700" />
      </div>

      <Suspense fallback={<div className="flex h-64 items-center justify-center rounded-2xl bg-white text-sm text-gray-400">图表加载中...</div>}>
        <DashboardCharts analytics={summary.current} itemNames={summary.item_names} />
      </Suspense>

      <section className="ios-card overflow-hidden rounded-2xl bg-white">
        <div className="flex flex-col gap-3 border-b border-gray-100 bg-gray-50/70 p-5 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="font-bold text-gray-900">参与统计的订单</h3>
          <input aria-label="搜索统计订单" placeholder="搜索订单号、商品或买家" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-yellow-400 sm:w-64" />
        </div>
        <div className="max-h-[420px] overflow-auto">
          {ordersLoading ? (
            <div className="flex items-center justify-center py-16 text-sm text-gray-400"><Activity className="mr-2 h-5 w-5 animate-spin" />加载订单明细...</div>
          ) : filteredOrders.length === 0 ? (
            <div className="py-16 text-center text-sm text-gray-400">暂无订单明细</div>
          ) : (
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="sticky top-0 bg-white text-xs text-gray-400"><tr><th className="px-5 py-3">订单</th><th className="px-5 py-3">买家</th><th className="px-5 py-3">金额</th><th className="px-5 py-3">状态</th><th className="px-5 py-3 text-right">详情</th></tr></thead>
              <tbody className="divide-y divide-gray-100">
                {filteredOrders.map((order) => (
                  <tr key={order.order_id}>
                    <td className="px-5 py-4"><div className="flex items-center gap-3"><PackageCheck className="h-8 w-8 rounded-lg bg-gray-100 p-1.5 text-gray-400" /><div><p className="font-bold text-gray-900">{order.item_title || summary.item_names[order.item_id] || order.item_id || '未知商品'}</p><p className="mt-0.5 font-mono text-xs text-gray-400">{order.order_id}</p></div></div></td>
                    <td className="px-5 py-4 text-gray-700">{order.buyer_id}</td>
                    <td className="px-5 py-4 font-bold text-gray-900">¥{order.amount || '0.00'}</td>
                    <td className="px-5 py-4"><StatusBadge status={order.status || order.order_status || 'unknown'} /></td>
                    <td className="px-5 py-4 text-right"><a href={`https://www.goofish.com/order-detail?orderId=${order.order_id}&role=seller`} target="_blank" rel="noopener noreferrer" title="查看闲鱼订单" className="inline-flex rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-900"><ExternalLink className="h-4 w-4" /></a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
};

export default Dashboard;
