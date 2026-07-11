// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getRegistrationAdminStatus,
  listRegistrationUsers,
  setRegistrationEnabled,
  setRegistrationLimit,
  setRegistrationUserActive,
} from '../services/api';
import RegistrationManagement from './RegistrationManagement';

vi.mock('../services/api', () => ({
  getRegistrationAdminStatus: vi.fn(),
  listRegistrationUsers: vi.fn(),
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

describe('RegistrationManagement', () => {
  beforeEach(() => {
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue(status);
    vi.mocked(listRegistrationUsers).mockResolvedValue({ success: true, users });
    vi.mocked(setRegistrationUserActive).mockResolvedValue({ success: true, user: { ...users[0], is_active: false } });
    vi.mocked(setRegistrationEnabled).mockResolvedValue({ success: true, enabled: true, message: '注册功能已开启' });
    vi.mocked(setRegistrationLimit).mockResolvedValue({ success: true, message: '用户容量已更新' });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows receipt-confirmed SMTP status and registration capacity without invitation controls', async () => {
    render(<RegistrationManagement />);
    await screen.findByText('su***@example.com');

    expect(screen.getByText('已实收验证')).toBeInTheDocument();
    expect(screen.getByText('3 / 20')).toBeInTheDocument();
    expect(screen.getByText('剩余 17 个名额')).toBeInTheDocument();
    expect(screen.queryByText(/邀请码/)).not.toBeInTheDocument();
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
});
