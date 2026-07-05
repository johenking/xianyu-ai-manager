import { get, post } from '../request';
import type { ApiResponse, LoginResponse } from '../../types';

// Auth
export const login = async (data: { username?: string; password?: string; email?: string; verification_code?: string }): Promise<LoginResponse> => {
  return post('/login', data);
};

export const verifyToken = async (): Promise<{ authenticated: boolean; user_id?: number; username?: string }> => {
  return get('/verify');
};

export const logout = async (): Promise<ApiResponse> => {
  return post('/logout', {});
};

export const changePassword = async (currentPassword: string, newPassword: string): Promise<ApiResponse> => {
  return post('/change-password', { current_password: currentPassword, new_password: newPassword });
};
