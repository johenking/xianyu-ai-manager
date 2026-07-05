import { get, post, put } from '../request';
import type { ApiResponse, SettingsSectionKey, SettingsSummary, SystemSettings } from '../../types';

// Settings
export const getSystemSettings = async (): Promise<SystemSettings> => {
    return get<SystemSettings>('/system-settings');
};

export const getSettingsSummary = async (): Promise<SettingsSummary> => {
  const result = await get<{ success: boolean } & SettingsSummary>('/api/settings/summary');
  return result;
};

export const saveSettingsSection = async (
  section: SettingsSectionKey,
  settings: Partial<SystemSettings>,
  secretActions: Record<string, 'keep' | 'set' | 'clear'> = {},
): Promise<ApiResponse & SettingsSummary & { saved_at: string }> => {
  return put(`/api/settings/sections/${section}`, { settings, secret_actions: secretActions });
};

export const verifySettingsSection = async (
  section: 'ai' | 'smtp',
  settings: Partial<SystemSettings>,
  secretActions: Record<string, 'keep' | 'set' | 'clear'> = {},
): Promise<{ success: boolean; state: string; message: string }> => {
  return post(`/api/settings/verify/${section}`, { settings, secret_actions: secretActions });
};

export const updateSystemSettings = async (settings: Partial<SystemSettings>): Promise<ApiResponse> => {
    // API expects individual PUTs, but we'll loop in the service for convenience or assume bulk endpoint if updated
    // Based on docs 12.2, we iterate.
    const promises = Object.entries(settings).map(([key, value]) => {
         return put(`/system-settings/${key}`, { value: String(value) });
    });
    await Promise.all(promises);
    return { success: true, message: 'Settings saved' };
};
