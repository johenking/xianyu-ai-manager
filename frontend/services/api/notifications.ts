import { del, get, post, put } from '../request';
import type { ApiResponse } from '../../types';

// Notification Channels
export const getNotificationChannels = async (): Promise<{ success: boolean; data?: any[] }> => {
  const result = await get<any[]>('/notification-channels');
  const channels = (result || []).map((item: any) => {
    let parsedConfig;
    try {
      parsedConfig = JSON.parse(item.config);
    } catch {
      parsedConfig = undefined;
    }
    return {
      id: String(item.id),
      name: item.name,
      type: item.type,
      config: parsedConfig,
      enabled: item.enabled,
      created_at: item.created_at,
      updated_at: item.updated_at,
    };
  });
  return { success: true, data: channels };
}

export const createNotificationChannel = async (data: { name: string; type: string; config: Record<string, unknown> }): Promise<ApiResponse> => {
  return post('/notification-channels', {
    ...data,
    config: JSON.stringify(data.config)
  });
}

export const updateNotificationChannel = async (channelId: string, data: { name?: string; config?: Record<string, unknown>; enabled?: boolean }): Promise<ApiResponse> => {
  const payload: Record<string, unknown> = { ...data };
  if ('config' in data) {
    payload.config = JSON.stringify(data.config);
  }
  return put(`/notification-channels/${channelId}`, payload);
}

export const deleteNotificationChannel = async (channelId: string): Promise<ApiResponse> => {
  return del(`/notification-channels/${channelId}`);
}

// Message Notifications
export const getMessageNotifications = async (): Promise<{ success: boolean; data?: any[] }> => {
  const result = await get<Record<string, any[]>>('/message-notifications');
  const notifications = [];
  for (const [cookieId, channelList] of Object.entries(result || {})) {
    if (Array.isArray(channelList)) {
      for (const item of channelList) {
        notifications.push({
          cookie_id: cookieId,
          channel_id: item.channel_id,
          channel_name: item.channel_name,
          enabled: item.enabled,
        });
      }
    }
  }
  return { success: true, data: notifications };
}

export const setMessageNotification = async (cookieId: string, channelId: number, enabled: boolean): Promise<ApiResponse> => {
  return post(`/message-notifications/${cookieId}`, { channel_id: channelId, enabled });
}

export const deleteMessageNotification = async (notificationId: string): Promise<ApiResponse> => {
  return del(`/message-notifications/${notificationId}`);
}

export const deleteAccountNotifications = async (cookieId: string): Promise<ApiResponse> => {
  return del(`/message-notifications/account/${cookieId}`);
}
