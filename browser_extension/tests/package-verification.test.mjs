import assert from 'node:assert/strict';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const extensionRoot = fileURLToPath(new URL('../', import.meta.url));
const verifier = join(extensionRoot, 'scripts', 'verify-package.mjs');
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

async function buildFixture(root, name, options = {}) {
  const stage = join(root, name);
  await mkdir(stage, { recursive: true });
  for (const relative of packageFiles) {
    if (options.omit === relative) continue;
    const destination = join(stage, relative);
    await mkdir(dirname(destination), { recursive: true });
    await copyFile(join(extensionRoot, relative), destination);
  }
  if (options.oldManifest) {
    const manifestPath = join(stage, 'manifest.json');
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
    manifest.version = '1.2.0';
    await writeFile(manifestPath, JSON.stringify(manifest));
  }
  if (options.tamper) {
    await writeFile(join(stage, 'popup.js'), 'throw new Error("tampered");\n');
  }
  const archive = join(root, name + '.zip');
  const files = packageFiles.filter((relative) => relative !== options.omit);
  const zipped = spawnSync('zip', ['-q', archive, ...files], { cwd: stage, encoding: 'utf8' });
  assert.equal(zipped.status, 0, zipped.stderr);
  return archive;
}

test('package verifier rejects missing, old, and tampered extension archives', async () => {
  const root = await mkdtemp(join(tmpdir(), 'xmc-extension-negative-'));
  try {
    const fixtures = [
      await buildFixture(root, 'missing-content', { omit: 'content.js' }),
      await buildFixture(root, 'old-manifest', { oldManifest: true }),
      await buildFixture(root, 'tampered', { tamper: true }),
    ];
    for (const archive of fixtures) {
      const verified = spawnSync(process.execPath, [verifier, archive], { encoding: 'utf8' });
      assert.notEqual(verified.status, 0, basename(archive) + ' unexpectedly passed');
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
