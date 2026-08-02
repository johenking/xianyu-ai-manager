import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { access, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const root = new URL('../', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.version, '1.2.1');

const packageFiles = [
  'manifest.json',
  'popup.html',
  'popup.css',
  'popup.js',
  'lib.mjs',
  'background.js',
  'content.js',
  'README.md',
  'icons/icon-16.png',
  'icons/icon-32.png',
  'icons/icon-48.png',
  'icons/icon-128.png',
];

for (const relative of packageFiles) {
  await access(new URL(relative, root));
}

const backgroundParse = spawnSync(
  process.execPath,
  ['--input-type=module', '--check'],
  {
    input: await readFile(new URL('background.js', root), 'utf8'),
    encoding: 'utf8',
  },
);
assert.equal(backgroundParse.status, 0, backgroundParse.stderr || 'background.js module parse failed');

const popupHtml = await readFile(new URL('popup.html', root), 'utf8');
assert.match(popupHtml, /<svg[\s>]/);
assert.match(popupHtml, /rel="icon"/);

const digest = (buffer) => createHash('sha256').update(buffer).digest('hex');
const archiveName = `xianyu-browser-bridge-${manifest.version}.zip`;
const archiveUrls = process.argv.length > 2
  ? process.argv.slice(2).map((archivePath) => pathToFileURL(archivePath))
  : [
    new URL(`dist/${archiveName}`, root),
    new URL(`../../static/downloads/${archiveName}`, import.meta.url),
    new URL('../../static/downloads/xianyu-cookie-importer.zip', import.meta.url),
  ];
const archiveBuffers = await Promise.all(archiveUrls.map((url) => readFile(url)));
assert.equal(new Set(archiveBuffers.map(digest)).size, 1, 'extension archives differ');

for (const archiveUrl of archiveUrls) {
  const archivePath = decodeURIComponent(archiveUrl.pathname);
  const listing = spawnSync('unzip', ['-Z1', archivePath], { encoding: 'utf8' });
  assert.equal(listing.status, 0, listing.stderr || `cannot list ${archivePath}`);
  assert.deepEqual(listing.stdout.trim().split('\n'), packageFiles, `unexpected file list in ${archivePath}`);
  for (const relative of packageFiles) {
    const extracted = spawnSync('unzip', ['-p', archivePath, relative]);
    assert.equal(extracted.status, 0, extracted.stderr?.toString() || `cannot read ${relative}`);
    const source = await readFile(new URL(relative, root));
    assert.equal(digest(extracted.stdout), digest(source), `${relative} does not match source`);
  }
  const archivedManifest = JSON.parse(spawnSync('unzip', ['-p', archivePath, 'manifest.json'], {
    encoding: 'utf8',
  }).stdout);
  assert.equal(archivedManifest.version, manifest.version);
}
console.log('Chrome extension source verification passed.');
