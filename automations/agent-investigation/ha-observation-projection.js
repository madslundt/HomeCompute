// Embedded into the fixed input-free n8n route by the trusted setup helper.
const policy=POLICY;
const version=$('Read HA configuration').first().json.version;
const rows=$input.all();
if(typeof version!=='string'||!/^v?[0-9]+(?:[.][0-9A-Za-z_-]+){0,5}$/.test(version)||!rows.length||rows.length>100000)throw Error('Invalid HA inventory');
const byId=new Map();
for(const {json:e} of rows){
 if(!e||typeof e.entity_id!=='string'||!/^[a-z_]+[.][a-z0-9_]+$/.test(e.entity_id)||byId.has(e.entity_id)||typeof e.state!=='string'||e.state.length>1024)throw Error('Invalid HA inventory');
 byId.set(e.entity_id,e);
}
const now=Date.now(), generated=new Date(now).toISOString();
const date=x=>typeof x==='string'&&/T.*(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(x)&&Number.isFinite(Date.parse(x))?new Date(x).toISOString():null;
const num=x=>(typeof x==='number'||(typeof x==='string'&&x.trim()!==''))&&Number.isFinite(Number(x))&&Math.abs(Number(x))<=1e12?Number(x):null;
const fresh=(x,ttl)=>date(x)&&now-Date.parse(x)>=0&&now-Date.parse(x)<=ttl*1000;
const unknown=reason=>({status:'unknown',reason});
const optimizer=byId.get('sensor.staircase_battery_optimizer_state');
const refresh=optimizer?.attributes?.last_refresh;
const meterRefresh=byId.get('sensor.watts_live_effekt')?.last_reported||byId.get('sensor.watts_live_effekt')?.last_updated;
const entities={};
for(const [alias,[id,unit,source,attribute]] of Object.entries(policy.numeric)){
 const e=byId.get(id), a=e?.attributes||{};
 const reported=date(e?.last_updated);
 let sampled=source==='device'||source==='device_integrated'||source==='estimate'?date(refresh):
  source==='whole_house_meter'||source==='house_meter_import'?date(meterRefresh):
  source==='configured'?generated:source==='vat_inclusive_tariff'&&alias!=='electricity_price'?generated:date(e?.last_reported||e?.last_updated);
 const value=num(attribute?a[attribute]:e?.state);
 const actualUnit=attribute?unit:a.unit_of_measurement;
 const unitOK=actualUnit===unit||(unit==='DKK/kWh'&&actualUnit==='kr/kWh');
 let reason=!e?'missing':['unknown','unavailable'].includes(e?.state)?'health_not_reported':!unitOK||value===null?'unsupported_schema':
  !fresh(sampled,source==='vat_inclusive_tariff'?7200:900)?'stale':null;
 if(!reason&&unit==='%'&&(value<0||value>100))reason='unsupported_schema';
 entities[alias]={...(reason?unknown(reason):{status:'available',value}),unit,source,reported_at:reported,sampled_at:sampled};
}
const checks=[];
const add=(key,status,evidence)=>checks.push({check_id:'ha.'+key,status,evidence,observed_at:generated});
const stateCheck=(id,valid,good)=>{const e=byId.get(id);return !e?unknown('missing'):!valid.includes(e.state)?unknown('health_not_reported'):{status:good(e)?'healthy':'degraded',available:good(e)};};
const group=(aliases)=>{const missing=aliases.map(k=>entities[k]).find(e=>e.status!=='available');return missing?{status:'degraded',reason:missing.reason}:{status:'healthy',available:true};};
const addResult=(key,r)=>{const {status,...evidence}=r;add(key,status==='unknown'?'degraded':status,evidence);};
addResult('battery_optimizer',!optimizer?unknown('missing'):!fresh(refresh,900)?unknown('stale'):
 typeof optimizer.attributes?.healthy!=='boolean'?unknown('unsupported_schema'):
 {status:optimizer.attributes.healthy?'healthy':'degraded',failed:!optimizer.attributes.healthy});
addResult('battery_telemetry',group(['battery_soc','battery_charge_power','battery_discharge_power','connected_load_power']));
addResult('grid_meter',group(['grid_import_power','grid_export_power']));
addResult('tariff',group(['electricity_price']));
addResult('grid_availability',stateCheck('binary_sensor.staircase_battery_grid_available',['on','off'],e=>e.state==='on'));
let updates=0,unavailable=0;
for(const [alias,id] of Object.entries(policy.updates)){
 const e=byId.get(id), a=e?.attributes||{};
 let evidence={},status;
 if(!e||!['on','off'].includes(e.state)){status='degraded';evidence={reason:e?'health_not_reported':'missing'};}
 else {status=e.state==='on'?'degraded':'healthy';evidence.available=e.state==='on';if(e.state==='on')updates++;
  for(const k of ['installed_version','latest_version'])if(typeof a[k]==='string'&&/^v?[0-9]+(?:[.][0-9A-Za-z_-]+){0,5}$/.test(a[k]))evidence[k]=a[k];
 }
 add('update.'+alias,status,evidence);
}
const price=byId.get('sensor.stromligning_current_price_vat');
let price_slots=[];
if(entities.electricity_price.status==='available'&&Array.isArray(price?.attributes?.prices)&&price.attributes.prices.length<=200){
 let prior=null;const projected=[];
 for(const s of price.attributes.prices){const start=date(s.start),end=date(s.end),value=num(s.price);
  if(!start||!end||start>=end||value===null||(prior&&start<prior)||Date.parse(start)<now-86400000||Date.parse(start)>now+172800000){projected.length=0;break;}
  projected.push({start,end,price_dkk_per_kwh:value});prior=end;
 }price_slots=projected;
}
const p=byId.get('sensor.staircase_battery_operation_plan'), a=p?.attributes||{};
let plan=unknown(!p?'missing':!fresh(a.created_at,900)?'stale':'unsupported_schema');
if(fresh(a.created_at,900)&&Array.isArray(a.blocks)&&a.blocks.length>0&&a.blocks.length<=32){
 const totals=['expected_cost_dkk','baseline_cost_dkk','expected_savings_dkk'];let valid=totals.every(k=>num(a[k])!==null),prior=null;const blocks=[];
 for(const b of a.blocks){const start=date(b.start),end=date(b.end),out={start,end,action:b.action};
  if(!start||!end||start>=end||!['grid','charge','battery','idle'].includes(b.action)||(prior&&start<prior)||Date.parse(start)<now-86400000||Date.parse(start)>now+172800000)valid=false;
  for(const k of ['energy_kwh','expected_load_kwh','expected_grid_import_kwh','expected_cost_dkk','expected_savings_dkk']){out[k]=num(b[k]);if(out[k]===null)valid=false;}
  blocks.push(out);prior=end;
 }
 if(valid){plan={status:'available',created_at:date(a.created_at),blocks};for(const k of totals)plan[k]=num(a[k]);}
}
addResult('operation_plan',plan.status==='available'?{status:'healthy',available:true}:{status:'degraded',reason:plan.reason});
unavailable=checks.filter(c=>c.status!=='healthy'&&c.evidence.reason).length;
const tomorrow=byId.get('binary_sensor.stromligning_tomorrow_available_vat')?.state;
const result={schema_version:2,host:'home-assistant',generated_at:generated,installed_version:version,update_available_count:updates,unavailable_count:unavailable,checks,
 energy:{entities,price_slots,plan,solar:{status:'unknown',reason:'not_provisioned'},tomorrow_prices:['on','off'].includes(tomorrow)?tomorrow:'unknown'}};
if(JSON.stringify(result).length>65536)throw Error('Projection budget');
return [{json:result}];
