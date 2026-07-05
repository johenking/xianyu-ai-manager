import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const staticRoot = path.resolve(scriptDir, '../../static');
const manifestPath = path.join(staticRoot, '.asset-generations.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const generations = Array.isArray(manifest.generations) ? manifest.generations : [];
const retained = new Set(generations.flat());
const assetFiles = fs.readdirSync(path.join(staticRoot, 'assets'))
  .map((name) => `assets/${name}`)
  .sort();
const orphaned = assetFiles.filter((name) => !retained.has(name));

if (orphaned.length > 0) {
  throw new Error(`Found orphaned build assets: ${orphaned.join(', ')}`);
}

if (process.env.VITE_BUILD_SOURCEMAP !== 'true') {
  const sourceMaps = assetFiles.filter((name) => name.endsWith('.map'));
  if (sourceMaps.length > 0) {
    throw new Error(`Source maps must be explicitly enabled: ${sourceMaps.join(', ')}`);
  }
}

const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8');
const entryMatch = html.match(/src="\/static\/(assets\/index-[^"]+\.js)"/);
if (!entryMatch) {
  throw new Error('Unable to locate the production entry chunk in static/index.html');
}

const baselineBytes = 865_910;
const maximumBytes = Math.floor(baselineBytes * 0.7);
const entryBytes = fs.statSync(path.join(staticRoot, entryMatch[1])).size;
if (entryBytes > maximumBytes) {
  throw new Error(`Entry chunk is ${entryBytes} bytes; expected <= ${maximumBytes} bytes`);
}

const reduction = ((baselineBytes - entryBytes) / baselineBytes * 100).toFixed(1);
console.log(JSON.stringify({
  generations: generations.map((generation) => generation.length),
  asset_files: assetFiles.length,
  orphaned: orphaned.length,
  entry_bytes: entryBytes,
  baseline_bytes: baselineBytes,
  reduction_percent: Number(reduction),
}, null, 2));
