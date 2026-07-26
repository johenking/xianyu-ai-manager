// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SkillCenter from './SkillCenter';
import {
  createSkillMonitorTask,
  getAccountDetails,
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
  getSkillBrowserStatus: vi.fn(),
  getSkillCapabilities: vi.fn(),
  getSkillDeliveryDiagnostics: vi.fn(),
  getSkillMonitorResults: vi.fn(),
  getSkillMonitorTasks: vi.fn(),
  getSkillOpsHealth: vi.fn(),
  runSkillMonitorTask: vi.fn(),
  updateSkillMonitorTask: vi.fn(),
}));

describe('SkillCenter loading and monitor controls', () => {
  beforeEach(() => {
    vi.mocked(getAccountDetails).mockResolvedValue([
      { id: 'account-1', remark: '账号一', nickname: '账号一', search_readiness: { ready: true, state: 'ready', blockers: [] } },
    ] as any);
    vi.mocked(getSkillCapabilities).mockResolvedValue({
      runtime_mode: 'preview',
      operation_gates: {
        manual_run: { enabled: false, reason_code: 'monitor_disabled', message: '真实搜索总开关关闭，当前为可用预览' },
        schedule_activation: { enabled: false, reason_code: 'monitor_disabled', message: '真实搜索总开关关闭，当前为可用预览' },
        delivery: { enabled: false, reason_code: 'monitor_disabled', message: '真实搜索总开关关闭，当前为可用预览' },
        mtop: { enabled: false, reason_code: 'monitor_disabled', message: 'MTop 适配器开关关闭' },
      },
      data: {
        config_ready: { available: false, label: '待完善配置', detail: '没有绑定可用账号的完整任务', badge_state: 'missing' },
        last_real_search: { available: false, label: '从未验证', detail: '没有真实搜索记录', badge_state: 'missing' },
        last_scheduled_run: { available: false, label: '从未运行', detail: '没有定时运行记录', badge_state: 'missing' },
        last_ai_decision: { available: false, label: '从未判断', detail: '没有 AI 过滤决策', badge_state: 'missing' },
        last_real_delivery: { available: false, label: '从未确认', detail: '没有真实通知投递', badge_state: 'missing' },
      },
    } as any);
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([]);
    vi.mocked(getSkillMonitorResults).mockResolvedValue([]);
    vi.mocked(getSkillOpsHealth).mockResolvedValue({ api: 'ok', database: { path: '', exists: true, writable: true }, cookie_manager: 'ready', accounts: { total: 0, enabled: 0, listening: 0, listener_state: 'not_required' }, ai: { global_configured: false, enabled_accounts: 0, ready_accounts: 0, model: '' }, skills: { monitor_tasks: 0, monitor_results: 0, logs: 0 }, recent_logs: [] } as any);
    vi.mocked(getSkillBrowserStatus).mockResolvedValue({ status: 'ready', playwright_importable: true, playwright_launchable: true, active_cookie_tasks: 0, account_count: 0, stale: false, checking: false } as any);
    vi.mocked(getSkillDeliveryDiagnostics).mockResolvedValue({ cards_total: 0, delivery_rules_total: 0, pending_orders_sample: 0, auto_delivery_ready: false, recommendations: [] } as any);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads the continuous monitor and diagnostics workflow without segmented tabs', async () => {
    render(<SkillCenter />);

    await screen.findByPlaceholderText('监控关键词');
    expect(getSkillMonitorTasks).toHaveBeenCalledTimes(1);
    expect(getSkillMonitorResults).toHaveBeenCalledTimes(1);
    expect(getSkillOpsHealth).toHaveBeenCalledTimes(1);

    expect(screen.getByPlaceholderText(/AI 商品过滤要求/)).toBeInTheDocument();
    // “定时运行”既是能力卡标题又是表单开关文案，用 switch role 精确定位表单开关
    expect(screen.getByRole('switch', { name: '定时运行' })).toBeInTheDocument();
    expect(screen.getByText('命中后通知')).toBeInTheDocument();

    expect(screen.queryByRole('navigation', { name: '页面分区' })).not.toBeInTheDocument();
    expect(await screen.findByText('核心运行正常')).toBeInTheDocument();
  });

  it('creates preview configuration with schedule and notifications forced off', async () => {
    vi.mocked(createSkillMonitorTask).mockResolvedValue({ success: true, id: 7, message: 'ok' });
    render(<SkillCenter />);

    const keyword = await screen.findByPlaceholderText('监控关键词');
    fireEvent.change(keyword, { target: { value: 'iPhone' } });
    fireEvent.change(screen.getByPlaceholderText(/AI 商品过滤要求/), {
      target: { value: '只保留低价商品' },
    });
    fireEvent.change(screen.getByRole('combobox', { name: '绑定闲鱼账号' }), { target: { value: 'account-1' } });
    expect(screen.getByRole('switch', { name: '定时运行' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: '命中后通知' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存预览配置' }));

    await waitFor(() => expect(createSkillMonitorTask).toHaveBeenCalledWith(expect.objectContaining({
      keyword: 'iPhone',
      ai_filter: '只保留低价商品',
      account_id: 'account-1',
      notify_enabled: false,
      schedule_enabled: false,
      schedule_interval_minutes: 60,
    })));
    expect(await screen.findByText('监控任务已创建，当前保持预览状态')).toBeInTheDocument();
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
      account_id: 'account-1',
      readiness: { configured: true, runnable: true, blockers: [] },
    }] as any);
    vi.mocked(getSkillCapabilities).mockResolvedValue({
      runtime_mode: 'live',
      operation_gates: {
        manual_run: { enabled: true, reason_code: '', message: '已满足运行门槛' },
        schedule_activation: { enabled: true, reason_code: '', message: '已满足运行门槛' },
        delivery: { enabled: true, reason_code: '', message: '已满足运行门槛' },
        mtop: { enabled: false, reason_code: 'mtop_disabled', message: 'MTop 适配器开关关闭' },
      },
      data: {},
    } as any);
    vi.mocked(updateSkillMonitorTask).mockResolvedValue({ success: true, message: 'ok' });
    render(<SkillCenter />);

    fireEvent.click(await screen.findByRole('button', { name: '开启定时' }));

    await waitFor(() => expect(updateSkillMonitorTask).toHaveBeenCalledWith(9, {
      schedule_enabled: true,
      schedule_interval_minutes: 30,
    }));
    expect(await screen.findByText('已开启定时监控')).toBeInTheDocument();
  });

  it('disables run and shows blockers for a not-ready task, and surfaces run evidence', async () => {
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([{
      id: 11,
      name: '未就绪任务',
      keyword: 'iPad',
      published_within_hours: 24,
      notify_enabled: false,
      enabled: true,
      schedule_enabled: false,
      schedule_interval_minutes: 30,
      readiness: { configured: false, runnable: false, blockers: ['未绑定闲鱼账号'] },
      latest_run_evidence: null,
    }, {
      id: 12,
      name: '已就绪任务',
      keyword: 'iPhone',
      published_within_hours: 24,
      notify_enabled: false,
      enabled: true,
      schedule_enabled: false,
      schedule_interval_minutes: 30,
      account_id: 'account-1',
      readiness: { configured: true, runnable: true, blockers: [] },
      latest_run_evidence: {
        status: 'success',
        trigger_type: 'manual',
        source_adapter: 'playwright',
        raw_result_count: 7,
        accepted_result_count: 3,
        observed_at: 1700000000,
      },
    }] as any);
    render(<SkillCenter />);

    await screen.findByText('未就绪任务');

    // 未就绪任务显示阻塞原因，且“运行”按钮被禁用
    expect(screen.getByText(/待完善：未绑定闲鱼账号/)).toBeInTheDocument();
    const runButtons = screen.getAllByRole('button', { name: '运行' });
    expect(runButtons[0]).toBeDisabled();
    // 已就绪任务的运行按钮可用
    expect(runButtons[1]).not.toBeDisabled();

    // 已就绪任务展示最近真实运行证据
    expect(screen.getByText(/成功 · 手动 · Playwright/)).toBeInTheDocument();
    expect(screen.getByText(/抓取 7 条 · 采纳 3 条/)).toBeInTheDocument();
    // 未运行过的任务显示占位
    expect(screen.getByText('尚无真实运行证据')).toBeInTheDocument();
  });

  it('renders capability cards with Chinese titles in fixed order, not raw backend keys', async () => {
    render(<SkillCenter />);

    await screen.findByPlaceholderText('监控关键词');

    // 修复前这里会显示 code_present 等英文 key，回归保护
    expect(screen.queryByText('code_present')).not.toBeInTheDocument();
    expect(screen.queryByText('config_ready')).not.toBeInTheDocument();

    // 能力卡专属标题（不与表单文案重名）
    ['功能模式', '任务配置', '真实搜索', 'AI 筛选', '结果通知'].forEach((title) => {
      expect(screen.getByText(title)).toBeInTheDocument();
    });
    // “定时运行”与表单开关文案重名，确认至少作为能力卡出现
    expect(screen.getAllByText('定时运行').length).toBeGreaterThan(0);
  });

  it('renders one continuous page with diagnostics fixed at the bottom', async () => {
    render(<SkillCenter />);

    await screen.findByPlaceholderText('监控关键词');
    expect(screen.queryByText('SKILL CENTER')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '页面分区' })).not.toBeInTheDocument();
    expect(await screen.findByText('运行诊断')).toBeInTheDocument();
    expect(screen.getByText('监控结果')).toBeInTheDocument();
  });

  it('collapses run diagnostics when all core checks are healthy', async () => {
    vi.mocked(getSkillOpsHealth).mockResolvedValue({
      api: 'ok',
      database: { path: '', exists: true, writable: true },
      cookie_manager: 'ready',
      accounts: { total: 1, listening: 1, listener_state: 'running' },
      ai: { global_configured: true, enabled_accounts: 1, ready_accounts: 1, model: 'x' },
      skills: { monitor_tasks: 0, monitor_results: 0, logs: 0 },
      recent_logs: [],
    } as any);
    vi.mocked(getSkillBrowserStatus).mockResolvedValue({
      playwright_importable: true,
      playwright_launchable: true,
      active_cookie_tasks: 0,
      account_count: 1,
    } as any);
    render(<SkillCenter />);

    // 核心健康 → 默认折叠：显示“核心运行正常”，三块诊断卡不渲染
    expect(await screen.findByText('核心运行正常')).toBeInTheDocument();
    expect(screen.queryByText('部署健康')).not.toBeInTheDocument();

    // 点击折叠头（摘要文字唯一）可手动展开
    fireEvent.click(screen.getByText('核心运行正常'));
    expect(await screen.findByText('部署健康')).toBeInTheDocument();
  });

  it('exposes the capability rail controls and snap semantics', async () => {
    render(<SkillCenter />);
    const track = await screen.findByRole('region', { name: '能力状态横向轨道' });
    expect(track.className).toContain('snap-x');
    expect(track.className).toContain('snap-mandatory');
    expect(screen.getByRole('button', { name: '向左查看能力' })).toHaveClass('h-11', 'w-11');
    expect(screen.getByRole('button', { name: '向右查看能力' })).toHaveClass('h-11', 'w-11');
    fireEvent.keyDown(track, { key: 'End' });
    expect(track).toHaveAttribute('tabindex', '0');
  });

  it('keeps an existing task form collapsed and opens the shared editor for completion', async () => {
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([{
      id: 31, name: '旧任务', keyword: 'MacBook', published_within_hours: 24, notify_enabled: false,
      enabled: true, schedule_enabled: false, schedule_interval_minutes: 60, account_id: '',
      readiness: { configured: false, runnable: false, blockers: ['未绑定闲鱼账号'] },
      latest_run_evidence: null,
    }] as any);
    render(<SkillCenter />);

    await screen.findByText('旧任务');
    expect(screen.queryByPlaceholderText('监控关键词')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '完善配置' }));
    expect(await screen.findByDisplayValue('MacBook')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存任务配置' })).toBeInTheDocument();
  });

  it('labels legacy success without evidence as unverified history', async () => {
    vi.mocked(getSkillMonitorTasks).mockResolvedValue([{
      id: 32, name: '历史任务', keyword: 'iPad', published_within_hours: 24, notify_enabled: false,
      enabled: true, schedule_enabled: false, schedule_interval_minutes: 60, account_id: 'account-1',
      last_status: 'success', readiness: { configured: true, runnable: false, blockers: ['真实搜索总开关关闭，当前为可用预览'] },
      latest_run_evidence: null,
    }] as any);
    render(<SkillCenter />);
    expect(await screen.findByText('历史状态，尚未验证')).toBeInTheDocument();
    expect(screen.queryByText('成功')).not.toBeInTheDocument();
  });
});
