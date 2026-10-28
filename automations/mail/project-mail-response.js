const context = $('Validate mail request').first().json;
const response = $json;
const refused = () => [{json:{ok:false,error:'Mail provider unavailable; no message changed'}}];
if (response.statusCode !== 200 || !response.body || typeof response.body !== 'object') return refused();
const raw = response.body;
const clean = (value, n) => typeof value === 'string' ? value.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'').slice(0,n) : '';
const identity = value => typeof value === 'string' && /^[A-Za-z0-9_+=/-]{1,1024}$/.test(value);
let result = {ok:true,account:context.account,truncated:false};
if (context.operation === 'search') {
  const rows = context.provider === 'gmail' ? (raw.threads || []) : raw.value;
  if (!Array.isArray(rows) || rows.some(r=>!r || !identity(context.provider==='gmail'?r.id:r.conversationId))) return refused();
  const seen = new Set();
  result.threads = rows.slice(0,10).flatMap(r => {
    const id = context.provider === 'gmail' ? r.id : r.conversationId;
    if (seen.has(id)) return [];
    seen.add(id);
    return [{thread_id:id,snippet:clean(context.provider==='gmail'?r.snippet:r.bodyPreview,500),
      ...(context.provider==='outlook'?{subject:clean(r.subject,200),sender:clean(r.from?.emailAddress?.address,300),received_at:clean(r.receivedDateTime,80)}:{})}];
  });
  result.truncated = rows.length > 10 || Boolean(raw.nextPageToken || raw['@odata.nextLink']);
} else {
  const rows = context.provider === 'gmail' ? raw.messages : raw.value;
  if (!Array.isArray(rows) || rows.some(r=>!r || !identity(r.id))) return refused();
  if (context.provider==='gmail' && raw.id!==context.thread_id) return refused();
  if (context.provider==='outlook' && rows.some(r=>r.conversationId!==context.thread_id)) return refused();
  result.thread_id=context.thread_id;
  result.truncated=rows.length>10 || Boolean(raw['@odata.nextLink']);
  result.messages=rows.slice(-10).map(r=>{
    if (context.provider==='outlook') {
      const body=r.body?.contentType==='text'?r.body.content:'';
      return {id:r.id,subject:clean(r.subject,200),sender:clean(r.from?.emailAddress?.address,300),received_at:clean(r.receivedDateTime,80),
        snippet:clean(r.bodyPreview,500),body:clean(body,1400),body_truncated:body.length>1400 || Boolean(r.body && r.body.contentType!=='text')};
    }
    const payload=r.payload || {}, headers=Array.isArray(payload.headers)?payload.headers:[];
    const header=name=>headers.find(h=>typeof h.name==='string' && h.name.toLowerCase()===name)?.value;
    const parts=[];
    function visit(part,depth=0) {
      if (!part || depth>2) return;
      if (part.mimeType==='text/plain' && typeof part.body?.data==='string') parts.push(part.body.data);
      for (const child of Array.isArray(part.parts)?part.parts.slice(0,20):[]) visit(child,depth+1);
    }
    visit(payload);
    let body='';
    try { body=parts.slice(0,5).map(p=>Buffer.from(p.slice(0,20000),'base64url').toString('utf8')).join('\n'); } catch { body=''; }
    return {id:r.id,subject:clean(header('subject'),200),sender:clean(header('from'),300),received_at:clean(header('date'),80),
      snippet:clean(r.snippet,500),body:clean(body || r.snippet,1400),body_truncated:body.length>1400 || parts.some(p=>p.length>20000) || !body};
  });
}
if (JSON.stringify(result).length>32000) return refused();
return [{json:result}];
