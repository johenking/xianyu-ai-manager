import React, { useMemo, useState } from 'react';
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Flame, Layers, TrendingDown, TrendingUp } from 'lucide-react';
import type { Order, OrderAnalytics } from '../types';
import {
  ContributionList,
  DonutMix,
  Histogram,
  PANEL_CLASS,
  PanelTitle,
  RankList,
  ShareBars,
  formatMoney,
  getTrendHighlights,
  itemDisplayName,
  statusMetaOf,
  type DailyPoint,
  type HistogramBin,
} from './ui/dashboardParts';

const CHART_INITIAL_DIMENSION = { width: 320, height: 180 } as const;

export const formatHeroAxisTick = (value: number): string => {
  if (Math.abs(value) < 1000) return String(value);
  return `${Number((value / 1000).toFixed(1))}k`;
};

/* ---------------- 深色 hero 区的营收趋势图（品牌黄线 + 渐变，日期已按范围补零） ---------------- */

const HeroTrendTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ payload: DailyPoint }>;
}> = ({ active, payload }) => {
  if (!active || !payload || !payload.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-white/10 bg-slate-800/95 px-3 py-2 shadow-xl">
      <p className="text-xs font-bold text-white">{point.date}</p>
      <p className="mt-0.5 text-xs text-slate-300">销售额：<span className="font-bold text-white">¥{formatMoney(point.amount)}</span></p>
      <p className="text-xs text-slate-300">订单数：<span className="font-bold text-white">{point.orders} 单</span></p>
    </div>
  );
};

/** 图内峰/谷标注文字：跟随 ReferenceDot 的 viewBox 定位，靠边时自动换锚点防溢出 */
const TrendMarkerLabel: React.FC<{
  viewBox?: { x?: number; y?: number };
  text: string;
  fill: string;
  anchor: 'start' | 'middle' | 'end';
  testId: string;
}> = ({ viewBox, text, fill, anchor, testId }) => (
  <text
    data-testid={testId}
    x={viewBox?.x ?? 0}
    y={(viewBox?.y ?? 0) - 9}
    textAnchor={anchor}
    fill={fill}
    fontSize={10}
    fontWeight={700}
    style={{ fontVariantNumeric: 'tabular-nums' }}
  >
    {text}
  </text>
);

const TrendHighlight: React.FC<{
  label: string;
  point: DailyPoint | null;
  value: string;
  tone: string;
  testId: string;
}> = ({ label, point, value, tone, testId }) => (
  <div className="min-w-0 px-2 py-1.5 first:pl-0 sm:border-l sm:border-white/10 sm:first:border-l-0" data-testid={testId}>
    <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
    <p className={`mt-0.5 truncate text-xs font-bold ${tone}`} title={point ? `${point.label} ${value}` : value}>
      {point ? `${point.label} · ${value}` : '--'}
    </p>
  </div>
);

export const HeroTrend: React.FC<{
  points: DailyPoint[];
  highlightPoints?: DailyPoint[];
  granularity?: 'hour' | 'day';
  timeCoverage?: { total_orders: number; with_ordered_at: number; coverage_rate: number };
}> = ({ points, highlightPoints = points, granularity = 'day', timeCoverage }) => {
  const hasData = points.some((point) => point.amount > 0 || point.orders > 0);
  if (!points.length || !hasData) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-xl bg-white/[0.04] text-sm text-slate-500">
        所选周期暂无成交
      </div>
    );
  }
  const dense = points.length > 12;
  const highlights = getTrendHighlights(highlightPoints);
  const prefersReducedMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const coverageRate = timeCoverage?.coverage_rate;
  const anchorFor = (point: DailyPoint): 'start' | 'middle' | 'end' => {
    const index = points.findIndex((candidate) => candidate.label === point.label);
    if (index <= 1) return 'start';
    if (index >= points.length - 2) return 'end';
    return 'middle';
  };
  return (
    <div className="w-full" data-testid="hero-trend">
      <div className="h-[176px] w-full sm:h-[192px]">
      <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
        <ComposedChart data={points} margin={{ top: 18, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="heroRevenue" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#FFE815" stopOpacity={0.26} />
              <stop offset="100%" stopColor="#FFE815" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.06)" />
          <XAxis
            dataKey="label"
            axisLine={false}
            tickLine={false}
            tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }}
            interval={dense ? Math.ceil(points.length / 8) : 0}
            padding={{ left: 8, right: 8 }}
          />
          <YAxis
            yAxisId="amount"
            axisLine={false}
            tickLine={false}
            tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }}
            tickFormatter={formatHeroAxisTick}
            width={48}
          />
          <YAxis yAxisId="orders" orientation="right" hide domain={[0, 'auto']} />
          <Tooltip content={<HeroTrendTooltip />} cursor={{ stroke: 'rgba(255,255,255,0.16)' }} />
          <Bar
            yAxisId="orders"
            dataKey="orders"
            name="订单数"
            fill="#38BDF8"
            fillOpacity={0.24}
            radius={[2, 2, 0, 0]}
            isAnimationActive={!prefersReducedMotion}
            animationDuration={650}
          />
          <Area
            yAxisId="amount"
            type="linear"
            dataKey="amount"
            name="销售额"
            stroke="#FFE815"
            strokeWidth={2}
            fill="url(#heroRevenue)"
            activeDot={{ r: 3.5, fill: '#FFE815', stroke: '#111827', strokeWidth: 1.5 }}
            isAnimationActive={!prefersReducedMotion}
            animationDuration={650}
            animationEasing="ease-out"
          />
          {highlights.peakAmount && (
            <ReferenceDot
              x={highlights.peakAmount.label}
              y={highlights.peakAmount.amount}
              yAxisId="amount"
              r={4}
              fill="#FFE815"
              stroke="#111827"
              strokeWidth={1.5}
              isFront
              label={(
                <TrendMarkerLabel
                  text={`¥${formatMoney(highlights.peakAmount.amount)}`}
                  fill="#FFE815"
                  anchor={anchorFor(highlights.peakAmount)}
                  testId="hero-peak-marker"
                />
              )}
            />
          )}
          {highlights.lowAmount && (
            <ReferenceDot
              x={highlights.lowAmount.label}
              y={highlights.lowAmount.amount}
              yAxisId="amount"
              r={3.5}
              fill="#94A3B8"
              stroke="#111827"
              strokeWidth={1.5}
              isFront
              label={(
                <TrendMarkerLabel
                  text={`¥${formatMoney(highlights.lowAmount.amount)}`}
                  fill="#94A3B8"
                  anchor={anchorFor(highlights.lowAmount)}
                  testId="hero-low-marker"
                />
              )}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      </div>
      <div className="mt-2 grid grid-cols-2 border-t border-white/10 pt-1 sm:grid-cols-4">
        <TrendHighlight
          label="订单峰值"
          point={highlights.peakOrders}
          value={`${highlights.peakOrders?.orders || 0} 单`}
          tone="text-sky-300"
          testId="trend-peak-orders"
        />
        <TrendHighlight
          label="订单低谷"
          point={highlights.lowOrders}
          value={`${highlights.lowOrders?.orders || 0} 单`}
          tone="text-slate-300"
          testId="trend-low-orders"
        />
        <TrendHighlight
          label="营收上升最快"
          point={highlights.fastestGrowth?.to || null}
          value={highlights.fastestGrowth ? `+¥${formatMoney(highlights.fastestGrowth.delta)}` : '--'}
          tone="text-emerald-300"
          testId="trend-fastest-growth"
        />
        <TrendHighlight
          label="营收回落最大"
          point={highlights.slowestGrowth?.to || null}
          value={highlights.slowestGrowth ? `${highlights.slowestGrowth.delta >= 0 ? '+' : '-'}¥${formatMoney(Math.abs(highlights.slowestGrowth.delta))}` : '--'}
          tone={highlights.slowestGrowth && highlights.slowestGrowth.delta < 0 ? 'text-red-300' : 'text-slate-300'}
          testId="trend-slowest-growth"
        />
      </div>
      {granularity === 'hour' && timeCoverage && coverageRate !== undefined && coverageRate < 1 && (
        <p className="mt-2 text-[10px] text-slate-500">
          时段覆盖 {Math.round(coverageRate * 100)}% · {timeCoverage.total_orders - timeCoverage.with_ordered_at} 笔订单缺少下单时间
        </p>
      )}
    </div>
  );
};

/* ---------------- 客单价分桶（从订单明细在前端聚合，与后端口径一致的实付金额） ---------------- */

export const PRICE_BANDS = [
  { key: 'lt10', label: '<¥10', min: 0, max: 10 },
  { key: '10to20', label: '¥10-20', min: 10, max: 20 },
  { key: '20to50', label: '¥20-50', min: 20, max: 50 },
  { key: '50to100', label: '¥50-100', min: 50, max: 100 },
  { key: 'gte100', label: '≥¥100', min: 100, max: Number.POSITIVE_INFINITY },
] as const;

/** 订单实付金额（元）：规范化分值优先，缺失时回退解析展示金额；均不可用返回 null 不冒充 0 */
export const orderAmountOf = (order: Order): number | null => {
  if (typeof order.paid_amount_fen === 'number' && Number.isFinite(order.paid_amount_fen)) {
    return order.paid_amount_fen / 100;
  }
  const parsed = Number.parseFloat(String(order.amount ?? ''));
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
};

export interface PriceBinsResult {
  bins: HistogramBin[];
  /** 金额缺失、未计入分布的订单数 */
  excluded: number;
}

export const buildPriceBins = (orders: Order[]): PriceBinsResult => {
  const bins: HistogramBin[] = PRICE_BANDS.map((band) => ({
    key: band.key,
    label: band.label,
    count: 0,
    amount: 0,
  }));
  let excluded = 0;
  for (const order of orders) {
    const amount = orderAmountOf(order);
    if (amount === null) {
      excluded += 1;
      continue;
    }
    const index = PRICE_BANDS.findIndex((band) => amount >= band.min && amount < band.max);
    const bin = bins[index === -1 ? bins.length - 1 : index];
    bin.count += 1;
    bin.amount += amount;
  }
  return { bins, excluded };
};

/* ---------------- 买家构成（按所选周期内下单次数区分复购/单次） ---------------- */

export interface BuyerMix {
  total: number;
  repeat: number;
  single: number;
}

export const buildBuyerMix = (orders: Order[]): BuyerMix => {
  const counts = new Map<string, number>();
  for (const order of orders) {
    const buyer = String(order.buyer_id || '').trim();
    if (!buyer) continue;
    counts.set(buyer, (counts.get(buyer) || 0) + 1);
  }
  let repeat = 0;
  for (const count of counts.values()) {
    if (count >= 2) repeat += 1;
  }
  return { total: counts.size, repeat, single: counts.size - repeat };
};

/* ---------------- 成交爆品榜（当期 vs 上一周期环比） ---------------- */

interface HotItemRow {
  itemId: string;
  name: string;
  orderCount: number;
  totalAmount: number;
  avgAmount: number;
  isNew: boolean;          // 新品：当期有、上一周期无
  growthRate: number | null; // 订单量环比；新品为 null
}

const formatGrowth = (rate: number) => `${rate >= 0 ? '+' : ''}${Math.round(rate * 100)}%`;

/* ---------------- 浅色分析区：订单与商品 + 成交爆品榜 ---------------- */

type ItemSortMode = 'amount' | 'count';

const DashboardCharts: React.FC<{
  analytics: OrderAnalytics;
  itemNames: Record<string, string>;
  previous?: OrderAnalytics;
  /** 参与统计的订单明细（客单价分布/买家构成在前端聚合） */
  orders?: Order[];
  ordersLoading?: boolean;
}> = ({ analytics, itemNames, previous, orders = [], ordersLoading = false }) => {
  const itemStats = analytics.item_stats || [];
  const [itemSort, setItemSort] = useState<ItemSortMode>('amount');

  // 商品成交榜（销量/销售额双维度合并为一个榜单，前 8）
  const rankRows = useMemo(() => {
    const sorted = [...itemStats].sort((a, b) => (itemSort === 'amount'
      ? b.total_amount - a.total_amount || b.order_count - a.order_count
      : b.order_count - a.order_count || b.total_amount - a.total_amount));
    return sorted.slice(0, 8).map((entry) => ({
      key: entry.item_id,
      label: itemDisplayName(entry.item_id, itemNames[entry.item_id]),
      value: itemSort === 'amount' ? entry.total_amount : entry.order_count,
      valueLabel: itemSort === 'amount' ? `¥${formatMoney(entry.total_amount)}` : `${entry.order_count} 单`,
      hint: itemSort === 'amount' ? `${entry.order_count} 单` : `¥${formatMoney(entry.total_amount)}`,
    }));
  }, [itemNames, itemSort, itemStats]);

  // 订单状态分布（语义色占比条）
  const statusRows = (analytics.status_stats || []).map((entry) => {
    const meta = statusMetaOf(entry.status);
    return { key: entry.status, label: meta.label, count: entry.count, color: meta.bar };
  });

  // 客单价分布与买家构成从订单明细聚合；明细尚未到达时显示加载占位
  const ordersPending = ordersLoading && orders.length === 0;
  const priceBins = useMemo(() => buildPriceBins(orders), [orders]);
  const buyerMix = useMemo(() => buildBuyerMix(orders), [orders]);

  // 账号贡献（后端按金额降序 Top 20，这里取前 6）
  const accountRows = (analytics.account_stats || []).slice(0, 6).map((entry) => ({
    key: entry.cookie_id,
    label: entry.account_name,
    amount: entry.total_amount,
    count: entry.order_count,
  }));

  // 成交爆品榜：以当期商品为基准，与上一周期订单量做环比
  // hasPrevious 用于区分"无环比数据"（首次/单周期）与"有数据但无爆品"两种空态
  const prevStats = previous?.item_stats || [];
  const hasPrevious = prevStats.length > 0;
  const prevOrderById = new Map<string, number>();
  for (const entry of prevStats) prevOrderById.set(entry.item_id, entry.order_count);

  const hotItems: HotItemRow[] = itemStats
    // 噪音过滤：当期至少 2 单才纳入爆品，避免 1→2 单=100% 的低基数虚高
    .filter((entry) => entry.order_count >= 2)
    .map((entry) => {
      const prevCount = prevOrderById.get(entry.item_id);
      const isNew = prevCount === undefined || prevCount === 0;
      return {
        itemId: entry.item_id,
        name: itemDisplayName(entry.item_id, itemNames[entry.item_id]),
        orderCount: entry.order_count,
        totalAmount: entry.total_amount,
        avgAmount: entry.avg_amount,
        isNew,
        growthRate: isNew ? null : (entry.order_count - prevCount!) / prevCount!,
      };
    })
    // 排序：新品优先（按当期订单量），其余按环比增长率降序
    .sort((a, b) => {
      if (a.isNew !== b.isNew) return a.isNew ? -1 : 1;
      if (a.isNew && b.isNew) return b.orderCount - a.orderCount;
      return (b.growthRate ?? 0) - (a.growthRate ?? 0);
    })
    .slice(0, 8);

  const loadingPlaceholder = (
    <div className="flex h-24 items-center justify-center text-sm text-gray-400">数据加载中...</div>
  );

  return (
    <div className="space-y-5">
      <section className={`${PANEL_CLASS} p-6`}>
        <div className="mb-5 flex items-center gap-2">
          <Layers className="h-5 w-5 text-gray-400" />
          <h3 className="text-base font-extrabold text-gray-900">订单与商品</h3>
        </div>
        <div className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-2">
          <div className="lg:row-span-2" data-testid="item-rank-panel">
            <div className="flex items-start justify-between gap-3">
              <PanelTitle
                title="商品成交榜"
                sub={itemSort === 'amount' ? '按销售额排序 · 前 8' : '按订单量排序 · 前 8'}
              />
              <div className="flex shrink-0 items-center rounded-lg bg-gray-100 p-0.5" role="group" aria-label="商品成交榜排序方式">
                {([['amount', '销售额'], ['count', '订单量']] as Array<[ItemSortMode, string]>).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setItemSort(mode)}
                    aria-pressed={itemSort === mode}
                    className={`rounded-md px-2.5 py-1 text-xs font-bold transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-900 ${
                      itemSort === mode ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-400 hover:text-gray-600'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <RankList rows={rankRows} emptyText="暂无数据" />
          </div>
          <div>
            <PanelTitle
              title="客单价分布"
              sub={`按订单实付金额分桶${priceBins.excluded > 0 ? ` · ${priceBins.excluded} 笔金额缺失未计入` : ''}`}
            />
            {ordersPending ? loadingPlaceholder : <Histogram bins={priceBins.bins} emptyText="暂无订单明细" />}
          </div>
          <div>
            <PanelTitle title="买家构成" sub="所选周期内下单 ≥ 2 次计为复购" />
            {ordersPending ? loadingPlaceholder : (
              <DonutMix
                total={buyerMix.total}
                totalLabel="下单买家"
                primary={{ label: '复购买家', count: buyerMix.repeat }}
                secondary={{ label: '单次买家', count: buyerMix.single }}
                footnote={buyerMix.total > 0
                  ? <>周期内复购率 <span className="font-bold text-gray-700">{((buyerMix.repeat / buyerMix.total) * 100).toFixed(1)}%</span>，按下单次数统计，不涉及客户画像</>
                  : undefined}
                emptyText="暂无买家数据"
              />
            )}
          </div>
          <div>
            <PanelTitle title="账号贡献" sub="按销售额降序 · 前 6" />
            <ContributionList rows={accountRows} emptyText="暂无账号成交数据" />
          </div>
          <div>
            <PanelTitle title="订单状态分布" sub="按订单量统计 · 退款完成订单已扣除" />
            <ShareBars rows={statusRows} emptyText="暂无数据" labelWidthClass="w-16" />
          </div>
        </div>
      </section>

      <section className={`${PANEL_CLASS} p-6`} data-testid="hot-items-panel">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Flame className="h-5 w-5 text-gray-400" />
          <h3 className="text-base font-extrabold text-gray-900">成交爆品榜</h3>
          <span className="text-sm text-gray-400">当期订单量 ≥ 2，按环比增长排序</span>
        </div>
        {hotItems.length === 0 ? (
          <div className="flex h-28 items-center justify-center text-sm text-gray-400">
            {hasPrevious ? '暂无符合条件的成交爆品（当期订单量需 ≥ 2）' : '暂无环比数据，需至少两个周期'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-xs text-gray-400">
                  <th className="pb-3 font-medium">商品</th>
                  <th className="pb-3 text-right font-medium">当期</th>
                  <th className="pb-3 text-right font-medium">环比</th>
                  <th className="pb-3 text-right font-medium">销售额</th>
                  <th className="pb-3 text-right font-medium">客单价</th>
                </tr>
              </thead>
              <tbody>
                {hotItems.map((item) => (
                  <tr key={item.itemId} className="border-b border-gray-50 last:border-0">
                    <td className="max-w-[300px] py-3 pr-4">
                      <span className="block truncate font-medium text-gray-900" title={item.name}>{item.name}</span>
                    </td>
                    <td className="py-3 text-right tabular-nums text-gray-700">{item.orderCount} 单</td>
                    <td className="py-3 text-right">
                      {item.isNew ? (
                        <span className="inline-flex items-center rounded-md bg-gray-900 px-2 py-0.5 text-xs font-bold text-white">新品</span>
                      ) : (
                        <span className={`inline-flex items-center gap-1 font-bold tabular-nums ${item.growthRate! >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                          {item.growthRate! >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                          {formatGrowth(item.growthRate!)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-right tabular-nums text-gray-700">¥{formatMoney(item.totalAmount)}</td>
                    <td className="py-3 text-right tabular-nums text-gray-700">¥{formatMoney(item.avgAmount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default DashboardCharts;
