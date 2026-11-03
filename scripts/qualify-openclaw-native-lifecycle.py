#!/usr/bin/env python3
"""Scoped synthetic native upgrade qualification, separate from Atlas ownership."""
from __future__ import annotations
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import socket
import subprocess
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
STATE=Path('/var/lib/homecompute-openclaw-qualification')
HOME=STATE/'worker'
ACCOUNT='hc-oc-qualify'
NAME='agent-oc140'
GATEWAY='nemoclaw-9124'
OPERATOR=Path('/home/hermes-operator')
BUILD=None
ARCHIVES={'0.0.129':'add6fe5676da2f73a21fd17b37350c58ba6da417643ba715596ef7b92ac8c514',
 '0.0.140':'55197aa4f462403363f8ec0567ef268aa742e1122bd82d0cecb8693fc0be1cf2'}
FILES=('scripts/qualify-openclaw-native-lifecycle.py',)


def bundle():
 h=hashlib.sha256()
 for name in FILES:h.update(name.encode()+b'\0'+hashlib.sha256((ROOT/name).read_bytes()).digest())
 return h.hexdigest()


def execute(argv,**kw):
 return subprocess.check_output(argv,timeout=kw.pop('timeout',30),**kw)


def fence():
 names=execute(['docker','ps','--filter','name=^/openshell-default--agent-openclaw-','--format','{{.Names}}']).decode().splitlines()
 if len(names)!=1:raise ValueError('exact production sandbox unavailable')
 production=names[0]
 d=json.loads(execute(['docker','inspect',production]))[0]
 config=execute(['docker','exec',production,'cat','/sandbox/.openclaw/openclaw.json'])
 return {'id':d['Id'],'image':d['Config']['Image'],'limits':[d['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus')],
  'config_sha256':hashlib.sha256(config).hexdigest(),
  'registry_sha256':hashlib.sha256((OPERATOR/'.nemoclaw/gateways/9123/sandboxes.json').read_bytes()).hexdigest(),
  'cli':str((OPERATOR/'.local/bin/nemoclaw').resolve())}


def atomic(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,sort_keys=True));temp.chmod(0o600);temp.replace(path)


def env():
 ip=json.loads(execute(['docker','network','inspect','bridge']))[0]['IPAM']['Config'][0]['Gateway']
 return {'HOME':str(HOME),'USER':ACCOUNT,'LOGNAME':ACCOUNT,'PATH':str(HOME/'bin')+':/usr/bin:/bin',
  'NEMOCLAW_OPENSHELL_BIN':str(HOME/'bin/openshell'),'NEMOCLAW_OPENSHELL_GATEWAY_BIN':str(HOME/'bin/openshell-gateway'),
  'NEMOCLAW_OPENSHELL_SANDBOX_BIN':str(HOME/'bin/openshell-sandbox'),
  'DOCKER_HOST':'unix:///var/run/docker.sock','CI':'1','NO_COLOR':'1','NEMOCLAW_NON_INTERACTIVE':'1',
  'NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE':'1','NEMOCLAW_GATEWAY_PORT':'9124','NEMOCLAW_GATEWAY_BIND_ADDRESS':'127.0.0.1',
  'NEMOCLAW_DASHBOARD_PORT':'18792','NEMOCLAW_AGENT':'openclaw','NEMOCLAW_SANDBOX_NAME':NAME,
  'NEMOCLAW_PROVIDER':'custom','NEMOCLAW_ENDPOINT_URL':f'http://{ip}:18081/v1','NEMOCLAW_TRUSTED_PRIVATE_HOSTS':ip,
  'NEMOCLAW_MODEL':'hc-fixture','NEMOCLAW_PREFERRED_API':'openai-completions','COMPATIBLE_API_KEY':'synthetic-fixture-only',
  'NEMOCLAW_CONTEXT_WINDOW':'8192','NEMOCLAW_MAX_TOKENS':'256','NEMOCLAW_REASONING':'false','NEMOCLAW_WEB_SEARCH_PROVIDER':'none',
  'NEMOCLAW_CPU':'2','NEMOCLAW_RAM':'3Gi','NEMOCLAW_SANDBOX_GPU':'0','NEMOCLAW_AGENT_HEARTBEAT_EVERY':'0m',
  'NEMOCLAW_DISABLE_TELEMETRY':'1','NODE_OPTIONS':'--max-old-space-size=768'}


def as_user(argv,label,timeout=900):
 entry=pwd.getpwnam(ACCOUNT)
 target=STATE/(label+'.log')
 with target.open('xb') as out:
  def credentials():
   os.initgroups(ACCOUNT,entry.pw_gid);os.setgid(entry.pw_gid);os.setuid(entry.pw_uid)
  p=subprocess.Popen(argv,env=env(),cwd=HOME,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
   preexec_fn=credentials,start_new_session=True)
  try:p.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=15)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
   raise RuntimeError(label+' timed out; inspect owned fixture') from None
 if p.returncode:raise RuntimeError(label+' failed; inspect private log')


def cli(version,*args):return [str(HOME/'bin/node'),str(HOME/('nemoclaw-'+version)/'bin/nemoclaw.js'),*args]


def prepare():
 if STATE.exists():raise ValueError('fixture state already exists; inspect instead of overwriting')
 for port in (9124,18792,18081):
  with socket.socket() as s:s.bind(('0.0.0.0',port))
 if execute(['docker','ps','-aq','--filter','name='+NAME]).strip():raise ValueError('fixture sandbox name occupied')
 try:pwd.getpwnam(ACCOUNT)
 except KeyError:pass
 else:raise ValueError('fixture account occupied')
 if not BUILD or BUILD.is_symlink() or BUILD.resolve().parent!=OPERATOR/'.local/share/homecompute/openclaw-cli-builds':raise ValueError('reviewed build directory required')
 receipt=json.loads((BUILD/'receipt.json').read_bytes())
 if set(receipt['artifacts'])!={'0.0.129','0.0.140'}:raise ValueError('both CLI build artifacts required')
 for version,item in receipt['artifacts'].items():
  p=BUILD/('nemoclaw-'+version+'.tar.gz')
  with p.open('rb') as f:
   if hashlib.file_digest(f,'sha256').hexdigest()!=ARCHIVES[version] or item['sha256']!=ARCHIVES[version]:raise ValueError('CLI archive hash mismatch')
 available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
 if available<5*1024**3 or shutil.disk_usage(OPERATOR).free<5*1024**3:raise ValueError('fixture headroom unavailable')
 STATE.mkdir(mode=0o700);HOME.mkdir(mode=0o700)
 atomic(STATE/'fence.json',fence())
 subprocess.run(['useradd','--system','--user-group','--home-dir',str(HOME),'--no-create-home','--shell','/usr/sbin/nologin','--groups','docker',ACCOUNT],check=True)
 entry=pwd.getpwnam(ACCOUNT)
 (HOME/'bin').mkdir()
 shutil.copy2(OPERATOR/'.nvm/versions/node/v22.23.3/bin/node',HOME/'bin/node')
 for name in ('openshell','openshell-sandbox','openshell-gateway'):
  src=OPERATOR/'.local/bin'/name
  if src.exists():shutil.copy2(src.resolve(),HOME/'bin'/name)
 for version in ('0.0.129','0.0.140'):
  with tarfile.open(BUILD/('nemoclaw-'+version+'.tar.gz')) as t:t.extractall(HOME,filter='data')
 shutil.copy2(ROOT/FILES[0],HOME/'fixture.py')
 (HOME/'agents.json').write_text(json.dumps({'agents':[],'main':{'tools':{'allow':[],'deny':['*']},'subagents':{'allowAgents':[],'requireAgentId':True}}}))
 for path in [HOME,*HOME.rglob('*')]:
  if path.is_symlink():os.lchown(path,entry.pw_uid,entry.pw_gid);continue
  os.chown(path,entry.pw_uid,entry.pw_gid)
 # Traversal is limited to this locked fixture account and root.
 os.chown(STATE,0,entry.pw_gid);STATE.chmod(0o710)
 atomic(STATE/'build.json',receipt)
 subprocess.run(['systemd-run','--unit=hc-openclaw-fixture-provider','--collect','--property=MemoryMax=128M',
  '--property=TasksMax=32','--uid='+ACCOUNT,'--gid='+ACCOUNT,'/usr/bin/python3',str(HOME/'fixture.py'),'serve'],check=True,capture_output=True)
 print(json.dumps({'prepared':True,'production_unchanged':fence()==json.loads((STATE/'fence.json').read_bytes())}))


def observe(label):
 as_user([str(HOME/'bin/openshell'),'sandbox','get','-g',GATEWAY,NAME,'-o','json'],label+'-metadata',30)
 d=json.loads((STATE/(label+'-metadata.log')).read_bytes())
 if d['name']!=NAME or d['phase']!='Ready':raise ValueError('fixture not Ready')
 names=execute(['docker','ps','-a','--filter','name='+NAME+'-'+d['id']+'$','--format','{{.Names}}']).decode().splitlines()
 if len(names)!=1 or not names[0].endswith('--'+NAME+'-'+d['id']):raise ValueError('exact fixture container unavailable')
 container=names[0]
 inspected=json.loads(execute(['docker','inspect',container]))[0]
 if inspected['HostConfig']['Memory']>4*1024**3 or inspected['HostConfig']['NanoCpus']>2_000_000_000:raise ValueError('fixture resource cap exceeded')
 v=execute(['docker','exec',container,'openclaw','--version'],timeout=60).decode().strip()
 atomic(STATE/(label+'-observed.json'),{'id':d['id'],'image':inspected['Config']['Image'],'version':v})
 return v,container


def state_check(container,seed=False):
 code="""import pathlib,sqlite3
p=pathlib.Path('/sandbox/.openclaw/workspace');p.mkdir(exist_ok=True)
marker=p/'HC-SYNTHETIC-UPGRADE.txt'
if SEED:
 marker.write_text('homecompute-native-upgrade-state-v1')
 with sqlite3.connect(p/'hc-synthetic.sqlite') as db:
  db.execute('create table evidence (value text)');db.execute('insert into evidence values (?)',('retained',))
assert marker.read_text()=='homecompute-native-upgrade-state-v1'
with sqlite3.connect('file:'+str(p/'hc-synthetic.sqlite')+'?mode=ro',uri=True) as db:
 assert db.execute('pragma integrity_check').fetchall()==[('ok',)]
 assert db.execute('select value from evidence').fetchall()==[('retained',)]
print('state-preserved')
""".replace('SEED',str(seed))
 execute(['docker','exec','--user','998:998',container,'python3','-c',code])
 health=json.loads(execute(['docker','exec',container,'openclaw','gateway','health','--timeout','10000','--json'],timeout=60))
 if health.get('ok') is not True:raise ValueError('authenticated fixture gateway health failed')


def run(action):
 if fence()!=json.loads((STATE/'fence.json').read_bytes()):raise ValueError('production fence changed')
 if action=='baseline':
  as_user(cli('0.0.129','onboard','--name',NAME,'--agent','openclaw','--agents',str(HOME/'agents.json'),
   '--non-interactive','--yes-i-accept-third-party-software','--yes','--no-gpu','--no-sandbox-gpu','--no-ollama-autostart','--control-ui-port','18792'),'baseline')
  expected='2026.9.1'
 elif action in ('upgrade','rollback'):
  version='0.0.140' if action=='upgrade' else '0.0.129'
  as_user(cli(version,NAME,'rebuild','--yes','--verbose'),action)
  expected='2026.9.2' if action=='upgrade' else '2026.9.1'
 else:raise ValueError('unknown lifecycle step')
 actual,container=observe(action)
 state_check(container,seed=action=='baseline')
 if expected not in actual:raise ValueError('native runtime version mismatch')
 if fence()!=json.loads((STATE/'fence.json').read_bytes()):raise ValueError('production fence changed after fixture')
 print(json.dumps({'phase':action,'runtime_version':actual,'production_unchanged':True}))


class Provider(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.reply({'object':'list','data':[{'id':'hc-fixture','object':'model','owned_by':'synthetic'}]})
 def do_POST(self):
  length=int(self.headers.get('Content-Length','0'))
  if not 0<length<=262144:self.send_error(413);return
  data=json.loads(self.rfile.read(length))
  result={'id':'chatcmpl-fixture','object':'chat.completion','created':int(time.time()),'model':'hc-fixture',
   'choices':[{'index':0,'message':{'role':'assistant','content':'SYNTHETIC_FIXTURE_OK'},'finish_reason':'stop'}],
   'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  if data.get('tools'):
   function=data['tools'][0].get('function',data['tools'][0])
   name=function.get('name')
   if name not in ('emit_ok','sessions_send'):self.send_error(400);return
   arguments={'value':'OK'} if name=='emit_ok' else {'message':'hello'}
   result['choices'][0]['message']={'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}
   result['choices'][0]['finish_reason']='tool_calls'
  if data.get('stream'):
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
   chunk={'id':result['id'],'object':'chat.completion.chunk','created':result['created'],'model':'hc-fixture','choices':[{'index':0,'delta':{'role':'assistant','content':'SYNTHETIC_FIXTURE_OK'},'finish_reason':None}]}
   chunk['choices'][0]['delta']=result['choices'][0]['message']
   if data.get('tools'):chunk['choices'][0]['delta']['tool_calls'][0]['index']=0
   self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
   chunk['choices'][0].update(delta={},finish_reason=result['choices'][0]['finish_reason'])
   self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
  else:self.reply(result)
 def reply(self,value):
  raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


def cleanup():
 if fence()!=json.loads((STATE/'fence.json').read_bytes()):raise ValueError('production fence changed')
 registry=HOME/'.nemoclaw/gateways/9124/sandboxes.json'
 if registry.exists():
  rows=json.loads(registry.read_bytes()).get('sandboxes',{})
  if set(rows)-{NAME}:raise ValueError('unexpected fixture registry ownership')
  if NAME in rows:as_user(cli('0.0.140',NAME,'destroy','--yes'),'cleanup-destroy',240)
 if execute(['docker','ps','-aq','--filter','name='+NAME]).strip():raise ValueError('fixture container remains')
 subprocess.run(['systemctl','stop','hc-openclaw-fixture-provider.service'],check=True)
 subprocess.run(['pkill','-TERM','-u',ACCOUNT],check=False)
 subprocess.run(['gpasswd','--delete',ACCOUNT,'docker'],check=True,capture_output=True)
 print(json.dumps({'fixture_stopped':True,'diagnostics_retained':True,'production_unchanged':fence()==json.loads((STATE/'fence.json').read_bytes())}))


def main():
 global BUILD
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('plan','prepare','serve','baseline','upgrade','rollback','cleanup'));p.add_argument('--build-dir',type=Path);p.add_argument('--revision');p.add_argument('--expected-hash');a=p.parse_args()
 if a.action=='plan':print(json.dumps({'bundle_hash':bundle(),'sandbox':NAME,'gateway':GATEWAY,'production_changed':False}));return
 if a.action=='serve':
  ip=env()['NEMOCLAW_TRUSTED_PRIVATE_HOSTS'];http.server.ThreadingHTTPServer((ip,18081),Provider).serve_forever();return
 if os.geteuid()!=0 or socket.gethostname()!='agents' or ROOT!=OPERATOR/'.local/share/homecompute/releases'/str(a.revision) or bundle()!=a.expected_hash:raise ValueError('exact root guest release/hash required')
 subprocess.run(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'merge-base','--is-ancestor',a.revision,'origin/master'],check=True,capture_output=True)
 for name in FILES:
  if execute(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'show',a.revision+':'+name])!=(ROOT/name).read_bytes():raise ValueError('published source mismatch')
 os.umask(0o077)
 BUILD=a.build_dir
 if a.action=='prepare':prepare()
 elif a.action=='cleanup':cleanup()
 else:run(a.action)


if __name__=='__main__':main()
