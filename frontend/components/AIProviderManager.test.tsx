// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createAIProvider,
  discoverAIProviderModels,
  getAIProviders,
} from '../services/api';
import AIProviderManager from './AIProviderManager';

vi.mock('../services/api', () => ({
  createAIProvider: vi.fn(),
  discoverAIProviderModels: vi.fn(),
  deleteAIProvider: vi.fn(),
  getAIProviders: vi.fn(),
  refreshAIProviderModels: vi.fn(),
  testAIProvider: vi.fn(),
  updateAIProvider: vi.fn(),
}));

const presets = {
  deepseek: {
    label: 'DeepSeek',
    provider_type: 'openai_compatible' as const,
    base_url: 'https://provider.example.test/v1',
    default_model: 'preset-model',
  },
};

afterEach(cleanup);

describe('AIProviderManager model discovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAIProviders).mockResolvedValue({ providers: [], presets });
    vi.mocked(discoverAIProviderModels).mockResolvedValue({ models: ['model-a', 'model-b'] });
    vi.mocked(createAIProvider).mockResolvedValue({} as never);
  });

  it('fills the selector from discovery and saves the selected model with its cache', async () => {
    render(<AIProviderManager />);
    fireEvent.click(await screen.findByRole('button', { name: '添加平台' }));

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-test' } });
    fireEvent.click(screen.getByRole('button', { name: /获取模型/ }));

    await waitFor(() => expect(discoverAIProviderModels).toHaveBeenCalledWith(expect.objectContaining({
      provider_type: 'openai_compatible',
      base_url: 'https://provider.example.test/v1',
      api_key: 'sk-test',
    })));
    expect(screen.getByRole('button', { name: /model-a/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /model-a/ }));
    fireEvent.click(screen.getByRole('option', { name: 'model-b' }));
    expect(screen.getByRole('button', { name: /model-b/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /保存平台/ }));
    await waitFor(() => expect(createAIProvider).toHaveBeenCalledWith(expect.objectContaining({
      default_model: 'model-b',
      models: ['model-a', 'model-b'],
    })));
  });

  it('restores the cached model list while editing an existing provider', async () => {
    vi.mocked(getAIProviders).mockResolvedValue({
      presets,
      providers: [{
        id: 7,
        name: 'Saved provider',
        provider_type: 'openai_compatible',
        preset: 'deepseek',
        base_url: 'https://provider.example.test/v1',
        default_model: 'cached-model',
        models: ['cached-model', 'second-model'],
        models_cached_at: 123,
        models_cache_fresh: true,
        verification_status: 'verified',
        verification_message: '',
        last_verified_at: 123,
        is_default: false,
        api_key_configured: true,
        api_key_masked: '****test',
      }],
    });

    render(<AIProviderManager />);
    fireEvent.click(await screen.findByRole('button', { name: '编辑平台' }));
    expect(screen.getByRole('button', { name: /cached-model/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /cached-model/ }));
    expect(screen.getByRole('option', { name: 'second-model' })).toBeInTheDocument();
  });
});
