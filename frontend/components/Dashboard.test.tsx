// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getDashboardSummary, getValidOrders, getTrafficAnalytics, getBuyerBehaviorAnalytics, getItemPerformanceAnalytics, getItemTrafficAnalytics, getItemMetricStatus } from '../services/api';
import Dashboard, { DASHBOARD_REFRESH_MS } from './Dashboard';

vi.mock('../services/api', () => ({
  getDashboardSummary: vi.fn(),
  getValidOrders: vi.fn(),
  getTrafficAnalytics: vi.fn(),
  getBuyerBehaviorAnalytics: vi.fn(),
  getItemPerformanceAnalytics: vi.fn(),
  getItemTrafficAnalytics: vi.fn(),
  getItemMetricStatus: vi.fn(),
}));

vi.mock('@number-flow/react', () => ({
  default: ({
    value,
    format,
    className,
    'aria-label': ariaLabel,
    'aria-hidden': ariaHidden,
  }: {
    value: number;
    format?: Intl.NumberFormatOptions;
    className?: string;
    'aria-label'?: string;
    'aria-hidden'?: boolean | 'true';
  }) => (
    <span className={className} aria-label={ariaLabel} aria-hidden={ariaHidden}>
      {new Intl.NumberFormat('zh-CN', format).format(value)}
    </span>
  ),
}));

const summary = {
  success: true,
  scope: 'user' as const,
  range: {
    start_date: '2026-07-05',
    end_date: '2026-07-11',
    previous_start_date: '2026-06-28',
    previous_end_date: '2026-07-04',
  },
  stats: {
    total_users: 1,
    total_cookies: 2,
    active_cookies: 1,
    total_cards: 3,
    total_keywords: 4,
    total_orders: 5,
  },
  current: {
    revenue_stats: { total_amount: 88.5, total_orders: 2 },
    daily_stats: [{ date: '2026-07-10', amount: 88.5, order_count: 2 }],
    status_stats: [{ status: 'pending_ship', count: 2, amount: 88.5 }],
    item_stats: [],
  },
  previous: {
    revenue_stats: { total_amount: 40, total_orders: 1 },
    daily_stats: [],
    item_stats: [],
  },
  item_names: {},
};

describe('Dashboard summary loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    vi.mocked(getDashboardSummary).mockResolvedValue(summary);
    vi.mocked(getValidOrders).mockResolvedValue([]);
    vi.mocked(getTrafficAnalytics).mockResolvedValue({
      coverage: { total_orders: 0, with_ordered_at: 0, coverage_rate: 0 },
      time_coverage: { total_orders: 0, with_ordered_at: 0, coverage_rate: 0 },
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
      metric_source: 'order_transactions',
      time_source: 'order_snapshot_ordered_at',
      time_semantics: 'platform_order_recorded_at',
      hourly: [],
      weekday: [],
      sufficient_data: false,
      data_requirement: { minimum_orders: 20, minimum_time_coverage: 0.8 },
      insufficient_reason: '至少需要 20 笔有效成交订单',
      recommendation: null,
    });
    vi.mocked(getBuyerBehaviorAnalytics).mockResolvedValue({
      summary: { total_buyers: 0, repeat_buyers: 0, repeat_rate: 0 },
      frequency: [],
      top_buyers: [],
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
      metric_source: 'order_transactions',
    });
    vi.mocked(getItemPerformanceAnalytics).mockResolvedValue({
      metric_source: 'order_transactions',
      amount_coverage: { total_orders: 0, with_amount: 0, coverage_rate: 0 },
      items: [],
    });
    vi.mocked(getItemTrafficAnalytics).mockResolvedValue({
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
    });
    vi.mocked(getItemMetricStatus).mockResolvedValue({
      adapter_available: false,
      enabled_accounts: 0,
      accounts: [],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('uses one summary request for first paint and defers order details', async () => {
    render(<Dashboard />);

    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();
    expect(screen.getByLabelText('活跃账号 1，总数 2')).toBeInTheDocument();
    expect(getDashboardSummary).toHaveBeenCalledTimes(1);
    expect(getDashboardSummary).toHaveBeenNthCalledWith(1, { range: 'today' }, expect.any(AbortSignal));
    expect(screen.getByRole('button', { name: '今天' })).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(getValidOrders).toHaveBeenCalledTimes(1));
  });

  it('finishes loading with an explicit empty state', async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue({
      ...summary,
      stats: { ...summary.stats, total_cookies: 0, active_cookies: 0 },
      current: {
        revenue_stats: { total_amount: 0, total_orders: 0 },
        daily_stats: [],
        item_stats: [],
      },
    });
    render(<Dashboard />);

    expect(await screen.findByText('还没有经营数据')).toBeInTheDocument();
    expect(screen.queryByLabelText('仪表盘加载中')).not.toBeInTheDocument();
  });

  it('shows an error terminal state and retries successfully', async () => {
    vi.mocked(getDashboardSummary)
      .mockRejectedValueOnce(new Error('汇总接口暂时不可用'))
      .mockResolvedValueOnce(summary);
    render(<Dashboard />);

    expect(await screen.findByText('汇总接口暂时不可用')).toBeInTheDocument();
    expect(screen.queryByLabelText('仪表盘加载中')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();
    expect(getDashboardSummary).toHaveBeenCalledTimes(2);
  });

  it('ignores an older response after the user selects a newer range', async () => {
    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();

    let resolveOlder: (value: typeof summary) => void = () => undefined;
    let resolveNewer: (value: typeof summary) => void = () => undefined;
    vi.mocked(getDashboardSummary)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNewer = resolve; }));

    fireEvent.click(screen.getByRole('button', { name: '昨天' }));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: '今天' }));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(3));

    await act(async () => {
      resolveNewer({
        ...summary,
        current: { ...summary.current, revenue_stats: { total_amount: 22, total_orders: 1 } },
      });
    });
    expect(await screen.findByLabelText('今日营收 ¥22.00')).toBeInTheDocument();
    await act(async () => {
      resolveOlder({
        ...summary,
        current: { ...summary.current, revenue_stats: { total_amount: 11, total_orders: 1 } },
      });
    });

    expect(screen.getByLabelText('今日营收 ¥22.00')).toBeInTheDocument();
    expect(screen.queryByLabelText('昨日营收 ¥11.00')).not.toBeInTheDocument();
  });

  it('refreshes after 15 seconds without reloading business insights', async () => {
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    render(<Dashboard />);

    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();
    await waitFor(() => expect(getItemMetricStatus).toHaveBeenCalledTimes(1));
    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    expect(scheduled).toBeDefined();

    await act(async () => {
      (scheduled![0] as () => void)();
    });

    expect(getDashboardSummary).toHaveBeenCalledTimes(2);
    expect(getTrafficAnalytics).toHaveBeenCalledTimes(1);
    expect(getBuyerBehaviorAnalytics).toHaveBeenCalledTimes(1);
    expect(getItemPerformanceAnalytics).toHaveBeenCalledTimes(1);
    expect(getItemTrafficAnalytics).toHaveBeenCalledTimes(1);
    expect(getItemMetricStatus).toHaveBeenCalledTimes(1);
  });

  it('rolls the revenue and order totals in both directions when live data changes', async () => {
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    const withValues = (amount: number, orders: number) => ({
      ...summary,
      current: {
        ...summary.current,
        revenue_stats: { total_amount: amount, total_orders: orders },
      },
    });
    vi.mocked(getDashboardSummary)
      .mockResolvedValueOnce(withValues(100, 1))
      .mockResolvedValueOnce(withValues(120, 2))
      .mockResolvedValueOnce(withValues(70, 1));

    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥100.00')).toBeInTheDocument();

    const firstRefresh = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    await act(async () => (firstRefresh![0] as () => void)());
    expect(await screen.findByLabelText('今日营收 ¥120.00')).toBeInTheDocument();

    const secondRefresh = timeoutSpy.mock.calls.filter(([, delay]) => delay === DASHBOARD_REFRESH_MS).at(-1);
    await act(async () => (secondRefresh![0] as () => void)());
    expect(await screen.findByLabelText('今日营收 ¥70.00')).toBeInTheDocument();
    expect(screen.getByLabelText('订单数 1')).toBeInTheDocument();
  });

  it('pauses while hidden or offline and refreshes immediately when available', async () => {
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();

    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    expect(scheduled).toBeDefined();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    await act(async () => {
      (scheduled![0] as () => void)();
    });
    expect(getDashboardSummary).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(2));

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    window.dispatchEvent(new Event('offline'));
    const nextScheduled = timeoutSpy.mock.calls.filter(([, delay]) => delay === DASHBOARD_REFRESH_MS).at(-1);
    await act(async () => {
      (nextScheduled![0] as () => void)();
    });
    expect(getDashboardSummary).toHaveBeenCalledTimes(2);

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
    window.dispatchEvent(new Event('online'));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(3));
  });

  it('does not overlap refreshes and aborts the active request on unmount', async () => {
    let resolveRefresh: (value: typeof summary) => void = () => undefined;
    vi.mocked(getDashboardSummary)
      .mockResolvedValueOnce(summary)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    const view = render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();

    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    act(() => {
      (scheduled![0] as () => void)();
    });
    window.dispatchEvent(new Event('online'));
    expect(getDashboardSummary).toHaveBeenCalledTimes(2);

    const signal = vi.mocked(getDashboardSummary).mock.calls[1][1];
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => resolveRefresh(summary));
  });

  it('keeps the last values when a background refresh fails', async () => {
    vi.mocked(getDashboardSummary)
      .mockResolvedValueOnce(summary)
      .mockRejectedValueOnce(new Error('temporary'));
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();

    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    await act(async () => {
      (scheduled![0] as () => void)();
    });

    expect(screen.getByLabelText('今日营收 ¥88.50')).toBeInTheDocument();
    expect(screen.getByText('更新延迟')).toBeInTheDocument();
  });

  it('refreshes order details when only an order status changes', async () => {
    vi.mocked(getDashboardSummary)
      .mockResolvedValueOnce(summary)
      .mockResolvedValueOnce({
        ...summary,
        current: {
          ...summary.current,
          status_stats: [{ status: 'shipped', count: 2, amount: 88.5 }],
        },
      });
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();
    await waitFor(() => expect(getValidOrders).toHaveBeenCalledTimes(1));

    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    await act(async () => {
      (scheduled![0] as () => void)();
    });
    await waitFor(() => expect(getValidOrders).toHaveBeenCalledTimes(2));
  });

  it('clears the background refresh state when a range change supersedes it', async () => {
    let resolveBackground: (value: typeof summary) => void = () => undefined;
    vi.mocked(getDashboardSummary)
      .mockResolvedValueOnce(summary)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveBackground = resolve; }))
      .mockResolvedValueOnce({
        ...summary,
        range: { ...summary.range, start_date: '2026-07-11', end_date: '2026-07-11' },
      });
    const timeoutSpy = vi.spyOn(window, 'setTimeout');
    render(<Dashboard />);
    expect(await screen.findByLabelText('今日营收 ¥88.50')).toBeInTheDocument();

    const scheduled = timeoutSpy.mock.calls.find(([, delay]) => delay === DASHBOARD_REFRESH_MS);
    await act(async () => {
      (scheduled![0] as () => void)();
    });
    expect(screen.getByText('更新中')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '昨天' }));
    expect(await screen.findByText('实时')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '立即刷新仪表盘' })).not.toBeDisabled();
    await act(async () => resolveBackground(summary));
  });
});
