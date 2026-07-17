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
    </div>
  );
};

export default DashboardCharts;
