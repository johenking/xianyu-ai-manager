// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  generateAIItemKnowledge,
  getAIItemKnowledge,
  getAccountDetails,
  getItems,
  importAIItemKnowledge,
} from '../services/api';
import ItemKnowledgeModal from './ItemKnowledgeModal';
import { clearToasts } from './ui/Toast';
import { clearConfirmDialogs } from './ui/ConfirmDialog';

vi.mock('../services/api', () => ({
  getAIItemKnowledge: vi.fn(),
  getAIItemKnowledgeVersions: vi.fn(),
  generateAIItemKnowledge: vi.fn(),
  saveAIItemKnowledgeDraft: vi.fn(),
  publishAIItemKnowledge: vi.fn(),
  rollbackAIItemKnowledge: vi.fn(),
  getAccountDetails: vi.fn(),
  getItems: vi.fn(),
  copyAIItemKnowledge: vi.fn(),
  importAIItemKnowledge: vi.fn(),
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
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', nickname: '账号一' },
      { id: 'account-2', nickname: '账号二' },
    ] as any);
    vi.mocked(getItems).mockResolvedValue([
      sourceItem,
      {
        ...sourceItem, id: 2, cookie_id: 'account-2', item_id: 'item-c',
        item_title: '隔壁账号有档案商品', knowledge_has_draft: true, knowledge_published_version: 0,
      },
    ] as any);
    vi.mocked(generateAIItemKnowledge).mockResolvedValue({
      message: '旧草稿已替换，新的AI结构化草稿已生成',
      source_detail_hash: 'hash-a',
      draft: {
        overview: { text: '卖家填写的概览', source: 'user', status: 'confirmed' },
        pricing: [{ label: 'Pro', amount: '145元', source: 'ai', status: 'pending' }],
        process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    });
    vi.mocked(importAIItemKnowledge).mockResolvedValue({
      message: '已导入为当前商品草稿，确认无误后再发布',
      source_kind: 'draft',
      ...emptyProfile,
      draft: {
        overview: { text: '搬来的概览', source: 'user', status: 'confirmed' },
        pricing: [], process: [], after_sales: [], forbidden: [], faqs: [], notes: [],
      },
    } as any);
  });

  afterEach(() => {
    clearConfirmDialogs();
    clearToasts();
    cleanup();
    vi.clearAllMocks();
  });

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

  it('点档案搬运会打开搬运弹窗，并把当前档案状态传下去', async () => {
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');

    fireEvent.click(screen.getByRole('button', { name: /档案搬运/ }));

    // 默认落在导入 tab，候选按账号分组加载
    expect(await screen.findByRole('tab', { name: /从其他商品导入/ })).toBeTruthy();
    expect(await screen.findByRole('radio', { name: /隔壁账号有档案商品/ })).toBeTruthy();
    await waitFor(() => expect(getItems).toHaveBeenCalled());

    // 当前商品没有档案内容 → 分发方向被禁用并说明原因
    fireEvent.click(screen.getByRole('tab', { name: /复制到其他商品/ }));
    expect(await screen.findByText(/当前商品还没有可分发的档案/)).toBeTruthy();
  });

  it('导入成功后主区直接显示导入来的草稿内容', async () => {
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');

    fireEvent.click(screen.getByRole('button', { name: /档案搬运/ }));
    fireEvent.click(await screen.findByRole('radio', { name: /隔壁账号有档案商品/ }));
    // 当前草稿为空，导入不需要二次确认
    fireEvent.click(screen.getByRole('button', { name: '导入为当前商品草稿' }));

    await waitFor(() => expect(importAIItemKnowledge).toHaveBeenCalledWith(
      'account-1', 'item-a', { cookie_id: 'account-2', item_id: 'item-c' },
    ));
    // 弹窗关闭，主区草稿换成导入内容并给出提示
    expect(await screen.findByDisplayValue('搬来的概览')).toBeTruthy();
    expect(screen.getByText('已导入档案为当前草稿，确认内容后再发布')).toBeTruthy();
    expect(screen.queryByRole('tab', { name: /从其他商品导入/ })).toBeNull();
  });
});
