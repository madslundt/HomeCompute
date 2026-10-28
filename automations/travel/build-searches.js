const p = $input.first().json.preferences;
if (!p || !Array.isArray(p.childrenBirthDates) || p.childrenBirthDates.length !== 2) throw new Error('Configure two child birth dates in Travel preferences.');
if (p.preferenceQuery!==undefined && (typeof p.preferenceQuery!=='string' || p.preferenceQuery.length>500 || /[\r\n\x00-\x1f]/.test(p.preferenceQuery))) throw new Error('Invalid descriptive travel preference');
const date = value => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error('Invalid date');
  const d = new Date(value + 'T12:00:00Z');
  if (!Number.isFinite(+d) || d.toISOString().slice(0,10) !== value) throw new Error('Invalid date');
  return d;
};
const today = date($now.setZone('Europe/Copenhagen').toISODate());
const iso = d => d.toISOString().slice(0,10);
const add = (d,n) => new Date(+d + n*86400000);
const age = (dob,at) => at.getUTCFullYear()-dob.getUTCFullYear() - (iso(at).slice(5)<iso(dob).slice(5) ? 1 : 0);
const births = p.childrenBirthDates.map(date);
for (const k of ['windowDays','minNights','maxNights','maxTotalDKK','maxPerPersonNightDKK','minStars','minGuestRating','maxAirportKm','maxBeachMetres','maxDeals','minPriceDropPercent','minDaytimeC','minSunshineHours','maxRainProbabilityPercent','maxRainMmPerDay','maxWindKmh','maxHistoricalWetFraction']) {
  if (typeof p[k] !== 'number' || !Number.isFinite(p[k]) || p[k] <= 0) throw new Error('Invalid travel preference: '+k);
}
if (p.windowDays>90 || p.minNights<7 || p.maxNights>21 || p.minNights>p.maxNights || p.maxDeals>5 || p.minStars>5 || p.minGuestRating>10 || p.minPriceDropPercent>100) throw new Error('Travel preferences outside supported bounds');
if (!['seasonal_allowed','forecast_only'].includes(p.weatherMode) || p.minDaytimeC<25 || p.minDaytimeC>45 || p.minSunshineHours>16
    || p.maxRainProbabilityPercent>100 || p.maxHistoricalWetFraction>1) throw new Error('Invalid weather preferences');
const first = add(today,1), last = add(today,p.windowDays), pairs = new Map();
for (let offset=p.minNights;offset<=p.windowDays+p.maxNights;offset++) {
  const ages=births.map(b=>age(b,add(today,offset)));
  if (ages.some(a=>a<2 || a>17)) throw new Error('Child ages outside supported range; review family settings');
  pairs.set(ages.join('-'),ages);
}
return [...pairs.values()].flatMap(ages=>{
  const params = {
    age:[42,42,...ages].join('-'),orig:'CPH',edepdate:iso(first),ldepdate:iso(last),
    duration:p.minNights+'-'+p.maxNights,sort:'price',facility:'pool-childspool',
    rating:p.minStars,guest_rating:Math.ceil(p.minGuestRating*5),flight_type:'direct_flight',
    beach:p.maxBeachMetres,price:p.maxTotalDKK,rows:100,page:1,
  };
  const nearLast=add(today,Math.min(p.windowDays,15-p.minNights));
  const variants=p.minNights<15?[{...params,ldepdate:iso(nearLast),lretdate:iso(add(today,15))}]:[];
  if (p.weatherMode==='seasonal_allowed') variants.push({...params,temp_air_day:p.minDaytimeC+',42'});
  return variants.map(query=>({json:{url:'https://www.afbudsrejser.dk/charter.json?'+Object.entries(query).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&'),
    ageString:params.age,childAges:ages,firstDeparture:iso(first),lastDeparture:query.ldepdate,
    preferences:p,checkedAt:$now.toISO()}}));
});
