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

describe('DashboardCharts 成交爆品榜', () => {
  const current: OrderAnalytics = {
    revenue_stats: { total_amount: 3000, total_orders: 30 },
    daily_stats: [{ date: '2026-07-10', amount: 1000, order_count: 10 }],
    item_stats: [
      { item_id: 'up', order_count: 12, total_amount: 1680, avg_amount: 140 },   // 环比 4→12 = +200%
      { item_id: 'new', order_count: 8, total_amount: 960, avg_amount: 120 },     // 新品（previous 无）
      { item_id: 'down', order_count: 4, total_amount: 320, avg_amount: 80 },      // 环比 5→4 = -20%
      { item_id: 'low', order_count: 1, total_amount: 50, avg_amount: 50 },        // 当期仅 1 单，应被过滤
    ],
  };
  const previous: OrderAnalytics = {
    revenue_stats: { total_amount: 2000, total_orders: 20 },
    daily_stats: [],
    item_stats: [
      { item_id: 'up', order_count: 4, total_amount: 560, avg_amount: 140 },
      { item_id: 'down', order_count: 5, total_amount: 400, avg_amount: 80 },
    ],
  };
  const names = { up: '复古相机', new: '手办模型', down: '帆布包', low: '小配件' };

  it('渲染环比增长率、新品标记与金额/客单价', () => {
    render(<DashboardCharts analytics={current} previous={previous} itemNames={names} />);
    expect(screen.getByText('成交爆品榜')).toBeInTheDocument();
    expect(screen.getByText('复古相机')).toBeInTheDocument();
    expect(screen.getByText('+200%')).toBeInTheDocument();
    expect(screen.getByText('🆕 新品')).toBeInTheDocument();
    expect(screen.getByText('-20%')).toBeInTheDocument();
    // 金额与客单价展示
    expect(screen.getByText('¥1,680.00')).toBeInTheDocument();
    expect(screen.getByText('¥140.00')).toBeInTheDocument();
    // 当期仅 1 单的商品被噪音过滤，不出现在榜单
    expect(screen.queryByText('小配件')).not.toBeInTheDocument();
  });

  it('无上一周期数据时当期达标商品全部作为新品进榜', () => {
    render(<DashboardCharts analytics={current} itemNames={names} />);
    // 没有 previous，所有当期达标商品（≥2 单）都按新品处理，不算环比
    expect(screen.getByText('复古相机')).toBeInTheDocument();
    expect(screen.getAllByText('🆕 新品').length).toBe(3); // up/new/down 三个达标商品
    expect(screen.queryByText('+200%')).not.toBeInTheDocument();
    // low 仍被噪音过滤
    expect(screen.queryByText('小配件')).not.toBeInTheDocument();
  });

  it('无上一周期且当期无达标商品时提示需两个周期', () => {
    const sparseNoPrev: OrderAnalytics = {
      revenue_stats: { total_amount: 50, total_orders: 1 },
      daily_stats: [],
      item_stats: [{ item_id: 'low', order_count: 1, total_amount: 50, avg_amount: 50 }],
    };
    render(<DashboardCharts analytics={sparseNoPrev} itemNames={names} />);
    expect(screen.getByText('暂无环比数据，需至少两个周期')).toBeInTheDocument();
  });

  it('有上一周期但无商品达标时提示无爆品', () => {
    const sparse: OrderAnalytics = {
      revenue_stats: { total_amount: 50, total_orders: 1 },
      daily_stats: [],
      item_stats: [{ item_id: 'low', order_count: 1, total_amount: 50, avg_amount: 50 }],
    };
    render(<DashboardCharts analytics={sparse} previous={previous} itemNames={names} />);
    expect(screen.getByText('暂无符合条件的成交爆品（当期订单量需 ≥ 2）')).toBeInTheDocument();
  });
});
