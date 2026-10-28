import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Exercise installed terminal assembly and payload formatting without starting
// a gateway or invoking tools, models, or messaging. Candidate mode validates
// the pinned overlay in memory before any live runtime mutation.
const dist = '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist';
const installed = fs.existsSync(dist);
const script = process.env.OPENCLAW_YIELD_SETUP ?? fileURLToPath(new URL('../scripts/setup-openclaw-yield-acknowledgment.py', import.meta.url));
function extract(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `native function ${name} exists`);
  const end = source.indexOf('\n}', start) + 2;
  assert.ok(end > start);
  return source.slice(start, end);
}
let prepareTerminal;
let acknowledgment;
if (installed) {
  let terminal = fs.readFileSync(`${dist}/embedded-agent-DaSvA-Yk.js`, 'utf8');
  if (process.env.OPENCLAW_YIELD_CANDIDATE === '1') {
    terminal = execFileSync('python3', ['-c',
      'import runpy,sys; m=runpy.run_path(sys.argv[1]); sys.stdout.buffer.write(m["overlay"](sys.stdin.buffer.read()))', script],
    { input: terminal, maxBuffer: 3 * 1024 * 1024 }).toString();
  }
  const importsContinuationHelper = [...terminal.matchAll(/\bimport\s*\{([^}]+)\}\s*from\s*["'][^"']+["']/g)]
    .some(([, names]) => names.split(',').some(name => name.trim().split(/\s+as\s+/).at(-1) === 'hasYieldContinuationEvidence'));
  assert.ok(importsContinuationHelper || /\bfunction hasYieldContinuationEvidence\s*\(/.test(terminal),
    'continuation helper must be bound in the real terminal module, not only the test harness');
  const builtin = fs.readFileSync(`${dist}/builtin-openclaw-zQV8Wwjr.js`, 'utf8');
  const dispatcher = fs.readFileSync(`${dist}/agent-runner.runtime-CAdsMsQt.js`, 'utf8');
  const { t: buildEmbeddedRunPayloads } = await import(pathToFileURL(`${dist}/payloads-B7_zRmw4.js`).href);
  const hasYieldContinuationEvidence = vm.runInNewContext(`(${extract(builtin, 'hasYieldContinuationEvidence')})`, {
    hasCommittedMessagingToolDeliveryEvidence: () => false,
    hasAcceptedSessionSpawn: spawns => (spawns?.length ?? 0) > 0,
    hasAsyncActivity: tools => (tools ?? []).some(tool => tool.asyncStarted),
  });
  prepareTerminal = vm.runInNewContext(`(${extract(terminal, 'prepareEmbeddedRunTerminal')})`, {
    projectAgentRunAttemptTerminal: () => ({}),
    isEmbeddedRunTerminalTimeout: () => false,
    isEmbeddedRunTimeoutFinal: () => false,
    buildUsageAgentMetaFields: () => ({}),
    resolveReportedModelRef: ({ provider, model }) => ({ provider, model }),
    estimateAggregateUsageCost: () => undefined,
    resolveModelCostConfig: () => undefined,
    resolveFinalAssistantVisibleText: () => undefined,
    resolveFinalAssistantRawText: () => undefined,
    buildEmbeddedRunPayloads,
    hasYieldContinuationEvidence,
    isEmbeddedRunTerminalInterrupted: () => false,
    hasMessagingToolDeliveryEvidence: () => false,
    mergeAttemptToolMediaPayloads: ({ payloads }) => payloads,
    getCoreTtsAttemptResultMediaUrls: () => [],
    buildTraceToolSummary: () => ({}),
    resolveEmbeddedRunFailureSignal: () => undefined,
    resolveEmbeddedRunTerminalToolFailure: () => undefined,
  });
  acknowledgment = vm.runInNewContext(`(${extract(dispatcher, 'buildSessionsYieldAcknowledgmentPayload')})`, {
    markReplyPayloadForSourceSuppressionDelivery: payload => payload,
  });
}

const waiting = 'Waiting for Orion to come back with the current energy plan status.';
function input(overrides = {}) {
  return {
    runParams: { config: {}, runId: 'synthetic-energy-read-then-yield', thinkLevel: 'off' },
    attempt: {
      terminal: {}, assistantTexts: [], toolMetas: [{ toolName: 'read', isError: true }],
      lastAssistant: { role: 'assistant', content: [{ type: 'toolCall', name: 'sessions_yield',
        arguments: { acknowledgment: waiting } }], stopReason: 'toolUse' },
      lastToolError: { toolName: 'read', error: 'File not found: /sandbox/.openclaw/workspace/MEMORY.md' },
      yieldDetected: true, yieldAcknowledgment: waiting,
      acceptedSessionSpawns: [{ childSessionKey: 'agent:operator:subagent:synthetic' }],
      ...overrides,
    },
    terminalState: { outcome: {} }, currentAttemptCompletedAssistant: null,
    usageAccumulator: { assistantTurns: 1 }, outerContextTokenMeta: {}, contextRecoveryState: {},
    provider: 'inference', model: 'automation-moe', activeErrorContext: { provider: 'inference', model: 'automation-moe' },
    resolvedToolResultFormat: 'text',
  };
}
function nativeAcknowledgment(overrides = {}) {
  return acknowledgment({ yielded: true, yieldAcknowledgment: waiting, isInteractive: true,
    hasVisibleMessageDelivery: false, ...overrides });
}

test('earlier missing-file warning leaves the terminal slot available for native waiting acknowledgment', { skip: !installed }, () => {
  const result = prepareTerminal(input());
  assert.equal(result.payloads.length, 0, 'stale read warning must not replace a valid waiting message');
  assert.equal(nativeAcknowledgment().text, waiting);
});
test('ordinary tool failures and yields without acknowledgment or continuation retain warnings', { skip: !installed }, () => {
  for (const overrides of [{ yieldDetected: false }, { yieldAcknowledgment: '' }, { yieldAcknowledgment: ' ' },
    { yieldAcknowledgment: undefined }, { acceptedSessionSpawns: [] }]) {
    const result = prepareTerminal(input(overrides));
    assert.equal(result.payloads.length, 1);
    assert.equal(result.payloads[0].text, '⚠️ Read failed');
    assert.equal(result.payloads[0].isError, true);
  }
});
test('native dispatcher retains silent, subagent, heartbeat and already-delivered acknowledgment rules', { skip: !installed }, () => {
  for (const overrides of [{ isInteractive: false }, { isHeartbeat: true }, { silentExpected: true },
    { isSubagentSession: true }, { hasExplicitSilentReply: true }, { hasVisibleMessageDelivery: true }]) {
    assert.equal(nativeAcknowledgment(overrides), undefined);
  }
});
