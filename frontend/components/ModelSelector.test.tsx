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

  it('returns to the fetched list when the current model becomes available', () => {
    const onChange = vi.fn();
    const view = render(
      <ModelSelector
        models={['other-model']}
        value="model-a"
        onChange={onChange}
      />,
    );

    expect(screen.getByLabelText('自定义模型 ID')).toBeInTheDocument();
    view.rerender(
      <ModelSelector
        models={['model-a', 'other-model']}
        value="model-a"
        onChange={onChange}
      />,
    );

    expect(screen.getByRole('button', { name: /model-a/ })).toBeInTheDocument();
  });

  it('keeps the custom entry available when no models are cached', () => {
    render(
      <ModelSelector
        models={[]}
        value="preset-model"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /preset-model/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '自定义模型 ID' }));
    expect(screen.getByLabelText('自定义模型 ID')).toBeInTheDocument();
  });
});
