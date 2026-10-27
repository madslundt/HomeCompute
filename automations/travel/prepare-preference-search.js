// Only public hotel text reaches the local embedding endpoint; exact offer
// identities stay beside the workflow and are never inferred from similarity.
const rows=$input.all(), query=rows[0]?.json.request?.preferences?.preferenceQuery || '';
if(typeof query!=='string' || query.length>500 || /[\r\n\x00-\x1f]/.test(query))throw Error('Invalid descriptive travel preference');
const bounded=(value,maximum=1700)=>{
  const text=String(value??'').replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  let result='';
  for(const c of text){if(Buffer.byteLength(result+c,'utf8')>maximum)break;result+=c;}
  return result;
};
const identity=(r,o)=>JSON.stringify([o.id,o.hotel?.id,o.supplier?.code,o.providerUrl,o.origin?.dt,o.return?.dt,r.request?.ageString]);
const candidates=[],seen=new Set();
if(query.trim())for(const row of rows){
  const r=row.json;
  for(const o of r.offers || []){
    if(!o.supplierVerified || typeof o.hotel?.id!=='string' || typeof o.providerUrl!=='string' || !o.providerUrl.startsWith('https://www.solfaktor.dk/'))continue;
    const id=identity(r,o);
    if(seen.has(id))continue;
    seen.add(id);
    candidates.push({id,text:bounded([o.hotel.name,o.hotel.description,o.hotel.room_name,o.destination?.name,
      o.destination?.country_name,...(Array.isArray(o.metabool)?o.metabool:[])].filter(x=>typeof x==='string').join('. '))});
  }
}
// Supplier verification is deliberately capped at six upstream. Do not make
// a partial semantic ranking if that contract changes.
const enabled=Boolean(query.trim() && candidates.length && candidates.length<=6);
const instruction='Instruct: Given family holiday preferences, retrieve hotel descriptions matching the requested atmosphere, facilities and location\nQuery: ';
return [{json:{embeddingEnabled:enabled,preferenceQuery:query,candidates:enabled?candidates:[],
  embeddingBody:{model:'embedding',encoding_format:'float',input:enabled?[instruction+bounded(query,1500),...candidates.map(c=>c.text)]:[]}}}];
