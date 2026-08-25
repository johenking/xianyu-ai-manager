// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Order, OrderAnalytics } from '../types';
import DashboardCharts, {
  buildBuyerMix,
  buildPriceBins,
  formatHeroAxisTick,
  HeroTrend,
  orderAmountOf,
} from './DashboardCharts';

// recharts 的 ResponsiveContainer 在 jsdom 下测不到尺寸，图表不会渲染 SVG；
// 这里用固定尺寸桩替换并把宽高直接传给子图表，让峰谷标注等 SVG 内容可被断言。
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 240 }}>
        {React.isValidElement(children)
          ? React.cloneElement(children as React.ReactElement<{ width?: number; height?: number }>, { width: 400, height: 240 })
          : children}
      </div>
    ),
  };
});

const makeOrder = (overrides: Partial<Order> & { order_id: string }): Order => ({
  id: overrides.order_id,
  cookie_id: 'acc-1',
  item_id: 'A1',
  buyer_id: 'buyer-1',
  quantity: 1,
  amount: '10.00',
  status: 'completed',
  ...overrides,
});

const baseAnalytics: OrderAnalytics = {
  revenue_stats: { total_amount: 1280, total_orders: 12 },
  daily_stats: [{ date: '2026-07-10', amount: 680, order_count: 6 }],
  item_stats: [
    { item_id: 'A1', order_count: 8, total_amount: 800, avg_amount: 100 },
    { item_id: 'A2', order_count: 10, total_amount: 300, avg_amount: 30 },
  ],
  status_stats: [
    { status: 'completed', count: 7, amount: 700 },
    { status: 'pending_ship', count: 3, amount: 300 },
    { status: 'shipped', count: 2, amount: 280 },
  ],
  account_stats: [
    { cookie_id: 'acc-main', account_name: '主力号', order_count: 9, total_amount: 900 },
    { cookie_id: 'acc-backup', account_name: 'acc-backup', order_count: 3, total_amount: 380 },
  ],
};

afterEach(() => {
  cleanup();
});

describe('客单价与买家构成聚合', () => {
  it('orderAmountOf 优先分值，回退展示金额，均缺失返回 null', () => {
    expect(orderAmountOf(makeOrder({ order_id: 'o1', paid_amount_fen: 1250, amount: '99.00' }))).toBe(12.5);
    expect(orderAmountOf(makeOrder({ order_id: 'o2', paid_amount_fen: null, amount: '8.80' }))).toBe(8.8);
    expect(orderAmountOf(makeOrder({ order_id: 'o3', paid_amount_fen: null, amount: '' }))).toBeNull();
  });

  it('buildPriceBins 按固定价格带分桶并统计缺失金额订单', () => {
    const { bins, excluded } = buildPriceBins([
      makeOrder({ order_id: 'o1', paid_amount_fen: 500 }),    // <¥10
      makeOrder({ order_id: 'o2', paid_amount_fen: 1500 }),   // ¥10-20
      makeOrder({ order_id: 'o3', paid_amount_fen: 1999 }),   // ¥10-20
      makeOrder({ order_id: 'o4', paid_amount_fen: 5000 }),   // ¥50-100（下界含）
      makeOrder({ order_id: 'o5', paid_amount_fen: 100_00 }), // ≥¥100（下界含）
      makeOrder({ order_id: 'o6', paid_amount_fen: null, amount: '' }),
    ]);
    expect(bins.map((bin) => bin.count)).toEqual([1, 2, 0, 1, 1]);
    expect(bins[1].amount).toBeCloseTo(34.99);
    expect(excluded).toBe(1);
  });

  it('buildBuyerMix 按周期内下单次数区分复购与单次买家', () => {
    const mix = buildBuyerMix([
      makeOrder({ order_id: 'o1', buyer_id: 'a' }),
      makeOrder({ order_id: 'o2', buyer_id: 'a' }),
      makeOrder({ order_id: 'o3', buyer_id: 'b' }),
      makeOrder({ order_id: 'o4', buyer_id: '  ' }), // 空买家不计入
    ]);
    expect(mix).toEqual({ total: 2, repeat: 1, single: 1 });
  });
});

describe('DashboardCharts 订单与商品分区', () => {
  it('渲染状态分布/客单价/买家构成/账号贡献，不再渲染地区分布', () => {
    const orders = [
      makeOrder({ order_id: 'o1', buyer_id: 'a', paid_amount_fen: 1500 }),
      makeOrder({ order_id: 'o2', buyer_id: 'a', paid_amount_fen: 1800 }),
      makeOrder({ order_id: 'o3', buyer_id: 'b', paid_amount_fen: 6000 }),
    ];
    render(<DashboardCharts analytics={baseAnalytics} itemNames={{ A1: '测试商品' }} orders={orders} />);
    expect(screen.getByText('订单状态分布')).toBeInTheDocument();
    expect(screen.getByText('按订单量统计 · 退款完成订单已扣除')).toBeInTheDocument();
    // 客单价分布：3 笔订单中 2 笔落在 ¥10-20 主力价格带
    expect(screen.getByText('客单价分布')).toBeInTheDocument();
    expect(screen.getByText(/主力价格带/)).toBeInTheDocument();
    // 买家构成：a 复购、b 单次
    expect(screen.getByText('买家构成')).toBeInTheDocument();
    expect(screen.getByText('复购买家')).toBeInTheDocument();
    expect(screen.getByText(/周期内复购率/)).toBeInTheDocument();
    // 账号贡献：备注优先显示
    expect(screen.getByText('账号贡献')).toBeInTheDocument();
    expect(screen.getByText('主力号')).toBeInTheDocument();
    expect(screen.getByText('¥900.00')).toBeInTheDocument();
    // 地区分布已下线（生产收货城市字段为空，不再展示占位面板）
    expect(screen.queryByText('地区分布')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无收货城市数据')).not.toBeInTheDocument();
  });

  it('商品成交榜默认按销售额排序，可切换为订单量排序', () => {
    render(<DashboardCharts analytics={baseAnalytics} itemNames={{ A1: '高价品', A2: '走量品' }} orders={[]} />);
    const rankPanel = screen.getByTestId('item-rank-panel');
    expect(within(rankPanel).getByText('商品成交榜')).toBeInTheDocument();
    expect(within(rankPanel).getByText('按销售额排序 · 前 8')).toBeInTheDocument();
    // 默认金额榜：第一名是 A1（¥800）
    const defaultRows = within(rankPanel).getAllByTitle(/高价品|走量品/);
    expect(defaultRows[0]).toHaveTextContent('高价品');
    expect(within(rankPanel).getByText('¥800.00')).toBeInTheDocument();

    fireEvent.click(within(rankPanel).getByRole('button', { name: '订单量' }));
    expect(within(rankPanel).getByText('按订单量排序 · 前 8')).toBeInTheDocument();
    // 订单量榜：第一名换成 A2（10 单）
    const sortedRows = within(rankPanel).getAllByTitle(/高价品|走量品/);
    expect(sortedRows[0]).toHaveTextContent('走量品');
    expect(within(rankPanel).getByText('10 单')).toBeInTheDocument();
  });

  it('缺少 status_stats/account_stats/orders 时显示空态而不报错', () => {
    const empty: OrderAnalytics = {
      revenue_stats: { total_amount: 0, total_orders: 0 },
      daily_stats: [],
    };
    render(<DashboardCharts analytics={empty} itemNames={{}} orders={[]} />);
    expect(screen.getByText('订单状态分布')).toBeInTheDocument();
    expect(screen.getByText('暂无订单明细')).toBeInTheDocument();
    expect(screen.getByText('暂无买家数据')).toBeInTheDocument();
    expect(screen.getByText('暂无账号成交数据')).toBeInTheDocument();
  });

  it('订单明细加载中显示占位而不是误报空态', () => {
    render(<DashboardCharts analytics={baseAnalytics} itemNames={{}} orders={[]} ordersLoading />);
    expect(screen.getAllByText('数据加载中...').length).toBe(2);
    expect(screen.queryByText('暂无订单明细')).not.toBeInTheDocument();
  });
});

describe('HeroTrend 坐标轴', () => {
  it('压缩千位金额并保留三位数原值', () => {
    expect(formatHeroAxisTick(3800)).toBe('3.8k');
    expect(formatHeroAxisTick(950)).toBe('950');
  });

  it('使用已完成点计算洞察，不把当前未结束点当成回落', () => {
    const points = [
      { date: '2026-08-18 08:00', label: '08:00', amount: 20, orders: 2 },
      { date: '2026-08-18 09:00', label: '09:00', amount: 40, orders: 4 },
      { date: '2026-08-18 10:00', label: '10:00', amount: 1, orders: 0 },
    ];
    render(<HeroTrend points={points} highlightPoints={points.slice(0, 2)} granularity="hour" />);

    expect(screen.getByTestId('trend-peak-orders')).toHaveTextContent('09:00 · 4 单');
    expect(screen.getByTestId('trend-fastest-growth')).toHaveTextContent('09:00 · +¥20.00');
    expect(screen.getByTestId('trend-slowest-growth')).toHaveTextContent('--');
  });

  it('图上标注营收最高与最低点金额', () => {
    const points = [
      { date: '2026-08-18 08:00', label: '08:00', amount: 20, orders: 2 },
      { date: '2026-08-18 09:00', label: '09:00', amount: 40, orders: 4 },
      { date: '2026-08-18 10:00', label: '10:00', amount: 5, orders: 1 },
    ];
    render(<HeroTrend points={points} highlightPoints={points} granularity="hour" />);

    expect(screen.getByTestId('hero-peak-marker')).toHaveTextContent('¥40.00');
    expect(screen.getByTestId('hero-low-marker')).toHaveTextContent('¥5.00');
  });

  it('营收全为 0 时不渲染峰谷标注', () => {
    const points = [
      { date: '2026-08-18 08:00', label: '08:00', amount: 0, orders: 2 },
      { date: '2026-08-18 09:00', label: '09:00', amount: 0, orders: 4 },
    ];
    render(<HeroTrend points={points} highlightPoints={points} granularity="hour" />);

    expect(screen.queryByTestId('hero-peak-marker')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hero-low-marker')).not.toBeInTheDocument();
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
    render(<DashboardCharts analytics={current} previous={previous} itemNames={names} orders={[]} />);
    expect(screen.getByText('成交爆品榜')).toBeInTheDocument();
    // 商品名同时出现在成交榜与爆品榜等多个维度
    expect(screen.getAllByText('复古相机').length).toBeGreaterThan(0);
    expect(screen.getByText('+200%')).toBeInTheDocument();
    expect(screen.getByText('新品')).toBeInTheDocument();
    expect(screen.getByText('-20%')).toBeInTheDocument();
    // 金额与客单价展示（销售额同时出现在成交榜主值与爆品榜）
    expect(screen.getAllByText('¥1,680.00').length).toBeGreaterThan(0);
    expect(screen.getAllByText('¥140.00').length).toBeGreaterThan(0);
    // 当期仅 1 单的商品被噪音过滤，不出现在爆品榜（成交榜仍会展示它）
    const hotPanel = screen.getByTestId('hot-items-panel');
    expect(within(hotPanel).queryByText('小配件')).not.toBeInTheDocument();
  });

  it('无上一周期数据时当期达标商品全部作为新品进榜', () => {
    render(<DashboardCharts analytics={current} itemNames={names} orders={[]} />);
    // 没有 previous，所有当期达标商品（≥2 单）都按新品处理，不算环比
    expect(screen.getAllByText('复古相机').length).toBeGreaterThan(0);
    expect(screen.getAllByText('新品').length).toBe(3); // up/new/down 三个达标商品
    expect(screen.queryByText('+200%')).not.toBeInTheDocument();
    // low 仍被噪音过滤，不进爆品榜
    const hotPanel = screen.getByTestId('hot-items-panel');
    expect(within(hotPanel).queryByText('小配件')).not.toBeInTheDocument();
  });

  it('无上一周期且当期无达标商品时提示需两个周期', () => {
    const sparseNoPrev: OrderAnalytics = {
      revenue_stats: { total_amount: 50, total_orders: 1 },
      daily_stats: [],
      item_stats: [{ item_id: 'low', order_count: 1, total_amount: 50, avg_amount: 50 }],
    };
    render(<DashboardCharts analytics={sparseNoPrev} itemNames={names} orders={[]} />);
    expect(screen.getByText('暂无环比数据，需至少两个周期')).toBeInTheDocument();
  });

  it('有上一周期但无商品达标时提示无爆品', () => {
    const sparse: OrderAnalytics = {
      revenue_stats: { total_amount: 50, total_orders: 1 },
      daily_stats: [],
      item_stats: [{ item_id: 'low', order_count: 1, total_amount: 50, avg_amount: 50 }],
    };
    render(<DashboardCharts analytics={sparse} previous={previous} itemNames={names} orders={[]} />);
    expect(screen.getByText('暂无符合条件的成交爆品（当期订单量需 ≥ 2）')).toBeInTheDocument();
  });
});
