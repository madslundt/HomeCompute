import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucket, localDate, summarize, validateData, validatePricing, priceUsage } from '../deploy/control-plane/token-usage/metrics.mjs';

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

const cachedRow = (model = 'home') => ({ ...row('2026-10-21', model, 1_000_000, 100_000),
  cached_input_tokens: 800_000, cache_tracked_input_tokens: 1_000_000, cache_reported_requests: 1 });
const rates = { input: 2, cached: .5, output: 8 };

test('pricing separates cached input without double counting and compares the same volume', () => {
  const usage = cachedRow();
  assert.equal(priceUsage(usage, rates).estimated_cost, 1.6);
  assert.equal(priceUsage(usage, { input: 1, cached: .25, output: 4 }).estimated_cost, .8);
  assert.equal(priceUsage(usage, { ...rates, cached: null }).estimated_cost, 2.8);
  assert.equal(priceUsage(usage, { input: 0, cached: 0, output: 0 }).estimated_cost, 0);
  assert.equal(priceUsage(usage, { input: null, output: 8 }), null);
});

test('unknown historical cache usage stays unknown and uses uncached pricing', () => {
  const unknown = row('2026-10-21', 'home', 1_000_000, 100_000);
  const result = summarize([cachedRow(), unknown], state, { home: rates });
  assert.equal(result.totals.estimated_cost, 4.4);
  assert.equal(result.totals.cached_input_tokens, 800_000);
  assert.equal(result.totals.cache_tracked_input_tokens, 1_000_000);
  assert.equal(result.totals.cache_reported_requests, 1);
  assert.equal(result.totals.assumed_cache_input_tokens, 1_000_000);
  assert.equal(result.totals.priced_requests, 2);
});

test('missing rates make totals partial and filters apply before cost aggregation', () => {
  const rows = [cachedRow(), cachedRow('automation')];
  const result = summarize(rows, state, { home: rates });
  assert.equal(result.totals.estimated_cost, 1.6);
  assert.equal(result.totals.unpriced_requests, 1);
  assert.equal(result.periods.find(p => p.date === '2026-10-19').estimated_cost, 1.6);
  assert.equal(summarize(rows, { ...state, model: 'automation' }, { home: rates }).totals.priced_requests, 0);
  assert.equal(summarize(rows, { ...state, to: '2026-10-20' }, { home: rates }).totals.estimated_cost, 0);
});

test('stored rates and cache coverage reject invalid values and preserve explicit zero', () => {
  assert.equal(validatePricing({ currency: 'DKK', models: { home: { input: 0, output: 0 } } }).models.home.input, 0);
  for (const value of [-1, Infinity, NaN, '2', 1e10]) {
    assert.throws(() => validatePricing({ currency: 'USD', models: { home: { input: value } } }));
  }
  assert.throws(() => validatePricing({ currency: 'invalid', models: {} }));
  const snapshot = { schema_version: 1, generated_at: '2026-10-21T07:00:00Z', tracking_since: '2026-10-21T07:00:00Z',
    timezone: 'Europe/Copenhagen', rows: [cachedRow()] };
  assert.equal(validateData(snapshot), snapshot);
  for (const invalid of [{ cached_input_tokens: 1_000_001 }, { cache_tracked_input_tokens: 800 },
    { cache_reported_requests: 2 }, { cache_reported_requests: 0 }, { cached_input_tokens: -1 }, { cached_input_tokens: null }]) {
    assert.throws(() => validateData({ ...snapshot, rows: [{ ...cachedRow(), ...invalid }] }));
  }
  assert.equal(validateData({ ...snapshot, rows: [{ ...cachedRow(), cached_input_tokens: 0 }] }).rows[0].cached_input_tokens, 0);
});
