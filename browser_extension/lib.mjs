export const LOCAL_IMPORT_URL = 'http://127.0.0.1:8091/api/browser-extension/import';
export const ALLOWED_SUFFIXES = ['goofish.com', 'taobao.com'];

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
    const pairingId = String(parsed.pairing_id || '').trim();
    const pairingCode = String(parsed.pairing_code || '').trim();
    if (pairingId && pairingCode) {
      return { pairingId, pairingCode };
    }
  } catch (_) {
    // The compact pairing format below is also supported.
  }
  const separator = raw.indexOf(':');
  if (separator > 0) {
    const pairingId = raw.slice(0, separator).trim();
    const pairingCode = raw.slice(separator + 1).trim();
    if (pairingId && pairingCode) return { pairingId, pairingCode };
  }
  throw new Error('配对信息格式不正确，请重新复制');
}

export function buildImportPayload(pairing, cookies, userAgent) {
  return {
    pairing_id: pairing.pairingId,
    pairing_code: pairing.pairingCode,
    cookies: (cookies || []).filter(isAllowedCookie).map(serializeCookie),
    user_agent: String(userAgent || ''),
  };
}
