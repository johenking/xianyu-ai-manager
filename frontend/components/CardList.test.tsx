// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Card } from '../types';
import { getCards } from '../services/api';
import CardList from './CardList';

vi.mock('../services/api', () => ({
  getCards: vi.fn(),
  createCard: vi.fn(),
  updateCard: vi.fn(),
  deleteCard: vi.fn(),
}));

const buildCard = (overrides: Partial<Card> = {}): Card => ({
  id: 1,
  name: '首充卡密',
  type: 'text',
  description: '首充专用',
  enabled: true,
  text_content: 'CODE-0001',
  delay_seconds: 0,
  created_at: '2026-08-01 10:00:00',
  updated_at: '2026-08-01 10:00:00',
  ...overrides,
});

describe('CardList 卡密列表读取失败的可见错误态', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getCards).mockResolvedValue([buildCard()]);
  });

  afterEach(() => cleanup());

  it('读取失败时展示可见错误与重试入口，而不是「暂无卡密」', async () => {
    vi.mocked(getCards).mockRejectedValue(new Error('卡密接口暂时不可用'));
    render(<CardList />);

    expect(await screen.findByText('卡密接口暂时不可用')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('卡密列表加载失败');
    expect(screen.queryByText('暂无卡密配置，请点击右上角添加。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('点击重试会重新发起请求并在成功后渲染卡密', async () => {
    vi.mocked(getCards)
      .mockRejectedValueOnce(new Error('卡密接口暂时不可用'))
      .mockResolvedValueOnce([buildCard({ name: '重试后的卡密' })]);
    render(<CardList />);

    await screen.findByText('卡密接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('重试后的卡密')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(getCards).toHaveBeenCalledTimes(2);
  });

  it('连续重试时过期响应不覆盖最新列表', async () => {
    const resolvers: Array<(value: Card[]) => void> = [];
    vi.mocked(getCards)
      .mockRejectedValueOnce(new Error('卡密接口暂时不可用'))
      .mockImplementation(() => new Promise<Card[]>((resolve) => { resolvers.push(resolve); }));
    render(<CardList />);

    await screen.findByText('卡密接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(getCards).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(getCards).toHaveBeenCalledTimes(3));

    await act(async () => {
      resolvers[1]([buildCard({ id: 2, name: '最新卡密' })]);
    });
    expect(await screen.findByText('最新卡密')).toBeInTheDocument();
    await act(async () => {
      resolvers[0]([buildCard({ id: 3, name: '过期卡密' })]);
    });

    expect(screen.getByText('最新卡密')).toBeInTheDocument();
    expect(screen.queryByText('过期卡密')).not.toBeInTheDocument();
  });
});
