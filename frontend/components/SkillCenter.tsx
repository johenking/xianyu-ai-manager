import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Cpu,
  Eye,
  Loader2,
  Pencil,
  Play,
  Plus,
  Radar,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
} from 'lucide-react';
import {
  createSkillMonitorTask,
  getAccountDetails,
  getSkillBrowserStatus,
  getSkillCapabilities,
  getSkillDeliveryDiagnostics,
  getSkillMonitorResults,
  getSkillMonitorTasks,
  getSkillOpsHealth,
  runSkillMonitorTask,
  updateSkillMonitorTask,
} from '../services/api';
import type {
  AccountDetail,
  SkillBrowserStatus,
  SkillCapability,
  SkillDeliveryDiagnostics,
  SkillMonitorResult,
  SkillMonitorTask,
  SkillOperationGate,
  SkillOperationGates,
  SkillOpsHealth,
} from '../types';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { IconAction, PageHeader, WorkSurface } from './ui/ProtectedPage';

const disabledGate: SkillOperationGate = {
  enabled: false,
  reason_code: 'checking',
  message: '运行状态尚未确认',
};

const emptyOperationGates: SkillOperationGates = {
  manual_run: disabledGate,
  schedule_activation: disabledGate,
  delivery: disabledGate,
  mtop: disabledGate,
};

const emptyTaskForm = {
  name: '',
  keyword: '',
  min_price: '',
  max_price: '',
  region: '',
  published_within_hours: '24',
  account_id: '',
  ai_filter: '',
  notify_enabled: false,
  schedule_enabled: false,
  schedule_interval_minutes: '60',
};

type TaskForm = typeof emptyTaskForm;

const capabilityOrder = [
  'runtime_mode',
  'config_ready',
  'last_real_search',
  'last_scheduled_run',
  'last_ai_decision',
  'last_real_delivery',
] as const;

const capabilityTitles: Record<(typeof capabilityOrder)[number], string> = {
  runtime_mode: '功能模式',
  config_ready: '任务配置',
  last_real_search: '真实搜索',
  last_scheduled_run: '定时运行',
  last_ai_decision: 'AI 筛选',
  last_real_delivery: '结果通知',
};

const skillRunStatusLabel = (status: string): string => ({
  success: '成功',
  failed: '失败',
  running: '进行中',
  claimed: '已领取',
  pending: '等待中',
  interrupted: '已中断',
  action_required: '需处理',
}[status] || status || '未知');

const formatSkillTimestamp = (value?: number | string | null): string => {
  if (value === null || value === undefined || value === '') return '';
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 1e12 ? numeric * 1000 : numeric)
    : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const taskToForm = (task: SkillMonitorTask): TaskForm => ({
  name: task.name || '',
  keyword: task.keyword || '',
  min_price: task.min_price === null || task.min_price === undefined ? '' : String(task.min_price),
  max_price: task.max_price === null || task.max_price === undefined ? '' : String(task.max_price),
  region: task.region || '',
  published_within_hours: String(task.published_within_hours || 24),
  account_id: task.account_id || '',
  ai_filter: task.ai_filter || '',
  notify_enabled: Boolean(task.notify_enabled),
  schedule_enabled: Boolean(task.schedule_enabled),
  schedule_interval_minutes: String(task.schedule_interval_minutes || 60),
});

const SkillCenter: React.FC = () => {
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [tasks, setTasks] = useState<SkillMonitorTask[]>([]);
  const [results, setResults] = useState<SkillMonitorResult[]>([]);
  const [capabilities, setCapabilities] = useState<Record<string, SkillCapability>>({});
  const [runtimeMode, setRuntimeMode] = useState<'preview' | 'live'>('preview');
  const [operationGates, setOperationGates] = useState<SkillOperationGates>(emptyOperationGates);
  const [taskForm, setTaskForm] = useState<TaskForm>(emptyTaskForm);
  const [editingTaskId, setEditingTaskId] = useState<number | null>(null);
  const [formExpanded, setFormExpanded] = useState(true);
  const [loading, setLoading] = useState(false);
  const [runningTaskId, setRunningTaskId] = useState<number | null>(null);
  const [statusText, setStatusText] = useState('');

  const [opsHealth, setOpsHealth] = useState<SkillOpsHealth | null>(null);
  const [browserStatus, setBrowserStatus] = useState<SkillBrowserStatus | null>(null);
  const [deliveryDiagnostics, setDeliveryDiagnostics] = useState<SkillDeliveryDiagnostics | null>(null);
  const [opsLoading, setOpsLoading] = useState(true);
  const [opsLoadError, setOpsLoadError] = useState('');
  const [opsManualExpanded, setOpsManualExpanded] = useState<boolean | null>(null);

  const formInitializedRef = useRef(false);
  const keywordInputRef = useRef<HTMLInputElement>(null);
  const accountSelectRef = useRef<HTMLSelectElement>(null);
  const capabilityTrackRef = useRef<HTMLDivElement>(null);
  const dragStateRef = useRef({ active: false, startX: 0, startScrollLeft: 0 });
  const [draggingTrack, setDraggingTrack] = useState(false);
  const [trackAtStart, setTrackAtStart] = useState(true);
  const [trackAtEnd, setTrackAtEnd] = useState(false);

  const readyAccounts = useMemo(
    () => accounts.filter((account) => account.search_readiness?.ready),
    [accounts],
  );

  const displayCapabilities = useMemo(() => {
    const modeCapability: SkillCapability = runtimeMode === 'live'
      ? {
          available: true,
          state: 'live',
          badge_state: 'ready',
          label: '生产运行',
          detail: '真实搜索总开关已开启，具体动作仍受定时、通知和账号门槛约束。',
        }
      : {
          available: true,
          state: 'preview',
          badge_state: 'warning',
          label: '可用预览',
          detail: '可完善任务和账号配置；真实搜索、定时、通知与 MTop 保持关闭。',
        };
    return capabilityOrder.map((key) => ({
      key,
      title: capabilityTitles[key],
      capability: key === 'runtime_mode'
        ? modeCapability
        : capabilities[key] || {
            available: false,
            state: 'checking',
            badge_state: 'checking',
            label: '正在读取',
            detail: '正在读取真实配置与运行证据。',
          },
    }));
  }, [capabilities, runtimeMode]);

  const loadMonitor = async () => {
    const [taskList, resultList] = await Promise.all([
      getSkillMonitorTasks(),
      getSkillMonitorResults(),
    ]);
    setTasks(taskList);
    setResults(resultList);
    if (!formInitializedRef.current) {
      setFormExpanded(taskList.length === 0);
      formInitializedRef.current = true;
    }
  };

  const loadCapabilities = async () => {
    const capabilityResult = await getSkillCapabilities();
    if ('data' in capabilityResult && capabilityResult.data) {
      setCapabilities(capabilityResult.data);
      setRuntimeMode(capabilityResult.runtime_mode || 'preview');
      setOperationGates(capabilityResult.operation_gates || emptyOperationGates);
    } else {
      setCapabilities(capabilityResult as unknown as Record<string, SkillCapability>);
      setRuntimeMode('preview');
      setOperationGates(emptyOperationGates);
    }
  };

  const loadOps = async () => {
    setOpsLoading(true);
    setOpsLoadError('');
    setOpsManualExpanded(null);
    try {
      const [health, browser, delivery] = await Promise.all([
        getSkillOpsHealth(),
        getSkillBrowserStatus(),
        getSkillDeliveryDiagnostics(),
      ]);
      setOpsHealth(health);
      setBrowserStatus(browser);
      setDeliveryDiagnostics(delivery);
    } catch (error) {
      const message = error instanceof Error ? error.message : '诊断数据加载失败';
      setStatusText(message);
      setOpsLoadError(message);
    } finally {
      setOpsLoading(false);
    }
  };

  const loadAll = async () => {
    setLoading(true);
    setStatusText('');
    try {
      const [accountList] = await Promise.all([
        getAccountDetails(),
        loadMonitor(),
        loadCapabilities(),
        loadOps(),
      ]);
      setAccounts(accountList);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!browserStatus?.checking) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      timer = setTimeout(async () => {
        try {
          const next = await getSkillBrowserStatus();
          if (cancelled) return;
          setBrowserStatus(next);
          if (next.checking) void poll();
        } catch {
          if (!cancelled) void poll();
        }
      }, 750);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [browserStatus?.checking]);

  const updateTrackEdges = () => {
    const track = capabilityTrackRef.current;
    if (!track) return;
    setTrackAtStart(track.scrollLeft <= 2);
    setTrackAtEnd(track.scrollLeft + track.clientWidth >= track.scrollWidth - 2);
  };

  useEffect(() => {
    updateTrackEdges();
    window.addEventListener('resize', updateTrackEdges);
    return () => window.removeEventListener('resize', updateTrackEdges);
  }, [displayCapabilities.length]);

  const scrollCapabilityTrack = (direction: -1 | 1) => {
    const track = capabilityTrackRef.current;
    if (!track) return;
    setTrackScroll(track, track.scrollLeft + direction * 236);
  };

  const snapCapabilityTrack = () => {
    const track = capabilityTrackRef.current;
    if (!track) return;
    const target = Math.round(track.scrollLeft / 236) * 236;
    setTrackScroll(track, target);
  };

  const handleTrackKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const track = capabilityTrackRef.current;
    if (!track) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      scrollCapabilityTrack(event.key === 'ArrowLeft' ? -1 : 1);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setTrackScroll(track, event.key === 'Home' ? 0 : track.scrollWidth);
    }
  };

  const handleTrackPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    const track = capabilityTrackRef.current;
    if (!track) return;
    dragStateRef.current = { active: true, startX: event.clientX, startScrollLeft: track.scrollLeft };
    setDraggingTrack(true);
    track.setPointerCapture(event.pointerId);
  };

  const handleTrackPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStateRef.current.active) return;
    const track = capabilityTrackRef.current;
    if (!track) return;
    track.scrollLeft = dragStateRef.current.startScrollLeft - (event.clientX - dragStateRef.current.startX);
  };

  const finishTrackDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStateRef.current.active) return;
    dragStateRef.current.active = false;
    setDraggingTrack(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    snapCapabilityTrack();
  };

  const beginNewTask = () => {
    setEditingTaskId(null);
    setTaskForm(emptyTaskForm);
    setFormExpanded(true);
    setStatusText('');
    window.setTimeout(() => keywordInputRef.current?.focus(), 0);
  };

  const beginEditTask = (task: SkillMonitorTask) => {
    setEditingTaskId(task.id);
    setTaskForm(taskToForm(task));
    setFormExpanded(true);
    setStatusText('');
    window.setTimeout(() => keywordInputRef.current?.focus(), 0);
  };

  const handleSaveTask = async () => {
    if (!taskForm.keyword.trim()) {
      setStatusText('请输入监控关键词');
      setFormExpanded(true);
      keywordInputRef.current?.focus();
      return;
    }
    const selectedAccount = accounts.find((account) => account.id === taskForm.account_id);
    if (!selectedAccount || !selectedAccount.search_readiness?.ready) {
      setStatusText(selectedAccount?.search_readiness?.blockers?.[0] || '请选择身份完整的闲鱼账号');
      setFormExpanded(true);
      accountSelectRef.current?.focus();
      return;
    }

    const payload: Partial<SkillMonitorTask> = {
      name: taskForm.name.trim() || `${taskForm.keyword.trim()} 监控`,
      keyword: taskForm.keyword.trim(),
      min_price: taskForm.min_price ? Number(taskForm.min_price) : null,
      max_price: taskForm.max_price ? Number(taskForm.max_price) : null,
      region: taskForm.region.trim(),
      published_within_hours: Number(taskForm.published_within_hours) || 24,
      ai_filter: taskForm.ai_filter.trim(),
      notify_enabled: editingTaskId === null ? false : taskForm.notify_enabled,
      account_id: taskForm.account_id,
      enabled: true,
      schedule_enabled: editingTaskId === null ? false : taskForm.schedule_enabled,
      schedule_interval_minutes: Number(taskForm.schedule_interval_minutes) || 60,
    };

    setLoading(true);
    try {
      if (editingTaskId === null) {
        await createSkillMonitorTask(payload);
        setStatusText('监控任务已创建，当前保持预览状态');
      } else {
        await updateSkillMonitorTask(editingTaskId, payload);
        setStatusText('监控任务配置已保存');
      }
      setTaskForm(emptyTaskForm);
      setEditingTaskId(null);
      setFormExpanded(false);
      await Promise.all([loadMonitor(), loadCapabilities()]);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '保存失败');
      setFormExpanded(true);
    } finally {
      setLoading(false);
    }
  };

  const handleRunTask = async (taskId: number) => {
    setRunningTaskId(taskId);
    try {
      const result = await runSkillMonitorTask(taskId);
      setStatusText(result.message || `真实监控完成，命中 ${result.created_count || 0} 条`);
      await Promise.all([loadMonitor(), loadCapabilities(), loadOps()]);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '运行失败');
    } finally {
      setRunningTaskId(null);
    }
  };

  const handleToggleTaskSchedule = async (task: SkillMonitorTask) => {
    setLoading(true);
    try {
      await updateSkillMonitorTask(task.id, {
        schedule_enabled: !task.schedule_enabled,
        schedule_interval_minutes: task.schedule_interval_minutes || 60,
      });
      setStatusText(task.schedule_enabled ? '已关闭定时监控' : '已开启定时监控');
      await Promise.all([loadMonitor(), loadCapabilities()]);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '更新定时状态失败');
    } finally {
      setLoading(false);
    }
  };

  const diagnosticsResolved = Boolean(opsLoadError) || Boolean(
    !opsLoading && opsHealth && browserStatus && deliveryDiagnostics && !browserStatus.checking,
  );
  const opsCoreChecks = useMemo(() => {
    if (opsLoadError) return [{ label: '诊断接口', healthy: false, advice: opsLoadError }];
    if (!diagnosticsResolved || !opsHealth || !browserStatus) return [];
    const enabledAccounts = Number(opsHealth.accounts.enabled || 0);
    return [
      { label: 'API 服务', healthy: opsHealth.api === 'ok', advice: '后端 API 未就绪，请检查 8091 服务。' },
      { label: '数据库写入', healthy: Boolean(opsHealth.database.exists && opsHealth.database.writable), advice: '数据库写入探针失败，请检查磁盘和 SQLite 锁。' },
      { label: '账号监听管理器', healthy: opsHealth.cookie_manager === 'ready', advice: '账号监听管理器未就绪，请检查服务启动日志。' },
      {
        label: '已启用账号监听',
        healthy: enabledAccounts === 0 || opsHealth.accounts.listener_state === 'running',
        advice: `有启用账号未在监听（${opsHealth.accounts.listening}/${enabledAccounts}）。`,
      },
      {
        label: '浏览器启动',
        healthy: browserStatus.playwright_launchable,
        advice: browserStatus.playwright_error
          ? `浏览器探针失败：${browserStatus.playwright_error}`
          : 'Playwright 浏览器探针未通过。',
      },
    ];
  }, [browserStatus, diagnosticsResolved, opsHealth, opsLoadError]);
  const opsUnhealthyChecks = opsCoreChecks.filter((check) => !check.healthy);
  const opsCoreHealthy = diagnosticsResolved && opsUnhealthyChecks.length === 0;
  const opsExpanded = opsManualExpanded ?? (diagnosticsResolved && !opsCoreHealthy);

  const renderTaskEvidence = (task: SkillMonitorTask) => {
    const evidence = task.latest_run_evidence;
    if (!evidence) {
      if (task.last_status === 'success') {
        return <div className="mt-2 text-xs font-bold text-amber-700">历史状态，尚未验证</div>;
      }
      return <div className="mt-2 text-xs text-gray-400">尚无真实运行证据</div>;
    }
    const source = evidence.source_adapter === 'playwright' ? 'Playwright' : 'MTop';
    if (evidence.status === 'success' && evidence.accepted_result_count === 0) {
      return (
        <div className="mt-2 text-xs text-gray-500">
          真实搜索完成，暂无符合条件商品 · {source} · 抓取 {evidence.raw_result_count} 条
          {evidence.observed_at ? ` · ${formatSkillTimestamp(evidence.observed_at)}` : ''}
        </div>
      );
    }
    return (
      <div className="mt-2 text-xs text-gray-500">
        {skillRunStatusLabel(evidence.status)} · {evidence.trigger_type === 'scheduled' ? '定时' : '手动'} · {source}
        {' · '}抓取 {evidence.raw_result_count} 条 · 采纳 {evidence.accepted_result_count} 条
        {evidence.observed_at ? ` · ${formatSkillTimestamp(evidence.observed_at)}` : ''}
      </div>
    );
  };

  return (
    <div className="min-w-0 space-y-6 sm:space-y-8">
      <PageHeader
        icon={SlidersHorizontal}
        title="技能中心"
        description="监控配置、真实证据与运行诊断"
        actions={<IconAction icon={loading ? Loader2 : Eye} label="刷新状态" busy={loading} onClick={() => void loadAll()} disabled={loading} />}
      />

      <section aria-labelledby="capability-heading" className="min-w-0">
        <div className="mb-3 flex items-end justify-between gap-4">
          <div>
            <h2 id="capability-heading" className="text-lg font-extrabold text-gray-900">能力状态</h2>
            <p className="mt-1 text-xs text-gray-500">配置状态与真实运行证据分开显示</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              aria-label="向左查看能力"
              disabled={trackAtStart}
              onClick={() => scrollCapabilityTrack(-1)}
              className="ios-btn-secondary flex h-11 w-11 items-center justify-center rounded-xl disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
            <button
              type="button"
              aria-label="向右查看能力"
              disabled={trackAtEnd}
              onClick={() => scrollCapabilityTrack(1)}
              className="ios-btn-secondary flex h-11 w-11 items-center justify-center rounded-xl disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronRight className="h-5 w-5" />
            </button>
          </div>
        </div>
        <div
          ref={capabilityTrackRef}
          role="region"
          aria-label="能力状态横向轨道"
          tabIndex={0}
          onKeyDown={handleTrackKeyDown}
          onScroll={updateTrackEdges}
          onPointerDown={handleTrackPointerDown}
          onPointerMove={handleTrackPointerMove}
          onPointerUp={finishTrackDrag}
          onPointerCancel={finishTrackDrag}
          className={`max-w-full overflow-x-auto overscroll-x-contain pb-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400 focus-visible:ring-offset-2 motion-reduce:scroll-auto ${draggingTrack ? 'cursor-grabbing snap-none select-none' : 'cursor-grab snap-x snap-mandatory scroll-smooth'}`}
        >
          <div className="flex w-max gap-3">
            {displayCapabilities.map(({ key, title, capability }) => {
              const observed = formatSkillTimestamp(capability.observed_at as number | string | null | undefined);
              const footer = key === 'runtime_mode'
                ? (runtimeMode === 'preview' ? '生产运行开关保持关闭' : '生产运行门槛已开启')
                : (observed || '暂无运行时间证据');
              return (
                <WorkSurface key={key} as="article" className="h-[132px] w-56 shrink-0 snap-start px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="pt-1 text-sm font-bold text-gray-900">{title}</span>
                    <StatusBadge state={capability.badge_state || (capability.available ? 'ready' : 'missing')} label={capability.label} />
                  </div>
                  <p className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 text-gray-500">{capability.detail}</p>
                  <p className="mt-2 truncate border-t border-gray-100 pt-2 text-[11px] text-gray-400">{footer}</p>
                </WorkSurface>
              );
            })}
          </div>
        </div>
      </section>

      {statusText && (
        <div role="status" className="rounded-xl border border-yellow-200 bg-yellow-50 px-5 py-3 text-sm font-bold text-gray-700">
          {statusText}
        </div>
      )}

      <section aria-labelledby="monitor-heading" className="space-y-6">
        <WorkSurface className="overflow-hidden">
          <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#FFE815]">
                <Search className="h-5 w-5 text-black" />
              </div>
              <div>
                <h2 id="monitor-heading" className="text-lg font-extrabold text-gray-900">监控任务</h2>
                <p className="text-xs text-gray-500">关键词、价格、地区和所属账号</p>
              </div>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={beginNewTask} className="ios-btn-primary flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold">
                <Plus className="h-4 w-4" />新建任务
              </button>
              <button
                type="button"
                aria-expanded={formExpanded}
                aria-controls="monitor-task-form"
                onClick={() => setFormExpanded((current) => !current)}
                className="ios-btn-secondary flex h-11 w-11 items-center justify-center rounded-xl"
                aria-label={formExpanded ? '折叠任务配置' : '展开任务配置'}
              >
                {formExpanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
              </button>
            </div>
          </div>

          {formExpanded && (
            <div id="monitor-task-form" className="border-t border-gray-100 p-5 sm:p-6">
              <div className="mb-5 flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-extrabold text-gray-900">{editingTaskId === null ? '新建监控任务' : '完善 / 编辑任务'}</h3>
                  <p className="mt-1 text-xs text-gray-500">保存配置不会启动真实搜索</p>
                </div>
                <StatusBadge state={runtimeMode === 'preview' ? 'warning' : 'ready'} label={runtimeMode === 'preview' ? '可用预览' : '生产运行'} />
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <input value={taskForm.name} onChange={(event) => setTaskForm({ ...taskForm, name: event.target.value })} placeholder="任务名称" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                <input ref={keywordInputRef} value={taskForm.keyword} onChange={(event) => setTaskForm({ ...taskForm, keyword: event.target.value })} placeholder="监控关键词" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                <div className="grid grid-cols-2 gap-3">
                  <input value={taskForm.min_price} onChange={(event) => setTaskForm({ ...taskForm, min_price: event.target.value })} placeholder="最低价" type="number" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                  <input value={taskForm.max_price} onChange={(event) => setTaskForm({ ...taskForm, max_price: event.target.value })} placeholder="最高价" type="number" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <input value={taskForm.region} onChange={(event) => setTaskForm({ ...taskForm, region: event.target.value })} placeholder="地区" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                  <input value={taskForm.published_within_hours} onChange={(event) => setTaskForm({ ...taskForm, published_within_hours: event.target.value })} aria-label="发布时间小时" placeholder="发布时间小时" type="number" min="1" className="ios-input min-h-11 w-full rounded-xl px-4 py-3" />
                </div>
                <div className="lg:col-span-2">
                  <select ref={accountSelectRef} aria-label="绑定闲鱼账号" value={taskForm.account_id} onChange={(event) => setTaskForm({ ...taskForm, account_id: event.target.value })} className="ios-input min-h-11 w-full rounded-xl px-4 py-3">
                    <option value="" disabled>选择身份完整的闲鱼账号</option>
                    {accounts.map((account) => {
                      const ready = Boolean(account.search_readiness?.ready);
                      const reason = account.search_readiness?.blockers?.[0];
                      return (
                        <option key={account.id} value={account.id} disabled={!ready}>
                          {account.remark || account.nickname || account.id}{ready ? '' : `（${reason || '身份未就绪'}）`}
                        </option>
                      );
                    })}
                  </select>
                  {readyAccounts.length === 0 && <p className="mt-2 text-xs font-bold text-amber-700">当前没有身份完整的所属账号，请先在账号管理完成登录恢复。</p>}
                </div>
                <textarea value={taskForm.ai_filter} onChange={(event) => setTaskForm({ ...taskForm, ai_filter: event.target.value })} placeholder="AI 商品过滤要求，例如：只保留价格明显低于市场价、卖家描述可信、适合捡漏的商品" className="ios-input min-h-28 w-full resize-none rounded-xl px-4 py-3 lg:col-span-2" />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-xl bg-gray-50 px-4 py-3">
                  <div className="flex items-center justify-between gap-3 text-sm font-bold text-gray-700">
                    <span>定时运行</span>
                    <ToggleControl
                      label="定时运行"
                      checked={taskForm.schedule_enabled}
                      disabled={editingTaskId === null || (!taskForm.schedule_enabled && !operationGates.schedule_activation.enabled)}
                      onChange={(schedule_enabled) => setTaskForm({ ...taskForm, schedule_enabled })}
                    />
                  </div>
                  <p className="mt-2 text-xs text-amber-700">{editingTaskId === null ? '新任务保存时固定为关闭，保存后可按运行门槛启用。' : (!operationGates.schedule_activation.enabled ? operationGates.schedule_activation.message : '定时运行门槛已满足。')}</p>
                </div>
                <div className="rounded-xl bg-gray-50 px-4 py-3">
                  <div className="flex items-center justify-between gap-3 text-sm font-bold text-gray-700">
                    <span>命中后通知</span>
                    <ToggleControl
                      label="命中后通知"
                      checked={taskForm.notify_enabled}
                      disabled={editingTaskId === null || (!taskForm.notify_enabled && !operationGates.delivery.enabled)}
                      onChange={(notify_enabled) => setTaskForm({ ...taskForm, notify_enabled })}
                    />
                  </div>
                  <p className="mt-2 text-xs text-amber-700">{editingTaskId === null ? '新任务保存时固定为关闭，保存后可按运行门槛启用。' : (!operationGates.delivery.enabled ? operationGates.delivery.message : '结果通知门槛已满足。')}</p>
                </div>
              </div>
              {taskForm.schedule_enabled && (
                <select value={taskForm.schedule_interval_minutes} onChange={(event) => setTaskForm({ ...taskForm, schedule_interval_minutes: event.target.value })} className="ios-input mt-4 min-h-11 w-full rounded-xl px-4 py-3">
                  <option value="15">每 15 分钟</option><option value="30">每 30 分钟</option><option value="60">每 1 小时</option><option value="360">每 6 小时</option><option value="720">每 12 小时</option><option value="1440">每 24 小时</option>
                </select>
              )}
              <div className="mt-4"><InlineNotice>当前可以安全保存账号和筛选条件；生产运行门槛保持独立，保存不会触发搜索、通知或 MTop。</InlineNotice></div>
              <div className="mt-5 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <button type="button" onClick={() => setFormExpanded(false)} className="ios-btn-secondary min-h-11 rounded-xl px-5 text-sm font-bold">取消</button>
                <button type="button" onClick={() => void handleSaveTask()} disabled={loading || readyAccounts.length === 0} className="ios-btn-primary flex min-h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radar className="h-4 w-4" />}
                  {editingTaskId === null ? '保存预览配置' : '保存任务配置'}
                </button>
              </div>
            </div>
          )}
        </WorkSurface>

        <WorkSurface className="p-5 sm:p-6">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-extrabold text-gray-900">任务列表</h3>
            <span className="text-xs font-bold text-gray-500">{tasks.length} 个任务</span>
          </div>
          <div className="space-y-3">
            {tasks.map((task) => {
              const configured = Boolean(task.readiness?.configured);
              const runBlocked = task.readiness?.runnable === false;
              const scheduleBlocked = !task.schedule_enabled && !operationGates.schedule_activation.enabled;
              return (
                <article key={task.id} className="rounded-xl border border-gray-100 p-4">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="truncate font-bold text-gray-900">{task.name}</h4>
                        {!configured && <StatusBadge state="warning" label="待完善配置" />}
                      </div>
                      <p className="mt-1 text-xs text-gray-500">{task.keyword || '未填写关键词'} · {task.region || '全国'} · {task.min_price ?? '-'}–{task.max_price ?? '-'} 元</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-bold">
                        <span className={`rounded-md px-2 py-1 ${task.schedule_enabled ? 'bg-yellow-100 text-yellow-800' : 'bg-gray-100 text-gray-500'}`}>{task.schedule_enabled ? `定时每 ${task.schedule_interval_minutes || 60} 分钟` : '定时关闭'}</span>
                        <span className={`rounded-md px-2 py-1 ${task.ai_filter ? 'bg-yellow-100 text-yellow-800' : 'bg-gray-100 text-gray-500'}`}>{task.ai_filter ? 'AI 筛选已配置' : 'AI 筛选未配置'}</span>
                        <span className={`rounded-md px-2 py-1 ${task.notify_enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>{task.notify_enabled ? '通知已配置' : '通知关闭'}</span>
                      </div>
                      {!configured && task.readiness?.blockers?.length ? <p className="mt-2 text-xs font-bold text-amber-700">待完善：{task.readiness.blockers.join('；')}</p> : null}
                      {configured && runBlocked && task.readiness?.blockers?.length ? <p className="mt-2 text-xs text-amber-700">真实运行门槛：{task.readiness.blockers.join('；')}</p> : null}
                      {renderTaskEvidence(task)}
                      {task.last_error && <p className="mt-2 text-xs text-red-600">最近错误：{task.last_error}</p>}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={() => beginEditTask(task)} className="ios-btn-secondary flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold">
                        <Pencil className="h-4 w-4" />{configured ? '编辑配置' : '完善配置'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleToggleTaskSchedule(task)}
                        disabled={loading || !configured || scheduleBlocked}
                        title={scheduleBlocked ? operationGates.schedule_activation.message : undefined}
                        className="ios-btn-secondary min-h-11 rounded-xl px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {task.schedule_enabled ? '关闭定时' : '开启定时'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleRunTask(task.id)}
                        disabled={runningTaskId === task.id || runBlocked || !configured}
                        title={runBlocked ? task.readiness?.blockers.join('；') : undefined}
                        className="ios-btn-primary flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {runningTaskId === task.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}运行
                      </button>
                    </div>
                  </div>
                  {scheduleBlocked && configured && <p className="mt-3 border-t border-gray-100 pt-3 text-xs text-amber-700">定时：{operationGates.schedule_activation.message}</p>}
                </article>
              );
            })}
            {tasks.length === 0 && <div className="py-10 text-center text-sm text-gray-500">暂无监控任务。可先保存一份不会启动真实搜索的预览配置。</div>}
          </div>
        </WorkSurface>

        <WorkSurface className="p-5 sm:p-6">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-extrabold text-gray-900">监控结果</h3>
            <span className="text-xs font-bold text-gray-500">{results.length} 条结果</span>
          </div>
          <div className="max-w-full overflow-x-auto overscroll-x-contain">
            <table className="min-w-[760px] w-full text-sm">
              <thead><tr className="border-b border-gray-100 text-left text-gray-500"><th className="py-3 pr-4">商品</th><th className="py-3 pr-4">价格</th><th className="py-3 pr-4">地区</th><th className="py-3 pr-4">来源</th><th className="py-3 pr-4">AI</th><th className="py-3 pr-4">通知</th><th className="py-3 pr-4">过滤</th></tr></thead>
              <tbody>
                {results.map((result) => (
                  <tr key={result.id} className="border-b border-gray-50">
                    <td className="py-3 pr-4 font-bold text-gray-900"><a href={result.item_url} target="_blank" rel="noreferrer" className="hover:underline">{result.title}</a></td>
                    <td className="py-3 pr-4">{result.price ?? '-'}</td><td className="py-3 pr-4">{result.region || '-'}</td>
                    <td className="py-3 pr-4">{result.raw_data?.is_real_data ? <span className="rounded-lg bg-green-100 px-2 py-1 text-xs font-bold text-green-700">真实 · {result.raw_data.source || 'search'}</span> : <span className="rounded-lg bg-red-100 px-2 py-1 text-xs font-bold text-red-700">非真实</span>}</td>
                    <td className="py-3 pr-4 text-xs text-gray-500">{result.raw_data?.ai_filter ? <span className="rounded-lg bg-yellow-100 px-2 py-1 font-bold text-yellow-800">{result.ai_score} · {result.ai_reason || 'AI 推荐'}</span> : '未启用'}</td>
                    <td className="py-3 pr-4 text-xs text-gray-500"><span className="rounded-lg bg-gray-100 px-2 py-1 font-bold">{result.notify_status || 'disabled'}</span></td>
                    <td className="py-3 pr-4 text-xs text-gray-500">{result.raw_data?.filter_reason || '-'}{result.raw_data?.publish_time ? ` · ${result.raw_data.publish_time}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {results.length === 0 && <div className="py-10 text-center text-sm text-gray-500">暂无真实监控结果。预览模式不会生成模拟结果。</div>}
          </div>
        </WorkSurface>
      </section>

      <section aria-labelledby="ops-heading" className="space-y-4">
        <WorkSurface className="p-5 sm:p-6">
          <button type="button" aria-expanded={opsExpanded} onClick={() => setOpsManualExpanded(!opsExpanded)} className="flex min-h-11 w-full items-center justify-between gap-3 text-left">
            <div className="flex items-center gap-3">
              {!diagnosticsResolved ? <Loader2 className="h-6 w-6 animate-spin text-blue-600" /> : opsCoreHealthy ? <ShieldCheck className="h-6 w-6 text-green-600" /> : <AlertTriangle className="h-6 w-6 text-amber-600" />}
              <div>
                <h2 id="ops-heading" className="text-lg font-extrabold text-gray-900">运行诊断</h2>
                <p className="text-xs font-bold text-gray-500">{!diagnosticsResolved ? '正在检查核心运行状态…' : opsCoreHealthy ? '核心运行正常' : `${opsUnhealthyChecks.length} 项核心检查需处理`}</p>
              </div>
            </div>
            {opsExpanded ? <ChevronUp className="h-5 w-5 text-gray-400" /> : <ChevronDown className="h-5 w-5 text-gray-400" />}
          </button>
          {diagnosticsResolved && !opsCoreHealthy && (
            <ul className="mt-4 space-y-2">{opsUnhealthyChecks.map((check) => <li key={check.label} className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span><strong>{check.label}：</strong>{check.advice}</span></li>)}</ul>
          )}
        </WorkSurface>

        {opsExpanded && diagnosticsResolved && (
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
            <WorkSurface className="p-5 sm:p-6"><div className="mb-5 flex items-center gap-3"><ShieldCheck className="h-6 w-6 text-green-600" /><h3 className="text-lg font-extrabold text-gray-900">部署健康</h3></div><div className="space-y-3 text-sm"><Metric label="API" value={opsHealth?.api === 'ok' ? '可用' : opsHealth?.api || '未知'} /><Metric label="数据库" value={opsHealth?.database.exists ? '已连接' : '不可用'} /><Metric label="数据库写入" value={opsHealth?.database.writable ? '回滚探针通过' : '不可用'} /><Metric label="监听管理器" value={opsHealth?.cookie_manager === 'ready' ? '已就绪' : '未就绪'} /><Metric label="启用账号监听" value={opsHealth?.accounts ? `${opsHealth.accounts.listening}/${opsHealth.accounts.enabled || 0}` : '-'} /></div></WorkSurface>
            <WorkSurface className="p-5 sm:p-6"><div className="mb-5 flex items-center gap-3"><Cpu className="h-6 w-6 text-gray-700" /><h3 className="text-lg font-extrabold text-gray-900">浏览器状态</h3></div><div className="space-y-3 text-sm"><Metric label="Playwright 驱动" value={browserStatus?.playwright_importable ? '已安装' : '不可用'} /><Metric label="浏览器启动" value={browserStatus?.playwright_launchable ? '验证成功' : '不可用'} /><Metric label="探针时间" value={formatSkillTimestamp(browserStatus?.observed_at) || '-'} compact /><Metric label="账号数" value={String(browserStatus?.account_count ?? '-')} /><Metric label="运行任务" value={String(browserStatus?.active_cookie_tasks ?? '-')} />{browserStatus?.playwright_error ? <Metric label="启动原因" value={browserStatus.playwright_error} compact /> : null}</div></WorkSurface>
            <WorkSurface className="p-5 sm:p-6"><div className="mb-5 flex items-center gap-3"><Wrench className="h-6 w-6 text-amber-600" /><h3 className="text-lg font-extrabold text-gray-900">信息项</h3></div><div className="space-y-3 text-sm"><Metric label="AI 全局配置" value={opsHealth?.ai.global_configured ? '已配置' : '未使用'} /><Metric label="AI 可用账号" value={String(opsHealth?.ai.ready_accounts ?? '-')} /><Metric label="自动发货" value={deliveryDiagnostics?.auto_delivery_ready ? '已配置' : '未使用'} /><Metric label="卡券 / 规则" value={`${deliveryDiagnostics?.cards_total ?? 0} / ${deliveryDiagnostics?.delivery_rules_total ?? 0}`} /></div></WorkSurface>
            <WorkSurface className="p-5 sm:p-6 xl:col-span-3"><div className="mb-4 flex items-center justify-between"><h3 className="text-lg font-extrabold text-gray-900">运行日志</h3><button type="button" onClick={() => void loadOps()} className="ios-btn-secondary flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold"><Activity className="h-4 w-4" />刷新</button></div><div className="space-y-2">{(opsHealth?.recent_logs || []).map((log) => <div key={log.id} className="flex flex-col gap-1 rounded-xl border border-gray-100 px-4 py-3 text-sm sm:flex-row sm:items-center sm:gap-3"><CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" /><strong className="text-gray-700">{log.module}</strong><span className="min-w-0 flex-1 text-gray-500">{log.message}</span><span className="text-xs text-gray-400">{log.created_at}</span></div>)}{!opsHealth?.recent_logs?.length && <div className="py-8 text-center text-sm text-gray-500">暂无技能运行日志</div>}</div></WorkSurface>
          </div>
        )}
      </section>
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string; compact?: boolean }> = ({ label, value, compact }) => (
  <div className="flex items-center justify-between gap-4 rounded-xl border border-gray-100 px-4 py-3"><span className="text-gray-500">{label}</span><span className={`text-right font-extrabold text-gray-900 ${compact ? 'max-w-[180px] truncate' : ''}`}>{value}</span></div>
);

const setTrackScroll = (track: HTMLDivElement, left: number) => {
  if (typeof track.scrollTo === 'function') {
    const reduceMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    track.scrollTo({ left, behavior: reduceMotion ? 'auto' : 'smooth' });
  } else {
    track.scrollLeft = left;
  }
};

export default SkillCenter;
