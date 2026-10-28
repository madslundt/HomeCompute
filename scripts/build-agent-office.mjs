#!/usr/bin/env node
// Build a self-contained, metadata-only plugin. No host or gateway mutations.
import { readFile, readdir, mkdir, copyFile, writeFile, lstat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve, relative, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { projectRoster } from '../deploy/agent-office/catalog.mjs';
import { configPatch } from '../deploy/agent-office/config-patch.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = join(root, 'deploy/agent-office');
const { values } = parseArgs({ options: { output: { type: 'string' }, config: { type: 'string' } } });
if (!values.output) throw new Error('Usage: node scripts/build-agent-office.mjs --output /absolute/new-directory');
const output = resolve(values.output);
// A bundle contains only public UI and the finite roster projection. Native
// config is deliberately neither read nor copied, so tokens cannot enter it.
if (output === root || output.startsWith(root + '/')) throw new Error('Build outside the source checkout');
await mkdir(output, { mode: 0o700 }); // Refuse existing output, including symlinks.
const hashes = {};
async function copy(path) {
  const info = await lstat(join(source, path));
  if (info.isSymbolicLink()) throw new Error('Refuse symlinked bundle input');
  if (info.isDirectory()) {
    await mkdir(join(output, path), { mode: 0o700 });
    for (const name of (await readdir(join(source, path))).sort()) await copy(join(path, name));
  } else if (info.isFile()) {
    if (path === 'package.json') {
      const manifest = JSON.parse(await readFile(join(source, path), 'utf8'));
      delete manifest.scripts; // Source-checkout commands do not belong to an installed plugin.
      await writeFile(join(output, path), JSON.stringify(manifest, null, 2) + '\n');
    } else await copyFile(join(source, path), join(output, path));
    hashes[path] = createHash('sha256').update(await readFile(join(output, path))).digest('hex');
  } else throw new Error('Regular input files required');
}
for (const path of ['activity.mjs', 'auth.mjs', 'catalog.mjs', 'scene-layout.mjs', 'server.mjs', 'index.mjs', 'openclaw.plugin.json', 'package.json', 'public']) await copy(path);
const team = JSON.parse(await readFile(join(root, 'config/openclaw-team.json'), 'utf8'));
const roster = JSON.stringify(projectRoster(team), null, 2) + '\n';
await writeFile(join(output, 'roster.json'), roster, { mode: 0o600 });
hashes['roster.json'] = createHash('sha256').update(roster).digest('hex');
const receipt = { schema_version: 1, source: relative(root, source), files: hashes };
await writeFile(join(output, 'bundle.json'), JSON.stringify(receipt, null, 2) + '\n', { mode: 0o600 });
const bundleDigest = createHash('sha256').update(JSON.stringify(receipt)).digest('hex');
if (values.config) {
  const native = JSON.parse(await readFile(values.config, 'utf8'));
  const changes = configPatch(native, `/sandbox/.openclaw/homecompute-agent-office/${bundleDigest}`);
  await writeFile(join(output, 'config-patch.json'), JSON.stringify(changes, null, 2) + '\n', { mode: 0o600 });
}
console.log(JSON.stringify({ output, files: Object.keys(hashes).length, bundle_sha256: bundleDigest }));
