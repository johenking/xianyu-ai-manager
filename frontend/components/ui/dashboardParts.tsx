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

/* ---------------- 类目区分色（非语义场景，避免品牌黄大面积作数据色） ---------------- */

export const CATEGORY_COLORS = ['#111827', '#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#14B8A6', '#F97316'];

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
    <div className="space-y-2.5">
      {rows.map((row) => (
        <div key={row.key} className="flex items-center gap-3">
          <div className={`${labelWidthClass} shrink-0 truncate text-[13px] font-medium text-gray-700`} title={row.label}>
            {row.label}
          </div>
          <div className="h-4 min-w-0 flex-1 overflow-hidden rounded bg-gray-100">
            <div
              className="h-full rounded"
              style={{ width: `${Math.max(2, (row.count / total) * 100)}%`, background: row.color }}
            />
          </div>
          <div className="w-20 shrink-0 text-right text-[13px] text-gray-600">
            {formatCount(row.count)} {unit}
          </div>
          <div className="w-14 shrink-0 text-right text-xs text-gray-400">
            {((row.count / total) * 100).toFixed(1)}%
          </div>
          {row.hint !== undefined && (
            <div className="hidden w-24 shrink-0 text-right text-xs text-gray-400 xl:block">{row.hint}</div>
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
          <div className="w-16 shrink-0 text-right text-[13px] font-bold text-gray-900">{row.valueLabel}</div>
          {row.hint !== undefined && (
            <div className="hidden w-32 shrink-0 text-right text-xs text-gray-400 lg:block">{row.hint}</div>
          )}
        </div>
      ))}
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
