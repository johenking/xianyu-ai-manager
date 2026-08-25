// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Card } from '../types';
import {
  createCard,
  getCards,
  importCardStock,
  validateCardApi,
} from '../services/api';
import CardList from './CardList';

vi.mock('../services/api', () => ({
  getCards: vi.fn(),
  createCard: vi.fn(),
  updateCard: vi.fn(),
  deleteCard: vi.fn(),
  importCardStock: vi.fn(),
  validateCardApi: vi.fn(),
}));

const stats = { available: 0, reserved: 0, used: 0, review: 0, bound: 0, low_stock: false };
const buildCard = (overrides: Partial<Card> = {}): Card => ({
  id: 1,
  name: '课程资料包',
  type: 'text',
  description: '付款后发送',
  enabled: true,
  text_content: '网盘链接 + 提取码',
  delay_seconds: 0,
  low_stock_threshold: 5,
  stats,
  created_at: '2026-08-01 10:00:00',
  updated_at: '2026-08-01 10:00:00',
  ...overrides,
});

describe('CardList mature resource library', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getCards).mockResolvedValue([buildCard()]);
    vi.mocked(createCard).mockResolvedValue({ id: 9, message: 'ok' });
    vi.mocked(importCardStock).mockResolvedValue({
      added: 2,
      duplicates: 1,
      blank: 0,
      invalid: 0,
      total: 3,
      stats: { ...stats, available: 4 },
    });
    vi.mocked(validateCardApi).mockResolvedValue({ status: 'validated', message: 'ok' });
  });

  afterEach(() => cleanup());

  it('shows a visible retry state instead of presenting a failed read as empty', async () => {
    vi.mocked(getCards).mockRejectedValue(new Error('资源接口暂时不可用'));
    render(<CardList />);

    expect(await screen.findByText('资源接口暂时不可用')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('资源库加载失败');
    expect(screen.queryByText('资源库还是空的')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('retries the list read and renders the recovered resource', async () => {
    vi.mocked(getCards)
      .mockRejectedValueOnce(new Error('资源接口暂时不可用'))
      .mockResolvedValueOnce([buildCard({ name: '重试后的资料' })]);
    render(<CardList />);

    await screen.findByText('资源接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('重试后的资料')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(getCards).toHaveBeenCalledTimes(2);
  });

  it('does not let an older retry overwrite the newest list generation', async () => {
    const resolvers: Array<(value: Card[]) => void> = [];
    vi.mocked(getCards)
      .mockRejectedValueOnce(new Error('资源接口暂时不可用'))
      .mockImplementation(() => new Promise<Card[]>((resolve) => { resolvers.push(resolve); }));
    render(<CardList />);

    await screen.findByText('资源接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(getCards).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(getCards).toHaveBeenCalledTimes(3));

    await act(async () => { resolvers[1]([buildCard({ id: 2, name: '最新资料' })]); });
    expect(await screen.findByText('最新资料')).toBeInTheDocument();
    await act(async () => { resolvers[0]([buildCard({ id: 3, name: '过期资料' })]); });
    expect(screen.getByText('最新资料')).toBeInTheDocument();
    expect(screen.queryByText('过期资料')).not.toBeInTheDocument();
  });

  it('creates fixed material with the typed field instead of lossy generic content', async () => {
    render(<CardList />);
    await screen.findByText('课程资料包');
    fireEvent.click(screen.getByRole('button', { name: '新建资源' }));
    fireEvent.change(screen.getByLabelText('资源名称'), { target: { value: '百度网盘资料' } });
    fireEvent.change(screen.getByLabelText('发货内容'), { target: { value: '链接：https://pan.example\n提取码：1234' } });
    fireEvent.click(screen.getByRole('button', { name: '创建资源' }));

    await waitFor(() => expect(createCard).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createCard).mock.calls[0][0] as Record<string, unknown>;
    expect(payload).toMatchObject({
      name: '百度网盘资料',
      type: 'text',
      text_content: '链接：https://pan.example\n提取码：1234',
    });
    expect(payload).not.toHaveProperty('content');
  });

  it('previews and imports one-time stock through the dedicated dedupe endpoint', async () => {
    const dataCard = buildCard({
      id: 3,
      name: '一次一密库存',
      type: 'data',
      text_content: undefined,
      stats: { ...stats, available: 2 },
    });
    vi.mocked(getCards).mockResolvedValue([dataCard]);
    render(<CardList />);
    fireEvent.click(await screen.findByText('一次一密库存'));
    fireEvent.change(screen.getByLabelText('补货内容'), { target: { value: 'CODE-A\nCODE-A\nCODE-B' } });

    expect(screen.getByText('预检：3 条，输入内重复 1 条')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /确认补货/ }));
    await waitFor(() => expect(importCardStock).toHaveBeenCalledWith(3, {
      format: 'lines',
      content: 'CODE-A\nCODE-A\nCODE-B',
    }));
  });

  it('keeps API tokens masked and validates only the saved v1 configuration', async () => {
    const apiCard = buildCard({
      id: 4,
      name: '供应方接口',
      type: 'api',
      text_content: undefined,
      api_config: {
        protocol: 'fulfillment_api_v1',
        method: 'POST',
        url: 'https://provider.example/v1/allocate',
        timeout: 10,
        spec: {},
      },
      api_token_configured: true,
      token_preview: '••••cdef',
      api_validation_status: 'unvalidated',
    });
    const validated = { ...apiCard, api_validation_status: 'validated' as const };
    vi.mocked(getCards).mockResolvedValueOnce([apiCard]).mockResolvedValue([validated]);
    render(<CardList />);
    fireEvent.click(await screen.findByText('供应方接口'));

    expect(screen.getByLabelText('API Token')).toHaveAttribute('type', 'password');
    expect(screen.getByPlaceholderText(/已保存 ••••cdef/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('provider-secret');
    fireEvent.click(screen.getByRole('button', { name: /验证连接/ }));
    await waitFor(() => expect(validateCardApi).toHaveBeenCalledWith(4, undefined));
    expect(await screen.findByText('连接验证通过，现在可以绑定商品')).toBeInTheDocument();
  });
});
