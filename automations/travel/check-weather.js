const contexts=$('Prepare supplier checks').all();
return $input.all().flatMap((item,i)=>{
  const c=contexts[i]?.json, r=item.json, d=r.daily, u=r.daily_units, p=c?.request.preferences;
  if (!c || r.error || !d || !Array.isArray(d.time) || d.time.some(t=>typeof t!=='string' || !/^\d{4}-\d{2}-\d{2}$/.test(t))
      || u?.temperature_2m_max!=='°C' || u.precipitation_sum!=='mm'
      || u.sunshine_duration!=='s' || u.wind_speed_10m_max!=='km/h' || typeof r.latitude!=='number' || typeof r.longitude!=='number'
      || Math.abs(r.latitude-c.offer.destination.latitude)>0.6 || Math.abs(r.longitude-c.offer.destination.longitude)>0.6) return [];
  const summary=[];
  for (const day of c.weatherDays) {
    const indices=[];
    for(let j=0;j<d.time.length;j++)if(c.weatherMode==='forecast'?d.time[j]===day:d.time[j].slice(5)===day.slice(5))indices.push(j);
    if(indices.length!==(c.weatherMode==='forecast'?1:5))return [];
    if(c.weatherMode==='historical' && new Set(indices.map(j=>d.time[j].slice(0,4))).size!==5)return [];
    const values=indices.map(j=>({high:d.temperature_2m_max?.[j],sun:d.sunshine_duration?.[j],rain:d.precipitation_sum?.[j],wind:d.wind_speed_10m_max?.[j],prob:d.precipitation_probability_max?.[j]}));
    if(values.some(v=>['high','sun','rain','wind'].some(k=>typeof v[k]!=='number' || !Number.isFinite(v[k])) || v.sun<0 || v.rain<0 || v.wind<0))return [];
    const avg=k=>values.reduce((n,v)=>n+v[k],0)/values.length;
    const high=avg('high'), sunshine=avg('sun')/3600, rain=avg('rain'), wind=avg('wind');
    if(high<p.minDaytimeC || sunshine<p.minSunshineHours || rain>p.maxRainMmPerDay || wind>p.maxWindKmh)return [];
    if(c.weatherMode==='forecast') {
      if(u.precipitation_probability_max!=='%' || typeof values[0].prob!=='number' || !Number.isFinite(values[0].prob)
          || values[0].prob<0 || values[0].prob>p.maxRainProbabilityPercent)return [];
    } else if(values.filter(v=>v.rain>p.maxRainMmPerDay).length/values.length>p.maxHistoricalWetFraction)return [];
    summary.push({high,sunshine,rain,wind});
  }
  if(!summary.length)return [];
  const weather={verified:true,basis:c.weatherMode,checkedAt:$now.toISO(),minDailyHigh:Math.min(...summary.map(v=>v.high)),
    maxDailyHigh:Math.max(...summary.map(v=>v.high)),minSunshineHours:Math.min(...summary.map(v=>v.sunshine)),
    maxDailyRainMm:Math.max(...summary.map(v=>v.rain)),sampleYears:c.weatherMode==='historical'?5:null};
  return [{json:{...c,offer:{...c.offer,weather}}}];
});
