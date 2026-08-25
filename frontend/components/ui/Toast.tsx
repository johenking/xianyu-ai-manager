import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, CheckCircle2, X } from 'lucide-react';

export type ToastKind = 'success' | 'error';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}

type ToastListener = (toasts: ToastItem[]) => void;

// 模块级轻量存储：任意组件可直接 pushToast，无需 Context 接线
let toastState: ToastItem[] = [];
let toastListeners: ToastListener[] = [];
let toastSeq = 0;
const dismissTimers = new Map<number, ReturnType<typeof setTimeout>>();

const MAX_VISIBLE_TOASTS = 4;
const SUCCESS_DURATION_MS = 3200;
const ERROR_DURATION_MS = 5200;

const emitToasts = () => {
  const snapshot = [...toastState];
  toastListeners.forEach((listener) => listener(snapshot));
};

export const dismissToast = (id: number) => {
  const timer = dismissTimers.get(id);
  if (timer) {
    clearTimeout(timer);
    dismissTimers.delete(id);
  }
  if (!toastState.some((toast) => toast.id === id)) return;
  toastState = toastState.filter((toast) => toast.id !== id);
  emitToasts();
};

export const clearToasts = () => {
  dismissTimers.forEach((timer) => clearTimeout(timer));
  dismissTimers.clear();
  if (toastState.length === 0) return;
  toastState = [];
  emitToasts();
};

export const pushToast = (kind: ToastKind, text: string, durationMs?: number): number => {
  const content = (text || '').trim();
  if (!content) return -1;
  toastSeq += 1;
  const id = toastSeq;
  toastState = [...toastState, { id, kind, text: content }];
  // 超过上限时移除最旧一条，并清理其定时器
  while (toastState.length > MAX_VISIBLE_TOASTS) {
    const [oldest] = toastState;
    const timer = dismissTimers.get(oldest.id);
    if (timer) {
      clearTimeout(timer);
      dismissTimers.delete(oldest.id);
    }
    toastState = toastState.slice(1);
  }
  emitToasts();
  const duration = durationMs ?? (kind === 'success' ? SUCCESS_DURATION_MS : ERROR_DURATION_MS);
  dismissTimers.set(id, setTimeout(() => dismissToast(id), duration));
  return id;
};

// 全局弹窗出口：固定右上角、层级高于弹窗遮罩(9999)，空列表时不渲染任何节点
export const ToastViewport: React.FC = () => {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const listener: ToastListener = (next) => setItems(next);
    toastListeners.push(listener);
    listener([...toastState]);
    return () => {
      toastListeners = toastListeners.filter((entry) => entry !== listener);
    };
  }, []);

  if (items.length === 0) return null;

  return createPortal(
    <div
      className="fixed top-4 right-4 z-[10050] flex w-[min(92vw,360px)] flex-col gap-2 pointer-events-none"
      role="status"
      aria-live="polite"
    >
      {items.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto flex items-start gap-2 rounded-xl px-4 py-3 text-white shadow-lg animate-fade-in ${
            toast.kind === 'success' ? 'bg-green-600' : 'bg-red-600'
          }`}
        >
          {toast.kind === 'success'
            ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
          <span className="min-w-0 flex-1 break-words text-sm font-bold leading-5">{toast.text}</span>
          <button
            type="button"
            onClick={() => dismissToast(toast.id)}
            className="rounded-md p-0.5 text-white/80 hover:bg-white/20 hover:text-white"
            aria-label="关闭提醒"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
    </div>,
    document.body
  );
};

export default ToastViewport;
