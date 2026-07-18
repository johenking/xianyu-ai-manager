// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const buildCapabilities = (detail = '不代表真实运行通过') => ({
  code_present: {
    available: true,
    state: 'present',
    badge_state: 'ready',
    label: '代码已加载',
    detail,
    evidence: { offline_mtop_adapter: offlineMTopContract },
  },
  config_ready: { available: false, state: 'blocked', badge_state: 'missing', label: '尚未就绪', detail: '全局监控开关关闭' },
  last_real_search: { available: false, state: 'never', badge_state: 'missing', label: '从未验证', detail: '没有真实搜索记录' },
  last_scheduled_run: { available: false, state: 'never', badge_state: 'missing', label: '从未运行', detail: '没有定时运行记录' },
  last_ai_decision: { available: false, state: 'never', badge_state: 'missing', label: '从未判断', detail: '没有 AI 决策' },
  last_real_delivery: { available: false, state: 'never', badge_state: 'missing', label: '从未确认', detail: '没有通知记录' },
}) as any;

const monitorTask = (id: number, name: string) => ({
  id,
  name,
  keyword: name,
  published_within_hours: 24,
  notify_enabled: false,
  enabled: true,
  schedule_enabled: false,
  schedule_interval_minutes: 30,
}) as any;

const deferred = <T,>() => {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
};

describe('SkillCenter loading and monitor controls', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '账号一', nickname: '账号一' },
    ] as any);
    vi.mocked(getItemsByCookie).mockResolvedValue([] as any);
    vi.mocked(getSkillCapabilities).mockResolvedValue(buildCapabilities());
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

  it('keeps the newest StrictMode snapshot when an older request completes last', async () => {
    const firstAccounts = deferred<any[]>();
    const secondAccounts = deferred<any[]>();
    const firstCapabilities = deferred<any>();
    const secondCapabilities = deferred<any>();
    const firstTasks = deferred<any[]>();
    const secondTasks = deferred<any[]>();
    const firstResults = deferred<any[]>();
    const secondResults = deferred<any[]>();

    vi.mocked(getAccountDetails)
      .mockImplementationOnce(() => firstAccounts.promise)
      .mockImplementationOnce(() => secondAccounts.promise);
    vi.mocked(getSkillCapabilities)
      .mockImplementationOnce(() => firstCapabilities.promise)
      .mockImplementationOnce(() => secondCapabilities.promise);
    vi.mocked(getSkillMonitorTasks)
      .mockImplementationOnce(() => firstTasks.promise)
      .mockImplementationOnce(() => secondTasks.promise);
    vi.mocked(getSkillMonitorResults)
      .mockImplementationOnce(() => firstResults.promise)
      .mockImplementationOnce(() => secondResults.promise);

    render(<StrictMode><SkillCenter /></StrictMode>);
    await waitFor(() => expect(getSkillCapabilities).toHaveBeenCalledTimes(2));

    await act(async () => {
      secondAccounts.resolve([{ id: 'new-account', remark: '最新账号' }] as any);
      secondCapabilities.resolve(buildCapabilities('最新能力证据'));
      secondTasks.resolve([monitorTask(2, '最新任务')]);
      secondResults.resolve([]);
      await Promise.resolve();
    });
    expect(await screen.findByText('最新任务')).toBeInTheDocument();
    expect(screen.getByText('最新能力证据')).toBeInTheDocument();

    await act(async () => {
      firstAccounts.resolve([{ id: 'old-account', remark: '旧账号' }] as any);
      firstCapabilities.resolve(buildCapabilities('旧能力证据'));
      firstTasks.resolve([monitorTask(1, '旧任务')]);
      firstResults.resolve([]);
      await Promise.resolve();
    });

    expect(screen.getByText('最新任务')).toBeInTheDocument();
    expect(screen.getByText('最新能力证据')).toBeInTheDocument();
    expect(screen.queryByText('旧任务')).not.toBeInTheDocument();
    expect(screen.queryByText('旧能力证据')).not.toBeInTheDocument();
  });

  it('ignores a stale rejection after the newest StrictMode request succeeds', async () => {
    const firstAccounts = deferred<any[]>();
    const secondAccounts = deferred<any[]>();
    const firstCapabilities = deferred<any>();
    const secondCapabilities = deferred<any>();
    const firstTasks = deferred<any[]>();
    const secondTasks = deferred<any[]>();
    const firstResults = deferred<any[]>();
    const secondResults = deferred<any[]>();

    vi.mocked(getAccountDetails)
      .mockImplementationOnce(() => firstAccounts.promise)
      .mockImplementationOnce(() => secondAccounts.promise);
    vi.mocked(getSkillCapabilities)
      .mockImplementationOnce(() => firstCapabilities.promise)
      .mockImplementationOnce(() => secondCapabilities.promise);
    vi.mocked(getSkillMonitorTasks)
      .mockImplementationOnce(() => firstTasks.promise)
      .mockImplementationOnce(() => secondTasks.promise);
    vi.mocked(getSkillMonitorResults)
      .mockImplementationOnce(() => firstResults.promise)
      .mockImplementationOnce(() => secondResults.promise);

    render(<StrictMode><SkillCenter /></StrictMode>);
    await waitFor(() => expect(getSkillCapabilities).toHaveBeenCalledTimes(2));

    await act(async () => {
      secondAccounts.resolve([{ id: 'new-account', remark: '最新账号' }] as any);
      secondCapabilities.resolve(buildCapabilities('最新能力证据'));
      secondTasks.resolve([monitorTask(2, '最新任务')]);
      secondResults.resolve([]);
      await Promise.resolve();
    });
    expect(await screen.findByText('最新任务')).toBeInTheDocument();

    await act(async () => {
      firstAccounts.resolve([{ id: 'old-account', remark: '旧账号' }] as any);
      firstTasks.resolve([monitorTask(1, '旧任务')]);
      firstResults.resolve([]);
      firstCapabilities.reject(new Error('过期请求失败'));
      await Promise.resolve();
    });

    expect(screen.getByText('最新任务')).toBeInTheDocument();
    expect(screen.queryByText('旧任务')).not.toBeInTheDocument();
    expect(screen.queryByText('过期请求失败')).not.toBeInTheDocument();
  });

  it('does not start account item loading after an unresolved mount is unmounted', async () => {
    const accounts = deferred<any[]>();
    const capabilities = deferred<any>();
    const tasks = deferred<any[]>();
    const results = deferred<any[]>();
    vi.mocked(getAccountDetails).mockReturnValue(accounts.promise);
    vi.mocked(getSkillCapabilities).mockReturnValue(capabilities.promise);
    vi.mocked(getSkillMonitorTasks).mockReturnValue(tasks.promise);
    vi.mocked(getSkillMonitorResults).mockReturnValue(results.promise);

    const view = render(<SkillCenter />);
    view.unmount();

    await act(async () => {
      accounts.resolve([{ id: 'late-account', remark: '迟到账号' }] as any);
      capabilities.resolve(buildCapabilities());
      tasks.resolve([]);
      results.resolve([]);
      await Promise.resolve();
    });

    expect(getItemsByCookie).not.toHaveBeenCalled();
  });

  it('propagates monitor snapshot failures through the initial fail-closed load', async () => {
    vi.mocked(getSkillMonitorTasks).mockRejectedValueOnce(new Error('监控快照加载失败'));
    render(<SkillCenter />);

    expect(await screen.findByText('监控快照加载失败')).toBeInTheDocument();
    expect(screen.getByText(/离线状态证据未返回/)).toBeInTheDocument();
  });

  it('marks retained data stale after refresh failure and clears the error on recovery', async () => {
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([monitorTask(3, '保留任务')]);
    render(<SkillCenter />);
    expect(await screen.findByText('保留任务')).toBeInTheDocument();

    vi.mocked(getSkillCapabilities).mockRejectedValueOnce(new Error('刷新能力失败'));
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));

    expect(await screen.findByText('刷新失败，当前显示上次成功数据：刷新能力失败')).toBeInTheDocument();
    expect(screen.getByText('保留任务')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));
    await waitFor(() => {
      expect(screen.queryByText(/刷新失败，当前显示上次成功数据/)).not.toBeInTheDocument();
    });
    expect(screen.getByText('保留任务')).toBeInTheDocument();
  });

  it('starts a fresh non-StrictMode request from the refresh action', async () => {
    render(<SkillCenter />);
    await screen.findByPlaceholderText('监控关键词');
    vi.mocked(getSkillMonitorTasks).mockClear();
    vi.mocked(getSkillMonitorResults).mockClear();

    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));

    await waitFor(() => expect(getSkillMonitorTasks).toHaveBeenCalledTimes(1));
    expect(getSkillMonitorResults).toHaveBeenCalledTimes(1);
  });

  it('keeps the user-selected test account after a successful refresh', async () => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '账号一', nickname: '账号一' },
      { id: 'account-2', remark: '账号二', nickname: '账号二' },
    ] as any);
    render(<SkillCenter />);
    await screen.findByPlaceholderText('监控关键词');

    fireEvent.click(screen.getByRole('button', { name: /AI 专家客服/ }));
    const accountSelect = await screen.findByDisplayValue('账号一');
    fireEvent.change(accountSelect, { target: { value: 'account-2' } });
    expect(screen.getByDisplayValue('账号二')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));
    await waitFor(() => expect(getAccountDetails).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue('账号二')).toBeInTheDocument();
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
