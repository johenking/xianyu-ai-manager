import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
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
import {
  InlineNote,
  PANEL_CLASS,
  PanelTitle,
  ShareBars,
  formatCount,
  formatMoney,
  itemDisplayName,
} from './ui/dashboardParts';

const WEEKDAY_LABELS: Record<string, string> = {
  '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六',
};
const WEEKDAY_ORDER = ['1', '2', '3', '4', '5', '6', '0'];
const CHART_INITIAL_DIMENSION = { width: 320, height: 240 } as const;

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

/* ---------------- 通用白底图表提示框 ---------------- */

interface BarTipPayload {
  payload?: Record<string, number | string>;
  name?: string;
  dataKey?: string;
  value?: number;
}

const LightTooltip: React.FC<{
  active?: boolean;
  payload?: BarTipPayload[];
  label?: string;
  /** 优先展示原始值字段（用于 0 值底座场景） */
  rawKey?: string;
  unit?: string;
}> = ({ active, payload, label, rawKey, unit = '单' }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-lg">
      <p className="text-xs font-bold text-gray-900">{label}</p>
      {rawKey ? (
        <p className="text-xs text-gray-600">
          订单数：<span className="font-bold text-gray-900">{formatCount(Number(payload[0]?.payload?.[rawKey] ?? 0))} {unit}</span>
        </p>
      ) : (
        payload.map((entry) => (
          <p key={String(entry.dataKey)} className="text-xs text-gray-600">
            {entry.name}：<span className="font-bold text-gray-900">{formatCount(Number(entry.value ?? 0))}</span>
          </p>
        ))
      )}
    </div>
  );
};

/* ---------------- 峰值高亮柱状图（0 值画浅色底座，最大值黄色并标数值） ---------------- */

const PeakBarChart: React.FC<{
  data: Array<{ label: string; raw: number }>;
  interval?: number;
}> = ({ data, interval = 0 }) => {
  const max = Math.max(...data.map((entry) => entry.raw), 1);
  const floor = max * 0.04;
  const plotted = data.map((entry) => ({ ...entry, plotted: entry.raw === 0 ? floor : entry.raw }));
  return (
    <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
      <BarChart data={plotted} margin={{ top: 18, right: 8, left: -20, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="#F3F4F6" />
        <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} interval={interval} />
        <YAxis
          allowDecimals={false}
          axisLine={false}
          tickLine={false}
          tick={{ fill: '#9CA3AF', fontSize: 11 }}
          domain={[0, Math.ceil(max * 1.15)]}
        />
        <Tooltip content={<LightTooltip rawKey="raw" />} cursor={{ fill: 'rgba(17, 24, 39, 0.03)' }} />
        <Bar dataKey="plotted" name="订单数" radius={[3, 3, 0, 0]} maxBarSize={22}>
          {plotted.map((entry) => (
            <Cell
              key={entry.label}
              fill={entry.raw === 0 ? '#F3F4F6' : entry.raw === max ? '#FFE815' : '#111827'}
            />
          ))}
          <LabelList
            dataKey="raw"
            position="top"
            content={(props) => {
              const { x, y, width, value } = props as { x?: number; y?: number; width?: number; value?: number };
              if (value !== max || !value || x === undefined || y === undefined || width === undefined) return null;
              return (
                <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill="#111827">
                  {value}
                </text>
              );
            }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
};

const BusinessInsights: React.FC<{
  range: { start_date: string; end_date: string };
  /** 摘要数据版本号：变化时静默跟刷各分析区（保留已渲染内容，不闪骨架） */
  refreshSignal?: number;
}> = ({ range, refreshSignal = 0 }) => {
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
  const lastForegroundKey = useRef('');

  useEffect(() => {
    if (!range.start_date || !range.end_date) return;
    // 范围切换/手动重试 = 前台加载（显示骨架并清空错误）；
    // 仅 refreshSignal 变化 = 静默跟刷（保留现有内容，失败不打扰）。
    const foregroundKey = `${range.start_date}|${range.end_date}|${reloadGeneration}`;
    const isForeground = lastForegroundKey.current !== foregroundKey;
    lastForegroundKey.current = foregroundKey;

    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    const controller = new AbortController();
    if (isForeground) {
      setLoading({
        timing: true,
        buyers: true,
        performance: true,
        itemTraffic: true,
        metricStatus: true,
      });
      setErrors({});
    }

    const run = async <T,>(
      key: InsightKey,
      setter: React.Dispatch<React.SetStateAction<T | null>>,
      request: () => Promise<T>,
    ) => {
      try {
        const value = await request();
        if (requestGeneration.current !== generation || controller.signal.aborted) return;
        setter(value);
        // 静默刷新成功后清除该区块的历史错误提示
        setErrors((current) => {
          if (!(key in current)) return current;
          const next = { ...current };
          delete next[key];
          return next;
        });
      } catch (reason) {
        if (requestGeneration.current !== generation || controller.signal.aborted) return;
        // 静默刷新失败不清空已渲染的数据，等下一轮再试
        if (!isForeground) return;
        setter(null);
        setErrors((current) => ({ ...current, [key]: errorMessage(reason) }));
      } finally {
        if (isForeground && requestGeneration.current === generation && !controller.signal.aborted) {
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
  }, [range, refreshSignal, reloadGeneration]);

  const hourlyData = useMemo(() => {
    const byHour = new Map<number, TrafficAnalytics['hourly'][number]>();
    for (const entry of timing?.hourly ?? []) byHour.set(entry.hour, entry);
    return Array.from({ length: 24 }, (_, hour) => {
      const entry = byHour.get(hour);
      return {
        label: `${String(hour).padStart(2, '0')}时`,
        raw: entry?.order_count || 0,
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
        raw: entry?.order_count || 0,
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
      <div className={`${PANEL_CLASS} flex h-64 items-center justify-center text-sm text-gray-400`} role="status" aria-label="经营分析加载中">
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
      <div className={`${PANEL_CLASS} flex flex-col items-center justify-center gap-3 py-14 text-center`}>
        <AlertCircle className="h-8 w-8 text-red-500" />
        <p className="text-sm text-gray-500">{errors.timing}</p>
        <button type="button" onClick={() => setReloadGeneration((value) => value + 1)} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-bold text-white">
          <RefreshCw className="h-4 w-4" />重试
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* 订单时段分析 */}
      <section className={`${PANEL_CLASS} p-6`}>
        <PanelTitle
          icon={<Clock className="h-5 w-5 text-gray-400" />}
          title="订单时段分析"
          badge="订单时间快照"
          sub="按平台订单时间快照统计的时段规律（东八区）"
        />

        {timingCoverageShort && (
          <div className="mb-4">
            <InlineNote tone="warn">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                时段分布基于 {Math.round((coverage!.coverage_rate) * 100)}% 有订单时间的订单
                （{coverage!.with_ordered_at}/{coverage!.total_orders} 笔）；缺少订单时间的订单未计入图表。
              </span>
            </InlineNote>
          </div>
        )}

        {timing?.recommendation ? (
          <div className="mb-4">
            <InlineNote tone="good">
              <span className="text-sm font-medium">{timing.recommendation.message}</span>
            </InlineNote>
          </div>
        ) : timing?.insufficient_reason && coverage?.total_orders ? (
          <div className="mb-4">
            <InlineNote>时段建议待补充：{timing.insufficient_reason}</InlineNote>
          </div>
        ) : null}

        {loading.timing ? (
          <SectionLoading />
        ) : errors.timing ? (
          <SectionError message={errors.timing} />
        ) : !hasTiming ? (
          <div className="flex h-52 flex-col items-center justify-center text-gray-400">
            <Clock className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无带订单时间的订单</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-3 text-xs font-bold text-gray-500">按小时分布（峰值高亮）</p>
              <div className="h-[230px]">
                <PeakBarChart data={hourlyData} interval={2} />
              </div>
            </div>
            <div>
              <p className="mb-3 text-xs font-bold text-gray-500">按星期分布</p>
              <div className="h-[230px]">
                <PeakBarChart data={weekdayData} />
              </div>
            </div>
          </div>
        )}
      </section>

      {/* 成交商品表现 */}
      <section className={`${PANEL_CLASS} p-6`}>
        <PanelTitle
          icon={<PackageSearch className="h-5 w-5 text-gray-400" />}
          title="成交商品表现"
          badge="成交订单"
          sub="按成交订单数与实付金额排序"
        />

        {amountCoverageShort && (
          <div className="mb-4">
            <InlineNote tone="warn">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                金额覆盖率 {Math.round(amountCoverage!.coverage_rate * 100)}%
                （{amountCoverage!.with_amount}/{amountCoverage!.total_orders} 笔）；订单数仍完整统计。
              </span>
            </InlineNote>
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
                {performance!.items.slice(0, 12).map((item) => {
                  const hasRealTitle = Boolean(String(item.item_title || '').trim());
                  return (
                    <tr key={item.item_id}>
                      <td className="max-w-[280px] py-3 pr-4">
                        <div className="truncate font-bold text-gray-900" title={itemDisplayName(item.item_id, item.item_title)}>
                          {itemDisplayName(item.item_id, item.item_title)}
                        </div>
                        {/* 无标题商品不再重复展示同一串 ID */}
                        {hasRealTitle && (
                          <div className="mt-0.5 truncate font-mono text-[11px] text-gray-400">{item.item_id}</div>
                        )}
                      </td>
                      <td className="py-3 text-right font-bold tabular-nums text-gray-800">{item.order_count}</td>
                      <td className="py-3 text-right tabular-nums text-gray-700">¥{formatMoney(item.total_amount)}</td>
                      <td className="py-3 text-right tabular-nums text-gray-700">¥{formatMoney(item.avg_amount)}</td>
                      <td className="py-3 text-right tabular-nums text-gray-500">{item.orders_with_amount}/{item.order_count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 商品流量 */}
      <section className={`${PANEL_CLASS} p-6`}>
        <PanelTitle
          icon={<Eye className="h-5 w-5 text-gray-400" />}
          title="商品流量"
          badge="卖家后台已验证快照"
          sub="相邻卖家后台快照之间的累计增量，定时采样约每 4 小时一次"
        />

        {errors.metricStatus && (
          <div className="mb-4">
            <InlineNote tone="warn">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{errors.metricStatus}</span>
            </InlineNote>
          </div>
        )}

        {metricAdapterUnavailable && hasItemTraffic && (
          <div className="mb-4">
            <InlineNote tone="warn">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>历史快照仍可查看；当前卖家后台适配器不可用，暂停新增采集。</span>
            </InlineNote>
          </div>
        )}

        {loading.itemTraffic ? (
          <SectionLoading />
        ) : errors.itemTraffic ? (
          <SectionError message={errors.itemTraffic} />
        ) : metricAdapterUnavailable && !hasItemTraffic ? (
          <div className="flex h-44 flex-col items-center justify-center px-4 text-center text-gray-400">
            <Database className="mb-3 h-11 w-11 opacity-20" />
            <p className="font-medium">真实商品流量采集尚未启用</p>
            <p className="mt-2 text-xs">卖家后台适配器完成真实账号验收前，系统不会生成或推测流量数据</p>
          </div>
        ) : !hasItemTraffic ? (
          <div className="flex h-44 flex-col items-center justify-center px-4 text-center text-gray-400">
            <Database className="mb-3 h-11 w-11 opacity-20" />
            <p className="font-medium">尚无已验证商品流量快照</p>
            <p className="mt-2 text-xs">积累 14 天且至少 20 个有效观测窗口后生成窗口建议</p>
          </div>
        ) : (
          <>
            <div className="mb-5 grid grid-cols-2 divide-x divide-y divide-gray-100 border-y border-gray-100 sm:grid-cols-4 sm:divide-y-0">
              <div className="p-4 sm:pl-0"><div className="text-xs text-gray-400">曝光增量</div><div className="mt-1 text-xl font-extrabold tabular-nums text-gray-900">{formatCount(itemTraffic!.totals.exposure_delta)}</div></div>
              <div className="p-4"><div className="text-xs text-gray-400">浏览增量</div><div className="mt-1 text-xl font-extrabold tabular-nums text-gray-900">{formatCount(itemTraffic!.totals.view_delta)}</div></div>
              <div className="p-4"><div className="text-xs text-gray-400">想要增量</div><div className="mt-1 text-xl font-extrabold tabular-nums text-gray-900">{formatCount(itemTraffic!.totals.want_delta)}</div></div>
              <div className="p-4 sm:pr-0"><div className="text-xs text-gray-400">可比窗口 / 天数</div><div className="mt-1 text-xl font-extrabold tabular-nums text-gray-900">{itemTraffic!.recommendation_window_count} / {itemTraffic!.recommendation_distinct_days}</div></div>
            </div>

            <div className="mb-5">
              {itemTraffic!.recommendation ? (
                <InlineNote tone="good">
                  <span className="text-sm font-medium">{itemTraffic!.recommendation!.message}</span>
                </InlineNote>
              ) : (
                <InlineNote>流量建议待补充：{itemTraffic!.insufficient_reason}</InlineNote>
              )}
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-1 text-xs font-bold text-gray-500">观测窗口增量</p>
                <p className="mb-3 text-xs text-gray-400">增量属于整个窗口，不能细分到单小时</p>
                <div className="h-[230px]">
                  <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                    <BarChart data={itemTrafficWindows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke="#F3F4F6" />
                      <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} interval={2} />
                      <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 11 }} />
                      <Tooltip content={<LightTooltip />} cursor={{ fill: 'rgba(17, 24, 39, 0.03)' }} />
                      <Bar dataKey="exposure_delta" name="曝光增量" fill="#111827" radius={[3, 3, 0, 0]} maxBarSize={18} />
                      <Bar dataKey="view_delta" name="浏览增量" fill="#94A3B8" radius={[3, 3, 0, 0]} maxBarSize={18} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <p className="mb-3 text-xs font-bold text-gray-500">商品流量排行</p>
                <div className="max-h-[230px] overflow-auto rounded-lg border border-gray-100">
                  <table className="w-full min-w-[440px] text-left text-sm">
                    <thead className="sticky top-0 bg-white text-xs text-gray-400">
                      <tr><th className="px-4 py-2.5 font-medium">商品</th><th className="px-4 py-2.5 text-right font-medium">曝光</th><th className="px-4 py-2.5 text-right font-medium">浏览</th><th className="px-4 py-2.5 text-right font-medium">想要</th></tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {itemTraffic!.items.map((item) => (
                        <tr key={item.item_id}>
                          <td className="max-w-[180px] truncate px-4 py-2.5 font-medium text-gray-900" title={itemDisplayName(item.item_id, itemNames.get(item.item_id))}>
                            {itemDisplayName(item.item_id, itemNames.get(item.item_id))}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-gray-600">{formatCount(item.exposure_delta)}</td>
                          <td className="px-4 py-2.5 text-right font-bold tabular-nums text-gray-900">{formatCount(item.view_delta)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-gray-600">{formatCount(item.want_delta)}</td>
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

      {/* 买家行为分析 */}
      <section className={`${PANEL_CLASS} p-6`}>
        <PanelTitle
          icon={<Users className="h-5 w-5 text-gray-400" />}
          title="买家行为分析"
          sub="复购与下单频次（仅统计下单行为，不涉及客户画像）"
        />

        {loading.buyers ? (
          <SectionLoading />
        ) : errors.buyers ? (
          <SectionError message={errors.buyers} />
        ) : !hasBuyers ? (
          <div className="flex h-52 flex-col items-center justify-center text-gray-400">
            <Users className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无买家数据</p>
          </div>
        ) : (
          <>
            {/* 关键数字行内呈现，复购率用品牌黄强调 */}
            <div className="mb-6 flex flex-wrap items-center gap-x-7 gap-y-3 border-y border-gray-100 py-4">
              <div className="text-sm text-gray-500">下单买家 <span className="ml-1 text-xl font-extrabold tabular-nums text-gray-900">{formatCount(buyerSummary!.total_buyers)}</span></div>
              <div className="text-sm text-gray-500">复购买家 <span className="ml-1 text-xl font-extrabold tabular-nums text-gray-900">{formatCount(buyerSummary!.repeat_buyers)}</span></div>
              <div className="text-sm text-gray-500">复购率 <span className="ml-1 rounded bg-[#FFE815] px-2 py-0.5 text-xl font-extrabold tabular-nums text-black">{(buyerSummary!.repeat_rate * 100).toFixed(1)}%</span></div>
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-3 text-xs font-bold text-gray-500">下单频次分布</p>
                <ShareBars
                  rows={frequencyData.map((entry) => ({
                    key: entry.label,
                    label: entry.label,
                    count: entry.buyer_count,
                    color: '#111827',
                  }))}
                  unit="人"
                  emptyText="暂无数据"
                  labelWidthClass="w-16"
                />
              </div>
              <div>
                <p className="mb-3 text-xs font-bold text-gray-500">买家贡献榜</p>
                <div className="max-h-[230px] overflow-auto rounded-lg border border-gray-100">
                  <table className="w-full text-left text-sm">
                    <thead className="sticky top-0 bg-white text-xs text-gray-400">
                      <tr><th className="px-4 py-2.5 font-medium">买家</th><th className="px-4 py-2.5 text-center font-medium">下单</th><th className="px-4 py-2.5 text-right font-medium">贡献额</th></tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {buyers!.top_buyers.map((buyer) => (
                        <tr key={buyer.buyer_id}>
                          <td className="px-4 py-2.5 font-medium text-gray-900">{buyer.buyer_nickname || buyer.buyer_id}</td>
                          <td className="px-4 py-2.5 text-center tabular-nums text-gray-600">{buyer.order_count}</td>
                          <td className="px-4 py-2.5 text-right font-bold tabular-nums text-gray-900">¥{formatMoney(buyer.total_amount)}</td>
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
