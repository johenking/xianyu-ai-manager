// Auth
export interface LoginResponse {
  success: boolean;
  token?: string;
  message?: string;
  user_id?: number;
  username?: string;
  is_admin?: boolean;
}

export interface LoginRequest {
  identifier?: string;
  username?: string;
  email?: string;
  password: string;
}

export type VerifyResponse = {
  authenticated: true;
  user_id: number;
  username: string;
  is_admin: boolean;
} | {
  authenticated: false;
  user_id?: never;
  username?: never;
  is_admin?: never;
};

export interface RegistrationConfig {
  enabled: boolean;
  ready: boolean;
  invite_required: boolean;
  terms_version: string;
  terms_url: string;
  privacy_url: string;
  support_email: string;
  message: string;
}

export interface AuthCaptchaResponse {
  success: boolean;
  challenge_id: string;
  captcha_image: string;
  expires_in: number;
}

export interface EmailCodeRequest {
  purpose: 'register' | 'password_reset';
  email: string;
  captcha_challenge_id: string;
  captcha_code: string;
}

export interface EmailCodeResponse {
  success: boolean;
  challenge_id: string;
  expires_in: number;
  cooldown_seconds: number;
  message: string;
}

export interface RegistrationRequest {
  email: string;
  challenge_id: string;
  verification_code: string;
  username: string;
  password: string;
  terms_version: string;
  terms_accepted: boolean;
}

export interface PasswordResetRequest {
  email: string;
  challenge_id: string;
  verification_code: string;
  new_password: string;
}

export interface RegistrationUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string;
  terms_version: string | null;
  terms_accepted_at: string | number | null;
}

export interface RegistrationAdminStatus {
  success: boolean;
  registration: {
    enabled: boolean;
    ready: boolean;
    requested: boolean;
    terms_version: string;
  };
  smtp: {
    configured: boolean;
    verified: boolean;
    verified_at: string;
    support_email: string;
  };
  user_limit: number;
  user_count: number;
  remaining_slots: number;
}
