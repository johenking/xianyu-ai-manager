// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  copyAIItemKnowledge,
  generateAIItemKnowledge,
  getAIItemKnowledge,
  getItemsByCookie,
} from '../services/api';
import ItemKnowledgeModal from './ItemKnowledgeModal';

vi.mock('../services/api', () => ({
  getAIItemKnowledge: vi.fn(),
  getAIItemKnowledgeVersions: vi.fn(),
  generateAIItemKnowledge: vi.fn(),
  saveAIItemKnowledgeDraft: vi.fn(),
  publishAIItemKnowledge: vi.fn(),
  rollbackAIItemKnowledge: vi.fn(),
  getItemsByCookie: vi.fn(),
  copyAIItemKnowledge: vi.fn(),
}));

const sourceItem = {
  id: 1,
  cookie_id: 'account-1',
  item_id: 'item-a',
  item_title: 'Claude商品A',
  item_price: '145',
};

const emptyProfile = {
  cookie_id: 'account-1',
  item_id: 'item-a',
  draft: {},
  published: {},
  source_detail_hash: '',
  current_source_hash: 'hash-a',
  source_changed: false,
  published_version: 0,
  item: { item_id: 'item-a', title: 'Claude商品A', price: '145', detail: '官网代充' },
};

describe('ItemKnowledgeModal overview workflow', () => {
  beforeEach(() => {
    vi.mocked(getAIItemKnowledge).mockResolvedValue(emptyProfile);
    vi.mocked(getItemsByCookie).mockResolvedValue([
      sourceItem,
      { ...sourceItem, id: 2, item_id: 'item-b', item_title: 'Claude商品B', item_price: '155' },
    ]);
    vi.mocked(generateAIItemKnowledge).mockResolvedValue({
      message: '概览已保存，AI结构化草稿已生成',
      source_detail_hash: 'hash-a',
      draft: {
        overview: { text: '卖家填写的概览', source: 'user', status: 'confirmed' },
        pricing: [{ label: 'Pro', amount: '145元', source: 'ai', status: 'pending' }],
        process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    vi.mocked(copyAIItemKnowledge).mockResolvedValue({
      message: '已复制到 1 个商品草稿',
      copied_item_ids: ['item-b'],
      skipped_item_ids: [],
      missing_item_ids: [],
    });
  });

  afterEach(() => cleanup());

  it('requires and sends the seller overview before generating details', async () => {
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');
    const generateButton = screen.getByRole('button', { name: /第 2 步.*AI 生成结构化草稿/ });
    expect(generateButton.hasAttribute('disabled')).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/用自己的话描述这个商品/), {
      target: { value: '卖家填写的概览' },
    });
    expect(generateButton.hasAttribute('disabled')).toBe(false);
    fireEvent.click(generateButton);

    await waitFor(() => expect(generateAIItemKnowledge).toHaveBeenCalledWith(
      'account-1',
      'item-a',
      expect.objectContaining({
        overview: '卖家填写的概览',
        profile: expect.objectContaining({
          overview: expect.objectContaining({ text: '卖家填写的概览' }),
        }),
      }),
    ));
    expect(await screen.findByText('概览已保存，AI结构化草稿已生成')).toBeTruthy();
  });

  it('copies the current archive to selected product drafts only', async () => {
    vi.mocked(getAIItemKnowledge).mockResolvedValue({
      ...emptyProfile,
      draft: {
        overview: { text: '同款Claude代充', source: 'user', status: 'confirmed' },
        pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');
    fireEvent.click(screen.getByRole('button', { name: '复制到其他商品' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Claude商品B/ }));
    fireEvent.click(screen.getByRole('button', { name: '复制到所选草稿' }));

    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', ['item-b'], false
    ));
    expect(await screen.findByText('已复制到 1 个商品草稿')).toBeTruthy();
  });
});
