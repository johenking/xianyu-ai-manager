// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { OrderAnalytics } from '../types';
import DashboardCharts from './DashboardCharts';

// recharts 的 ResponsiveContainer 在 jsdom 下拿不到尺寸会告警但不影响断言，
// 这里用固定尺寸桩替换，聚焦业务渲染逻辑。
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 240 }}>{children}</div>
    ),
  };
});

const baseAnalytics: OrderAnalytics = {
  revenue_stats: { total_amount: 1280, total_orders: 12 },
  daily_stats: [{ date: '2026-07-10', amount: 680, order_count: 6 }],
  item_stats: [
    { item_id: 'A1', order_count: 8, total_amount: 800, avg_amount: 100 },
  ],
  status_stats: [
    { status: 'completed', count: 7, amount: 700 },
    { status: 'pending_ship', count: 3, amount: 300 },
    { status: 'shipped', count: 2, amount: 280 },
  ],
  city_stats: [
    { city: '杭州', order_count: 5, total_amount: 500 },
    { city: '上海', order_count: 4, total_amount: 420 },
  ],
};

afterEach(() => {
  cleanup();
});

describe('DashboardCharts 状态与地区分布', () => {
  it('有数据时渲染订单状态分布与地区分布区块', () => {
    render(<DashboardCharts analytics={baseAnalytics} itemNames={{ A1: '测试商品' }} />);
    // recharts 图表内部在 jsdom 下不渲染，这里断言区块标题与说明（图表外部元素）
    expect(screen.getByText('订单状态分布')).toBeInTheDocument();
    expect(screen.getByText('仅统计待发货/已发货/已完成订单')).toBeInTheDocument();
    expect(screen.getByText('地区分布')).toBeInTheDocument();
    expect(screen.getByText('收货城市订单量 Top 10')).toBeInTheDocument();
    // 有城市数据时不应出现城市空态
    expect(screen.queryByText('暂无收货城市数据')).not.toBeInTheDocument();
  });

  it('缺少 status_stats/city_stats 时显示空态而不报错', () => {
    const empty: OrderAnalytics = {
      revenue_stats: { total_amount: 0, total_orders: 0 },
      daily_stats: [],
    };
    render(<DashboardCharts analytics={empty} itemNames={{}} />);
    expect(screen.getByText('订单状态分布')).toBeInTheDocument();
    expect(screen.getByText('暂无收货城市数据')).toBeInTheDocument();
  });
});
