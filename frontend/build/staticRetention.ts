import fs from 'node:fs';
import path from 'node:path';
import type { OutputBundle } from 'rollup';
import type { Plugin, ResolvedConfig } from 'vite';

interface AssetGenerationManifest {
  generations: string[][];
}

const MANIFEST_NAME = '.asset-generations.json';

const readManifest = (manifestPath: string): AssetGenerationManifest => {
  try {
    const parsed = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as AssetGenerationManifest;
    if (Array.isArray(parsed.generations)) {
      return {
        generations: parsed.generations
          .filter((generation) => Array.isArray(generation))
          .map((generation) => generation.filter((item) => typeof item === 'string')),
      };
    }
  } catch {
    // The first build has no manifest yet.
  }
  return { generations: [] };
};

const listFiles = (root: string, current = root): string[] => {
  if (!fs.existsSync(current)) return [];
  return fs.readdirSync(current, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(current, entry.name);
    if (entry.isDirectory()) return listFiles(root, absolute);
    return [path.relative(root, absolute).split(path.sep).join('/')];
  });
};

export const staticRetentionPlugin = (): Plugin => {
  let config: ResolvedConfig;
  let previousGenerations: string[][] = [];
  let currentGeneration: string[] = [];

  return {
    name: 'xianyu-static-retention',
    apply: 'build',
    configResolved(resolvedConfig) {
      config = resolvedConfig;
      const manifestPath = path.join(config.build.outDir, MANIFEST_NAME);
      previousGenerations = readManifest(manifestPath).generations.slice(0, 1);
      if (!config.build.sourcemap) {
        previousGenerations = previousGenerations.map((generation) => (
          generation.filter((fileName) => !fileName.endsWith('.map'))
        ));
      }
    },
    generateBundle(_options, bundle: OutputBundle) {
      currentGeneration = Object.keys(bundle)
        .filter((fileName) => fileName.startsWith('assets/'))
        .sort();
    },
    closeBundle() {
      const outDir = config.build.outDir;
      const assetsDir = path.join(outDir, 'assets');
      const keep = new Set([...currentGeneration, ...previousGenerations.flat()]);

      for (const relativeAsset of listFiles(outDir, assetsDir)) {
        if (!keep.has(relativeAsset)) {
          fs.rmSync(path.join(outDir, relativeAsset), { force: true });
        }
      }

      const manifest: AssetGenerationManifest = {
        generations: [currentGeneration, ...previousGenerations].slice(0, 2),
      };
      fs.writeFileSync(
        path.join(outDir, MANIFEST_NAME),
        `${JSON.stringify(manifest, null, 2)}\n`,
        'utf8',
      );
    },
  };
};
