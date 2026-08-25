import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import NumberFlow from '@number-flow/react';
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
  fillHourlySeries,
  formatCount,
  formatMoney,
  selectTrendPoints,
} from './ui/dashboardParts';

const DashboardCharts = lazy(() => import('./DashboardCharts'));
const BusinessInsights = lazy(() => import('./BusinessInsights'));
// hero 内嵌趋势图与其余图表同 chunk，保持 recharts 只进懒加载分包
const HeroTrend = lazy(() => import('./DashboardCharts').then((module) => ({ default: module.HeroTrend })));

type TimeRange = 'today' | 'yesterday' | '3days' | '7days' | '30days' | 'custom';

const TIME_RANGES: Array<{ key: TimeRange; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: '3days', label: '近3天' },
  { key: '7days', label: '近7天' },
  { key: '30days', label: '近30天' },
  { key: 'custom', label: '自定义' },
];

const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';

export const getShanghaiClock = (value: Date = new Date()): { date: string; hour: number } => {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(value);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value || '00';
  return { date: `${get('year')}-${get('month')}-${get('day')}`, hour: Number(get('hour')) };
};

const compactDate = (value: string): string => {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return match ? `${Number(match[2])}月${Number(match[3])}日` : value;
};

const rangeDateLabel = (timeRange: TimeRange, start: string, end: string): string => {
  if (timeRange === 'today') return `今天 · ${compactDate(end)}`;
  if (timeRange === 'yesterday') return `昨天 · ${compactDate(end)}`;
  if (start === end) return compactDate(start);
  return `${compactDate(start)} - ${compactDate(end)}`;
};

const revenueTitle = (timeRange: TimeRange, start: string, end: string): string => {
  if (timeRange === 'today') return '今日营收';
  if (timeRange === 'yesterday') return '昨日营收';
  if (start === end || timeRange === 'custom') return '所选日期营收';
  return '所选周期营收';
};

const comparisonLabel = (timeRange: TimeRange): string => {
  if (timeRange === 'today') return '昨日';
  if (timeRange === 'yesterday') return '前日';
  if (timeRange === 'custom') return '前一日';
  return '上一周期';
};

export const DASHBOARD_REFRESH_MS = 15_000;

const NUMBER_FLOW_TIMING: EffectTiming = {
  duration: 650,
  easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
};
const NUMBER_FLOW_OPACITY_TIMING: EffectTiming = { duration: 180, easing: 'ease-out' };
const NUMBER_FLOW_MOTION = {
  locales: 'zh-CN',
  transformTiming: NUMBER_FLOW_TIMING,
  spinTiming: NUMBER_FLOW_TIMING,
  opacityTiming: NUMBER_FLOW_OPACITY_TIMING,
  respectMotionPreference: true,
} as const;
const MONEY_FORMAT: Intl.NumberFormatOptions = {
  style: 'currency',
  currency: 'CNY',
  currencyDisplay: 'narrowSymbol',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
};
const COUNT_FORMAT: Intl.NumberFormatOptions = { maximumFractionDigits: 0 };

type SummaryLoadMode = 'foreground' | 'background';

/** 深色 hero 区的环比徽章：正=绿升、负=红降、上期为 0=中性「较上期 新增」 */
const CompareBadge: React.FC<{ current: number; previous: number; periodLabel: string }> = ({ current, previous, periodLabel }) => {
  const compare = compareOf(current, previous);
  if (compare.type === 'new') {
    return <span className="rounded-md border border-white/15 bg-white/10 px-2 py-1 text-xs font-bold text-slate-200">较{periodLabel}新增</span>;
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
  const [timeRange, setTimeRange] = useState<TimeRange>('today');
  const [customStartDate, setCustomStartDate] = useState('');
  const [customEndDate, setCustomEndDate] = useState('');
  const [dashboardNow, setDashboardNow] = useState(() => new Date());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDelayed, setRefreshDelayed] = useState(false);
  const [error, setError] = useState('');
  const [validOrders, setValidOrders] = useState<Order[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const summaryRequestGeneration = useRef(0);
  const summaryAbortController = useRef<AbortController | null>(null);
  const summaryFingerprint = useRef('');

  const loadSummary = useCallback(async (
    range: TimeRange,
    mode: SummaryLoadMode = 'foreground',
  ): Promise<boolean> => {
    if (range === 'custom' && (!customStartDate || !customEndDate)) return false;
    if (mode === 'background' && summaryAbortController.current) return false;

    summaryAbortController.current?.abort();
    const controller = new AbortController();
    summaryAbortController.current = controller;
    const generation = summaryRequestGeneration.current + 1;
    summaryRequestGeneration.current = generation;
    if (mode === 'foreground') {
      // A range change supersedes a background refresh. Clear its visual state
      // immediately because the aborted request's generation is stale.
      setRefreshing(false);
      setLoading(true);
      setError('');
      setRefreshDelayed(false);
    } else {
      setRefreshing(true);
    }
    try {
      const result = await getDashboardSummary({
        range,
        ...(range === 'custom' ? { start_date: customStartDate, end_date: customEndDate } : {}),
      }, controller.signal);
      if (summaryRequestGeneration.current !== generation || controller.signal.aborted) return false;

      setDashboardNow(new Date());
      const fingerprint = JSON.stringify(result);
      if (summaryFingerprint.current !== fingerprint) {
        summaryFingerprint.current = fingerprint;
        setSummary(result);
      }
      setRefreshDelayed(false);
      return true;
    } catch (loadError) {
      if (controller.signal.aborted || (loadError instanceof Error && loadError.name === 'AbortError')) {
        return false;
      }
      if (summaryRequestGeneration.current === generation) {
        if (mode === 'foreground') {
          summaryFingerprint.current = '';
          setSummary(null);
          setError(loadError instanceof Error ? loadError.message : '仪表盘加载失败');
        } else {
          setRefreshDelayed(true);
        }
      }
      return false;
    } finally {
      if (summaryAbortController.current === controller) summaryAbortController.current = null;
      if (summaryRequestGeneration.current === generation) {
        if (mode === 'foreground') {
          setLoading(false);
          setRefreshing(false);
        }
        else setRefreshing(false);
      }
    }
  }, [customEndDate, customStartDate]);

  useEffect(() => {
    if (timeRange !== 'custom') void loadSummary(timeRange);
  }, [loadSummary, timeRange]);

  useEffect(() => {
    if (timeRange === 'custom' && (!customStartDate || !customEndDate)) return undefined;

    let disposed = false;
    let running = false;
    let timer: number | undefined;

    const canRefresh = () => document.visibilityState === 'visible' && navigator.onLine;
    const schedule = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (!disposed && canRefresh()) timer = window.setTimeout(run, DASHBOARD_REFRESH_MS);
    };
    const run = async () => {
      if (disposed || running || !canRefresh()) return;
      running = true;
      try {
        await loadSummary(timeRange, 'background');
      } finally {
        running = false;
        schedule();
      }
    };
    const handleAvailabilityChange = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (canRefresh()) void run();
    };

    schedule();
    document.addEventListener('visibilitychange', handleAvailabilityChange);
    window.addEventListener('online', handleAvailabilityChange);
    window.addEventListener('offline', handleAvailabilityChange);
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', handleAvailabilityChange);
      window.removeEventListener('online', handleAvailabilityChange);
      window.removeEventListener('offline', handleAvailabilityChange);
    };
  }, [customEndDate, customStartDate, loadSummary, timeRange]);

  useEffect(() => () => {
    summaryRequestGeneration.current += 1;
    summaryAbortController.current?.abort();
    summaryAbortController.current = null;
  }, []);

  const orderRangeStart = summary?.range.start_date || '';
  const orderRangeEnd = summary?.range.end_date || '';
  const orderRefreshCount = summary?.current.revenue_stats.total_orders || 0;
  const orderRefreshAmount = summary?.current.revenue_stats.total_amount || 0;
  const orderRefreshStatuses = (summary?.current.status_stats || [])
    .map((entry) => `${entry.status}:${entry.count}:${entry.amount}`)
    .join('|');

  useEffect(() => {
    if (!orderRangeStart || !orderRangeEnd) return undefined;
    setOrdersLoading(true);
    let cancelled = false;
    const loadOrders = () => {
      void getValidOrders({
        start_date: orderRangeStart,
        end_date: orderRangeEnd,
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
  }, [orderRangeEnd, orderRangeStart, orderRefreshAmount, orderRefreshCount, orderRefreshStatuses]);

  const heroGranularity = summary?.trend_granularity
    || (summary?.range.start_date === summary?.range.end_date && summary?.current.hourly_stats ? 'hour' : 'day');

  const shanghaiClock = getShanghaiClock(dashboardNow);

  // 单日固定 24 个小时桶；正在进行的周期只把已发生点交给图表洞察。
  const trendPoints = useMemo(() => {
    if (!summary) return { chartPoints: [], highlightPoints: [] };
    const points = heroGranularity === 'hour' && summary.current.hourly_stats
      ? fillHourlySeries(summary.current.hourly_stats, summary.range.start_date)
      : fillDailySeries(summary.current.daily_stats, summary.range.start_date, summary.range.end_date);
    return selectTrendPoints(
      points,
      heroGranularity,
      summary.range.end_date,
      shanghaiClock.date,
      shanghaiClock.hour,
    );
  }, [heroGranularity, shanghaiClock.date, shanghaiClock.hour, summary]);
  const heroPoints = trendPoints.chartPoints;
  const highlightPoints = trendPoints.highlightPoints;

  const insightRange = useMemo(() => ({
    start_date: summary?.range.start_date || '',
    end_date: summary?.range.end_date || '',
  }), [summary?.range.end_date, summary?.range.start_date]);

  const filteredOrders = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return validOrders;
    return validOrders.filter((order) => [order.order_id, order.item_id, order.buyer_id]
      .some((value) => String(value || '').toLowerCase().includes(term)));
  }, [searchTerm, validOrders]);

  if (loading && !summary) {
    return <div className="flex min-h-[50vh] items-center justify-center text-gray-400" role="status" aria-label="仪表盘加载中"><Activity className="h-8 w-8 animate-spin motion-reduce:animate-none text-[#D6B500]" /></div>;
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
  const refreshBusy = loading || refreshing;
  const refreshLabel = refreshBusy ? '更新中' : refreshDelayed ? '更新延迟' : '实时';
  const dateLabel = rangeDateLabel(timeRange, summary.range.start_date, summary.range.end_date);
  const revenueLabel = revenueTitle(timeRange, summary.range.start_date, summary.range.end_date);
  const previousLabel = comparisonLabel(timeRange);
  const trendLabel = heroGranularity === 'hour'
    ? (timeRange === 'today' ? '今日每小时' : timeRange === 'yesterday' ? '昨日每小时' : '所选日期每小时')
    : '所选周期每日';
  const trendCutoff = summary.range.end_date === shanghaiClock.date
    ? ` · 截至 ${String(shanghaiClock.hour).padStart(2, '0')}:00`
    : '';

  return (
    <div className="space-y-5 animate-fade-in">
      <section className="overflow-hidden rounded-[16px] bg-[#111827] p-5 text-white shadow-[0_10px_24px_rgba(15,23,42,0.16)] ring-1 ring-white/5 sm:p-6 lg:p-7">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <h2 className="shrink-0 text-xl font-extrabold sm:text-2xl">运营概览</h2>
              <span className="min-w-0 truncate rounded-lg border border-white/15 bg-white/[0.07] px-2.5 py-1 text-xs font-bold text-slate-300" title={`${summary.range.start_date} 至 ${summary.range.end_date}`}>
                {dateLabel}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span
                className={`inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-bold ${refreshDelayed ? 'border-yellow-400 bg-white/10 text-yellow-300' : 'border-white/15 bg-emerald-400/15 text-emerald-300'}`}
                title={refreshDelayed ? '最近一次自动更新失败，系统会继续重试' : '数据已连接'}
                aria-live="polite"
              >
                <span className={`h-2 w-2 rounded-full ${refreshDelayed ? 'bg-yellow-300' : 'bg-emerald-400'}`} />
                {refreshLabel}
              </span>
              <button
                type="button"
                onClick={() => void loadSummary(timeRange, 'background')}
                disabled={refreshBusy || (timeRange === 'custom' && (!customStartDate || !customEndDate))}
                className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-slate-400 transition-colors duration-150 hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400 disabled:cursor-not-allowed disabled:opacity-40"
                title="立即刷新"
                aria-label="立即刷新仪表盘"
              >
                <RefreshCw className={`h-4 w-4 ${refreshBusy ? 'animate-spin motion-reduce:animate-none' : ''}`} />
              </button>
            </div>
          </div>
          <div className="-mx-1 min-w-0 overflow-x-auto px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <div className="flex min-w-max items-center gap-1.5">
              {TIME_RANGES.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setTimeRange(option.key)}
                  aria-pressed={timeRange === option.key}
                  className={`inline-flex min-h-11 items-center rounded-lg px-3.5 text-[13px] font-bold transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400 ${
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
        </div>

        {timeRange === 'custom' && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input aria-label="开始日期" type="date" value={customStartDate} onChange={(event) => setCustomStartDate(event.target.value)} className="min-h-11 rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm text-white [color-scheme:dark]" />
            <span className="text-xs text-slate-400">至</span>
            <input aria-label="结束日期" type="date" value={customEndDate} onChange={(event) => setCustomEndDate(event.target.value)} className="min-h-11 rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm text-white [color-scheme:dark]" />
            <button type="button" onClick={() => void loadSummary('custom')} disabled={!customStartDate || !customEndDate} className="min-h-11 rounded-lg bg-[#FFE815] px-4 text-sm font-bold text-black transition-opacity disabled:opacity-40">应用</button>
          </div>
        )}

        <div className="mt-5 grid grid-cols-1 items-end gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-8">
          <div className="min-w-0">
            <p className="text-xs font-bold tracking-wide text-slate-400">{revenueLabel} (CNY)</p>
            <div className="mt-2 flex min-w-0 items-end gap-2.5">
              <NumberFlow
                {...NUMBER_FLOW_MOTION}
                value={currentRevenue}
                format={MONEY_FORMAT}
                aria-label={`${revenueLabel} ¥${formatMoney(currentRevenue)}`}
                className="whitespace-nowrap tabular-nums text-[40px] font-extrabold leading-none sm:text-[48px] lg:text-[52px]"
              />
              <CompareBadge current={currentRevenue} previous={previousRevenue} periodLabel={previousLabel} />
            </div>
            <p className="mt-2 text-xs text-slate-400">{previousLabel} ¥{formatMoney(previousRevenue)}</p>

            <div className="mt-5 grid grid-cols-3 divide-x divide-white/10">
              <div className="min-w-0 pr-3 sm:pr-5">
                <p className="truncate text-[11px] text-slate-400">活跃账号 / 总数</p>
                <p className="mt-1.5 flex items-center gap-1 text-xl font-extrabold" aria-label={`活跃账号 ${summary.stats.active_cookies}，总数 ${summary.stats.total_cookies}`}>
                  <NumberFlow {...NUMBER_FLOW_MOTION} value={summary.stats.active_cookies} format={COUNT_FORMAT} aria-hidden="true" />
                  <span aria-hidden="true">/</span>
                  <NumberFlow {...NUMBER_FLOW_MOTION} value={summary.stats.total_cookies} format={COUNT_FORMAT} aria-hidden="true" />
                </p>
              </div>
              <div className="min-w-0 px-3 sm:px-5">
                <p className="text-[11px] text-slate-400">订单数</p>
                <NumberFlow
                  {...NUMBER_FLOW_MOTION}
                  value={summary.current.revenue_stats.total_orders}
                  format={COUNT_FORMAT}
                  aria-label={`订单数 ${formatCount(summary.current.revenue_stats.total_orders)}`}
                  className="mt-1.5 tabular-nums text-xl font-extrabold"
                />
              </div>
              <div className="min-w-0 pl-3 sm:pl-5">
                <p className="text-[11px] text-slate-400">库存卡密</p>
                <NumberFlow
                  {...NUMBER_FLOW_MOTION}
                  value={summary.stats.total_cards}
                  format={COUNT_FORMAT}
                  aria-label={`库存卡密 ${formatCount(summary.stats.total_cards)}`}
                  className="mt-1.5 tabular-nums text-xl font-extrabold"
                />
              </div>
            </div>
          </div>

          <div className="min-w-0">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="min-w-0 truncate text-xs font-bold tracking-wide text-slate-400">
                营收趋势 · {trendLabel}{trendCutoff} · 销售额与订单
              </p>
              <div className="flex shrink-0 items-center gap-2.5 text-[10px] font-semibold text-slate-500" aria-hidden="true">
                <span className="inline-flex items-center gap-1"><span className="h-1.5 w-3 rounded-full bg-[#FFE815]" />销售额</span>
                <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-sm bg-sky-300" />订单</span>
              </div>
            </div>
            <Suspense fallback={<div className="h-[158px] animate-pulse rounded-xl bg-white/[0.04] motion-reduce:animate-none sm:h-[174px]" />}>
              <HeroTrend
                points={heroPoints}
                highlightPoints={highlightPoints}
                granularity={heroGranularity}
                timeCoverage={summary.current.time_coverage}
              />
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
          <BusinessInsights range={insightRange} />
        </Suspense>
      )}

      <section className={`${PANEL_CLASS} overflow-hidden`}>
        <div className="flex flex-col gap-3 border-b border-gray-100 bg-gray-50/70 p-5 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-[15px] font-bold text-gray-900">参与统计的订单</h3>
          <input aria-label="搜索统计订单" placeholder="搜索订单号、商品或买家" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-gray-900 sm:w-64" />
        </div>
        <div className="max-h-[420px] overflow-auto">
          {ordersLoading ? (
            <div className="flex items-center justify-center py-16 text-sm text-gray-400"><Activity className="mr-2 h-5 w-5 animate-spin motion-reduce:animate-none" />加载订单明细...</div>
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
