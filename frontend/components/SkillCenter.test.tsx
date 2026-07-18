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

const offlineMTopContract = {
  contract_version: 'stage-c-offline-v1',
  code_present: true,
  evidence_scope: 'code_and_configuration_only',
  gate: {
    master_enabled: false,
    mtop_enabled: false,
    network_allowed: false,
    executable: false,
    fail_closed: true,
  },
  limits: {
    page_size: 30,
    max_pages: 3,
    max_results: 90,
    max_runtime_seconds: 45,
    request_timeout_seconds: 15,
    max_attempts_per_page: 3,
    max_response_bytes: 1000000,
    global_requests_per_window: 30,
    account_requests_per_window: 6,
    budget_window_seconds: 60,
    base_backoff_seconds: 0.5,
    max_backoff_seconds: 10,
    failure_threshold: 3,
    failure_cooldown_seconds: 3600,
    probe_lease_seconds: 60,
  },
  canary: {
    keyword: 'iPhone 15 Pro',
    sort: 'latest',
    region: '',
    min_price: null,
    max_price: null,
    pages: 1,
    verification: 'unverified',
  },
  real_acceptance: {
    state: 'blocked',
    blocker_code: 'dedicated_test_account_required',
    shadow_verified: false,
    value_verified: false,
  },
} as const;

describe('SkillCenter loading and monitor controls', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '账号一', nickname: '账号一' },
    ] as any);
    vi.mocked(getItemsByCookie).mockResolvedValue([] as any);
    vi.mocked(getSkillCapabilities).mockResolvedValue({
      code_present: {
        available: true,
        state: 'present',
        badge_state: 'ready',
        label: '代码已加载',
        detail: '不代表真实运行通过',
        evidence: { offline_mtop_adapter: offlineMTopContract },
      },
      config_ready: { available: false, state: 'blocked', badge_state: 'missing', label: '尚未就绪', detail: '全局监控开关关闭' },
      last_real_search: { available: false, state: 'never', badge_state: 'missing', label: '从未验证', detail: '没有真实搜索记录' },
      last_scheduled_run: { available: false, state: 'never', badge_state: 'missing', label: '从未运行', detail: '没有定时运行记录' },
      last_ai_decision: { available: false, state: 'never', badge_state: 'missing', label: '从未判断', detail: '没有 AI 决策' },
      last_real_delivery: { available: false, state: 'never', badge_state: 'missing', label: '从未确认', detail: '没有通知记录' },
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
    expect(screen.getByText('代码存在')).toBeInTheDocument();
    expect(screen.getByText('配置就绪')).toBeInTheDocument();
    expect(screen.getByText('最近真实搜索')).toBeInTheDocument();
    expect(screen.getByText('从未验证')).toBeInTheDocument();
    expect(screen.getByText('不代表真实运行通过')).toBeInTheDocument();
    expect(screen.getByText('MTop 离线适配器')).toBeInTheDocument();
    expect(screen.getByText('默认关闭')).toBeInTheDocument();
    expect(screen.getByText(/“iPhone 15 Pro” · 最新排序/)).toBeInTheDocument();
    expect(screen.getByText(/当前缺少已批准的专用测试账号/)).toBeInTheDocument();

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
    fireEvent.click(screen.getByRole('switch', { name: '定时运行' }));
    fireEvent.click(screen.getByRole('switch', { name: '命中后通知' }));
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

  it('shows loading and fail-closed missing evidence states', async () => {
    let resolveCapabilities: ((value: any) => void) | undefined;
    vi.mocked(getSkillCapabilities).mockReturnValue(new Promise((resolve) => {
      resolveCapabilities = resolve;
    }));
    render(<SkillCenter />);

    expect(await screen.findByText('MTop 离线状态加载中')).toBeInTheDocument();
    resolveCapabilities?.({
      code_present: {
        available: true,
        state: 'present',
        badge_state: 'ready',
        label: '代码已加载',
        detail: '不代表真实运行通过',
      },
    });

    expect(await screen.findByText(/离线状态证据未返回/)).toBeInTheDocument();
  });

  it('keeps the offline panel blocked when capability loading fails', async () => {
    vi.mocked(getSkillCapabilities).mockRejectedValue(new Error('synthetic capability failure'));
    render(<SkillCenter />);

    expect(await screen.findByText('synthetic capability failure')).toBeInTheDocument();
    expect(screen.getByText(/离线状态证据未返回/)).toBeInTheDocument();
  });

  it('uses the protected-page navigation across every skill view', async () => {
    render(<SkillCenter />);

    await screen.findByPlaceholderText('监控关键词');
    expect(screen.queryByText('SKILL CENTER')).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '页面分区' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /AI 专家客服/ }));
    expect(await screen.findByText('测试回复')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /稳定增强/ }));
    expect(await screen.findByText('部署健康')).toBeInTheDocument();
    expect(screen.getByText('浏览器状态')).toBeInTheDocument();
    expect(screen.getByText('发货诊断')).toBeInTheDocument();
  });
});
