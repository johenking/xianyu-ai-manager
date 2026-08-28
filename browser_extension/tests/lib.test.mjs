import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  ALLOWED_SUFFIXES,
  buildImportPayload,
  buildClientImportPayload,
  browserFamilyFromUserAgent,
  bytesToBase64Url,
  canonicalJson,
  collectAllowedCookies,
  cookieFingerprint,
  cookieQueryPlans,
  formatConsoleError,
  isAllowedImportUrl,
  isAllowedCookie,
  isConsolePageUrl,
  mergeCookieRecords,
  parsePairingBundle,
  selectCookieStore,
  serializeCookie,
} from '../lib.mjs';

test('selects the Cookie Store that owns the active tab', () => {
  const selected = selectCookieStore(
    [
      { id: 'default', tabIds: [1, 2] },
      { id: 'incognito', tabIds: [8] },
    ],
    8,
  );
  assert.equal(selected.id, 'incognito');
});

test('falls back to the default Cookie Store when the tab is not listed yet', () => {
  const selected = selectCookieStore(
    [
      { id: '0', tabIds: [], incognito: false },
      { id: '1', tabIds: [99], incognito: true },
    ],
    12,
  );
  assert.equal(selected.id, '0');
});

test('queries cookies by allowlisted domain instead of store-wide getAll', () => {
  const plans = cookieQueryPlans('0');
  assert.equal(plans.some((plan) => !plan.domain), false);
  assert.equal(plans.some((plan) => plan.storeId !== '0'), false);
  for (const suffix of ALLOWED_SUFFIXES) {
    assert.equal(plans.some((plan) => plan.domain === suffix && !plan.partitionKey), true);
    assert.equal(
      plans.some((plan) => plan.domain === suffix && plan.partitionKey?.topLevelSite?.includes(suffix)),
      true,
    );
  }
});

test('merges unpartitioned and partitioned cookies without dropping unb', () => {
  const merged = mergeCookieRecords([
    [{ name: 'unb', value: '123', domain: '.taobao.com', path: '/' }],
    [{ name: 'cookie2', value: 'session', domain: '.goofish.com', path: '/', partitionKey: { topLevelSite: 'https://www.goofish.com' } }],
    [{ name: 'unb', value: '123', domain: '.taobao.com', path: '/' }],
  ]);
  assert.equal(merged.length, 2);
  assert.equal(merged.some((cookie) => cookie.name === 'unb'), true);
  assert.equal(merged.some((cookie) => cookie.name === 'cookie2'), true);
});

test('collectAllowedCookies asks Chrome by domain and keeps partitioned records', async () => {
  const calls = [];
  const cookies = await collectAllowedCookies('0', async (details) => {
    calls.push(details);
    if (details.partitionKey) {
      return [{ name: 'cookie2', value: 'part', domain: '.goofish.com', path: '/', partitionKey: details.partitionKey }];
    }
    if (details.domain === 'taobao.com') {
      return [{ name: 'unb', value: '123', domain: '.taobao.com', path: '/' }];
    }
    return [];
  });
  assert.equal(calls.some((details) => details.domain && !details.url), true);
  assert.equal(calls.some((details) => details.partitionKey), true);
  assert.equal(cookies.some((cookie) => cookie.name === 'unb' && cookie.value === '123'), true);
  assert.equal(cookies.some((cookie) => cookie.name === 'cookie2'), true);
});

test('surfaces FastAPI object details instead of [object Object]', () => {
  assert.equal(
    formatConsoleError({ detail: { code: 'pairing_expired', message: '配对已过期' } }, 410),
    '配对已过期',
  );
  assert.equal(
    formatConsoleError({ detail: [{ loc: ['body'], msg: 'Field required', type: 'missing' }] }, 422),
    'Field required',
  );
  assert.equal(formatConsoleError({ detail: '配对码错误' }, 403), '配对码错误');
});

test('encodes URL-safe device proof material and distinguishes Edge', () => {
  assert.equal(bytesToBase64Url(Uint8Array.from([251, 255, 255])), '-___');
  assert.equal(browserFamilyFromUserAgent('Mozilla/5.0 Edg/138.0.0.0'), 'edge');
  assert.equal(browserFamilyFromUserAgent('Mozilla/5.0 Chrome/138.0.0.0'), 'chrome');
});

test('serializes structured cookie metadata including partition key', () => {
  const serialized = serializeCookie({
    name: 'cookie2',
    value: 'secret-value',
    domain: '.goofish.com',
    path: '/',
    secure: true,
    httpOnly: true,
    sameSite: 'no_restriction',
    expirationDate: 2_000_000_000,
    storeId: 'default',
    partitionKey: {
      topLevelSite: 'https://goofish.com',
      hasCrossSiteAncestor: false,
    },
  });
  assert.deepEqual(serialized.partitionKey, {
    topLevelSite: 'https://goofish.com',
    hasCrossSiteAncestor: false,
  });
  assert.equal(serialized.httpOnly, true);
  assert.equal(serialized.value, 'secret-value');
});

test('filters non-allowlisted cookie domains from an import payload', () => {
  const payload = buildImportPayload(
    { protocolVersion: 2, pairingId: 'pairing-id', pairingToken: 'T'.repeat(43) },
    [
      { name: 'unb', value: '123', domain: '.goofish.com', path: '/' },
      { name: 'private', value: 'other-site', domain: '.example.com', path: '/' },
    ],
    'Chrome UA',
  );
  assert.equal(payload.cookies.length, 1);
  assert.equal(payload.cookies[0].name, 'unb');
});

test('parses a versioned HTTPS pairing bundle without persistence', () => {
  assert.deepEqual(
    parsePairingBundle(JSON.stringify({
      protocol_version: 2,
      pairing_id: 'one',
      pairing_token: 'T'.repeat(43),
      import_url: 'https://xianyu.cxywjx.top/api/browser-extension/import',
      console_origin: 'https://xianyu.cxywjx.top',
      expires_at: 2_000_000_000,
    })),
    {
      protocolVersion: 2,
      pairingId: 'one',
      pairingToken: 'T'.repeat(43),
      importUrl: 'https://xianyu.cxywjx.top/api/browser-extension/import',
      consoleOrigin: 'https://xianyu.cxywjx.top',
      expiresAt: 2_000_000_000,
    },
  );
  assert.equal(isAllowedImportUrl('https://xianyu.cxywjx.top/api/browser-extension/import'), true);
  assert.equal(isAllowedImportUrl('http://xianyu.cxywjx.top/api/browser-extension/import'), false);
  assert.equal(isAllowedImportUrl('https://example.com/api/browser-extension/import'), false);
  assert.equal(isAllowedImportUrl('https://xianyu.cxywjx.top/other'), false);
  assert.equal(isConsolePageUrl('https://xianyu.cxywjx.top/accounts'), true);
  assert.equal(isConsolePageUrl('http://xianyu.cxywjx.top/accounts'), false);
  assert.equal(isConsolePageUrl('https://xianyu.cxywjx.top.evil.example/accounts'), false);
});

test('manifest provides a strict MV3 current-device bridge', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../manifest.json', import.meta.url), 'utf8'),
  );
  assert.deepEqual(
    [...manifest.permissions].sort(),
    ['activeTab', 'alarms', 'cookies', 'scripting', 'storage', 'tabs'],
  );
  assert.deepEqual(manifest.background, { service_worker: 'background.js', type: 'module' });
  assert.deepEqual(manifest.content_scripts[0].matches, ['https://xianyu.cxywjx.top/*']);
  assert.deepEqual(manifest.content_scripts[0].js, ['content.js']);
  assert.equal(manifest.version, '1.2.2');
  assert.equal(manifest.host_permissions.length, 5);
  for (const suffix of ALLOWED_SUFFIXES) {
    assert.equal(
      manifest.host_permissions.some((entry) => entry.includes(suffix)),
      true,
    );
  }
  assert.equal(
    manifest.host_permissions.some((entry) => entry === 'https://xianyu.cxywjx.top/*'),
    true,
  );
  assert.equal(manifest.host_permissions.some((entry) => entry.startsWith('http://')), false);
});

test('popup code never writes sensitive values to extension storage', async () => {
  const popup = await readFile(new URL('../popup.js', import.meta.url), 'utf8');
  assert.equal(/chrome\.storage|localStorage|sessionStorage/.test(popup), false);
  assert.equal(isAllowedCookie({ domain: '.taobao.com' }), true);
  assert.equal(isAllowedCookie({ domain: '.example.com' }), false);
  assert.match(popup, /collectAllowedCookies/);
  assert.match(popup, /formatConsoleError/);
  assert.equal(popup.includes('当前标签页不是闲鱼或淘宝官方页面'), false);
  assert.equal(/getAll\(\{ storeId:/.test(popup), false);
});

test('background keeps secrets out of persistent extension storage', async () => {
  const background = await readFile(new URL('../background.js', import.meta.url), 'utf8');
  const content = await readFile(new URL('../content.js', import.meta.url), 'utf8');
  assert.equal(/storage\.(local|sync)|localStorage|sessionStorage/.test(background), false);
  assert.equal(/storage\.(local|sync)|localStorage|sessionStorage/.test(content), false);
  assert.match(background, /storage\.session/);
  assert.match(background, /indexedDB/);
  assert.match(background, /generateKey\([\s\S]*false/);
  assert.match(background, /requireConsoleSender\(sender\)/);
  assert.equal(content.includes('https://xianyu.cxywjx.top'), true);
  assert.match(background, /injectConsoleBridgeIntoOpenTabs/);
  assert.match(background, /extensionVersion: chrome\.runtime\.getManifest\(\)\.version/);
  assert.match(background, /protocolVersion: BRIDGE_PROTOCOL_VERSION/);
  assert.match(background, /const LOGIN_URL = 'https:\/\/www\.goofish\.com\/login'/);
  assert.match(background, /chrome\.tabs\.create\(\{ url: LOGIN_URL, active: true \}\)/);
  assert.match(background, /chrome\.tabs\.update\(existingTab\.id, \{ active: true \}\)/);
  assert.match(background, /collectAllowedCookies/);
  assert.equal(/getAll\(\{ storeId:/.test(background), false);
  assert.match(background, /for \(const \[sessionId, session\] of Object\.entries\(sessions\)\)/);
  assert.match(background, /startedAt: Date\.now\(\) \/ 1000/);
  assert.match(content, /BRIDGE_INSTANCE_KEY/);
  assert.match(content, /extensionVersion: chrome\.runtime\.getManifest\(\)\.version/);
  assert.equal(/console\.(log|debug|info|warn|error)/.test(background), false);
});

test('builds a client import payload without a transferable session secret', () => {
  const payload = buildClientImportPayload({
    sessionId: 'session-1',
    deviceId: 'device_fixture_1234',
    mode: 'sms',
    challengeId: 'challenge-1',
    signature: 'signature',
  }, [
    { name: 'unb', value: '123', domain: '.goofish.com', path: '/' },
    { name: 'foreign', value: 'no', domain: '.example.com', path: '/' },
  ], 'Browser UA');
  assert.equal(payload.cookies.length, 1);
  assert.equal(payload.session_token, undefined);
  assert.equal(payload.device_id, 'device_fixture_1234');
  assert.equal(
    canonicalJson({ z: 1, a: { y: 2, x: 3 } }),
    '{"a":{"x":3,"y":2},"z":1}',
  );
});

test('fingerprints allowed Cookie state so renewal cannot submit a stale baseline', () => {
  const baseline = cookieFingerprint([
    { name: 'unb', value: '1', domain: '.goofish.com' },
    { name: 'cookie2', value: 'old', domain: '.goofish.com' },
  ]);
  const renewed = cookieFingerprint([
    { name: 'unb', value: '1', domain: '.goofish.com' },
    { name: 'cookie2', value: 'new', domain: '.goofish.com' },
  ]);
  assert.notEqual(baseline, renewed);
});
