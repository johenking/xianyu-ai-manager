// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getAITrainingRules, getItemsByCookie, sendAITrainingMessage } from '../services/api';
import AITrainingLab from './AITrainingLab';

vi.mock('../services/api', () => ({
  getItemsByCookie: vi.fn(),
  getAITrainingRules: vi.fn(),
  sendAITrainingMessage: vi.fn(),
  saveAITrainingRules: vi.fn(),
  deleteAITrainingRule: vi.fn(),
  setAITrainingRuleEnabled: vi.fn(),
  getAIItemKnowledge: vi.fn(),
  saveAIItemKnowledgeDraft: vi.fn(),
}));

describe('AITrainingLab rule audit', () => {
  beforeEach(() => {
    vi.mocked(getItemsByCookie).mockResolvedValue([{
      cookie_id: 'account-1', item_id: 'item-a', item_title: 'Claude代充', item_price: '145',
    } as any]);
    vi.mocked(getAITrainingRules).mockResolvedValue({
      global_rules: [],
      item_rules: [{ id: 3, scope: 'item', text: '不要说这是礼品卡', enabled: true }],
      context: {
        applied_rules: [{ id: 3, scope: 'item', text: '不要说这是礼品卡', enabled: true }],
        excluded_rules: [{ id: 4, scope: 'item', text: '其他商品规则', enabled: true, reason: 'other_item' }],
        disabled_rules: [{ id: 5, scope: 'item', text: '停用规则', enabled: false, reason: 'disabled' }],
        applied_count: 1,
        excluded_count: 1,
        disabled_count: 1,
        total_count: 3,
      },
    });
    vi.mocked(sendAITrainingMessage).mockResolvedValue({
      session_id: 'session-1',
      reply: '不是礼品卡，这是官网代充服务。',
      warnings: [],
      regenerated: true,
      knowledge_source: 'draft',
      knowledge_version: 2,
      rule_context: {
        applied_rules: [{ id: 3, scope: 'item', text: '不要说这是礼品卡', enabled: true }],
        excluded_rules: [], disabled_rules: [], applied_count: 1, excluded_count: 0, disabled_count: 0, total_count: 1,
      },
      rule_audit: {
        results: [{ rule_id: 3, text: '不要说这是礼品卡', status: 'followed', reason: '已否认礼品卡' }],
        violation_count: 0,
        unknown_count: 0,
        conflicts: ['规则与旧商品详情存在冲突'],
      },
    });
  });

  afterEach(() => cleanup());

  it('shows loaded rule counts and the final compliance audit', async () => {
    render(<AITrainingLab account={{ id: 'account-1', enabled: true, auto_confirm: true }} onClose={() => undefined} />);

    expect(await screen.findByText('已加载 1 / 3')).toBeTruthy();
    expect(screen.getByText('其他商品 1')).toBeTruthy();
    expect(screen.getByText('已停用 1')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '发送测试' }));

    await waitFor(() => expect(sendAITrainingMessage).toHaveBeenCalled());
    expect(await screen.findByText('已自动重答')).toBeTruthy();
    expect(screen.getByText('遵守 1')).toBeTruthy();
    expect(screen.getByText('规则与旧商品详情存在冲突')).toBeTruthy();
    expect(screen.getByText('本次读取：未发布草稿')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '查看完整审计' }));
    expect(screen.getByText('规则 R3 · 已遵守')).toBeTruthy();
    expect(screen.getByRole('button', { name: '收起审计' })).toBeTruthy();
  });

  it('shows when a price rule guard blocks the model reply', async () => {
    vi.mocked(sendAITrainingMessage).mockResolvedValue({
      session_id: 'session-1',
      reply: '按当前商品规则：Pro无质保145元，有质保155元。',
      warnings: [],
      regenerated: true,
      guarded_by_rule: true,
      guard_reason: 'price_rule_violation',
      guarded_rule_ids: [11],
      knowledge_source: 'draft',
      knowledge_version: 2,
      rule_context: {
        applied_rules: [{ id: 11, scope: 'item', text: 'Pro无质保145元，有质保155元', enabled: true }],
        excluded_rules: [], disabled_rules: [], applied_count: 1, excluded_count: 0, disabled_count: 0, total_count: 1,
      },
      rule_audit: {
        results: [{ rule_id: 11, text: 'Pro无质保145元，有质保155元', status: 'violated', reason: '模型回复135元' }],
        violation_count: 1,
        unknown_count: 0,
        conflicts: [],
      },
    });
    render(<AITrainingLab account={{ id: 'account-1', enabled: true, auto_confirm: true }} onClose={() => undefined} />);

    await screen.findByText('已加载 1 / 3');
    fireEvent.click(screen.getByRole('button', { name: '发送测试' }));

    expect(await screen.findByText('已规则兜底')).toBeTruthy();
    expect(screen.getByText('价格规则硬优先，已阻止违规报价')).toBeTruthy();
    expect(screen.getAllByText(/Pro无质保145元/).length).toBeGreaterThan(0);
  });
});
