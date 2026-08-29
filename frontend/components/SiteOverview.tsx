import React, { useEffect, useState } from 'react';
import { Building2, ChevronDown, Loader2, UsersRound } from 'lucide-react';
import type { AgentSummaryRow, GlobalDashboardSummary } from '../types';
import { getAdminAgentSummary, getGlobalDashboardSummary } from '../services/api';
import { PANEL_CLASS, formatCount, formatMoney } from './ui/dashboardParts';

/**
 * 全站经营视图：
 * - 默认折叠（用户主看运营概览，这里属重复信息），展开偏好记 localStorage；
 * - 折叠时不请求任何接口，首次展开才加载；
 * - 任何登录用户都能看到全站合计（大盘信心），拿不到任何分用户数据；
 * - admin 额外看到按代理分组的销量/销售额明细，停用代理不展示（重新启用即恢复）。
 */
const EXPANDED_STORAGE_KEY = 'xianyu-dashboard:site-overview-expanded';

const readStoredExpanded = (): boolean => {
  try {
    return window.localStorage.getItem(EXPANDED_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
};

const SiteOverview: React.FC<{
  isAdmin: boolean;
  startDate: string;
  endDate: string;
}> = ({ isAdmin, startDate, endDate }) => {
  const [expanded, setExpanded] = useState(readStoredExpanded);
  const [site, setSite] = useState<GlobalDashboardSummary | null>(null);
  const [agents, setAgents] = useState<AgentSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!expanded || !startDate || !endDate) return undefined;
    let cancelled = false;
    setLoading(true);
    setError('');
    const params = { range: 'custom' as const, start_date: startDate, end_date: endDate };
    const requests: Promise<void>[] = [
      getGlobalDashboardSummary(params).then((value) => {
        if (!cancelled) setSite(value);
      }),
    ];
    if (isAdmin) {
      requests.push(
        getAdminAgentSummary(params).then((value) => {
          if (!cancelled) setAgents(value.agents);
        }),
      );
    }
    Promise.all(requests)
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error && caught.message ? caught.message : '全站汇总加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [expanded, endDate, isAdmin, startDate]);

  const toggleExpanded = () => {
    setExpanded((value) => {
      const next = !value;
      try {
        window.localStorage.setItem(EXPANDED_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // 私隐模式等存储不可用时仅本次会话内生效
      }
      return next;
    });
  };

  const activeAgents = agents.filter((agent) => agent.is_active);

  return (
    <section className={`${PANEL_CLASS} ${expanded ? 'space-y-4' : ''} p-5`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={toggleExpanded}
          aria-expanded={expanded}
          className="flex min-w-0 items-center gap-2 text-left"
        >
          <Building2 className="h-5 w-5 shrink-0 text-gray-700" />
          <h3 className="text-[15px] font-bold text-gray-900">全站经营</h3>
          <span className="truncate text-xs text-gray-400">所有伙伴合计 · 所选周期</span>
          <ChevronDown className={`h-4 w-4 shrink-0 text-gray-400 transition-transform motion-reduce:transition-none ${expanded ? 'rotate-180' : ''}`} />
        </button>
        {expanded && site && !error ? (
          <div className="flex items-center gap-6">
            <div className="text-right">
              <p className="text-[11px] text-gray-400">全站销售额</p>
              <p className="tabular-nums text-xl font-extrabold text-gray-900" aria-label={`全站销售额 ¥${formatMoney(site.current.total_amount)}`}>
                ¥{formatMoney(site.current.total_amount)}
              </p>
              <p className="text-[11px] text-gray-400">上期 ¥{formatMoney(site.previous.total_amount)}</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-gray-400">全站订单</p>
              <p className="tabular-nums text-xl font-extrabold text-gray-900" aria-label={`全站订单 ${formatCount(site.current.total_orders)}`}>
                {formatCount(site.current.total_orders)}
              </p>
              <p className="text-[11px] text-gray-400">上期 {formatCount(site.previous.total_orders)}</p>
            </div>
          </div>
        ) : null}
      </div>

      {expanded ? (
        error ? (
          <div className="px-1 py-2 text-sm text-gray-500">全站经营暂不可用：{error}</div>
        ) : !site ? (
          <div className="flex items-center justify-center gap-2 px-1 py-4 text-sm text-gray-400" aria-label="全站经营加载中">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />全站经营加载中...
          </div>
        ) : isAdmin ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs font-bold text-gray-500">
              <UsersRound className="h-4 w-4" />分代理明细（仅管理员可见，停用代理不展示）
            </div>
            <div className="overflow-x-auto rounded-xl border border-gray-100">
              <table className="min-w-[480px] w-full text-left text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500">
                  <tr>
                    <th className="px-3 py-2.5">代理</th>
                    <th className="px-3 py-2.5 text-right">闲鱼账号</th>
                    <th className="px-3 py-2.5 text-right">订单数</th>
                    <th className="px-3 py-2.5 text-right">销售额</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {activeAgents.length ? activeAgents.map((agent) => (
                    <tr key={agent.user_id}>
                      <td className="px-3 py-3 font-bold text-gray-900">{agent.username}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-gray-700">{agent.account_count}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-gray-700">{formatCount(agent.total_orders)}</td>
                      <td className="px-3 py-3 text-right tabular-nums font-bold text-gray-900">¥{formatMoney(agent.total_amount)}</td>
                    </tr>
                  )) : (
                    <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">暂无启用的代理</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : null
      ) : null}
    </section>
  );
};

export default SiteOverview;
