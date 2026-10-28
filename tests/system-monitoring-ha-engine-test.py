#!/usr/bin/env python3
"""Opt-in isolated n8n engine qualification of HA GETs and the finite projection."""
import argparse
import base64
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    flow = json.loads((ROOT / 'automations/agent-investigation/n8n-ha-metadata-workflow.json').read_text())
    flow['id'] = 'hc-synthetic-ha-metadata'
    flow['name'] = 'Isolated synthetic HA metadata qualification'
    for node in flow['nodes']:
        if node['type'] == 'n8n-nodes-base.webhook':
            node['type'] = 'n8n-nodes-base.manualTrigger'; node['typeVersion'] = 1
            node['parameters'] = {}; node.pop('credentials', None); node.pop('webhookId', None)
        elif node['name'] == 'Approved input-free metadata request':
            node['parameters']['jsCode'] = node['parameters']['jsCode'].replace('configured=false', 'configured=true')
        elif node['type'] == 'n8n-nodes-base.homeAssistant':
            node['credentials'] = {'homeAssistantApi': {'id': 'synthetic-ha', 'name': 'synthetic-ha'}}
        elif node['type'] == 'n8n-nodes-base.respondToWebhook':
            node['type'] = 'n8n-nodes-base.httpRequest'; node['typeVersion'] = 4.2
            node['parameters'] = {'method':'POST','url':'http://127.0.0.1:18888/receipt','sendBody':True,
                                  'specifyBody':'json','jsonBody':'={{JSON.stringify($json)}}','options':{'timeout':5000}}
    credentials = [{'id':'synthetic-ha','name':'synthetic-ha','type':'homeAssistantApi',
                    'data':{'host':'127.0.0.1','port':18888,'ssl':False,'accessToken':'synthetic-no-production-credential'}}]
    projection = next(n['parameters']['jsCode'] for n in flow['nodes'] if n['name'] == 'Project finite HA metadata')
    policy = json.loads(re.search(r'const policy=(.*);\n', projection).group(1))
    mock = """const http=require('http'),fs=require('fs');const reads=[];const policy=POLICY;
function states(){
 const now=Date.now(),at=seconds=>new Date(now+seconds*1000).toISOString();
 const rows=Object.entries(policy.numeric).map(([alias,[entity_id,unit,source,attribute]])=>({entity_id,
  state:attribute?'charging':String(unit==='%'?52:1.5),last_updated:at(-86400),last_reported:at(-10),
  attributes:{unit_of_measurement:unit,friendly_name:'PRIVATE',token:'SECRET',...(attribute?{[attribute]:300}:{})}}));
 rows.push({entity_id:'sensor.staircase_battery_optimizer_state',state:'ready',last_updated:at(-86400),attributes:{healthy:true,last_refresh:at(-10),private_text:'PRIVATE'}});
 rows.push({entity_id:'binary_sensor.staircase_battery_grid_available',state:'on',last_updated:at(-86400)});
 rows.push({entity_id:'binary_sensor.stromligning_tomorrow_available_vat',state:'on'});
 for(const [alias,entity_id] of Object.entries(policy.updates))rows.push({entity_id,state:alias==='core'?'on':'off',
  attributes:{installed_version:alias==='os'?'16.3':'2026.10.1',latest_version:alias==='os'?'16.3':'2026.10.2',release_summary:'PRIVATE'}});
 rows.push({entity_id:'sensor.staircase_battery_operation_plan',state:'ready',last_updated:at(-86400),attributes:{created_at:at(-10),expected_cost_dkk:5,baseline_cost_dkk:7,expected_savings_dkk:2,
  blocks:[{start:at(0),end:at(3600),action:'charge',energy_kwh:0.5,expected_load_kwh:0.2,expected_grid_import_kwh:0.7,expected_cost_dkk:1.2,expected_savings_dkk:0.3,private_reason:'PRIVATE'}],private_note:'SECRET'}});
 rows.find(e=>e.entity_id===policy.numeric.electricity_price[0]).attributes.prices=[{start:at(0),end:at(3600),price:1.5},{start:at(3600),end:at(7200),price:-0.25}];
 rows.push({entity_id:'person.private',state:'home',attributes:{latitude:55.1,token:'SECRET'}});
 rows.push({entity_id:'sensor.unrelated',state:'unavailable'});
 rows.push({entity_id:'automation.private',state:'off'});
 return rows;
}
http.createServer((req,res)=>{let raw='';req.on('data',x=>raw+=x);req.on('end',()=>{
res.setHeader('content-type','application/json');
if(req.url==='/receipt'&&req.method==='POST'){
 const data=JSON.parse(raw);const fields=['schema_version','host','generated_at','installed_version','update_available_count','unavailable_count','checks','energy'];
 const check=id=>data.checks?.find(c=>c.check_id==='ha.'+id);
 const passed=Object.keys(data).length===8&&fields.every(k=>Object.hasOwn(data,k))&&data.schema_version===2&&data.host==='home-assistant'&&data.installed_version==='2026.10.1'&&data.update_available_count===1&&data.unavailable_count===0&&data.checks?.length===12&&check('battery_optimizer')?.status==='healthy'&&check('operation_plan')?.status==='healthy'&&check('update.core')?.evidence.latest_version==='2026.10.2'&&data.energy?.entities.battery_soc.value===52&&data.energy?.entities.battery_discharge_power.value===300&&data.energy?.entities.grid_import_energy.unit==='Wh'&&data.energy?.price_slots.length===2&&data.energy?.plan.blocks.length===1&&!/PRIVATE|SECRET|latitude|friendly_name/.test(JSON.stringify(data))&&reads.join(',')==='/api/config,/api/states';
 fs.writeFileSync('/tmp/receipt.json',JSON.stringify({passed,reads,metadata:data,production_credentials:false,external_requests:0}));res.end('{}');return;}
if(req.method!=='GET'||req.headers.authorization!=='Bearer synthetic-no-production-credential'){res.writeHead(403);res.end('{}');return;}
reads.push(req.url);
if(req.url==='/api/config')res.end(JSON.stringify({version:'2026.10.1',location:'PRIVATE'}));
else if(req.url==='/api/states')res.end(JSON.stringify(states()));
else{res.writeHead(404);res.end('{}');}});}).listen(18888,'127.0.0.1');""".replace('POLICY', json.dumps(policy))
    return {'workflow.json':json.dumps(flow),'credentials.json':json.dumps(credentials),'mock.cjs':mock}


def live():
    payload = base64.b64encode(json.dumps(fixture()).encode()).decode()
    source = '''import base64,json,pathlib,subprocess,tempfile
files=json.loads(base64.b64decode(PAYLOAD))
image=subprocess.check_output(['sudo','-n','docker','inspect','--format','{{.Image}}','homecompute-automation-n8n-1'],text=True).strip()
if not image.startswith('sha256:'):raise ValueError('immutable image required')
with tempfile.TemporaryDirectory(prefix='hc-n8n-ha-metadata-') as raw:
 directory=pathlib.Path(raw);directory.chmod(0o755)
 for name,body in files.items():
  path=directory/name;path.write_text(body);path.chmod(0o444)
 command=['sudo','-n','docker','run','--pull=never','--rm','--network','none','--cpus','0.5','--memory','768m',
 '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true',
 '--tmpfs','/home/node/.n8n:uid=1000,gid=1000,mode=700','--tmpfs','/tmp:uid=1000,gid=1000,mode=700',
 '-e','N8N_DIAGNOSTICS_ENABLED=false','-e','N8N_VERSION_NOTIFICATIONS_ENABLED=false',
 '-v',str(directory)+':/fixture:ro','--entrypoint','/bin/sh',image,'-c',
 "node /fixture/mock.cjs & n8n import:credentials --input=/fixture/credentials.json >/tmp/credentials.log 2>&1 && n8n import:workflow --input=/fixture/workflow.json >/tmp/workflow.log 2>&1 && n8n execute --id=hc-synthetic-ha-metadata >/tmp/execute.log 2>&1; result=$?; if [ $result -eq 0 ] && [ -f /tmp/receipt.json ]; then cat /tmp/receipt.json; else tail -c 4000 /tmp/execute.log /tmp/workflow.log /tmp/credentials.log; exit 1; fi"]
 result=subprocess.run(command,text=True,capture_output=True,timeout=110)
 if result.returncode:print(json.dumps({'passed':False,'diagnostics':result.stdout[-5000:],'stderr':result.stderr[-1000:]}))
 else:print(json.dumps({'image':image,'network':'none',**json.loads(result.stdout)}))
'''.replace('PAYLOAD', repr(payload))
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','home-core','python3 -'],
                            input=source,text=True,capture_output=True,timeout=120)
    if result.returncode:
        raise SystemExit('Isolated HA engine check unavailable')
    receipt = json.loads(result.stdout)
    print(json.dumps(receipt, indent=2))
    return receipt['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True, help='disposable network-none n8n on home-core; synthetic HA only')
    parser.parse_args()
    raise SystemExit(0 if live() else 1)
