const configured = false;
const calendars = __CALENDAR_IDS__;
if (!configured) throw Error('Trusted calendar route setup required');
const aliases = ['mads', 'kristina', 'alvira', 'nora', 'family'];
const object = x => x && typeof x === 'object' && !Array.isArray(x);
const keys = (x, allowed, required = allowed) => object(x) && Object.keys(x).every(k => allowed.includes(k)) && required.every(k => k in x);
const fail = () => { throw Error('Calendar request outside finite policy'); };
if ($input.all().length !== 1 || Object.keys($json.query || {}).length || Object.keys($json.params || {}).length) fail();
const r = $json.body;
if (!keys(r, ['operation', 'arguments'])) fail();
const a = r.arguments, op = r.operation;
const mutable = ['summary', 'description', 'location', 'start', 'end', 'all_day'];
const policies = {
  list: [[], []], events: [['calendar', 'start', 'end', 'query'], ['calendar', 'start', 'end']],
  event: [['calendar', 'event_id'], ['calendar', 'event_id']],
  availability: [['calendars', 'start', 'end'], ['calendars', 'start', 'end']],
  create: [['calendar', 'request_id', ...mutable], ['calendar', 'request_id', 'summary', 'start', 'end', 'all_day']],
  update: [['calendar', 'event_id', 'expected_etag', 'request_id', ...mutable], ['calendar', 'event_id', 'expected_etag', 'request_id']],
};
if (!(op in policies) || !keys(a, ...policies[op])) fail();
if (Object.keys(calendars).sort().join() !== aliases.slice().sort().join() || aliases.some(k => typeof calendars[k] !== 'string' || !calendars[k].includes('@'))) fail();
if ('calendar' in a && !aliases.includes(a.calendar)) fail();
const limits = {summary:300, description:2000, location:300, query:200, event_id:1024, expected_etag:256, request_id:36, start:25, end:25, calendars:80, calendar:16};
for (const [k,v] of Object.entries(a)) {
  if (k === 'all_day') { if (typeof v !== 'boolean') fail(); continue; }
  if (typeof v !== 'string' || v.length > limits[k] || (k !== 'description' && v !== v.trim()) || /[\x00-\x08\x0b-\x1f]/.test(v) || (k !== 'description' && /[\n\t]/.test(v))) fail();
  if (!v.length && !['description','location'].includes(k)) fail();
}
if ('event_id' in a && !/^[A-Za-z0-9_-]{5,1024}$/.test(a.event_id)) fail();
if ('request_id' in a && !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(a.request_id)) fail();
if ('expected_etag' in a && !/^"[^"\r\n]{1,254}"$/.test(a.expected_etag)) fail();
if (op === 'update' && !mutable.some(k => k in a)) fail();
const date = s => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) fail();
  const d = new Date(s + 'T00:00:00Z');
  if (!Number.isFinite(+d) || d.toISOString().slice(0,10) !== s) fail();
  return +d;
};
const timed = s => {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$/.test(s)) fail();
  if (+s.slice(11,13)>23 || +s.slice(14,16)>59 || +s.slice(17,19)>59) fail();
  date(s.slice(0,10));
  const d = new Date(s);
  if (!Number.isFinite(+d)) fail();
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'longOffset'}).formatToParts(d).map(x => [x.type,x.value]));
  const local = `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
  const offset = p.timeZoneName.replace('GMT','') || '+00:00';
  if (!s.endsWith('Z') && s !== local + offset) fail();
  return {value:+d, canonical:local+offset};
};
let body = Object.fromEntries(['summary','description','location'].filter(k => k in a).map(k => [k,a[k]]));
const temporal = ['start','end','all_day'].filter(k => k in a);
if (['create','update'].includes(op) && temporal.length && temporal.length !== 3) fail();
if ('start' in a) {
  const allDay = a.all_day === true;
  const start = allDay ? {value:date(a.start)} : timed(a.start), end = allDay ? {value:date(a.end)} : timed(a.end);
  if (!(end.value > start.value && end.value - start.value <= 31*86400000)) fail();
  if (['create','update'].includes(op)) body = {...body, start:allDay?{date:a.start}:{dateTime:start.canonical,timeZone:'Europe/Copenhagen'}, end:allDay?{date:a.end}:{dateTime:end.canonical,timeZone:'Europe/Copenhagen'}};
}
let selected = [a.calendar];
if (op === 'list') selected = aliases;
if (op === 'availability') {
  selected = a.calendars.split(',');
  if (!selected.length || selected.length > 5 || new Set(selected).size !== selected.length || selected.some(k => !aliases.includes(k))) fail();
}
const base = 'https://www.googleapis.com/calendar/v3';
const eventId = a.event_id || (a.request_id ? 'hc' + a.request_id.replaceAll('-','') : null);
return selected.map(alias => {
  let url, method='GET', requestBody={};
  const path = base + '/calendars/' + encodeURIComponent(calendars[alias]);
  if (op === 'list') url=base+'/users/me/calendarList/'+encodeURIComponent(calendars[alias]);
  else if (op === 'availability') {
    url=base+'/freeBusy'; method='POST';
    requestBody={timeMin:a.start,timeMax:a.end,timeZone:'Europe/Copenhagen',items:[{id:calendars[alias]}]};
  } else if (op === 'events') {
    const query = {timeMin:a.start,timeMax:a.end,singleEvents:'true',orderBy:'startTime',maxResults:'10'};
    if (a.query) query.q=a.query;
    url=path+'/events?'+Object.entries(query).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
  } else url=path+'/events/'+encodeURIComponent(eventId);
  return {json:{operation:op,arguments:a,alias,calendar_id:calendars[alias],event_id:eventId,body,
    request:{method,url,body:requestBody},write_url:path+'/events'+(op==='update'?'/'+encodeURIComponent(eventId):'')}};
});
