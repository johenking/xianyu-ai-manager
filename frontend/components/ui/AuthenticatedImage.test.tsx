// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AuthenticatedImage from './AuthenticatedImage';


describe('AuthenticatedImage', () => {
  beforeEach(() => {
    localStorage.setItem('auth_token', 'synthetic-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      new Blob(['synthetic-image'], { type: 'image/png' }),
      { status: 200, headers: { 'Content-Type': 'image/png' } },
    )));
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
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
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
