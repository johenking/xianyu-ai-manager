import { describe, expect, it } from 'vitest';
import { getInitialOpenSection, isSectionDirty } from './settingsState';

describe('settings section state', () => {
  it('opens required incomplete sections and keeps optional smtp collapsed', () => {
    expect(getInitialOpenSection({
      basic: { configured: true },
      ai: { configured: false },
      smtp: { configured: false },
    })).toBe('ai');
    expect(getInitialOpenSection({
      basic: { configured: true },
      ai: { configured: true },
      smtp: { configured: true },
    })).toBeNull();
  });

  it('marks a section dirty only when editable values or secret actions changed', () => {
    const saved = { ai_model: 'deepseek-chat', ai_api_key_masked: '****1234' };
    expect(isSectionDirty(saved, { ...saved }, {})).toBe(false);
    expect(isSectionDirty(saved, { ...saved, ai_model: 'qwen-plus' }, {})).toBe(true);
    expect(isSectionDirty(saved, { ...saved }, { ai_api_key: 'set' })).toBe(true);
  });
});
