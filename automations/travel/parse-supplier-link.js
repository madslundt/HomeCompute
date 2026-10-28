const contexts=$('Check holiday weather').all();
return $input.all().flatMap((item,i)=>{
  const c=contexts[i]?.json, html=item.json.data;
  if (!c || typeof html!=='string') return [];
  const match=html.match(/<a\b[^>]*class="redirect-link[^"\n]*"[^>]*href="([^"]+)"/i);
  if (!match) return [];
  const providerUrl=match[1].replace(/&amp;/g,'&').replace(/&#(?:0?38|x26);/gi,'&');
  if (!providerUrl.startsWith('https://www.solfaktor.dk/') || /[\s<>"\\]/.test(providerUrl)) return [];
  const params=Object.fromEntries(providerUrl.split('?')[1]?.split('&').map(p=>{const n=p.indexOf('=');return [decodeURIComponent(p.slice(0,n)),decodeURIComponent(p.slice(n+1))];})||[]);
  if (params.airport!=='CPH' || params.departure!==c.offer.origin.dt.slice(0,10) || params.return!==c.offer.return.dt.slice(0,10)
      || params.room0adults!=='2' || params.room0childages!==c.request.childAges.join(',') || params.directFlight!=='true'
      || !params.cs1 || !/^\d+$/.test(params.genId||'')) return [];
  return [{json:{...c,providerUrl,providerParams:params}}];
});
