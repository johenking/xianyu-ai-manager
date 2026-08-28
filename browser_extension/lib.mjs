export const CONSOLE_ORIGIN = 'https://xianyu.cxywjx.top';
export const PUBLIC_IMPORT_URL = `${CONSOLE_ORIGIN}/api/browser-extension/import`;
export const ALLOWED_SUFFIXES = ['goofish.com', 'taobao.com'];

export function bytesToBase64Url(bytes) {
  const binary = Array.from(new Uint8Array(bytes), (value) => String.fromCharCode(value)).join('');
  return btoa(binary).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

export function browserFamilyFromUserAgent(userAgent) {
  return /Edg\//.test(String(userAgent || '')) ? 'edge' : 'chrome';
}

export function isAllowedImportUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:'
      && url.origin === CONSOLE_ORIGIN
      && url.pathname === '/api/browser-extension/import'
      && !url.search
      && !url.hash;
  } catch (_) {
    return false;
  }
}

export function isConsolePageUrl(value) {
  try {
    return new URL(String(value || '')).origin === CONSOLE_ORIGIN;
  } catch (_) {
    return false;
  }
}

export function isAllowedHost(hostname) {
  const host = String(hostname || '').toLowerCase().replace(/^\.+|\.+$/g, '');
  return ALLOWED_SUFFIXES.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

export function isAllowedCookie(cookie) {
  const domain = String(cookie?.domain || '').toLowerCase().replace(/^\.+|\.+$/g, '');
  return isAllowedHost(domain);
}

export function selectCookieStore(stores, tabId) {
  const list = stores || [];
  const matched = list.find((store) => (store.tabIds || []).includes(tabId));
  if (matched) return matched;
  return list.find((store) => store && store.incognito !== true) || list[0] || null;
}

export const COOKIE_PARTITION_SITES = [
  'https://www.goofish.com',
  'https://m.goofish.com',
  'https://h5.m.goofish.com',
  'https://www.taobao.com',
  'https://login.taobao.com',
];

export function cookieQueryPlans(storeId) {
  const plans = [];
  for (const domain of ALLOWED_SUFFIXES) {
    plans.push({ storeId, domain });
    for (const topLevelSite of COOKIE_PARTITION_SITES) {
      if (!topLevelSite.includes(domain)) continue;
      plans.push({ storeId, domain, partitionKey: { topLevelSite } });
    }
  }
  return plans;
}

export function mergeCookieRecords(cookieLists) {
  const seen = new Map();
  for (const cookies of cookieLists || []) {
    for (const cookie of cookies || []) {
      const partition = cookie?.partitionKey?.topLevelSite || '';
      const key = [
        String(cookie?.name || ''),
        String(cookie?.domain || ''),
        String(cookie?.path || '/'),
        partition,
      ].join('\0');
      seen.set(key, cookie);
    }
  }
  return [...seen.values()];
}

export async function collectAllowedCookies(storeId, getAll) {
  const lists = [];
  for (const plan of cookieQueryPlans(storeId)) {
    try {
      lists.push(await getAll(plan));
    } catch (_) {
      // Older Chrome builds reject partitionKey; unpartitioned queries still run.
    }
  }
  return mergeCookieRecords(lists);
}

export function formatConsoleError(result, status) {
  const detail = result?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
    return detail.message;
  }
  if (typeof result?.message === 'string' && result.message.trim()) return result.message;
  return `监控台返回 ${status}`;
}

export function serializeCookie(cookie) {
  const serialized = {
    name: String(cookie.name || ''),
    value: String(cookie.value || ''),
    domain: String(cookie.domain || ''),
    path: String(cookie.path || '/'),
    secure: Boolean(cookie.secure),
    httpOnly: Boolean(cookie.httpOnly),
    sameSite: cookie.sameSite || null,
    expirationDate: Number.isFinite(cookie.expirationDate) ? cookie.expirationDate : null,
    storeId: cookie.storeId ? String(cookie.storeId) : null,
    partitionKey: cookie.partitionKey
      ? {
          topLevelSite: cookie.partitionKey.topLevelSite || null,
          hasCrossSiteAncestor: Boolean(cookie.partitionKey.hasCrossSiteAncestor),
        }
      : null,
  };
  return serialized;
}

export function parsePairingBundle(rawValue) {
  const raw = String(rawValue || '').trim();
  if (!raw) throw new Error('请粘贴配对信息');
  try {
    const parsed = JSON.parse(raw);
    const protocolVersion = Number(parsed.protocol_version);
    const pairingId = String(parsed.pairing_id || '').trim();
    const pairingToken = String(parsed.pairing_token || '').trim();
    const importUrl = String(parsed.import_url || '').trim();
    const consoleOrigin = String(parsed.console_origin || '').trim();
    const expiresAt = Number(parsed.expires_at);
    if (
      protocolVersion !== 2
      || !pairingId
      || pairingToken.length < 32
      || !isAllowedImportUrl(importUrl)
      || consoleOrigin !== CONSOLE_ORIGIN
      || !Number.isFinite(expiresAt)
    ) {
      throw new Error('配对信息格式不正确，请重新复制');
    }
    if (expiresAt <= Date.now() / 1000) {
      throw new Error('配对已过期，请在监控台重新创建');
    }
    return {
      protocolVersion,
      pairingId,
      pairingToken,
      importUrl,
      consoleOrigin,
      expiresAt,
    };
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('配对')) throw error;
  }
  throw new Error('配对信息格式不正确，请重新复制');
}

export function buildImportPayload(pairing, cookies, userAgent) {
  return {
    protocol_version: pairing.protocolVersion,
    pairing_id: pairing.pairingId,
    pairing_token: pairing.pairingToken,
    cookies: (cookies || []).filter(isAllowedCookie).map(serializeCookie),
    user_agent: String(userAgent || ''),
  };
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function buildClientImportPayload(session, cookies, userAgent) {
  return {
    session_id: String(session.sessionId || ''),
    device_id: String(session.deviceId || ''),
    mode: String(session.mode || ''),
    challenge_id: String(session.challengeId || ''),
    signature: String(session.signature || ''),
    cookies: (cookies || []).filter(isAllowedCookie).map(serializeCookie),
    user_agent: String(userAgent || ''),
  };
}

export function cookieFingerprint(cookies) {
  const entries = (cookies || [])
    .filter(isAllowedCookie)
    .map((cookie) => [String(cookie.name || ''), String(cookie.value || ''), String(cookie.domain || '')])
    .sort((left, right) => left.join('\0').localeCompare(right.join('\0')));
  return entries.map((entry) => entry.join('=')).join(';');
}
