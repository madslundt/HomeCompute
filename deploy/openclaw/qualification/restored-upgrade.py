#!/usr/bin/env python3
"""Native lifecycle exercise inside an authorized networkless COW restore only."""
import argparse
import copy
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socketserver
import sqlite3
import struct
import subprocess
import tarfile
import threading
import time
import uuid

STATE=Path('/var/lib/docker/volumes/nemoclaw-openclaw-state-v1-agent-openclaw/_data')
HOME=Path('/home/hermes-operator')
WORK=Path('/var/lib/homecompute-upgrade-fixture')
SECTIONS=('agents','tools','channels','cron','hooks','browser')
ROOT=None
COUNTER=0
SQLITE_COUNT=0
LAST_HEALTH=None
HEALTH_REDACTOR=None
SNAPSHOT_METRICS={}
BRIDGE_PROBE_IMAGE='busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662'


def fixture_dns_response(packet):
 # The native DNS preflight expects NXDOMAIN for its reserved .invalid name.
 # No other name is resolved, forwarded, logged or accepted as NXDOMAIN.
 if len(packet)<12 or len(packet)>512:return None
 identifier,flags,questions,answers,authority,additional=struct.unpack('!6H',packet[:12])
 if flags&0x8000 or questions!=1:return None
 offset=12;labels=[]
 while offset<len(packet):
  size=packet[offset];offset+=1
  if size==0:break
  if size>63 or offset+size>len(packet):return None
  try:labels.append(packet[offset:offset+size].decode('ascii'))
  except UnicodeDecodeError:return None
  offset+=size
 else:return None
 if offset+4>len(packet):return None
 name='.'.join(labels).lower();rcode=3 if re.fullmatch(r'nemoclaw-dns-probe-[a-f0-9]+\.invalid',name) else 2
 return struct.pack('!6H',identifier,0x8080|(flags&0x0100)|rcode,1,0,0,0)+packet[12:offset+4]


class FixtureDNS(socketserver.BaseRequestHandler):
 def handle(self):
  packet,sock=self.request;response=fixture_dns_response(packet)
  if response is not None:sock.sendto(response,self.client_address)


def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def run(argv,label,*,env=None,user=False,timeout=180,check=True):
 global COUNTER
 COUNTER+=1;out=WORK/(str(COUNTER)+'-'+label+'.log')
 kwargs={}
 if user:
  entry=pwd.getpwnam('hermes-operator')
  def credentials():os.initgroups(entry.pw_name,entry.pw_gid);os.setgid(entry.pw_gid);os.setuid(entry.pw_uid)
  kwargs['preexec_fn']=credentials
 with out.open('xb') as log:
  process=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,**kwargs)
  try:process.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
   os.killpg(process.pid,signal.SIGTERM)
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
   raise ValueError('timeout:'+label) from None
 if check and process.returncode:raise ValueError('failed:'+label)
 return out.read_bytes()


def environment(version,assets):
 versions=module('prepare-openclaw-upgrade-assets').VERSIONS
 env={'HOME':str(HOME),'USER':'hermes-operator','LOGNAME':'hermes-operator',
  'PATH':str(HOME/'.nvm/versions/node/v22.23.3/bin')+':'+str(HOME/'.local/bin')+':/usr/local/bin:/usr/bin:/bin',
  'XDG_RUNTIME_DIR':'/run/user/1000','DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus',
  'NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE':'1',
  'NEMOCLAW_TRUSTED_PRIVATE_HOSTS':'ai.home.arpa','NEMOCLAW_DISABLE_TELEMETRY':'1','NEMOCLAW_CPU':'2','NEMOCLAW_RAM':'8Gi',
  'NEMOCLAW_GATEWAY_PORT':'9123','COMPATIBLE_API_KEY':'synthetic-fixture-only',
  'GITHUB_ACTIONS':'true','NEMOCLAW_RUN_LIVE_E2E':'1',
  'NEMOCLAW_E2E_MANAGED_IMAGE_CATALOG':str(assets/('catalog-'+version+'.json')),
  'NEMOCLAW_E2E_MANAGED_IMAGE_REVISION':versions[version],'NO_COLOR':'1','CI':'1'}
 allowed=module('supervise-openclaw-nemoclaw').ENV_KEYS
 inherited={}
 for line in Path('/etc/homecompute-openclaw/agent-openclaw.env').read_text().splitlines():
  if line and not line.startswith('#') and '=' in line:
   key,value=line.split('=',1)
   if key in allowed:inherited[key]=value.strip('"')
 inherited.update(env)
 inherited['NEMOCLAW_INSTALL_REF']=versions[version];inherited['NEMOCLAW_INSTALL_TAG']='v'+version
 return inherited


def cli(version,*args):return [str(HOME/'.nvm/versions/node/v22.23.3/bin/node'),str(WORK/('nemoclaw-'+version)/'bin/nemoclaw.js'),*args]


def stop_native(version,env,label):
 query=[str(HOME/'.local/bin/openshell'),'sandbox','get','-g','nemoclaw-9123','agent-openclaw','-o','json']
 before=json.loads(run(query,label+'-identity',env=env,user=True,timeout=30))
 if before.get('name')!='agent-openclaw' or before.get('phase')!='Ready':raise ValueError('exact-ready-stop-target-required')
 expected=str(uuid.UUID(before['id']))
 run(cli(version,'agent-openclaw','stop'),label,env=env,user=True,check=False)
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  raw=run(query,label+'-observation',env=env,user=True,timeout=15,check=False)
  try:after=json.loads(raw)
  except ValueError:time.sleep(2);continue
  if after.get('id')!=expected or after.get('name')!='agent-openclaw':raise ValueError('stop-identity-changed')
  if after.get('phase')=='Stopped':
   status=subprocess.check_output(['docker','inspect','--format','{{.State.Status}}','openshell-default--agent-openclaw-'+expected],timeout=15).decode().strip()
   if status not in ('exited','dead'):raise ValueError('stopped-native-container-not-quiescent')
   return
  time.sleep(2)
 raise ValueError('native-stop-not-positively-stopped')


def container():
 rows=subprocess.check_output(['docker','ps','--filter','name=^/openshell-default--agent-openclaw-','--format','{{.Names}}'],timeout=15).decode().splitlines()
 if len(rows)!=1:raise ValueError('exact-clone-container-required')
 return rows[0]


def start_native(version,env,label):
 query=[str(HOME/'.local/bin/openshell'),'sandbox','get','-g','nemoclaw-9123','agent-openclaw','-o','json']
 before=json.loads(run(query,label+'-identity',env=env,user=True,timeout=30))
 if before.get('name')!='agent-openclaw' or before.get('phase')!='Stopped':raise ValueError('exact-stopped-start-target-required')
 expected=str(uuid.UUID(before['id']))
 run(cli(version,'agent-openclaw','start'),label,env=env,user=True,timeout=300,check=False)
 after=json.loads(run(query,label+'-observation',env=env,user=True,timeout=30))
 if after.get('id')!=expected or after.get('name')!='agent-openclaw' or after.get('phase')!='Ready':raise ValueError('exact-ready-start-required')
 status=subprocess.check_output(['docker','inspect','--format','{{.State.Status}}','openshell-default--agent-openclaw-'+expected],timeout=15).decode().strip()
 if status!='running':raise ValueError('ready-native-container-not-running')
 health(version,env)


def health(version,env):
 deadline=time.monotonic()+180;retries=0
 while True:
  try:
   result=health_once(version,env)
   LAST_HEALTH['readiness_retries']=retries
   return result
  except ValueError as error:
   if str(error) not in ('gateway-or-plugin-health-failed','failed:gateway-health','timeout:gateway-health') or time.monotonic()>=deadline:raise
   retries+=1;time.sleep(3)


def health_once(version,env):
 global LAST_HEALTH
 raw=run(cli(version,'agent-openclaw','exec','--','openclaw','gateway','health','--timeout','10000','--json'),'gateway-health',env=env,user=True,timeout=90)
 data,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])
 plugins=data.get('plugins',{})
 LAST_HEALTH={'ok':data.get('ok') is True,'loaded':sorted(p for p in plugins.get('loaded',[]) if isinstance(p,str)),
  'error_count':len(plugins.get('errors',[])),'unavailable_count':len(plugins.get('unavailable',[]))}
 if HEALTH_REDACTOR and (plugins.get('errors') or plugins.get('unavailable')):LAST_HEALTH['private_plugin_diagnostics']=HEALTH_REDACTOR(json.dumps({key:plugins.get(key,[]) for key in ('errors','unavailable')},sort_keys=True))[:4096]
 paused=json.loads((STATE/'openclaw.json').read_bytes()).get('channels',{}).get('mattermost',{}).get('enabled') is False
 required={'homecompute-safety','homecompute-agent-office','memory-core','nemoclaw'}
 if not paused:required.add('mattermost')
 accounts=data.get('channels',{}).get('mattermost',{}).get('accounts',{})
 running=sum(value.get('running') is True or value.get('connected') is True for value in accounts.values())
 LAST_HEALTH.update(mattermost_paused=paused,running_mattermost_accounts=running)
 if data.get('ok') is not True or plugins.get('errors') or plugins.get('unavailable') or not required<=set(plugins.get('loaded',[])):raise ValueError('gateway-or-plugin-health-failed')
 if paused and running:raise ValueError('paused-mattermost-account-running')
 return data


def policy_projection(config):
 value={key:copy.deepcopy(config.get(key)) for key in SECTIONS}
 value['plugins']={key:copy.deepcopy(val) for key,val in config.get('plugins',{}).items() if key!='installs'}
 authority=module('supervise-openclaw-nemoclaw').mcp_authority(config)
 if authority is None:raise ValueError('full-team-fixed-mcp-required')
 value['mcp']=authority['mcp']
 return value


def paused_config(original):
 value=copy.deepcopy(original);value.setdefault('cron',{})['enabled']=False
 for name,channel in value.get('channels',{}).items():
  if name!='defaults' and isinstance(channel,dict):channel['enabled']=False
 for agent in [value['agents'].setdefault('defaults',{}),*(a for a in value['agents']['entries'].values() if 'heartbeat' in a)]:
  agent.setdefault('heartbeat',{})['every']='0m'
 return value


def native_policy(env,label):
 raw=run([str(HOME/'.local/bin/openshell'),'policy','get','-g','nemoclaw-9123','agent-openclaw','--base','-o','json'],label,env=env,user=True)
 return json.loads(raw)['policy']


def wait_idle(version,env):
 deadline=time.monotonic()+90;empty=0
 while time.monotonic()<deadline:
  raw=run(cli(version,'agent-openclaw','exec','--','openclaw','gateway','call','homecompute-safety.llm.activity','--params','{}','--json'),'native-activity',env=env,user=True,timeout=30)
  value,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])
  if value.get('active_turns')==0:
   empty+=1
   if empty>=2:return
  else:empty=0
  time.sleep(1)
 raise ValueError('native-fixture-not-quiescent')


def synchronize_mcp(version,env):
 supervisor=module('supervise-openclaw-nemoclaw');prep=module('prepare-openclaw-upgrade-assets')
 registry=HOME/'.nemoclaw/gateways/9123/sandboxes.json'
 query=[str(HOME/'.local/bin/openshell'),'sandbox','get','-g','nemoclaw-9123','agent-openclaw','-o','json']
 metadata=json.loads(run(query,'mcp-current-native-identity',env=env,user=True));identifier=metadata['id']
 generation=json.loads(registry.read_bytes())['sandboxes']['agent-openclaw']['lifecycleGeneration']
 class Context:
  cli='qualified-native-cli';uuid=identifier
  state=WORK/'mcp-reconciliation';container='openshell-default--agent-openclaw-'+identifier
  def check_registry(self):
   entry=json.loads(registry.read_bytes())['sandboxes']['agent-openclaw']
   if entry['lifecycleGeneration']!=generation or entry['workload']['reference']!=prep.IMAGES[version] or entry['workload']['sourceRevision']!=prep.VERSIONS[version]:raise ValueError('mcp-native-registry-drift')
  def check_container(self):
   image=subprocess.check_output(['docker','inspect','--format','{{.Config.Image}}',self.container],timeout=15).decode().strip()
   if image not in (prep.IMAGES[version],prep.IMAGES[version].split('@')[1]):raise ValueError('mcp-native-image-drift')
  def observe(self):
   value=json.loads(run(query,'mcp-native-observation',env=env,user=True))
   if value['id']!=identifier:raise ValueError('mcp-native-identity-drift')
   return value['phase']
  def ready(self):health(version,env);return True
  def run(self,argv,label,**kwargs):
   if argv[0]==self.cli:argv=cli(version,*argv[1:])
   return 0,run(argv,label,env=env,user=True,**kwargs)
 ctx=Context();ctx.generation=generation;ctx.state.mkdir(mode=0o700,exist_ok=True)
 try:supervisor.reconcile_mcp(ctx,read_config=lambda:json.loads((STATE/'openclaw.json').read_bytes()),required=True)
 except supervisor.Refused as error:raise ValueError('native-mcp-binding:'+str(error)) from None


def archive_operator_material(version,env):
 archiver=module('archive-openclaw-operator-artifacts');prep=module('prepare-openclaw-upgrade-assets');owned=container()
 registry=HOME/'.nemoclaw/gateways/9123/sandboxes.json';entry=json.loads(registry.read_bytes())['sandboxes']['agent-openclaw'];generation=entry['lifecycleGeneration']
 def guard():
  current=json.loads(registry.read_bytes())['sandboxes']['agent-openclaw']
  if container()!=owned or current['lifecycleGeneration']!=generation or current['workload']['reference']!=prep.IMAGES[version] or current['workload']['sourceRevision']!=prep.VERSIONS[version]:raise ValueError('operator-archive-native-identity-drift')
 return archiver.archive_owned(owned,guard,apply=True,archive_root=WORK/'operator-archives')


def durable_table_digest(db,table,metrics=None):
 # Runtime authority fingerprints are deliberately recomputed by native doctor;
 # preserve their owning job identities, while native_policy separately fences
 # the actual sandbox permissions. Delivery payloads/routing/dedup and task
 # history are durable and are compared in full.
 quoted='"'+table.replace('"','""')+'"'
 fields='store_key,job_id' if table=='cron_job_runtime_authorities' else '*'
 values=[];size=0;cursor=db.execute('select '+fields+' from '+quoted)
 columns=[column[0] for column in cursor.description];per_column=[[] for _ in columns] if metrics is not None else None
 for row in cursor:
  encoded=json.dumps(row,sort_keys=True,default=lambda b:{'bytes':b.hex()});size+=len(encoded.encode())
  if len(values)>=100000 or size>64*1024**2:raise ValueError('native-durable-queue-budget')
  values.append(encoded)
  if per_column is not None:
   for i,value in enumerate(row):per_column[i].append(json.dumps(value,sort_keys=True,default=lambda b:{'bytes':b.hex()}))
 if metrics is not None:metrics.update(rows=len(values),columns={name:hashlib.sha256('\n'.join(sorted(per_column[i])).encode()).hexdigest() for i,name in enumerate(columns)})
 return hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest()


def native_data_integrity():
 global SQLITE_COUNT,SNAPSHOT_METRICS
 SNAPSHOT_METRICS={}
 count=0;queues={};files=0
 for path in STATE.rglob('*'):
  if path.is_symlink() or not path.is_file():continue
  files+=1
  if files>200000:raise ValueError('native-state-file-budget')
  with path.open('rb') as stream:header=stream.read(16)
  if header!=b'SQLite format 3\0':continue
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10) as db:
   db.execute('pragma query_only=on')
   if db.execute('pragma integrity_check').fetchall()!=[('ok',)]:raise ValueError('native-state-sqlite-integrity')
   count+=1
   for (table,) in db.execute('select name from sqlite_master where type="table"'):
    if not re.fullmatch(r'(?:channel_ingress|channel_inbox|cron_jobs|cron_job_runs|tasks|reminders)[a-zA-Z0-9_]*',table) and table not in {'delivery_queue_entries','task_runs','task_delivery_state','cron_run_receipts','cron_job_runtime_authorities','claw_cron_refs'}:continue
    key=str(path.relative_to(STATE))+':'+table;metrics={}
    queues[key]=durable_table_digest(db,table,metrics);SNAPSHOT_METRICS[key]=metrics
 if count<39:raise ValueError('native-restored-sqlite-inventory-incomplete')
 SQLITE_COUNT=count
 return queues


def preparation_diff(before,after,old_metrics,new_metrics):
 work_before=before['workspaces'];work_after=after['workspaces']
 changed_work=[key for key in sorted(set(work_before)|set(work_after)) if work_before.get(key)!=work_after.get(key)]
 changed_tables=[key for key in sorted(set(before['durable_queues'])|set(after['durable_queues'])) if before['durable_queues'].get(key)!=after['durable_queues'].get(key)]
 tables={}
 for key in changed_tables[:8]:
  old=old_metrics.get(key,{});new=new_metrics.get(key,{})
  columns=set(old.get('columns',{}))|set(new.get('columns',{}))
  tables[key]={'before_rows':old.get('rows'),'after_rows':new.get('rows'),'changed_columns':[c for c in sorted(columns) if old.get('columns',{}).get(c)!=new.get('columns',{}).get(c)]}
 result={'conversation_roles':[role for role in sorted(set(before['conversations'])|set(after['conversations'])) if before['conversations'].get(role)!=after['conversations'].get(role)],
  'workspace_changed_count':len(changed_work),'workspace_changed_paths':changed_work[:16],
  'durable_changed_count':len(changed_tables),'durable_tables':tables}
 while len(json.dumps(result).encode())>4096:
  result['truncated']=True
  if result['workspace_changed_paths']:result['workspace_changed_paths'].pop()
  elif result['durable_tables']:result['durable_tables'].pop(next(reversed(result['durable_tables'])))
  else:break
 return result


def changed_policy_paths(before,after,prefix=''):
 if before==after:return []
 if isinstance(before,dict) and isinstance(after,dict):
  paths=[]
  for key in sorted(set(before)|set(after)):
   path=prefix+'.'+key if prefix else key
   paths.extend(changed_policy_paths(before.get(key),after.get(key),path))
   if len(paths)>=64:break
  return paths[:64]
 return [prefix]


def snapshot():
 base=module('restore-openclaw-nemoclaw')
 values={}
 for role in json.loads((STATE/'openclaw.json').read_bytes())['agents']['entries']:
  values[role]=base.logical_conversations(STATE) if role=='main' else role_conversations(role)
 workspaces={}
 for root in STATE.glob('workspace*'):
  for p in root.rglob('*'):
   info=p.lstat();key=str(p.relative_to(STATE))
   metadata={'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&0o7777}
   if p.is_symlink():workspaces[key]={**metadata,'link':str(p.readlink())}
   elif p.is_file():
    with p.open('rb') as f:workspaces[key]={**metadata,'sha256':hashlib.file_digest(f,'sha256').hexdigest()}
   elif p.is_dir():workspaces[key]=metadata
 return {'conversations':values,'workspaces':workspaces,'durable_queues':native_data_integrity()}


def role_conversations(role):
 # The base verifier uses a fixed main path. Reproduce its immutable transcript
 # projection for each retained role without exposing rows or payloads.
 result={};p=STATE/'agents'/role/'agent/openclaw-agent.sqlite'
 if not p.exists():return result
 with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
  db.execute('pragma query_only=on')
  if db.execute('pragma integrity_check').fetchall()!=[('ok',)]:raise ValueError('role-sqlite-integrity')
  tables={r[0] for r in db.execute('select name from sqlite_master where type="table"')}
  for table in ('session_nodes','session_windows','transcript_events'):
   if table not in tables:continue
   rows=db.execute('select * from '+table).fetchall()
   if table!='transcript_events':
    indexes=[i for i,c in enumerate(db.execute('pragma table_info('+table+')')) if c[5]]
    if not indexes:raise ValueError('role-primary-key-missing')
    rows=[tuple(row[i] for i in indexes) for row in rows]
   normalized=sorted(json.dumps(row,sort_keys=True,default=lambda b:{'bytes':b.hex()}) for row in rows)
   result[table]=hashlib.sha256('\n'.join(normalized).encode()).hexdigest()
 return result


def install_ports(core):
 c=container();child=module('setup-openclaw-child-results');target,_,_=child.runtime_profile(core)
 source=run(['docker','exec',c,'cat',str(target)],'read-child-module')
 patched=child.overlay(source,core);p=WORK/'child-patch.js';p.write_bytes(patched)
 run(['docker','cp',str(p),c+':'+str(target)],'install-child-port')
 queue=module('setup-openclaw-llm-queue')
 data=queue.activity_module((ROOT/'deploy/openclaw/safety-plugin/llm-queue.mjs').read_bytes(),core)
 p=WORK/'llm-queue.mjs';p.write_bytes(data)
 run(['docker','cp',str(p),c+':/sandbox/.openclaw/extensions/homecompute-safety/llm-queue.mjs'],'install-activity-port')
 run(['docker','exec','--user','0',c,'chmod','644',str(target),'/sandbox/.openclaw/extensions/homecompute-safety/llm-queue.mjs'],'protect-public-ports')


def mattermost_port(version,env):
 raw=run(cli(version,'agent-openclaw','exec','--','openclaw','plugins','inspect','mattermost','--runtime','--json'),'inspect-native-mattermost',env=env,user=True)
 report,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])
 record=report['install'];root=Path(record['installPath']);v=record.get('version',record.get('resolvedVersion'))
 if record.get('source')!='npm' or v not in ('2026.9.1','2026.9.2') or not str(root).startswith('/sandbox/.openclaw/npm/projects/openclaw-mattermost-'):raise ValueError('qualified-native-mattermost-install-required')
 queue=module('setup-openclaw-llm-queue');filename,_,_=queue.runtime_profile(v);target=root/'dist'/filename
 data=run(['docker','exec',container(),'cat',str(target)],'read-installed-mattermost-runtime');patched=queue.overlay(data,v)
 path=WORK/'mattermost-port.js';path.write_bytes(patched)
 run(['docker','cp',str(path),container()+':'+str(target)],'port-native-mattermost-runtime')
 return str(target),hashlib.sha256(patched).hexdigest(),v


def mattermost_probe(runtime,expected):
 target='/tmp/hc-installed-mattermost-qualification.mjs'
 run(['docker','cp',str(ROOT/'deploy/openclaw/qualification/installed-mattermost.mjs'),container()+':'+target],'stage-installed-mattermost-fixture')
 output=run(['docker','exec','--user','0',container(),'node',target,runtime,expected],'verify-installed-mattermost-ingress',timeout=60)
 if b'"installed_mattermost_ingress_verified":true' not in output:raise ValueError('installed-mattermost-ingress-unverified')


class Provider(http.server.BaseHTTPRequestHandler):
 child_tail='HC_CHILD_EVIDENCE_'+uuid.uuid4().hex
 child_called=False
 parent_received=False
 spawn_requested=False
 yield_requested=False
 def log_message(self,*args):pass
 def do_GET(self):self.reply({'object':'list','data':[{'id':'automation-moe','object':'model','owned_by':'synthetic'}]})
 def reply(self,value):
  raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_POST(self):
  size=int(self.headers.get('Content-Length','0'))
  if not 0<size<=1048576:self.send_error(413);return
  data=json.loads(self.rfile.read(size));message={'role':'assistant','content':'HC_SYNTHETIC_MODEL_OK'};finish='stop'
  if data.get('tool_choice')=='required':
   f=data['tools'][0]['function'];name=f['name'];args={'message':'hello'} if name=='sessions_send' else {'value':'OK'}
   message={'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]};finish='tool_calls'
  else:
   texts=json.dumps(data.get('messages',[]))
   functions={t.get('function',{}).get('name') for t in data.get('tools',[])}
   tool=None;args=None
   if 'HC_NATIVE_CHILD_TASK' in texts and 'HC_NATIVE_HANDOFF_CHECK' not in texts:
    Provider.child_called=True;time.sleep(3)
    message['content']='Synthetic child evidence. '*70+Provider.child_tail
   elif 'HC_NATIVE_HANDOFF_CHECK' in texts and 'sessions_spawn' in functions:
    if Provider.child_tail in texts:
     Provider.parent_received=True;message['content']='HC_PARENT_VERIFIED '+Provider.child_tail
    elif not Provider.spawn_requested:
     Provider.spawn_requested=True;tool='sessions_spawn'
     args={'agentId':'researcher','mode':'run','context':'isolated','task':'HC_NATIVE_CHILD_TASK: Return the synthetic fixture evidence.','cleanup':'keep'}
    elif 'sessions_yield' in functions and not Provider.yield_requested:
     Provider.yield_requested=True;tool='sessions_yield';args={'acknowledgment':'Nova is checking the synthetic evidence.','message':'Wait for the accepted synthetic researcher task.'}
    else:message['content']='Waiting for synthetic researcher evidence.'
   if tool:
    message={'role':'assistant','content':None,'tool_calls':[{'id':'call_'+uuid.uuid4().hex,'type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]};finish='tool_calls'
  result={'id':'chatcmpl-hc-fixture','object':'chat.completion','created':int(time.time()),'model':data.get('model','automation-moe'),
   'choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  if not data.get('stream'):self.reply(result);return
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
  delta=copy.deepcopy(message)
  if 'tool_calls' in delta:delta['tool_calls'][0]['index']=0
  chunk={k:result[k] for k in ('id','created','model')};chunk.update(object='chat.completion.chunk',choices=[{'index':0,'delta':delta,'finish_reason':None}])
  self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode());chunk['choices'][0].update(delta={},finish_reason=finish)
  self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())


def main():
 global ROOT,HEALTH_REDACTOR
 p=argparse.ArgumentParser();p.add_argument('--nonce',required=True);p.add_argument('--expected-policy',required=True);p.add_argument('--manifest-sha256',required=True);a=p.parse_args()
 nonce=str(uuid.UUID(a.nonce));result={'native_upgrade_verified':False,'native_rollback_verified':False,'state_preserved':False,'registry_metadata_replay':True}
 def progress(stage):
  with open('/dev/ttyS0','w') as f:f.write('\nHC_OPENCLAW_UPGRADE_PROGRESS_'+nonce+' '+stage+'\n')
 try:
  if os.geteuid()!=0 or Path('/run/hc-openclaw-qualification-permit').read_text().strip()!=nonce:raise ValueError('isolated-qualification-permit-required')
  if any((p/'device').exists() for p in Path('/sys/class/net').iterdir()):raise ValueError('networkless-clone-required')
  os.umask(0o077);WORK.mkdir(mode=0o700)
  ROOT=Path('/mnt/hc-openclaw-qualification');ROOT.mkdir()
  run(['mount','-o','ro','/dev/vdb',str(ROOT)],'mount-read-only-assets')
  assets=ROOT/'assets';prep=module('prepare-openclaw-upgrade-assets')
  if prep.sha(assets/'manifest.json')!=a.manifest_sha256:raise ValueError('asset-manifest-changed')
  manifest=json.loads((assets/'manifest.json').read_bytes())
  for name,row in manifest['files'].items():
   if '/' in name or name.startswith('.') or prep.sha(assets/name)!=row['sha256'] or (assets/name).stat().st_size!=row['bytes']:raise ValueError('asset-content-changed')
  for version in prep.VERSIONS:
   if prep.sha(assets/('nemoclaw-'+version+'.tar.gz'))!=prep.ARCHIVES[version]:raise ValueError('CLI-artifact-changed')
   prep.catalog_valid(json.loads((assets/('catalog-'+version+'.json')).read_bytes()),version)
   with tarfile.open(assets/('nemoclaw-'+version+'.tar.gz')) as t:t.extractall(WORK,filter='data')
   # Public CLI files must be readable by the genuine native operator.
   for path in [WORK/('nemoclaw-'+version),*(WORK/('nemoclaw-'+version)).rglob('*')]:
    os.chown(path,1000,1000,follow_symlinks=False)
  WORK.chmod(0o755)
  restore_spec=importlib.util.spec_from_file_location('inspect_restore',ROOT/'deploy/openclaw/recovery/inspect-restored-state.py');restore=importlib.util.module_from_spec(restore_spec);restore_spec.loader.exec_module(restore);HEALTH_REDACTOR=restore.redact_diagnostic
  restore.verify_policy(a.expected_policy);progress('baseline-startup')
  restore.gateway_health(lambda _:None)
  env=environment('0.0.129',assets)
  run(['systemctl','--user','stop','homecompute-openclaw-supervisor.service'],'stop-clone-watchdog',env=env,user=True)
  stop_native('0.0.129',env,'pause-clone-native')
  original=json.loads((STATE/'openclaw.json').read_bytes());paused=paused_config(original)
  target=STATE/'openclaw.json';target.write_text(json.dumps(paused));os.chown(target,998,998);target.chmod(0o600)
  before=snapshot();before_metrics=copy.deepcopy(SNAPSHOT_METRICS);policy=policy_projection(paused);sandbox_policy=native_policy(env,'capture-native-policy')
  # Retain the exact provider/registry endpoint identity, with clone-only DNS
  # and a synthetic credential. The VM has no NIC or host share.
  bridge=json.loads(subprocess.check_output(['docker','network','inspect','bridge']))[0]['IPAM']['Config'][0]['Gateway']
  hosts=Path('/etc/hosts');lines=[line for line in hosts.read_text().splitlines() if 'ai.home.arpa' not in line.split()[1:]]
  hosts.write_text('\n'.join(lines)+'\n'+bridge+' ai.home.arpa\n')
  dns=socketserver.UDPServer((bridge,53),FixtureDNS);threading.Thread(target=dns.serve_forever,daemon=True).start()
  resolver=Path('/etc/resolv.conf');resolver.unlink();resolver.write_text('nameserver '+bridge+'\noptions timeout:1 attempts:1\n');resolver.chmod(0o644)
  result['local_negative_dns_fixture']=True
  server=http.server.ThreadingHTTPServer(('0.0.0.0',18080),Provider);threading.Thread(target=server.serve_forever,daemon=True).start()
  run([str(HOME/'.local/bin/openshell'),'provider','update','-g','nemoclaw-9123','compatible-endpoint','--credential','COMPATIBLE_API_KEY'],'replace-clone-only-credential',env=env,user=True)
  progress('start-paused-native-rebuild-source');start_native('0.0.129',env,'start-paused-native-rebuild-source')
  synchronize_mcp('0.0.129',env)
  wait_idle('0.0.129',env)
  archive_operator_material('0.0.129',env)
  current_policy=policy_projection(json.loads((STATE/'openclaw.json').read_bytes()));after=snapshot()
  result['preparation_preserved']=current_policy==policy and after==before
  if after!=before:result['private_preparation_diff']=preparation_diff(before,after,before_metrics,SNAPSHOT_METRICS)
  if current_policy!=policy:
   result['preparation_policy_changed_sections']=[key for key in sorted(set(policy)|set(current_policy)) if policy.get(key)!=current_policy.get(key)]
   result['private_preparation_policy_paths']=changed_policy_paths(policy,current_policy)
   raise ValueError('paused-source-policy-drift')
  # Diagnostic continuation remains networkless and cannot be promoted when
  # preparation changed data. Collect native transition evidence in one run;
  # the final preservation gate still requires the original preparation.
  before=after;before_metrics=copy.deepcopy(SNAPSHOT_METRICS)
  wait_idle('0.0.129',env)
  progress('name-authenticated-oci-content');named=WORK/'named-candidate-image.tar'
  naming=prep.named_archive(assets/'candidate-image.tar',named,prep.IMAGES['0.0.140']);(WORK/'oci-naming-proof.json').write_text(json.dumps(naming,sort_keys=True))
  progress('load-candidate-image');run(['docker','image','load','--input',str(named)],'load-candidate-image',timeout=240)
  run(['docker','image','inspect',prep.IMAGES['0.0.140']],'verify-exact-candidate-cache')
  progress('warm-native-bridge-probe');started=time.monotonic();event_start=int(time.time())
  run(['docker','run','--rm','--pull=never','--network','bridge','--read-only','--cpus','1','--memory','128m','--memory-swap','128m','--pids-limit','64','--cap-drop','ALL','--security-opt','no-new-privileges',BRIDGE_PROBE_IMAGE,'true'],'warm-native-bridge-probe',timeout=120)
  result['bridge_warmup_seconds']=round(time.monotonic()-started,3)
  events=run(['docker','events','--since',str(event_start),'--until',str(int(time.time())+1),'--filter','image='+BRIDGE_PROBE_IMAGE,'--format','{{.Type}} {{.Action}} {{.TimeNano}}'],'native-bridge-warmup-events',timeout=30)
  if len(events)>16384:raise ValueError('native-bridge-event-budget')
  result['bridge_warmup_events']=len(events.splitlines())
  for version,core,label in [('0.0.140','2026.9.2','upgrade'),('0.0.129','2026.9.1','rollback')]:
   env=environment(version,assets);progress(label+'-native-rebuild')
   archive_operator_material('0.0.129' if label=='upgrade' else '0.0.140',environment('0.0.129' if label=='upgrade' else '0.0.140',assets))
   run(cli(version,'agent-openclaw','rebuild','--yes','--verbose'),label+'-native-rebuild',env=env,user=True,timeout=600)
   c=container()
   run(['docker','update','--cpus','2','--memory',str(8*1024**3),'--memory-swap',str(8*1024**3),c],label+'-retain-resource-budget')
   image=subprocess.check_output(['docker','inspect','--format','{{.Config.Image}}',c],timeout=15).decode().strip()
   if image not in (prep.IMAGES[version],prep.IMAGES[version].split('@')[1]):raise ValueError(label+'-image-mismatch')
   install_ports(core)
   mm_runtime,mm_sha,mm_version=mattermost_port(version,env)
   stop_native(version,env,label+'-stop-for-ports')
   start_native(version,env,label+'-start-with-ports')
   synchronize_mcp(version,env)
   health(version,env)
   if label=='upgrade':
    mattermost_probe(mm_runtime,mm_sha);result['installed_mattermost_ingress_verified']=True;result['mattermost_version']=mm_version
   actual=json.loads((STATE/'openclaw.json').read_bytes())
   if policy_projection(actual)!=policy:raise ValueError(label+'-retained-policy-drift')
   if native_policy(env,label+'-native-policy')!=sandbox_policy:raise ValueError(label+'-native-policy-drift')
   current=snapshot();result[label+'_state_preserved']=current==before
   if current!=before:result['private_'+label+'_state_diff']=preparation_diff(before,current,before_metrics,SNAPSHOT_METRICS)
   if core not in run(['docker','exec',container(),'openclaw','--version'],label+'-actual-version').decode():raise ValueError(label+'-runtime-version')
   result['native_'+label+'_verified']=True;progress(label+'-verified')
   if label=='upgrade':
    answer=run(cli(version,'agent-openclaw','exec','--','openclaw','agent','--agent','main','--session-id',str(uuid.uuid4()),'--message','Reply with a short acknowledgement.','--json'),'synthetic-native-turn',env=env,user=True,timeout=180)
    if b'HC_SYNTHETIC_MODEL_OK' not in answer:raise ValueError('native-synthetic-turn-failed')
    result['task_delivery_verified']=True
    handoff=run(cli(version,'agent-openclaw','exec','--','openclaw','agent','--agent','main','--session-id',str(uuid.uuid4()),'--message','HC_NATIVE_HANDOFF_CHECK: Ask researcher for its synthetic fixture evidence, then report the evidence received.','--json'),'synthetic-native-handoff',env=env,user=True,timeout=180)
    deadline=time.monotonic()+150
    while time.monotonic()<deadline and not Provider.parent_received:time.sleep(2)
    result.update(handoff_child_called=Provider.child_called,handoff_yield_requested=Provider.yield_requested,handoff_parent_received=Provider.parent_received)
    if not (Provider.child_called and Provider.parent_received and Provider.yield_requested):raise ValueError('native-handoff-or-tail-retention-failed')
    delivered,_=json.JSONDecoder().raw_decode(handoff.decode()[handoff.index(b'{'):])
    if delivered.get('result',{}).get('meta',{}).get('yielded') is not True:raise ValueError('native-yield-not-executed')
    result['native_yield_verified']=True;result['agent_handoff_verified']=True
    wait_idle(version,env);before=snapshot();before_metrics=copy.deepcopy(SNAPSHOT_METRICS)
  result['native_sqlite_integrity']=True;result['native_sqlite_databases']=SQLITE_COUNT
  result['oci_transport_name_verified']=True;result['operator_archives_verified']=True;result['state_preserved']=all(result.get(k) is True for k in ('preparation_preserved','upgrade_state_preserved','rollback_state_preserved'));result['roles']=len(paused['agents']['entries']);result['authenticated_gateway_verified']=True;result['mcp_binding_verified']=True
  if result['state_preserved'] is not True:result['error_category']='state-comparison-refused'
 except Exception as error:
  result['error_category']=str(error) if isinstance(error,ValueError) else type(error).__name__
  if LAST_HEALTH is not None:result['last_health']=LAST_HEALTH
  logs=sorted(WORK.glob('*.log'),key=lambda p:p.stat().st_mtime_ns) if WORK.exists() else []
  if logs and 'restore' in locals():
   result['private_diagnostic']=restore.redact_diagnostic(logs[-1].read_text(errors='replace')[-8192:])
   result['private_startup_diagnostics']=restore.private_diagnostics({})
 with open('/dev/ttyS0','w') as f:f.write('\nHC_OPENCLAW_UPGRADE_'+nonce+' '+json.dumps(result,sort_keys=True)+'\n')


if __name__=='__main__':main()
