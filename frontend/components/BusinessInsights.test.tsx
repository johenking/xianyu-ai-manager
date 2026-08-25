// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  BuyerBehaviorAnalytics,
  ItemMetricStatus,
  ItemPerformanceAnalytics,
  ItemTrafficAnalytics,
  TrafficAnalytics,
} from '../types';
import {
  getBuyerBehaviorAnalytics,
  getItemMetricStatus,
  getItemPerformanceAnalytics,
  getItemTrafficAnalytics,
  getTrafficAnalytics,
} from '../services/api';
import BusinessInsights from './BusinessInsights';

vi.mock('../services/api', () => ({
  getTrafficAnalytics: vi.fn(),
  getBuyerBehaviorAnalytics: vi.fn(),
  getItemMetricStatus: vi.fn(),
  getItemPerformanceAnalytics: vi.fn(),
  getItemTrafficAnalytics: vi.fn(),
}));

vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 240 }}>{children}</div>
    ),
  };
});

const range = { start_date: '2026-07-05', end_date: '2026-07-11' };

const fullTiming: TrafficAnalytics = {
  coverage: { total_orders: 10, with_ordered_at: 10, coverage_rate: 1 },
  time_coverage: { total_orders: 10, with_ordered_at: 10, coverage_rate: 1 },
  amount_coverage: { total_orders: 10, with_amount: 9, coverage_rate: 0.9 },
  metric_source: 'order_transactions',
  time_source: 'order_snapshot_ordered_at',
  time_semantics: 'platform_order_recorded_at',
  hourly: [
    { hour: 9, order_count: 3, amount: 120 },
    { hour: 21, order_count: 5, amount: 300 },
  ],
  weekday: [
    { weekday: '1', order_count: 4, amount: 200 },
    { weekday: '0', order_count: 4, amount: 220 },
  ],
  sufficient_data: false,
  data_requirement: { minimum_orders: 20, minimum_time_coverage: 0.8 },
  insufficient_reason: '至少需要 20 笔有效成交订单',
  recommendation: null,
};

const fullBuyers: BuyerBehaviorAnalytics = {
  summary: { total_buyers: 8, repeat_buyers: 3, repeat_rate: 0.375 },
  frequency: [
    { order_count: 1, buyer_count: 5 },
    { order_count: 2, buyer_count: 3 },
  ],
  top_buyers: [
    { buyer_id: 'b1', buyer_nickname: '老客甲', order_count: 4, total_amount: 500 },
    { buyer_id: 'b2', buyer_nickname: '', order_count: 2, total_amount: 180 },
  ],
  amount_coverage: { total_orders: 10, with_amount: 9, coverage_rate: 0.9 },
  metric_source: 'order_transactions',
};

const fullPerformance: ItemPerformanceAnalytics = {
  metric_source: 'order_transactions',
  amount_coverage: { total_orders: 10, with_amount: 8, coverage_rate: 0.8 },
  items: [{
    item_id: 'item-1',
    item_title: '复古相机',
    order_count: 6,
    total_amount: 900,
    avg_amount: 150,
    orders_with_amount: 5,
  }],
};

const emptyItemTraffic: ItemTrafficAnalytics = {
  metric_source: 'seller_backend_verified_snapshots',
  aggregation_semantics: 'counter_delta_between_consecutive_snapshots',
  time_precision: 'observation_window',
  timezone: 'Asia/Shanghai',
  schedule_interval_hours: 4,
  snapshot_count: 0,
  valid_snapshot_count: 0,
  valid_observation_window_count: 0,
  recommendation_window_count: 0,
  recommendation_distinct_days: 0,
  irregular_window_count: 0,
  distinct_days: 0,
  reset_count: 0,
  totals: { exposure_delta: 0, view_delta: 0, want_delta: 0 },
  observation_windows: [],
  hourly: [],
  hourly_semantics: 'legacy_observation_window_end_hour',
  items: [],
  sufficient_data: false,
  data_requirement: {
    minimum_days: 14,
    minimum_snapshots: 20,
    minimum_observation_windows: 20,
    minimum_window_hours: 2,
    maximum_window_hours: 6,
  },
  insufficient_reason: '至少需要 14 天且 20 个接近四小时采样的有效观测窗口',
  recommendation: null,
};

const unavailableMetricStatus: ItemMetricStatus = {
  adapter_available: false,
  enabled_accounts: 0,
  accounts: [],
};

describe('BusinessInsights', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTrafficAnalytics).mockResolvedValue(fullTiming);
    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue(fullBuyers);
    vi.mocked(getItemPerformanceAnalytics).mockResolvedValue(fullPerformance);
    vi.mocked(getItemTrafficAnalytics).mockResolvedValue(emptyItemTraffic);
    vi.mocked(getItemMetricStatus).mockResolvedValue(unavailableMetricStatus);
  });

  afterEach(() => cleanup());

  it('separates transaction timing, item performance and verified traffic', async () => {
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('订单时段分析')).toBeInTheDocument();
    expect(screen.getByText('成交商品表现')).toBeInTheDocument();
    expect(screen.getByText('复古相机')).toBeInTheDocument();
    expect(screen.getByText('商品流量')).toBeInTheDocument();
    expect(screen.getByText('真实商品流量采集尚未启用')).toBeInTheDocument();
    // 37.5% 同时出现在复购率徽章与频次分布占比列（3/8 巧合同值）
    expect(screen.getAllByText('37.5%').length).toBeGreaterThan(0);
    expect(screen.getByText('老客甲')).toBeInTheDocument();
    expect(screen.getByText('b2')).toBeInTheDocument();
    expect(screen.queryByText('时段流量分析')).not.toBeInTheDocument();
    expect(getItemPerformanceAnalytics).toHaveBeenCalledWith(range, expect.any(AbortSignal));
    expect(getItemTrafficAnalytics).toHaveBeenCalledWith(range, expect.any(AbortSignal));
  });

  it('shows time and amount coverage warnings without dropping order counts', async () => {
    vi.mocked(getTrafficAnalytics).mockResolvedValue({
      ...fullTiming,
      coverage: { total_orders: 10, with_ordered_at: 6, coverage_rate: 0.6 },
      time_coverage: { total_orders: 10, with_ordered_at: 6, coverage_rate: 0.6 },
    });
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText(/时段分布基于 60% 有订单时间的订单/)).toBeInTheDocument();
    expect(screen.getByText(/6\/10 笔/)).toBeInTheDocument();
    expect(screen.getByText(/金额覆盖率 80%/)).toBeInTheDocument();
    expect(screen.getByText(/8\/10 笔/)).toBeInTheDocument();
  });

  it('renders truthful empty states', async () => {
    const emptyTiming: TrafficAnalytics = {
      ...fullTiming,
      coverage: { total_orders: 0, with_ordered_at: 0, coverage_rate: 0 },
      time_coverage: { total_orders: 0, with_ordered_at: 0, coverage_rate: 0 },
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
      hourly: [],
      weekday: [],
      insufficient_reason: '至少需要 20 笔有效成交订单',
    };
    vi.mocked(getTrafficAnalytics).mockResolvedValue(emptyTiming);
    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue({
      ...fullBuyers,
      summary: { total_buyers: 0, repeat_buyers: 0, repeat_rate: 0 },
      frequency: [],
      top_buyers: [],
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
    });
    vi.mocked(getItemPerformanceAnalytics).mockResolvedValue({
      ...fullPerformance,
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
      items: [],
    });
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('暂无带订单时间的订单')).toBeInTheDocument();
    expect(screen.getByText('暂无成交商品数据')).toBeInTheDocument();
    expect(screen.getByText('暂无买家数据')).toBeInTheDocument();
  });

  it('shows verified traffic totals and an insufficient-data gate', async () => {
    vi.mocked(getItemMetricStatus).mockResolvedValue({
      adapter_available: true,
      enabled_accounts: 1,
      accounts: [],
    });
    vi.mocked(getItemTrafficAnalytics).mockResolvedValue({
      ...emptyItemTraffic,
      snapshot_count: 3,
      valid_snapshot_count: 3,
      valid_observation_window_count: 3,
      recommendation_window_count: 3,
      recommendation_distinct_days: 2,
      irregular_window_count: 0,
      distinct_days: 2,
      totals: { exposure_delta: 1200, view_delta: 240, want_delta: 18 },
      observation_windows: [{
        start_hour: 16,
        end_hour: 20,
        day_span: 0,
        crosses_midnight: false,
        window_count: 3,
        average_duration_hours: 4.1,
        minimum_duration_hours: 4,
        maximum_duration_hours: 4.2,
        exposure_delta: 1200,
        view_delta: 240,
        want_delta: 18,
      }],
      hourly: [{
        hour: 20,
        window_start_hour: 16,
        window_end_hour: 20,
        day_span: 0,
        crosses_midnight: false,
        window_count: 3,
        average_duration_hours: 4.1,
        exposure_delta: 1200,
        view_delta: 240,
        want_delta: 18,
      }],
      items: [{
        item_id: 'item-1',
        snapshot_count: 3,
        observation_window_count: 3,
        exposure_delta: 1200,
        view_delta: 240,
        want_delta: 18,
      }],
    });
    render(<BusinessInsights range={range} />);

    expect((await screen.findAllByText('1,200')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/流量建议待补充：至少需要 14 天且 20 个接近四小时采样的有效观测窗口/)).toBeInTheDocument();
    expect(screen.getByText('观测窗口增量')).toBeInTheDocument();
    expect(screen.getByText('增量属于整个窗口，不能细分到单小时')).toBeInTheDocument();
    expect(screen.queryByText('按小时增量')).not.toBeInTheDocument();
    expect(screen.queryByText('尚无已验证商品流量快照')).not.toBeInTheDocument();
  });

  it('isolates an optional traffic failure from other business analysis', async () => {
    vi.mocked(getItemTrafficAnalytics).mockRejectedValue(new Error('流量接口暂时不可用'));
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('订单时段分析')).toBeInTheDocument();
    expect(screen.getByText('成交商品表现')).toBeInTheDocument();
    expect(screen.getByText('买家行为分析')).toBeInTheDocument();
    expect(screen.getByText('流量接口暂时不可用')).toBeInTheDocument();
  });

  it('renders core analytics while the optional traffic request is still pending', async () => {
    let resolveTraffic: (value: ItemTrafficAnalytics) => void = () => undefined;
    vi.mocked(getItemTrafficAnalytics).mockImplementation(
      () => new Promise((resolve) => { resolveTraffic = resolve; }),
    );
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('复古相机')).toBeInTheDocument();
    expect(screen.getByText('订单时段分析')).toBeInTheDocument();
    expect(screen.getByText('买家行为分析')).toBeInTheDocument();
    expect(screen.getByText('正在加载...')).toBeInTheDocument();

    resolveTraffic(emptyItemTraffic);
    expect(await screen.findByText('真实商品流量采集尚未启用')).toBeInTheDocument();
  });

  it('keeps historical traffic visible when the live adapter is unavailable', async () => {
    vi.mocked(getItemTrafficAnalytics).mockResolvedValue({
      ...emptyItemTraffic,
      snapshot_count: 3,
      valid_snapshot_count: 3,
      totals: { exposure_delta: 1200, view_delta: 240, want_delta: 18 },
    });
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText(/历史快照仍可查看/)).toBeInTheDocument();
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(screen.queryByText('真实商品流量采集尚未启用')).not.toBeInTheDocument();
  });

  it('shows metric status failures without hiding the other analytics', async () => {
    vi.mocked(getItemMetricStatus).mockRejectedValue(new Error('指标采集状态暂时不可用'));
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('订单时段分析')).toBeInTheDocument();
    expect(screen.getByText('成交商品表现')).toBeInTheDocument();
    expect(screen.getByText('指标采集状态暂时不可用')).toBeInTheDocument();
  });

  it('shows a global error state and retries when all analysis requests fail', async () => {
    vi.mocked(getTrafficAnalytics)
      .mockRejectedValueOnce(new Error('分析接口暂时不可用'))
      .mockResolvedValueOnce(fullTiming);
    vi.mocked(getBuyerBehaviorAnalytics)
      .mockRejectedValueOnce(new Error('分析接口暂时不可用'))
      .mockResolvedValueOnce(fullBuyers);
    vi.mocked(getItemPerformanceAnalytics)
      .mockRejectedValueOnce(new Error('分析接口暂时不可用'))
      .mockResolvedValueOnce(fullPerformance);
    vi.mocked(getItemTrafficAnalytics)
      .mockRejectedValueOnce(new Error('分析接口暂时不可用'))
      .mockResolvedValueOnce(emptyItemTraffic);
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('分析接口暂时不可用')).toBeInTheDocument();
    screen.getByRole('button', { name: '重试' }).click();
    expect(await screen.findByText('订单时段分析')).toBeInTheDocument();
  });

  it('refreshSignal 变化时静默跟刷：重新请求但不回到骨架态', async () => {
    const { rerender } = render(<BusinessInsights range={range} refreshSignal={1} />);
    expect(await screen.findByText('复古相机')).toBeInTheDocument();
    expect(getTrafficAnalytics).toHaveBeenCalledTimes(1);

    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue({
      ...fullBuyers,
      summary: { total_buyers: 9, repeat_buyers: 4, repeat_rate: 0.444 },
    });
    rerender(<BusinessInsights range={range} refreshSignal={2} />);

    // 静默刷新期间旧内容保持可见，不出现整块加载骨架
    expect(screen.getByText('复古相机')).toBeInTheDocument();
    expect(screen.queryByText('正在加载...')).not.toBeInTheDocument();
    expect(await screen.findByText('44.4%')).toBeInTheDocument();
    expect(getTrafficAnalytics).toHaveBeenCalledTimes(2);
  });

  it('静默跟刷失败时保留已渲染数据而不显示错误', async () => {
    const { rerender } = render(<BusinessInsights range={range} refreshSignal={1} />);
    expect(await screen.findByText('复古相机')).toBeInTheDocument();

    vi.mocked(getItemPerformanceAnalytics).mockRejectedValue(new Error('临时网络抖动'));
    rerender(<BusinessInsights range={range} refreshSignal={2} />);

    await waitFor(() => expect(getItemPerformanceAnalytics).toHaveBeenCalledTimes(2));
    expect(screen.getByText('复古相机')).toBeInTheDocument();
    expect(screen.queryByText('临时网络抖动')).not.toBeInTheDocument();
  });

  it('keeps only the latest response when range changes', async () => {
    let resolveOld: (value: TrafficAnalytics) => void = () => undefined;
    vi.mocked(getTrafficAnalytics)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({
        ...fullTiming,
        coverage: { total_orders: 2, with_ordered_at: 1, coverage_rate: 0.5 },
        time_coverage: { total_orders: 2, with_ordered_at: 1, coverage_rate: 0.5 },
      });
    const { rerender } = render(<BusinessInsights range={range} />);
    rerender(<BusinessInsights range={{ start_date: '2026-07-12', end_date: '2026-07-18' }} />);

    expect(await screen.findByText(/时段分布基于 50% 有订单时间的订单/)).toBeInTheDocument();
    resolveOld(fullTiming);
    await waitFor(() => {
      expect(screen.getByText(/时段分布基于 50% 有订单时间的订单/)).toBeInTheDocument();
    });
  });
});
