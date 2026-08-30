// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  copyAIItemKnowledge,
  getAccountDetails,
  getItems,
  importAIItemKnowledge,
} from '../services/api';
import ItemKnowledgeTransferModal from './ItemKnowledgeTransferModal';
import ToastViewport, { clearToasts } from './ui/Toast';
import ConfirmDialogHost, { clearConfirmDialogs } from './ui/ConfirmDialog';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  getItems: vi.fn(),
  copyAIItemKnowledge: vi.fn(),
  importAIItemKnowledge: vi.fn(),
}));

const currentItem = {
  id: 1,
  cookie_id: 'account-1',
  item_id: 'item-a',
  item_title: 'Codex邀请重置送积分',
  item_price: '2.99',
  knowledge_has_draft: true,
  knowledge_published_version: 3,
};

const allItems = [
  currentItem,
  {
    id: 2, cookie_id: 'account-1', item_id: 'item-b', item_title: 'ChatGPT Plus 合租一个月',
    item_price: '28.00', knowledge_has_draft: false, knowledge_published_version: 0,
  },
  {
    id: 3, cookie_id: 'account-2', item_id: 'item-c', item_title: 'Claude Pro 邀请码 5x',
    item_price: '145.00', knowledge_has_draft: true, knowledge_published_version: 0,
  },
  {
    id: 4, cookie_id: 'account-2', item_id: 'item-d', item_title: 'Cursor Pro 教育认证代做',
    item_price: '59.00', knowledge_has_draft: false, knowledge_published_version: 2,
  },
];

const importedProfile = {
  cookie_id: 'account-1',
  item_id: 'item-a',
  draft: {
    overview: { text: '搬来的概览', source: 'user', status: 'confirmed' },
    pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
  },
  published: {},
  source_detail_hash: '',
  current_source_hash: 'hash-a',
  source_changed: false,
  published_version: 0,
  item: { item_id: 'item-a', title: 'Codex邀请重置送积分', price: '2.99', detail: '' },
};

const renderModal = (props: Record<string, unknown> = {}) => render(
  <>
    <ToastViewport />
    <ConfirmDialogHost />
    <ItemKnowledgeTransferModal
      item={currentItem as any}
      canDistribute
      onImported={() => undefined}
      onClose={() => undefined}
      {...props}
    />
  </>
);

describe('ItemKnowledgeTransferModal', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', nickname: '陈潇轩很专业' },
      { id: 'account-2', nickname: '小梅很专业' },
    ] as any);
    vi.mocked(getItems).mockResolvedValue(allItems as any);
    vi.mocked(importAIItemKnowledge).mockResolvedValue({
      message: '已导入为当前商品草稿，确认无误后再发布',
      source_kind: 'draft',
      ...importedProfile,
    } as any);
    vi.mocked(copyAIItemKnowledge).mockResolvedValue({
      message: '已覆盖 2 个商品草稿',
      copied_item_ids: ['item-b', 'item-c'],
      skipped_item_ids: [],
      missing_item_ids: [],
      source_kind: 'draft',
      copied_count: 2,
      skipped_count: 0,
      missing_count: 0,
      skipped_reasons: {},
    } as any);
  });

  afterEach(() => {
    clearConfirmDialogs();
    clearToasts();
    cleanup();
    vi.clearAllMocks();
  });

  it('导入页只列出其他账号里有档案的商品，并按账号分组', async () => {
    renderModal();

    expect(await screen.findByRole('radio', { name: /Claude Pro 邀请码 5x/ })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /Cursor Pro 教育认证代做/ })).toBeTruthy();
    // 当前商品自己、以及没有档案的商品都不能作为来源
    expect(screen.queryByRole('radio', { name: /Codex邀请重置送积分/ })).toBeNull();
    expect(screen.queryByRole('radio', { name: /ChatGPT Plus 合租一个月/ })).toBeNull();
    expect(within(screen.getByRole('group', { name: '小梅很专业' })).getAllByRole('radio')).toHaveLength(2);
    expect(screen.queryByRole('group', { name: '陈潇轩很专业' })).toBeNull();
    expect(screen.getByRole('button', { name: '导入为当前商品草稿' }).hasAttribute('disabled')).toBe(true);
  });

  it('跨账号导入先弹覆盖确认，确认后调用接口并把新草稿回传给父组件', async () => {
    const onImported = vi.fn();
    renderModal({ onImported });

    fireEvent.click(await screen.findByRole('radio', { name: /Claude Pro 邀请码 5x/ }));
    fireEvent.click(screen.getByRole('button', { name: '导入为当前商品草稿' }));

    // 当前商品已有档案内容（canDistribute=true），导入前必须二次确认
    const dialog = await screen.findByRole('alertdialog', { name: '覆盖当前草稿' });
    fireEvent.click(within(dialog).getByRole('button', { name: '导入并覆盖' }));

    await waitFor(() => expect(importAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', { cookie_id: 'account-2', item_id: 'item-c' },
    ));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(
      expect.objectContaining({ item_id: 'item-a' }),
    ));
    await waitFor(() => expect(
      within(screen.getByRole('status')).getByText(/已导入为当前商品草稿/)
    ).toBeTruthy());
  });

  it('取消导入确认时不调用接口；当前无档案内容则不弹确认', async () => {
    renderModal();
    fireEvent.click(await screen.findByRole('radio', { name: /Claude Pro 邀请码 5x/ }));
    fireEvent.click(screen.getByRole('button', { name: '导入为当前商品草稿' }));
    const dialog = await screen.findByRole('alertdialog', { name: '覆盖当前草稿' });
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(importAIItemKnowledge).not.toHaveBeenCalled();
    cleanup();

    // 当前商品没有可分发内容时，导入不再弹确认，直接执行
    renderModal({ canDistribute: false });
    fireEvent.click(await screen.findByRole('radio', { name: /Claude Pro 邀请码 5x/ }));
    fireEvent.click(screen.getByRole('button', { name: '导入为当前商品草稿' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await waitFor(() => expect(importAIItemKnowledge).toHaveBeenCalled());
  });

  it('账号筛选和搜索都能收窄来源列表', async () => {
    renderModal();
    await screen.findByRole('radio', { name: /Claude Pro 邀请码 5x/ });

    fireEvent.change(screen.getByLabelText('搜索商品标题或ID'), { target: { value: 'Cursor' } });
    expect(screen.queryByRole('radio', { name: /Claude Pro 邀请码 5x/ })).toBeNull();
    expect(screen.getByRole('radio', { name: /Cursor Pro 教育认证代做/ })).toBeTruthy();

    fireEvent.change(screen.getByLabelText('搜索商品标题或ID'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '陈潇轩很专业' }));
    expect(await screen.findByText(/该账号下没有可作为来源的商品/)).toBeTruthy();
  });

  it('分发页可以跨账号多选，目标含已有档案时先确认再把二元组交给复制接口', async () => {
    renderModal({ initialTab: 'distribute' });
    await screen.findByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ });

    fireEvent.click(screen.getByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Claude Pro 邀请码 5x/ }));
    fireEvent.click(screen.getByRole('button', { name: '覆盖所选 2 个商品草稿' }));

    // Claude Pro 邀请码 5x 已有草稿档案，覆盖前需要确认
    const dialog = await screen.findByRole('alertdialog', { name: '覆盖已有档案' });
    fireEvent.click(within(dialog).getByRole('button', { name: '继续覆盖' }));

    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith('account-1', 'item-a', [
      { cookie_id: 'account-1', item_id: 'item-b' },
      { cookie_id: 'account-2', item_id: 'item-c' },
    ]));
  });

  it('分发页对已有档案的目标给出覆盖提醒，并支持只选无档案', async () => {
    renderModal({ initialTab: 'distribute' });
    await screen.findByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ });

    fireEvent.click(screen.getByRole('button', { name: '只选无档案' }));
    expect((screen.getByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /Claude Pro 邀请码 5x/ }) as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByRole('checkbox', { name: /Cursor Pro 教育认证代做/ }));
    expect(screen.getByText(/其中 1 个已有档案，草稿会被覆盖/)).toBeTruthy();
  });

  it('草稿有未保存修改时，分发前先保存再复制', async () => {
    const onBeforeDistribute = vi.fn().mockResolvedValue(undefined);
    renderModal({ initialTab: 'distribute', dirty: true, onBeforeDistribute });
    await screen.findByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ });

    fireEvent.click(screen.getByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存草稿并覆盖所选 1 个商品草稿' }));

    await waitFor(() => expect(onBeforeDistribute).toHaveBeenCalled());
    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalled());
  });

  it('当前商品没有档案时，分发页禁用并提示先建档案', async () => {
    renderModal({ canDistribute: false, initialTab: 'distribute' });

    expect(await screen.findByText(/当前商品还没有可分发的档案/)).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: /ChatGPT Plus 合租一个月/ })).toBeNull();
  });
});
