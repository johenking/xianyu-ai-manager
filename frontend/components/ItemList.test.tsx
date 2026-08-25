// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ItemList from './ItemList';
import ToastViewport, { clearToasts } from './ui/Toast';
import {
  getAccountDetails,
  getItems,
  getItemsByCookie,
  syncItemsFromAccount,
  deleteItem,
  updateItemMultiSpec,
  updateItemMultiQuantityDelivery,
} from '../services/api';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  getItems: vi.fn(),
  getItemsByCookie: vi.fn(),
  getCards: vi.fn(async () => []),
  syncItemsFromAccount: vi.fn(),
  deleteItem: vi.fn(),
  updateItemMultiSpec: vi.fn(),
  updateItemMultiQuantityDelivery: vi.fn(),
  updateItemDeliveryBinding: vi.fn(),
  updateItemInviteAutoFulfillment: vi.fn(),
}));

// 知识档案弹窗有独立测试；这里只验证 ItemList 与它的开关/刷新协作
vi.mock('./ItemKnowledgeModal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <button onClick={onClose}>关闭知识档案弹窗</button>
  ),
}));

const accounts = [
  {
    id: 'account-1',
    value: 'unb=account-1',
    cookie: 'unb=account-1',
    enabled: true,
    auto_confirm: false,
    remark: '账号一',
    nickname: '账号一',
  },
  {
    id: 'account-2',
    value: 'unb=account-2',
    cookie: 'unb=account-2',
    enabled: true,
    auto_confirm: false,
    remark: '账号二',
    nickname: '账号二',
  },
] as any;

const accountOneItems = [
  {
    id: 1,
    cookie_id: 'account-1',
    item_id: 'item-1',
    item_title: '账号一商品',
    item_price: '145',
    item_image: 'https://img.alicdn.com/account-one.jpg',
  },
] as any;

const knowledgeItems = [
  {
    id: 11,
    cookie_id: 'account-1',
    item_id: 'item-published',
    item_title: '已发布档案商品',
    item_price: '145',
    knowledge_has_draft: true,
    knowledge_published_version: 3,
  },
  {
    id: 12,
    cookie_id: 'account-1',
    item_id: 'item-draft',
    item_title: '草稿档案商品',
    item_price: '155',
    knowledge_has_draft: true,
    knowledge_published_version: 0,
  },
  {
    id: 13,
    cookie_id: 'account-1',
    item_id: 'item-none',
    item_title: '未建档商品',
    item_price: '165',
    knowledge_has_draft: false,
    knowledge_published_version: 0,
  },
] as any;

const accountTwoItems = [
  {
    id: 2,
    cookie_id: 'account-2',
    item_id: 'item-2',
    item_title: '账号二商品',
    item_price: '155',
  },
] as any;

describe('ItemList account filtering', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue(accounts);
    vi.mocked(getItemsByCookie).mockImplementation(async (cookieId: string) => (
      cookieId === 'account-1' ? accountOneItems : accountTwoItems
    ));
    vi.mocked(getItems).mockResolvedValue([...accountOneItems, ...accountTwoItems]);
    vi.mocked(syncItemsFromAccount).mockResolvedValue({ success: true, message: '商品同步完成' });
    vi.mocked(deleteItem).mockResolvedValue({ message: 'deleted' });
    vi.mocked(updateItemMultiSpec).mockResolvedValue({ message: 'updated' });
    vi.mocked(updateItemMultiQuantityDelivery).mockResolvedValue({ message: 'updated' });
  });

  afterEach(() => {
    clearToasts();
    cleanup();
    vi.clearAllMocks();
  });

  it('defaults to the first account and only shows all items after choosing all accounts', async () => {
    render(<ItemList />);

    await screen.findByText('账号一商品');
    const image = screen.getByRole('img', { name: '账号一商品' });
    expect(image).toHaveAttribute('src', 'https://img.alicdn.com/account-one.jpg');
    expect(image).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(screen.queryByText('账号二商品')).not.toBeInTheDocument();
    expect(getItemsByCookie).toHaveBeenCalledWith('account-1');
    expect(getItems).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('商品账号'), { target: { value: 'account-2' } });
    await screen.findByText('账号二商品');
    expect(screen.queryByText('账号一商品')).not.toBeInTheDocument();
    expect(getItemsByCookie).toHaveBeenLastCalledWith('account-2');

    fireEvent.change(screen.getByLabelText('商品账号'), { target: { value: '__all__' } });
    await waitFor(() => expect(getItems).toHaveBeenCalledTimes(1));
    expect(screen.getByText('账号一商品')).toBeInTheDocument();
    expect(screen.getByText('账号二商品')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /同步商品/ })).toBeDisabled();
  });

  it('shows reconciliation statistics and retries the image when sync returns a new URL', async () => {
    vi.mocked(getItemsByCookie)
      .mockResolvedValueOnce(accountOneItems)
      .mockResolvedValueOnce([{
        ...accountOneItems[0],
        item_image: 'https://img.alicdn.com/account-one-new.jpg',
      }] as any);
    vi.mocked(syncItemsFromAccount).mockResolvedValue({
      success: true,
      message: '同步完成：在售 1 件，隐藏历史 2 件，更新图片 1 件',
      active_count: 1,
      hidden_count: 2,
      images_updated: 1,
      failed_count: 0,
    });

    render(<ItemList />);

    const initialImage = await screen.findByRole('img', { name: '账号一商品' });
    fireEvent.error(initialImage);
    fireEvent.click(screen.getByRole('button', { name: /同步商品/ }));

    expect(await screen.findByText('同步完成：在售 1 件，隐藏历史 2 件，更新图片 1 件')).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: '账号一商品' })).toHaveAttribute(
      'src',
      'https://img.alicdn.com/account-one-new.jpg',
    );
  });

  it('marks knowledge state on each card and filters by knowledge presence', async () => {
    vi.mocked(getItemsByCookie).mockResolvedValue(knowledgeItems);

    render(<ItemList />);
    await screen.findByText('已发布档案商品');

    expect(screen.getByRole('button', { name: /知识档案 · 已发布 v3/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /知识档案 · 草稿未发布/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /知识档案 · 未建档/ })).toBeInTheDocument();
    expect(screen.getByText('档案 v3')).toBeInTheDocument();
    expect(screen.getByText('档案草稿')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '有档案 (2)' }));
    expect(screen.getByText('已发布档案商品')).toBeInTheDocument();
    expect(screen.getByText('草稿档案商品')).toBeInTheDocument();
    expect(screen.queryByText('未建档商品')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '无档案 (1)' }));
    expect(screen.getByText('未建档商品')).toBeInTheDocument();
    expect(screen.queryByText('已发布档案商品')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '全部 (3)' }));
    expect(screen.getByText('已发布档案商品')).toBeInTheDocument();
    expect(screen.getByText('未建档商品')).toBeInTheDocument();
  });

  it('pops toast feedback for sync and toggle operations', async () => {
    render(
      <>
        <ItemList />
        <ToastViewport />
      </>
    );
    await screen.findByText('账号一商品');

    fireEvent.click(screen.getByRole('button', { name: '多规格' }));
    await waitFor(() => expect(updateItemMultiSpec).toHaveBeenCalled());
    await waitFor(() => expect(
      within(screen.getByRole('status')).getByText('多规格已开启')
    ).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /同步商品/ }));
    await waitFor(() => expect(syncItemsFromAccount).toHaveBeenCalled());
    await waitFor(() => expect(
      within(screen.getByRole('status')).getByText('商品同步完成')
    ).toBeInTheDocument());
  });

  it('pops an error toast when an operation fails', async () => {
    vi.mocked(updateItemMultiQuantityDelivery).mockRejectedValueOnce(new Error('切换多数量发货失败：网络异常'));

    render(
      <>
        <ItemList />
        <ToastViewport />
      </>
    );
    await screen.findByText('账号一商品');

    fireEvent.click(screen.getByRole('button', { name: '多数量发货' }));
    await waitFor(() => expect(
      within(screen.getByRole('status')).getByText('切换多数量发货失败：网络异常')
    ).toBeInTheDocument());
  });

  it('refreshes items after closing the knowledge modal so badges stay current', async () => {
    vi.mocked(getItemsByCookie)
      .mockResolvedValueOnce(knowledgeItems)
      .mockResolvedValueOnce([
        { ...knowledgeItems[2], knowledge_has_draft: true },
        ...knowledgeItems.slice(0, 2),
      ] as any);

    render(<ItemList />);
    await screen.findByText('未建档商品');

    fireEvent.click(screen.getByRole('button', { name: /知识档案 · 未建档/ }));
    fireEvent.click(await screen.findByRole('button', { name: '关闭知识档案弹窗' }));

    await waitFor(() => expect(getItemsByCookie).toHaveBeenCalledTimes(2));
    // 刷新后原“未建档”商品变为草稿态，列表里出现两个草稿标识、不再有未建档
    await waitFor(() => expect(screen.getAllByRole('button', { name: /知识档案 · 草稿未发布/ })).toHaveLength(2));
    expect(screen.queryByRole('button', { name: /知识档案 · 未建档/ })).not.toBeInTheDocument();
  });
});
