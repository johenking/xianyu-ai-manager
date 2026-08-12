import React, { useEffect, useState } from 'react';

// 订单商品图：优先走应用媒体端点（服务端缓存，商品下架/外链失效后依然可用），
// 端点需要 Bearer 头而 <img> 带不了，因此 fetch→blob。
// 端点任意失败（HTTP 非 2xx 或网络错误）时降级为 CDN 直链（directSrc）展示；
// 仅当没有直链可降级、或展示中的直链/blob 自身加载失败时，才显示失效占位与重试入口。
// blob URL 按订单缓存于模块级 Map（页面生命周期内复用，条目数受分页上限约束）。
const objectUrlCache = new Map<string, string>();

export const clearOrderItemImageCache = () => {
  objectUrlCache.forEach((url) => {
    if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url);
  });
  objectUrlCache.clear();
};

type ImageFailureReason = 'not_saved' | 'source_expired' | 'unsupported_format';

const FAILURE_LABELS: Record<ImageFailureReason, string> = {
  not_saved: '图片未保存',
  source_expired: '图片源已失效',
  unsupported_format: '图片格式不支持',
};

const parseFailureReason = async (response: Response): Promise<ImageFailureReason> => {
  try {
    const payload = await response.json();
    const reason = payload?.detail?.reason;
    if (reason === 'not_saved' || reason === 'source_expired' || reason === 'unsupported_format') {
      return reason;
    }
  } catch {
    // 非 JSON 错误按源失效呈现。
  }
  return response.status === 422 ? 'unsupported_format' : 'source_expired';
};

interface OrderItemImageProps {
  orderId: string;
  /** 订单快照/目录兜底的 CDN 直链，应用端点失败时降级使用 */
  directSrc?: string;
  alt: string;
  className?: string;
  fallback: React.ReactNode;
}

const OrderItemImage: React.FC<OrderItemImageProps> = ({ orderId, directSrc, alt, className, fallback }) => {
  // appSrc 只保存应用端点产出的 blob URL；直链展示统一由渲染阶段的 directSrc 兜底
  const [appSrc, setAppSrc] = useState<string | undefined>(() => objectUrlCache.get(orderId));
  // 端点失败原因：有直链时先降级直链并保留该原因，供直链也失效时展示占位
  const [endpointFailure, setEndpointFailure] = useState<ImageFailureReason | null>(null);
  // 展示中的 <img>（blob 或直链）触发 onError 时快照的占位原因
  const [displayFailure, setDisplayFailure] = useState<ImageFailureReason | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setEndpointFailure(null);
    setDisplayFailure(null);
    if (!orderId || typeof fetch !== 'function' || objectUrlCache.has(orderId)) {
      setAppSrc(objectUrlCache.get(orderId));
      return;
    }
    let token: string | null = null;
    try {
      token = window.localStorage?.getItem?.('auth_token') ?? null;
    } catch {
      token = null;
    }
    if (!token) {
      // 未登录/测试降级：清掉可能残留的 blob，渲染阶段直接用 CDN 直链。
      setAppSrc(undefined);
      return;
    }
    const controller = new AbortController();
    fetch(`/api/orders/${encodeURIComponent(orderId)}/item-image`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const reason = await parseFailureReason(response);
          if (controller.signal.aborted) return;
          // 端点失败只记录原因：有直链时渲染阶段降级直链，无直链时才据此显示占位
          setEndpointFailure(reason);
          return;
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        objectUrlCache.set(orderId, url);
        setAppSrc(url);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setEndpointFailure('source_expired');
        }
      });
    return () => controller.abort();
  }, [attempt, directSrc, orderId]);

  // 占位仅两种来源：展示中的图片真实加载失败，或端点失败且没有直链可降级
  const failureReason = displayFailure ?? (directSrc ? null : endpointFailure);
  if (failureReason) {
    const label = FAILURE_LABELS[failureReason];
    return (
      <button
        type="button"
        aria-label={`${label}，点击重试`}
        title={`${label}，点击重试`}
        className="w-full h-full flex flex-col items-center justify-center gap-0.5 text-[10px] leading-tight text-gray-500 bg-gray-50"
        onClick={() => {
          // 清空全部状态，重走“端点 → 降级直链”完整链路
          setEndpointFailure(null);
          setDisplayFailure(null);
          setAppSrc(undefined);
          setAttempt((value) => value + 1);
        }}
      >
        {fallback}
        <span>{label}</span>
        <span className="text-blue-600">重试</span>
      </button>
    );
  }

  const src = appSrc || directSrc;
  if (!src) return <>{fallback}</>;
  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      // blob 解码失败按格式不支持；直链失败沿用端点原因，无端点原因则视为源失效
      onError={() => setDisplayFailure(appSrc ? 'unsupported_format' : endpointFailure ?? 'source_expired')}
    />
  );
};

export default OrderItemImage;
