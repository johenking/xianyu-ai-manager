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
import { ShoppingCart } from 'lucide-react';
import type { OrderAnalytics } from '../types';

const COLORS = ['#FFE815', '#3B82F6', '#10B981', '#F59E0B', '#E11D48'];

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

const DashboardCharts: React.FC<{
  analytics: OrderAnalytics;
  itemNames: Record<string, string>;
}> = ({ analytics, itemNames }) => {
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
            <ResponsiveContainer width="100%" height="100%">
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
              <ResponsiveContainer width="100%" height="100%">
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
              <ResponsiveContainer width="100%" height="100%">
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
              <ResponsiveContainer width="100%" height="100%">
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
              <ResponsiveContainer width="100%" height="100%">
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
    </div>
  );
};

export default DashboardCharts;
