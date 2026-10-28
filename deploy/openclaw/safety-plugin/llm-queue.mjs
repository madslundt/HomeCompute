// Offline gating runs before durable Mattermost ingress claims. Accepted native
// turns retain ownership of their normal lifecycle and are never replayed here.
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

export const READINESS_URL = 'https://home-core.tail479ad.ts.net:9443/llm/status';
const CORE_ACTIVITY = '/usr/local/lib/node_modules/openclaw/dist/gateway-active-work-DoISxXUF.js';
const CORE_SHA256 = '762f2ad2930d6445b77487ecf878c985dca7181238f5301d37fc054a71a56966';

export async function textAvailable(transport = globalThis.fetch, abortSignal) {
  try {
    const response = await transport(READINESS_URL, {
      method: 'GET', redirect: 'error', signal: abortSignal
        ? AbortSignal.any([abortSignal, AbortSignal.timeout(4000)]) : AbortSignal.timeout(4000),
      headers: { accept: 'application/json', 'cache-control': 'no-cache' },
    });
    if (!response.ok || !response.body) return false;
    const reader = response.body.getReader();
    const chunks = []; let size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 4096) return false;
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    return value?.available === true;
  } catch { return false; }
}

export async function runWhenTextReady(work, abortSignal, available = () => textAvailable(globalThis.fetch, abortSignal),
  wait = () => new Promise(resolve => {
    const done = () => { clearTimeout(timer); abortSignal?.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, 10000);
    abortSignal?.addEventListener('abort', done, { once: true });
    if (abortSignal?.aborted) done();
  })) {
  // A false result leaves native pending rows untouched: no claims, retries,
  // adoption, transcript writes, model calls, or outbound replies.
  // The core monitor resets its pumping flag inside work's finally block. Keep
  // the pump pending until ready, then always enter work (also after abort, when
  // the core immediately exits without claiming). Returning early would strand
  // the monitor permanently after the first offline poll.
  while (!abortSignal?.aborted && !await available()) await wait();
  await work();
}

export async function nativeActivity(read = readFile, load = path => import(path)) {
  const source = await read(CORE_ACTIVITY);
  if (createHash('sha256').update(source).digest('hex') !== CORE_SHA256) {
    throw new Error('Native activity inventory changed; qualification required');
  }
  const inventory = await load(CORE_ACTIVITY);
  return summarizeNativeActivity(inventory.t());
}

export function summarizeNativeActivity(snapshot, clock = Date.now) {
  const count = snapshot?.counts?.totalActive;
  if (!Number.isSafeInteger(count) || count < 0 || count > 1000000) {
    throw new Error('Native activity inventory is invalid');
  }
  return { active_turns: count, observed_at: clock() / 1000 };
}

export function registerLlmActivity(api) {
  api.registerGatewayMethod('homecompute-safety.llm.activity', async ({ params, respond }) => {
    if (Object.keys(params ?? {}).length) {
      respond(false, undefined, { code: 'INVALID_REQUEST', message: 'Activity takes no input' });
      return;
    }
    try { respond(true, await nativeActivity()); }
    catch { respond(false, undefined, { code: 'UNAVAILABLE', message: 'Native activity cannot be verified' }); }
  }, { scope: 'operator.read' });
}
