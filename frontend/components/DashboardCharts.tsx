import React from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ShoppingCart, TrendingUp, TrendingDown } from 'lucide-react';
import type { OrderAnalytics } from '../types';

const COLORS = ['#FFE815', '#3B82F6', '#10B981', '#F59E0B', '#E11D48'];
const CHART_INITIAL_DIMENSION = { width: 320, height: 240 } as const;

// 订单状态中文标签，与订单列表/仪表盘保持一致
const STATUS_LABELS: Record<string, string> = {
  processing: '处理中',
  pending_ship: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunding: '退款中',
  unknown: '未知',
};

const shortName = (value: string, length: number) => (
  value.length > length ? `${value.slice(0, length)}...` : value
);

// 成交爆品榜单行：当期指标 + 相对上一周期的订单量环比
interface HotItemRow {
  itemId: string;
  name: string;
  orderCount: number;
  totalAmount: number;
  avgAmount: number;
  isNew: boolean;          // 新品：当期有、上一周期无
  growthRate: number | null; // 订单量环比；新品为 null
}

const DashboardCharts: React.FC<{
  analytics: OrderAnalytics;
  itemNames: Record<string, string>;
  previous?: OrderAnalytics;
}> = ({ analytics, itemNames, previous }) => {
  const chartData = analytics.daily_stats.map((entry) => ({
    name: entry.date.slice(5),
    amount: entry.amount,
    orders: entry.order_count || 0,
  })).reverse();
  const totalOrders = analytics.revenue_stats.total_orders || 0;
  const itemStats = analytics.item_stats || [];
  const productSales = itemStats.slice(0, 10).map((entry) => ({
    name: shortName(itemNames[entry.item_id] || entry.item_id, 12),
    sales: entry.order_count,
  }));
  const orderShares = itemStats.slice(0, 6).map((entry, index) => ({
    name: shortName(itemNames[entry.item_id] || entry.item_id, 10),
    value: entry.order_count,
    color: COLORS[index % COLORS.length],
  }));

  // 订单状态分布（饼图）
  const statusStats = analytics.status_stats || [];
  const statusShares = statusStats.map((entry, index) => ({
    name: STATUS_LABELS[entry.status] || entry.status,
    value: entry.count,
    color: COLORS[index % COLORS.length],
  }));
  const totalStatusCount = statusStats.reduce((sum, entry) => sum + entry.count, 0);

  // 地区分布（按收货城市 Top 10，横向柱状图）
  const cityStats = analytics.city_stats || [];
  const cityData = cityStats.slice(0, 10).map((entry) => ({
    name: shortName(entry.city, 6),
    orders: entry.order_count,
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
        name: shortName(itemNames[entry.item_id] || entry.item_id, 14),
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

  const formatGrowth = (rate: number) => `${rate >= 0 ? '+' : ''}${Math.round(rate * 100)}%`;

  return (
    <div className="space-y-6">
      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-6">
          <h3 className="text-lg font-bold text-gray-900">营收趋势</h3>
          <p className="mt-1 text-sm text-gray-400">所选周期内的每日销售额</p>
        </div>
        <div className="h-[320px] w-full">
          {chartData.length === 0 || analytics.revenue_stats.total_amount === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-gray-400">
              <ShoppingCart className="mb-3 h-12 w-12 opacity-20" />
              <p className="font-medium">暂无营收数据</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
              <AreaChart data={chartData} margin={{ top: 12, right: 12, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="dashboardRevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#FFE815" stopOpacity={0.45} />
                    <stop offset="95%" stopColor="#FFE815" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="#F3F4F6" strokeDasharray="3 3" />
                <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                <YAxis axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 12 }} />
                <Tooltip formatter={(value) => `¥${Number(value).toFixed(2)}`} />
                <Area type="monotone" dataKey="amount" stroke="#D6B500" strokeWidth={3} fill="url(#dashboardRevenue)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="ios-card rounded-2xl p-6">
          <h3 className="mb-5 text-lg font-bold text-gray-900">商品销量排行</h3>
          <div className="h-[280px]">
            {productSales.length === 0 ? <div className="flex h-full items-center justify-center text-gray-400">暂无数据</div> : (
              <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                <BarChart data={productSales} layout="vertical" margin={{ left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal vertical={false} stroke="#F3F4F6" />
                  <XAxis type="number" axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" axisLine={false} tickLine={false} width={105} />
                  <Tooltip />
                  <Bar dataKey="sales" fill="#111827" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>

        <section className="ios-card rounded-2xl p-6">
          <h3 className="mb-5 text-lg font-bold text-gray-900">商品下单占比</h3>
          <div className="h-[280px]">
            {orderShares.length === 0 || totalOrders === 0 ? <div className="flex h-full items-center justify-center text-gray-400">暂无数据</div> : (
              <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                <PieChart>
                  <Pie data={orderShares} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}>
                    {orderShares.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                  </Pie>
                  <Tooltip />
                  <Legend verticalAlign="bottom" iconType="circle" />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="ios-card rounded-2xl p-6">
          <h3 className="mb-1 text-lg font-bold text-gray-900">订单状态分布</h3>
          <p className="mb-4 text-sm text-gray-400">仅统计待发货/已发货/已完成订单</p>
          <div className="h-[280px]">
            {statusShares.length === 0 || totalStatusCount === 0 ? <div className="flex h-full items-center justify-center text-gray-400">暂无数据</div> : (
              <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                <PieChart>
                  <Pie data={statusShares} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}>
                    {statusShares.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                  </Pie>
                  <Tooltip formatter={(value) => `${value} 单`} />
                  <Legend verticalAlign="bottom" iconType="circle" />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>

        <section className="ios-card rounded-2xl p-6">
          <h3 className="mb-1 text-lg font-bold text-gray-900">地区分布</h3>
          <p className="mb-4 text-sm text-gray-400">收货城市订单量 Top 10</p>
          <div className="h-[280px]">
            {cityData.length === 0 ? <div className="flex h-full items-center justify-center text-gray-400">暂无收货城市数据</div> : (
              <ResponsiveContainer width="100%" height="100%" initialDimension={CHART_INITIAL_DIMENSION}>
                <BarChart data={cityData} layout="vertical" margin={{ left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal vertical={false} stroke="#F3F4F6" />
                  <XAxis type="number" axisLine={false} tickLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" axisLine={false} tickLine={false} width={70} />
                  <Tooltip formatter={(value) => `${value} 单`} />
                  <Bar dataKey="orders" fill="#3B82F6" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>
      </div>

      <section className="ios-card rounded-2xl p-6">
        <div className="mb-4 flex items-center gap-2">
          <h3 className="text-lg font-bold text-gray-900">成交爆品榜</h3>
          <span className="text-sm text-gray-400">当期订单量 ≥ 2，按环比增长排序</span>
        </div>
        {hotItems.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-sm text-gray-400">
            {hasPrevious ? '暂无符合条件的成交爆品（当期订单量需 ≥ 2）' : '暂无环比数据，需至少两个周期'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
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
                    <td className="py-3 font-medium text-gray-900">{item.name}</td>
                    <td className="py-3 text-right text-gray-700">{item.orderCount} 单</td>
                    <td className="py-3 text-right">
                      {item.isNew ? (
                        <span className="inline-flex items-center rounded-md bg-[#FFE815] px-2 py-0.5 text-xs font-bold text-black">🆕 新品</span>
                      ) : (
                        <span className={`inline-flex items-center gap-1 font-bold ${item.growthRate! >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                          {item.growthRate! >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                          {formatGrowth(item.growthRate!)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-right text-gray-700">¥{item.totalAmount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
                    <td className="py-3 text-right text-gray-700">¥{item.avgAmount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
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
