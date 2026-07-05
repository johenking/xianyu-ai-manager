import { get, post, put } from '../request';
import type {
  ApiResponse,
  SkillAgentPrompt,
  SkillBrowserStatus,
  SkillCapability,
  SkillDeliveryDiagnostics,
  SkillMonitorResult,
  SkillMonitorTask,
  SkillOpsHealth,
} from '../../types';

// Skill Center
export const getSkillMonitorTasks = async (): Promise<SkillMonitorTask[]> => {
  const res = await get<{ success: boolean; data: SkillMonitorTask[] }>('/api/skills/monitor/tasks');
  return res.data || [];
};

export const createSkillMonitorTask = async (data: Partial<SkillMonitorTask>): Promise<{ success: boolean; id: number; message: string }> => {
  return post('/api/skills/monitor/tasks', data);
};

export const runSkillMonitorTask = async (taskId: number): Promise<{
  success: boolean;
  message: string;
  result_ids: number[];
  created_count: number;
  raw_count: number;
  source?: string;
  is_real_data?: boolean;
}> => {
  return post(`/api/skills/monitor/tasks/${taskId}/run`, {});
};

export const getSkillMonitorResults = async (taskId?: number): Promise<SkillMonitorResult[]> => {
  const res = await get<{ success: boolean; data: SkillMonitorResult[] }>('/api/skills/monitor/results', taskId ? { task_id: taskId } : undefined);
  return res.data || [];
};

export const getSkillAgentPrompts = async (): Promise<SkillAgentPrompt[]> => {
  const res = await get<{ success: boolean; data: SkillAgentPrompt[] }>('/api/skills/agent/prompts');
  return res.data || [];
};

export const updateSkillAgentPrompt = async (prompt: SkillAgentPrompt): Promise<ApiResponse> => {
  return put(`/api/skills/agent/prompts/${prompt.prompt_type}`, prompt);
};

export const testSkillAgentReply = async (data: {
  message: string;
  cookie_id?: string;
  item_id?: string;
}): Promise<{
  success: boolean;
  intent: string;
  expert: string;
  reply: string;
  used_prompt?: SkillAgentPrompt;
  cookie_id?: string;
  model_name?: string;
  base_url?: string;
  is_real_ai?: boolean;
}> => {
  return post('/api/skills/agent/test-reply', data);
};

export const getSkillCapabilities = async (): Promise<Record<string, SkillCapability>> => {
  const result = await get<{ success: boolean; data: Record<string, SkillCapability> }>('/api/skills/capabilities');
  return result.data || {};
};

export const getSkillOpsHealth = async (): Promise<SkillOpsHealth> => {
  const res = await get<{ success: boolean; data: SkillOpsHealth }>('/api/skills/ops/health');
  return res.data;
};

export const getSkillBrowserStatus = async (): Promise<SkillBrowserStatus> => {
  const res = await get<{ success: boolean; data: SkillBrowserStatus }>('/api/skills/ops/browser-status');
  return res.data;
};

export const getSkillDeliveryDiagnostics = async (): Promise<SkillDeliveryDiagnostics> => {
  const res = await get<{ success: boolean; data: SkillDeliveryDiagnostics }>('/api/skills/ops/delivery-diagnostics');
  return res.data;
};
