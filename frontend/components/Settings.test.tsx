// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getAIProviders,
  getRegistrationAdminStatus,
  getSettingsSummary,
  listRegistrationUsers,
  saveSettingsSection,
  confirmSmtpVerification,
  verifySettingsSection,
} from '../services/api';
import { SettingsSummary } from '../types';
import Settings from './Settings';

vi.mock('../services/api', () => ({
  getSettingsSummary: vi.fn(),
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
    render(<Settings />);
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
    render(<Settings />);
    fireEvent.click(await screen.findByRole('button', { name: /AI 配置/ }));

    fireEvent.click(screen.getByRole('button', { name: '验证连接' }));
    expect(await screen.findAllByText('验证中')).not.toHaveLength(0);
    rejectVerification(new Error('连接超时'));

    await waitFor(() => expect(screen.getAllByText('不可用').length).toBeGreaterThan(0));
    expect(screen.getByText('连接超时')).toBeTruthy();
  });

  it('keeps registration controls in the dedicated gated management section', async () => {
    render(<Settings />);

    expect(await screen.findByRole('heading', { name: '注册管理' })).toBeInTheDocument();
    expect(screen.queryByText('允许用户注册')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /SMTP 配置/ }));
    expect(screen.getByLabelText('支持邮箱')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '开放注册' })).toBeDisabled();
  });

  it('applies the QQ Mail preset without filling an authorization code', async () => {
    render(<Settings />);
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
    render(<Settings />);
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
});
