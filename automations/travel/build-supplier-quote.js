const contexts=$('Read supplier link').all();
return $input.all().flatMap((item,i)=>{
  const c=contexts[i]?.json, html=item.json.data;
  if (!c || typeof html!=='string') return [];
  const decoded=html.replace(/\\"/g,'"'), ids=[...decoded.matchAll(/"hotelId"\s*:\s*(\d+)/g)].map(m=>Number(m[1]));
  if (!ids.length || new Set(ids).size!==1 || !Number.isSafeInteger(ids[0]) || ids[0]<=0) return [];
  const p=c.providerParams;
  const quoteBody={pageData:{sapiId:ids[0]},sapiSearchData:{departure:{code:'CPH'},dates:{startDate:p.departure,endDate:p.return},
    roomConfiguration:[{id:0,adults:2,children:2,childAges:c.request.childAges}]},cs1:p.cs1,meta:'reisegiganten',log:false,
    generationId:p.genId,locale:'dk',infantPrice:Number(p.infp||0),isDirectFlight:true};
  return [{json:{...c,quoteBody,quoteUrl:'https://www.solfaktor.dk/custom-api/yggdrasil/basket/getPackage'}}];
});
