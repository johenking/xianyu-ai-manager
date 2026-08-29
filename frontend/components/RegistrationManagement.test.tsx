// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import RegistrationManagement from './RegistrationManagement';

vi.mock('../services/api', () => ({
  createRegistrationInvites: vi.fn(),
  getRegistrationAdminStatus: vi.fn(),
  listRegistrationInvites: vi.fn(),
  listRegistrationUsers: vi.fn(),
  revokeRegistrationInvite: vi.fn(),
  setInviteRequired: vi.fn(),
  setRegistrationEnabled: vi.fn(),
  setRegistrationLimit: vi.fn(),
  setRegistrationUserActive: vi.fn(),
}));

const status = {
  success: true,
  registration: { enabled: false, ready: true, requested: false, terms_version: 'v2' },
  smtp: { configured: true, verified: true, verified_at: '2026-07-11T10:00:00+08:00', support_email: 'su***@example.com' },
  user_limit: 20,
  user_count: 3,
  remaining_slots: 17,
};

const users = [{
  id: 2,
  username: 'pilot-user',
  email: 'pilot@example.com',
  is_active: true,
  created_at: '2026-07-11 10:30:00',
  terms_version: 'v1',
  terms_accepted_at: '2026-07-11 10:30:00',
}];

const activeInvite = {
  id: 7,
  hint: 'REG-ABC...WXYZ',
  note: '代理小王',
  status: 'active' as const,
  expires_at: 1_800_400_000,
  used_at: null,
  used_by_user_id: null,
  revoked_at: null,
  created_by_user_id: 1,
  created_at: 1_800_300_000,
};

describe('RegistrationManagement', () => {
  beforeEach(() => {
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue(status);
    vi.mocked(listRegistrationUsers).mockResolvedValue({ success: true, users });
    vi.mocked(listRegistrationInvites).mockResolvedValue({
      success: true,
      invite_required: false,
      invites: [activeInvite],
    });
    vi.mocked(setRegistrationUserActive).mockResolvedValue({ success: true, user: { ...users[0], is_active: false } });
    vi.mocked(setRegistrationEnabled).mockResolvedValue({ success: true, enabled: true, message: '注册功能已开启' });
    vi.mocked(setRegistrationLimit).mockResolvedValue({ success: true, message: '用户容量已更新' });
    vi.mocked(setInviteRequired).mockResolvedValue({ success: true, invite_required: true });
    vi.mocked(createRegistrationInvites).mockResolvedValue({
      success: true,
      invites: [{ ...activeInvite, id: 8, code: 'REG-NEWCODENEWCODENEWCODE23', hint: 'REG-NEW...DE23', note: '' }],
    });
    vi.mocked(revokeRegistrationInvite).mockResolvedValue({
      success: true,
      invite: { ...activeInvite, status: 'revoked', revoked_at: 1_800_350_000 },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows receipt-confirmed SMTP status, capacity, and the invite management block', async () => {
    render(<RegistrationManagement />);
    await screen.findByText('su***@example.com');

    expect(screen.getByText('已实收验证')).toBeInTheDocument();
    expect(screen.getByText('3 / 20')).toBeInTheDocument();
    expect(screen.getByText('剩余 17 个名额')).toBeInTheDocument();
    expect(screen.getByText('邀请码准入')).toBeInTheDocument();
    expect(screen.getByText('REG-ABC...WXYZ')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '注册需要邀请码' })).not.toBeChecked();
  });

  it('adjusts capacity, disables users, and opens registration when ready', async () => {
    vi.mocked(getRegistrationAdminStatus)
      .mockResolvedValueOnce(status)
      .mockResolvedValue({ ...status, user_limit: 12, remaining_slots: 9 });
    render(<RegistrationManagement />);
    await screen.findByText('pilot-user');

    fireEvent.change(screen.getByLabelText('用户容量'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: '保存容量' }));
    await waitFor(() => expect(setRegistrationLimit).toHaveBeenCalledWith(12));
    expect(await screen.findByText('3 / 12')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('switch', { name: '停用用户 pilot-user' }));
    await waitFor(() => expect(setRegistrationUserActive).toHaveBeenCalledWith(2, false));

    fireEvent.click(screen.getByRole('switch', { name: '开放注册' }));
    await waitFor(() => expect(setRegistrationEnabled).toHaveBeenCalledWith(true));
  });

  it('shows a full-capacity warning and keeps registration closed', async () => {
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue({
      ...status,
      registration: { ...status.registration, ready: false },
      user_limit: 3,
      remaining_slots: 0,
    });

    render(<RegistrationManagement />);

    expect(await screen.findByText(/用户容量已满/)).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '开放注册' })).toBeDisabled();
  });

  it('toggles invite requirement, generates one-time plaintext codes, and revokes invites', async () => {
    render(<RegistrationManagement />);
    await screen.findByText('邀请码准入');

    fireEvent.click(screen.getByRole('switch', { name: '注册需要邀请码' }));
    await waitFor(() => expect(setInviteRequired).toHaveBeenCalledWith(true));
    expect(await screen.findByText('注册已改为邀请码准入')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('邀请码数量'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('邀请码有效天数'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('邀请码备注'), { target: { value: '代理直邀' } });
    fireEvent.click(screen.getByRole('button', { name: /生成邀请码/ }));
    await waitFor(() => expect(createRegistrationInvites).toHaveBeenCalledWith(1, 7, '代理直邀'));
    expect(await screen.findByText('REG-NEWCODENEWCODENEWCODE23')).toBeInTheDocument();
    expect(screen.getByText(/明文仅显示这一次/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '吊销' }));
    await waitFor(() => expect(revokeRegistrationInvite).toHaveBeenCalledWith(7));
    expect(await screen.findByText('已吊销')).toBeInTheDocument();
  });
});
