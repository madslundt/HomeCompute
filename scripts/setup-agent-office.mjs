#!/usr/bin/env node
// Guarded native-plugin lifecycle, executed inside the existing sandbox from
// the reviewed release. Default only plans; no restart or network changes.
import { readFile, writeFile, mkdir, open, chmod, rename, lstat, mkdtemp } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { configPatch } from '../deploy/agent-office/config-patch.mjs';
import { readAuth } from '../deploy/agent-office/auth.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const INSTALL = '/sandbox/.openclaw/homecompute-agent-office';
const CONFIG = '/sandbox/.openclaw/openclaw.json';
const AUTH = '/sandbox/.openclaw/agent-office-auth.json';

async function regular(path, max = 4 * 1024 * 1024) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > max) throw new Error('Bounded regular input required');
    return await file.readFile();
  } finally { await file.close(); }
}

export async function verifyBundle(directory) {
  const receipt = JSON.parse(await regular(join(directory, 'bundle.json'), 65536));
  if (receipt.schema_version !== 1 || receipt.source !== 'deploy/agent-office' || !receipt.files || Object.keys(receipt.files).length > 64) throw new Error('Bundle receipt refused');
  const files = new Map();
  for (const [name, digest] of Object.entries(receipt.files)) {
    if (!/^(?:[a-z][a-z0-9.-]*\.(?:mjs|json)|public\/(?:[a-z][a-z0-9.-]*\.(?:mjs|css|html)|assets\/[a-z][a-z0-9.-]*\.(?:png|json)))$/.test(name)
        || !/^[a-f0-9]{64}$/.test(digest)) throw new Error('Bundle path or digest refused');
    const bytes = await regular(join(directory, name));
    if (sha(bytes) !== digest) throw new Error('Bundle content digest mismatch');
    files.set(name, bytes);
  }
  for (const name of ['index.mjs', 'auth.mjs', 'server.mjs', 'activity.mjs', 'catalog.mjs', 'scene-layout.mjs', 'openclaw.plugin.json', 'roster.json', 'public/index.html']) {
    if (!files.has(name)) throw new Error('Required bundle input missing');
  }
  return { files, digest: sha(JSON.stringify(receipt)) };
}

export async function setup(values) {
  const bundle = await verifyBundle(values.bundle);
  const bytes = await regular(CONFIG);
  const beforeDigest = sha(bytes);
  const installed = join(INSTALL, bundle.digest);
  const changes = configPatch(JSON.parse(bytes), installed);
  const report = { bundle_sha256: bundle.digest, config_sha256: beforeDigest, installed_path: installed, changes, applied: false };
  if (!values.apply) return report;
  if (process.getuid?.() !== 0 || values['expected-bundle-sha256'] !== bundle.digest || values['expected-config-sha256'] !== beforeDigest) throw new Error('Apply requires root and both reviewed input digests');
  await readAuth(AUTH); // Refuse publishing any listener without valid credentials.
  for (const path of ['/sandbox', '/sandbox/.openclaw', INSTALL]) {
    await mkdir(path, { recursive: true, mode: 0o755 });
    if (!(await lstat(path)).isDirectory() || (await lstat(path)).isSymbolicLink()) throw new Error('Managed directory refused');
  }
  const installInfo = await lstat(INSTALL);
  if (installInfo.uid !== 0 || installInfo.mode & 0o022) throw new Error('Protected installation owner required');
  let exists = false;
  try { await lstat(installed); exists = true; } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (exists) {
    const installedInfo = await lstat(installed);
    if (!installedInfo.isDirectory() || installedInfo.isSymbolicLink() || installedInfo.uid !== 0 || installedInfo.mode & 0o022) throw new Error('Protected installed bundle required');
    if ((await verifyBundle(installed)).digest !== bundle.digest) throw new Error('Installed bundle differs');
    for (const name of bundle.files.keys()) {
      const info = await lstat(join(installed, name));
      if (info.uid !== 0 || info.mode & 0o022) throw new Error('Protected installed file required');
    }
  } else {
    const stage = await mkdtemp(join(INSTALL, '.stage-'));
    for (const [name, data] of bundle.files) {
      await mkdir(join(stage, name, '..'), { recursive: true, mode: 0o755 });
      await writeFile(join(stage, name), data, { mode: 0o444, flag: 'wx' });
    }
    await writeFile(join(stage, 'bundle.json'), await regular(join(values.bundle, 'bundle.json'), 65536), { mode: 0o444, flag: 'wx' });
    await chmod(stage, 0o755);
    await rename(stage, installed);
  }
  const invoke = apply => execFileSync('openclaw', ['config', 'set', '--batch-json', JSON.stringify(changes), ...(apply ? [] : ['--dry-run'])], {
    env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/sandbox', OPENCLAW_CONFIG_PATH: CONFIG },
    stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000,
  });
  try { invoke(false); } catch { throw new Error('Native config validation failed; no config applied'); }
  if (sha(await regular(CONFIG)) !== beforeDigest) throw new Error('Native configuration changed during review');
  const backup = join(INSTALL, `config-before-${beforeDigest}.json`);
  try { await writeFile(backup, bytes, { mode: 0o600, flag: 'wx' }); }
  catch (error) { if (error.code !== 'EEXIST' || sha(await regular(backup)) !== beforeDigest) throw new Error('Retained configuration backup differs'); }
  try { invoke(true); } catch { throw new Error('Native config apply unconfirmed; retain backup and inspect before retry'); }
  const actual = JSON.parse(await regular(CONFIG));
  for (const change of changes) {
    const value = change.path.split('.').reduce((current, part) => current?.[part], actual);
    if (!isDeepStrictEqual(value, change.value)) throw new Error('Post-apply plugin configuration mismatch; retain backup');
  }
  report.applied = true;
  report.backup = backup;
  report.restart_required = true;
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { values } = parseArgs({ options: { bundle: { type: 'string' }, apply: { type: 'boolean', default: false }, 'expected-bundle-sha256': { type: 'string' }, 'expected-config-sha256': { type: 'string' } } });
  if (!values.bundle?.startsWith('/')) throw new Error('Provide an absolute --bundle directory');
  try { console.log(JSON.stringify(await setup(values))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
