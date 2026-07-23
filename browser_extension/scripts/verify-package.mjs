import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { access, readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
assert.equal(manifest.manifest_version, 3);

for (const relative of [
  'popup.html',
  'popup.css',
  'popup.js',
  'lib.mjs',
  'icons/icon-16.png',
  'icons/icon-32.png',
  'icons/icon-48.png',
  'icons/icon-128.png',
]) {
  await access(new URL(relative, root));
}

const popupHtml = await readFile(new URL('popup.html', root), 'utf8');
assert.match(popupHtml, /<svg[\s>]/);
assert.match(popupHtml, /rel="icon"/);

const sourceArchive = await readFile(new URL('dist/xianyu-cookie-importer.zip', root));
const publicArchive = await readFile(new URL('../../static/downloads/xianyu-cookie-importer.zip', import.meta.url));
const digest = (buffer) => createHash('sha256').update(buffer).digest('hex');
assert.equal(digest(sourceArchive), digest(publicArchive));
console.log('Chrome extension source verification passed.');
