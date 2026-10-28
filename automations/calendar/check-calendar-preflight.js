const contexts = $('Validate calendar request').all().map(x => x.json);
const rows = $input.all().map(x => x.json);
if (rows.length !== contexts.length) throw Error('Calendar preflight incomplete');
const op = contexts[0].operation;
const clean = (x,n) => typeof x === 'string' ? x.replace(/[\x00-\x08\x0b-\x1f]/g,'').slice(0,n) : '';
const interval = x => x && typeof x==='object' && typeof x.start==='string' && typeof x.end==='string'
  && [x.start,x.end].every(s=>/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(s)&&Number.isFinite(Date.parse(s)))
  && Date.parse(x.end)>Date.parse(x.start);
const deny = reason => [{json:{write:false,result:{ok:false,reason}}}];
if (op === 'list') {
  if (rows.some(r => r.statusCode !== 200)) return deny('calendar_metadata_unavailable');
  return [{json:{write:false,result:{ok:true,calendars:rows.map((r,i) => ({alias:contexts[i].alias,name:clean(r.body.summary,200),access:clean(r.body.accessRole,32)}))}}}];
}
if (op === 'availability') {
  return [{json:{write:false,result:{ok:true,calendars:rows.map((r,i) => {
    const c=contexts[i], b=r.body?.calendars?.[c.calendar_id];
    if (r.statusCode !== 200 || !b || ('errors' in b && (!Array.isArray(b.errors)||b.errors.length)) || !Array.isArray(b.busy) || b.busy.length > 25 || b.busy.some(x=>!interval(x))) return {alias:c.alias,status:'unknown',busy:[]};
    return {alias:c.alias,status:'known',busy:b.busy.map(x => ({start:clean(x.start,40),end:clean(x.end,40)}))};
  })}}}];
}
const r=rows[0],c=contexts[0],a=c.arguments,e=r.body;
if (op === 'events') {
  if (r.statusCode !== 200 || !Array.isArray(e.items)) return deny('calendar_events_unavailable');
  return [{json:{write:false,result:{ok:true,events:e.items.slice(0,10),truncated:typeof e.nextPageToken==='string'&&e.nextPageToken.length>0}}}];
}
if (op === 'event') {
  if (r.statusCode === 404) return [{json:{write:false,result:{ok:true,event:null}}}];
  if (r.statusCode !== 200 || e.id !== c.event_id) return deny('calendar_event_unavailable');
  return [{json:{write:false,result:{ok:true,event:e}}}];
}
if (op === 'create') {
  if (r.statusCode !== 404) return deny('event_id_exists_or_unavailable');
  c.body={...c.body,id:c.event_id,extendedProperties:{private:{homecompute_request:a.request_id}}};
} else {
  const p=e?.extendedProperties?.private || {};
  if (r.statusCode !== 200 || e.id !== c.event_id || e.etag !== a.expected_etag) return deny('stale_or_unavailable_event');
  if (e.status !== 'confirmed' || (e.eventType && e.eventType !== 'default') || e.organizer?.self !== true || e.attendees?.length || e.attendeesOmitted || e.recurrence?.length || e.recurringEventId || ['sourceType','sourceId','sourceKey','aulaCalendar'].some(k => k in p)) return deny('event_not_safe_to_change');
}
return [{json:{write:true,request:{method:op==='create'?'POST':'PATCH',url:c.write_url+'?sendUpdates=none',body:c.body,headers:op==='update'?{'If-Match':a.expected_etag}:{}}}}];
