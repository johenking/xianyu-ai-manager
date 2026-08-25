// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ToastViewport, { clearToasts, pushToast } from './Toast';

describe('ToastViewport', () => {
  afterEach(() => {
    clearToasts();
    cleanup();
    vi.useRealTimers();
  });

  it('renders pushed toasts with success and error styles', () => {
    render(<ToastViewport />);

    act(() => {
      pushToast('success', '草稿已保存');
      pushToast('error', '发布失败');
    });

    const region = screen.getByRole('status');
    expect(region).toHaveTextContent('草稿已保存');
    expect(region).toHaveTextContent('发布失败');
  });

  it('auto dismisses a toast after its duration', () => {
    vi.useFakeTimers();
    render(<ToastViewport />);

    act(() => {
      pushToast('success', '多规格已开启');
    });
    expect(screen.getByRole('status')).toHaveTextContent('多规格已开启');

    act(() => {
      vi.advanceTimersByTime(3300);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('dismisses a toast when its close button is clicked', () => {
    render(<ToastViewport />);

    act(() => {
      pushToast('error', '同步商品失败');
    });
    fireEvent.click(screen.getByRole('button', { name: '关闭提醒' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('keeps at most four toasts and drops the oldest', () => {
    render(<ToastViewport />);

    act(() => {
      pushToast('success', '提示一');
      pushToast('success', '提示二');
      pushToast('success', '提示三');
      pushToast('success', '提示四');
      pushToast('success', '提示五');
    });

    const region = screen.getByRole('status');
    expect(region).not.toHaveTextContent('提示一');
    expect(region).toHaveTextContent('提示二');
    expect(region).toHaveTextContent('提示五');
  });
});
