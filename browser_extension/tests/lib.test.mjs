import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  ALLOWED_SUFFIXES,
  buildImportPayload,
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
    { pairingId: 'pairing-id', pairingCode: 'ABCD1234' },
    [
      { name: 'unb', value: '123', domain: '.goofish.com', path: '/' },
      { name: 'private', value: 'other-site', domain: '.example.com', path: '/' },
    ],
    'Chrome UA',
  );
  assert.equal(payload.cookies.length, 1);
  assert.equal(payload.cookies[0].name, 'unb');
});

test('parses JSON and compact pairing formats without persistence', () => {
  assert.deepEqual(
    parsePairingBundle('{"pairing_id":"one","pairing_code":"TWO"}'),
    { pairingId: 'one', pairingCode: 'TWO' },
  );
  assert.deepEqual(parsePairingBundle('one:TWO'), {
    pairingId: 'one',
    pairingCode: 'TWO',
  });
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
    manifest.host_permissions.some((entry) => entry === 'http://127.0.0.1:8091/*'),
    true,
  );
});

test('popup code never writes sensitive values to extension storage', async () => {
  const popup = await readFile(new URL('../popup.js', import.meta.url), 'utf8');
  assert.equal(/chrome\.storage|localStorage|sessionStorage/.test(popup), false);
  assert.equal(isAllowedCookie({ domain: '.taobao.com' }), true);
  assert.equal(isAllowedCookie({ domain: '.example.com' }), false);
});
