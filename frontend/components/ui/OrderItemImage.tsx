import React, { useEffect, useState } from 'react';

type ImageFailureReason = 'not_saved' | 'source_expired' | 'unsupported_format';
type ProxyImageResult = { src: string; failure?: never } | { src?: never; failure: ImageFailureReason };

interface ProxyImageRequest {
  key: string;
  orderId: string;
  token: string;
  controller: AbortController;
  consumers: number;
  state: 'queued' | 'active' | 'settled';
  promise: Promise<ProxyImageResult>;
  resolve: (result: ProxyImageResult) => void;
  reject: (reason: unknown) => void;
}

const MAX_PROXY_CONCURRENCY = 4;
const FAILURE_TTL_MS: Record<ImageFailureReason, number> = {
  not_saved: 5 * 60_000,
  source_expired: 30_000,
  unsupported_format: 5 * 60_000,
};
const objectUrlCache = new Map<string, string>();
const failureCache = new Map<string, { reason: ImageFailureReason; expiresAt: number }>();
const proxyRequests = new Map<string, ProxyImageRequest>();
const proxyQueue: ProxyImageRequest[] = [];
let activeProxyRequests = 0;
let cacheAuthToken: string | null = null;

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

const cacheFailure = (key: string, reason: ImageFailureReason) => {
  failureCache.set(key, { reason, expiresAt: Date.now() + FAILURE_TTL_MS[reason] });
};

const cachedFailure = (key: string): ImageFailureReason | null => {
  const cached = failureCache.get(key);
  if (!cached) return null;
  if (cached.expiresAt <= Date.now()) {
    failureCache.delete(key);
    return null;
  }
  return cached.reason;
};

const finishProxyRequest = (request: ProxyImageRequest) => {
  if (request.state === 'active') activeProxyRequests = Math.max(0, activeProxyRequests - 1);
  request.state = 'settled';
  if (proxyRequests.get(request.key) === request) proxyRequests.delete(request.key);
};

const pumpProxyQueue = () => {
  while (activeProxyRequests < MAX_PROXY_CONCURRENCY && proxyQueue.length) {
    const request = proxyQueue.shift();
    if (!request || request.state !== 'queued') continue;
    if (request.consumers <= 0 || request.controller.signal.aborted) {
      request.state = 'settled';
      if (proxyRequests.get(request.key) === request) proxyRequests.delete(request.key);
      request.reject(new DOMException('Aborted', 'AbortError'));
      continue;
    }

    request.state = 'active';
    activeProxyRequests += 1;
    void (async () => {
      try {
        const response = await fetch(
          `/api/orders/${encodeURIComponent(request.orderId)}/item-image`,
          {
            headers: { Authorization: `Bearer ${request.token}` },
            signal: request.controller.signal,
          },
        );
        if (!response.ok) {
          const reason = await parseFailureReason(response);
          if (request.controller.signal.aborted) {
            throw new DOMException('Aborted', 'AbortError');
          }
          cacheFailure(request.key, reason);
          request.resolve({ failure: reason });
          return;
        }
        const blob = await response.blob();
        if (request.controller.signal.aborted) {
          throw new DOMException('Aborted', 'AbortError');
        }
        const url = URL.createObjectURL(blob);
        objectUrlCache.set(request.key, url);
        failureCache.delete(request.key);
        request.resolve({ src: url });
      } catch (error) {
        if (request.controller.signal.aborted) {
          request.reject(error);
          return;
        }
        cacheFailure(request.key, 'source_expired');
        request.resolve({ failure: 'source_expired' });
      } finally {
        finishProxyRequest(request);
        pumpProxyQueue();
      }
    })();
  }
};

const resetImageCaches = () => {
  objectUrlCache.forEach((url) => {
    if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url);
  });
  objectUrlCache.clear();
  failureCache.clear();
  proxyRequests.forEach((request) => request.controller.abort());
  proxyQueue.splice(0).forEach((request) => {
    if (request.state !== 'queued') return;
    request.state = 'settled';
    request.reject(new DOMException('Aborted', 'AbortError'));
  });
  proxyRequests.clear();
};

export const clearOrderItemImageCache = () => {
  resetImageCaches();
  cacheAuthToken = null;
};

const imageCacheKey = (orderId: string, token: string) => {
  if (cacheAuthToken !== token) {
    resetImageCaches();
    cacheAuthToken = token;
  }
  return orderId;
};

const acquireProxyImage = (
  orderId: string,
  token: string,
  force: boolean,
): { promise: Promise<ProxyImageResult>; release: () => void } => {
  const key = imageCacheKey(orderId, token);
  const objectUrl = objectUrlCache.get(key);
  if (objectUrl) {
    return { promise: Promise.resolve({ src: objectUrl }), release: () => undefined };
  }
  if (force) failureCache.delete(key);
  const failure = cachedFailure(key);
  if (failure) {
    return { promise: Promise.resolve({ failure }), release: () => undefined };
  }

  let request = proxyRequests.get(key);
  if (!request) {
    let resolveRequest: (result: ProxyImageResult) => void = () => undefined;
    let rejectRequest: (reason: unknown) => void = () => undefined;
    const promise = new Promise<ProxyImageResult>((resolve, reject) => {
      resolveRequest = resolve;
      rejectRequest = reject;
    });
    request = {
      key,
      orderId,
      token,
      controller: new AbortController(),
      consumers: 0,
      state: 'queued',
      promise,
      resolve: resolveRequest,
      reject: rejectRequest,
    };
    proxyRequests.set(key, request);
    proxyQueue.push(request);
  }

  request.consumers += 1;
  pumpProxyQueue();
  let released = false;
  return {
    promise: request.promise,
    release: () => {
      if (released || request?.state === 'settled') return;
      released = true;
      request.consumers = Math.max(0, request.consumers - 1);
      if (request.consumers > 0) return;
      request.controller.abort();
      if (request.state === 'queued') {
        request.state = 'settled';
        if (proxyRequests.get(request.key) === request) proxyRequests.delete(request.key);
        request.reject(new DOMException('Aborted', 'AbortError'));
      }
    },
  };
};

interface OrderItemImageProps {
  orderId: string;
  /** 订单快照/目录里的 CDN 直链；失败后才回退到鉴权代理 */
  directSrc?: string;
  alt: string;
  className?: string;
  fallback: React.ReactNode;
}

const OrderItemImage: React.FC<OrderItemImageProps> = ({ orderId, directSrc, alt, className, fallback }) => {
  const [src, setSrc] = useState<string | undefined>(() => directSrc);
  const [failure, setFailure] = useState<ImageFailureReason | null>(null);
  const [proxyRequested, setProxyRequested] = useState(() => !directSrc);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setSrc(directSrc);
    setFailure(null);
    setProxyRequested(!directSrc);
    setAttempt(0);
  }, [directSrc, orderId]);

  useEffect(() => {
    if (!proxyRequested || !orderId || typeof fetch !== 'function') return undefined;
    let token: string | null = null;
    try {
      token = window.localStorage?.getItem?.('auth_token') ?? null;
    } catch {
      token = null;
    }
    if (!token) {
      setFailure('source_expired');
      return undefined;
    }
    let active = true;
    const subscription = acquireProxyImage(orderId, token, attempt > 0);
    void subscription.promise
      .then((result) => {
        if (!active) return;
        if (result.src) {
          setSrc(result.src);
          setFailure(null);
        } else {
          setSrc(undefined);
          setFailure(result.failure);
        }
      })
      .catch(() => {
        if (active) setFailure('source_expired');
      });
    return () => {
      active = false;
      subscription.release();
    };
  }, [attempt, orderId, proxyRequested]);

  if (failure) {
    const label = FAILURE_LABELS[failure];
    return (
      <button
        type="button"
        aria-label={`${label}，点击重试`}
        title={`${label}，点击重试`}
        className="w-full h-full flex flex-col items-center justify-center gap-0.5 text-[10px] leading-tight text-gray-500 bg-gray-50"
        onClick={() => {
          setFailure(null);
          setAttempt((value) => value + 1);
          if (directSrc) {
            setSrc(directSrc);
            setProxyRequested(false);
          } else {
            setSrc(undefined);
            setProxyRequested(true);
          }
        }}
      >
        {fallback}
        <span>{label}</span>
        <span className="text-blue-600">重试</span>
      </button>
    );
  }

  if (!src) return <>{fallback}</>;
  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => {
        if (directSrc && src === directSrc) {
          setSrc(undefined);
          setFailure(null);
          setProxyRequested(true);
          return;
        }
        setSrc(undefined);
        setFailure('unsupported_format');
      }}
    />
  );
};

export default OrderItemImage;
