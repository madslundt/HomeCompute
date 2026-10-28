// Embeddings discover review candidates; this code never produces a calendar write.
const semanticCalendar = (() => {
  const text = value => String(value ?? '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  const day = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? value : Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat('sv-SE', {timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value)) : '';
  const title = value => text(value).replace(/^(?:(?:Deltager|Uafklaret|Afvist)\s*[·:–-]\s*)+/i, '');
  const role = event => {
    const value = title(event.summary).toLocaleLowerCase('da-DK');
    if (/^svar\b|\b(?:tilmeld\w*|frameld\w*|tilmelding\w*|rsvp)\b|\bmeld\w* (?:til|fra)|\b(?:giv besked|svar senest|svarfrist|svar ønskes)\b/.test(value)) return 'rsvp';
    if (/\b(?:ansøg\w*|ansøgning\w*|aflever\w*|betaling\w*|betal|frist|senest)\b/.test(value)) return 'deadline';
    if (/^(?:husk|medbring|tag\b.*\bmed)|^til sfo.*\bmedbring\b/.test(value)) return 'preparation';
    return ['event','rsvp','deadline','preparation'].includes(event.eventKind) ? event.eventKind : 'event';
  };
  const normalize = (raw, entity, id) => {
    if (!raw || raw.status === 'cancelled' || !text(raw.summary) || !entity) return null;
    const start = typeof raw.start === 'string' ? raw.start : raw.start?.date || raw.start?.dateTime;
    const end = typeof raw.end === 'string' ? raw.end : raw.end?.date || raw.end?.dateTime;
    if (!day(start) || !Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) || Date.parse(end) <= Date.parse(start)) return null;
    return {id, source_id:raw.id || raw.eventId, entity, owner:text(raw.organizer?.email),
      title:title(raw.summary), day:day(start), role:role(raw), start, end,
      allDay:typeof raw.start === 'string' ? Boolean(raw.allDay) : Boolean(raw.start?.date),
      recurring:Boolean(raw.recurringEventId || raw.recurrence?.length || raw.originalStartTime || raw.repetitive || raw.recurring),
      source:raw};
  };
  const compatible = (a, b) => Boolean(a && b && a.entity === b.entity && a.day === b.day && a.role === b.role &&
    a.allDay === b.allDay && a.recurring === b.recurring &&
    (!a.owner || !b.owner || a.owner === b.owner) &&
    (a.allDay ? a.start === b.start && a.end === b.end :
      Date.parse(a.start) === Date.parse(b.start) && Date.parse(a.end) === Date.parse(b.end)));
  const prepare = (rows, context = {}) => {
    const unique = new Map();
    for (const row of rows) {
      if (row.work === true || row.readOnly === true) continue;
      const candidate = normalize(row.event, row.entity, '');
      if (!candidate || !candidate.source_id || context.startDate && candidate.day < context.startDate || context.endDate && candidate.day >= context.endDate) continue;
      const key = row.entity + '|' + candidate.source_id;
      const previous = unique.get(key);
      if (!previous || (Date.parse(row.event.updated) || 0) > (Date.parse(previous.source.updated) || 0)) unique.set(key, candidate);
    }
    const records = [...unique.values()].map((row, index) => ({...row,id:'r'+index}));
    const groups = [];
    for (const row of records) {
      const group = groups.find(group => compatible(group[0],row) && group[0].owner === row.owner);
      if (group) group.push(row); else groups.push([row]);
    }
    const requests = [];
    const request = (group, scope) => ({profile:'calendar',operation:'pairs',records:group.map(row => ({
      id:row.id,text:row.title.slice(0,350),metadata:{calendar_id:scope,day:row.day,role:row.role,start:row.start,end:row.end}}))});
    for (let index=0; index<groups.length; index++) {
      const group=groups[index];
      if (group.length<2) continue;
      // Block pairs cover all combinations without ever exceeding 64 records/request.
      const blocks=[]; for(let offset=0;offset<group.length;offset+=32) blocks.push(group.slice(offset,offset+32));
      for(let left=0;left<blocks.length;left++) for(let right=left;right<blocks.length;right++) {
        const selected=left===right?blocks[left]:[...blocks[left],...blocks[right]];
        if(selected.length>1) requests.push(request(selected,'g'+index));
      }
    }
    return {records,requests:requests.length?requests:[{profile:'calendar',operation:'pairs',records:[]}]};
  };
  const candidates = (prepared, responses, threshold = 0.86) => {
    const found=new Map(),records=new Map(prepared.records.map(row=>[row.id,row]));
    for(let index=0;index<responses.length;index++) {
      const response=responses[index]?.body || responses[index];
      if(response?.available!==true || !Array.isArray(response.pairs)) continue;
      const allowed=new Set(prepared.requests[index]?.records.map(row=>row.id)||[]);
      for(const pair of response.pairs) {
        if(!allowed.has(pair.left_id)||!allowed.has(pair.right_id)||pair.left_id===pair.right_id||!Number.isFinite(pair.score)||pair.score<threshold)continue;
        const left=records.get(pair.left_id),right=records.get(pair.right_id);
        if(!compatible(left,right))continue;
        const key=[left.entity,left.day,...[left.source_id,right.source_id].sort()].join('|');
        if(!found.has(key))found.set(key,{left,right,score:pair.score});
      }
    }
    return [...found.values()];
  };
  const prepareIntake = (planned, existing) => {
    const empty={records:[],requests:[{profile:'calendar',operation:'pairs',records:[]}]};
    if(planned.action!=='create')return empty;
    const incoming=normalize(planned,planned.person,'incoming');
    if(!incoming)return empty;
    const records=[incoming,...existing.map((event,index)=>normalize(event,planned.person,'e'+index)).filter(row=>compatible(incoming,row))];
    const requests=[];
    for(let offset=1;offset<records.length;offset+=63)requests.push({profile:'calendar',operation:'pairs',records:[incoming,...records.slice(offset,offset+63)].map(row=>({
      id:row.id,text:row.title.slice(0,350),metadata:{calendar_id:'intake',day:row.day,role:row.role,start:row.start,end:row.end}}))});
    return {records,requests:requests.length?requests:empty.requests};
  };
  const appendReviews = (audit, pairs) => {
    const reviews=[...(audit.reviews||[])];
    for(const pair of pairs) {
      const {left,right}=pair,ids=[left.source_id,right.source_id].sort();
      const covered=(audit.plans||[]).some(plan=>plan.entity===left.entity&&[plan.keep,...plan.duplicates].some(event=>event.id===ids[0])&&[plan.keep,...plan.duplicates].some(event=>event.id===ids[1])) ||
        reviews.some(review=>review.date===left.day&&review.entities?.includes(left.entity)&&ids.every(id=>review.ids?.includes(id)));
      const title=[left,right].sort((a,b)=>a.source_id.localeCompare(b.source_id))[0].title;
      if(!covered)reviews.push({reason:'semantic_candidate',entities:[left.entity],ids,date:left.day,title});
    }
    return {...audit,reviews};
  };
  return {day,role,normalize,compatible,prepare,prepareIntake,candidates,appendReviews};
})();
