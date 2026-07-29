import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Activity,
  AlertCircle,
  Clock,
  Database,
  Eye,
  PackageSearch,
  RefreshCw,
  Repeat,
  Users,
} from 'lucide-react';
import type {
  BuyerBehaviorAnalytics,
  ItemMetricStatus,
  ItemPerformanceAnalytics,
  ItemTrafficAnalytics,
  TrafficAnalytics,
} from '../types';
import {
  getBuyerBehaviorAnalytics,
  getItemMetricStatus,
  getItemPerformanceAnalytics,
  getItemTrafficAnalytics,
  getTrafficAnalytics,
} from '../services/api';

const WEEKDAY_LABELS: Record<string, string> = {
  '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六',
};
const WEEKDAY_ORDER = ['1', '2', '3', '4', '5', '6', '0'];
const CHART_INITIAL_DIMENSION = { width: 320, height: 240 } as const;

const formatAmount = (value: number) => `¥${Number(value || 0).toFixed(2)}`;
const formatCount = (value: number) => Number(value || 0).toLocaleString('zh-CN');
type InsightKey = 'timing' | 'buyers' | 'performance' | 'itemTraffic' | 'metricStatus';

const errorMessage = (reason: unknown) => (
  reason instanceof Error ? reason.message : '数据接口暂时不可用'
);

const SectionError: React.FC<{ message: string }> = ({ message }) => (
  <div className="flex min-h-36 flex-col items-center justify-center gap-2 text-center text-gray-400">
    <AlertCircle className="h-8 w-8 text-amber-500" />
    <p className="text-sm">{message}</p>
  </div>
);

const SectionLoading: React.FC = () => (
  <div className="flex min-h-36 items-center justify-center text-sm text-gray-400" role="status">
    <Activity className="mr-2 h-5 w-5 animate-spin text-[#D6B500]" />正在加载...
  </div>
);

const BusinessInsights: React.FC<{
  range: { start_date: string; end_date: string };
}> = ({ range }) => {
  const [timing, setTiming] = useState<TrafficAnalytics | null>(null);
  const [buyers, setBuyers] = useState<BuyerBehaviorAnalytics | null>(null);
  const [performance, setPerformance] = useState<ItemPerformanceAnalytics | null>(null);
  const [itemTraffic, setItemTraffic] = useState<ItemTrafficAnalytics | null>(null);
  const [metricStatus, setMetricStatus] = useState<ItemMetricStatus | null>(null);
  const [loading, setLoading] = useState<Record<InsightKey, boolean>>({
    timing: true,
    buyers: true,
    performance: true,
    itemTraffic: true,
    metricStatus: true,
  });
  const [errors, setErrors] = useState<Partial<Record<InsightKey, string>>>({});
  const requestGeneration = useRef(0);
  const [reloadGeneration, setReloadGeneration] = useState(0);

  useEffect(() => {
    if (!range.start_date || !range.end_date) return;
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const controller = new AbortController();
    setLoading({
      timing: true,
      buyers: true,
      performance: true,
      itemTraffic: true,
      metricStatus: true,
    });
    setErrors({});

    const run = async <T,>(
      key: InsightKey,
      setter: React.Dispatch<React.SetStateAction<T | null>>,
      request: () => Promise<T>,
    ) => {
      try {
        const value = await request();
        if (requestGeneration.current !== generation || controller.signal.aborted) return;
        setter(value);
      } catch (reason) {
        if (requestGeneration.current !== generation || controller.signal.aborted) return;
        setter(null);
        setErrors((current) => ({ ...current, [key]: errorMessage(reason) }));
      } finally {
        if (requestGeneration.current === generation && !controller.signal.aborted) {
          setLoading((current) => ({ ...current, [key]: false }));
        }
      }
    };

    void run('timing', setTiming, () => getTrafficAnalytics(range, controller.signal));
    void run('buyers', setBuyers, () => getBuyerBehaviorAnalytics(range, controller.signal));
    void run('performance', setPerformance, () => getItemPerformanceAnalytics(range, controller.signal));
    void run('itemTraffic', setItemTraffic, () => getItemTrafficAnalytics(range, controller.signal));
    void run('metricStatus', setMetricStatus, () => getItemMetricStatus(controller.signal));

    return () => controller.abort();
  }, [range, reloadGeneration]);

  const hourlyData = useMemo(() => {
    const byHour = new Map<number, TrafficAnalytics['hourly'][number]>();
    for (const entry of timing?.hourly ?? []) byHour.set(entry.hour, entry);
    return Array.from({ length: 24 }, (_, hour) => {
      const entry = byHour.get(hour);
      return {
        label: `${String(hour).padStart(2, '0')}时`,
        order_count: entry?.order_count || 0,
        amount: entry?.amount || 0,
      };
    });
  }, [timing]);

  const weekdayData = useMemo(() => {
    const byWeekday = new Map<string, TrafficAnalytics['weekday'][number]>();
    for (const entry of timing?.weekday ?? []) byWeekday.set(entry.weekday, entry);
    return WEEKDAY_ORDER.map((key) => {
      const entry = byWeekday.get(key);
      return {
        label: WEEKDAY_LABELS[key],
        order_count: entry?.order_count || 0,
        amount: entry?.amount || 0,
      };
    });
  }, [timing]);

  const itemTrafficWindows = useMemo(() => {
    return (itemTraffic?.observation_windows ?? []).map((entry) => ({
      ...entry,
      label: entry.day_span > 1
        ? `${String(entry.start_hour).padStart(2, '0')}时-${entry.day_span}天后${String(entry.end_hour).padStart(2, '0')}时`
        : entry.crosses_midnight
          ? `${String(entry.start_hour).padStart(2, '0')}时-次日${String(entry.end_hour).padStart(2, '0')}时`
          : `${String(entry.start_hour).padStart(2, '0')}时-${String(entry.end_hour).padStart(2, '0')}时`,
    }));
  }, [itemTraffic]);

  const frequencyData = useMemo(() => (buyers?.frequency || []).map((entry) => ({
    label: `${entry.order_count}单`,
    buyer_count: entry.buyer_count,
  })), [buyers]);

  const itemNames = useMemo(() => new Map(
    (performance?.items || []).map((item) => [item.item_id, item.item_title || item.item_id]),
  ), [performance]);

  const coverage = timing?.coverage;
  const hasTiming = (coverage?.with_ordered_at || 0) > 0;
  const timingCoverageShort = Boolean(
    coverage && coverage.total_orders > 0 && coverage.with_ordered_at < coverage.total_orders,
  );
  const amountCoverage = performance?.amount_coverage;
  const amountCoverageShort = Boolean(
    amountCoverage
      && amountCoverage.total_orders > 0
      && amountCoverage.with_amount < amountCoverage.total_orders,
  );
  const buyerSummary = buyers?.summary;
  const hasBuyers = (buyerSummary?.total_buyers || 0) > 0;
  const hasPerformance = (performance?.items.length || 0) > 0;
  const hasItemTraffic = (itemTraffic?.snapshot_count || 0) > 0;
  const metricAdapterUnavailable = metricStatus?.adapter_available === false;

  const allAnalyticsLoading = loading.timing
    && loading.buyers
    && loading.performance
    && loading.itemTraffic;

  if (allAnalyticsLoading) {
    return (
      <div className="ios-card flex h-64 items-center justify-center rounded-2xl bg-white text-sm text-gray-400" role="status" aria-label="经营分析加载中">
        <Activity className="mr-2 h-5 w-5 animate-spin text-[#D6B500]" />经营分析加载中...
      </div>
    );
  }

  const allAnalyticsFailed = Boolean(
    !Object.values(loading).some(Boolean)
      && errors.timing && errors.buyers && errors.performance && errors.itemTraffic,
  );

  if (allAnalyticsFailed) {
    return (
      <div className="ios-card flex flex-col items-center justify-center gap-3 rounded-2xl bg-white py-14 text-center">
        <AlertCircle className="h-8 w-8 text-red-500" />
        <p className="text-sm text-gray-500">{errors.timing}</p>
        <button type="button" onClick={() => setReloadGeneration((value) => value + 1)} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-bold text-white">
          <RefreshCw className="h-4 w-4" />重试
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Clock className="h-5 w-5 text-[#D6B500]" />
          <h3 className="text-lg font-bold text-gray-900">订单时段分析</h3>
          <span className="rounded-md bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-500">订单时间快照</span>
        </div>
        <p className="mb-5 text-sm text-gray-400">按平台订单时间快照统计的时段规律（东八区）</p>

        {timingCoverageShort && (
          <div className="mb-5 flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-xs text-amber-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              时段分布基于 {Math.round((coverage!.coverage_rate) * 100)}% 有订单时间的订单
              （{coverage!.with_ordered_at}/{coverage!.total_orders} 笔）；缺少订单时间的订单未计入图表。
            </span>
          </div>
        )}

        {timing?.recommendation ? (
          <div className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
            {timing.recommendation.message}
          </div>
        ) : timing?.insufficient_reason && coverage?.total_orders ? (
          <div className="mb-5 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
            时段建议待补充：{timing.insufficient_reason}
          </div>
        ) : null}

        {loading.timing ? (
          <SectionLoading />
        ) : errors.timing ? (
          <SectionError message={errors.timing} />
        ) : !hasTiming ? (
          <div className="flex h-56 flex-col items-center justify-center text-gray-400">
            <Clock className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无带订单时间的订单</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-3 text-sm font-bold text-gray-700">按小时分布</p>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                  <BarChart data={hourlyData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid vertical={false} stroke="#F3F4F6" strokeDasharray="3 3" />
                    <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} interval={2} />
                    <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                    <Tooltip formatter={(value, name) => (name === '成交额' ? formatAmount(Number(value)) : `${value} 单`)} />
                    <Bar dataKey="order_count" name="订单数" fill="#FFE815" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <p className="mb-3 text-sm font-bold text-gray-700">按星期分布</p>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                  <BarChart data={weekdayData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid vertical={false} stroke="#F3F4F6" strokeDasharray="3 3" />
                    <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                    <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                    <Tooltip formatter={(value) => `${value} 单`} />
                    <Bar dataKey="order_count" name="订单数" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}
      </section>

      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <PackageSearch className="h-5 w-5 text-blue-600" />
          <h3 className="text-lg font-bold text-gray-900">成交商品表现</h3>
          <span className="rounded-md bg-blue-50 px-2 py-1 text-[11px] font-bold text-blue-700">成交订单</span>
        </div>
        <p className="mb-5 text-sm text-gray-400">按成交订单数与实付金额排序</p>

        {amountCoverageShort && (
          <div className="mb-5 flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-xs text-amber-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              金额覆盖率 {Math.round(amountCoverage!.coverage_rate * 100)}%
              （{amountCoverage!.with_amount}/{amountCoverage!.total_orders} 笔）；订单数仍完整统计。
            </span>
          </div>
        )}

        {loading.performance ? (
          <SectionLoading />
        ) : errors.performance ? (
          <SectionError message={errors.performance} />
        ) : !hasPerformance ? (
          <div className="flex h-40 flex-col items-center justify-center text-gray-400">
            <PackageSearch className="mb-3 h-10 w-10 opacity-20" />
            <p className="font-medium">暂无成交商品数据</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="border-b border-gray-100 text-xs text-gray-400">
                <tr>
                  <th className="pb-3 font-medium">商品</th>
                  <th className="pb-3 text-right font-medium">订单</th>
                  <th className="pb-3 text-right font-medium">销售额</th>
                  <th className="pb-3 text-right font-medium">客单价</th>
                  <th className="pb-3 text-right font-medium">金额覆盖</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {performance!.items.slice(0, 12).map((item) => (
                  <tr key={item.item_id}>
                    <td className="max-w-[280px] py-3 pr-4">
                      <div className="truncate font-bold text-gray-900">{item.item_title || item.item_id}</div>
                      <div className="mt-0.5 truncate font-mono text-[11px] text-gray-400">{item.item_id}</div>
                    </td>
                    <td className="py-3 text-right font-bold text-gray-800">{item.order_count}</td>
                    <td className="py-3 text-right text-gray-700">{formatAmount(item.total_amount)}</td>
                    <td className="py-3 text-right text-gray-700">{formatAmount(item.avg_amount)}</td>
                    <td className="py-3 text-right text-gray-500">{item.orders_with_amount}/{item.order_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Eye className="h-5 w-5 text-emerald-600" />
          <h3 className="text-lg font-bold text-gray-900">商品流量</h3>
          <span className="rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">卖家后台已验证快照</span>
        </div>
        <p className="mb-5 text-sm text-gray-400">相邻卖家后台快照之间的累计增量，定时采样约每 4 小时一次</p>

        {errors.metricStatus && (
          <div className="mb-5 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errors.metricStatus}</span>
          </div>
        )}

        {metricAdapterUnavailable && hasItemTraffic && (
          <div className="mb-5 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>历史快照仍可查看；当前卖家后台适配器不可用，暂停新增采集。</span>
          </div>
        )}

        {loading.itemTraffic ? (
          <SectionLoading />
        ) : errors.itemTraffic ? (
          <SectionError message={errors.itemTraffic} />
        ) : metricAdapterUnavailable && !hasItemTraffic ? (
          <div className="flex h-48 flex-col items-center justify-center px-4 text-center text-gray-400">
            <Database className="mb-3 h-11 w-11 opacity-20" />
            <p className="font-medium">真实商品流量采集尚未启用</p>
            <p className="mt-2 text-xs">卖家后台适配器完成真实账号验收前，系统不会生成或推测流量数据</p>
          </div>
        ) : !hasItemTraffic ? (
          <div className="flex h-48 flex-col items-center justify-center px-4 text-center text-gray-400">
            <Database className="mb-3 h-11 w-11 opacity-20" />
            <p className="font-medium">尚无已验证商品流量快照</p>
            <p className="mt-2 text-xs">积累 14 天且至少 20 个有效观测窗口后生成窗口建议</p>
          </div>
        ) : (
          <>
            <div className="mb-6 grid grid-cols-2 divide-x divide-y divide-gray-100 border-y border-gray-100 sm:grid-cols-4 sm:divide-y-0">
              <div className="p-4 sm:pl-0"><div className="text-xs text-gray-400">曝光增量</div><div className="mt-1 text-xl font-extrabold text-gray-900">{formatCount(itemTraffic!.totals.exposure_delta)}</div></div>
              <div className="p-4"><div className="text-xs text-gray-400">浏览增量</div><div className="mt-1 text-xl font-extrabold text-gray-900">{formatCount(itemTraffic!.totals.view_delta)}</div></div>
              <div className="p-4"><div className="text-xs text-gray-400">想要增量</div><div className="mt-1 text-xl font-extrabold text-gray-900">{formatCount(itemTraffic!.totals.want_delta)}</div></div>
              <div className="p-4 sm:pr-0"><div className="text-xs text-gray-400">可比窗口 / 天数</div><div className="mt-1 text-xl font-extrabold text-gray-900">{itemTraffic!.recommendation_window_count} / {itemTraffic!.recommendation_distinct_days}</div></div>
            </div>

            {itemTraffic!.recommendation ? (
              <div className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
                {itemTraffic!.recommendation!.message}
              </div>
            ) : (
              <div className="mb-6 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
                流量建议待补充：{itemTraffic!.insufficient_reason}
              </div>
            )}

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-1 text-sm font-bold text-gray-700">观测窗口增量</p>
                <p className="mb-3 text-xs text-gray-400">增量属于整个窗口，不能细分到单小时</p>
                <div className="h-[240px]">
                  <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                    <BarChart data={itemTrafficWindows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke="#F3F4F6" strokeDasharray="3 3" />
                      <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} interval={2} />
                      <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} />
                      <Tooltip />
                      <Bar dataKey="exposure_delta" name="曝光增量" fill="#10B981" radius={[3, 3, 0, 0]} />
                      <Bar dataKey="view_delta" name="浏览增量" fill="#3B82F6" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <p className="mb-3 text-sm font-bold text-gray-700">商品流量排行</p>
                <div className="max-h-[240px] overflow-auto rounded-lg border border-gray-100">
                  <table className="w-full min-w-[440px] text-left text-sm">
                    <thead className="sticky top-0 bg-white text-xs text-gray-400">
                      <tr><th className="px-4 py-2.5">商品</th><th className="px-4 py-2.5 text-right">曝光</th><th className="px-4 py-2.5 text-right">浏览</th><th className="px-4 py-2.5 text-right">想要</th></tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {itemTraffic!.items.map((item) => (
                        <tr key={item.item_id}>
                          <td className="max-w-[180px] truncate px-4 py-2.5 font-medium text-gray-900">{itemNames.get(item.item_id) || item.item_id}</td>
                          <td className="px-4 py-2.5 text-right text-gray-600">{formatCount(item.exposure_delta)}</td>
                          <td className="px-4 py-2.5 text-right font-bold text-gray-900">{formatCount(item.view_delta)}</td>
                          <td className="px-4 py-2.5 text-right text-gray-600">{formatCount(item.want_delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </section>

      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-2 flex items-center gap-2">
          <Users className="h-5 w-5 text-[#D6B500]" />
          <h3 className="text-lg font-bold text-gray-900">买家行为分析</h3>
        </div>
        <p className="mb-5 text-sm text-gray-400">复购与下单频次（仅统计下单行为，不涉及客户画像）</p>

        {loading.buyers ? (
          <SectionLoading />
        ) : errors.buyers ? (
          <SectionError message={errors.buyers} />
        ) : !hasBuyers ? (
          <div className="flex h-56 flex-col items-center justify-center text-gray-400">
            <Users className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无买家数据</p>
          </div>
        ) : (
          <>
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-gray-100 bg-gray-50/60 p-4">
                <p className="text-sm text-gray-500">下单买家</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{buyerSummary!.total_buyers}</p>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50/60 p-4">
                <p className="text-sm text-gray-500">复购买家</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{buyerSummary!.repeat_buyers}</p>
              </div>
              <div className="rounded-lg border border-gray-100 bg-[#FFE815]/20 p-4">
                <p className="flex items-center gap-1 text-sm text-gray-600"><Repeat className="h-3.5 w-3.5" />复购率</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{(buyerSummary!.repeat_rate * 100).toFixed(1)}%</p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-3 text-sm font-bold text-gray-700">下单频次分布</p>
                <div className="h-[240px]">
                  <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                    <BarChart data={frequencyData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke="#F3F4F6" strokeDasharray="3 3" />
                      <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                      <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                      <Tooltip formatter={(value) => `${value} 人`} />
                      <Bar dataKey="buyer_count" name="买家数" fill="#10B981" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <p className="mb-3 text-sm font-bold text-gray-700">买家贡献榜</p>
                <div className="max-h-[240px] overflow-auto rounded-lg border border-gray-100">
                  <table className="w-full text-left text-sm">
                    <thead className="sticky top-0 bg-white text-xs text-gray-400">
                      <tr><th className="px-4 py-2.5">买家</th><th className="px-4 py-2.5 text-center">下单</th><th className="px-4 py-2.5 text-right">贡献额</th></tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {buyers!.top_buyers.map((buyer) => (
                        <tr key={buyer.buyer_id}>
                          <td className="px-4 py-2.5 font-medium text-gray-900">{buyer.buyer_nickname || buyer.buyer_id}</td>
                          <td className="px-4 py-2.5 text-center text-gray-600">{buyer.order_count}</td>
                          <td className="px-4 py-2.5 text-right font-bold text-gray-900">{formatAmount(buyer.total_amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
};

export default BusinessInsights;
