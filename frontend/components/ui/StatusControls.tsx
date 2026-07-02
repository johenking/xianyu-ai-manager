import React from 'react';
import { CheckCircle2, AlertCircle, Info } from 'lucide-react';

export const ToggleControl: React.FC<{
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}> = ({ checked, onChange, label, disabled }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    disabled={disabled}
    onClick={() => onChange(!checked)}
    className={`relative h-8 w-14 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-yellow-400 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${checked ? 'bg-[#FFE815]' : 'bg-gray-300'}`}
  >
    <span className={`absolute left-1 top-1 h-6 w-6 rounded-full bg-white shadow-sm transition-transform ${checked ? 'translate-x-6' : 'translate-x-0'}`} />
  </button>
);

export const StatusBadge: React.FC<{ state: string; label: string }> = ({ state, label }) => {
  const style = state === 'ready' || state === 'saved'
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : state === 'dirty' || state === 'warning'
      ? 'bg-amber-50 text-amber-700 border-amber-200'
      : state === 'error' || state === 'missing'
        ? 'bg-red-50 text-red-700 border-red-200'
        : 'bg-gray-100 text-gray-600 border-gray-200';
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-bold ${style}`}>{label}</span>;
};

export const InlineNotice: React.FC<{ tone?: 'success' | 'error' | 'info'; children: React.ReactNode }> = ({ tone = 'info', children }) => {
  const Icon = tone === 'success' ? CheckCircle2 : tone === 'error' ? AlertCircle : Info;
  const style = tone === 'success'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-blue-200 bg-blue-50 text-blue-800';
  return <div role="status" className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium ${style}`}><Icon className="mt-0.5 h-4 w-4 shrink-0" />{children}</div>;
};
