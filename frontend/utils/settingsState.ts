export type SettingsSectionKey = 'basic' | 'ai' | 'smtp';

type SectionSummary = Record<SettingsSectionKey, { configured: boolean }>;

const stable = (value: Record<string, unknown>) => JSON.stringify(
  Object.keys(value).sort().reduce<Record<string, unknown>>((result, key) => {
    result[key] = value[key];
    return result;
  }, {}),
);

export const isSectionDirty = (
  saved: Record<string, unknown>,
  draft: Record<string, unknown>,
  secretActions: Record<string, string>,
) => stable(saved) !== stable(draft)
  || Object.values(secretActions).some((action) => action && action !== 'keep');

export const getInitialOpenSection = (summary: SectionSummary): SettingsSectionKey | null => {
  if (!summary.basic.configured) return 'basic';
  if (!summary.ai.configured) return 'ai';
  return null;
};
