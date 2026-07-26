// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteOrder, getOrderDetail, getOrders, syncOrders } from '../services/api';
import OrderList from './OrderList';
import { clearOrderItemImageCache } from './ui/OrderItemImage';

vi.mock('../services/api', () => ({
  getOrders: vi.fn(),
  getOrderDetail: vi.fn(),
  getItems: vi.fn().mockResolvedValue([]),
  getAccountDetails: vi.fn().mockResolvedValue([]),
  syncOrders: vi.fn(),
  syncSingleOrder: vi.fn(),
  manualShipOrder: vi.fn(),
  updateOrder: vi.fn(),
  deleteOrder: vi.fn(),
  importOrders: vi.fn(),
}));

const pageOf = (data: any[], extra: Record<string, unknown> = {}) => ({
  success: true,
  data,
  total: data.length,
  page: 1,
  page_size: 20,
  total_pages: 1,
  ...extra,
});

const refundOrder = {
  id: 'refund-1', order_id: 'refund-1', cookie_id: 'account-1', item_id: '',
  item_title: '退款商品', item_image: 'https://img.alicdn.com/refund.jpg',
  buyer_id: 'buyer-1', quantity: 1, amount: '20', status: 'refunded',
};
const unknownOrder = {
  id: 'unknown-1', order_id: 'unknown-1', cookie_id: 'account-1', item_id: '',
  buyer_id: '', quantity: 1, amount: '30', status: 'unknown',
};

beforeEach(() => {
  const values = new Map<string, string>();
  const storage = {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { values.set(key, String(value)); }),
    removeItem: vi.fn((key: string) => { values.delete(key); }),
    clear: vi.fn(() => { values.clear(); }),
    key: vi.fn((index: number) => Array.from(values.keys())[index] ?? null),
    get length() { return values.size; },
  };
  vi.stubGlobal('localStorage', storage);
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storage,
  });
});

describe('OrderList status sync', () => {
  beforeEach(() => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder, unknownOrder]) as any);
    vi.mocked(getOrderDetail).mockResolvedValue({ success: true, data: refundOrder as any });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('shows refunded and unknown as distinct truthful states', async () => {
    render(<OrderList />);

    expect(await screen.findByText('已退款')).toBeTruthy();
    expect((await screen.findAllByText('待核对')).length).toBeGreaterThan(1);
    // 移动端卡片与桌面表格双 DOM 各渲染一张商品图，且都回退到 CDN 直链
    const images = await screen.findAllByRole('img', { name: '退款商品' });
    expect(images.length).toBeGreaterThanOrEqual(2);
    images.forEach((img) => expect(img).toHaveAttribute('src', 'https://img.alicdn.com/refund.jpg'));
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

describe('OrderList server-side search', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('debounces input and sends search to the backend instead of fetching all pages', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);
    await screen.findByText('已退款');
    const callsBeforeTyping = vi.mocked(getOrders).mock.calls.length;

    const input = screen.getByPlaceholderText('搜索订单号/商品/买家...');
    fireEvent.change(input, { target: { value: '退' } });
    fireEvent.change(input, { target: { value: '退款' } });
    fireEvent.change(input, { target: { value: '退款商品' } });

    // 防抖窗口内不应触发新请求
    expect(vi.mocked(getOrders).mock.calls.length).toBe(callsBeforeTyping);

    await waitFor(() => {
      const lastCall = vi.mocked(getOrders).mock.calls.at(-1)?.[0];
      expect(lastCall?.search).toBe('退款商品');
    }, { timeout: 2000 });
    // 三次连续输入只应产生一次搜索请求
    expect(vi.mocked(getOrders).mock.calls.length).toBe(callsBeforeTyping + 1);
  });

  it('ignores a stale response that resolves after a newer one', async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    vi.mocked(getOrders)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementation(async () => pageOf([unknownOrder]) as any);

    render(<OrderList />);
    // 触发第二次请求（筛选变化）
    fireEvent.click(await screen.findByRole('button', { name: '待核对' }));
    await screen.findAllByText('待核对');

    // 迟到的第一次响应此时才返回，必须被丢弃（refund-1 订单号只会出现在数据行）
    await act(async () => {
      resolveFirst(pageOf([refundOrder]));
    });
    expect(screen.queryByText(/refund-1/)).toBeNull();
  });

  it('sends exactly one request when a filter resets page two to page one', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder], {
      total: 21,
      total_pages: 2,
    }) as any);
    render(<OrderList />);
    await screen.findByText('已退款');

    fireEvent.click(screen.getByRole('button', { name: /下一页/i }));
    await waitFor(() => {
      expect(vi.mocked(getOrders).mock.calls.at(-1)?.[0]?.page).toBe(2);
    });
    const callsBeforeFilter = vi.mocked(getOrders).mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: '待核对' }));
    await waitFor(() => {
      const latest = vi.mocked(getOrders).mock.calls.at(-1)?.[0];
      expect(latest?.page).toBe(1);
      expect(latest?.status).toBe('unknown');
    });
    expect(vi.mocked(getOrders).mock.calls.length).toBe(callsBeforeFilter + 1);
  });
});

describe('OrderList danger zone', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('requires explicit confirmation inside detail modal before deleting', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    vi.mocked(getOrderDetail).mockResolvedValue({ success: true, data: refundOrder as any });
    vi.mocked(deleteOrder).mockResolvedValue({ success: true } as any);
    render(<OrderList />);

    // 行内不再暴露删除按钮，先进详情
    fireEvent.click((await screen.findAllByTitle(/查看详情/))[0]);
    const deleteEntry = await screen.findByRole('button', { name: /删除订单/ });
    fireEvent.click(deleteEntry);
    expect(deleteOrder).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole('button', { name: '确认删除' }));
    await waitFor(() => expect(deleteOrder).toHaveBeenCalledWith('refund-1'));
  });
});

describe('OrderList truthful list presentation', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('masks buyer ids, preserves null amount, and labels the normalized order time', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([{
      ...unknownOrder,
      buyer_id: 'buyer-sensitive-12345678',
      amount: '',
      paid_amount_fen: null,
      ordered_at_utc: 1784512800,
      created_at: '2026-07-20 10:00:00',
    }]) as any);

    render(<OrderList />);

    expect((await screen.findAllByText('买家 · ID 尾号 5678')).length).toBeGreaterThan(0);
    expect(screen.queryByText('buyer-sensitive-12345678')).toBeNull();
    expect((await screen.findAllByText('未记录')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/成交时间/)).length).toBeGreaterThan(0);
  });

  it('falls back to created_at with an explicit label', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([{
      ...refundOrder,
      ordered_at_utc: null,
      created_at: '2026-07-20 10:00:00',
    }]) as any);

    render(<OrderList />);

    expect((await screen.findAllByText(/创建时间回退/)).length).toBeGreaterThan(0);
  });

  it('restores the existing Excel import entry instead of a JSON textarea', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([]) as any);
    render(<OrderList />);

    fireEvent.click(await screen.findByRole('button', { name: '插入订单' }));
    expect(await screen.findByText('选择Excel文件')).toBeTruthy();
    const input = document.querySelector('input[type="file"]');
    expect(input).toHaveAttribute('accept', '.xlsx,.xls');
    expect(screen.queryByText('订单 JSON 数组')).toBeNull();
  });
});

describe('OrderList request races and media failures', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('ignores a stale detail response after another order is selected', async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    let resolveSecond: (value: unknown) => void = () => {};
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder, unknownOrder]) as any);
    vi.mocked(getOrderDetail)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    render(<OrderList />);

    const detailButtons = await screen.findAllByTitle(/查看详情/);
    fireEvent.click(detailButtons[0]);
    fireEvent.click(detailButtons[1]);
    await act(async () => {
      resolveSecond({
        success: true,
        data: { ...unknownOrder, receiver_name: '第二位收货人' },
      });
    });
    expect(await screen.findByText('第二位收货人')).toBeTruthy();

    await act(async () => {
      resolveFirst({
        success: true,
        data: { ...refundOrder, receiver_name: '迟到的第一位收货人' },
      });
    });
    expect(screen.queryByText('迟到的第一位收货人')).toBeNull();
  });

  it('shows a machine-readable image reason and lets the user retry', async () => {
    localStorage.setItem('auth_token', 'test-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: vi.fn().mockResolvedValue({ detail: { reason: 'not_saved' } }),
    }));
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);

    const retries = await screen.findAllByRole('button', { name: /图片未保存.*重试/ });
    expect(retries.length).toBeGreaterThan(0);
    const callsBeforeRetry = vi.mocked(fetch).mock.calls.length;
    fireEvent.click(retries[0]);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(callsBeforeRetry));
  });

  it('replaces a failed buyer avatar with an icon', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([{
      ...refundOrder,
      buyer_display_name: '买家甲',
      buyer_avatar_url: 'https://img.alicdn.com/avatar.jpg',
    }]) as any);
    const { container } = render(<OrderList />);

    await waitFor(() => {
      expect(container.querySelectorAll(
        'img[src="https://img.alicdn.com/avatar.jpg"]',
      ).length).toBeGreaterThanOrEqual(2);
    });
    const mobileAvatar = container.querySelector(
      '.md\\:hidden img[src="https://img.alicdn.com/avatar.jpg"]',
    ) as HTMLImageElement;
    expect(mobileAvatar).not.toBeNull();
    const avatar = container.querySelector(
      'img[src="https://img.alicdn.com/avatar.jpg"]',
    ) as HTMLImageElement;
    fireEvent.error(avatar);
    expect((await screen.findAllByLabelText('买家头像占位')).length).toBeGreaterThan(0);
  });
});
