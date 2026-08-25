// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteOrder, getOrderDetail, getOrders, getAccountDetails, getItems, getShippingRules, manualShipOrder, syncOrders, syncSingleOrder } from '../services/api';
import OrderList from './OrderList';
import { clearOrderItemImageCache } from './ui/OrderItemImage';

vi.mock('../services/api', () => ({
  getOrders: vi.fn(),
  getOrderDetail: vi.fn(),
  getItems: vi.fn().mockResolvedValue([]),
  getAccountDetails: vi.fn().mockResolvedValue([]),
  getShippingRules: vi.fn().mockResolvedValue([]),
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

const syncCoverage = (total = 0) => ({
  status: { covered: 0, total, rate: 0 },
  item_image: { covered: 0, total, rate: 0 },
  buyer_nickname: { covered: 0, total, rate: 0 },
  buyer_avatar: { covered: 0, total, rate: 0 },
  amount: { covered: 0, total, rate: 0 },
  time: { covered: 0, total, rate: 0 },
});

const syncSummary = (extra: Record<string, unknown> = {}) => ({
  total_seen: 0,
  discovered: 0,
  status_updated: 0,
  details_updated: 0,
  unchanged: 0,
  failed: 0,
  status_unconfirmed: 0,
  field_coverage: syncCoverage(),
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
    const onNavigateAccounts = vi.fn();
    vi.mocked(syncOrders).mockResolvedValue({
      success: false,
      partial: false,
      message: '登录状态已过期，请先在账号管理更新登录状态',
      days: 90,
      summary: syncSummary(),
      requires_login: ['account-1'],
      accounts: [],
    });
    render(<OrderList onNavigateAccounts={onNavigateAccounts} />);

    fireEvent.click(await screen.findByRole('button', { name: '同步近90天订单' }));

    await waitFor(() => {
      expect(screen.getAllByText(/登录状态已过期/).length).toBeGreaterThan(0);
    }, { timeout: 5000 });
    expect(screen.getByText(/account-1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '前往账号管理' }));
    expect(onNavigateAccounts).toHaveBeenCalledTimes(1);
  });

  it('renders partial sync, unconfirmed status and field coverage', async () => {
    vi.mocked(syncOrders).mockResolvedValue({
      success: false,
      partial: true,
      message: '订单同步部分完成',
      days: 90,
      summary: syncSummary({
        total_seen: 1,
        failed: 1,
        status_unconfirmed: 1,
        field_coverage: {
          ...syncCoverage(1),
          item_image: { covered: 1, total: 1, rate: 1 },
          amount: { covered: 1, total: 1, rate: 1 },
        },
      }),
      requires_login: [],
      accounts: [{
        cookie_id: 'account-1',
        success: false,
        partial: true,
        error_code: 'status_unconfirmed',
        message: '平台状态仍待确认',
      }],
    });
    render(<OrderList />);

    fireEvent.click(await screen.findByRole('button', { name: '同步近90天订单' }));

    await waitFor(() => {
      expect(screen.getAllByText('订单同步部分完成').length).toBeGreaterThanOrEqual(2);
    }, { timeout: 5000 });
    expect(screen.getByText('状态待确认 1')).toBeInTheDocument();
    expect(screen.getByText('商品图片')).toBeInTheDocument();
    expect(screen.getByText('买家昵称')).toBeInTheDocument();
    expect(screen.getAllByText('100%').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/平台状态仍待确认/)).toBeInTheDocument();
  });

  it('treats single-order partial refresh as partial instead of success', async () => {
    vi.mocked(syncSingleOrder).mockResolvedValue({
      success: false,
      partial: true,
      error_code: 'status_unconfirmed',
      requires_login: false,
      message: '订单已获取部分字段，但平台状态仍待确认',
      summary: syncSummary({ status_unconfirmed: 1, failed: 1 }),
      fields_obtained: ['amount'],
      data: {
        order_id: 'refund-1',
        order_status: 'refunded',
        status_changed: false,
        details_changed: true,
      },
    });
    render(<OrderList />);

    fireEvent.click((await screen.findAllByTitle(/查看详情/))[0]);
    fireEvent.click(await screen.findByRole('button', { name: /同步此订单/ }));

    expect(await screen.findByText('订单已获取部分字段，但平台状态仍待确认')).toBeInTheDocument();
    expect(screen.queryByText('订单同步完成')).not.toBeInTheDocument();
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
    expect(input).toHaveAttribute('accept', '.xlsx');
    expect(screen.getByText('仅支持 .xlsx 格式')).toBeTruthy();
    expect(screen.queryByText(/\.xls 格式/)).toBeNull();
    expect(screen.queryByText('订单 JSON 数组')).toBeNull();
  });

  it('rejects a legacy .xls selection with a clear UI error', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([]) as any);
    render(<OrderList />);

    fireEvent.click(await screen.findByRole('button', { name: '插入订单' }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(['legacy'], 'orders.xls', { type: 'application/vnd.ms-excel' })],
      },
    });

    expect(await screen.findByText('仅支持 .xlsx 文件，请重新选择')).toBeTruthy();
    expect(screen.getByRole('button', { name: '导入订单' })).toBeDisabled();
    expect(screen.queryByText('orders.xls')).toBeNull();
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

  it('shows a machine-readable image reason and lets the user retry when no direct link exists', async () => {
    localStorage.setItem('auth_token', 'test-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: vi.fn().mockResolvedValue({ detail: { reason: 'not_saved' } }),
    }));
    // 订单没有 item_image 直链可降级：端点失败时仍显示对应占位与重试
    vi.mocked(getOrders).mockResolvedValue(pageOf([{ ...refundOrder, item_image: '' }]) as any);
    render(<OrderList />);

    const retries = await screen.findAllByRole('button', { name: /图片未保存.*重试/ });
    expect(retries.length).toBeGreaterThan(0);
    const callsBeforeRetry = vi.mocked(fetch).mock.calls.length;
    fireEvent.click(retries[0]);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(callsBeforeRetry));
  });

  it('renders the CDN direct link without touching the application proxy first', async () => {
    localStorage.setItem('auth_token', 'test-token');
    vi.stubGlobal('fetch', vi.fn());
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);

    const images = await screen.findAllByRole('img', { name: '退款商品' });
    expect(images.length).toBeGreaterThanOrEqual(2);
    images.forEach((img) => expect(img).toHaveAttribute('src', 'https://img.alicdn.com/refund.jpg'));
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByText(/图片源已失效/)).toBeNull();
  });

  it('uses one shared proxy fallback and surfaces its reason when duplicate direct images fail', async () => {
    localStorage.setItem('auth_token', 'test-token');
    const jsonMock = vi.fn().mockResolvedValue({ detail: { reason: 'not_saved' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: jsonMock,
    }));
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);

    const images = await screen.findAllByRole('img', { name: '退款商品' });
    expect(images.length).toBeGreaterThanOrEqual(2);
    images.forEach((imageNode) => fireEvent.error(imageNode));

    const retries = await screen.findAllByRole('button', { name: /图片未保存.*重试/ });
    expect(jsonMock).toHaveBeenCalledTimes(1);
    const retry = retries[0];
    const callsBeforeRetry = vi.mocked(fetch).mock.calls.length;
    fireEvent.click(retry);
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

describe('OrderList manual shipping choices', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('does not offer local full delivery for an invite item', async () => {
    const pendingOrder = {
      ...refundOrder,
      status: 'pending_ship',
      item_id: 'invite-item',
      item_title: '邀请商品',
    };
    vi.mocked(getOrders).mockResolvedValue(pageOf([pendingOrder]) as any);
    vi.mocked(getItems).mockResolvedValue([{
      id: 'invite-item',
      cookie_id: 'account-1',
      item_id: 'invite-item',
      item_title: '邀请商品',
      invite_auto_fulfillment: true,
    }] as any);
    vi.mocked(getShippingRules).mockResolvedValue([]);

    render(<OrderList />);
    fireEvent.click((await screen.findAllByRole('button', { name: '立即发货' }))[0]);

    expect(await screen.findByText('该商品由邀请服务履约，本地不重复发送卡券。')).toBeInTheDocument();
    expect(screen.queryByText('完整发货（匹配卡券并发送）')).toBeNull();
    expect(manualShipOrder).not.toHaveBeenCalled();
  });
});

describe('OrderList account attribution', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('labels each order with its owning account when multiple accounts exist', async () => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '主号小铺' },
      { id: 'account-2', remark: '备用号' },
    ] as any);
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);

    render(<OrderList />);

    // 桌面表格 + 移动卡片双 DOM 各渲染一次账号标识
    expect((await screen.findAllByText('主号小铺')).length).toBeGreaterThanOrEqual(2);
  });

  it('falls back to the raw cookie id when the account has no remark', async () => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1' },
      { id: 'account-2', remark: '备用号' },
    ] as any);
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);

    render(<OrderList />);

    expect((await screen.findAllByText('account-1')).length).toBeGreaterThanOrEqual(2);
  });

  it('hides the account label for single-account users to avoid noise', async () => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '唯一号' },
    ] as any);
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);

    render(<OrderList />);

    await screen.findByText('已退款');
    expect(screen.queryByText('唯一号')).toBeNull();
  });
});

describe('OrderList date range filter', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('passes the selected date range to the backend query', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);
    await screen.findByText('已退款');

    fireEvent.change(screen.getByLabelText('成交开始日期'), { target: { value: '2026-07-01' } });
    fireEvent.change(screen.getByLabelText('成交结束日期'), { target: { value: '2026-07-20' } });

    await waitFor(() => {
      const latest = vi.mocked(getOrders).mock.calls.at(-1)?.[0];
      expect(latest?.startDate).toBe('2026-07-01');
      expect(latest?.endDate).toBe('2026-07-20');
      expect(latest?.page).toBe(1);
    });
  });

  it('blocks a start-after-end range in the UI without hitting the backend', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);
    await screen.findByText('已退款');

    // 先设合法的开始日期并等其请求落地，作为干净基线
    fireEvent.change(screen.getByLabelText('成交开始日期'), { target: { value: '2026-07-20' } });
    await waitFor(() => {
      expect(vi.mocked(getOrders).mock.calls.at(-1)?.[0]?.startDate).toBe('2026-07-20');
    });
    const callsBefore = vi.mocked(getOrders).mock.calls.length;

    // 结束日期早于开始日期：进入非法态的这一步不得发起新请求
    fireEvent.change(screen.getByLabelText('成交结束日期'), { target: { value: '2026-07-01' } });

    expect(await screen.findByText('开始日期不得晚于结束日期')).toBeTruthy();
    expect(vi.mocked(getOrders).mock.calls.length).toBe(callsBefore);
  });

  it('clears the range and refetches without date bounds', async () => {
    vi.mocked(getOrders).mockResolvedValue(pageOf([refundOrder]) as any);
    render(<OrderList />);
    await screen.findByText('已退款');

    fireEvent.change(screen.getByLabelText('成交开始日期'), { target: { value: '2026-07-01' } });
    await waitFor(() => {
      expect(vi.mocked(getOrders).mock.calls.at(-1)?.[0]?.startDate).toBe('2026-07-01');
    });

    fireEvent.click(screen.getByLabelText('清除日期筛选'));
    await waitFor(() => {
      const latest = vi.mocked(getOrders).mock.calls.at(-1)?.[0];
      expect(latest?.startDate).toBeUndefined();
      expect(latest?.endDate).toBeUndefined();
    });
  });
});
