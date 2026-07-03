import { describe, expect, it } from 'vitest';
import { requiresProviderTest } from './aiProviderState';

describe('AI provider assignment state', () => {
  it('requires a fresh test only when provider or model changes', () => {
    const saved = { provider_profile_id: 3, model_name: 'deepseek-chat' };

    expect(requiresProviderTest(saved, { ...saved })).toBe(false);
    expect(requiresProviderTest(saved, { ...saved, model_name: 'deepseek-reasoner' })).toBe(true);
    expect(requiresProviderTest(saved, { provider_profile_id: 4, model_name: 'deepseek-chat' })).toBe(true);
  });
});
