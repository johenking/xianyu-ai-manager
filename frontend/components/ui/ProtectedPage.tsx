import React from 'react';
import type { LucideIcon } from 'lucide-react';

type PageHeaderProps = {
  icon: LucideIcon;
  title: string;
  description: string;
  actions?: React.ReactNode;
};

export const PageHeader: React.FC<PageHeaderProps> = ({ icon: Icon, title, description, actions }) => (
  <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
    <div className="flex min-w-0 items-center gap-3">
      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#FFE815] text-black shadow-sm shadow-yellow-100">
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0">
        <h2 className="text-2xl font-extrabold text-gray-950">{title}</h2>
        <p className="mt-1 text-sm leading-6 text-gray-500">{description}</p>
      </div>
    </div>
    {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
  </header>
);

type SurfaceProps = {
  children: React.ReactNode;
  className?: string;
  as?: 'div' | 'section' | 'article';
};

export const WorkSurface: React.FC<SurfaceProps> = ({ children, className = '', as = 'section' }) => {
  const Tag = as;
  return (
    <Tag className={`rounded-2xl border border-gray-100 bg-white shadow-[0_8px_24px_rgba(0,0,0,0.04)] ${className}`}>
      {children}
    </Tag>
  );
};

export type SegmentedNavItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  detail?: string;
  trailing?: React.ReactNode;
};

type SegmentedNavProps = {
  value: string;
  items: SegmentedNavItem[];
  onChange: (value: string) => void;
  expandedValue?: string | null;
};

export const SegmentedNav: React.FC<SegmentedNavProps> = ({ value, items, onChange, expandedValue }) => (
  <nav className="max-w-full overflow-x-auto rounded-2xl border border-gray-100 bg-white p-1.5 shadow-[0_8px_24px_rgba(0,0,0,0.04)]" aria-label="页面分区">
    <div className="flex min-w-max gap-1">
      {items.map((item) => {
        const Icon = item.icon;
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            aria-current={selected ? 'page' : undefined}
            aria-expanded={expandedValue === undefined ? undefined : expandedValue === item.id}
            onClick={() => onChange(item.id)}
            className={`flex min-h-12 min-w-36 items-center gap-3 rounded-xl px-4 py-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400 focus-visible:ring-offset-2 ${selected ? 'bg-[#FFE815] text-black shadow-sm shadow-yellow-100' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'}`}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-extrabold">{item.label}</span>
              {item.detail ? <span className="mt-0.5 block max-w-44 truncate text-xs font-medium opacity-70">{item.detail}</span> : null}
            </span>
            {item.trailing ? <span className="shrink-0">{item.trailing}</span> : null}
          </button>
        );
      })}
    </div>
  </nav>
);

type IconActionProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  label: string;
  busy?: boolean;
  danger?: boolean;
};

export const IconAction: React.FC<IconActionProps> = ({ icon: Icon, label, busy, danger, className = '', ...props }) => (
  <button
    type="button"
    title={label}
    aria-label={label}
    className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${danger ? 'border-red-100 bg-red-50 text-red-600 hover:bg-red-100' : 'border-gray-100 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-950'} ${className}`}
    {...props}
  >
    <Icon className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />
  </button>
);
