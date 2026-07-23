// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ItemList from './ItemList';
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
  syncItemsFromAccount: vi.fn(),
  deleteItem: vi.fn(),
  updateItemMultiSpec: vi.fn(),
  updateItemMultiQuantityDelivery: vi.fn(),
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
    cleanup();
    vi.clearAllMocks();
  });

  it('defaults to the first account and only shows all items after choosing all accounts', async () => {
    render(<ItemList />);

    await screen.findByText('账号一商品');
    const image = screen.getByRole('img', { name: '账号一商品' });
    expect(image).toHaveAttribute('src', 'https://img.alicdn.com/account-one.jpg');
    expect(image).toHaveAttribute('referrerpolicy', 'no-referrer');
    fireEvent.error(image);
    await waitFor(() => expect(screen.queryByRole('img', { name: '账号一商品' })).not.toBeInTheDocument());
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
});
