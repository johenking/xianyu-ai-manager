// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getDashboardSummary, getValidOrders } from '../services/api';
import Dashboard from './Dashboard';

vi.mock('../services/api', () => ({
  getDashboardSummary: vi.fn(),
  getValidOrders: vi.fn(),
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
    vi.mocked(getDashboardSummary).mockResolvedValue(summary);
    vi.mocked(getValidOrders).mockResolvedValue([]);
  });

  afterEach(() => cleanup());

  it('uses one summary request for first paint and defers order details', async () => {
    render(<Dashboard />);

    expect(await screen.findByText('¥88.50')).toBeInTheDocument();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(getDashboardSummary).toHaveBeenCalledTimes(1);
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

    expect(await screen.findByText('¥88.50')).toBeInTheDocument();
    expect(getDashboardSummary).toHaveBeenCalledTimes(2);
  });

  it('ignores an older response after the user selects a newer range', async () => {
    render(<Dashboard />);
    expect(await screen.findByText('¥88.50')).toBeInTheDocument();

    let resolveOlder: (value: typeof summary) => void = () => undefined;
    let resolveNewer: (value: typeof summary) => void = () => undefined;
    vi.mocked(getDashboardSummary)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNewer = resolve; }));

    fireEvent.click(screen.getByRole('button', { name: '今天' }));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: '昨天' }));
    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalledTimes(3));

    await act(async () => {
      resolveNewer({
        ...summary,
        current: { ...summary.current, revenue_stats: { total_amount: 22, total_orders: 1 } },
      });
    });
    expect(await screen.findByText('¥22.00')).toBeInTheDocument();
    await act(async () => {
      resolveOlder({
        ...summary,
        current: { ...summary.current, revenue_stats: { total_amount: 11, total_orders: 1 } },
      });
    });

    expect(screen.getByText('¥22.00')).toBeInTheDocument();
    expect(screen.queryByText('¥11.00')).not.toBeInTheDocument();
  });
});
