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
  setRegistrationEnabled,
  setRegistrationUserActive,
} from '../services/api';
import RegistrationManagement from './RegistrationManagement';

vi.mock('../services/api', () => ({
  createRegistrationInvites: vi.fn(),
  getRegistrationAdminStatus: vi.fn(),
  listRegistrationInvites: vi.fn(),
  listRegistrationUsers: vi.fn(),
  revokeRegistrationInvite: vi.fn(),
  setRegistrationEnabled: vi.fn(),
  setRegistrationUserActive: vi.fn(),
}));

const status = {
  success: true,
  registration: { enabled: false, ready: true, requested: false, terms_version: 'v1' },
  smtp: { configured: true, verified: true, verified_at: '2026-07-11T10:00:00+08:00', support_email: 'su***@example.com' },
  invites: { active: 1, used: 0, expired: 0, revoked: 0 },
};

const invites = [{
  id: 1,
  hint: 'INV-****-A1B2',
  note: '内测',
  created_at: 1_752_200_000,
  expires_at: 1_752_804_800,
  used_at: null,
  used_by_user_id: null,
  revoked_at: null,
  created_by_user_id: 1,
  status: 'active' as const,
}];

const users = [{
  id: 2,
  username: 'pilot-user',
  email: 'pilot@example.com',
  is_active: true,
  created_at: '2026-07-11 10:30:00',
  terms_version: 'v1',
  terms_accepted_at: '2026-07-11 10:30:00',
}];

describe('RegistrationManagement', () => {
  beforeEach(() => {
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue(status);
    vi.mocked(listRegistrationInvites).mockResolvedValue({ success: true, invites });
    vi.mocked(listRegistrationUsers).mockResolvedValue({ success: true, users });
    vi.mocked(createRegistrationInvites).mockResolvedValue({
      success: true,
      message: '邀请码已创建，原始邀请码仅显示本次',
      invites: [{ ...invites[0], id: 2, code: 'INVITE-PRIVATE-ONE' }],
    });
    vi.mocked(revokeRegistrationInvite).mockResolvedValue({ success: true, invite: { ...invites[0], status: 'revoked' } });
    vi.mocked(setRegistrationUserActive).mockResolvedValue({ success: true, user: { ...users[0], is_active: false } });
    vi.mocked(setRegistrationEnabled).mockResolvedValue({ success: true, enabled: true, message: '注册功能已开启' });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('creates invitation codes and displays raw values only in the creation result', async () => {
    render(<RegistrationManagement />);
    await screen.findByText('su***@example.com');

    fireEvent.change(screen.getByLabelText('生成数量'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('有效天数'), { target: { value: '14' } });
    fireEvent.change(screen.getByLabelText('邀请码备注'), { target: { value: '第二批内测' } });
    fireEvent.click(screen.getByRole('button', { name: '生成邀请码' }));

    await waitFor(() => expect(createRegistrationInvites).toHaveBeenCalledWith({
      count: 2,
      valid_days: 14,
      note: '第二批内测',
    }));
    expect(await screen.findByText('INVITE-PRIVATE-ONE')).toBeInTheDocument();
    expect(screen.getByText(/原始邀请码仅显示本次/)).toBeInTheDocument();
  });

  it('revokes active invites, disables users, and opens registration when ready', async () => {
    render(<RegistrationManagement />);
    await screen.findByText('pilot-user');

    fireEvent.click(screen.getByRole('button', { name: '吊销邀请码 INV-****-A1B2' }));
    await waitFor(() => expect(revokeRegistrationInvite).toHaveBeenCalledWith(1));

    fireEvent.click(screen.getByRole('switch', { name: '停用用户 pilot-user' }));
    await waitFor(() => expect(setRegistrationUserActive).toHaveBeenCalledWith(2, false));

    fireEvent.click(screen.getByRole('switch', { name: '开放邀请注册' }));
    await waitFor(() => expect(setRegistrationEnabled).toHaveBeenCalledWith(true));
  });

  it('keeps the registration switch disabled until readiness checks pass', async () => {
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue({
      ...status,
      registration: { ...status.registration, ready: false },
      smtp: { ...status.smtp, verified: false },
    });

    render(<RegistrationManagement />);

    expect(await screen.findByText(/验证 SMTP 并保留至少一个有效邀请码/)).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '开放邀请注册' })).toBeDisabled();
  });
});
