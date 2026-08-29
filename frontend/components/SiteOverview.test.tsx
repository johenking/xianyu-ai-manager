// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getAdminAgentSummary, getGlobalDashboardSummary } from '../services/api';
import SiteOverview from './SiteOverview';

vi.mock('../services/api', () => ({
  getAdminAgentSummary: vi.fn(),
  getGlobalDashboardSummary: vi.fn(),
}));

const range = {
  start_date: '2026-07-05',
  end_date: '2026-07-11',
  previous_start_date: '2026-06-28',
  previous_end_date: '2026-07-04',
};

const EXPANDED_STORAGE_KEY = 'xianyu-dashboard:site-overview-expanded';

const expandPanel = () => {
  fireEvent.click(screen.getByRole('button', { name: /全站经营/ }));
};

let localStorageValues: Map<string, string>;

describe('SiteOverview', () => {
  beforeEach(() => {
    localStorageValues = new Map();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: vi.fn((key: string) => localStorageValues.get(key) ?? null),
        setItem: vi.fn((key: string, value: string) => {
          localStorageValues.set(key, value);
        }),
        removeItem: vi.fn((key: string) => {
          localStorageValues.delete(key);
        }),
        clear: vi.fn(() => localStorageValues.clear()),
      },
    });
    vi.mocked(getGlobalDashboardSummary).mockResolvedValue({
      success: true,
      scope: 'site',
      range,
      current: { total_orders: 12, total_amount: 345.5 },
      previous: { total_orders: 8, total_amount: 200 },
    });
    vi.mocked(getAdminAgentSummary).mockResolvedValue({
      success: true,
      range,
      agents: [
        { user_id: 2, username: 'agent-top', is_active: true, account_count: 3, total_orders: 9, total_amount: 300 },
        { user_id: 3, username: 'agent-idle', is_active: false, account_count: 1, total_orders: 0, total_amount: 0 },
      ],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    localStorageValues.clear();
  });

  it('starts collapsed by default and issues no requests while collapsed', () => {
    render(<SiteOverview isAdmin startDate="2026-07-05" endDate="2026-07-11" />);

    expect(screen.getByRole('button', { name: /全站经营/ })).toHaveAttribute('aria-expanded', 'false');
    expect(getGlobalDashboardSummary).not.toHaveBeenCalled();
    expect(getAdminAgentSummary).not.toHaveBeenCalled();
    expect(screen.queryByText('全站销售额')).not.toBeInTheDocument();
    expect(screen.queryByText(/分代理明细/)).not.toBeInTheDocument();
  });

  it('shows site totals to ordinary users after expanding, without requesting per-agent detail', async () => {
    render(<SiteOverview isAdmin={false} startDate="2026-07-05" endDate="2026-07-11" />);
    expandPanel();

    expect(await screen.findByText('¥345.50')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(getGlobalDashboardSummary).toHaveBeenCalledWith({
      range: 'custom',
      start_date: '2026-07-05',
      end_date: '2026-07-11',
    });
    expect(getAdminAgentSummary).not.toHaveBeenCalled();
    expect(screen.queryByText(/分代理明细/)).not.toBeInTheDocument();
    expect(screen.queryByText('agent-top')).not.toBeInTheDocument();
  });

  it('shows the per-agent breakdown to admins after expanding, hiding disabled agents', async () => {
    render(<SiteOverview isAdmin startDate="2026-07-05" endDate="2026-07-11" />);
    expandPanel();

    expect(await screen.findByText(/分代理明细/)).toBeInTheDocument();
    await waitFor(() => expect(getAdminAgentSummary).toHaveBeenCalled());
    expect(screen.getByText('agent-top')).toBeInTheDocument();
    expect(screen.getByText('¥300.00')).toBeInTheDocument();
    expect(screen.queryByText('agent-idle')).not.toBeInTheDocument();
    expect(screen.queryByText('停用')).not.toBeInTheDocument();
  });

  it('remembers the expanded preference via localStorage', async () => {
    localStorageValues.set(EXPANDED_STORAGE_KEY, '1');
    render(<SiteOverview isAdmin={false} startDate="2026-07-05" endDate="2026-07-11" />);

    expect(screen.getByRole('button', { name: /全站经营/ })).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText('¥345.50')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /全站经营/ }));
    expect(localStorageValues.get(EXPANDED_STORAGE_KEY)).toBe('0');
    expect(screen.queryByText('全站销售额')).not.toBeInTheDocument();
  });

  it('degrades to a quiet unavailable notice on load failure', async () => {
    vi.mocked(getGlobalDashboardSummary).mockRejectedValue(new Error('接口超时'));
    render(<SiteOverview isAdmin={false} startDate="2026-07-05" endDate="2026-07-11" />);
    expandPanel();

    expect(await screen.findByText(/全站经营暂不可用：接口超时/)).toBeInTheDocument();
  });
});
