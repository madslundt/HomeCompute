const keys = ['requests', 'input_tokens', 'output_tokens', 'total_tokens'];
const cacheKeys = ['cached_input_tokens', 'cache_tracked_input_tokens', 'cache_reported_requests'];
const sumKeys = [...keys, ...cacheKeys, 'estimated_cost', 'priced_requests', 'unpriced_requests', 'assumed_cache_input_tokens'];
export const emptyTotals = () => Object.fromEntries(sumKeys.map(key => [key, 0]));
export const add = (target, row) => { for (const key of sumKeys) target[key] += row[key] ?? 0; return target; };
export const currencies = ['USD', 'DKK', 'EUR', 'GBP'];
const validRate = value => Number.isFinite(value) && value >= 0 && value <= 1e9;
export function validatePricing(value) {
  if (!value || !currencies.includes(value.currency) || !value.models || typeof value.models !== 'object' || Array.isArray(value.models)) throw new Error('Invalid pricing');
  const models = Object.fromEntries(Object.entries(value.models).map(([model, rates]) => {
    if (!model.trim() || model.length > 120 || !rates || typeof rates !== 'object') throw new Error('Invalid model pricing');
    return [model, Object.fromEntries(['input', 'cached', 'output'].map(key => {
      const rate = rates[key] ?? null;
      if (rate !== null && !validRate(rate)) throw new Error('Invalid rate');
      return [key, rate];
    }))];
  }));
  return { currency: value.currency, models };
}
export function priceUsage(row, rates) {
  if (!rates || !validRate(rates.input) || !validRate(rates.output)
      || (rates.cached != null && !validRate(rates.cached))) return null;
  const cached = row.cached_input_tokens ?? 0;
  const cachedRate = rates.cached ?? rates.input;
  return { estimated_cost: ((row.input_tokens - cached) * rates.input + cached * cachedRate + row.output_tokens * rates.output) / 1e6,
    assumed_cache_input_tokens: cachedRate === rates.input ? 0 : row.input_tokens - (row.cache_tracked_input_tokens ?? 0) };
}
export function localDate(value = new Date()) {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Copenhagen', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
}
export function shiftDate(day, days) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
export function bucket(day, period) {
  if (period === 'month') return `${day.slice(0, 7)}-01`;
  if (period === 'week') {
    const weekday = new Date(`${day}T12:00:00Z`).getUTCDay();
    return shiftDate(day, -((weekday + 6) % 7));
  }
  return day;
}
export function summarize(rows, { from, to, model = 'all', period = 'day', trackingDate }, pricing = {}) {
  const totals = emptyTotals(), models = new Map(), periods = new Map();
  // Dates before tracking began are unknown, rather than invented zero usage.
  let start = from < trackingDate ? trackingDate : from;
  if (start && to && start <= to) {
    for (let day = start; day <= to; day = shiftDate(day, 1)) {
      const key = bucket(day, period);
      if (!periods.has(key)) periods.set(key, { date: key, ...emptyTotals() });
    }
  }
  for (const row of rows) {
    if (row.period_start < from || row.period_start > to || (model !== 'all' && row.model !== model)) continue;
    const cost = priceUsage(row, Object.hasOwn(pricing, row.model) ? pricing[row.model] : null);
    const priced = { ...row, ...cost, priced_requests: cost ? row.requests : 0, unpriced_requests: cost ? 0 : row.requests };
    add(totals, priced);
    if (!models.has(row.model)) models.set(row.model, { model: row.model, ...emptyTotals() });
    add(models.get(row.model), priced);
    const key = bucket(row.period_start, period);
    if (!periods.has(key)) periods.set(key, { date: key, ...emptyTotals() });
    add(periods.get(key), priced);
  }
  return { totals, models: [...models.values()].sort((a, b) => b.total_tokens - a.total_tokens),
    periods: [...periods.values()].sort((a, b) => a.date.localeCompare(b.date)) };
}
export function validateData(data) {
  if (data.schema_version !== 1 || data.timezone !== 'Europe/Copenhagen' || !Array.isArray(data.rows)
      || !Number.isFinite(Date.parse(data.generated_at)) || !Number.isFinite(Date.parse(data.tracking_since))) throw new Error('Invalid usage snapshot');
  for (const row of data.rows) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(row.period_start) || typeof row.model !== 'string'
        || !keys.every(key => Number.isSafeInteger(row[key]) && row[key] >= 0)) throw new Error('Invalid usage row');
    if (cacheKeys.some(key => row[key] !== undefined) && (!cacheKeys.every(key => Number.isSafeInteger(row[key]) && row[key] >= 0)
        || row.cached_input_tokens > row.cache_tracked_input_tokens || row.cache_tracked_input_tokens > row.input_tokens
        || row.cache_reported_requests > row.requests || (!row.cache_reported_requests && row.cache_tracked_input_tokens))) throw new Error('Invalid cache usage');
  }
  return data;
}
