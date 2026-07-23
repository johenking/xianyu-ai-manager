// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getAIProviders,
  getRegistrationAdminStatus,
  getSettingsSummary,
  getUserSettingsSummary,
  listRegistrationUsers,
  saveUserBasicSettings,
  saveSettingsSection,
  confirmSmtpVerification,
  verifySettingsSection,
} from '../services/api';
import { SettingsSummary } from '../types';
import Settings from './Settings';

vi.mock('../services/api', () => ({
  getSettingsSummary: vi.fn(),
  getUserSettingsSummary: vi.fn(),
  getRegistrationAdminStatus: vi.fn(),
  listRegistrationUsers: vi.fn(),
  setRegistrationEnabled: vi.fn(),
  setRegistrationLimit: vi.fn(),
  setRegistrationUserActive: vi.fn(),
  getAIProviders: vi.fn(),
  createAIProvider: vi.fn(),
  updateAIProvider: vi.fn(),
  deleteAIProvider: vi.fn(),
  refreshAIProviderModels: vi.fn(),
  testAIProvider: vi.fn(),
  saveSettingsSection: vi.fn(),
  saveUserBasicSettings: vi.fn(),
  confirmSmtpVerification: vi.fn(),
  verifySettingsSection: vi.fn(),
}));

const summary: SettingsSummary = {
  settings: {
    registration_enabled: true,
    show_default_login_info: false,
    login_captcha_enabled: false,
    item_sync_enabled: true,
    item_sync_interval: 600,
    item_sync_max_pages: 5,
    ai_api_url: 'https://api.example.com',
    ai_model: 'model-a',
    ai_api_key_configured: true,
    ai_api_key_masked: '****1234',
    default_reply: '稍后回复',
    smtp_port: 587,
    smtp_use_tls: true,
    smtp_use_ssl: false,
  },
  sections: {
    basic: { state: 'saved', label: '已保存', configured: true },
    ai: { state: 'ready', label: '已配置', configured: true, model: 'model-a' },
    smtp: { state: 'optional', label: '可选未配置', configured: false },
  },
  runtime: { cookie_manager: true, account_count: 1, active_tasks: 1 },
};

describe('Settings configuration sections', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSettingsSummary).mockResolvedValue(summary);
    vi.mocked(getAIProviders).mockResolvedValue({ providers: [], presets: {} });
    vi.mocked(getRegistrationAdminStatus).mockResolvedValue({
      success: true,
      registration: { enabled: false, ready: false, requested: false, terms_version: 'v2' },
      smtp: { configured: false, verified: false, verified_at: '', support_email: '' },
      user_limit: 20,
      user_count: 0,
      remaining_slots: 20,
    });
    vi.mocked(listRegistrationUsers).mockResolvedValue({ success: true, users: [] });
    vi.mocked(saveSettingsSection).mockReset();
    vi.mocked(verifySettingsSection).mockReset();
    vi.mocked(confirmSmtpVerification).mockReset();
  });

  afterEach(() => cleanup());

  const openAndEditAi = async () => {
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /AI 配置/ }));
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'model-b' } });
  };

  it('collapses only after the server confirms a successful save', async () => {
    vi.mocked(saveSettingsSection).mockResolvedValue({
      ...summary,
      settings: { ...summary.settings, ai_model: 'model-b' },
      saved_at: '2026-07-03T10:00:00',
      success: true,
      message: '配置已保存',
    });
    await openAndEditAi();

    fireEvent.click(screen.getByRole('button', { name: '保存并折叠' }));

    await screen.findByText(/AI 配置已保存并确认/);
    expect(screen.queryByRole('button', { name: '保存并折叠' })).toBeNull();
  });

  it('keeps the section open and shows the reason when saving fails', async () => {
    vi.mocked(saveSettingsSection).mockRejectedValue(new Error('数据库写入失败'));
    await openAndEditAi();

    fireEvent.click(screen.getByRole('button', { name: '保存并折叠' }));

    await screen.findByText('数据库写入失败');
    expect(screen.getByRole('button', { name: '保存并折叠' })).toBeTruthy();
  });

  it('shows checking and unavailable states during a failed connection test', async () => {
    let rejectVerification: (reason: Error) => void = () => undefined;
    vi.mocked(verifySettingsSection).mockImplementation(() => new Promise((_, reject) => {
      rejectVerification = reject;
    }));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /AI 配置/ }));

    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    expect(await screen.findAllByText('验证中')).not.toHaveLength(0);
    rejectVerification(new Error('连接超时'));

    await waitFor(() => expect(screen.getAllByText('不可用').length).toBeGreaterThan(0));
    expect(screen.getByText('连接超时')).toBeTruthy();
  });

  it('keeps registration controls in the dedicated gated management section', async () => {
    render(<Settings isAdmin />);

    expect(await screen.findByRole('heading', { name: '注册管理' })).toBeInTheDocument();
    expect(screen.queryByText('允许用户注册')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /SMTP 配置/ }));
    expect(screen.getByLabelText('支持邮箱')).toBeInTheDocument();
    expect(await screen.findByRole('switch', { name: '开放注册' })).toBeDisabled();
  });

  it('applies the QQ Mail preset without filling an authorization code', async () => {
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: 'QQ 邮箱预设' }));

    expect(screen.getByLabelText('SMTP 服务器')).toHaveValue('smtp.qq.com');
    expect(screen.getByLabelText('端口')).toHaveValue(465);
    expect(screen.getByRole('switch', { name: 'SSL' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'STARTTLS' })).not.toBeChecked();
    expect(screen.getByLabelText('邮箱授权码')).toHaveValue('');
  });

  it('requires the six-digit receipt code before SMTP is marked verified', async () => {
    vi.mocked(verifySettingsSection).mockResolvedValue({
      success: true,
      state: 'challenge_sent',
      message: '验证邮件已发送',
      challenge_id: 'smtp-challenge-1',
      expires_in: 600,
      masked_recipient: 're***@example.com',
    });
    vi.mocked(confirmSmtpVerification).mockResolvedValue({
      success: true,
      state: 'verified',
      message: 'SMTP 实收验证成功',
    });
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));

    expect(await screen.findByText(/re\*\*\*@example.com/)).toBeInTheDocument();
    expect(screen.queryByText('已验证')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('SMTP 收件验证码'), { target: { value: '482615' } });
    fireEvent.click(screen.getByRole('button', { name: '确认收件码' }));

    await waitFor(() => expect(confirmSmtpVerification).toHaveBeenCalledWith({
      challenge_id: 'smtp-challenge-1',
      verification_code: '482615',
    }));
    expect(await screen.findByText('SMTP 实收验证成功')).toBeInTheDocument();
  });

  it('serializes SMTP verification actions and clears a returned challenge after editing', async () => {
    let resolveVerification: (result: Awaited<ReturnType<typeof verifySettingsSection>>) => void = () => undefined;
    vi.mocked(verifySettingsSection).mockImplementation(() => new Promise((resolve) => {
      resolveVerification = resolve;
    }));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.change(screen.getByLabelText('SMTP 服务器'), { target: { value: 'smtp.before-verify.example.com' } });
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    await waitFor(() => expect(verifySettingsSection).toHaveBeenCalledTimes(1));

    expect(screen.getByLabelText('SMTP 服务器')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'QQ 邮箱预设' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '验证连接' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存并折叠' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重新读取' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    fireEvent.click(screen.getByRole('button', { name: '保存并折叠' }));
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    expect(verifySettingsSection).toHaveBeenCalledTimes(1);
    expect(saveSettingsSection).not.toHaveBeenCalled();
    expect(getSettingsSummary).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveVerification({
        success: true,
        state: 'pending',
        message: '验证邮件已发送',
        challenge_id: 'current-smtp-challenge',
        expires_in: 600,
        masked_recipient: 're***@example.com',
      });
    });

    expect(await screen.findByLabelText('SMTP 收件验证码')).toBeInTheDocument();
    expect(screen.getByLabelText('SMTP 服务器')).not.toBeDisabled();
    fireEvent.change(screen.getByLabelText('SMTP 服务器'), { target: { value: 'smtp.changed.example.com' } });
    expect(screen.queryByLabelText('SMTP 收件验证码')).not.toBeInTheDocument();
  });

  it('rejects every section save while SMTP verification is pending', async () => {
    vi.mocked(verifySettingsSection).mockImplementation(() => new Promise(() => undefined));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    await waitFor(() => expect(verifySettingsSection).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /AI 配置/ }));
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'model-during-smtp-verify' } });
    const saveButton = screen.getByRole('button', { name: '保存并折叠' });
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);

    expect(saveSettingsSection).not.toHaveBeenCalled();
  });

  it('rejects SMTP verification and confirmation while another section save is pending', async () => {
    vi.mocked(verifySettingsSection).mockResolvedValue({
      success: true,
      state: 'pending',
      message: '验证邮件已发送',
      challenge_id: 'smtp-before-basic-save',
      expires_in: 600,
      masked_recipient: 're***@example.com',
    });
    vi.mocked(saveSettingsSection).mockImplementation(() => new Promise(() => undefined));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    await screen.findByLabelText('SMTP 收件验证码');

    fireEvent.click(screen.getByRole('button', { name: /基础设置/ }));
    fireEvent.click(screen.getByRole('switch', { name: '显示默认登录信息' }));
    fireEvent.click(screen.getByRole('button', { name: '保存并折叠' }));
    await waitFor(() => expect(saveSettingsSection).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /SMTP 配置/ }));
    fireEvent.change(screen.getByLabelText('SMTP 收件验证码'), { target: { value: '482615' } });
    const verifyButton = screen.getByRole('button', { name: '验证连接' });
    const confirmButton = screen.getByRole('button', { name: '确认收件码' });
    expect(verifyButton).toBeDisabled();
    expect(confirmButton).toBeDisabled();
    fireEvent.click(verifyButton);
    fireEvent.click(confirmButton);

    expect(verifySettingsSection).toHaveBeenCalledTimes(1);
    expect(confirmSmtpVerification).not.toHaveBeenCalled();
  });

  it('locks SMTP-changing actions while receipt confirmation is pending', async () => {
    let resolveConfirmation: (result: Awaited<ReturnType<typeof confirmSmtpVerification>>) => void = () => undefined;
    vi.mocked(verifySettingsSection).mockResolvedValue({
      success: true,
      state: 'pending',
      message: '验证邮件已发送',
      challenge_id: 'smtp-challenge-before-edit',
      expires_in: 600,
      masked_recipient: 're***@example.com',
    });
    vi.mocked(confirmSmtpVerification).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve;
    }));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    fireEvent.change(await screen.findByLabelText('SMTP 收件验证码'), { target: { value: '482615' } });
    fireEvent.click(screen.getByRole('button', { name: '确认收件码' }));
    await waitFor(() => expect(confirmSmtpVerification).toHaveBeenCalledTimes(1));

    expect(screen.getByLabelText('SMTP 服务器')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'QQ 邮箱预设' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '验证连接' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存并折叠' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重新读取' })).toBeDisabled();
    await act(async () => {
      resolveConfirmation({ success: true, state: 'ready', message: 'SMTP 实收验证成功' });
    });

    expect(await screen.findByText('SMTP 实收验证成功')).toBeInTheDocument();
  });

  it('synchronizes persisted settings and registration state after a pending SMTP challenge', async () => {
    const syncedSummary: SettingsSummary = {
      ...summary,
      settings: { ...summary.settings, smtp_server: 'smtp.persisted.example.com' },
      sections: { ...summary.sections, smtp: { state: 'warning', label: '待验证', configured: true } },
    };
    vi.mocked(getSettingsSummary)
      .mockResolvedValueOnce(summary)
      .mockResolvedValue(syncedSummary);
    vi.mocked(verifySettingsSection).mockResolvedValue({
      success: true,
      state: 'pending',
      message: '验证邮件已发送',
      challenge_id: 'smtp-pending-sync',
      expires_in: 600,
      masked_recipient: 're***@example.com',
    });
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.change(screen.getByLabelText('SMTP 服务器'), { target: { value: 'smtp.persisted.example.com' } });
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));

    expect(await screen.findByLabelText('SMTP 收件验证码')).toBeInTheDocument();
    await waitFor(() => expect(getSettingsSummary).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getRegistrationAdminStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText('当前内容与数据库一致')).toBeInTheDocument();
    expect(screen.getByLabelText('SMTP 服务器')).toHaveValue('smtp.persisted.example.com');
  });

  it('synchronizes persisted state after SMTP verification delivery fails', async () => {
    vi.mocked(verifySettingsSection).mockRejectedValue(new Error('SMTP 验证邮件发送失败'));
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));

    expect(await screen.findByText('SMTP 验证邮件发送失败')).toBeInTheDocument();
    await waitFor(() => expect(getSettingsSummary).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getRegistrationAdminStatus).toHaveBeenCalledTimes(2));
  });

  it('synchronizes settings and registration state after SMTP confirmation succeeds', async () => {
    vi.mocked(verifySettingsSection).mockResolvedValue({
      success: true,
      state: 'pending',
      message: '验证邮件已发送',
      challenge_id: 'smtp-confirm-sync',
      expires_in: 600,
      masked_recipient: 're***@example.com',
    });
    vi.mocked(confirmSmtpVerification).mockResolvedValue({
      success: true,
      state: 'ready',
      message: 'SMTP 配置已确认',
    });
    render(<Settings isAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: /SMTP 配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    fireEvent.change(await screen.findByLabelText('SMTP 收件验证码'), { target: { value: '482615' } });
    fireEvent.click(screen.getByRole('button', { name: '确认收件码' }));

    expect(await screen.findByText('SMTP 配置已确认')).toBeInTheDocument();
    await waitFor(() => expect(getSettingsSummary).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(getRegistrationAdminStatus).toHaveBeenCalledTimes(3));
  });
});

const userSummary = {
  success: true,
  settings: {
    item_sync_enabled: true,
    item_sync_interval: 600,
    item_sync_max_pages: 5,
  },
  sources: {
    item_sync_enabled: 'global' as const,
    item_sync_interval: 'global' as const,
    item_sync_max_pages: 'user' as const,
  },
  inherited: false,
};

describe('ordinary user settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getUserSettingsSummary).mockResolvedValue(userSummary);
    vi.mocked(getAIProviders).mockResolvedValue({ providers: [], presets: {} });
  });

  afterEach(() => cleanup());

  it('loads only personal settings and keeps admin-only controls and APIs unavailable', async () => {
    render(<Settings isAdmin={false} />);

    expect(await screen.findByRole('heading', { name: '商品自动同步' })).toBeInTheDocument();
    expect(getUserSettingsSummary).toHaveBeenCalledTimes(1);
    expect(getSettingsSummary).not.toHaveBeenCalled();
    expect(getRegistrationAdminStatus).not.toHaveBeenCalled();
    expect(listRegistrationUsers).not.toHaveBeenCalled();
    expect(saveSettingsSection).not.toHaveBeenCalled();
    expect(verifySettingsSection).not.toHaveBeenCalled();
    expect(confirmSmtpVerification).not.toHaveBeenCalled();
    expect(screen.getAllByText('继承系统默认')).toHaveLength(2);
    expect(screen.getByText('个人设置')).toBeInTheDocument();
    expect(screen.queryByText('SMTP 配置')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '注册管理' })).not.toBeInTheDocument();
    expect(screen.queryByText('显示默认登录信息')).not.toBeInTheDocument();
    expect(screen.queryByText('登录滑动验证码')).not.toBeInTheDocument();
    expect(screen.queryByText('账号管理器')).not.toBeInTheDocument();
    expect(screen.queryByText('监听任务')).not.toBeInTheDocument();
    await waitFor(() => expect(getAIProviders).toHaveBeenCalledTimes(1));
  });

  it('validates and saves editable personal sync settings in seconds', async () => {
    vi.mocked(saveUserBasicSettings).mockResolvedValue({
      success: true,
      message: '个人设置已保存',
      settings: {
        item_sync_enabled: false,
        item_sync_interval: 3600,
        item_sync_max_pages: 12,
      },
      sources: {
        item_sync_enabled: 'user',
        item_sync_interval: 'user',
        item_sync_max_pages: 'user',
      },
      inherited: false,
    });
    render(<Settings isAdmin={false} />);

    fireEvent.click(await screen.findByRole('switch', { name: '商品自动同步' }));
    fireEvent.change(screen.getByLabelText('同步间隔（秒）'), { target: { value: '59' } });
    fireEvent.change(screen.getByLabelText('最多同步页数'), { target: { value: '51' } });
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    expect(await screen.findByText('同步间隔需为 60 到 86400 秒')).toBeInTheDocument();
    expect(screen.getByText('最多同步页数需为 1 到 50')).toBeInTheDocument();
    expect(saveUserBasicSettings).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('同步间隔（秒）'), { target: { value: '3600' } });
    fireEvent.change(screen.getByLabelText('最多同步页数'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => expect(saveUserBasicSettings).toHaveBeenCalledWith({
      item_sync_enabled: false,
      item_sync_interval: 3600,
      item_sync_max_pages: 12,
    }));
    expect(await screen.findByText('个人设置已保存')).toBeInTheDocument();
  });

  it('submits only changed fields so untouched values keep inheriting defaults', async () => {
    vi.mocked(saveUserBasicSettings).mockResolvedValue({
      ...userSummary,
      message: '个人设置已保存',
      settings: { ...userSummary.settings, item_sync_interval: 1800 },
      sources: { ...userSummary.sources, item_sync_interval: 'user' },
    });
    render(<Settings isAdmin={false} />);

    const saveButton = await screen.findByRole('button', { name: '保存设置' });
    expect(saveButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('同步间隔（秒）'), { target: { value: '1800' } });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(saveUserBasicSettings).toHaveBeenCalledWith({
      item_sync_interval: 1800,
    }));
  });

  it('shows load errors and retries the personal endpoint', async () => {
    vi.mocked(getUserSettingsSummary)
      .mockRejectedValueOnce(new Error('个人设置读取失败'))
      .mockResolvedValueOnce(userSummary);
    render(<Settings isAdmin={false} />);

    expect(await screen.findByText('个人设置读取失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByRole('heading', { name: '商品自动同步' })).toBeInTheDocument();
    expect(getUserSettingsSummary).toHaveBeenCalledTimes(2);
    expect(getSettingsSummary).not.toHaveBeenCalled();
  });
});
