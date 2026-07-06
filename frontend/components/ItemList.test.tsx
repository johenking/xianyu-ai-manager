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
});
