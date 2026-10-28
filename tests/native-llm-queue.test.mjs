import assert from 'node:assert/strict';
import test from 'node:test';
import plugin from '../deploy/openclaw/safety-plugin/index.mjs';
import { READINESS_URL, runWhenTextReady, textAvailable, nativeActivity,
  summarizeNativeActivity } from '../deploy/openclaw/safety-plugin/llm-queue.mjs';

test('offline native ingress leaves claim pump untouched; recovery invokes it once', async () => {
  let claims = 0;
  const work = async () => { claims++; };
  let polls = 0;
  await runWhenTextReady(work, undefined, async () => ++polls > 20, async () => {
    assert.equal(claims, 0);
  });
  assert.equal(claims, 1);
  assert.equal(polls, 21);
});

test('shutdown releases the core pump so its own aborted guard can settle safely', async () => {
  const abort = new AbortController(); let workCalled = false;
  await runWhenTextReady(async () => { assert.equal(abort.signal.aborted, true); workCalled = true; },
    abort.signal, async () => false, async () => abort.abort());
  assert.equal(workCalled, true);
});

test('readiness uses fixed approved origin, bounded GET and strict boolean availability', async () => {
  for (const body of [{ available: false }, { available: 1 }, {}, [], { available: true }]) {
    const ready = await textAvailable(async (url, options) => {
      assert.equal(url, READINESS_URL);
      assert.equal(options.method, 'GET');
      assert.equal(options.redirect, 'error');
      return new Response(JSON.stringify(body));
    });
    assert.equal(ready, body?.available === true);
  }
  assert.equal(await textAvailable(async () => { throw new Error('offline'); }), false);
  assert.equal(await textAvailable(async () => new Response('denied', { status: 403 })), false);
  assert.equal(await textAvailable(async () => new Response('x'.repeat(4097))), false);
  assert.equal(await textAvailable(async () => new Response('invalid json')), false);
});

test('native full-run inventory preserves active work across model-call gaps', () => {
  assert.deepEqual(summarizeNativeActivity({ counts: { totalActive: 3 } }, () => 12000),
    { active_turns: 3, observed_at: 12 });
  for (const count of [undefined, -1, 1.5, '0', NaN, Infinity, 1000001]) {
    assert.throws(() => summarizeNativeActivity({ counts: { totalActive: count } }));
  }
});

test('unexpected core implementation fails closed before invoking inventory', async () => {
  let imported = false;
  await assert.rejects(() => nativeActivity(async () => Buffer.from('unqualified update'), async () => {
    imported = true;
  }), /qualification required/);
  assert.equal(imported, false);
});

test('activity RPC requires no input and only read scope', async () => {
  const calls = [];
  plugin.register({ on() {}, registerService() {}, registerGatewayMethod: (...args) => calls.push(args) });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'homecompute-safety.llm.activity');
  assert.deepEqual(calls[0][2], { scope: 'operator.read' });
  let response;
  await calls[0][1]({ params: { arbitrary: 'input' }, respond: (...args) => { response = args; } });
  assert.equal(response[0], false);
  assert.equal(response[2].code, 'INVALID_REQUEST');
});
