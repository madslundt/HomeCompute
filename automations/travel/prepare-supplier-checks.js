const requests=$('Build exact age searches').all().map(x=>x.json), responses=$input.all();
if (requests.length!==responses.length) throw new Error('Travel source response count differs');
const candidates=[];
for (let i=0;i<responses.length;i++) {
  const r=responses[i].json, request=requests[i], p=request.preferences;
  if (!Array.isArray(r.offers) || r.dataLayer?.organization?.country!=='DK'
      || r.dataLayer?.page?.query?.age?.[0]!==request.ageString || r.dataLayer?.page?.query?.orig?.[0]!=='CPH') throw new Error('Travel source composition changed');
  for (const o of r.offers) {
    const h=o.hotel, f=o.metabool;
    if (o.supplier?.code!=='SOLFAKTOR_DK' || typeof o.total_price!=='number' || o.total_price<=0 || o.total_price>p.maxTotalDKK
        || !h || typeof h.rating!=='number' || h.rating<p.minStars || typeof h.guest_rating!=='number' || h.guest_rating*2<p.minGuestRating
        || !Array.isArray(f) || !['direct_flight','pool','childspool'].every(v=>f.includes(v)) || f.includes('adultsonly')
        || typeof o.distance_airport!=='number' || o.distance_airport<0 || o.distance_airport>p.maxAirportKm
        || typeof o.distance_beach!=='number' || o.distance_beach<0 || o.distance_beach>p.maxBeachMetres
        || typeof o.redirect_url!=='string' || !o.redirect_url.startsWith('/charterredirect?') || /[\s<>]/.test(o.redirect_url)) continue;
    const returnDay=String(o.return?.dt||'').slice(0,10);
    if (!request.preferences.childrenBirthDates.every((d,n)=>Number(returnDay.slice(0,4))-Number(d.slice(0,4))-(returnDay.slice(5)<d.slice(5)?1:0)===request.childAges[n])) continue;
    candidates.push({offer:o,request,dataLayer:r.dataLayer,redirectUrl:'https://www.afbudsrejser.dk'+o.redirect_url});
  }
}
candidates.sort((a,b)=>a.offer.total_price-b.offer.total_price);
const hotels=new Set();
return candidates.filter(c=>{if(hotels.has(c.offer.hotel.id))return false;hotels.add(c.offer.hotel.id);return true;})
  .slice(0,6).flatMap(c=>{
    const d=c.offer.destination, p=c.request.preferences, start=c.offer.origin.dt.slice(0,10), end=c.offer.return.dt.slice(0,10);
    if (typeof d?.latitude!=='number' || !Number.isFinite(d.latitude) || Math.abs(d.latitude)>90
        || typeof d.longitude!=='number' || !Number.isFinite(d.longitude) || Math.abs(d.longitude)>180) return [];
    const today=new Date(c.request.checkedAt.slice(0,10)+'T12:00:00Z'), forecastEnd=new Date(+today+15*86400000).toISOString().slice(0,10);
    const forecast=end<=forecastEnd;
    if (!forecast && p.weatherMode!=='seasonal_allowed') return [];
    const days=[];
    for(let dt=Date.parse(start+'T12:00:00Z');dt<Date.parse(end+'T12:00:00Z');dt+=86400000)days.push(new Date(dt).toISOString().slice(0,10));
    if (days.length<1 || days.length>21) return [];
    const crossYear=end.slice(0,4)!==start.slice(0,4), lastYear=today.getUTCFullYear()-1-(crossYear?1:0);
    const params={latitude:d.latitude,longitude:d.longitude,timezone:'auto',
      daily:'temperature_2m_max,precipitation_sum,sunshine_duration,wind_speed_10m_max'+(forecast?',precipitation_probability_max':'')};
    if(forecast)params.forecast_days=16;
    else {params.start_date=(lastYear-4)+'-'+start.slice(5);params.end_date=(lastYear+(crossYear?1:0))+'-'+days[days.length-1].slice(5);params.models='era5';}
    const weatherUrl=(forecast?'https://api.open-meteo.com/v1/forecast':'https://archive-api.open-meteo.com/v1/archive')+'?'+Object.entries(params).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
    return [{json:{...c,weatherMode:forecast?'forecast':'historical',weatherDays:days,weatherUrl}}];
  });
