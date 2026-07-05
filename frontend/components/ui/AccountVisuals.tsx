import React, { useEffect, useState } from 'react';
import { Eye, EyeOff, UserRound } from 'lucide-react';

export const AccountAvatar: React.FC<{
  src?: string | null;
  label: string;
  className?: string;
}> = ({ src, label, className = '' }) => {
  const [failed, setFailed] = useState(!src);

  useEffect(() => setFailed(!src), [src]);

  if (failed) {
    return (
      <div
        role="img"
        aria-label={`${label}头像占位图`}
        className={`flex items-center justify-center bg-gray-100 text-gray-400 ${className}`}
      >
        <UserRound className="h-8 w-8" aria-hidden="true" />
      </div>
    );
  }

  return (
    <img
      src={src || ''}
      alt={`${label}头像`}
      onError={() => setFailed(true)}
      className={className}
    />
  );
};

export const CookieEditor: React.FC<{
  value: string;
  onChange: (value: string) => void;
}> = ({ value, onChange }) => {
  const [revealed, setRevealed] = useState(false);

  if (!revealed) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-bold text-gray-900">{value ? `Cookie 已保存 · ${value.length} 字符` : '尚未保存 Cookie'}</p>
          <p className="mt-1 text-xs text-gray-500">默认隐藏敏感内容，需要更新时再显示。</p>
        </div>
        <button
          type="button"
          onClick={() => setRevealed(true)}
          className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 text-sm font-bold text-gray-700 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-yellow-400 focus:ring-offset-2"
        >
          <Eye className="h-4 w-4" aria-hidden="true" />
          显示并编辑
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <textarea
        aria-label="Cookie 内容"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="更新账号 Cookie"
        className="ios-input h-32 w-full resize-none rounded-xl px-4 py-3 font-mono text-xs"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500">当前 Cookie 长度：{value.length} 字符</p>
        <button
          type="button"
          onClick={() => setRevealed(false)}
          className="inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm font-bold text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-yellow-400"
        >
          <EyeOff className="h-4 w-4" aria-hidden="true" />
          隐藏
        </button>
      </div>
    </div>
  );
};
