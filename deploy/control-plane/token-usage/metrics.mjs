const keys = ['requests', 'input_tokens', 'output_tokens', 'total_tokens'];
export const emptyTotals = () => Object.fromEntries(keys.map(key => [key, 0]));
export const add = (target, row) => { for (const key of keys) target[key] += row[key]; return target; };
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
export function summarize(rows, { from, to, model = 'all', period = 'day', trackingDate }) {
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
    add(totals, row);
    if (!models.has(row.model)) models.set(row.model, { model: row.model, ...emptyTotals() });
    add(models.get(row.model), row);
    const key = bucket(row.period_start, period);
    if (!periods.has(key)) periods.set(key, { date: key, ...emptyTotals() });
    add(periods.get(key), row);
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
  }
  return data;
}
