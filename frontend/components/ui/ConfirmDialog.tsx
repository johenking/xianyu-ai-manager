import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, HelpCircle } from 'lucide-react';

export interface ConfirmDialogOptions {
  title: string;
  message?: string;
  confirmText?: string;
  cancelText?: string;
  tone?: 'default' | 'danger';
}

interface PendingConfirm extends ConfirmDialogOptions {
  id: number;
  resolve: (value: boolean) => void;
}

type Listener = (pending: PendingConfirm | null) => void;

let current: PendingConfirm | null = null;
let listeners: Listener[] = [];
let seq = 0;

const notify = () => listeners.forEach((listener) => listener(current));

/**
 * 全站统一确认弹窗（替代原生 window.confirm）。
 * 返回 Promise<boolean>：确认 true，取消/关闭/Escape false。
 * 需在应用根部挂载 <ConfirmDialogHost />（index.tsx 已挂载；组件测试中需自行渲染）。
 */
export const confirmDialog = (options: ConfirmDialogOptions): Promise<boolean> =>
  new Promise((resolve) => {
    // 同一时刻只保留一个确认框：新请求到来时旧请求按取消处理
    current?.resolve(false);
    current = { confirmText: '确认', cancelText: '取消', tone: 'default', ...options, id: ++seq, resolve };
    notify();
  });

const settle = (value: boolean) => {
  if (!current) return;
  const pending = current;
  current = null;
  notify();
  pending.resolve(value);
};

/** 测试辅助：清空未决确认框（按取消处理）。 */
export const clearConfirmDialogs = () => settle(false);

export const ConfirmDialogHost: React.FC = () => {
  const [pending, setPending] = useState<PendingConfirm | null>(current);
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const listener: Listener = (value) => setPending(value);
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((item) => item !== listener);
    };
  }, []);

  useEffect(() => {
    if (!pending) return undefined;
    cancelButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        settle(false);
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [pending]);

  if (!pending) return null;

  const danger = pending.tone === 'danger';
  const titleId = `confirm-dialog-title-${pending.id}`;

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm animate-fade-in"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={(event) => {
        if (event.target === event.currentTarget) settle(false);
      }}
    >
      <div className="w-full max-w-sm rounded-3xl bg-white p-6 shadow-2xl animate-slide-up">
        <div className="flex items-start gap-4">
          <span
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${
              danger ? 'bg-red-50 text-red-500' : 'bg-[#FFF8B8] text-[#7C5D00]'
            }`}
            aria-hidden="true"
          >
            {danger ? <AlertTriangle className="h-5 w-5" /> : <HelpCircle className="h-5 w-5" />}
          </span>
          <div className="min-w-0 flex-1">
            <h3 id={titleId} className="text-lg font-extrabold leading-6 text-gray-900">{pending.title}</h3>
            {pending.message ? <p className="mt-2 text-sm leading-6 text-gray-500">{pending.message}</p> : null}
          </div>
        </div>
        <div className="mt-6 flex gap-3">
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={() => settle(false)}
            className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-3 font-bold text-gray-700 transition-colors hover:bg-gray-50"
          >
            {pending.cancelText}
          </button>
          <button
            type="button"
            onClick={() => settle(true)}
            className={`flex-1 rounded-xl px-4 py-3 font-bold transition-colors ${
              danger
                ? 'bg-red-600 text-white hover:bg-red-700'
                : 'ios-btn-primary'
            }`}
          >
            {pending.confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ConfirmDialogHost;
