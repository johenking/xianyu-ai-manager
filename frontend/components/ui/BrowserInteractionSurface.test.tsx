// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ImgHTMLAttributes } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BrowserInteractionSurface from './BrowserInteractionSurface';

vi.mock('./AuthenticatedImage', () => ({
  default: (props: ImgHTMLAttributes<HTMLImageElement>) => <img {...props} />,
}));

describe('BrowserInteractionSurface', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('batches a normalized pointer gesture against the displayed frame', async () => {
    const onInteract = vi.fn().mockResolvedValue(undefined);
    render(
      <BrowserInteractionSurface
        imageUrl="/api/official-login/sessions/session-1/image"
        frameRevision={7}
        onInteract={onInteract}
      />,
    );
    const image = screen.getByRole('img', { name: '闲鱼登录验证画面' });
    vi.spyOn(image, 'getBoundingClientRect').mockReturnValue({
      left: 10,
      top: 20,
      width: 200,
      height: 100,
      right: 210,
      bottom: 120,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(image, { pointerId: 1, clientX: 30, clientY: 40 });
    fireEvent.pointerMove(image, { pointerId: 1, clientX: 110, clientY: 70 });
    fireEvent.pointerUp(image, { pointerId: 1, clientX: 190, clientY: 100 });

    await waitFor(() => expect(onInteract).toHaveBeenCalledTimes(1));
    expect(onInteract).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'gesture',
      frame_revision: 7,
      points: [
        { x: 0.1, y: 0.2 },
        { x: 0.5, y: 0.5 },
        { x: 0.9, y: 0.8 },
      ],
      duration_ms: expect.any(Number),
    }));
  });

  it('clears sensitive text immediately and exposes only allowed keys', async () => {
    let resolveInteraction: (() => void) | undefined;
    const onInteract = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => {
        resolveInteraction = resolve;
      }),
    );
    render(
      <BrowserInteractionSurface
        imageUrl="/qr-login/verification-image/session-1"
        frameRevision={3}
        onInteract={onInteract}
      />,
    );

    const input = screen.getByLabelText('向闲鱼页面输入文字');
    fireEvent.change(input, { target: { value: '482615' } });
    fireEvent.click(screen.getByRole('button', { name: '发送到闲鱼页面' }));

    expect(input).toHaveValue('');
    expect(onInteract).toHaveBeenCalledWith({
      kind: 'text',
      frame_revision: 3,
      text: '482615',
    });
    resolveInteraction?.();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '发送回车键' })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole('button', { name: '发送回车键' }));
    expect(onInteract).toHaveBeenLastCalledWith({
      kind: 'key',
      frame_revision: 3,
      key: 'Enter',
    });
  });
});
