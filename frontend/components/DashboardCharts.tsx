import React from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Flame, Layers, TrendingDown, TrendingUp } from 'lucide-react';
import type { OrderAnalytics } from '../types';
import {
  CATEGORY_COLORS,
  PANEL_CLASS,
  PanelTitle,
  RankList,
  ShareBars,
  formatMoney,
  itemDisplayName,
  statusMetaOf,
  type DailyPoint,
} from './ui/dashboardParts';

const CHART_INITIAL_DIMENSION = { width: 320, height: 180 } as const;

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

export const HeroTrend: React.FC<{ points: DailyPoint[] }> = ({ points }) => {
  const hasData = points.some((point) => point.amount > 0 || point.orders > 0);
  if (!points.length || !hasData) {
    return (
      <div className="flex h-[180px] items-center justify-center rounded-xl bg-white/[0.04] text-sm text-slate-500">
        所选周期暂无成交
      </div>
    );
  }
  const dense = points.length > 12;
  return (
    <div className="h-[180px] w-full">
      <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
        <AreaChart data={points} margin={{ top: 6, right: 4, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id="heroRevenue" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#FFE815" stopOpacity={0.32} />
              <stop offset="100%" stopColor="#FFE815" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.07)" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            axisLine={false}
            tickLine={false}
            tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }}
            interval={dense ? Math.ceil(points.length / 8) : 0}
          />
          <YAxis axisLine={false} tickLine={false} tick={{ fill: 'rgba(255,255,255,0.45)', fontSize: 11 }} width={44} />
          <Tooltip content={<HeroTrendTooltip />} cursor={{ stroke: 'rgba(255,255,255,0.2)' }} />
          <Area type="monotone" dataKey="amount" name="销售额" stroke="#FFE815" strokeWidth={2.5} fill="url(#heroRevenue)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
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

const DashboardCharts: React.FC<{
  analytics: OrderAnalytics;
  itemNames: Record<string, string>;
  previous?: OrderAnalytics;
}> = ({ analytics, itemNames, previous }) => {
  const itemStats = analytics.item_stats || [];

  // 商品销量排行（HTML 条形列表，名称完整显示，前 10）
  const rankRows = itemStats.slice(0, 10).map((entry) => ({
    key: entry.item_id,
    label: itemDisplayName(entry.item_id, itemNames[entry.item_id]),
    value: entry.order_count,
    valueLabel: `${entry.order_count} 单`,
  }));

  // 商品下单占比（水平占比条，前 6，附金额说明）
  const shareRows = itemStats.slice(0, 6).map((entry, index) => ({
    key: entry.item_id,
    label: itemDisplayName(entry.item_id, itemNames[entry.item_id]),
    count: entry.order_count,
    color: CATEGORY_COLORS[index % CATEGORY_COLORS.length],
    hint: `¥${formatMoney(entry.total_amount)}`,
  }));

  // 订单状态分布（语义色占比条）
  const statusRows = (analytics.status_stats || []).map((entry) => {
    const meta = statusMetaOf(entry.status);
    return { key: entry.status, label: meta.label, count: entry.count, color: meta.bar };
  });

  // 地区分布（收货城市 Top 10 占比条）
  const cityRows = (analytics.city_stats || []).slice(0, 10).map((entry) => ({
    key: entry.city,
    label: entry.city,
    count: entry.order_count,
    color: '#3B82F6',
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

  return (
    <div className="space-y-5">
      <section className={`${PANEL_CLASS} p-6`}>
        <div className="mb-5 flex items-center gap-2">
          <Layers className="h-5 w-5 text-blue-600" />
          <h3 className="text-base font-extrabold text-gray-900">订单与商品</h3>
        </div>
        <div className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-2">
          <div>
            <PanelTitle title="订单状态分布" sub="仅统计待发货/已发货/已完成订单" />
            <ShareBars rows={statusRows} emptyText="暂无数据" labelWidthClass="w-16" />
          </div>
          <div>
            <PanelTitle title="商品销量排行" sub="按成交订单量排序 · 前 10" />
            <RankList rows={rankRows} emptyText="暂无数据" />
          </div>
          <div>
            <PanelTitle title="商品下单占比" sub="按订单量占比 · 前 6" />
            <ShareBars rows={shareRows} emptyText="暂无数据" labelWidthClass="w-36" />
          </div>
          <div>
            <PanelTitle title="地区分布" sub="收货城市订单量 Top 10" />
            <ShareBars rows={cityRows} emptyText="暂无收货城市数据" labelWidthClass="w-16" />
          </div>
        </div>
      </section>

      <section className={`${PANEL_CLASS} p-6`} data-testid="hot-items-panel">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Flame className="h-5 w-5 text-orange-500" />
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
                    <td className="py-3 text-right text-gray-700">{item.orderCount} 单</td>
                    <td className="py-3 text-right">
                      {item.isNew ? (
                        <span className="inline-flex items-center rounded-md bg-gray-900 px-2 py-0.5 text-xs font-bold text-white">新品</span>
                      ) : (
                        <span className={`inline-flex items-center gap-1 font-bold ${item.growthRate! >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                          {item.growthRate! >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                          {formatGrowth(item.growthRate!)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-right text-gray-700">¥{formatMoney(item.totalAmount)}</td>
                    <td className="py-3 text-right text-gray-700">¥{formatMoney(item.avgAmount)}</td>
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
