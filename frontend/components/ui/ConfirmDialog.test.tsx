// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import ConfirmDialogHost, { clearConfirmDialogs, confirmDialog } from './ConfirmDialog';

describe('ConfirmDialogHost', () => {
  afterEach(() => {
    clearConfirmDialogs();
    cleanup();
  });

  it('resolves true when the confirm button is clicked', async () => {
    render(<ConfirmDialogHost />);

    let result: Promise<boolean>;
    act(() => {
      result = confirmDialog({ title: '删除商品', message: '确认删除吗？', confirmText: '删除', tone: 'danger' });
    });

    expect(screen.getByRole('alertdialog')).toHaveTextContent('删除商品');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('确认删除吗？');

    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await expect(result!).resolves.toBe(true);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('resolves false when the cancel button is clicked', async () => {
    render(<ConfirmDialogHost />);

    let result: Promise<boolean>;
    act(() => {
      result = confirmDialog({ title: '放弃修改' });
    });

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await expect(result!).resolves.toBe(false);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('resolves false when Escape is pressed', async () => {
    render(<ConfirmDialogHost />);

    let result: Promise<boolean>;
    act(() => {
      result = confirmDialog({ title: '清空记录' });
    });

    fireEvent.keyDown(document, { key: 'Escape' });
    await expect(result!).resolves.toBe(false);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('cancels the previous pending confirm when a new one arrives', async () => {
    render(<ConfirmDialogHost />);

    let first: Promise<boolean>;
    let second: Promise<boolean>;
    act(() => {
      first = confirmDialog({ title: '第一个确认' });
    });
    act(() => {
      second = confirmDialog({ title: '第二个确认', confirmText: '继续' });
    });

    await expect(first!).resolves.toBe(false);
    expect(screen.getByRole('alertdialog')).toHaveTextContent('第二个确认');

    fireEvent.click(screen.getByRole('button', { name: '继续' }));
    await expect(second!).resolves.toBe(true);
  });
});
