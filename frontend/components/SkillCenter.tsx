import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  CheckCircle2,
  Cpu,
  Eye,
  Loader2,
  Play,
  Radar,
  Save,
  Search,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
} from 'lucide-react';
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
  runSkillMonitorTask,
  testSkillAgentReply,
  updateSkillAgentPrompt,
  updateSkillMonitorTask,
} from '../services/api';
import {
  AccountDetail,
  Item,
  SkillAgentPrompt,
  SkillBrowserStatus,
  SkillDeliveryDiagnostics,
  SkillMonitorResult,
  SkillMonitorTask,
  SkillOpsHealth,
  SkillCapability,
} from '../types';
import { InlineNotice, StatusBadge } from './ui/StatusControls';

type SkillTab = 'monitor' | 'agent' | 'ops';

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

const promptOrder: SkillAgentPrompt['prompt_type'][] = ['price', 'tech', 'default'];
const capabilityTitles: Record<string, string> = {
  manual_monitor: '手动真实监控',
  scheduled_monitor: '定时调度',
  ai_filter: 'AI 商品过滤',
  notifications: '监控结果通知',
  expert_live_reply: '专家客服策略',
};

const SkillCenter: React.FC = () => {
  const [activeSkill, setActiveSkill] = useState<SkillTab>('monitor');
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [tasks, setTasks] = useState<SkillMonitorTask[]>([]);
  const [results, setResults] = useState<SkillMonitorResult[]>([]);
  const [prompts, setPrompts] = useState<SkillAgentPrompt[]>([]);
  const [opsHealth, setOpsHealth] = useState<SkillOpsHealth | null>(null);
  const [browserStatus, setBrowserStatus] = useState<SkillBrowserStatus | null>(null);
  const [deliveryDiagnostics, setDeliveryDiagnostics] = useState<SkillDeliveryDiagnostics | null>(null);
  const [capabilities, setCapabilities] = useState<Record<string, SkillCapability>>({});
  const [testItems, setTestItems] = useState<Item[]>([]);
  const [taskForm, setTaskForm] = useState(emptyTaskForm);
  const [testMessage, setTestMessage] = useState('这个还能便宜一点吗？');
  const [testAccountId, setTestAccountId] = useState('');
  const [testItemId, setTestItemId] = useState('');
  const [testReply, setTestReply] = useState<{
    intent: string;
    expert: string;
    reply: string;
    cookie_id?: string;
    model_name?: string;
    base_url?: string;
    is_real_ai?: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [runningTaskId, setRunningTaskId] = useState<number | null>(null);
  const [statusText, setStatusText] = useState('');
  const [loadedTabs, setLoadedTabs] = useState<Record<SkillTab, boolean>>({
    monitor: false,
    agent: false,
    ops: false,
  });

  const promptMap = useMemo(() => {
    return prompts.reduce<Record<string, SkillAgentPrompt>>((acc, prompt) => {
      acc[prompt.prompt_type] = prompt;
      return acc;
    }, {});
  }, [prompts]);

  const loadMonitor = async () => {
    const [taskList, resultList] = await Promise.all([
      getSkillMonitorTasks(),
      getSkillMonitorResults(),
    ]);
    setTasks(taskList);
    setResults(resultList);
    setLoadedTabs((current) => ({ ...current, monitor: true }));
  };

  const loadAgent = async () => {
    setPrompts(await getSkillAgentPrompts());
    setLoadedTabs((current) => ({ ...current, agent: true }));
  };

  const loadOps = async () => {
    const [health, browser, delivery] = await Promise.all([
      getSkillOpsHealth(),
      getSkillBrowserStatus(),
      getSkillDeliveryDiagnostics(),
    ]);
    setOpsHealth(health);
    setBrowserStatus(browser);
    setDeliveryDiagnostics(delivery);
    setLoadedTabs((current) => ({ ...current, ops: true }));
  };

  const loadAll = async () => {
    setLoading(true);
    try {
      const [accountList, capabilityList] = await Promise.all([
        getAccountDetails(),
        getSkillCapabilities(),
        loadMonitor(),
      ]);
      setAccounts(accountList);
      setCapabilities(capabilityList);
      if (!testAccountId && accountList.length > 0) {
        setTestAccountId(accountList[0].id);
      }
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  useEffect(() => {
    if (activeSkill === 'agent' && !loadedTabs.agent) {
      loadAgent().catch((error) => setStatusText(error instanceof Error ? error.message : '专家配置加载失败'));
    }
    if (activeSkill === 'ops' && !loadedTabs.ops) {
      loadOps().catch((error) => setStatusText(error instanceof Error ? error.message : '诊断数据加载失败'));
    }
  }, [activeSkill, loadedTabs.agent, loadedTabs.ops]);

  useEffect(() => {
    if (!testAccountId) {
      setTestItems([]);
      setTestItemId('');
      return;
    }
    getItemsByCookie(testAccountId)
      .then((items) => {
        setTestItems(items);
        setTestItemId((current) => items.some((item) => String(item.item_id) === current)
          ? current
          : String(items[0]?.item_id || ''));
      })
      .catch((error) => {
        setTestItems([]);
        setTestItemId('');
        setStatusText(error instanceof Error ? error.message : '真实商品加载失败');
      });
  }, [testAccountId]);

  const handleCreateTask = async () => {
    if (!taskForm.keyword.trim()) {
      setStatusText('请输入监控关键词');
      return;
    }

    setLoading(true);
    try {
      await createSkillMonitorTask({
        name: taskForm.name || `${taskForm.keyword} 监控`,
        keyword: taskForm.keyword,
        min_price: taskForm.min_price ? Number(taskForm.min_price) : null,
        max_price: taskForm.max_price ? Number(taskForm.max_price) : null,
        region: taskForm.region,
        published_within_hours: Number(taskForm.published_within_hours) || 24,
        ai_filter: taskForm.ai_filter,
        notify_enabled: taskForm.notify_enabled,
        account_id: taskForm.account_id,
        enabled: true,
        schedule_enabled: taskForm.schedule_enabled,
        schedule_interval_minutes: Number(taskForm.schedule_interval_minutes) || 60,
      });
      setTaskForm(emptyTaskForm);
      setStatusText('监控任务已创建');
      await loadMonitor();
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  const handleRunTask = async (taskId: number) => {
    setRunningTaskId(taskId);
    try {
      const result = await runSkillMonitorTask(taskId);
      setStatusText(result.message || `真实监控完成，命中 ${result.created_count || 0} 条`);
      await loadMonitor();
      if (loadedTabs.ops) {
        await loadOps();
      }
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
      setStatusText(!task.schedule_enabled ? '已开启定时监控' : '已关闭定时监控');
      await loadMonitor();
      setCapabilities(await getSkillCapabilities());
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '更新定时状态失败');
    } finally {
      setLoading(false);
    }
  };

  const handlePromptChange = (promptType: SkillAgentPrompt['prompt_type'], content: string) => {
    setPrompts((current) =>
      current.map((prompt) =>
        prompt.prompt_type === promptType ? { ...prompt, content } : prompt
      )
    );
  };

  const handleSavePrompt = async (promptType: SkillAgentPrompt['prompt_type']) => {
    const prompt = promptMap[promptType];
    if (!prompt) return;

    setLoading(true);
    try {
      await updateSkillAgentPrompt(prompt);
      setStatusText('专家提示词已保存');
      await loadAgent();
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '保存失败');
    } finally {
      setLoading(false);
    }
  };

  const handleTestReply = async () => {
    if (!testMessage.trim()) {
      setStatusText('请输入测试消息');
      return;
    }
    if (!testAccountId || !testItemId) {
      setStatusText('请先选择真实账号和真实商品');
      return;
    }

    setLoading(true);
    try {
      const result = await testSkillAgentReply({
        message: testMessage,
        cookie_id: testAccountId,
        item_id: testItemId,
      });
      setTestReply({
        intent: result.intent,
        expert: result.expert,
        reply: result.reply,
        cookie_id: result.cookie_id,
        model_name: result.model_name,
        base_url: result.base_url,
        is_real_ai: result.is_real_ai,
      });
      setStatusText(result.is_real_ai ? '真实AI回复已生成' : '测试回复已生成');
      await loadOps();
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : '测试失败');
    } finally {
      setLoading(false);
    }
  };

  const renderMonitor = () => (
    <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-6">
      <section className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-10 h-10 rounded-xl bg-[#FFE815] flex items-center justify-center">
            <Search className="w-5 h-5 text-black" />
          </div>
          <div>
            <h3 className="text-lg font-extrabold text-gray-900">监控任务</h3>
            <p className="text-xs text-gray-500">关键词、价格、地区和账号绑定</p>
          </div>
        </div>

        <div className="space-y-4">
          <input
            value={taskForm.name}
            onChange={(event) => setTaskForm({ ...taskForm, name: event.target.value })}
            placeholder="任务名称"
            className="w-full ios-input px-4 py-3 rounded-xl"
          />
          <input
            value={taskForm.keyword}
            onChange={(event) => setTaskForm({ ...taskForm, keyword: event.target.value })}
            placeholder="监控关键词"
            className="w-full ios-input px-4 py-3 rounded-xl"
          />
          <div className="grid grid-cols-2 gap-3">
            <input
              value={taskForm.min_price}
              onChange={(event) => setTaskForm({ ...taskForm, min_price: event.target.value })}
              placeholder="最低价"
              type="number"
              className="w-full ios-input px-4 py-3 rounded-xl"
            />
            <input
              value={taskForm.max_price}
              onChange={(event) => setTaskForm({ ...taskForm, max_price: event.target.value })}
              placeholder="最高价"
              type="number"
              className="w-full ios-input px-4 py-3 rounded-xl"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              value={taskForm.region}
              onChange={(event) => setTaskForm({ ...taskForm, region: event.target.value })}
              placeholder="地区"
              className="w-full ios-input px-4 py-3 rounded-xl"
            />
            <input
              value={taskForm.published_within_hours}
              onChange={(event) => setTaskForm({ ...taskForm, published_within_hours: event.target.value })}
              placeholder="发布时间小时"
              type="number"
              min="1"
              className="w-full ios-input px-4 py-3 rounded-xl"
            />
          </div>
          <select
            value={taskForm.account_id}
            onChange={(event) => setTaskForm({ ...taskForm, account_id: event.target.value })}
            className="w-full ios-input px-4 py-3 rounded-xl"
          >
            <option value="">不绑定账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.remark || account.nickname || account.id}
              </option>
            ))}
          </select>
          <textarea
            value={taskForm.ai_filter}
            onChange={(event) => setTaskForm({ ...taskForm, ai_filter: event.target.value })}
            placeholder="AI 商品过滤要求，例如：只保留价格明显低于市场价、卖家描述可信、适合捡漏的商品"
            className="w-full ios-input px-4 py-3 rounded-xl min-h-24 resize-none"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="flex items-center justify-between gap-3 rounded-xl bg-gray-50 px-4 py-3 text-sm font-bold text-gray-700">
              <span>定时运行</span>
              <input
                type="checkbox"
                checked={taskForm.schedule_enabled}
                onChange={(event) => setTaskForm({ ...taskForm, schedule_enabled: event.target.checked })}
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-xl bg-gray-50 px-4 py-3 text-sm font-bold text-gray-700">
              <span>命中后通知</span>
              <input
                type="checkbox"
                checked={taskForm.notify_enabled}
                onChange={(event) => setTaskForm({ ...taskForm, notify_enabled: event.target.checked })}
              />
            </label>
          </div>
          {taskForm.schedule_enabled && (
            <select
              value={taskForm.schedule_interval_minutes}
              onChange={(event) => setTaskForm({ ...taskForm, schedule_interval_minutes: event.target.value })}
              className="w-full ios-input px-4 py-3 rounded-xl"
            >
              <option value="15">每 15 分钟</option>
              <option value="30">每 30 分钟</option>
              <option value="60">每 1 小时</option>
              <option value="360">每 6 小时</option>
              <option value="720">每 12 小时</option>
              <option value="1440">每 24 小时</option>
            </select>
          )}
          <InlineNotice>
            定时默认关闭，最短 15 分钟。AI 过滤会在规则命中后再判断；通知支持 Webhook、微信、钉钉、飞书、Bark 和 Telegram，并发送到全部已启用的受支持渠道。
          </InlineNotice>
          <button
            onClick={handleCreateTask}
            disabled={loading}
            className="w-full ios-btn-primary h-12 rounded-xl font-bold flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Radar className="w-4 h-4" />}
            创建监控任务
          </button>
        </div>
      </section>

      <section className="space-y-6">
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-extrabold text-gray-900">任务列表</h3>
            <span className="text-xs font-bold text-gray-500">{tasks.length} 个任务</span>
          </div>
          <div className="space-y-3">
            {tasks.map((task) => (
              <div key={task.id} className="border border-gray-100 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div className="min-w-0">
                  <div className="font-bold text-gray-900 truncate">{task.name}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    {task.keyword} · {task.region || '全国'} · {task.min_price ?? '-'}-{task.max_price ?? '-'} 元
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-bold">
                    <span className={`rounded-full px-2 py-1 ${task.schedule_enabled ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}>
                      {task.schedule_enabled ? `定时每 ${task.schedule_interval_minutes || 60} 分钟` : '定时关闭'}
                    </span>
                    <span className={`rounded-full px-2 py-1 ${task.ai_filter ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-500'}`}>
                      {task.ai_filter ? 'AI过滤开启' : 'AI过滤关闭'}
                    </span>
                    <span className={`rounded-full px-2 py-1 ${task.notify_enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      {task.notify_enabled ? '通知开启' : '通知关闭'}
                    </span>
                    {task.last_status && (
                      <span className={`rounded-full px-2 py-1 ${task.last_status === 'failed' ? 'bg-red-100 text-red-700' : task.last_status === 'running' ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-500'}`}>
                        {task.last_status}
                      </span>
                    )}
                  </div>
                  {task.next_run_at && <div className="mt-1 text-[11px] text-gray-400">下次运行：{task.next_run_at}</div>}
                  {task.last_error && <div className="mt-1 text-[11px] text-red-500">错误：{task.last_error}</div>}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => handleToggleTaskSchedule(task)}
                    disabled={loading}
                    className="px-4 py-2 rounded-xl bg-gray-100 text-gray-700 font-bold text-sm hover:bg-gray-200 transition-colors"
                  >
                    {task.schedule_enabled ? '关闭定时' : '开启定时'}
                  </button>
                  <button
                    onClick={() => handleRunTask(task.id)}
                    disabled={runningTaskId === task.id}
                    className="px-4 py-2 rounded-xl bg-black text-white font-bold text-sm flex items-center gap-2 hover:bg-gray-800 transition-colors"
                  >
                    {runningTaskId === task.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    运行
                  </button>
                </div>
              </div>
            ))}
            {tasks.length === 0 && <div className="text-sm text-gray-500 py-8 text-center">暂无监控任务</div>}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-extrabold text-gray-900">监控结果</h3>
            <span className="text-xs font-bold text-gray-500">{results.length} 条结果</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-100">
                  <th className="py-3 pr-4">商品</th>
                  <th className="py-3 pr-4">价格</th>
                  <th className="py-3 pr-4">地区</th>
                  <th className="py-3 pr-4">来源</th>
                  <th className="py-3 pr-4">AI</th>
                  <th className="py-3 pr-4">通知</th>
                  <th className="py-3 pr-4">过滤</th>
                </tr>
              </thead>
              <tbody>
                {results.map((result) => (
                  <tr key={result.id} className="border-b border-gray-50">
                    <td className="py-3 pr-4 font-bold text-gray-900">
                      <a href={result.item_url} target="_blank" rel="noreferrer" className="hover:underline">
                        {result.title}
                      </a>
                    </td>
                    <td className="py-3 pr-4">{result.price ?? '-'}</td>
                    <td className="py-3 pr-4">{result.region || '-'}</td>
                    <td className="py-3 pr-4">
                      {result.raw_data?.is_real_data ? (
                        <span className="px-2 py-1 rounded-lg bg-green-100 text-green-700 text-xs font-bold">
                          真实 · {result.raw_data?.source || 'search'}
                        </span>
                      ) : (
                        <span className="px-2 py-1 rounded-lg bg-red-100 text-red-700 text-xs font-bold">
                          非真实
                        </span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs text-gray-500">
                      {result.raw_data?.ai_filter ? (
                        <span className="px-2 py-1 rounded-lg bg-purple-100 text-purple-700 font-bold">
                          {result.ai_score} · {result.ai_reason || 'AI推荐'}
                        </span>
                      ) : '未启用'}
                    </td>
                    <td className="py-3 pr-4 text-xs text-gray-500">
                      <span className="px-2 py-1 rounded-lg bg-gray-100 font-bold">
                        {result.notify_status || 'disabled'}
                      </span>
                      {result.raw_data?.notify_error ? <div className="mt-1 text-red-500">{result.raw_data.notify_error}</div> : null}
                    </td>
                    <td className="py-3 pr-4 text-xs text-gray-500">
                      {result.raw_data?.filter_reason || '-'}
                      {result.raw_data?.publish_time ? ` · ${result.raw_data.publish_time}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {results.length === 0 && <div className="text-sm text-gray-500 py-8 text-center">暂无监控结果</div>}
          </div>
        </div>
      </section>
    </div>
  );

  const renderAgent = () => (
    <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6">
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="lg:col-span-2">
          <InlineNotice>意图由本地规则路由到议价、技术或默认专家，不会额外调用分类模型。专家策略不能覆盖当前商品详情和商品知识。</InlineNotice>
        </div>
        {promptOrder.map((promptType) => {
          const prompt = promptMap[promptType];
          return (
            <div key={promptType} className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="font-extrabold text-gray-900">{prompt?.title || promptType}</h3>
                  <p className="text-xs text-gray-500 mt-1">{promptType}</p>
                </div>
                <button
                  onClick={() => handleSavePrompt(promptType)}
                  disabled={!prompt || loading}
                  className="px-3 py-2 rounded-xl bg-gray-900 text-white text-sm font-bold flex items-center gap-2"
                >
                  <Save className="w-4 h-4" />
                  保存
                </button>
              </div>
              <textarea
                value={prompt?.content || ''}
                onChange={(event) => handlePromptChange(promptType, event.target.value)}
                className="w-full ios-input px-4 py-3 rounded-xl h-44 resize-none text-sm"
              />
            </div>
          );
        })}
      </section>

      <section className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm h-fit">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center">
            <Bot className="w-5 h-5 text-purple-600" />
          </div>
          <div>
            <h3 className="text-lg font-extrabold text-gray-900">测试回复</h3>
            <p className="text-xs text-gray-500">调用真实 AI 引擎</p>
          </div>
        </div>
        <select
          value={testAccountId}
          onChange={(event) => setTestAccountId(event.target.value)}
          className="w-full ios-input px-4 py-3 rounded-xl mb-3"
        >
          <option value="">选择真实账号</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.remark || account.nickname || account.id}
            </option>
          ))}
        </select>
        <select
          value={testItemId}
          onChange={(event) => setTestItemId(event.target.value)}
          className="w-full ios-input px-4 py-3 rounded-xl mb-3"
          disabled={!testAccountId || testItems.length === 0}
        >
          <option value="">{testAccountId ? '选择真实商品' : '请先选择账号'}</option>
          {testItems.map((item) => (
            <option key={String(item.item_id)} value={String(item.item_id)}>
              {item.item_title || item.item_id}{item.item_price ? ` · ${String(item.item_price).startsWith('¥') ? item.item_price : `¥${item.item_price}`}` : ''}
            </option>
          ))}
        </select>
        <textarea
          value={testMessage}
          onChange={(event) => setTestMessage(event.target.value)}
          className="w-full ios-input px-4 py-3 rounded-xl h-28 resize-none"
        />
        <button
          onClick={handleTestReply}
          disabled={loading || !testAccountId || !testItemId}
          className="w-full mt-4 h-12 rounded-xl bg-black text-white font-bold flex items-center justify-center gap-2"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          生成测试回复
        </button>
        {testReply && (
          <div className="mt-5 border border-gray-100 rounded-xl p-4 bg-gray-50">
            <div className="text-xs text-gray-500 mb-2">
              {testReply.expert} · {testReply.intent} · {testReply.is_real_ai ? '真实AI' : '预览'}
            </div>
            {(testReply.model_name || testReply.cookie_id) && (
              <div className="text-xs text-gray-500 mb-2">
                模型：{testReply.model_name || '-'} · 账号：{testReply.cookie_id || '-'}
              </div>
            )}
            <div className="font-bold text-gray-900 leading-relaxed">{testReply.reply}</div>
          </div>
        )}
      </section>
    </div>
  );

  const renderOps = () => (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <section className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-5">
          <ShieldCheck className="w-6 h-6 text-green-600" />
          <h3 className="text-lg font-extrabold text-gray-900">部署健康</h3>
        </div>
        <div className="space-y-3 text-sm">
          <Metric label="API" value={opsHealth?.api === 'ok' ? '可用' : opsHealth?.api || '未知'} />
          <Metric label="数据库" value={opsHealth?.database.exists ? '已连接' : '不可用'} />
          <Metric label="数据库写入" value={opsHealth?.database.writable ? '可用' : '不可用'} />
          <Metric label="账号监听管理器" value={opsHealth?.cookie_manager === 'ready' ? '已就绪' : opsHealth?.cookie_manager || '未知'} />
          <Metric label="账号监听" value={opsHealth ? `${opsHealth.accounts.listening}/${opsHealth.accounts.total} 运行中` : '未知'} />
          <Metric label="AI 全局配置" value={opsHealth?.ai.global_configured ? '已配置' : '未配置'} />
          <Metric label="AI 可用账号" value={opsHealth ? `${opsHealth.ai.ready_accounts}/${opsHealth.accounts.total}` : '未知'} />
          <Metric label="AI 模型" value={opsHealth?.ai.model || '未配置'} compact />
        </div>
      </section>

      <section className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-5">
          <Cpu className="w-6 h-6 text-blue-600" />
          <h3 className="text-lg font-extrabold text-gray-900">浏览器状态</h3>
        </div>
        <div className="space-y-3 text-sm">
          <Metric label="Playwright 驱动" value={browserStatus?.playwright_importable ? '已安装' : '不可用'} />
          <Metric label="浏览器启动" value={browserStatus?.playwright_launchable ? '验证成功' : '不可用'} />
          <Metric label="账号数" value={String(browserStatus?.account_count ?? '-')} />
          <Metric label="运行任务" value={String(browserStatus?.active_cookie_tasks ?? '-')} />
          <Metric label="浏览器内核" value={browserStatus?.browser_path || '未识别'} compact />
          {browserStatus?.playwright_error && <Metric label="启动原因" value={browserStatus.playwright_error} compact />}
        </div>
      </section>

      <section className="bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-5">
          <Wrench className="w-6 h-6 text-amber-600" />
          <h3 className="text-lg font-extrabold text-gray-900">发货诊断</h3>
        </div>
        <div className="space-y-3 text-sm">
          <Metric label="卡券" value={String(deliveryDiagnostics?.cards_total ?? '-')} />
          <Metric label="规则" value={String(deliveryDiagnostics?.delivery_rules_total ?? '-')} />
          <Metric label="待处理样本" value={String(deliveryDiagnostics?.pending_orders_sample ?? '-')} />
          <Metric label="发货就绪" value={deliveryDiagnostics?.auto_delivery_ready ? '已就绪' : '条件不足'} />
        </div>
      </section>

      <section className="xl:col-span-3 bg-white rounded-2xl border border-gray-100 p-6 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-extrabold text-gray-900">运行日志</h3>
          <button onClick={loadOps} className="px-4 py-2 rounded-xl bg-gray-100 font-bold text-sm flex items-center gap-2">
            <Activity className="w-4 h-4" />
            刷新
          </button>
        </div>
        <div className="space-y-2">
          {(opsHealth?.recent_logs || []).map((log) => (
            <div key={log.id} className="flex items-center gap-3 border border-gray-100 rounded-xl px-4 py-3 text-sm">
              <CheckCircle2 className="w-4 h-4 text-green-500" />
              <span className="font-bold text-gray-700">{log.module}</span>
              <span className="text-gray-500 flex-1">{log.message}</span>
              <span className="text-xs text-gray-400">{log.created_at}</span>
            </div>
          ))}
          {(!opsHealth?.recent_logs || opsHealth.recent_logs.length === 0) && (
            <div className="text-sm text-gray-500 py-8 text-center">暂无技能运行日志</div>
          )}
        </div>
      </section>
    </div>
  );

  return (
    <div className="space-y-6 sm:space-y-8">
      <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-black text-[#FFE815] text-xs font-extrabold mb-3">
            <SlidersHorizontal className="w-3.5 h-3.5" />
            SKILL CENTER
          </div>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">技能中心</h2>
          <p className="text-gray-500 mt-2">真实能力与暂不可用能力分开显示</p>
        </div>
        <button
          onClick={loadAll}
          disabled={loading}
          className="px-5 py-3 rounded-xl bg-white border border-gray-100 font-bold flex items-center gap-2 shadow-sm"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
          刷新状态
        </button>
      </div>

      <div className="flex max-w-full overflow-x-auto rounded-2xl bg-white border border-gray-100 p-1 shadow-sm">
        {[
          { id: 'monitor', label: '监控捡漏', icon: Radar },
          { id: 'agent', label: 'AI 专家客服', icon: Bot },
          { id: 'ops', label: '稳定增强', icon: Wrench },
        ].map((item) => {
          const Icon = item.icon;
          const selected = activeSkill === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveSkill(item.id as SkillTab)}
              className={`flex-none whitespace-nowrap px-3 sm:px-5 py-3 rounded-xl font-bold text-sm flex items-center gap-2 transition-colors ${
                selected ? 'bg-[#FFE815] text-black' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              <Icon className="w-4 h-4 shrink-0" />
              {item.label}
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
        {(Object.entries(capabilities) as [string, SkillCapability][]).map(([key, capability]) => (
          <div key={key} className="rounded-xl border border-gray-100 bg-white px-4 py-3 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-bold text-gray-900">{capabilityTitles[key] || key}</span>
              <StatusBadge state={capability.available ? 'ready' : 'missing'} label={capability.label} />
            </div>
            <p className="mt-2 text-xs leading-relaxed text-gray-500">{capability.detail}</p>
          </div>
        ))}
      </div>

      {statusText && (
        <div className="bg-white border border-gray-100 rounded-2xl px-5 py-3 text-sm font-bold text-gray-700 shadow-sm">
          {statusText}
        </div>
      )}

      {activeSkill === 'monitor' && renderMonitor()}
      {activeSkill === 'agent' && renderAgent()}
      {activeSkill === 'ops' && renderOps()}
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string; compact?: boolean }> = ({ label, value, compact }) => (
  <div className="flex items-center justify-between gap-4 border border-gray-100 rounded-xl px-4 py-3">
    <span className="text-gray-500">{label}</span>
    <span className={`font-extrabold text-gray-900 text-right ${compact ? 'max-w-[160px] truncate' : ''}`}>{value}</span>
  </div>
);

export default SkillCenter;
