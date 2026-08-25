// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  copyAIItemKnowledge,
  generateAIItemKnowledge,
  getAIItemKnowledge,
  getItemsByCookie,
  saveAIItemKnowledgeDraft,
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
      message: '旧草稿已替换，新的AI结构化草稿已生成',
      source_detail_hash: 'hash-a',
      draft: {
        overview: { text: '卖家填写的概览', source: 'user', status: 'confirmed' },
        pricing: [{ label: 'Pro', amount: '145元', source: 'ai', status: 'pending' }],
        process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    vi.mocked(copyAIItemKnowledge).mockResolvedValue({
      message: '已覆盖 1 个商品草稿',
      copied_item_ids: ['item-b'],
      skipped_item_ids: [],
      missing_item_ids: [],
      source_kind: 'draft',
      copied_count: 1,
      skipped_count: 0,
      missing_count: 0,
      skipped_reasons: {},
    });
    vi.mocked(saveAIItemKnowledgeDraft).mockResolvedValue({
      ...emptyProfile,
      draft: {
        overview: { text: '已保存草稿', source: 'user', status: 'confirmed' },
        pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
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
      { overview: '卖家填写的概览' },
    ));
    expect(await screen.findByText('旧草稿已替换，新的AI结构化草稿已生成')).toBeTruthy();
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
    fireEvent.click(screen.getByRole('button', { name: '覆盖所选商品草稿' }));

    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', ['item-b']
    ));
    expect(getAIItemKnowledge).not.toHaveBeenCalledWith('account-1', 'item-b');
    expect(await screen.findByText(/已覆盖 1 个商品草稿/)).toBeTruthy();
  });

  it('can select all targets and saves dirty draft before copying', async () => {
    vi.mocked(getAIItemKnowledge).mockResolvedValue({
      ...emptyProfile,
      draft: {
        overview: { text: '同款Claude代充', source: 'user', status: 'confirmed' },
        pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');

    fireEvent.change(screen.getByDisplayValue('同款Claude代充'), {
      target: { value: '同款Claude代充，已编辑' },
    });
    fireEvent.click(screen.getByRole('button', { name: '复制到其他商品' }));
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    fireEvent.click(screen.getByRole('button', { name: '保存当前草稿并覆盖所选草稿' }));

    await waitFor(() => expect(saveAIItemKnowledgeDraft).toHaveBeenCalled());
    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', ['item-b']
    ));
  });

  it('shows knowledge state per copy target and can select only items without archives', async () => {
    vi.mocked(getAIItemKnowledge).mockResolvedValue({
      ...emptyProfile,
      draft: {
        overview: { text: '同款Claude代充', source: 'user', status: 'confirmed' },
        pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    vi.mocked(getItemsByCookie).mockResolvedValue([
      sourceItem,
      {
        ...sourceItem, id: 2, item_id: 'item-published', item_title: '已发布商品',
        knowledge_has_draft: true, knowledge_published_version: 2,
      },
      {
        ...sourceItem, id: 3, item_id: 'item-draft', item_title: '草稿商品',
        knowledge_has_draft: true, knowledge_published_version: 0,
      },
      {
        ...sourceItem, id: 4, item_id: 'item-none', item_title: '空白商品',
        knowledge_has_draft: false, knowledge_published_version: 0,
      },
    ] as any);

    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');
    fireEvent.click(screen.getByRole('button', { name: '复制到其他商品' }));

    expect(await screen.findByText('已发布 v2')).toBeTruthy();
    expect(screen.getByText('已有草稿')).toBeTruthy();
    expect(screen.getByText('无档案')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '只选无档案' }));
    expect((screen.getByRole('checkbox', { name: /空白商品/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /已发布商品/ }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: /草稿商品/ }) as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByRole('checkbox', { name: /已发布商品/ }));
    expect(screen.getByText(/其中 1 个目标已有档案，其草稿将被本档案覆盖/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('搜索复制目标商品'), { target: { value: '草稿商品' } });
    expect(screen.queryByRole('checkbox', { name: /空白商品/ })).toBeNull();
    expect(screen.getByRole('checkbox', { name: /草稿商品/ })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '覆盖所选商品草稿' }));
    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', ['item-none', 'item-published']
    ));
  });
});
