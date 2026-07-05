// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ModelSelector from './ModelSelector';


afterEach(cleanup);


describe('ModelSelector', () => {
  it('shows fetched models as explicit selectable options', () => {
    const onChange = vi.fn();
    render(
      <ModelSelector
        models={['deepseek-v4-flash', 'deepseek-v4-pro']}
        value="deepseek-v4-flash"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /deepseek-v4-flash/ }));
    fireEvent.click(screen.getByRole('option', { name: 'deepseek-v4-pro' }));

    expect(onChange).toHaveBeenCalledWith('deepseek-v4-pro');
  });

  it('uses a separate custom model mode', () => {
    const onChange = vi.fn();
    render(
      <ModelSelector
        models={['deepseek-v4-flash']}
        value="deepseek-v4-flash"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '自定义模型 ID' }));
    fireEvent.change(screen.getByLabelText('自定义模型 ID'), {
      target: { value: 'vendor/custom-model' },
    });

    expect(onChange).toHaveBeenCalledWith('vendor/custom-model');
  });
});
