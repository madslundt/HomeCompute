import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const directory = new URL('../automations/semantic-battery/', import.meta.url);
const common = readFileSync(new URL('../semantic-research/retrieval-common.js', directory), 'utf8');
function run(file, input = {}, upstream = {}, mode = 'production') {
  const context = {$json: input, $now: {toISO: () => '2026-10-26T12:00:00+01:00'}, $execution: {mode},
    $: name => ({item: {json: upstream[name]}})};
  const code = readFileSync(new URL(file, directory), 'utf8').replaceAll('PREPARE_NODE', "'Prepared'");
  return JSON.parse(JSON.stringify(vm.runInNewContext('(function(){' + common + '\n' + code + '})()', context)));
}
const current = {prompt: 'Current source IDs only. Snapshot: {"value":42}', event_id: 'poll_12345678',
  captured_at: '2026-10-26T11:00:00+01:00', source_ids: ['sensor.live', 'opportunities'], deterministic_findings: [{key: 'telemetry_stale', severity: 'ALERT', message: 'Missing recent samples'}],
  dry_run: false, review_evidence_complete: false, max_computed_opportunity_dkk: 0};
test('Battery query and restoration preserve every current field and numeric gate', () => {
  const prepared = run('prepare-search.js', current).json;
  assert.equal(prepared.retrievalBody.profile, 'battery');
  assert.ok(prepared.retrievalBody.query.includes('telemetry_stale'));
  assert.ok(!prepared.retrievalBody.query.includes('Current source IDs only'));
  assert.ok(!prepared.retrievalBody.query.includes('Snapshot:'));
  for (const response of [{}, {error: 'timeout'}, {available: false}, {available: true, results: null}])
    assert.deepEqual(run('apply-history.js', response, {Prepared: prepared}).json, current);
});
test('Only dated prior validated history is advisory and never adds current source IDs', () => {
  const prepared = run('prepare-search.js', current).json;
  const results = [
    {id: 'a', text: 'A previous explanation', source_id: 'hourly:old-event', metadata: {captured_at: '2026-10-25T11:00:00Z', source: 'hourly'}},
    {id: 'future', text: 'Future', source_id: 'weekly:future', metadata: {captured_at: '2030-01-01T00:00:00Z', source: 'weekly'}},
    {id: 'other', text: 'Private other scope', source_id: 'notion:other', metadata: {captured_at: '2026-10-25T11:00:00Z', source: 'research'}}];
  const applied = run('apply-history.js', {available: true, results}, {Prepared: prepared}).json;
  assert.ok(applied.prompt.startsWith(current.prompt));
  assert.ok(applied.prompt.includes('Never use history to establish or clear a current fault'));
  assert.ok(applied.prompt.includes('A previous explanation'));
  assert.ok(!applied.prompt.includes('Private other scope'));
  assert.ok(!applied.prompt.includes('Future'));
  for (const [key, value] of Object.entries(current)) if (key !== 'prompt') assert.deepEqual(applied[key], value);
});
test('Hourly archive skips invalid and dry runs, keeps stable event identity and original capture date', () => {
  const valid = {valid: true, review: {status: 'OK', summary: 'Normal'}, event_id: current.event_id};
  const upstream = {'Select LLM Review': current};
  for (const input of [{...valid, dry_run: true}, {...valid, valid: false}, {...valid, failure: 'bad'}, {...valid, event_id: 'x'}])
    assert.equal(run('remember-hourly.js', input, upstream), null);
  assert.equal(run('remember-hourly.js', valid, {'Select LLM Review': {...current, dry_run: true}}), null);
  const saved = run('remember-hourly.js', valid, upstream).json.retrievalBody;
  assert.equal(saved.source_id, 'hourly:' + current.event_id);
  assert.equal(saved.records[0].metadata.captured_at, current.captured_at);
});
test('Weekly archive follows existing formatter, skips test/manual, and keeps period/report type provenance', () => {
  const upstream = {'Prepare Review Snapshot': {reportType: 'system_weekly', period: 'Week 2026-10-19 through 2026-10-25'}};
  assert.equal(run('remember-weekly.js', {report: 'Checked'}, upstream, 'manual'), null);
  assert.equal(run('remember-weekly.js', {report: 'Checked'}, upstream, 'test'), null);
  assert.equal(run('remember-weekly.js', {}, upstream), null);
  const saved = run('remember-weekly.js', {report: 'Checked review'}, upstream).json.retrievalBody;
  assert.ok(saved.source_id.startsWith('weekly:system_weekly:Week-2026-10-19'));
  assert.ok(saved.records[0].text.includes('Checked review'));
  assert.equal(saved.records[0].metadata.source, 'system_weekly');
});
