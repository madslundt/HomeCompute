// Archive retrieved source wording only. Prompts, claims and generated summaries
// never become evidence. Failure to archive must not change the collector result.
const schoolEvidence = (() => {
  const plain = value => typeof value === 'string' ? value.replace(/<[^>]*>/g,' ')
    .replace(/&(nbsp|amp|quot|apos|aelig|oslash|aring);/gi,(_,key)=>({nbsp:' ',amp:'&',quot:'"',apos:"'",aelig:'æ',oslash:'ø',aring:'å'}[key.toLowerCase()]))
    .replace(/\s+/g,' ').trim() : '';
  const content = value => typeof value === 'string' ? plain(value) : value && typeof value==='object'
    ? plain(value.html || value.text || value.content || value.body || '') : '';
  const body = row => [...(Array.isArray(row?.messages)?row.messages:[]).map(message=>content(message.text||message.body)),
    content(row?.latestMessage?.text),content(row?.content||row?.description||row?.body||row?.text||row?.message)].filter(Boolean).join('\n');
  const success = value => value && typeof value==='object' && value.isError!==true && !value.error && value.ok!==false && value.success!==false &&
    !['error','failed','unauthorized','forbidden'].includes(String(value.status||'').toLowerCase()) &&
    !(Number(value.statusCode || value.httpStatus || value.status || 0)>=400);
  const parse = output => {
    if(!success(output))return null;
    const item=Array.isArray(output.content)?output.content.find(item=>item?.type==='text'):null;
    if(!item)return null;
    try {const value=typeof item.text==='string'?JSON.parse(item.text):item.text;return success(value)?value:null;} catch(_){return null;}
  };
  const family = tool => /^aula_messages_/.test(tool)?'message':/^aula_posts_/.test(tool)?'post':/^aula_calendar_/.test(tool)?'calendar':/^aula_ugebrev_/.test(tool)?'ugebrev':tool;
  const byteLength = value => {let total=0;for(const char of value){const code=char.codePointAt(0);total+=code<=127?1:code<=2047?2:code<=65535?3:4;}return total;};
  const clip = (value,budget) => {let result='',used=0;for(const char of value){const size=byteLength(char);if(used+size>budget)break;result+=char;used+=size;}return result;};
  const record = (tool,id,text,title='',time='') => {
    let original=String(id??'').trim();
    const prefixed=original.match(/^(aula_(?:messages|posts|calendar)_[a-z0-9_]+):(.+)$/);
    if(prefixed && family(prefixed[1])===family(tool))original=prefixed[2];
    if(!/^aula_(?!discover)[a-z0-9_]+$/.test(tool)||!original||original.length>440||!plain(text))return null;
    const key='aula:'+family(tool)+':'+original;
    return {id:key,text:clip(plain(text),16000),metadata:{source:key,source_tool:tool,source_record_id:original,
      source_timestamp:typeof time==='string'?time.slice(0,100):'',title:plain(title).slice(0,300)}};
  };
  const fromRaw = (sources,context) => {
    const records=[];
    for(const source of sources) {
      const payload=parse(source.output);if(!payload)continue;
      const tool=source.tool;
      const rows=Array.isArray(payload)?payload:tool==='aula_calendar_events'?payload.events:tool==='aula_messages_list_threads'?payload.threads:tool==='aula_posts_list'?payload.posts:tool==='aula_ugebrev_minuddannelse'?payload.items:[];
      for(const row of Array.isArray(rows)?rows:[]) {
        if(!success(row))continue;
        let id=row.id,title=row.subject||row.title||row.name||row.summary||'',time=row.latestMessage?.sendDateTime||row.date||row.startedTime||row.updatedAt||row.createdAt||row.startDateTime||row.start?.dateTime||row.start?.date||'';
        if(tool==='aula_ugebrev_minuddannelse') {
          const child=(context.aulaDiscovery?.children||[]).find(child=>plain(child.name).toLocaleLowerCase('da-DK')===plain(row.childName).toLocaleLowerCase('da-DK'));
          if(!child?.id||!/^\d{4}-W\d{2}$/.test(context.isoWeek||'')||!/^\d{4}-\d{2}-\d{2}/.test(row.date||''))continue;
          id='aula_ugebrev_minuddannelse:'+child.id+':'+context.isoWeek+':'+row.date;
          title='Ugeplan '+context.isoWeek;
        }
        const temporal=tool==='aula_calendar_events'?[typeof row.start==='string'?row.start:row.start?.dateTime||row.start?.date||row.startDateTime||'',
          typeof row.end==='string'?row.end:row.end?.dateTime||row.end?.date||row.endDateTime||''].filter(Boolean).join(' '):'';
        const sourceBody=[body(row),temporal].filter(Boolean).join('\n');
        const text=[plain(title),sourceBody].filter(Boolean).join('\n');
        // A title alone is insufficient evidence of fetched source content.
        if(!sourceBody)continue;
        const entry=record(tool,id,text,title,time);if(entry)records.push(entry);
      }
    }
    return records;
  };
  const fromEvidence = value => {
    let rows;try{rows=typeof value==='string'?JSON.parse(value):value;}catch(_){return [];}
    if(!Array.isArray(rows))return [];
    return rows.filter(row=>row?.found===true && typeof row.source_text==='string' && plain(row.source_text)).map(row=>
      record(String(row.tool||''),row.source_id,[plain(row.source_title),plain(row.source_text)].filter(Boolean).join('\n'),row.source_title,row.source_date)).filter(Boolean);
  };
  const requests = rows => {
    const unique=new Map();
    for(const row of rows)if(row && (!unique.has(row.id)||unique.get(row.id).text.length<row.text.length))unique.set(row.id,row);
    const batches=[];let records=[],bytes=0;
    for(const row of unique.values()) {
      const size=byteLength(JSON.stringify(row));
      if(records.length && (records.length>=128 || bytes+size>120000)){batches.push({profile:'school',operation:'school_ingest',records});records=[];bytes=0;}
      records.push(row);bytes+=size;
    }
    if(records.length)batches.push({profile:'school',operation:'school_ingest',records});
    return batches.length?batches:[{profile:'school',operation:'school_ingest',records:[]}];
  };
  return {parse,record,fromRaw,fromEvidence,requests};
})();
