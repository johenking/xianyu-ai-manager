// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import RemoteImage from './RemoteImage';

describe('RemoteImage', () => {
  afterEach(() => cleanup());

  it('keeps a failed URL hidden until the source changes', () => {
    const view = render(
      <RemoteImage src="https://example.com/first.jpg" alt="商品图" fallback={<span>暂无图片</span>} />,
    );

    fireEvent.error(screen.getByRole('img', { name: '商品图' }));
    expect(screen.getByText('暂无图片')).toBeInTheDocument();

    view.rerender(
      <RemoteImage src="https://example.com/first.jpg" alt="商品图" fallback={<span>暂无图片</span>} />,
    );
    expect(screen.queryByRole('img', { name: '商品图' })).not.toBeInTheDocument();

    view.rerender(
      <RemoteImage src="https://example.com/second.jpg" alt="商品图" fallback={<span>暂无图片</span>} />,
    );
    expect(screen.getByRole('img', { name: '商品图' })).toHaveAttribute(
      'src',
      'https://example.com/second.jpg',
    );
  });
});
