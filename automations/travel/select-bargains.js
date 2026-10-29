const responses=$input.all();
const requests=responses.map(x=>x.json.request);
if (requests.length!==responses.length || !requests.length) throw new Error('Travel source response count differs');
const p=requests[0].preferences;
const state=$getWorkflowStaticData('global');
const sent=state.sent || {};
const found=[];
const ageAt=(dob,day)=>Number(day.slice(0,4))-Number(dob.slice(0,4))-(day.slice(5)<dob.slice(5)?1:0);
const localDay=value=>new Date(value).toLocaleDateString('en-CA',{timeZone:'Europe/Copenhagen'});
const numeric=v=>typeof v==='number' && Number.isFinite(v);
const clean=v=>String(v??'').replace(/<[^>]*>/g,'').replace(/[@\r\n\[\]<>|]/g,' ').slice(0,180).trim();
for (let i=0;i<responses.length;i++) {
  const r=responses[i].json, request=requests[i], query=r.dataLayer?.page?.query;
  if (!Array.isArray(r.offers) || !numeric(r.n_search_results) || !query || query.age?.[0]!==request.ageString || query.orig?.[0]!=='CPH'
      || r.dataLayer?.organization?.country!=='DK') throw new Error('Travel source schema, market or family composition changed');
  for (const o of r.offers) {
    const h=o.hotel, features=o.metabool, total=o.total_price, nights=o.number_of_nights;
    if (!o.weather?.verified || !['forecast','historical'].includes(o.weather.basis) || o.weather.minDailyHigh<p.minDaytimeC
        || !o.supplierVerified || typeof o.providerUrl!=='string' || !o.providerUrl.startsWith('https://www.solfaktor.dk/')
        || !h || !Array.isArray(features) || !numeric(total) || total<=0 || total>p.maxTotalDKK
        || !Number.isInteger(nights) || nights<p.minNights || nights>p.maxNights
        || total/4/nights>p.maxPerPersonNightDKK || o.origin?.airport_iata!=='CPH'
        || !numeric(h.rating) || h.rating<p.minStars || h.rating>5 || !numeric(h.guest_rating) || h.guest_rating*2<p.minGuestRating || h.guest_rating>5
        || typeof h.name!=='string' || !h.name.trim() || typeof o.supplier?.name!=='string' || !o.supplier.name.trim()
        || !['pool','childspool','direct_flight'].every(x=>features.includes(x)) || features.includes('adultsonly')
        || o.out_legs!==1 || o.return_legs!==1 || !numeric(o.distance_airport) || o.distance_airport<0 || o.distance_airport>p.maxAirportKm
        || !numeric(o.distance_beach) || o.distance_beach<0 || o.distance_beach>p.maxBeachMetres) continue;
    const depart=Date.parse(o.origin.dt), back=Date.parse(o.return?.dt);
    if (!Number.isFinite(depart) || !Number.isFinite(back) || back<=depart || typeof h.id!=='string') continue;
    const departureDay=localDay(o.origin.dt), returnDay=localDay(o.return.dt);
    if (departureDay<request.firstDeparture || departureDay>request.lastDeparture
        || !p.childrenBirthDates.every((d,n)=>ageAt(d,returnDay)===request.childAges[n])) continue;
    if (typeof h.url!=='string' || !h.url.startsWith('/hotel/') || /[\s<>\[\]()\\]/.test(h.url)) continue;
    const ageMatch=h.url.match(/[?&]age=([^&]+)/);
    if (!ageMatch || decodeURIComponent(ageMatch[1])!==request.ageString) continue;
    const url=o.providerUrl;
    const key=['warm-supplier-checked',h.id,o.supplier?.code,departureDay,returnDay,clean(h.room_name),request.ageString].join('|');
    const previous=sent[key];
    const aiPrice=o.allInclusive?.totalDKK;
    const aiDropped=numeric(aiPrice) && numeric(previous?.allInclusiveTotalDKK) && aiPrice<=previous.allInclusiveTotalDKK*(1-p.minPriceDropPercent/100);
    if (previous && numeric(previous.price) && total>previous.price*(1-p.minPriceDropPercent/100) && !aiDropped) continue;
    found.push({key,hotelId:h.id,hotel:clean(h.name),destination:clean(o.destination?.name),country:clean(o.destination?.country_name),
      total,nights,guestRating:h.guest_rating*2,stars:h.rating,room:clean(h.room_name),supplier:clean(o.supplier?.name),
      departureDay,returnDay,url,airportKm:o.distance_airport,beachMetres:o.distance_beach,
      temperature:numeric(o.temp_air_day)?o.temp_air_day:null,ages:request.childAges,previousPrice:previous?.price,
      supplierCheckedAt:o.supplierCheckedAt,advertisedTotal:o.advertisedTotal,weather:o.weather,allInclusive:o.allInclusive,
      previousAllInclusiveTotalDKK:previous?.allInclusiveTotalDKK,
      ...(p.preferenceQuery?.trim() && o.preferenceQuery===p.preferenceQuery && numeric(o.preferenceScore)
          ? {preferenceScore:o.preferenceScore}: {})});
  }
}
found.sort((a,b)=>a.total-b.total || b.guestRating-a.guestRating);
// Keep the cheapest eligible dates/room per hotel. Semantic preferences choose
// between hotels only after every existing price, age, weather and safety gate.
const cheapestHotels=new Set();
const eligible=found.filter(o=>{if(cheapestHotels.has(o.hotelId))return false;cheapestHotels.add(o.hotelId);return true;});
if(p.preferenceQuery?.trim() && eligible.every(o=>numeric(o.preferenceScore)))
  eligible.sort((a,b)=>b.preferenceScore-a.preferenceScore || a.total-b.total || b.guestRating-a.guestRating);
const hotels=new Set(), keys=new Set(), deals=[];
for (const offer of eligible) {
  if (hotels.has(offer.hotelId) || keys.has(offer.key)) continue;
  hotels.add(offer.hotelId);keys.add(offer.key);deals.push(offer);
  if (deals.length>=p.maxDeals) break;
}
if (!deals.length) return [];
const money=n=>Math.round(n).toLocaleString('da-DK');
const markdown=v=>clean(v).replace(/([\\`*_~])/g,'\\$1');
const blocks=deals.map((o,index)=>{
  const w=o.weather;
  const weather=(w.basis==='forecast'?'Vejrudsigt':'Sæsonestimat fra 5 års historiske data (ikke en vejrudsigt)')+
    `: dagmaks. ${w.minDailyHigh.toFixed(1)}–${w.maxDailyHigh.toFixed(1)} °C · mindst ${w.minSunshineHours.toFixed(1)} timers sol/dag · lidt regn`;
  const ai=o.allInclusive;
  const aiText=ai?.status==='included'?`All inclusive: inkluderet — ${money(o.total)} kr. for familien`:
    ai?.status==='available'?`All inclusive, fly + hotel: ${money(ai.totalDKK)} kr. for familien`+(ai.roomChanged?` · andet værelse: ${markdown(ai.roomName)}`:''):
    ai?.status==='unavailable'?'All inclusive: tilbydes ikke på dette hotel for de valgte datoer':'All inclusive: pris kunne ikke kontrolleres';
  const change=o.previousPrice && o.total<o.previousPrice?` · faldet fra ${money(o.previousPrice)} kr.`:'';
  const link=o.url.replace(/\(/g,'%28').replace(/\)/g,'%29');
  return `### ${index+1}. [${markdown(o.hotel)}](${link})\n\n`+
    `**${money(o.total)} kr. for alle fire**${change}\n\n`+
    `- **Rejse:** ${o.departureDay} → ${o.returnDay} · ${o.nights} nætter · direkte fly fra København\n`+
    `- **Familien:** 2 voksne + børn på ${o.ages.join(' og ')} år ved hjemrejse · ${money(o.total/4/o.nights)} kr. pr. person pr. nat\n`+
    `- **Hotel:** ${markdown(o.destination)}, ${markdown(o.country)} · ${o.stars}★ · gæster ${o.guestRating.toFixed(1)}/10\n`+
    `- **Værelse:** ${markdown(o.room) || 'Værelse og måltider ikke oplyst'}\n`+
    `- ${aiText}\n`+
    `- **Ved hotellet:** pool og børnepool · strand ${o.beachMetres} m · lufthavn ${o.airportKm} km\n`+
    `- **Vejr:** ${weather}\n\n`+
    `*Fly + hotel: pris genberegnet hos ${markdown(o.supplier)} ${o.supplierCheckedAt.slice(0,16).replace('T',' ')}.*`;
});
const minimum=Math.min(...deals.map(offer=>offer.total));
const preview=`${deals.length} ${deals.length===1?'familieferie':'familieferier'} fra København: fly + hotel fra ${money(minimum)} kr. for alle fire.`;
const message=`${preview}\n\n## Billige familieferier fra København\n\n${blocks.join('\n\n')}\n\n`+
  `**Før bestilling**\n\n`+
  `- Fly + hotel er under ${money(p.maxTotalDKK)} kr. for familien og højst ${money(p.maxPerPersonNightDKK)} kr. pr. person pr. nat.\n`+
  `- All-inclusive-prisen er et valgfrit alternativ og kan overstige prisgrænsen. Måltider er kun inkluderet som angivet.\n`+
  `- Bagage, transfer, lokale afgifter og øvrige tillæg er ikke bekræftet. Prisen kan ændre sig inden bestilling.\n`+
  `- Kontrollér sæsonåbning af pools hos arrangøren.\n\n`+
  `**Kilder og vejr**\n\n`+
  `- Fundet via afbudsrejser.dk og priskontrolleret hos Solfaktor. Andre udbydere udelades, indtil deres totalpris kan kontrolleres.\n`+
  `- Vejrfilter: mindst ${p.minDaytimeC} °C i dagmaks., ${p.minSunshineHours} timers sol/dag og lav regnrisiko. Vejret kan ændre sig.\n`+
  `- Vejrdata: Open-Meteo. Fleksible datoer de næste ${p.windowDays} dage.`;
return [{json:{message,deals,checkedAt:requests[0].checkedAt,
  truncated:responses.some(x=>x.json.n_search_results>x.json.offers.length)}}];
