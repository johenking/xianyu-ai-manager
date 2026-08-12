// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountDetail, DefaultReply, ReplyRule } from '../types';
import {
  getAccountDetails,
  getCards,
  getDefaultReplies,
  getDefaultReply,
  getReplyRules,
  getShippingRules,
} from '../services/api';
import Keywords from './Keywords';

vi.mock('../services/api', () => ({
  getAccountDetails: vi.fn(),
  getReplyRules: vi.fn(),
  updateReplyRule: vi.fn(),
  deleteReplyRule: vi.fn(),
  getShippingRules: vi.fn(),
  updateShippingRule: vi.fn(),
  deleteShippingRule: vi.fn(),
  getCards: vi.fn(),
  getDefaultReplies: vi.fn(),
  getDefaultReply: vi.fn(),
  updateDefaultReply: vi.fn(),
  deleteDefaultReply: vi.fn(),
  clearDefaultReplyRecords: vi.fn(),
}));

const accounts: AccountDetail[] = [
  { id: 'account-1', enabled: true, auto_confirm: false, nickname: '账号一' },
  { id: 'account-2', enabled: true, auto_confirm: false, nickname: '账号二' },
];

const buildReplyRule = (keyword: string): ReplyRule => ({
  id: keyword,
  keyword,
  reply_content: `${keyword} 的自动回复`,
  match_type: 'exact',
  enabled: true,
});

const buildDefaultReply = (cookieId: string, replyContent: string): DefaultReply => ({
  cookie_id: cookieId,
  enabled: true,
  reply_content: replyContent,
  reply_once: false,
  reply_image_url: '',
});

const openDefaultTab = () => fireEvent.click(screen.getByRole('button', { name: /账号默认回复/ }));

describe('Keywords 读取失败的可见错误态与竞态防护', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAccountDetails).mockResolvedValue(accounts);
    vi.mocked(getReplyRules).mockResolvedValue([buildReplyRule('包邮')]);
    vi.mocked(getShippingRules).mockResolvedValue([]);
    vi.mocked(getCards).mockResolvedValue([]);
    vi.mocked(getDefaultReplies).mockResolvedValue({});
  });

  afterEach(() => cleanup());

  it('关键词读取失败时展示可见错误与重试入口，而不是「暂无关键词」', async () => {
    vi.mocked(getReplyRules).mockRejectedValue(new Error('关键词接口暂时不可用'));
    render(<Keywords />);

    expect(await screen.findByText('关键词接口暂时不可用')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('关键词加载失败');
    expect(screen.queryByText('暂无关键词')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('点击重试会重新发起关键词请求并在成功后渲染数据', async () => {
    vi.mocked(getReplyRules)
      .mockRejectedValueOnce(new Error('关键词接口暂时不可用'))
      .mockResolvedValueOnce([buildReplyRule('重试后的关键词')]);
    render(<Keywords />);

    await screen.findByText('关键词接口暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('重试后的关键词')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(getReplyRules).toHaveBeenCalledTimes(2);
    expect(getReplyRules).toHaveBeenLastCalledWith('account-1');
  });

  it('账号列表读取失败时展示可见错误与重试入口，而不是「请选择账号」', async () => {
    vi.mocked(getAccountDetails)
      .mockRejectedValueOnce(new Error('账号接口暂时不可用'))
      .mockResolvedValueOnce(accounts);
    render(<Keywords />);

    expect(await screen.findByText('账号接口暂时不可用')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('账号列表加载失败');
    expect(screen.queryByText('选择一个账号以管理其关键词规则')).not.toBeInTheDocument();
    expect(getReplyRules).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('包邮')).toBeInTheDocument();
    expect(getAccountDetails).toHaveBeenCalledTimes(2);
    expect(getReplyRules).toHaveBeenCalledWith('account-1');
  });

  it('快速切换账号后过期的关键词响应不覆盖最新视图', async () => {
    const pending = new Map<string, (value: ReplyRule[]) => void>();
    vi.mocked(getReplyRules).mockImplementation((cookieId?: string) => new Promise<ReplyRule[]>((resolve) => {
      pending.set(String(cookieId), resolve);
    }));
    render(<Keywords />);

    await waitFor(() => expect(pending.has('account-1')).toBe(true));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'account-2' } });
    await waitFor(() => expect(pending.has('account-2')).toBe(true));

    await act(async () => {
      pending.get('account-2')!([buildReplyRule('账号二关键词')]);
    });
    expect(await screen.findByText('账号二关键词')).toBeInTheDocument();
    await act(async () => {
      pending.get('account-1')!([buildReplyRule('账号一关键词')]);
    });

    expect(screen.getByText('账号二关键词')).toBeInTheDocument();
    expect(screen.queryByText('账号一关键词')).not.toBeInTheDocument();
  });

  it('默认回复读取失败时提示错误而不是打开空表单', async () => {
    vi.mocked(getDefaultReply).mockRejectedValue(new Error('默认回复接口暂时不可用'));
    render(<Keywords />);

    await screen.findByText('包邮');
    openDefaultTab();
    fireEvent.click(screen.getAllByTitle('编辑')[0]);

    expect(await screen.findByText('加载默认回复失败：默认回复接口暂时不可用，请重试')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存默认回复' })).not.toBeInTheDocument();
  });

  it('快速切换编辑账号后过期的默认回复响应不覆盖表单', async () => {
    const resolvers: Array<(value: DefaultReply) => void> = [];
    vi.mocked(getDefaultReply).mockImplementation(() => new Promise<DefaultReply>((resolve) => {
      resolvers.push(resolve);
    }));
    render(<Keywords />);

    await screen.findByText('包邮');
    openDefaultTab();
    const editButtons = screen.getAllByTitle('编辑');
    fireEvent.click(editButtons[0]);
    fireEvent.click(editButtons[1]);
    await waitFor(() => expect(resolvers).toHaveLength(2));

    await act(async () => {
      resolvers[1](buildDefaultReply('account-2', '账号二默认回复'));
    });
    expect(await screen.findByDisplayValue('账号二默认回复')).toBeInTheDocument();
    await act(async () => {
      resolvers[0](buildDefaultReply('account-1', '账号一默认回复'));
    });

    expect(screen.getByDisplayValue('账号二默认回复')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('账号一默认回复')).not.toBeInTheDocument();
  });
});
