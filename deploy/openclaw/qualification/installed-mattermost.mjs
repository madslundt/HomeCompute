// Runs only in the networkless restored VM; exercises the installed channel
// factory with an empty synthetic queue and controlled readiness responses.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const [runtime, expectedHash] = process.argv.slice(2);
let owner = path.dirname(runtime), sdkVerified = false;
while (owner !== path.dirname(owner)) {
  const metadata = path.join(owner, 'node_modules/openclaw/package.json');
  if (fs.existsSync(metadata)) {
    assert.equal(fs.realpathSync(metadata), '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/package.json');
    assert.equal(JSON.parse(fs.readFileSync(metadata, 'utf8')).version, '2026.9.2');
    sdkVerified = true; break;
  }
  owner = path.dirname(owner);
}
assert.equal(sdkVerified, true, 'installed channel must use the candidate core SDK');
const source = fs.readFileSync(runtime, 'utf8');
assert.equal(createHash('sha256').update(source).digest('hex'), expectedHash);
assert.ok(source.includes('runPumpTask: (work) => runWhenTextReady(work, options.abortSignal)'));
const temporary = runtime + '.hc-qualification-' + randomUUID() + '.mjs';
fs.writeFileSync(temporary, source + '\nexport { createMattermostIngressMonitor };\n', { flag: 'wx', mode: 0o400 });
const originalFetch = globalThis.fetch;
let available = false;
const calls = [], errors = [];
const abort = new AbortController();
let monitor;
try {
  globalThis.fetch = async url => {
    assert.equal(String(url), 'https://home-core.tail479ad.ts.net:9443/llm/status');
    return new Response(JSON.stringify({ available }), { status: 200 });
  };
  const { createMattermostIngressMonitor } = await import(pathToFileURL(temporary).href);
  const queue = Object.fromEntries(['prune', 'recoverStaleClaims', 'listPending', 'listClaims'].map(name =>
    [name, async () => { calls.push(name); return []; }]));
  monitor = createMattermostIngressMonitor({ queue, accountId: 'synthetic-qualification',
    runtime: { log() {}, error: message => errors.push(message) }, abortSignal: abort.signal,
    dispatch: async () => { throw new Error('No synthetic event may be delivered'); }, pollIntervalMs: 60000 });
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.deepEqual(calls, []);
  available = true;
  const deadline = Date.now() + 15000;
  while (!calls.includes('listClaims') && !errors.length && Date.now() < deadline)
    await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(calls.includes('listPending') && calls.includes('listClaims'));
  assert.deepEqual(errors, []);
  await monitor.waitForIdle();
  console.log(JSON.stringify({ installed_mattermost_ingress_verified: true, claims_while_unavailable: 0 }));
} finally {
  abort.abort();
  await monitor?.stop();
  globalThis.fetch = originalFetch;
  fs.unlinkSync(temporary);
}
