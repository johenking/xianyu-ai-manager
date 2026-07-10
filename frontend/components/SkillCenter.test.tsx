// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SkillCenter from './SkillCenter';
import {
  createSkillMonitorTask,
  getAccountDetails,
  getItemsByCookie,
  getSkillAgentPrompts,
  getSkillBrowserStatus,
  getSkillCapabilities,
  getSkillDeliveryDiagnostics,
  getSkillMonitorResults,
  getSkillMonitorTasks,
  getSkillOpsHealth,
  updateSkillMonitorTask,
} from '../services/api';

vi.mock('../services/api', () => ({
  createSkillMonitorTask: vi.fn(),
  getAccountDetails: vi.fn(),
  getItemsByCookie: vi.fn(),
  getSkillAgentPrompts: vi.fn(),
  getSkillBrowserStatus: vi.fn(),
  getSkillCapabilities: vi.fn(),
  getSkillDeliveryDiagnostics: vi.fn(),
  getSkillMonitorResults: vi.fn(),
  getSkillMonitorTasks: vi.fn(),
  getSkillOpsHealth: vi.fn(),
  runSkillMonitorTask: vi.fn(),
  testSkillAgentReply: vi.fn(),
  updateSkillAgentPrompt: vi.fn(),
  updateSkillMonitorTask: vi.fn(),
}));

describe('SkillCenter loading and monitor controls', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '账号一', nickname: '账号一' },
    ] as any);
    vi.mocked(getItemsByCookie).mockResolvedValue([] as any);
    vi.mocked(getSkillCapabilities).mockResolvedValue({
      manual_monitor: { available: true, label: '可用', detail: '手动运行' },
      scheduled_monitor: { available: true, label: '可用', detail: '单worker调度' },
      ai_filter: { available: true, label: '可用', detail: 'AI过滤' },
      notifications: { available: false, label: '缺少渠道', detail: '请先创建通知渠道' },
    });
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([]);
    vi.mocked(getSkillMonitorResults).mockResolvedValue([]);
    vi.mocked(getSkillAgentPrompts).mockResolvedValue([] as any);
    vi.mocked(getSkillOpsHealth).mockResolvedValue({ recent_logs: [] } as any);
    vi.mocked(getSkillBrowserStatus).mockResolvedValue({} as any);
    vi.mocked(getSkillDeliveryDiagnostics).mockResolvedValue({ recommendations: [] } as any);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads monitor data first and lazy-loads other skill tabs', async () => {
    render(<SkillCenter />);

    await screen.findByPlaceholderText('监控关键词');
    expect(getSkillMonitorTasks).toHaveBeenCalledTimes(1);
    expect(getSkillMonitorResults).toHaveBeenCalledTimes(1);
    expect(getSkillAgentPrompts).not.toHaveBeenCalled();
    expect(getSkillOpsHealth).not.toHaveBeenCalled();

    expect(screen.getByPlaceholderText(/AI 商品过滤要求/)).toBeInTheDocument();
    expect(screen.getByText('定时运行')).toBeInTheDocument();
    expect(screen.getByText('命中后通知')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /AI 专家客服/ }));
    await waitFor(() => expect(getSkillAgentPrompts).toHaveBeenCalledTimes(1));
    expect(getSkillOpsHealth).not.toHaveBeenCalled();
  });

  it('creates a scheduled task with AI and notification settings', async () => {
    vi.mocked(createSkillMonitorTask).mockResolvedValue({ success: true, id: 7, message: 'ok' });
    render(<SkillCenter />);

    const keyword = await screen.findByPlaceholderText('监控关键词');
    fireEvent.change(keyword, { target: { value: 'iPhone' } });
    fireEvent.change(screen.getByPlaceholderText(/AI 商品过滤要求/), {
      target: { value: '只保留低价商品' },
    });
    fireEvent.click(screen.getByText('定时运行').closest('label')!.querySelector('input')!);
    fireEvent.click(screen.getByText('命中后通知').closest('label')!.querySelector('input')!);
    fireEvent.click(screen.getByRole('button', { name: /创建监控任务/ }));

    await waitFor(() => expect(createSkillMonitorTask).toHaveBeenCalledWith(expect.objectContaining({
      keyword: 'iPhone',
      ai_filter: '只保留低价商品',
      notify_enabled: true,
      schedule_enabled: true,
      schedule_interval_minutes: 60,
    })));
    expect(await screen.findByText('监控任务已创建')).toBeInTheDocument();
  });

  it('updates the schedule state for an existing task', async () => {
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([{
      id: 9,
      name: '手机监控',
      keyword: 'iPhone',
      published_within_hours: 24,
      notify_enabled: false,
      enabled: true,
      schedule_enabled: false,
      schedule_interval_minutes: 30,
    }] as any);
    vi.mocked(updateSkillMonitorTask).mockResolvedValue({ success: true, message: 'ok' });
    render(<SkillCenter />);

    fireEvent.click(await screen.findByRole('button', { name: '开启定时' }));

    await waitFor(() => expect(updateSkillMonitorTask).toHaveBeenCalledWith(9, {
      schedule_enabled: true,
      schedule_interval_minutes: 30,
    }));
    expect(await screen.findByText('已开启定时监控')).toBeInTheDocument();
  });
});
