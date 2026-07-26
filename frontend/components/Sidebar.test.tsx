// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';

describe('Sidebar mobile controls', () => {
  afterEach(() => cleanup());

  it('keeps the mobile close control at least 44px square', () => {
    render(
      <Sidebar
        activeTab="skills"
        setActiveTab={vi.fn()}
        onLogout={vi.fn()}
        mobileOpen
        onMobileClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '关闭导航' })).toHaveClass('h-11', 'w-11', 'shrink-0');
  });
});
