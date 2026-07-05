import { del, get, post, put } from '../request';
import type { ApiResponse, DefaultReply } from '../../types';

// Default Reply
export const getDefaultReplies = async (): Promise<Record<string, DefaultReply>> => {
  const result = await get<Record<string, Partial<DefaultReply>>>('/default-replies');
  return Object.fromEntries(
    Object.entries(result || {}).map(([cookieId, reply]) => [
      cookieId,
      {
        cookie_id: reply.cookie_id || cookieId,
        enabled: reply.enabled ?? false,
        reply_content: reply.reply_content || '',
        reply_once: reply.reply_once ?? false,
        reply_image_url: reply.reply_image_url || ''
      }
    ])
  );
};

export const getDefaultReply = async (cookieId: string): Promise<DefaultReply> => {
  const result = await get<any>(`/api/default-reply/${cookieId}`);
  return {
    cookie_id: cookieId,
    enabled: result.enabled || false,
    reply_content: result.reply_content || '',
    reply_once: result.reply_once || false,
    reply_image_url: result.reply_image_url || ''
  };
};

export const updateDefaultReply = async (cookieId: string, data: Partial<DefaultReply>): Promise<ApiResponse> => {
  return put(`/api/default-reply/${cookieId}`, {
    enabled: data.enabled ?? false,
    reply_content: data.reply_content || '',
    reply_once: data.reply_once ?? false,
    reply_image_url: data.reply_image_url || ''
  });
};

export const deleteDefaultReply = async (cookieId: string): Promise<ApiResponse> => {
  return del(`/api/default-reply/${cookieId}`);
};

export const clearDefaultReplyRecords = async (cookieId: string): Promise<ApiResponse> => {
  return post(`/api/default-reply/${cookieId}/clear-records`, {});
};
