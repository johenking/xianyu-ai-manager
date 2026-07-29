// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AuthenticatedImage from './AuthenticatedImage';


describe('AuthenticatedImage', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, String(value))),
      removeItem: vi.fn((key: string) => values.delete(key)),
      clear: vi.fn(() => values.clear()),
      key: vi.fn((index: number) => Array.from(values.keys())[index] ?? null),
      get length() { return values.size; },
    };
    vi.stubGlobal('localStorage', storage);
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: storage,
    });
    localStorage.setItem('auth_token', 'synthetic-token');
    const imageBlob = new Blob(['synthetic-image'], { type: 'image/png' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'Content-Type': 'image/png' }),
      blob: vi.fn().mockResolvedValue(imageBlob),
    } as unknown as Response));
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:authenticated-image'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('loads protected images with the bearer token and revokes the object URL', async () => {
    const view = render(
      <AuthenticatedImage
        src="/api/official-login/sessions/session-1/image"
        alt="verification"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'verification' })).toHaveAttribute(
        'src',
        'blob:authenticated-image',
      );
    });
    expect(fetch).toHaveBeenCalledWith(
      '/api/official-login/sessions/session-1/image',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer synthetic-token',
        }),
      }),
    );

    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:authenticated-image');
  });

  it('uses data URLs directly without issuing an authenticated request', () => {
    render(
      <AuthenticatedImage
        src="data:image/png;base64,c3ludGhldGlj"
        alt="qr"
      />,
    );

    expect(screen.getByRole('img', { name: 'qr' })).toHaveAttribute(
      'src',
      'data:image/png;base64,c3ludGhldGlj',
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
