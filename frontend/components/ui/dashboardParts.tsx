import React from 'react';
import type { OrderStatus } from '../../types';

/**
 * 「深色驾驶舱」方案的共享展示部件与工具。
 * 供 Dashboard / DashboardCharts / BusinessInsights / OrderList / Keywords 复用，保证面板语言一致：
 * 中圆角面板、语义状态色、水平占比条替代饼图、HTML 排行列表替代纵向柱图。
 */

/* ---------------- 数字格式 ---------------- */

export const formatMoney = (value: number): string => (
  Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
);

export const formatCount = (value: number): string => Number(value || 0).toLocaleString('zh-CN');

/* ---------------- 订单状态语义色（全仪表盘统一） ---------------- */

export interface StatusMeta {
  label: string;
  /** 徽章样式 */
  chip: string;
  /** 占比条填充色 */
  bar: string;
}

/**
 * 订单状态语义色：全站唯一真源，覆盖 OrderStatus 的全部 9 个取值。
 * 退款中/已退款、已取消/退款已关闭 这两组语义相近，用同色系深浅区分而不共用一个色值。
 */
export const STATUS_META: Record<OrderStatus, StatusMeta> = {
  processing: { label: '处理中', chip: 'bg-amber-100 text-amber-800', bar: '#F59E0B' },
  pending_ship: { label: '待发货', chip: 'bg-[#FFE815] text-black', bar: '#FFE815' },
  shipped: { label: '已发货', chip: 'bg-blue-100 text-blue-700', bar: '#3B82F6' },
  completed: { label: '已完成', chip: 'bg-emerald-100 text-emerald-700', bar: '#10B981' },
  refunding: { label: '退款中', chip: 'bg-red-100 text-red-600', bar: '#EF4444' },
  refunded: { label: '已退款', chip: 'bg-red-100 text-red-700', bar: '#B91C1C' },
  refund_cancelled: { label: '退款已关闭', chip: 'bg-gray-100 text-gray-600', bar: '#6B7280' },
  cancelled: { label: '已取消', chip: 'bg-gray-100 text-gray-500', bar: '#9CA3AF' },
  // 徽章用琥珀色提示「需要人工核对」，虚线边框再与实底的「处理中」区分；
  // 占比条则保持中性灰，避免在状态分布图里与「处理中」的琥珀色混淆。
  unknown: { label: '待核对', chip: 'border border-dashed border-amber-300 bg-amber-50 text-amber-700', bar: '#D1D5DB' },
};

export const statusMetaOf = (status: string): StatusMeta => STATUS_META[status as OrderStatus] || STATUS_META.unknown;

export const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const meta = statusMetaOf(status);
  return (
    <span className={`inline-flex items-center rounded-md px-2.5 py-1 text-xs font-bold ${meta.chip}`}>
      {meta.label}
    </span>
  );
};

/* ---------------- 商品名兜底 ---------------- */

/** 商品缺标题时显示「未命名商品 · 尾号XXXX」，避免标题与 ID 重复展示同一串数字 */
export const itemDisplayName = (itemId: string, title?: string | null): string => {
  const clean = String(title || '').trim();
  if (clean && clean !== itemId) return clean;
  const tail = String(itemId || '').slice(-4);
  return tail ? `未命名商品 · 尾号${tail}` : '未命名商品';
};

/* ---------------- 类目区分色（非语义场景，收敛为墨色阶梯 + 少量点缀，贴近原生质感） ---------------- */

export const CATEGORY_COLORS = ['#111827', '#334155', '#0EA5E9', '#10B981', '#64748B', '#94A3B8', '#CBD5E1', '#F59E0B'];

/** 排行/贡献类列表的单色阶梯：第一名最深，依次变浅，避免彩虹配色 */
export const RANK_SHADES = ['#111827', '#374151', '#6B7280', '#9CA3AF', '#C4C9D1', '#D8DCE1'];

/* ---------------- 面板与标题 ---------------- */

/** 浅色内容面板：中圆角、细边、极轻投影 */
export const PANEL_CLASS = 'rounded-[16px] border border-black/[0.05] bg-white shadow-[0_4px_16px_rgba(0,0,0,0.03)]';

export const PanelTitle: React.FC<{
  icon?: React.ReactNode;
  title: string;
  badge?: string;
  badgeClass?: string;
  sub?: string;
}> = ({ icon, title, badge, badgeClass = 'bg-gray-100 text-gray-500', sub }) => (
  <div className="mb-4">
    <div className="flex flex-wrap items-center gap-2">
      {icon}
      <h3 className="text-[15px] font-bold text-gray-900">{title}</h3>
      {badge && <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${badgeClass}`}>{badge}</span>}
    </div>
    {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
  </div>
);

/** 低调的数据说明/不足提示（细边浅底小字，替代大色块警告） */
export const InlineNote: React.FC<{ tone?: 'neutral' | 'warn' | 'good'; children: React.ReactNode }> = ({ tone = 'neutral', children }) => {
  const toneClass = tone === 'warn'
    ? 'border-amber-200 bg-amber-50/60 text-amber-800'
    : tone === 'good'
      ? 'border-emerald-200 bg-emerald-50/60 text-emerald-800'
      : 'border-gray-200 bg-gray-50 text-gray-500';
  return (
    <div className={`flex items-start gap-1.5 rounded-lg border px-3 py-2 text-xs leading-relaxed ${toneClass}`}>
      {children}
    </div>
  );
};

/* ---------------- 水平占比条列表（替代饼图） ---------------- */

export interface ShareBarRow {
  key: string;
  label: string;
  count: number;
  color: string;
  /** 行尾附加说明，如金额 */
  hint?: string;
}

export const ShareBars: React.FC<{
  rows: ShareBarRow[];
  unit?: string;
  emptyText?: string;
  labelWidthClass?: string;
}> = ({ rows, unit = '单', emptyText = '暂无数据', labelWidthClass = 'w-28' }) => {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  if (!rows.length || total <= 0) {
    return <div className="flex h-24 items-center justify-center text-sm text-gray-400">{emptyText}</div>;
  }
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.key} className="flex items-center gap-3">
          <div className={`${labelWidthClass} shrink-0 truncate text-[13px] font-medium text-gray-700`} title={row.label}>
            {row.label}
          </div>
          <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
              style={{ width: `${Math.max(1.5, (row.count / total) * 100)}%`, background: row.color }}
            />
          </div>
          <div className="w-20 shrink-0 text-right text-[13px] font-semibold tabular-nums text-gray-700">
            {formatCount(row.count)} {unit}
          </div>
          <div className="w-14 shrink-0 text-right text-xs tabular-nums text-gray-400">
            {((row.count / total) * 100).toFixed(1)}%
          </div>
          {row.hint !== undefined && (
            <div className="hidden w-24 shrink-0 text-right text-xs tabular-nums text-gray-400 xl:block">{row.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
};

/* ---------------- HTML 排行条形列表（替代 recharts 纵向柱图，名称永不截断开头） ---------------- */

export interface RankRow {
  key: string;
  label: string;
  value: number;
  valueLabel: string;
  hint?: string;
}

export const RankList: React.FC<{ rows: RankRow[]; emptyText?: string }> = ({ rows, emptyText = '暂无数据' }) => {
  if (!rows.length) {
    return <div className="flex h-24 items-center justify-center text-sm text-gray-400">{emptyText}</div>;
  }
  const max = Math.max(...rows.map((row) => row.value), 1);
  return (
    <div className="space-y-1">
      {rows.map((row, index) => (
        <div key={row.key} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-gray-50">
          <span className={`w-5 shrink-0 text-center text-xs font-bold ${index < 3 ? 'text-gray-900' : 'text-gray-300'}`}>
            {index + 1}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-gray-800" title={row.label}>{row.label}</div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-gray-100">
              <div className="h-full rounded bg-gray-900" style={{ width: `${Math.max(2, (row.value / max) * 100)}%` }} />
            </div>
          </div>
          <div className="w-24 shrink-0 text-right text-[13px] font-bold tabular-nums text-gray-900">{row.valueLabel}</div>
          {row.hint !== undefined && (
            <div className="hidden w-20 shrink-0 text-right text-xs tabular-nums text-gray-400 lg:block">{row.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
};

/* ---------------- 分桶直方图（客单价分布等：HTML 柱列，主力桶深色强调） ---------------- */

export interface HistogramBin {
  key: string;
  label: string;
  count: number;
  /** 桶内营收，用于「订单占比 vs 营收占比」的footer说明 */
  amount: number;
}

export const Histogram: React.FC<{
  bins: HistogramBin[];
  unit?: string;
  emptyText?: string;
}> = ({ bins, unit = '单', emptyText = '暂无数据' }) => {
  const totalCount = bins.reduce((sum, bin) => sum + bin.count, 0);
  const totalAmount = bins.reduce((sum, bin) => sum + bin.amount, 0);
  if (!bins.length || totalCount <= 0) {
    return <div className="flex h-24 items-center justify-center text-sm text-gray-400">{emptyText}</div>;
  }
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const topBin = bins.reduce((best, bin) => (bin.count > best.count ? bin : best), bins[0]);
  return (
    <div>
      <div className="flex h-[168px] items-end gap-2.5 sm:gap-3">
        {bins.map((bin) => {
          const isTop = bin === topBin;
          const heightPct = (bin.count / maxCount) * 100;
          return (
            <div
              key={bin.key}
              className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1.5"
              title={`${bin.label}：${formatCount(bin.count)} ${unit} · 营收 ¥${formatMoney(bin.amount)}`}
            >
              <span className={`text-xs font-bold tabular-nums ${isTop ? 'text-gray-900' : 'text-gray-500'}`}>
                {formatCount(bin.count)}
              </span>
              <div
                className={`w-full max-w-[52px] rounded-t-md transition-[height] duration-500 ease-out motion-reduce:transition-none ${isTop ? 'bg-gray-900' : 'bg-gray-200'}`}
                style={{ height: `${Math.max(bin.count > 0 ? 4 : 2, heightPct)}%` }}
              />
              <span className={`truncate text-[11px] ${isTop ? 'font-bold text-gray-900' : 'text-gray-400'}`}>{bin.label}</span>
            </div>
          );
        })}
      </div>
      <p className="mt-3 border-t border-gray-100 pt-2.5 text-xs text-gray-400">
        主力价格带 <span className="font-bold text-gray-700">{topBin.label}</span>
        ：订单占 <span className="font-semibold tabular-nums text-gray-600">{((topBin.count / totalCount) * 100).toFixed(0)}%</span>
        {totalAmount > 0 && (
          <>
            ，营收占 <span className="font-semibold tabular-nums text-gray-600">{((topBin.amount / totalAmount) * 100).toFixed(0)}%</span>
          </>
        )}
      </p>
    </div>
  );
};

/* ---------------- 双色环形（买家构成等：深色为强调项，浅色为余量） ---------------- */

export const DonutMix: React.FC<{
  total: number;
  totalLabel: string;
  /** 深色强调项（如复购买家） */
  primary: { label: string; count: number; hint?: string };
  /** 浅色余量项（如新买家） */
  secondary: { label: string; count: number; hint?: string };
  footnote?: React.ReactNode;
  emptyText?: string;
}> = ({ total, totalLabel, primary, secondary, footnote, emptyText = '暂无数据' }) => {
  if (total <= 0) {
    return <div className="flex h-24 items-center justify-center text-sm text-gray-400">{emptyText}</div>;
  }
  const primaryPct = Math.min(100, Math.max(0, (primary.count / total) * 100));
  const rows = [
    { ...primary, swatch: '#111827', pct: primaryPct },
    { ...secondary, swatch: '#E5E7EB', pct: 100 - primaryPct },
  ];
  return (
    <div>
      <div className="flex items-center gap-5">
        <svg width="124" height="124" viewBox="0 0 42 42" role="img" aria-label={`${totalLabel} ${formatCount(total)}`}>
          <circle cx="21" cy="21" r="15.9" fill="none" stroke="#E5E7EB" strokeWidth="4.6" />
          {primaryPct > 0 && (
            <circle
              cx="21"
              cy="21"
              r="15.9"
              fill="none"
              stroke="#111827"
              strokeWidth="4.6"
              strokeLinecap={primaryPct >= 100 ? 'butt' : 'round'}
              strokeDasharray={`${primaryPct} ${100 - primaryPct}`}
              strokeDashoffset="25"
            />
          )}
          <text x="21" y="20.2" textAnchor="middle" className="fill-gray-900" fontSize="8" fontWeight="800">
            {formatCount(total)}
          </text>
          <text x="21" y="26.8" textAnchor="middle" className="fill-gray-400" fontSize="3.4">
            {totalLabel}
          </text>
        </svg>
        <div className="min-w-0 flex-1 space-y-2.5">
          {rows.map((row) => (
            <div key={row.label} className="flex items-baseline gap-2 text-[13px]">
              <span className="h-2.5 w-2.5 shrink-0 self-center rounded-[4px]" style={{ background: row.swatch }} />
              <span className="shrink-0 font-medium text-gray-700">{row.label}</span>
              <span className="ml-auto shrink-0 font-bold tabular-nums text-gray-900">{formatCount(row.count)}</span>
              <span className="w-12 shrink-0 text-right text-xs tabular-nums text-gray-400">{row.pct.toFixed(1)}%</span>
            </div>
          ))}
          {(primary.hint || secondary.hint) && (
            <p className="text-xs leading-relaxed text-gray-400">{primary.hint || secondary.hint}</p>
          )}
        </div>
      </div>
      {footnote && <p className="mt-3 border-t border-gray-100 pt-2.5 text-xs text-gray-400">{footnote}</p>}
    </div>
  );
};

/* ---------------- 贡献列表（账号贡献等：金额占比条 + 墨色阶梯） ---------------- */

export interface ContributionRow {
  key: string;
  label: string;
  amount: number;
  count: number;
  /** 阶梯色索引；不传按行序取 RANK_SHADES */
  shadeIndex?: number;
}

export const ContributionList: React.FC<{
  rows: ContributionRow[];
  emptyText?: string;
}> = ({ rows, emptyText = '暂无数据' }) => {
  const totalAmount = rows.reduce((sum, row) => sum + row.amount, 0);
  const totalCount = rows.reduce((sum, row) => sum + row.count, 0);
  if (!rows.length || (totalAmount <= 0 && totalCount <= 0)) {
    return <div className="flex h-24 items-center justify-center text-sm text-gray-400">{emptyText}</div>;
  }
  // 金额缺失时退回订单量占比，保证条形仍然可读
  const shareOf = (row: ContributionRow) => (
    totalAmount > 0 ? row.amount / totalAmount : row.count / Math.max(totalCount, 1)
  );
  return (
    <div className="space-y-3">
      {rows.map((row, index) => {
        const share = shareOf(row);
        return (
          <div key={row.key} className="flex items-center gap-3">
            <div className="w-20 shrink-0 truncate text-[13px] font-medium text-gray-700" title={row.label}>
              {row.label}
            </div>
            <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-100">
              <div
                className="h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
                style={{
                  width: `${Math.max(1.5, share * 100)}%`,
                  background: RANK_SHADES[Math.min(row.shadeIndex ?? index, RANK_SHADES.length - 1)],
                }}
              />
            </div>
            <div className="w-[104px] shrink-0 text-right">
              <div className="text-[13px] font-bold tabular-nums leading-tight text-gray-900">¥{formatMoney(row.amount)}</div>
              <div className="text-[11px] tabular-nums leading-tight text-gray-400">{formatCount(row.count)} 单 · {(share * 100).toFixed(1)}%</div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

/* ---------------- 环比徽章 ---------------- */

export type CompareResult =
  | { type: 'new' }
  | { type: 'flat' }
  | { type: 'up' | 'down'; percentLabel: string };

/** 上期为 0 时显示「较上期 新增」而不是误导性的 +100% */
export const compareOf = (current: number, previous: number): CompareResult => {
  if (previous === 0) return current > 0 ? { type: 'new' } : { type: 'flat' };
  const value = ((current - previous) / previous) * 100;
  return {
    type: value >= 0 ? 'up' : 'down',
    percentLabel: `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`,
  };
};

/* ---------------- 日期序列补零 ---------------- */

export interface DailyPoint {
  date: string;
  /** 用于 X 轴的短标签（MM-DD） */
  label: string;
  amount: number;
  orders: number;
  /** 小时趋势用于 tooltip 的完整时间标签 */
  tooltipLabel?: string;
}

export interface HourlyStat {
  hour: number | string;
  amount: number;
  order_count?: number;
}

export interface TrendDelta {
  from: DailyPoint;
  to: DailyPoint;
  delta: number;
}

export interface TrendHighlights {
  peakOrders: DailyPoint | null;
  lowOrders: DailyPoint | null;
  fastestGrowth: TrendDelta | null;
  slowestGrowth: TrendDelta | null;
}

export interface TrendPointSelection {
  chartPoints: DailyPoint[];
  highlightPoints: DailyPoint[];
}

const MAX_FILL_DAYS = 366;

/**
 * 将稀疏的 daily_stats 按 [startDate, endDate] 逐日补零，
 * 让趋势图忠实反映所选范围（无成交日为 0，而不是被跳过）。
 */
export const fillDailySeries = (
  dailyStats: Array<{ date: string; amount: number; order_count?: number }> | undefined,
  startDate: string,
  endDate: string,
): DailyPoint[] => {
  const byDate = new Map<string, { amount: number; orders: number }>();
  for (const entry of dailyStats || []) {
    byDate.set(entry.date, { amount: entry.amount || 0, orders: entry.order_count || 0 });
  }
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
    // 范围不可用时退回按已有数据升序展示，保证图表仍可渲染
    return Array.from(byDate.entries())
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([date, value]) => ({ date, label: date.slice(5), amount: value.amount, orders: value.orders }));
  }
  const points: DailyPoint[] = [];
  const cursor = new Date(start);
  for (let i = 0; i < MAX_FILL_DAYS && cursor <= end; i += 1) {
    const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`;
    const value = byDate.get(iso);
    points.push({ date: iso, label: iso.slice(5), amount: value?.amount || 0, orders: value?.orders || 0 });
    cursor.setDate(cursor.getDate() + 1);
  }
  return points;
};

const parseHour = (value: number | string): number | null => {
  if (typeof value === 'number' && Number.isInteger(value)) {
    return value >= 0 && value < 24 ? value : null;
  }
  const text = String(value).trim();
  const match = text.match(/(?:^|[T\s])(\d{1,2})(?::\d{2})?(?:$|[+-])/)
    || text.match(/^(\d{1,2})(?::\d{2})?$/);
  const hour = Number(match?.[1]);
  return Number.isInteger(hour) && hour >= 0 && hour < 24 ? hour : null;
};

/** 将单日 hourly_stats 补齐为稳定的 24 个小时桶，缺口明确显示为 0。 */
export const fillHourlySeries = (
  hourlyStats: HourlyStat[] | undefined,
  date: string,
): DailyPoint[] => {
  const byHour = new Map<number, { amount: number; orders: number }>();
  for (const entry of hourlyStats || []) {
    const hour = parseHour(entry.hour);
    if (hour === null) continue;
    const previous = byHour.get(hour) || { amount: 0, orders: 0 };
    byHour.set(hour, {
      // Repeated buckets should not make the chart lose an otherwise valid aggregate.
      amount: previous.amount + (Number(entry.amount) || 0),
      orders: previous.orders + (Number(entry.order_count) || 0),
    });
  }
  return Array.from({ length: 24 }, (_, hour) => {
    const value = byHour.get(hour) || { amount: 0, orders: 0 };
    const label = `${String(hour).padStart(2, '0')}:00`;
    return { date: `${date} ${label}`, label, amount: value.amount, orders: value.orders };
  });
};

/**
 * 当前周期仍在进行时，图表保留最新的部分时段，峰谷/涨跌只比较已结束时段。
 * 历史周期直接返回完整点集。
 */
export const selectTrendPoints = (
  points: DailyPoint[],
  granularity: 'hour' | 'day',
  rangeEndDate: string,
  currentDate: string,
  currentHour: number,
): TrendPointSelection => {
  if (rangeEndDate !== currentDate) {
    return { chartPoints: points, highlightPoints: points };
  }
  if (granularity === 'hour') {
    const safeHour = Math.min(23, Math.max(0, Math.trunc(currentHour)));
    return {
      chartPoints: points.slice(0, safeHour + 1),
      highlightPoints: points.slice(0, safeHour),
    };
  }
  return {
    chartPoints: points,
    highlightPoints: points.slice(0, -1),
  };
};

/** 返回订单量峰低点及相邻时段销售额变化的最快/最慢区间。并列时保留较早区间。 */
export const getTrendHighlights = (points: DailyPoint[]): TrendHighlights => {
  if (points.length < 2) {
    return { peakOrders: null, lowOrders: null, fastestGrowth: null, slowestGrowth: null };
  }
  let peakOrders = points[0];
  let lowOrders = points[0];
  for (const point of points.slice(1)) {
    if (point.orders > peakOrders.orders) peakOrders = point;
    if (point.orders < lowOrders.orders) lowOrders = point;
  }
  let fastestGrowth: TrendDelta | null = null;
  let slowestGrowth: TrendDelta | null = null;
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1];
    const to = points[index];
    const delta = to.amount - from.amount;
    const candidate = { from, to, delta };
    if (delta > 0 && (!fastestGrowth || delta > fastestGrowth.delta)) fastestGrowth = candidate;
    if (delta < 0 && (!slowestGrowth || delta < slowestGrowth.delta)) slowestGrowth = candidate;
  }
  return { peakOrders, lowOrders, fastestGrowth, slowestGrowth };
};
