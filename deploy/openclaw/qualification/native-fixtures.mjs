// Candidate-only native regressions. Invoked in the bounded, networkless image;
// all writable inputs are public source and synthetic state in tmpfs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const base = '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw';
const dist = base + '/dist';
const safety = '/tmp/hc-native-fixtures/safety';
const hashes = {
  'plugin-module-loader-cache-lf3kAaBw.js': 'e09d22fd6e1877f388c44be8e837dec5ebb0cb8002d29ddf41c26ef7f84c0d08',
  'hook-runner-global-0kfmMG4T.js': '7f9473df33a148d511815fca56b1349cc5525e516680a71da22ca2cc863d047d',
  'agent-runner.runtime-cwyzm408.js': '135a52075af80beb1cc5fd22a7e72324308f099208b00de00fb13967bf923911',
  'ingress-monitor--JQILMnk.js': '153485e44ec82352c72c79662bdf767abca222d739e9f82908aa3b3be02ee251',
  'reply-payload-DahKk-7n.js': '5d21073c8a5e1e35a007a6d8e5bbf180063db67d81892d4d01f0619e2dd748f2',
};
for (const [name, hash] of Object.entries(hashes)) {
  assert.equal(createHash('sha256').update(fs.readFileSync(dist + '/' + name)).digest('hex'), hash,
    'native fixture module preimage must match');
}
const load = filename => import(pathToFileURL(dist + '/' + filename).href);
const { n: nativeLoader } = await load('plugin-module-loader-cache-lf3kAaBw.js');
const { a: reset, i: initialize, t: runner } = await load('hook-runner-global-0kfmMG4T.js');
const loaderUrl = pathToFileURL(dist + '/plugin-module-loader-cache-lf3kAaBw.js').href;
const entry = safety + '/index.mjs';
const loaded = nativeLoader({ modulePath: entry, rootDir: safety, aliasMap: {}, importerUrl: loaderUrl })(entry);
const plugin = loaded.default ?? loaded;
const registry = { plugins: [{ id: 'homecompute-safety', status: 'loaded' }], hooks: [], typedHooks: [] };
const services = [], methods = [];
plugin.register({ on: (hookName, handler, options = {}) => registry.typedHooks.push({
  pluginId: 'homecompute-safety', hookName, handler, ...options }),
  registerService: service => services.push(service), registerGatewayMethod: (...args) => methods.push(args) });
assert.equal(registry.typedHooks.length, 2);
assert.equal(services.length, 1);
assert.equal(methods.length, 1);
assert.ok(registry.typedHooks.every(h => h.priority <= -1000000));
reset(); initialize(registry);
const context = role => ({ agentId: role, sessionKey: `agent:${role}:main` });
const event = params => ({ toolName: 'browser', params });
for (const params of [ { action: 'navigate', targetUrl: 'https://10.77.20.1/' },
  { action: 'act', targetId: 'synthetic', kind: 'evaluate', fn: '1' }, { action: 'act' } ]) {
  assert.equal((await runner().runBeforeToolCall(event(params), context('researcher'))).block, true);
}
const navigation = event({ action: 'navigate', targetUrl: 'https://example.com/' });
assert.equal((await runner().runBeforeToolCall(navigation, context('main'))).block, true);
const allowed = await runner().runBeforeToolCall(navigation, context('researcher'));
assert.equal(allowed.params.profile, 'homecompute');
assert.equal(allowed.params.target, 'host');
assert.equal((await runner().runBeforeToolCall({ toolName: 'homecompute__homecompute_shopping_list',
  params: { agentId: 'shopper' } }, context('main'))).block, true);
let subsequent = false;
registry.typedHooks.push({ pluginId: 'fixture', hookName: 'before_tool_call', priority: -2000000,
  handler: () => { subsequent = true; return { block: false }; } });
assert.equal((await runner().runBeforeToolCall(event({ action: 'act' }), context('researcher'))).block, true);
assert.equal(subsequent, false, 'native veto stops later permissive hooks');
registry.typedHooks.unshift({ pluginId: 'fixture', hookName: 'before_tool_call', priority: 1,
  handler: () => { throw new Error('Synthetic safety failure'); } });
await assert.rejects(() => runner().runBeforeToolCall(navigation, context('researcher')));
reset();

// Exercise the native ingress pump with an empty in-memory queue. No readiness
// HTTP request, real inbox, claim, transcript or delivery is used.
const { n: ingress } = await load('ingress-monitor--JQILMnk.js');
const { runWhenTextReady } = await import(pathToFileURL(safety + '/llm-queue.mjs').href);
const delay = () => new Promise(resolve => setTimeout(resolve, 2));
for (const resume of [true, false]) {
  let ready = false;
  const calls = [], errors = [];
  const abort = new AbortController();
  const queue = Object.fromEntries(['prune', 'recoverStaleClaims', 'listPending', 'listClaims'].map(name =>
    [name, async () => { calls.push(name); return []; }]));
  const monitor = ingress({ queue, abortSignal: abort.signal, pollIntervalMs: 60000,
    payload: { version: 1 }, onError: error => errors.push(error),
    runPumpTask: work => runWhenTextReady(work, abort.signal, async () => ready, delay) });
  try {
    monitor.start();
    await delay(); await delay();
    assert.deepEqual(calls, [], 'offline readiness must precede native queue inspection');
    if (resume) {
      ready = true;
      const deadline = Date.now() + 2000;
      while (!calls.includes('listClaims') && !errors.length && Date.now() < deadline) await delay();
      assert.ok(calls.includes('listPending') && calls.includes('listClaims'));
      await monitor.waitForPumpIdle();
    }
  } finally {
    abort.abort();
    await monitor.stop();
  }
  assert.deepEqual(errors, []);
  assert.equal(monitor.isStopped(), true);
}

// Expose two internal dispatcher entry points only in an ephemeral module copy.
// All sibling modules resolve to the original immutable runtime via symlinks.
const mirror = '/tmp/hc-native-fixtures/runtime';
fs.mkdirSync(mirror + '/dist', { recursive: true });
for (const name of fs.readdirSync(base)) {
  if (name !== 'dist') fs.symlinkSync(base + '/' + name, mirror + '/' + name);
}
if (!fs.existsSync(mirror + '/node_modules')) fs.symlinkSync(base + '/..', mirror + '/node_modules');
const dispatcher = 'agent-runner.runtime-cwyzm408.js';
for (const name of fs.readdirSync(dist)) {
  if (name !== dispatcher) fs.symlinkSync(dist + '/' + name, mirror + '/dist/' + name);
}
fs.writeFileSync(mirror + '/dist/' + dispatcher, fs.readFileSync(dist + '/' + dispatcher, 'utf8') +
  '\nexport { prepareReplyAgentPayloads, buildSessionsYieldAcknowledgmentPayload };\n', { flag: 'wx' });
const { prepareReplyAgentPayloads: prepare, buildSessionsYieldAcknowledgmentPayload: ack } =
  await import(pathToFileURL(mirror + '/dist/' + dispatcher).href);
const { y: metadata } = await load('reply-payload-DahKk-7n.js');
const waiting = 'Waiting for Orion.';
function input({ yielded = true, answer = false } = {}) {
  const warning = metadata({ text: 'Read failed', isError: true }, { toolErrorWarning: { toolName: 'read' } });
  return { context: { cfg: {}, followupRun: { run: { sessionKey: 'agent:main:main', verboseLevelOverride: 'off' } },
    sessionKey: 'agent:main:main', sessionCtx: {}, opts: {}, runStartedAt: Date.now(),
    replyMediaContext: { normalizePayload: payload => payload },
    returnWithQueuedFollowupDrain: value => value,
    typingSignals: { signalRunStart: async () => {}, signalTextDelta: async () => {},
      signalTyping: async () => {}, cleanup: async () => {} } },
    accounting: { configuredFallbackModel: { persistedAutoFallback: false }, fallbackAttempts: [],
      fallbackTransition: {}, directlySentBlockKeys: new Set(), directlySentBlockPayloads: [],
      payloadArray: answer ? [warning, { text: 'Synthetic final answer.' }] : [warning],
      runResult: { meta: { yielded, yieldAcknowledgment: yielded ? waiting : undefined } },
      selectedModel: 'fixture', selectedProvider: 'fixture', sessionModel: { provider: 'fixture', model: 'fixture' },
      replyUsageState: {}, preserveUserFacingSessionState: true } };
}
const yielded = await prepare(input());
assert.equal(yielded.kind, 'continue');
assert.deepEqual(yielded.guardedReplyPayloads.map(p => p.text), [waiting]);
const ordinary = await prepare(input({ yielded: false }));
assert.ok(ordinary.guardedReplyPayloads.some(p => p.text === 'Read failed'));
const answered = await prepare(input({ answer: true }));
assert.deepEqual(answered.guardedReplyPayloads.map(p => p.text), ['Synthetic final answer.']);
const ackInput = { yielded: true, yieldAcknowledgment: waiting, isInteractive: true,
  hasVisibleMessageDelivery: false };
assert.equal(ack(ackInput).text, waiting);
for (const overrides of [{ isInteractive: false }, { isHeartbeat: true }, { silentExpected: true },
  { isSubagentSession: true }, { hasExplicitSilentReply: true }, { hasVisibleMessageDelivery: true }]) {
  assert.equal(ack({ ...ackInput, ...overrides }), undefined);
}
console.log(JSON.stringify({ executed: true, native_loader: true, native_safety_policy: true,
  native_hook_failure_closed: true, native_hook_veto_sticky: true, native_ingress_gate: true,
  native_ingress_shutdown: true, native_yield_acknowledgment: true,
  ordinary_warning_preserved: true, terminal_answer_preserved: true, quiet_ack_rules_preserved: true }));
