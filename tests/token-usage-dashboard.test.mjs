import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucket, localDate, summarize, validateData } from '../deploy/control-plane/token-usage/metrics.mjs';

const row = (day, model, input, output) => ({ period_start: day, model, input_tokens: input,
  output_tokens: output, total_tokens: input + output, requests: 1 });
const state = { from: '2026-10-01', to: '2026-10-31', trackingDate: '2026-10-18', period: 'week', model: 'all' };

test('weekly buckets start Monday and use Copenhagen calendar dates across DST', () => {
  assert.equal(bucket('2026-10-18', 'week'), '2026-10-12');
  assert.equal(bucket('2026-10-19', 'week'), '2026-10-19');
  assert.equal(localDate('2026-10-24T22:30:00Z'), '2026-10-25');
  assert.equal(localDate('2026-10-25T22:30:00Z'), '2026-10-25');
  assert.equal(localDate('2026-10-25T23:30:00Z'), '2026-10-26');
});

test('date/model filters and weekly/monthly totals agree with daily input', () => {
  const rows = [row('2026-10-18', 'home', 10, 2), row('2026-10-19', 'home', 20, 3),
    row('2026-10-19', 'automation', 30, 4), row('2026-11-01', 'home', 1000, 1000)];
  const result = summarize(rows, state);
  assert.equal(result.totals.total_tokens, 69);
  assert.deepEqual(result.periods.map(p => p.total_tokens), [12, 57, 0]);
  const model = summarize(rows, { ...state, model: 'home', from: '2026-10-19' });
  assert.equal(model.totals.total_tokens, 23);
  assert.deepEqual(model.models.map(row => row.model), ['home']);
  assert.equal(summarize(rows, { ...state, period: 'month' }).periods[0].total_tokens, 69);
});

test('unknown history before tracking is absent; observed empty days are zero', () => {
  const result = summarize([], { ...state, period: 'day', to: '2026-10-20' });
  assert.deepEqual(result.periods.map(row => row.date), ['2026-10-18', '2026-10-19', '2026-10-20']);
  assert.ok(result.periods.every(row => row.total_tokens === 0));
  assert.deepEqual(summarize([], { ...state, to: '2026-10-17' }).periods, []);
});

test('invalid snapshots cannot become displayed totals', () => {
  const data = { schema_version: 1, generated_at: '2026-10-21T07:00:00Z', tracking_since: '2026-10-21T07:00:00Z',
    timezone: 'Europe/Copenhagen', rows: [row('2026-10-21', 'home', 1, 2)] };
  assert.equal(validateData(data), data);
  assert.throws(() => validateData({ ...data, rows: [{ ...data.rows[0], input_tokens: -1 }] }));
  assert.throws(() => validateData({ ...data, generated_at: 'invalid' }));
});
