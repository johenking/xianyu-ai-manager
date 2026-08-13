// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AutoDelivery from './AutoDelivery';
import {
  getAccountDetails,
  getCards,
  getItems,
  getItemsByCookie,
  updateItemDeliveryBinding,
  updateItemDeliveryBindingsBatch,
  updateItemInviteAutoFulfillment,
} from '../services/api';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  getCards: vi.fn(),
  getItems: vi.fn(),
  getItemsByCookie: vi.fn(),
  updateItemDeliveryBinding: vi.fn(),
  updateItemDeliveryBindingsBatch: vi.fn(),
  updateItemInviteAutoFulfillment: vi.fn(),
  createCard: vi.fn(),
  updateCard: vi.fn(),
  deleteCard: vi.fn(),
}));

const accounts = [{ id: 'acct-1', nickname: '账号一', remark: '账号一' }];
const cards = [
  { id: 7, name: '会员卡密', type: 'text', enabled: true },
  { id: 8, name: '停用卡密', type: 'text', enabled: false },
];
const items = [
  { id: 1, cookie_id: 'acct-1', item_id: 'i-1', item_title: '未配置商品', item_price: '10' },
  { id: 2, cookie_id: 'acct-1', item_id: 'i-2', item_title: '卡密商品', item_price: '20', delivery_card_id: 7 },
  { id: 3, cookie_id: 'acct-1', item_id: 'i-3', item_title: '邀请商品', item_price: '30', invite_auto_fulfillment: true },
];

describe('AutoDelivery', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue(accounts as any);
    vi.mocked(getCards).mockResolvedValue(cards as any);
    vi.mocked(getItemsByCookie).mockResolvedValue(items as any);
    vi.mocked(getItems).mockResolvedValue(items as any);
    vi.mocked(updateItemDeliveryBinding).mockResolvedValue({} as any);
    vi.mocked(updateItemDeliveryBindingsBatch).mockResolvedValue({ message: '', updated: 1, failed: [] } as any);
    vi.mocked(updateItemInviteAutoFulfillment).mockResolvedValue({} as any);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows each item current delivery mode, including the keyword fallback', async () => {
    render(<AutoDelivery />);

    expect(await screen.findByText('未配置商品')).toBeTruthy();
    expect(screen.getByText('会员卡密')).toBeTruthy();
    expect(screen.getByText('邀请重置')).toBeTruthy();
    expect(screen.getByText('关键词兜底')).toBeTruthy();
  });

  it('binds a card to one item and clears any invite selection', async () => {
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getAllByRole('button', { name: /设置/ })[0]);
    fireEvent.click(screen.getByRole('radio', { name: /发送卡密/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(updateItemDeliveryBindingsBatch).toHaveBeenCalledWith('acct-1', ['i-1'], 7);
    });
    // 停用的卡密不出现在可选项里
    expect(screen.queryByText('停用卡密（text）')).toBeNull();
  });

  it('switching an item to invite clears its card binding first', async () => {
    render(<AutoDelivery />);
    await screen.findByText('卡密商品');

    fireEvent.click(screen.getAllByRole('button', { name: /设置/ })[1]);
    fireEvent.click(screen.getByRole('radio', { name: /邀请重置/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(updateItemDeliveryBinding).toHaveBeenCalledWith('acct-1', 'i-2', null);
    });
    expect(updateItemInviteAutoFulfillment).toHaveBeenCalledWith('acct-1', 'i-2', true);
  });

  it('applies one card to several selected items in a single batch call', async () => {
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('checkbox', { name: '选择 未配置商品' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 卡密商品' }));
    fireEvent.click(screen.getByRole('button', { name: /批量设置发货/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(updateItemDeliveryBindingsBatch).toHaveBeenCalledWith('acct-1', ['i-1', 'i-2'], 7);
    });
  });

  it('offers the card library as a second tab so cards can be created in place', async () => {
    render(<AutoDelivery />);
    await screen.findByText('未配置商品');

    fireEvent.click(screen.getByRole('button', { name: /卡密资源库/ }));
    expect(await screen.findByText('卡密库存')).toBeTruthy();
  });
});
