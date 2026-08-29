import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, MonitorSmartphone, RefreshCw } from 'lucide-react';
import type { AdminAccountRow } from '../types';
import { getAdminAccountsOverview } from '../services/api';
import { StatusBadge } from './ui/StatusControls';

const formatEpoch = (value: number | null): string => {
  if (!value) return '—';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
};

const sessionBadge = (account: AdminAccountRow) => {
  if (account.session_expired) {
    return <StatusBadge state="error" label="已掉线" />;
  }
  if (account.refresh_state === 'manual_reauth_required') {
    return <StatusBadge state="error" label="需重新扫码" />;
  }
  if (!account.last_validated_at && !account.last_login_at) {
    return <StatusBadge state="pending" label="未校验" />;
  }
  return <StatusBadge state="ready" label="在线" />;
};

/**
 * admin 账号总览：全部代理的闲鱼账号 + 登录健康状态。
 * 只做可见性（谁掉线了、归谁管），不提供任何跨租户操作入口。
 */
const AdminAccountsPanel: React.FC = () => {
  const [accounts, setAccounts] = useState<AdminAccountRow[]>([]);
  const [expiredCount, setExpiredCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const overview = await getAdminAccountsOverview();
      setAccounts(overview.accounts);
      setExpiredCount(overview.expired_count);
    } catch (caught) {
      setError(caught instanceof Error && caught.message ? caught.message : '账号总览加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="ios-card rounded-2xl p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <MonitorSmartphone className="h-5 w-5 text-gray-700" />
          <h3 className="text-[15px] font-bold text-gray-900">账号总览</h3>
          <span className="text-xs text-gray-400">全部代理的闲鱼账号与登录健康</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">
            共 <span className="tabular-nums font-bold text-gray-900">{accounts.length}</span> 个
            {expiredCount ? (
              <span className="ml-2 text-red-600">掉线 <span className="tabular-nums font-bold">{expiredCount}</span> 个</span>
            ) : null}
          </span>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 text-xs font-bold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" /> : <RefreshCw className="h-3.5 w-3.5" />}刷新
          </button>
        </div>
      </div>

      {error ? (
        <p className="mt-4 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      ) : loading && !accounts.length ? (
        <p className="mt-4 flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />账号总览加载中...
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-100">
          <table className="min-w-[720px] w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs text-gray-500">
              <tr>
                <th className="px-3 py-2.5">闲鱼账号</th>
                <th className="px-3 py-2.5">归属代理</th>
                <th className="px-3 py-2.5">登录方式</th>
                <th className="px-3 py-2.5">监听</th>
                <th className="px-3 py-2.5">登录态</th>
                <th className="px-3 py-2.5">最近校验</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {accounts.length ? accounts.map((account) => (
                <tr key={account.cookie_id} className={account.session_expired ? 'bg-red-50/40' : undefined}>
                  <td className="px-3 py-3">
                    <p className="font-bold text-gray-900">{account.xianyu_nick || account.remark || account.cookie_id}</p>
                    <p className="text-xs text-gray-400">{account.cookie_id}</p>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-bold text-gray-800">{account.username}</span>
                    {account.user_is_active ? null : (
                      <span className="ml-2 inline-flex rounded-md bg-gray-100 px-1.5 py-0.5 text-[11px] font-bold text-gray-500">用户已停用</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-gray-700">{account.login_method_label}</td>
                  <td className="px-3 py-3">
                    <StatusBadge state={account.enabled ? 'ready' : 'pending'} label={account.enabled ? '监听中' : '已暂停'} />
                  </td>
                  <td className="px-3 py-3">{sessionBadge(account)}</td>
                  <td className="px-3 py-3 tabular-nums text-xs text-gray-500">{formatEpoch(account.last_validated_at ?? account.last_login_at)}</td>
                </tr>
              )) : (
                <tr><td colSpan={6} className="px-4 py-6 text-center text-gray-400">还没有任何闲鱼账号</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export default AdminAccountsPanel;
