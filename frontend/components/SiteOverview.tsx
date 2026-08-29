import React, { useEffect, useState } from 'react';
import { Building2, Loader2, UsersRound } from 'lucide-react';
import type { AgentSummaryRow, GlobalDashboardSummary } from '../types';
import { getAdminAgentSummary, getGlobalDashboardSummary } from '../services/api';
import { PANEL_CLASS, formatCount, formatMoney } from './ui/dashboardParts';
import { StatusBadge } from './ui/StatusControls';

/**
 * 全站经营视图：
 * - 任何登录用户都能看到全站合计（大盘信心），拿不到任何分用户数据；
 * - admin 额外看到按代理分组的销量/销售额明细。
 */
const SiteOverview: React.FC<{
  isAdmin: boolean;
  startDate: string;
  endDate: string;
}> = ({ isAdmin, startDate, endDate }) => {
  const [site, setSite] = useState<GlobalDashboardSummary | null>(null);
  const [agents, setAgents] = useState<AgentSummaryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!startDate || !endDate) return undefined;
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
  }, [endDate, isAdmin, startDate]);

  if (loading && !site) {
    return (
      <section className={`${PANEL_CLASS} flex items-center justify-center gap-2 px-5 py-6 text-sm text-gray-400`} aria-label="全站经营加载中">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />全站经营加载中...
      </section>
    );
  }

  if (error || !site) {
    return (
      <section className={`${PANEL_CLASS} px-5 py-4 text-sm text-gray-500`}>
        全站经营暂不可用{error ? `：${error}` : ''}
      </section>
    );
  }

  return (
    <section className={`${PANEL_CLASS} space-y-4 p-5`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Building2 className="h-5 w-5 text-gray-700" />
          <h3 className="text-[15px] font-bold text-gray-900">全站经营</h3>
          <span className="text-xs text-gray-400">所有伙伴合计 · 所选周期</span>
        </div>
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
      </div>

      {isAdmin ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs font-bold text-gray-500">
            <UsersRound className="h-4 w-4" />分代理明细（仅管理员可见）
          </div>
          <div className="overflow-x-auto rounded-xl border border-gray-100">
            <table className="min-w-[560px] w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500">
                <tr>
                  <th className="px-3 py-2.5">代理</th>
                  <th className="px-3 py-2.5">状态</th>
                  <th className="px-3 py-2.5 text-right">闲鱼账号</th>
                  <th className="px-3 py-2.5 text-right">订单数</th>
                  <th className="px-3 py-2.5 text-right">销售额</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {agents.length ? agents.map((agent) => (
                  <tr key={agent.user_id}>
                    <td className="px-3 py-3 font-bold text-gray-900">{agent.username}</td>
                    <td className="px-3 py-3"><StatusBadge state={agent.is_active ? 'ready' : 'error'} label={agent.is_active ? '启用' : '停用'} /></td>
                    <td className="px-3 py-3 text-right tabular-nums text-gray-700">{agent.account_count}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-gray-700">{formatCount(agent.total_orders)}</td>
                    <td className="px-3 py-3 text-right tabular-nums font-bold text-gray-900">¥{formatMoney(agent.total_amount)}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">暂无代理数据</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
};

export default SiteOverview;
