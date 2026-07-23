// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getItems, getOrders, syncOrders } from '../services/api';
import OrderList from './OrderList';

vi.mock('../services/api', () => ({
  getOrders: vi.fn(),
  getItems: vi.fn(),
  syncOrders: vi.fn(),
  syncSingleOrder: vi.fn(),
  manualShipOrder: vi.fn(),
  updateOrder: vi.fn(),
  deleteOrder: vi.fn(),
  importOrders: vi.fn(),
}));

describe('OrderList status sync', () => {
  beforeEach(() => {
    vi.mocked(getItems).mockResolvedValue([]);
    vi.mocked(getOrders).mockResolvedValue({
      success: true,
      data: [
        { id: 'refund-1', order_id: 'refund-1', cookie_id: 'account-1', item_id: '', item_title: '退款商品', item_image: 'https://img.alicdn.com/refund.jpg', buyer_id: '', quantity: 1, amount: '20', status: 'refunded' },
        { id: 'unknown-1', order_id: 'unknown-1', cookie_id: 'account-1', item_id: '', buyer_id: '', quantity: 1, amount: '30', status: 'unknown' },
      ] as any,
      total: 2,
      page: 1,
      page_size: 20,
      total_pages: 1,
    });
  });

  afterEach(() => cleanup());

  it('shows refunded and unknown as distinct truthful states', async () => {
    render(<OrderList />);

    expect(await screen.findByText('已退款')).toBeTruthy();
    expect((await screen.findAllByText('待核对')).length).toBeGreaterThan(1);
    expect(screen.getByRole('img', { name: '退款商品' })).toHaveAttribute('src', 'https://img.alicdn.com/refund.jpg');
  });

  it('shows login recovery guidance when recent sync requires login', async () => {
    vi.mocked(syncOrders).mockResolvedValue({
      success: false,
      message: '登录状态已过期，请先在账号管理更新登录状态',
      days: 90,
      summary: { total_seen: 0, discovered: 0, status_updated: 0, details_updated: 0, unchanged: 0, failed: 0 },
      requires_login: ['account-1'],
      accounts: [],
    });
    render(<OrderList />);

    fireEvent.click(await screen.findByRole('button', { name: '同步近90天订单' }));

    expect((await screen.findAllByText(/登录状态已过期/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/account-1/)).toBeTruthy();
  });
});
