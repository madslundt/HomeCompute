const source=$('Add all-inclusive price').all(), prepared=$('Prepare hotel preference search').first().json;
const response=$input.first()?.json;
const identity=(r,o)=>JSON.stringify([o.id,o.hotel?.id,o.supplier?.code,o.providerUrl,o.origin?.dt,o.return?.dt,r.request?.ageString]);
const data=response?.data, candidates=prepared.candidates;
let scores=null;
if(prepared.embeddingEnabled && Array.isArray(data) && data.length===candidates.length+1){
  const vectors=new Map();let valid=true;
  for(const row of data){
    const v=row?.embedding;
    if(!Number.isInteger(row?.index) || row.index<0 || row.index>=data.length || vectors.has(row.index)
       || !Array.isArray(v) || v.length!==1024 || v.some(n=>typeof n!=='number' || !Number.isFinite(n))){valid=false;break;}
    const norm=Math.hypot(...v);
    if(!Number.isFinite(norm) || norm<=0){valid=false;break;}
    vectors.set(row.index,v.map(n=>n/norm));
  }
  if(valid && vectors.size===data.length){
    const q=vectors.get(0);
    scores=new Map(candidates.map((c,i)=>[c.id,q.reduce((s,n,j)=>s+n*vectors.get(i+1)[j],0)]));
  }
}
return source.map(row=>{
  const r=row.json;
  // Drop any upstream scores. Only vectors from this request may affect order.
  return {json:{...r,offers:(r.offers||[]).map(o=>{
    const {preferenceScore,preferenceQuery,...offer}=o;
    const score=scores?.get(identity(r,o));
    return typeof score==='number' && Number.isFinite(score)
      ? {...offer,preferenceScore:score,preferenceQuery:prepared.preferenceQuery}:offer;
  })}};
});
