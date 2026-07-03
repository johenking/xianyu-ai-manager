export interface ProviderAssignment {
  provider_profile_id?: number | null;
  model_name: string;
}

export const requiresProviderTest = (saved: ProviderAssignment, draft: ProviderAssignment): boolean => (
  Number(saved.provider_profile_id || 0) !== Number(draft.provider_profile_id || 0)
  || saved.model_name !== draft.model_name
);
