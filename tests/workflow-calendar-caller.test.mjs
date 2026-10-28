import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {CALENDAR_RECEIPT_GUARD, guardedCalendarAcknowledgment} from '../automations/semantic-calendar/caller-guard.mjs';

const original = `const item = $('Validate and classify event').item.json;
const state = $getWorkflowStaticData('global');
state.handled ??= {};
if (item.sourceKey) state.handled[item.sourceKey] = 123;
if (item.sourceType === 'gmail' && item.emailId) state.handled[item.emailId] = 123;
if (item.sourceType === 'telegram') state.telegramNextUpdateId = Number(item.telegramUpdateId) + 1;
if (item.sourceType !== 'gmail') return [];
return $input.item;`;

function run(receipt, source) {
  const state = {handled: {previous: 1}, telegramNextUpdateId: 99};
  let stateReads = 0;
  try {
    const result = vm.runInNewContext(`(function () {${guardedCalendarAcknowledgment(original)}})()`, {
      $input: {item: {json: receipt}},
      $: () => ({item: {json: source}}),
      $getWorkflowStaticData: () => {stateReads++; return state;},
    });
    return {result: JSON.parse(JSON.stringify(result)), state, stateReads};
  } catch (error) { return {error: error.message, state, stateReads}; }
}

for (const sourceType of ['gmail', 'telegram']) {
  test(`${sourceType} review/failure receipt cannot acknowledge source or access static state`, () => {
    for (const receipt of [{ok: false, review_required: true}, {ok: true, review_required: true},
      {ok: true, action: 'review'}, {ok: false}, {}, {ok: 'true'}]) {
      const value = run(receipt, {routeIndex: 2, sourceType, sourceKey: 'new', emailId: 'email', telegramUpdateId: '105'});
      assert.match(value.error, /source remains unprocessed/);
      assert.equal(value.stateReads, 0);
      assert.deepEqual(value.state, {handled: {previous: 1}, telegramNextUpdateId: 99});
      assert.equal('result' in value, false);
    }
  });
  test(`${sourceType} successful receipt preserves original acknowledgment path`, () => {
    const value = run({ok: true, action: 'noop'}, {routeIndex: 2, sourceType, sourceKey: 'new', emailId: 'email', telegramUpdateId: '105'});
    assert.equal(value.error, undefined);
    assert.equal(value.state.handled.new, 123);
    assert.equal(value.stateReads, 1);
    if (sourceType === 'gmail') {
      assert.equal(value.state.handled.email, 123);
      assert.equal(value.result.json.ok, true);
    } else {
      assert.equal(value.state.telegramNextUpdateId, 106);
      assert.deepEqual(value.result, []);
    }
  });
  test(`${sourceType} non-event bypass route 5 remains acknowledged without a calendar receipt`, () => {
    const value = run({}, {routeIndex: 5, sourceType, sourceKey: 'non-event', emailId: 'email', telegramUpdateId: '106'});
    assert.equal(value.error, undefined);
    assert.equal(value.state.handled['non-event'], 123);
  });
}

test('patch preserves the original source exactly except for one guard and is idempotent', () => {
  const patched = guardedCalendarAcknowledgment(original);
  assert.equal(patched.replace(CALENDAR_RECEIPT_GUARD, ''), original);
  assert.equal(guardedCalendarAcknowledgment(patched), patched);
  assert.throws(() => guardedCalendarAcknowledgment('changed source'), /format changed/);
});
