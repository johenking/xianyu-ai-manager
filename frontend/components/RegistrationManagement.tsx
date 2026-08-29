import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader2, MailCheck, RefreshCw, ShieldCheck, Ticket, UserCheck, Users } from 'lucide-react';
import {
  createRegistrationInvites,
  getRegistrationAdminStatus,
  listRegistrationInvites,
  listRegistrationUsers,
  revokeRegistrationInvite,
  setInviteRequired,
  setRegistrationEnabled,
  setRegistrationLimit,
  setRegistrationUserActive,
} from '../services/api';
import type { RegistrationAdminStatus, RegistrationInvite, RegistrationUser } from '../types';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { IconAction, WorkSurface } from './ui/ProtectedPage';

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

const INVITE_STATUS_LABEL: Record<RegistrationInvite['status'], { label: string; state: 'ready' | 'warning' | 'error' | 'missing' }> = {
  active: { label: '可用', state: 'ready' },
  used: { label: '已使用', state: 'missing' },
  expired: { label: '已过期', state: 'warning' },
  revoked: { label: '已吊销', state: 'error' },
};

const RegistrationManagement: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const [status, setStatus] = useState<RegistrationAdminStatus | null>(null);
  const [users, setUsers] = useState<RegistrationUser[]>([]);
  const [limit, setLimit] = useState(1);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState('');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [inviteRequired, setInviteRequiredState] = useState(false);
  const [invites, setInvites] = useState<RegistrationInvite[]>([]);
  const [inviteCount, setInviteCount] = useState(1);
  const [inviteDays, setInviteDays] = useState(30);
  const [inviteNote, setInviteNote] = useState('');
  const [freshInvites, setFreshInvites] = useState<RegistrationInvite[]>([]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [statusResponse, userResponse, inviteResponse] = await Promise.all([
        getRegistrationAdminStatus(),
        listRegistrationUsers(),
        listRegistrationInvites(),
      ]);
      setStatus(statusResponse);
      setLimit(statusResponse.user_limit);
      setUsers(userResponse.users);
      setInviteRequiredState(inviteResponse.invite_required);
      setInvites(inviteResponse.invites);
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '注册管理加载失败') });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

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

  const updateLimit = async () => {
    setAction('limit');
    setNotice(null);
    try {
      const response = await setRegistrationLimit(limit);
      await load(true);
      setNotice({ tone: 'success', text: response.message || '用户容量已更新' });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '用户容量更新失败') });
    } finally {
      setAction('');
    }
  };

  const updateUser = async (user: RegistrationUser, isActive: boolean) => {
    setAction(`user-${user.id}`);
    setNotice(null);
    try {
      const response = await setRegistrationUserActive(user.id, isActive);
      setUsers((current) => current.map((item) => item.id === user.id ? response.user : item));
      setNotice({ tone: 'success', text: `${user.username} 已${isActive ? '启用' : '停用'}` });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '用户状态更新失败') });
    } finally {
      setAction('');
    }
  };

  const updateInviteRequired = async (enabled: boolean) => {
    setAction('invite-required');
    setNotice(null);
    try {
      const response = await setInviteRequired(enabled);
      setInviteRequiredState(response.invite_required);
      setNotice({ tone: 'success', text: response.invite_required ? '注册已改为邀请码准入' : '已关闭邀请码要求' });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '邀请码开关更新失败') });
    } finally {
      setAction('');
    }
  };

  const generateInvites = async () => {
    setAction('invite-create');
    setNotice(null);
    try {
      const response = await createRegistrationInvites(inviteCount, inviteDays, inviteNote.trim());
      setFreshInvites(response.invites);
      setInviteNote('');
      await load(true);
      setNotice({ tone: 'success', text: `已生成 ${response.invites.length} 个邀请码，明文仅此一次，请立即复制` });
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
      setFreshInvites((current) => current.filter((item) => item.id !== invite.id));
      setNotice({ tone: 'success', text: `邀请码 ${invite.hint} 已吊销` });
    } catch (error) {
      setNotice({ tone: 'error', text: getErrorMessage(error, '邀请码吊销失败') });
    } finally {
      setAction('');
    }
  };

  const full = Boolean(status && status.remaining_slots <= 0);

  return (
    <WorkSurface className="space-y-5 p-5 sm:p-7">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-gray-700" /><h3 id="registration-management-title" className="text-lg font-extrabold text-gray-950">注册管理</h3></div>
          <p className="mt-1 text-sm text-gray-500">SMTP 实收验证、注册容量和普通用户状态</p>
        </div>
        <IconAction icon={loading ? Loader2 : RefreshCw} label="刷新状态" busy={loading} onClick={() => void load()} disabled={loading} />
      </div>

      {notice ? <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice> : null}
      {loading && !status ? <div className="flex items-center justify-center py-10 text-sm text-gray-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载注册状态...</div> : null}

      {status ? <>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
            <div className="flex items-center justify-between gap-3"><span className="text-sm font-bold text-gray-900">注册状态</span><StatusBadge state={status.registration.enabled ? 'ready' : 'warning'} label={status.registration.enabled ? '已开放' : '已关闭'} /></div>
            <div className="mt-4 flex items-center justify-between gap-4"><span className="text-xs text-gray-500">协议 {status.registration.terms_version}</span><ToggleControl checked={status.registration.enabled} onChange={(value) => void updateEnabled(value)} label="开放注册" disabled={action === 'registration' || (!status.registration.enabled && !status.registration.ready)} /></div>
          </div>
          <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
            <div className="flex items-center justify-between gap-3"><span className="inline-flex items-center gap-2 text-sm font-bold text-gray-900"><MailCheck className="h-4 w-4" />SMTP</span><StatusBadge state={status.smtp.verified ? 'ready' : 'missing'} label={status.smtp.verified ? '已实收验证' : status.smtp.configured ? '待实收验证' : '未配置'} /></div>
            <p className="mt-4 text-xs text-gray-500">{status.smtp.support_email || '未设置独立支持邮箱'}</p>
          </div>
          <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
            <div className="flex items-center justify-between gap-3"><span className="inline-flex items-center gap-2 text-sm font-bold text-gray-900"><Users className="h-4 w-4" />用户容量</span><span className="text-xl font-extrabold text-gray-950">{status.user_count} / {status.user_limit}</span></div>
            <p className="mt-4 text-xs text-gray-500">剩余 {status.remaining_slots} 个名额</p>
          </div>
        </div>

        {!status.smtp.verified ? <InlineNotice>开放注册前必须完成 SMTP 独立收件地址的实收验证。</InlineNotice> : null}
        {full ? <InlineNotice tone="error">用户容量已满，请提高上限后再开放注册。</InlineNotice> : null}

        <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block flex-1 text-sm font-bold text-gray-800">用户容量
              <input aria-label="用户容量" type="number" min={1} max={1000} value={limit} onChange={(event) => setLimit(Number(event.target.value))} className="ios-input mt-2 h-10 w-full rounded-xl px-3 font-normal" />
            </label>
            <button type="button" onClick={() => void updateLimit()} disabled={action === 'limit' || limit < 1 || limit > 1000 || limit === status.user_limit} className="ios-btn-primary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold">{action === 'limit' ? <Loader2 className="h-4 w-4 animate-spin" /> : null}保存容量</button>
          </div>
          <p className="mt-2 text-xs text-gray-500">允许范围 1–1000。降低上限不会删除现有用户，保存后以服务端状态为准。</p>
        </div>

        <div className="space-y-4 rounded-xl border border-gray-100 bg-gray-50 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="inline-flex items-center gap-2 text-sm font-bold text-gray-900"><Ticket className="h-4 w-4" />邀请码准入</span>
            <ToggleControl checked={inviteRequired} onChange={(value) => void updateInviteRequired(value)} label="注册需要邀请码" disabled={action === 'invite-required'} />
          </div>
          <p className="text-xs text-gray-500">开启后，新用户注册时必须提交一个有效邀请码；每个邀请码只能使用一次。</p>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block w-24 text-sm font-bold text-gray-800">数量
              <input aria-label="邀请码数量" type="number" min={1} max={20} value={inviteCount} onChange={(event) => setInviteCount(Number(event.target.value))} className="ios-input mt-2 h-10 w-full rounded-xl px-3 font-normal" />
            </label>
            <label className="block w-28 text-sm font-bold text-gray-800">有效天数
              <input aria-label="邀请码有效天数" type="number" min={1} max={365} value={inviteDays} onChange={(event) => setInviteDays(Number(event.target.value))} className="ios-input mt-2 h-10 w-full rounded-xl px-3 font-normal" />
            </label>
            <label className="block flex-1 text-sm font-bold text-gray-800">备注
              <input aria-label="邀请码备注" type="text" maxLength={200} value={inviteNote} onChange={(event) => setInviteNote(event.target.value)} placeholder="给谁用的，例如：代理小王" className="ios-input mt-2 h-10 w-full rounded-xl px-3 font-normal" />
            </label>
            <button type="button" onClick={() => void generateInvites()} disabled={action === 'invite-create' || inviteCount < 1 || inviteCount > 20 || inviteDays < 1 || inviteDays > 365} className="ios-btn-primary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold">{action === 'invite-create' ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}生成邀请码</button>
          </div>

          {freshInvites.length ? (
            <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-bold text-amber-800">新生成的邀请码（明文仅显示这一次，刷新后不可找回）：</p>
              <ul className="space-y-1">
                {freshInvites.map((invite) => (
                  <li key={invite.id} className="flex flex-wrap items-center gap-2 text-sm">
                    <code className="rounded bg-white px-2 py-1 font-mono font-bold text-gray-900">{invite.code}</code>
                    {invite.note ? <span className="text-xs text-gray-500">{invite.note}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="overflow-x-auto rounded-xl border border-gray-100">
            <table className="min-w-[640px] divide-y divide-gray-200 text-left text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500"><tr><th className="px-3 py-2.5">邀请码</th><th className="px-3 py-2.5">备注</th><th className="px-3 py-2.5">过期时间</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5 text-right">操作</th></tr></thead>
              <tbody className="divide-y divide-gray-100 bg-white">{invites.length ? invites.map((invite) => {
                const meta = INVITE_STATUS_LABEL[invite.status];
                return (
                  <tr key={invite.id}>
                    <td className="whitespace-nowrap px-3 py-3 font-mono text-xs text-gray-700">{invite.hint}</td>
                    <td className="px-3 py-3 text-gray-600">{invite.note || '—'}</td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-gray-500">{formatDate(invite.expires_at)}</td>
                    <td className="px-3 py-3"><StatusBadge state={meta.state} label={meta.label} /></td>
                    <td className="px-3 py-3 text-right">
                      {invite.status === 'active' ? (
                        <button type="button" onClick={() => void revokeInvite(invite)} disabled={action === `invite-${invite.id}`} className="rounded-lg px-2.5 py-1.5 text-xs font-bold text-red-600 hover:bg-red-50 disabled:opacity-50">吊销</button>
                      ) : null}
                    </td>
                  </tr>
                );
              }) : <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">暂无邀请码</td></tr>}</tbody>
            </table>
          </div>
        </div>

        <div className="space-y-3">
          <h4 className="font-extrabold text-gray-950">最近注册用户</h4>
          <div className="overflow-x-auto rounded-xl border border-gray-100">
            <table className="min-w-[720px] divide-y divide-gray-200 text-left text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500"><tr><th className="px-3 py-2.5">用户</th><th className="px-3 py-2.5">邮箱</th><th className="px-3 py-2.5">注册时间</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5 text-right">启停</th></tr></thead>
              <tbody className="divide-y divide-gray-100 bg-white">{users.length ? users.map((user) => <tr key={user.id}><td className="whitespace-nowrap px-3 py-3"><span className="inline-flex items-center gap-2 font-bold"><UserCheck className="h-4 w-4 text-gray-400" />{user.username}</span></td><td className="px-3 py-3 text-gray-600">{user.email}</td><td className="whitespace-nowrap px-3 py-3 text-xs text-gray-500">{formatDate(user.created_at)}</td><td className="px-3 py-3"><StatusBadge state={user.is_active ? 'ready' : 'error'} label={user.is_active ? '启用' : '停用'} /></td><td className="px-3 py-3 text-right"><ToggleControl checked={user.is_active} onChange={(value) => void updateUser(user, value)} label={`${user.is_active ? '停用' : '启用'}用户 ${user.username}`} disabled={action === `user-${user.id}`} /></td></tr>) : <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">暂无普通用户</td></tr>}</tbody>
            </table>
          </div>
        </div>
      </> : null}
    </WorkSurface>
  );
};

export default RegistrationManagement;
