import { localDate, shiftDate, summarize, validateData } from './metrics.mjs';

const $ = id => document.getElementById(id);
const full = value => new Intl.NumberFormat('en-GB').format(value);
const compact = value => new Intl.NumberFormat('en-GB', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const dateLabel = day => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`));
let data = null, period = 'day', initialized = false, fetching = false;

function svgNode(tag, attributes = {}, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}
function rowsInto(id, rows, label) {
  $(id).replaceChildren();
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const value of [label(row), ...['requests', 'input_tokens', 'output_tokens', 'total_tokens'].map(key => full(row[key]))]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    $(id).append(tr);
  }
}
function chart(rows) {
  const svg = $('chart'); svg.replaceChildren();
  const width = Math.max(280, Math.min(1240, svg.getBoundingClientRect().width));
  const height = 275, left = 56, right = 20, top = 22, bottom = 37;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const w = width - left - right, h = height - top - bottom;
  const max = Math.max(1, ...rows.map(row => row.input_tokens + row.output_tokens));
  const power = 10 ** Math.floor(Math.log10(max));
  const ceiling = Math.ceil(max / power * 2) / 2 * power;
  for (let tick = 0; tick <= 4; tick++) {
    const y = top + h - h * tick / 4;
    svg.append(svgNode('line', { x1: left, x2: width - right, y1: y, y2: y, class: 'grid' }));
    svg.append(svgNode('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, compact(ceiling * tick / 4)));
  }
  svg.append(svgNode('line', { x1: left, x2: width - right, y1: top + h, y2: top + h, class: 'axis' }));
  const step = w / Math.max(rows.length, 1), barWidth = Math.min(58, step * .6);
  const labelStep = Math.max(1, Math.ceil(rows.length / (width < 700 ? 4 : 9)));
  rows.forEach((row, index) => {
    const x = left + step * (index + .5), input = row.input_tokens / ceiling * h, output = row.output_tokens / ceiling * h;
    svg.append(svgNode('rect', { x: x - barWidth / 2, y: top + h - input, width: barWidth, height: input, class: 'input' }));
    svg.append(svgNode('rect', { x: x - barWidth / 2, y: top + h - input - output, width: barWidth, height: output, class: 'output' }));
    if (row.total_tokens && rows.length <= 12) svg.append(svgNode('text', { x, y: top + h - input - output - 9, 'text-anchor': 'middle', class: 'total-label' }, compact(row.total_tokens)));
    const description = `${dateLabel(row.date)}: ${full(row.input_tokens)} input, ${full(row.output_tokens)} output, ${full(row.requests)} requests`;
    const hit = svgNode('rect', { x: x - step / 2, y: top, width: step, height: h, class: 'hit', tabindex: '0', role: 'img', 'aria-label': description });
    hit.append(svgNode('title', {}, description));
    for (const event of ['pointerenter', 'focus', 'click']) hit.addEventListener(event, () => { $('chart-detail').textContent = description; });
    svg.append(hit);
    if (index % labelStep === 0) svg.append(svgNode('text', { x, y: height - 9, 'text-anchor': 'middle' }, dateLabel(row.date)));
  });
}
function render() {
  if (!data || !$('from').value || !$('to').value) return;
  if ($('from').value > $('to').value) {
    $('status').textContent = 'Start date must be on or before end date.'; $('status').className = 'status error'; return;
  }
  const end = [$('to').value, localDate(data.generated_at)].sort()[0];
  const state = { from: $('from').value, to: end, model: $('model').value, period, trackingDate: localDate(data.tracking_since) };
  const result = summarize(data.rows, state);
  for (const [id, key] of Object.entries({ total: 'total_tokens', input: 'input_tokens', output: 'output_tokens', requests: 'requests' })) $(id).textContent = full(result.totals[key]);
  for (const button of document.querySelectorAll('[data-period]')) button.setAttribute('aria-pressed', String(button.dataset.period === period));
  chart(result.periods); $('chart-detail').textContent = '';
  $('empty').hidden = result.totals.requests !== 0;
  rowsInto('models', result.models, row => row.model);
  rowsInto('periods', result.periods, row => row.date);
  $('range-label').textContent = `${dateLabel(state.from)} – ${dateLabel(state.to)} ${state.to.slice(0, 4)}`;
  const params = new URLSearchParams({ period, from: state.from, to: state.to });
  if (state.model !== 'all') params.set('model', state.model);
  history.replaceState(null, '', `?${params}`);
  freshness();
}
function freshness(failed = false) {
  if (!data) return;
  const stale = Date.now() - Date.parse(data.generated_at) > 180000;
  $('status').className = `status${failed || stale ? ' warning' : ''}`;
  const time = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Copenhagen', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(data.generated_at));
  $('status').textContent = `${failed ? 'Refresh failed. Showing last snapshot. ' : stale ? 'Snapshot is stale. ' : ''}Updated ${time} · Europe/Copenhagen`;
  $('history').textContent = `History starts ${dateLabel(localDate(data.tracking_since))} ${localDate(data.tracking_since).slice(0, 4)}. Updates every minute.`;
}
async function refresh() {
  if (fetching) return;
  fetching = true; $('refresh').disabled = true;
  try {
    const response = await fetch('./usage.json', { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error('Snapshot unavailable');
    data = validateData(await response.json());
    const selectedModel = initialized ? $('model').value : new URLSearchParams(location.search).get('model') || 'all';
    $('model').replaceChildren(new Option('All models', 'all'));
    for (const model of [...new Set(data.rows.map(row => row.model))].sort()) $('model').add(new Option(model, model));
    $('model').value = [...$('model').options].some(option => option.value === selectedModel) ? selectedModel : 'all';
    if (!initialized) {
      const params = new URLSearchParams(location.search), today = localDate(), trackingDate = localDate(data.tracking_since);
      period = ['day', 'week', 'month'].includes(params.get('period')) ? params.get('period') : 'day';
      const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && Number.isFinite(Date.parse(value));
      $('from').value = validDate(params.get('from')) ? params.get('from') : [shiftDate(today, -29), trackingDate].sort().at(-1);
      $('to').value = validDate(params.get('to')) ? params.get('to') : today;
      $('from').max = today; $('to').max = today;
      initialized = true;
    }
    render();
  } catch {
    if (data) freshness(true);
    else { $('status').className = 'status error'; $('status').textContent = 'Usage snapshot unavailable. Try Refresh in a moment.'; }
  } finally { fetching = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', refresh);
$('filters').addEventListener('submit', event => event.preventDefault());
for (const id of ['from', 'to', 'model']) $(id).addEventListener('change', render);
for (const button of document.querySelectorAll('[data-period]')) button.addEventListener('click', () => { period = button.dataset.period; render(); });
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 120); });
// Keep return navigation on the same approved network used to open the page.
for (const link of document.querySelectorAll('header a')) {
  if (location.hostname.endsWith('.ts.net')) link.href = `http://${location.hostname}/`;
  else link.href = 'http://192.168.30.122/';
}
setInterval(refresh, 60000);
setInterval(() => { if (data && $('from').value <= $('to').value) freshness(); }, 15000);
refresh();
