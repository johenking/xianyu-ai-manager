// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getBuyerBehaviorAnalytics, getTrafficAnalytics } from '../services/api';
import BusinessInsights from './BusinessInsights';

vi.mock('../services/api', () => ({
  getTrafficAnalytics: vi.fn(),
  getBuyerBehaviorAnalytics: vi.fn(),
}));

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

const range = { start_date: '2026-07-05', end_date: '2026-07-11' };

const fullTraffic = {
  coverage: { total_orders: 10, with_ordered_at: 10, coverage_rate: 1 },
  hourly: [
    { hour: 9, order_count: 3, amount: 120 },
    { hour: 21, order_count: 5, amount: 300 },
  ],
  weekday: [
    { weekday: '1', order_count: 4, amount: 200 },
    { weekday: '0', order_count: 4, amount: 220 },
  ],
};

const fullBuyers = {
  summary: { total_buyers: 8, repeat_buyers: 3, repeat_rate: 0.375 },
  frequency: [
    { order_count: 1, buyer_count: 5 },
    { order_count: 2, buyer_count: 3 },
  ],
  top_buyers: [
    { buyer_id: 'b1', buyer_nickname: '老客甲', order_count: 4, total_amount: 500 },
    { buyer_id: 'b2', buyer_nickname: '', order_count: 2, total_amount: 180 },
  ],
};

describe('BusinessInsights', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTrafficAnalytics).mockResolvedValue(fullTraffic);
    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue(fullBuyers);
  });

  afterEach(() => cleanup());

  it('renders traffic sections, repeat rate and top buyers', async () => {
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('时段流量分析')).toBeInTheDocument();
    expect(screen.getByText('按小时分布')).toBeInTheDocument();
    expect(screen.getByText('按星期分布')).toBeInTheDocument();
    // 复购率 0.375 → 37.5%
    expect(screen.getByText('37.5%')).toBeInTheDocument();
    // 贡献榜昵称优先，无昵称回落 buyer_id
    expect(screen.getByText('老客甲')).toBeInTheDocument();
    expect(screen.getByText('b2')).toBeInTheDocument();
    expect(getTrafficAnalytics).toHaveBeenCalledWith(range);
    expect(getBuyerBehaviorAnalytics).toHaveBeenCalledWith(range);
  });

  it('shows a coverage warning when some orders lack ordered_at', async () => {
    vi.mocked(getTrafficAnalytics).mockResolvedValue({
      ...fullTraffic,
      coverage: { total_orders: 10, with_ordered_at: 6, coverage_rate: 0.6 },
    });
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText(/时段分布基于 60% 有成交时间的订单/)).toBeInTheDocument();
    expect(screen.getByText(/6\/10 笔/)).toBeInTheDocument();
  });

  it('renders empty states when there is no data', async () => {
    vi.mocked(getTrafficAnalytics).mockResolvedValue({
      coverage: { total_orders: 0, with_ordered_at: 0, coverage_rate: 0 },
      hourly: [],
      weekday: [],
    });
    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue({
      summary: { total_buyers: 0, repeat_buyers: 0, repeat_rate: 0 },
      frequency: [],
      top_buyers: [],
    });
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('暂无带成交时间的订单')).toBeInTheDocument();
    expect(screen.getByText('暂无买家数据')).toBeInTheDocument();
  });

  it('shows an error state and retries', async () => {
    vi.mocked(getTrafficAnalytics)
      .mockRejectedValueOnce(new Error('分析接口暂时不可用'))
      .mockResolvedValueOnce(fullTraffic);
    render(<BusinessInsights range={range} />);

    expect(await screen.findByText('分析接口暂时不可用')).toBeInTheDocument();
    screen.getByRole('button', { name: '重试' }).click();
    expect(await screen.findByText('时段流量分析')).toBeInTheDocument();
  });

  it('keeps only the latest response when range changes', async () => {
    let resolveOld: (v: typeof fullTraffic) => void = () => undefined;
    vi.mocked(getTrafficAnalytics)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({
        ...fullTraffic,
        coverage: { total_orders: 2, with_ordered_at: 1, coverage_rate: 0.5 },
      });
    const { rerender } = render(<BusinessInsights range={range} />);
    rerender(<BusinessInsights range={{ start_date: '2026-07-12', end_date: '2026-07-18' }} />);

    // 新请求先落地（覆盖率 50% 告警）
    expect(await screen.findByText(/时段分布基于 50% 有成交时间的订单/)).toBeInTheDocument();
    // 旧请求迟到 resolve 不得覆盖新结果
    resolveOld(fullTraffic);
    await waitFor(() => {
      expect(screen.getByText(/时段分布基于 50% 有成交时间的订单/)).toBeInTheDocument();
    });
  });
});
