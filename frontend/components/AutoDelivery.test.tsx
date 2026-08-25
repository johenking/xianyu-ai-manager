// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AutoDelivery from './AutoDelivery';
import {
  getAccountDetails,
  getCards,
  getFulfillmentRecords,
  getItems,
  getItemsByCookie,
  resendFulfillmentRecord,
  updateItemDeliveryMode,
  updateItemDeliveryModesBatch,
} from '../services/api';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  getCards: vi.fn(),
  getItems: vi.fn(),
  getItemsByCookie: vi.fn(),
  getFulfillmentRecords: vi.fn(),
  resendFulfillmentRecord: vi.fn(),
  updateItemDeliveryMode: vi.fn(),
  updateItemDeliveryModesBatch: vi.fn(),
  createCard: vi.fn(),
  updateCard: vi.fn(),
  deleteCard: vi.fn(),
  importCardStock: vi.fn(),
  validateCardApi: vi.fn(),
}));

const accounts = [{ id: 'acct-1', nickname: '账号一', remark: '账号一' }];
const cards = [
  {
    id: 7,
    name: '会员资料',
    type: 'text',
    enabled: true,
    text_content: '网盘链接和提取码',
    stats: { available: 0, reserved: 0, used: 0, review: 0, bound: 1, low_stock: false },
  },
  {
    id: 8,
    name: '待验证接口',
    type: 'api',
    enabled: true,
    api_validation_status: 'unvalidated',
    api_token_configured: true,
  },
];
const items = [
  { id: 1, cookie_id: 'acct-1', item_id: 'i-1', item_title: '未配置商品', item_price: '10' },
  { id: 2, cookie_id: 'acct-1', item_id: 'i-2', item_title: '资料商品', item_price: '20', delivery_mode: 'resource', delivery_card_id: 7 },
  { id: 3, cookie_id: 'acct-1', item_id: 'i-3', item_title: '邀请商品', item_price: '30', delivery_mode: 'invite', invite_auto_fulfillment: true },
  { id: 4, cookie_id: 'acct-1', item_id: 'i-4', item_title: '关闭商品', item_price: '40', delivery_mode: 'off' },
];

describe('AutoDelivery delivery workbench', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue(accounts as any);
    vi.mocked(getCards).mockResolvedValue(cards as any);
    vi.mocked(getItemsByCookie).mockResolvedValue(items as any);
    vi.mocked(getItems).mockResolvedValue(items as any);
    vi.mocked(updateItemDeliveryMode).mockResolvedValue({ message: 'ok', item_id: 'i-1' } as any);
    vi.mocked(updateItemDeliveryModesBatch).mockImplementation(async (_cookieId, itemIds) => ({
      updated: itemIds,
      failed: [],
    }));
    vi.mocked(getFulfillmentRecords).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(resendFulfillmentRecord).mockResolvedValue({ status: 'succeeded', event_id: 9 });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('shows explicit resource, invite, off, and never-configured states truthfully', async () => {
    render(<AutoDelivery />);

    expect(await screen.findByText('未配置商品')).toBeInTheDocument();
    expect(screen.getByText('会员资料')).toBeInTheDocument();
    expect(screen.getByText('邀请重置')).toBeInTheDocument();
    expect(screen.getByText('已关闭')).toBeInTheDocument();
    expect(screen.getByText('未配置')).toBeInTheDocument();
  });

  it('saves one product through the atomic resource mode endpoint', async () => {
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('button', { name: '设置 未配置商品' }));
    fireEvent.click(screen.getByRole('radio', { name: /资源发货/ }));
    expect(screen.getByRole('radio', { name: '会员资料 可用' })).toBeChecked();
    expect(screen.getByRole('radio', { name: /待验证接口 待验证/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(updateItemDeliveryMode).toHaveBeenCalledWith('acct-1', 'i-1', 'resource', 7);
    });
  });

  it('switches a bound product to invite in one atomic call', async () => {
    render(<AutoDelivery />);
    await screen.findByText('资料商品');

    fireEvent.click(screen.getByRole('button', { name: '设置 资料商品' }));
    fireEvent.click(screen.getByRole('radio', { name: /邀请重置/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(updateItemDeliveryMode).toHaveBeenCalledWith('acct-1', 'i-2', 'invite', null);
    });
    expect(updateItemDeliveryMode).toHaveBeenCalledTimes(1);
  });

  it('keeps failed rows selected when a batch only partially succeeds', async () => {
    vi.mocked(updateItemDeliveryModesBatch).mockResolvedValueOnce({
      updated: ['i-1'],
      failed: [{ item_id: 'i-2', error: 'resource_disabled' }],
    });
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('checkbox', { name: '选择 未配置商品' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 资料商品' }));
    fireEvent.click(screen.getByRole('button', { name: /批量设置/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => expect(updateItemDeliveryModesBatch).toHaveBeenCalledWith(
      'acct-1', ['i-1', 'i-2'], 'resource', 7,
    ));
    expect(await screen.findByText(/已更新 1 个商品，1 个失败/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '选择 未配置商品' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: '选择 资料商品' })).toBeChecked();
  });

  it('exposes the resource library as the second workbench page', async () => {
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('button', { name: '资源库' }));
    expect(await screen.findByRole('heading', { name: '资源库' })).toBeInTheDocument();
    expect(screen.getByText('固定内容已配置')).toBeInTheDocument();
  });

  it('lists masked fulfillment records and confirms immutable-payload resend', async () => {
    vi.mocked(getFulfillmentRecords).mockResolvedValue({
      items: [{
        id: 77,
        order_id: 'order-one',
        resource_name: '会员资料',
        payload_preview: '已保存 1 条交付内容',
        status: 'succeeded',
        quantity: 1,
        can_resend: true,
        created_at: '2026-08-24T12:00:00+08:00',
      }],
      total: 1,
    });
    vi.stubGlobal('confirm', vi.fn(() => true));
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('button', { name: '发货记录' }));
    expect(await screen.findByText('订单 order-one')).toBeInTheDocument();
    expect(screen.getByText(/已保存 1 条交付内容/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('ORIGINAL-CODE');
    fireEvent.click(screen.getByRole('button', { name: /原样重发/ }));

    await waitFor(() => expect(resendFulfillmentRecord).toHaveBeenCalledWith(77));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('不会换卡、扣库存或再次调用供应方'));
  });
});
