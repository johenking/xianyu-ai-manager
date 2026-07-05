import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Search } from 'lucide-react';


interface ModelSelectorProps {
  models: string[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}


const ModelSelector: React.FC<ModelSelectorProps> = ({ models, value, onChange, disabled }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [customMode, setCustomMode] = useState(Boolean(value && !models.includes(value)));
  const containerRef = useRef<HTMLDivElement>(null);
  const uniqueModels = useMemo(() => [...new Set(models)].sort(), [models]);
  const visibleModels = uniqueModels.filter((model) => model.toLowerCase().includes(query.toLowerCase()));

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, []);

  if (customMode) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="custom-account-ai-model" className="text-sm font-bold text-gray-700">自定义模型 ID</label>
          <button type="button" onClick={() => setCustomMode(false)} className="text-xs font-bold text-gray-600 hover:text-black">
            返回模型列表
          </button>
        </div>
        <input
          id="custom-account-ai-model"
          aria-label="自定义模型 ID"
          type="text"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="ios-input w-full rounded-xl px-4 py-3"
          placeholder="例如 vendor/model-name"
        />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative space-y-2">
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="ios-input flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left disabled:opacity-50"
      >
        <span className={value ? 'font-semibold text-gray-900' : 'text-gray-400'}>{value || '请选择模型'}</span>
        <ChevronDown className={`h-4 w-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <button type="button" onClick={() => setCustomMode(true)} className="text-xs font-bold text-gray-600 hover:text-black">
        自定义模型 ID
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl">
          <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
            <Search className="h-4 w-4 text-gray-400" />
            <input
              autoFocus
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="搜索模型"
              className="min-w-0 flex-1 border-0 bg-transparent py-1 text-sm outline-none"
              placeholder="搜索模型"
            />
          </div>
          <div role="listbox" aria-label="可用模型" className="max-h-56 overflow-y-auto p-1">
            {visibleModels.map((model) => (
              <button
                key={model}
                type="button"
                role="option"
                aria-selected={model === value}
                onClick={() => {
                  onChange(model);
                  setOpen(false);
                  setQuery('');
                }}
                className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium hover:bg-yellow-50"
              >
                <span className="break-all">{model}</span>
                {model === value && <Check className="h-4 w-4 shrink-0 text-green-600" />}
              </button>
            ))}
            {visibleModels.length === 0 && <div className="px-3 py-4 text-center text-sm text-gray-500">没有匹配模型</div>}
          </div>
        </div>
      )}
    </div>
  );
};


export default ModelSelector;
