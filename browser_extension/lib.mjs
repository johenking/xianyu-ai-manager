export const CONSOLE_ORIGIN = 'https://xianyu.cxywjx.top';
export const PUBLIC_IMPORT_URL = `${CONSOLE_ORIGIN}/api/browser-extension/import`;
export const ALLOWED_SUFFIXES = ['goofish.com', 'taobao.com'];

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

export function isAllowedHost(hostname) {
  const host = String(hostname || '').toLowerCase().replace(/^\.+|\.+$/g, '');
  return ALLOWED_SUFFIXES.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

export function isAllowedCookie(cookie) {
  const domain = String(cookie?.domain || '').toLowerCase().replace(/^\.+|\.+$/g, '');
  return isAllowedHost(domain);
}

export function selectCookieStore(stores, tabId) {
  return (stores || []).find((store) => (store.tabIds || []).includes(tabId)) || null;
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
