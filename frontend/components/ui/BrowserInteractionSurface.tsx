import React, { useRef, useState } from 'react';

import type { BrowserInteractionAction } from '../../services/api';
import AuthenticatedImage from './AuthenticatedImage';


interface BrowserInteractionSurfaceProps {
  imageUrl: string;
  frameRevision: number;
  onInteract: (action: BrowserInteractionAction) => Promise<unknown>;
  disabled?: boolean;
}

interface GestureDraft {
  pointerId: number;
  startedAt: number;
  points: Array<{ x: number; y: number }>;
}

const clamp = (value: number, minimum: number, maximum: number) => (
  Math.min(maximum, Math.max(minimum, value))
);

const normalizedPoint = (
  event: React.PointerEvent<HTMLImageElement>,
): { x: number; y: number } | null => {
  const bounds = event.currentTarget.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return null;
  return {
    x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
    y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
  };
};

const BrowserInteractionSurface: React.FC<BrowserInteractionSurfaceProps> = ({
  imageUrl,
  frameRevision,
  onInteract,
  disabled = false,
}) => {
  const gestureRef = useRef<GestureDraft | null>(null);
  const [text, setText] = useState('');
  const [pendingCount, setPendingCount] = useState(0);
  const [error, setError] = useState('');
  const isReady = Boolean(imageUrl && frameRevision > 0 && !disabled);
  const versionedImageUrl = imageUrl
    ? `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}revision=${frameRevision}`
    : '';

  const send = async (action: BrowserInteractionAction) => {
    setError('');
    setPendingCount((count) => count + 1);
    try {
      await onInteract(action);
    } catch (interactionError) {
      setError(
        interactionError instanceof Error
          ? interactionError.message
          : '操作未送达，请在最新画面上重试',
      );
    } finally {
      setPendingCount((count) => Math.max(0, count - 1));
    }
  };

  const appendPoint = (
    draft: GestureDraft,
    event: React.PointerEvent<HTMLImageElement>,
  ) => {
    const point = normalizedPoint(event);
    if (!point || draft.points.length >= 80) return;
    const previous = draft.points[draft.points.length - 1];
    if (previous && previous.x === point.x && previous.y === point.y) return;
    draft.points.push(point);
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLImageElement>) => {
    if (!isReady) return;
    event.preventDefault();
    const pointerId = event.pointerId ?? 0;
    const draft: GestureDraft = {
      pointerId,
      startedAt: performance.now(),
      points: [],
    };
    appendPoint(draft, event);
    gestureRef.current = draft;
    event.currentTarget.setPointerCapture?.(pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLImageElement>) => {
    const draft = gestureRef.current;
    if (!draft || draft.pointerId !== (event.pointerId ?? 0)) return;
    event.preventDefault();
    appendPoint(draft, event);
  };

  const finishGesture = (event: React.PointerEvent<HTMLImageElement>) => {
    const draft = gestureRef.current;
    if (!draft || draft.pointerId !== (event.pointerId ?? 0)) return;
    event.preventDefault();
    appendPoint(draft, event);
    gestureRef.current = null;
    event.currentTarget.releasePointerCapture?.(draft.pointerId);
    if (!draft.points.length) return;
    void send({
      kind: 'gesture',
      frame_revision: frameRevision,
      points: draft.points,
      duration_ms: Math.round(clamp(
        performance.now() - draft.startedAt,
        0,
        5000,
      )),
    });
  };

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (!isReady) return;
    event.preventDefault();
    const deltaX = clamp(event.deltaX, -2000, 2000);
    const deltaY = clamp(event.deltaY, -2000, 2000);
    if (deltaX === 0 && deltaY === 0) return;
    void send({
      kind: 'wheel',
      frame_revision: frameRevision,
      delta_x: deltaX,
      delta_y: deltaY,
    });
  };

  const handleTextSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const value = text.slice(0, 128);
    setText('');
    if (!isReady || !value) return;
    void send({
      kind: 'text',
      frame_revision: frameRevision,
      text: value,
    });
  };

  const sendKey = (key: 'Enter' | 'Backspace' | 'Tab' | 'Escape') => {
    if (!isReady) return;
    void send({
      kind: 'key',
      frame_revision: frameRevision,
      key,
    });
  };

  return (
    <section
      aria-label="闲鱼登录页面远程操作"
      className="rounded-2xl border border-gray-200 bg-white p-3 shadow-sm sm:p-4"
    >
      <div
        className="overflow-hidden rounded-xl border border-gray-200 bg-gray-50"
        onWheel={handleWheel}
      >
        <AuthenticatedImage
          src={versionedImageUrl}
          alt="闲鱼登录验证画面"
          draggable={false}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishGesture}
          onPointerCancel={() => {
            gestureRef.current = null;
          }}
          className="mx-auto block max-h-[560px] w-full select-none object-contain"
          style={{ touchAction: 'none' }}
        />
      </div>

      <p className="mt-3 text-xs leading-5 text-gray-500">
        可直接点击、拖动滑块或滚动画面。若出现手机扫码或人脸确认，请按闲鱼页面提示在手机上完成。
      </p>

      <form className="mt-3 flex gap-2" onSubmit={handleTextSubmit}>
        <label className="sr-only" htmlFor={`browser-text-${frameRevision}`}>
          向闲鱼页面输入文字
        </label>
        <input
          id={`browser-text-${frameRevision}`}
          value={text}
          onChange={(event) => setText(event.target.value.slice(0, 128))}
          disabled={!isReady}
          autoComplete="one-time-code"
          inputMode="text"
          placeholder="短信验证码或页面文字"
          className="min-h-11 min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3 text-sm text-gray-900 outline-none transition focus:border-gray-400 focus:ring-2 focus:ring-gray-200 disabled:cursor-not-allowed disabled:bg-gray-100"
        />
        <button
          type="submit"
          aria-label="发送到闲鱼页面"
          disabled={!isReady || !text || pendingCount > 0}
          className="min-h-11 rounded-xl bg-gray-900 px-4 text-sm font-semibold text-white transition hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          发送
        </button>
      </form>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([
          ['Enter', '回车'],
          ['Backspace', '退格'],
          ['Tab', '切换焦点'],
          ['Escape', '取消'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-label={`发送${label}键`}
            disabled={!isReady || pendingCount > 0}
            onClick={() => sendKey(key)}
            className="min-h-11 rounded-xl border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-300 disabled:cursor-not-allowed disabled:text-gray-300"
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  );
};

export default BrowserInteractionSurface;
