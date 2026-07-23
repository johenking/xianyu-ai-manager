import { get, post, put } from '../request';
import type {
  ApiResponse,
  AuthCaptchaResponse,
  EmailCodeRequest,
  EmailCodeResponse,
  LoginRequest,
  LoginResponse,
  PasswordResetRequest,
  PasswordResetVerifyRequest,
  PasswordResetVerifyResponse,
  RegistrationAdminStatus,
  RegistrationConfig,
  RegistrationRequest,
  RegistrationUser,
  VerifyResponse,
} from '../../types';

// Auth
export const login = async (data: LoginRequest): Promise<LoginResponse> => {
  return post('/login', data);
};

export const verifyToken = async (): Promise<VerifyResponse> => {
  return get('/verify');
};

export const logout = async (): Promise<ApiResponse> => {
  return post('/logout', {});
};

export const changePassword = async (currentPassword: string, newPassword: string): Promise<ApiResponse> => {
  return post('/change-password', { current_password: currentPassword, new_password: newPassword });
};

export const getRegistrationConfig = async (): Promise<RegistrationConfig> => {
  return get('/api/auth/registration-config');
};

export const createAuthCaptcha = async (): Promise<AuthCaptchaResponse> => {
  return post('/api/auth/captcha', {});
};

export const sendAuthEmailCode = async (data: EmailCodeRequest): Promise<EmailCodeResponse> => {
  return post('/api/auth/email-code', data);
};

export const registerAccount = async (data: RegistrationRequest): Promise<LoginResponse> => {
  return post('/register', data);
};

export const requestPasswordReset = async (data: PasswordResetRequest): Promise<ApiResponse> => {
  return post('/api/auth/password-reset', data);
};

export const verifyPasswordResetCode = async (
  data: PasswordResetVerifyRequest,
): Promise<PasswordResetVerifyResponse> => {
  return post('/api/auth/password-reset/verify-code', data);
};

export const getRegistrationAdminStatus = async (): Promise<RegistrationAdminStatus> => {
  return get('/api/admin/registration/status');
};

export const listRegistrationUsers = async (): Promise<{
  success: boolean;
  users: RegistrationUser[];
}> => {
  return get('/api/admin/registration/users');
};

export const setRegistrationUserActive = async (
  userId: number,
  isActive: boolean,
): Promise<{ success: boolean; user: RegistrationUser }> => {
  return put(`/api/admin/registration/users/${userId}`, { is_active: isActive });
};

export const setRegistrationEnabled = async (
  enabled: boolean,
): Promise<{ success: boolean; enabled: boolean; message: string }> => {
  return put('/api/admin/registration/enabled', { enabled });
};

export const setRegistrationLimit = async (
  limit: number,
): Promise<ApiResponse> => {
  return put('/api/admin/registration/limit', { limit });
};
