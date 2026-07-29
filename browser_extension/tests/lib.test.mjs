import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  ALLOWED_SUFFIXES,
  buildImportPayload,
  isAllowedImportUrl,
  isAllowedCookie,
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
});

test('manifest permissions stay within the approved allowlist', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../manifest.json', import.meta.url), 'utf8'),
  );
  assert.deepEqual([...manifest.permissions].sort(), ['activeTab', 'cookies']);
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.permissions.includes('storage'), false);
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
});
