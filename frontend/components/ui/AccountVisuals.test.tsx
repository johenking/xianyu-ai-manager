// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountAvatar, CookieEditor } from './AccountVisuals';

describe('AccountVisuals', () => {
  afterEach(() => cleanup());

  it('falls back to a labelled placeholder when the avatar cannot load', () => {
    render(<AccountAvatar src="https://invalid.example/avatar.png" label="演示账号" />);
    fireEvent.error(screen.getByRole('img', { name: '演示账号头像' }));
    expect(screen.getByRole('img', { name: '演示账号头像占位图' })).toBeTruthy();
  });

  it('keeps Cookie content hidden until the user explicitly reveals it', () => {
    const onChange = vi.fn();
    render(<CookieEditor value="private-cookie-value" onChange={onChange} />);

    expect(screen.queryByDisplayValue('private-cookie-value')).toBeNull();
    expect(screen.getByText('Cookie 已保存 · 20 字符')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '显示并编辑' }));
    const textarea = screen.getByRole('textbox', { name: 'Cookie 内容' });
    expect((textarea as HTMLTextAreaElement).value).toBe('private-cookie-value');

    fireEvent.change(textarea, { target: { value: 'updated-cookie' } });
    expect(onChange).toHaveBeenCalledWith('updated-cookie');
  });
});
