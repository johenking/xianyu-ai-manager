import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, AlertCircle, Clock, RefreshCw, Repeat, Users } from 'lucide-react';
import type { BuyerBehaviorAnalytics, TrafficAnalytics } from '../types';
import { getBuyerBehaviorAnalytics, getTrafficAnalytics } from '../services/api';

// 后端 strftime('%w')：'0'=周日 ... '6'=周六。按经营习惯以周一起头展示。
const WEEKDAY_LABELS: Record<string, string> = {
  '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六',
};
const WEEKDAY_ORDER = ['1', '2', '3', '4', '5', '6', '0'];

const formatAmount = (value: number) => `¥${Number(value || 0).toFixed(2)}`;

const BusinessInsights: React.FC<{
  range: { start_date: string; end_date: string };
}> = ({ range }) => {
  const [traffic, setTraffic] = useState<TrafficAnalytics | null>(null);
  const [buyers, setBuyers] = useState<BuyerBehaviorAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const requestGeneration = useRef(0);

  const load = useCallback(async () => {
    if (!range.start_date || !range.end_date) return;
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    setLoading(true);
    setError('');
    try {
      const [trafficData, buyerData] = await Promise.all([
        getTrafficAnalytics(range),
        getBuyerBehaviorAnalytics(range),
      ]);
      if (requestGeneration.current !== generation) return;
      setTraffic(trafficData);
      setBuyers(buyerData);
    } catch (loadError) {
      if (requestGeneration.current !== generation) return;
      setTraffic(null);
      setBuyers(null);
      setError(loadError instanceof Error ? loadError.message : '经营分析加载失败');
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    void load();
  }, [load]);

  // 时段分布补齐 0-23 小时缺口，避免柱状图跳空导致误读全天规律
  const hourlyData = useMemo(() => {
    const byHour = new Map<number, TrafficAnalytics['hourly'][number]>();
    for (const entry of traffic?.hourly ?? []) byHour.set(entry.hour, entry);
    return Array.from({ length: 24 }, (_, hour) => {
      const entry = byHour.get(hour);
      return {
        label: `${String(hour).padStart(2, '0')}时`,
        order_count: entry?.order_count || 0,
        amount: entry?.amount || 0,
      };
    });
  }, [traffic]);

  const weekdayData = useMemo(() => {
    const byWeekday = new Map<string, TrafficAnalytics['weekday'][number]>();
    for (const entry of traffic?.weekday ?? []) byWeekday.set(entry.weekday, entry);
    return WEEKDAY_ORDER.map((key) => {
      const entry = byWeekday.get(key);
      return {
        label: WEEKDAY_LABELS[key],
        order_count: entry?.order_count || 0,
        amount: entry?.amount || 0,
      };
    });
  }, [traffic]);

  const frequencyData = useMemo(() => (buyers?.frequency || []).map((entry) => ({
    label: `${entry.order_count}单`,
    buyer_count: entry.buyer_count,
  })), [buyers]);

  const coverage = traffic?.coverage;
  const hasTraffic = (coverage?.with_ordered_at || 0) > 0;
  const coverageShort = coverage && coverage.total_orders > 0
    && coverage.with_ordered_at < coverage.total_orders;
  const buyerSummary = buyers?.summary;
  const hasBuyers = (buyerSummary?.total_buyers || 0) > 0;

  if (loading) {
    return (
      <div className="ios-card flex h-64 items-center justify-center rounded-2xl bg-white text-sm text-gray-400" role="status" aria-label="经营分析加载中">
        <Activity className="mr-2 h-5 w-5 animate-spin text-[#D6B500]" />经营分析加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="ios-card flex flex-col items-center justify-center gap-3 rounded-2xl bg-white py-14 text-center">
        <AlertCircle className="h-8 w-8 text-red-500" />
        <p className="text-sm text-gray-500">{error}</p>
        <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-bold text-white">
          <RefreshCw className="h-4 w-4" />重试
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="ios-card rounded-2xl p-6 sm:p-8">
        <div className="mb-2 flex items-center gap-2">
          <Clock className="h-5 w-5 text-[#D6B500]" />
          <h3 className="text-lg font-bold text-gray-900">时段流量分析</h3>
        </div>
        <p className="mb-5 text-sm text-gray-400">按真实成交时间统计的下单时段规律（东八区）</p>

        {coverageShort && (
          <div className="mb-5 flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-xs text-amber-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              时段分布基于 {Math.round((coverage!.coverage_rate) * 100)}% 有成交时间的订单
              （{coverage!.with_ordered_at}/{coverage!.total_orders} 笔）；部分旧订单缺成交时间，未计入时段图表。
            </span>
          </div>
        )}

        {!hasTraffic ? (
          <div className="flex h-56 flex-col items-center justify-center text-gray-400">
            <Clock className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无带成交时间的订单</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-3 text-sm font-bold text-gray-700">按小时分布</p>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
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
                <ResponsiveContainer width="100%" height="100%">
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
        <div className="mb-2 flex items-center gap-2">
          <Users className="h-5 w-5 text-[#D6B500]" />
          <h3 className="text-lg font-bold text-gray-900">买家行为分析</h3>
        </div>
        <p className="mb-5 text-sm text-gray-400">复购与下单频次（仅统计下单行为，不涉及客户画像）</p>

        {!hasBuyers ? (
          <div className="flex h-56 flex-col items-center justify-center text-gray-400">
            <Users className="mb-3 h-12 w-12 opacity-20" />
            <p className="font-medium">暂无买家数据</p>
          </div>
        ) : (
          <>
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="rounded-xl border border-gray-100 bg-gray-50/60 p-4">
                <p className="text-sm text-gray-500">下单买家</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{buyerSummary!.total_buyers}</p>
              </div>
              <div className="rounded-xl border border-gray-100 bg-gray-50/60 p-4">
                <p className="text-sm text-gray-500">复购买家</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{buyerSummary!.repeat_buyers}</p>
              </div>
              <div className="rounded-xl border border-gray-100 bg-[#FFE815]/20 p-4">
                <p className="flex items-center gap-1 text-sm text-gray-600"><Repeat className="h-3.5 w-3.5" />复购率</p>
                <p className="mt-1 text-2xl font-extrabold text-gray-900">{(buyerSummary!.repeat_rate * 100).toFixed(1)}%</p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-3 text-sm font-bold text-gray-700">下单频次分布</p>
                <div className="h-[240px]">
                  <ResponsiveContainer width="100%" height="100%">
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
                <div className="max-h-[240px] overflow-auto rounded-xl border border-gray-100">
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
