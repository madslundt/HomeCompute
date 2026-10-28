const zone = 'Europe/Copenhagen';
const now = new Date($now.toISO());
const dayFormat = new Intl.DateTimeFormat('sv-SE', {timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'});
const timeFormat = new Intl.DateTimeFormat('en-GB', {timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
const clean = value => String(value ?? '').replace(/\b[a-z][a-z0-9+.-]*:\/\/\S+|www\.\S+|\b(?:javascript|data|mailto|file|tel):\S+/giu, '[link]').replace(/@/g, '@\u200b').replace(/[\u0000-\u001f\u007f]/gu, ' ').replace(/\s+/gu, ' ').trim();
const parse = (value, fallback) => {
  if (typeof value !== 'string') return value ?? fallback;
  try { return JSON.parse(value); } catch { return fallback; }
};
const localDay = value => {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return value;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? dayFormat.format(date) : '';
};
const today = localDay(now);
const clock = value => timeFormat.format(new Date(value)).replace(':00', '');
const read = name => { try { return $(name).first().json; } catch { return {}; } };
const usable = item => item && item.entity_id && !item.error && !['unknown', 'unavailable'].includes(item.state);
const sourceStatus = (sensor, stamp, ttl) => {
  if (!usable(sensor)) return 'unavailable';
  const age = now.getTime() - Date.parse(stamp);
  if (!Number.isFinite(age) || age < -60000) return 'unknown';
  return age > ttl ? 'stale' : 'ok';
};
const source_status = {};
const compact = value => {
  const text = clean(value);
  const characters = Array.from(text);
  return characters.length <= 120 ? text : characters.slice(0, 117).join('').replace(/\s+\S*$/u, '') + '…';
};

const weather = read('Get Home daytime forecast');
source_status.weather = sourceStatus(weather, weather.state, 2 * 3600000);
const rawForecast = source_status.weather === 'ok' ? parse(weather.attributes?.daytime_forecast, []) : [];
const forecast = [...new Map((Array.isArray(rawForecast) ? rawForecast : []).filter(row => row && localDay(row.datetime) === today
  && Number.isFinite(Date.parse(row.datetime))).map(row => [Date.parse(row.datetime), row])).values()]
  .sort((a, b) => Date.parse(a.datetime) - Date.parse(b.datetime));
const temperatures = forecast.filter(row => row.temperature !== null && row.temperature !== '' && row.temperature !== undefined)
  .map(row => Number(row.temperature)).filter(Number.isFinite);
if (!temperatures.length && source_status.weather === 'ok') source_status.weather = 'unknown';
const low = Math.round(Math.min(...temperatures));
const high = Math.round(Math.max(...temperatures));
const conditions = {
  sunny: 'solrigt', 'clear-night': 'klart', partlycloudy: 'delvist skyet', cloudy: 'overvejende skyet',
  rainy: 'regnfuldt', pouring: 'kraftig regn', snowy: 'sne', 'snowy-rainy': 'slud', fog: 'tåget',
  windy: 'blæsende', 'windy-variant': 'blæsende', lightning: 'torden', 'lightning-rainy': 'torden og regn', hail: 'hagl',
};
const counts = new Map();
for (const row of forecast) if (Object.hasOwn(conditions, row.condition)) counts.set(row.condition, (counts.get(row.condition) || 0) + 1);
const dominant = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
const temperature = low === high ? `${low}°C` : `${low}–${high}°C`;
const overview = `${temperature}${dominant ? ', ' + conditions[dominant] : ''}.`;

// Hourly rows bound rain windows. Never invent an end beyond the available forecast,
// or bridge missing hours as if the rain were continuous.
const isRain = row => !['snowy', 'hail'].includes(row.condition) && (
  ['rainy', 'pouring', 'lightning-rainy', 'snowy-rainy'].includes(row.condition)
  || Number(row.precipitation_probability) >= 40 || Number(row.precipitation) >= 0.2);
const rainWindows = [];
let current = null;
for (let i = 0; i < forecast.length; i++) {
  const row = forecast[i];
  const time = Date.parse(row.datetime);
  if (current && time - current.last > 3600000) {
    rainWindows.push(current);
    current = null;
  }
  if (isRain(row)) {
    if (!current) current = {start: time, last: time, end: null, clippedStart: i === 0};
    current.last = time;
  } else if (current) {
    current.end = time;
    rainWindows.push(current);
    current = null;
  }
}
if (current) rainWindows.push(current);
const rainSentences = rainWindows.map(window => {
  if (window.clippedStart && window.end) return `Regn frem til ca. kl. ${clock(window.end)}.`;
  if (window.end) return `Regn ca. kl. ${clock(window.start)}–${clock(window.end)}.`;
  return `Regn fra ca. kl. ${clock(window.start)} (sluttid ukendt).`;
});

const entries = [];
const seen = new Set();
const add = (key, text, order) => {
  if (!clean(text) || seen.has(key)) return;
  seen.add(key);
  entries.push({text, order});
};
const valueOf = value => typeof value === 'object' && value !== null ? value.dateTime || value.date : value;
const agenda = read('Get today calendar');
source_status.calendar = sourceStatus(agenda, agenda.attributes?.refreshed_at, 2 * 3600000);
const rawEvents = source_status.calendar === 'ok' ? parse(agenda.attributes?.events ?? agenda.attributes?.events_json, null) : [];
if (source_status.calendar === 'ok' && !Array.isArray(rawEvents)) source_status.calendar = 'unknown';
for (const event of Array.isArray(rawEvents) ? rawEvents : []) {
  const title = compact(event?.summary || event?.title);
  const start = valueOf(event?.start);
  const end = valueOf(event?.end);
  if (!title || !localDay(start) || !localDay(end) || ['cancelled', 'canceled'].includes(event.status)
    || ['not_attending', 'declined'].includes(event.participation_status) || event.self_response === 'declined' || /^Afvist\s*[·:–-]/iu.test(title)) continue;
  const allDay = /^\d{4}-\d{2}-\d{2}$/.test(start);
  // All-day ends are exclusive; timed events can span midnight.
  if (localDay(start) > today || (allDay ? localDay(end) <= today : new Date(end) <= now)) continue;
  if (localDay(start) !== today && localDay(end) < today) continue;
  const tag = ['Alvira', 'Nora', 'Mads', 'Kristina'].includes(event.calendar_tag) ? `${event.calendar_tag}: ` : '';
  const time = allDay ? 'Hele dagen' : localDay(start) < today ? 'I gang' : clock(start);
  add(`event|${tag}${title.toLocaleLowerCase('da-DK')}|${start}|${end}`, `${time} · ${tag}${title}`, allDay ? 0 : Date.parse(start));
}

for (const name of ['Get important reminders', 'Get morning reminders']) {
  const sensor = read(name);
  const payload = parse(sensor.attributes?.summary_payload, sensor.attributes || {});
  const key = name === 'Get important reminders' ? 'school' : 'morning_reminders';
  source_status[key] = sourceStatus(sensor, payload?.updated_at, 30 * 3600000);
  if (source_status[key] !== 'ok') continue;
  if (!payload || payload.active === false || sensor.state === 'inactive') continue;
  if (payload.expires_at && new Date(payload.expires_at) <= now) continue;
  const fresh = localDay(payload.updated_at) === today;
  const tasks = parse(payload.tasks, []);
  for (const task of Array.isArray(tasks) ? tasks : []) {
    if (!task || task.active === false || task.conditional || task.completed || task.upcoming_important
      || task.participation_status === 'not_attending' || task.registration_status === 'not_attending') continue;
    if (!(task.very_important || task.requires_action || ['important', 'critical'].includes(task.importance))) continue;
    const due = valueOf(task.due_at || task.action_due_date || task.due_date || task.event_date || task.date);
    if (due ? localDay(due) !== today : !fresh) continue;
    const expiry = task.expires_at;
    if (expiry && (/^\d{4}-\d{2}-\d{2}$/.test(expiry) ? expiry < today : new Date(expiry) <= now)) continue;
    const title = compact(task.title || task.text);
    const detail = compact(task.short_description || task.detail);
    const content = [title, detail && detail !== title ? detail : ''].filter(Boolean).join(' · ');
    if (!content) continue;
    const child = ['Alvira', 'Nora'].includes(task.child) ? `${task.child}: ` : '';
    add(`reminder|${child}${content.toLocaleLowerCase('da-DK')}`, child + content, task.importance === 'critical' ? 1 : Number.MAX_SAFE_INTEGER);
  }
  // The morning source also supports a simple dated message without tasks.
  if (name === 'Get morning reminders' && (!Array.isArray(tasks) || !tasks.length) && fresh) {
    add('morning|' + clean(payload.text).toLocaleLowerCase('da-DK'), compact(payload.text), Number.MAX_SAFE_INTEGER);
  }
}

const family = read('Get shared family tasks');
source_status.family_tasks = sourceStatus(family, family.attributes?.updated_at, 5 * 60000);
if (source_status.family_tasks === 'ok' && !Array.isArray(family.attributes?.tasks)) source_status.family_tasks = 'unknown';
if (source_status.family_tasks === 'ok') {
  if (family.attributes.truncated) source_status.family_tasks = 'partial';
  for (const task of family.attributes.tasks) {
    const due = localDay(task?.due_at);
    if (task?.status !== 'open' || !due || due > today) continue;
    const title = compact(task.title);
    if (!title) continue;
    const assignee = compact(task.assignee);
    add('family|' + task.task_id, `Familie · ${due < today ? 'Overskredet: ' : ''}${title}${assignee ? ' (' + assignee + ')' : ' (ikke fordelt)'}`, due < today ? -2 : -1);
  }
}
entries.sort((a, b) => a.order - b.order || a.text.localeCompare(b.text, 'da'));
const lines = entries.slice(0, 8).map(entry => entry.text);
// APNs allows 4096 bytes for the whole payload. The HA relay also includes the
// body in FCM's notification envelope: leave room for both copies and metadata.
// Count JSON escapes and pessimistically escape every non-ASCII UTF-16 unit, so
// Danish letters and emoji are safe regardless of the relay's JSON encoding.
const messageByteBudget = 1500;
const encodedSize = text => {
  const json = JSON.stringify(text);
  return json.length + (json.match(/[^\x00-\x7f]/g) || []).length * 5;
};
let rainCount = rainSentences.length;
const render = () => {
  const rain = rainSentences.slice(0, rainCount);
  if (rainCount < rainSentences.length) rain.push('Flere regnperioder.');
  const weatherText = source_status.weather === 'ok' ? `🌦️ Vejret i dag\n${overview}${rain.length ? ' ' + rain.join(' ') : ''}` : '🌅 Morgenoverblik';
  const reminders = [...lines];
  if (entries.length > lines.length) reminders.push(`+${entries.length - lines.length} flere punkter i kalenderen og påmindelserne.`);
  const names = {weather:'vejr',calendar:'kalender',school:'Aula',morning_reminders:'morgenpåmindelser',family_tasks:'familieopgaver'};
  const missing = Object.entries(source_status).filter(([,status]) => status !== 'ok').map(([key,status]) => names[key] + (status === 'stale' ? ' (forældet)' : status === 'partial' ? ' (delvis)' : ' (ukendt)'));
  return weatherText + (reminders.length ? `\n\n📌 Husk\n${reminders.join('\n')}` : '') + (missing.length ? '\n\nDatagrundlag: ' + missing.join(', ') + '.' : '');
};
const full_message = render();
const home_message = source_status.weather === 'ok'
  ? `🌦️ Vejret i dag\n${overview}${rainSentences.length ? ' ' + rainSentences.join(' ') : ''}`
  : '🌦️ Vejret i dag\nVejrdata er ikke tilgængelige.';
let message = render();
while (encodedSize(message) > messageByteBudget) {
  if (lines.length) lines.pop();
  else if (rainCount) rainCount--;
  else throw new Error('Weather summary exceeds the iOS message budget');
  message = render();
}
return [{json: {message, full_message, home_message, date: today, target_date: today, source_status}}];
