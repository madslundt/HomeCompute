const zone = 'Europe/Copenhagen';
const now = new Date($now.toISO());
const dayFormat = new Intl.DateTimeFormat('sv-SE', {timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'});
const timeFormat = new Intl.DateTimeFormat('en-GB', {timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
const today = dayFormat.format(now);
const target = new Date(today + 'T12:00:00Z');
target.setUTCDate(target.getUTCDate() + 1);
const tomorrow = target.toISOString().slice(0, 10);
const read = name => { try { return $(name).first().json; } catch { return {}; } };
const parse = (value, fallback) => {
  if (typeof value !== 'string') return value ?? fallback;
  try { return JSON.parse(value); } catch { return fallback; }
};
const clean = value => String(value ?? '').replace(/\b[a-z][a-z0-9+.-]*:\/\/\S+|www\.\S+|\b(?:javascript|data|mailto|file|tel):\S+/giu, '[link]')
  .replace(/@/g, '@\u200b').replace(/[\u0000-\u001f\u007f]/gu, ' ').replace(/\s+/gu, ' ').trim();
const compact = value => {
  const chars = Array.from(clean(value));
  return chars.length > 120 ? chars.slice(0, 117).join('').replace(/\s+\S*$/u, '') + '…' : chars.join('');
};
const valueOf = value => value && typeof value === 'object' ? value.dateTime || value.date : value;
const localDay = value => {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return value;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? dayFormat.format(parsed) : '';
};
const clock = value => timeFormat.format(new Date(value));
const sourceStatus = (sensor, stamp, ttl) => {
  if (!sensor || sensor.error || ['unknown', 'unavailable'].includes(sensor.state) || !sensor.entity_id) return 'unavailable';
  const age = now.getTime() - Date.parse(stamp);
  if (!Number.isFinite(age) || age < -60000) return 'unknown';
  return age > ttl ? 'stale' : 'ok';
};
const source_status = {};
const entries = [];
const seen = new Set();
const add = (key, text, order) => {
  if (!text || seen.has(key)) return;
  seen.add(key);
  entries.push({text, order});
};
const declined = item => ['cancelled', 'canceled', 'completed'].includes(item.status)
  || ['not_attending', 'declined'].includes(item.participation_status)
  || ['not_attending', 'declined'].includes(item.registration_status)
  || item.self_response === 'declined';

const calendar = read('Get tomorrow calendar');
source_status.calendar = sourceStatus(calendar, calendar.attributes?.refreshed_at, 2 * 3600000);
const events = source_status.calendar === 'ok' ? parse(calendar.attributes?.events ?? calendar.attributes?.events_json, null) : [];
if (source_status.calendar === 'ok' && !Array.isArray(events)) source_status.calendar = 'unknown';
for (const event of Array.isArray(events) ? events : []) {
  if (!event || declined(event)) continue;
  const title = compact(event.summary || event.title);
  const start = valueOf(event.start), end = valueOf(event.end);
  const startDay = localDay(start), endDay = localDay(end);
  if (!title || !startDay || !endDay || startDay > tomorrow || endDay < tomorrow || /^Afvist\s*[·:–-]/iu.test(title)) continue;
  const allDay = /^\d{4}-\d{2}-\d{2}$/.test(start);
  if (allDay ? endDay <= tomorrow : endDay === tomorrow && clock(end) === '00:00') continue;
  if (!allDay && (!Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) || Date.parse(end) <= Date.parse(start))) continue;
  const tag = ['Alvira', 'Nora', 'Mads', 'Kristina'].includes(event.calendar_tag) ? event.calendar_tag + ': ' : '';
  const time = allDay ? 'Hele dagen' : startDay < tomorrow ? 'Fortsætter' : clock(start);
  add('event|' + tag + title.toLocaleLowerCase('da-DK') + '|' + start + '|' + end,
    `${time} · ${tag}${title}`, allDay ? 0 : Date.parse(start));
}

const school = read('Get tomorrow school reminders');
const payload = parse(school.attributes?.summary_payload, null);
source_status.school = sourceStatus(school, payload?.updated_at, 30 * 3600000);
const schoolTasks = payload ? parse(payload.tasks, null) : null;
if (source_status.school === 'ok' && !Array.isArray(schoolTasks)) source_status.school = 'unknown';
if (source_status.school === 'ok' && payload?.collector_partial) source_status.school = 'partial';
if (['ok', 'partial'].includes(source_status.school) && payload.active !== false) {
  for (const task of schoolTasks) {
    if (!task || task.active === false || task.conditional || task.completed || declined(task)) continue;
    if (!(task.very_important || task.requires_action || task.kind === 'homework' || ['important', 'critical'].includes(task.importance))) continue;
    const due = localDay(valueOf(task.due_at || task.action_due_date || task.due_date || task.event_date || task.date));
    if (due !== tomorrow) continue;
    const expiry = task.expires_at;
    if (expiry && (/^\d{4}-\d{2}-\d{2}$/.test(expiry) ? expiry < tomorrow : Date.parse(expiry) <= now.getTime())) continue;
    const title = compact(task.title || task.text);
    const detail = compact(task.short_description || task.detail);
    const content = [title, detail && detail !== title ? detail : ''].filter(Boolean).join(' · ');
    if (!content) continue;
    const child = ['Alvira', 'Nora'].includes(task.child) ? task.child + ': ' : '';
    add('school|' + child + content.toLocaleLowerCase('da-DK'), 'Aula · ' + child + content, task.importance === 'critical' ? -3 : -1);
  }
}

const family = read('Get shared family tasks');
source_status.family_tasks = sourceStatus(family, family.attributes?.updated_at, 5 * 60000);
if (source_status.family_tasks === 'ok' && !Array.isArray(family.attributes?.tasks)) source_status.family_tasks = 'unknown';
if (source_status.family_tasks === 'ok') {
  if (family.attributes.truncated) source_status.family_tasks = 'partial';
  for (const task of family.attributes.tasks) {
    const due = localDay(task?.due_at);
    if (task?.status !== 'open' || !due || due > tomorrow) continue;
    const title = compact(task.title);
    if (!title) continue;
    const assignee = compact(task.assignee);
    const overdue = Date.parse(task.due_at) < now.getTime();
    const prefix = overdue ? 'Overskredet: ' : due === today ? 'I dag: ' : '';
    add('family|' + task.task_id, `Familie · ${prefix}${title}${assignee ? ' (' + assignee + ')' : ' (ikke fordelt)'}`, overdue ? -4 : -2);
  }
}

entries.sort((a, b) => a.order - b.order || a.text.localeCompare(b.text, 'da'));
const names = {calendar: 'kalender', school: 'Aula', family_tasks: 'familieopgaver'};
const missing = Object.entries(source_status).filter(([, status]) => status !== 'ok')
  .map(([key, status]) => names[key] + (status === 'stale' ? ' (forældet)' : status === 'partial' ? ' (delvis)' : ' (ukendt)'));
const heading = '🌙 Klar til i morgen · ' + new Intl.DateTimeFormat('da-DK', {timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long'}).format(target);
const coverage = missing.length ? '\n\nDatagrundlag: ' + missing.join(', ') + '.' : '';
const lines = entries.slice(0, 8).map(row => row.text);
const render = () => heading + '\n' + (lines.length ? lines.join('\n') : entries.length ? '' : 'Ingen kendte daterede punkter i de tilgængelige kilder.')
  + (entries.length > lines.length ? '\n+' + (entries.length - lines.length) + ' flere punkter i kalenderen, Aula og familieopgaver.' : '') + coverage;
const full_message = render();
const encodedSize = text => {
  const json = JSON.stringify(text);
  return json.length + (json.match(/[^\x00-\x7f]/g) || []).length * 5;
};
let message = render();
while (encodedSize(message) > 1500 && lines.length) {
  lines.pop();
  message = render();
}
if (encodedSize(message) > 1500) throw new Error('Tomorrow preparation exceeds notification budget');
return [{json: {message, full_message, date: today, target_date: tomorrow, source_status}}];
