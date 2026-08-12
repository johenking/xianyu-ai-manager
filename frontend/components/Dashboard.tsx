import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ExternalLink,
  PackageCheck,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import type { DashboardSummary, Order } from '../types';
import { getDashboardSummary, getValidOrders } from '../services/api';
import {
  PANEL_CLASS,
  StatusBadge,
  compareOf,
  fillDailySeries,
  formatCount,
  formatMoney,
} from './ui/dashboardParts';

const DashboardCharts = lazy(() => import('./DashboardCharts'));
const BusinessInsights = lazy(() => import('./BusinessInsights'));
// hero 内嵌趋势图与其余图表同 chunk，保持 recharts 只进懒加载分包
const HeroTrend = lazy(() => import('./DashboardCharts').then((module) => ({ default: module.HeroTrend })));

type TimeRange = 'today' | 'yesterday' | '3days' | '7days' | '30days' | 'custom';

const TIME_RANGES: Array<{ key: TimeRange; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: '3days', label: '三天内' },
  { key: '7days', label: '7天内' },
  { key: '30days', label: '一个月内' },
  { key: 'custom', label: '自定义' },
];

/** 深色 hero 区的环比徽章：正=绿升、负=红降、上期为 0=中性「较上期 新增」 */
const CompareBadge: React.FC<{ current: number; previous: number }> = ({ current, previous }) => {
  const compare = compareOf(current, previous);
  if (compare.type === 'new') {
    return <span className="rounded-md border border-white/15 bg-white/10 px-2 py-1 text-xs font-bold text-slate-200">较上期 新增</span>;
  }
  if (compare.type === 'flat') {
    return <span className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs font-bold text-slate-400">持平</span>;
  }
  const upward = compare.type === 'up';
  return (
    <span className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs font-bold ${upward ? 'bg-emerald-400/15 text-emerald-300' : 'bg-red-400/15 text-red-300'}`}>
      {upward ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
      {compare.percentLabel}
    </span>
  );
};

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

  // 趋势图数据：按所选范围逐日补零，让无成交日显示为 0 而不是被跳过
  const heroPoints = useMemo(() => {
    if (!summary) return [];
    return fillDailySeries(summary.current.daily_stats, summary.range.start_date, summary.range.end_date);
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
  const currentRevenue = summary.current.revenue_stats.total_amount;
  const previousRevenue = summary.previous.revenue_stats.total_amount;

  return (
    <div className="animate-fade-in space-y-5">
      {/* 深色驾驶舱 hero：核心营收 + 次级 KPI + 补零趋势 */}
      <section className="rounded-[20px] bg-gradient-to-br from-[#0F172A] via-[#121D36] to-[#1E293B] p-6 text-white shadow-[0_14px_36px_rgba(15,23,42,0.28)] sm:p-8">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-xl font-extrabold sm:text-2xl">运营概览</h2>
            <span className="rounded-full border border-white/15 bg-white/[0.07] px-3 py-1 text-xs font-bold text-slate-300">
              {summary.range.start_date} 至 {summary.range.end_date}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {TIME_RANGES.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setTimeRange(option.key)}
                className={`rounded-lg px-3.5 py-2 text-[13px] font-bold transition-colors ${
                  timeRange === option.key
                    ? 'bg-[#FFE815] text-black shadow-sm'
                    : 'bg-white/[0.06] text-slate-300 hover:bg-white/10 hover:text-white'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {timeRange === 'custom' && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <input aria-label="开始日期" type="date" value={customStartDate} onChange={(event) => setCustomStartDate(event.target.value)} className="rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm text-white [color-scheme:dark]" />
            <span className="text-xs text-slate-400">至</span>
            <input aria-label="结束日期" type="date" value={customEndDate} onChange={(event) => setCustomEndDate(event.target.value)} className="rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm text-white [color-scheme:dark]" />
            <button type="button" onClick={() => void loadSummary('custom')} disabled={!customStartDate || !customEndDate} className="rounded-lg bg-[#FFE815] px-4 py-2 text-sm font-bold text-black disabled:opacity-40">应用</button>
          </div>
        )}

        <div className="mt-7 grid grid-cols-1 items-end gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div>
            <p className="text-xs font-bold tracking-wide text-slate-400">累计营收 (CNY)</p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <span className="text-[44px] font-extrabold leading-none tracking-tight sm:text-[52px]">
                ¥{formatMoney(currentRevenue)}
              </span>
              <CompareBadge current={currentRevenue} previous={previousRevenue} />
            </div>
            <p className="mt-2 text-xs text-slate-400">上期 ¥{formatMoney(previousRevenue)}</p>

            <div className="mt-7 flex divide-x divide-white/10">
              <div className="pr-5 sm:pr-7">
                <p className="text-xs text-slate-400">活跃账号 / 总数</p>
                <p className="mt-1.5 text-xl font-extrabold">{summary.stats.active_cookies} / {summary.stats.total_cookies}</p>
              </div>
              <div className="px-5 sm:px-7">
                <p className="text-xs text-slate-400">订单数</p>
                <p className="mt-1.5 text-xl font-extrabold">{formatCount(summary.current.revenue_stats.total_orders)}</p>
              </div>
              <div className="pl-5 sm:pl-7">
                <p className="text-xs text-slate-400">库存卡密</p>
                <p className="mt-1.5 text-xl font-extrabold">{formatCount(summary.stats.total_cards)}</p>
              </div>
            </div>
          </div>

          <div className="min-w-0">
            <p className="mb-2 text-xs font-bold tracking-wide text-slate-400">营收趋势 · 所选周期每日销售额</p>
            <Suspense fallback={<div className="h-[180px] animate-pulse rounded-xl bg-white/[0.04]" />}>
              <HeroTrend points={heroPoints} />
            </Suspense>
          </div>
        </div>
      </section>

      {isEmpty && (
        <div className={`${PANEL_CLASS} px-5 py-6 text-center`}>
          <p className="font-bold text-gray-800">还没有经营数据</p>
          <p className="mt-1 text-sm text-gray-500">添加闲鱼账号后，订单和营收会显示在这里。</p>
        </div>
      )}

      <Suspense fallback={<div className={`${PANEL_CLASS} flex h-[420px] items-center justify-center text-sm text-gray-400`}>图表加载中...</div>}>
        <DashboardCharts analytics={summary.current} previous={summary.previous} itemNames={summary.item_names} />
      </Suspense>

      {!isEmpty && (
        <Suspense fallback={<div className={`${PANEL_CLASS} flex h-[520px] items-center justify-center text-sm text-gray-400`}>经营分析加载中...</div>}>
          <BusinessInsights range={{ start_date: summary.range.start_date, end_date: summary.range.end_date }} />
        </Suspense>
      )}

      <section className={`${PANEL_CLASS} overflow-hidden`}>
        <div className="flex flex-col gap-3 border-b border-gray-100 bg-gray-50/70 p-5 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-[15px] font-bold text-gray-900">参与统计的订单</h3>
          <input aria-label="搜索统计订单" placeholder="搜索订单号、商品或买家" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-gray-900 sm:w-64" />
        </div>
        <div className="max-h-[420px] overflow-auto">
          {ordersLoading ? (
            <div className="flex items-center justify-center py-16 text-sm text-gray-400"><Activity className="mr-2 h-5 w-5 animate-spin" />加载订单明细...</div>
          ) : filteredOrders.length === 0 ? (
            <div className="py-16 text-center text-sm text-gray-400">暂无订单明细</div>
          ) : (
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="sticky top-0 bg-white text-xs text-gray-400"><tr><th className="px-5 py-3 font-medium">订单</th><th className="px-5 py-3 font-medium">买家</th><th className="px-5 py-3 font-medium">金额</th><th className="px-5 py-3 font-medium">状态</th><th className="px-5 py-3 text-right font-medium">详情</th></tr></thead>
              <tbody className="divide-y divide-gray-100">
                {filteredOrders.map((order) => (
                  <tr key={order.order_id} className="hover:bg-gray-50/60">
                    <td className="px-5 py-4"><div className="flex items-center gap-3"><PackageCheck className="h-8 w-8 rounded-lg bg-gray-100 p-1.5 text-gray-400" /><div className="min-w-0"><p className="truncate font-bold text-gray-900">{order.item_title || summary.item_names[order.item_id] || order.item_id || '未知商品'}</p><p className="mt-0.5 font-mono text-xs text-gray-400">{order.order_id}</p></div></div></td>
                    <td className="px-5 py-4 text-gray-700">{order.buyer_id}</td>
                    <td className="px-5 py-4 font-bold tabular-nums text-gray-900">¥{order.amount || '0.00'}</td>
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
