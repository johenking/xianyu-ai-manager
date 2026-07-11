import React, { useCallback, useEffect, useState } from 'react';
import {
  Clipboard,
  Loader2,
  MailCheck,
  RefreshCw,
  ShieldCheck,
  TicketCheck,
  UserCheck,
  XCircle,
} from 'lucide-react';
import {
  createRegistrationInvites,
  getRegistrationAdminStatus,
  listRegistrationInvites,
  listRegistrationUsers,
  revokeRegistrationInvite,
  setRegistrationEnabled,
  setRegistrationUserActive,
} from '../services/api';
import type {
  RegistrationAdminStatus,
  RegistrationInvite,
  RegistrationInviteStatus,
  RegistrationUser,
} from '../types';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';

const statusLabels: Record<RegistrationInviteStatus, string> = {
  active: '有效',
  used: '已使用',
  expired: '已过期',
  revoked: '已吊销',
};

const statusBadgeState: Record<RegistrationInviteStatus, string> = {
  active: 'ready',
  used: 'saved',
  expired: 'warning',
  revoked: 'error',
};

const formatDate = (value: string | number | null | undefined): string => {
  if (value === null || value === undefined || value === '') return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
};

const getErrorMessage = (error: unknown, fallback: string) => (
  error instanceof Error && error.message ? error.message : fallback
);

const RegistrationManagement: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const [status, setStatus] = useState<RegistrationAdminStatus | null>(null);
  const [invites, setInvites] = useState<RegistrationInvite[]>([]);
  const [users, setUsers] = useState<RegistrationUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState('');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [count, setCount] = useState(1);
  const [validDays, setValidDays] = useState(7);
  const [note, setNote] = useState('');
  const [generated, setGenerated] = useState<RegistrationInvite[]>([]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [statusResponse, inviteResponse, userResponse] = await Promise.all([
        getRegistrationAdminStatus(),
        listRegistrationInvites(),
        listRegistrationUsers(),
      ]);
      setStatus(statusResponse);
      setInvites(inviteResponse.invites);
      setUsers(userResponse.users);
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '注册管理加载失败') });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const createInvites = async () => {
    setAction('create');
    setNotice(null);
    setGenerated([]);
    try {
      const response = await createRegistrationInvites({ count, valid_days: validDays, note: note.trim() });
      setGenerated(response.invites);
      setNotice({ tone: 'success', text: response.message });
      await load(true);
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '邀请码生成失败') });
    } finally {
      setAction('');
    }
  };

  const revokeInvite = async (invite: RegistrationInvite) => {
    setAction(`invite-${invite.id}`);
    setNotice(null);
    try {
      const response = await revokeRegistrationInvite(invite.id);
      setInvites((current) => current.map((item) => item.id === invite.id ? response.invite : item));
      setNotice({ tone: 'success', text: `邀请码 ${invite.hint} 已吊销` });
      await load(true);
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '邀请码吊销失败') });
    } finally {
      setAction('');
    }
  };

  const updateUser = async (user: RegistrationUser, isActive: boolean) => {
    setAction(`user-${user.id}`);
    setNotice(null);
    try {
      const response = await setRegistrationUserActive(user.id, isActive);
      setUsers((current) => current.map((item) => item.id === user.id ? { ...item, ...response.user } : item));
      setNotice({ tone: 'success', text: `${user.username} 已${isActive ? '启用' : '停用'}` });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '用户状态更新失败') });
    } finally {
      setAction('');
    }
  };

  const updateEnabled = async (enabled: boolean) => {
    setAction('registration');
    setNotice(null);
    try {
      const response = await setRegistrationEnabled(enabled);
      setStatus((current) => current ? {
        ...current,
        registration: { ...current.registration, enabled: response.enabled, requested: response.enabled },
      } : current);
      setNotice({ tone: 'success', text: response.message });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '注册开关更新失败') });
    } finally {
      setAction('');
    }
  };

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setNotice({ tone: 'success', text: '邀请码已复制' });
    } catch {
      setNotice({ tone: 'error', text: '浏览器未允许复制，请手动选择邀请码' });
    }
  };

  return (
    <section className="space-y-5 border-t border-gray-200 pt-7" aria-labelledby="registration-management-title">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-gray-700" /><h3 id="registration-management-title" className="text-lg font-extrabold text-gray-950">注册管理</h3></div>
          <p className="mt-1 text-sm text-gray-500">SMTP、单次邀请码和普通用户状态</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-sm font-bold text-gray-700 hover:bg-gray-50 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新
        </button>
      </div>

      {notice ? <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice> : null}
      {loading && !status ? <div className="flex h-24 items-center justify-center text-sm text-gray-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载注册状态...</div> : null}

      {status ? <>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3"><span className="text-sm font-bold text-gray-900">邀请注册</span><StatusBadge state={status.registration.enabled ? 'ready' : 'warning'} label={status.registration.enabled ? '已开放' : '已关闭'} /></div>
            <div className="mt-4 flex items-center justify-between gap-4"><span className="text-xs text-gray-500">协议 {status.registration.terms_version}</span><ToggleControl checked={status.registration.enabled} onChange={(value) => void updateEnabled(value)} label="开放邀请注册" disabled={action === 'registration' || (!status.registration.enabled && !status.registration.ready)} /></div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3"><span className="inline-flex items-center gap-2 text-sm font-bold text-gray-900"><MailCheck className="h-4 w-4" />SMTP</span><StatusBadge state={status.smtp.verified ? 'ready' : 'missing'} label={status.smtp.verified ? '已验证' : status.smtp.configured ? '待验证' : '未配置'} /></div>
            <p className="mt-4 text-xs text-gray-500">{status.smtp.support_email || '未设置支持邮箱'}</p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3"><span className="inline-flex items-center gap-2 text-sm font-bold text-gray-900"><TicketCheck className="h-4 w-4" />有效邀请码</span><span className="text-xl font-extrabold text-gray-950">{status.invites.active}</span></div>
            <p className="mt-4 text-xs text-gray-500">已用 {status.invites.used} · 过期 {status.invites.expired} · 吊销 {status.invites.revoked}</p>
          </div>
        </div>

        {!status.registration.ready ? <InlineNotice>开放注册前请先验证 SMTP 并保留至少一个有效邀请码。</InlineNotice> : null}

        <div className="space-y-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <div><h4 className="font-extrabold text-gray-950">生成单次邀请码</h4><p className="mt-1 text-xs text-gray-500">原始邀请码不会再次出现在列表中，请在本次结果中妥善分发。</p></div>
          <div className="grid gap-3 sm:grid-cols-[120px_120px_minmax(0,1fr)_auto] sm:items-end">
            <label className="text-sm font-bold text-gray-800">生成数量<input aria-label="生成数量" type="number" min={1} max={20} value={count} onChange={(event) => setCount(Number(event.target.value))} className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 font-normal outline-none focus:border-yellow-400" /></label>
            <label className="text-sm font-bold text-gray-800">有效天数<input aria-label="有效天数" type="number" min={1} max={365} value={validDays} onChange={(event) => setValidDays(Number(event.target.value))} className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 font-normal outline-none focus:border-yellow-400" /></label>
            <label className="text-sm font-bold text-gray-800">邀请码备注<input aria-label="邀请码备注" value={note} onChange={(event) => setNote(event.target.value)} maxLength={200} placeholder="例如：第一批内测" className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 font-normal outline-none focus:border-yellow-400" /></label>
            <button type="button" onClick={() => void createInvites()} disabled={action === 'create' || count < 1 || count > 20 || validDays < 1 || validDays > 365} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-gray-900 px-4 text-sm font-bold text-white hover:bg-black disabled:opacity-50">{action === 'create' ? <Loader2 className="h-4 w-4 animate-spin" /> : <TicketCheck className="h-4 w-4" />}生成邀请码</button>
          </div>
          {generated.length > 0 ? <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <p className="text-xs font-bold text-amber-900">请立即保存以下邀请码</p>
            {generated.map((invite) => invite.code ? <div key={invite.id} className="flex items-center gap-2"><code className="min-w-0 flex-1 overflow-x-auto rounded-md bg-white px-3 py-2 text-sm font-bold text-gray-900">{invite.code}</code><button type="button" title="复制邀请码" aria-label={`复制邀请码 ${invite.hint}`} onClick={() => void copyCode(invite.code!)} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-gray-600 hover:bg-white"><Clipboard className="h-4 w-4" /></button></div> : null)}
          </div> : null}
        </div>

        <div className="space-y-3">
          <h4 className="font-extrabold text-gray-950">邀请码记录</h4>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500"><tr><th className="px-3 py-2.5">标识</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5">备注</th><th className="px-3 py-2.5">到期时间</th><th className="px-3 py-2.5 text-right">操作</th></tr></thead>
              <tbody className="divide-y divide-gray-100 bg-white">{invites.length ? invites.map((invite) => <tr key={invite.id}><td className="whitespace-nowrap px-3 py-3 font-mono text-xs font-bold">{invite.hint}</td><td className="px-3 py-3"><StatusBadge state={statusBadgeState[invite.status]} label={statusLabels[invite.status]} /></td><td className="max-w-52 truncate px-3 py-3 text-gray-600">{invite.note || '—'}</td><td className="whitespace-nowrap px-3 py-3 text-xs text-gray-500">{formatDate(invite.expires_at)}</td><td className="px-3 py-3 text-right">{invite.status === 'active' ? <button type="button" aria-label={`吊销邀请码 ${invite.hint}`} title="吊销邀请码" onClick={() => void revokeInvite(invite)} disabled={action === `invite-${invite.id}`} className="inline-flex h-8 w-8 items-center justify-center rounded-md text-red-600 hover:bg-red-50 disabled:opacity-50">{action === `invite-${invite.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}</button> : '—'}</td></tr>) : <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">暂无邀请码</td></tr>}</tbody>
            </table>
          </div>
        </div>

        <div className="space-y-3">
          <h4 className="font-extrabold text-gray-950">最近注册用户</h4>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500"><tr><th className="px-3 py-2.5">用户</th><th className="px-3 py-2.5">邮箱</th><th className="px-3 py-2.5">注册时间</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5 text-right">启停</th></tr></thead>
              <tbody className="divide-y divide-gray-100 bg-white">{users.length ? users.map((user) => <tr key={user.id}><td className="whitespace-nowrap px-3 py-3"><span className="inline-flex items-center gap-2 font-bold"><UserCheck className="h-4 w-4 text-gray-400" />{user.username}</span></td><td className="px-3 py-3 text-gray-600">{user.email}</td><td className="whitespace-nowrap px-3 py-3 text-xs text-gray-500">{formatDate(user.created_at)}</td><td className="px-3 py-3"><StatusBadge state={user.is_active ? 'ready' : 'error'} label={user.is_active ? '启用' : '停用'} /></td><td className="px-3 py-3 text-right"><ToggleControl checked={user.is_active} onChange={(value) => void updateUser(user, value)} label={`${user.is_active ? '停用' : '启用'}用户 ${user.username}`} disabled={action === `user-${user.id}`} /></td></tr>) : <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">暂无普通用户</td></tr>}</tbody>
            </table>
          </div>
        </div>
      </> : null}
    </section>
  );
};

export default RegistrationManagement;
