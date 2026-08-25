// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import OrderItemImage, { clearOrderItemImageCache } from './OrderItemImage';

const imageResponse = () => new Response(new Blob(['image-bytes'], { type: 'image/png' }), {
  status: 200,
  headers: { 'content-type': 'image/png' },
});

const image = (orderId: string, directSrc?: string) => (
  <div className="h-16 w-16">
    <OrderItemImage
      orderId={orderId}
      directSrc={directSrc}
      alt={`商品图-${orderId}`}
      className="h-full w-full"
      fallback={<span>暂无图片</span>}
    />
  </div>
);

describe('OrderItemImage request scheduling', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    });
    localStorage.setItem('auth_token', 'test-token');
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:shared-order-image'),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    clearOrderItemImageCache();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('uses the direct image first and shares one proxy fallback across duplicate layouts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(imageResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(
      <>
        {image('order-1', 'https://img.alicdn.com/order-1.jpg')}
        {image('order-1', 'https://img.alicdn.com/order-1.jpg')}
      </>,
    );

    const directImages = screen.getAllByRole('img', { name: '商品图-order-1' });
    directImages.forEach((node) => {
      expect(node).toHaveAttribute('src', 'https://img.alicdn.com/order-1.jpg');
    });
    expect(fetchMock).not.toHaveBeenCalled();

    directImages.forEach((node) => fireEvent.error(node));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      screen.getAllByRole('img', { name: '商品图-order-1' }).forEach((node) => {
        expect(node).toHaveAttribute('src', 'blob:shared-order-image');
      });
    });
  });

  it('limits uncached proxy downloads to four concurrent requests', async () => {
    const responses: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      responses.push(resolve);
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <>
        {image('order-1')}
        {image('order-2')}
        {image('order-3')}
        {image('order-4')}
        {image('order-5')}
      </>,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    responses[0](imageResponse());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
  });

  it('negative-caches a known failure while letting an explicit retry bypass it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { reason: 'not_saved' },
    }), {
      status: 404,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const first = render(
      <>
        {image('order-missing')}
        {image('order-missing')}
      </>,
    );

    expect((await screen.findAllByRole('button', { name: /图片未保存.*重试/ })).length).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    first.unmount();

    render(image('order-missing'));
    const retry = await screen.findByRole('button', { name: /图片未保存.*重试/ });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(retry);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
