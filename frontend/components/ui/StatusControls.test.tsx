// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ToggleControl } from './StatusControls';

describe('ToggleControl', () => {
  afterEach(() => cleanup());

  it('exposes switch state and keeps the thumb inside the fixed track positions', () => {
    const onChange = vi.fn();
    const { rerender } = render(<ToggleControl checked={false} onChange={onChange} label="测试开关" />);
    const control = screen.getByRole('switch', { name: '测试开关' });
    const thumb = control.querySelector('span');

    expect(control.getAttribute('aria-checked')).toBe('false');
    expect(control.className).toContain('h-8');
    expect(control.className).toContain('w-14');
    expect(thumb?.className).toContain('translate-x-0');

    fireEvent.click(control);
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(<ToggleControl checked onChange={onChange} label="测试开关" />);
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch').querySelector('span')?.className).toContain('translate-x-6');
  });

  it('reports and enforces the disabled state', () => {
    const onChange = vi.fn();
    render(<ToggleControl checked={false} onChange={onChange} label="禁用开关" disabled />);
    const control = screen.getByRole('switch', { name: '禁用开关' });

    expect(control.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(control);
    expect(onChange).not.toHaveBeenCalled();
  });
});
