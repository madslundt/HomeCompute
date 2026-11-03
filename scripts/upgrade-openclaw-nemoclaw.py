#!/usr/bin/env python3
"""Guarded, state-preserving deployment of the natively qualified managed tuple."""
from __future__ import annotations
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tarfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
HOME=Path('/home/hermes-operator')
ETC=Path('/etc/homecompute-openclaw')
POLICY='config/openclaw-managed-deployment.json'
FILES=('scripts/upgrade-openclaw-nemoclaw.py',POLICY,'scripts/supervise-openclaw-nemoclaw.py',
 'scripts/restore-openclaw-nemoclaw.py','scripts/recover-openclaw-team.py','scripts/approve-native-openclaw-cli.py',
 'scripts/setup-openclaw-child-results.py','scripts/setup-openclaw-llm-queue.py','scripts/setup-openclaw-yield-acknowledgment.py',
 'scripts/setup-openclaw-public-dns.py','scripts/openclaw-public-dns.py','scripts/prepare-openclaw-upgrade-assets.py',
 'deploy/openclaw/qualification/restored-upgrade.py','deploy/openclaw/safety-plugin/index.mjs','deploy/openclaw/safety-plugin/browser-relay.mjs','deploy/openclaw/safety-plugin/llm-queue.mjs','deploy/openclaw/safety-plugin/public-dns.mjs',
 'deploy/openclaw/nemoclaw/homecompute-public-dns.service',
 'deploy/openclaw/nemoclaw/retained-mcp-route.json','deploy/openclaw/nemoclaw/retained-service-routes.json',
 'deploy/openclaw/tools/browser-policy.yaml','deploy/openclaw/mattermost-policy.yaml')
LEGACY_WRAPPER_SHA='98b975f6c32337e31d1e908856ebe534c36840798c06b86b43193162ff950e89'
LEGACY_SUPERVISOR_SHA='ca116a8de53999f1e0f304ecd6c6bea1a9fb29eb1c129bc6e6be1202af612485'
LEGACY_RECOVERY_SHA='f8b969d95a148e400455fc847945a61d5954baab76eef51b86b328a42757558c'
LEGACY_DNS_SHA='0284c0920f5d9f09df35bba8dcdb3a115c41d3579f969fd34fe1591adb6e074b'


def module(name):
 spec=importlib.util.spec_from_file_location(name.replace('-','_'),ROOT/'scripts'/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


SUP=module('supervise-openclaw-nemoclaw');TEAM=module('recover-openclaw-team');BASE=TEAM.BASE;ASSETS=module('prepare-openclaw-upgrade-assets')


def digest(data):return hashlib.sha256(data).hexdigest()


def bundle():
 h=hashlib.sha256()
 for name in FILES:h.update(name.encode()+b'\0'+hashlib.sha256((ROOT/name).read_bytes()).digest())
 return h.hexdigest()


def declaration():
 value=json.loads((ROOT/POLICY).read_bytes())
 profile=SUP.PROFILES.get(value.get('target_revision'))
 if (value.get('schema_version')!=1 or not profile or profile['tag']!=value.get('target_tag') or profile['core']!=value.get('openclaw_version')
  or profile['image']!=value.get('image') or value.get('openshell_version')!='0.0.116' or value.get('cpus')!=2 or value.get('memory_bytes')!=8*1024**3):raise ValueError('reviewed managed declaration required')
 return value


def qualified(policy,proof):
 keys=('accepted','native_upgrade_verified','native_rollback_verified','state_preserved','authenticated_gateway_verified',
  'task_delivery_verified','agent_handoff_verified','native_yield_verified','installed_mattermost_ingress_verified','oci_transport_name_verified','native_sqlite_integrity','source_disk_read_only','registry_metadata_replay')
 if (not re.fullmatch('[0-9a-f]{40}',policy.get('qualification_revision') or '') or policy.get('mattermost_version')!='2026.9.1'
  or policy.get('production_qualified') is not True or not all(proof.get(k) is True for k in keys)
  or proof.get('network') is not False or proof.get('production_modified') is not False or proof.get('roles')!=12 or type(proof.get('native_sqlite_databases')) is not int or proof['native_sqlite_databases']<39
  or proof.get('source_revision')!=policy.get('qualification_revision') or proof.get('assets_manifest_sha256')!=policy['qualification_assets_sha256']
  or proof.get('policy_sha256')!=policy['qualification_policy_sha256']
  or proof.get('mattermost_version')!=policy.get('mattermost_version')):raise ValueError('accepted matching native qualification required')


def source_guard(revision,expected):
 if os.getuid()!=1000 or socket.gethostname()!='agents' or ROOT!=Path('/srv/homecompute/releases')/revision or bundle()!=expected:raise ValueError('exact published guest lifecycle required')
 for p in (ROOT,*ROOT.parents):
  s=p.lstat()
  if s.st_uid!=0 or s.st_mode&0o022 or stat.S_ISLNK(s.st_mode):raise ValueError('root-protected release required')
 subprocess.run(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'merge-base','--is-ancestor',revision,'origin/master'],check=True,capture_output=True)
 for name in FILES:
  if subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'show',revision+':'+name])!=(ROOT/name).read_bytes():raise ValueError('published lifecycle bytes mismatch')


def environment():
 env={k:os.environ[k] for k in ('HOME','PATH','USER','LOGNAME') if k in os.environ}
 env.update(HOME=str(HOME),USER='hermes-operator',LOGNAME='hermes-operator',PATH=str(HOME/'.local/bin')+':'+str(HOME/'.nvm/versions/node/v22.23.3/bin')+':/usr/local/bin:/usr/bin:/bin')
 for name in ('agent-openclaw.env','supervisor-identity.env'):
  for line in SUP.private_file(ETC/name).decode().splitlines():
   if line and not line.startswith('#'):
    key,value=line.split('=',1);env[key]=value.strip('"')
 return env


def replace(path,data,mode=0o600):
 if path.is_symlink():raise ValueError('managed file symlink refused')
 temporary=path.with_name(path.name+'.upgrade-'+uuid.uuid4().hex)
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 temporary.replace(path);BASE.fsync_directory(path.parent)


def user_service(action):
 env={'HOME':str(HOME),'USER':'hermes-operator','LOGNAME':'hermes-operator','PATH':'/usr/bin:/bin',
  'XDG_RUNTIME_DIR':'/run/user/1000','DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus'}
 subprocess.run(['systemctl','--user',action,'homecompute-openclaw-supervisor.service'],env=env,check=True,timeout=30)


def archive_matches(archive,destination,version):
 if destination.is_symlink():return False
 prefix='nemoclaw-'+version
 expected={}
 with tarfile.open(archive) as tar:
  for member in tar.getmembers():
   rel=Path(member.name).relative_to(prefix).as_posix()
   if rel=='.':continue
   if member.isfile():expected[rel]=('file',hashlib.file_digest(tar.extractfile(member),'sha256').hexdigest(),bool(member.mode&0o100))
   elif member.issym():expected[rel]=('link',member.linkname)
   elif member.isdir():expected[rel]=('dir',)
   else:return False
 actual={}
 for path in destination.rglob('*'):
  key=path.relative_to(destination).as_posix();info=path.lstat()
  if path.is_symlink():actual[key]=('link',str(path.readlink()))
  elif info.st_mode&0o022:return False
  elif path.is_file():actual[key]=('file',ASSETS.sha(path),bool(info.st_mode&0o100))
  elif path.is_dir():actual[key]=('dir',)
  else:return False
 return expected==actual


def stage_cli(build,version):
 if build.is_symlink() or build.resolve().parent!=HOME/'.local/share/homecompute/openclaw-cli-builds':raise ValueError('reviewed public build required')
 archive=build/('nemoclaw-'+version+'.tar.gz')
 if ASSETS.sha(archive)!=ASSETS.ARCHIVES[version]:raise ValueError('qualified CLI artifact changed')
 revision=ASSETS.VERSIONS[version];dest=HOME/'.nemoclaw/releases'/('v'+version+'-'+revision)
 dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if dest.exists():
  if not archive_matches(archive,dest,version):raise ValueError('staged public CLI drift')
  return dest
 temporary=dest.parent/('.stage-'+uuid.uuid4().hex);temporary.mkdir(mode=0o700)
 with tarfile.open(archive) as tar:tar.extractall(temporary,filter='data')
 (temporary/('nemoclaw-'+version)).rename(dest);temporary.rmdir()
 identity=json.loads((dest/'dist/build-identity.json').read_bytes())
 if identity.get('sourceRevision')!=revision or identity.get('nemoclawVersion')!=version:raise ValueError('CLI artifact identity mismatch')
 return dest


def wrapper(root):
 node=HOME/'.nvm/versions/node/v22.23.3/bin/node'
 return ('#!/usr/bin/env bash\nexport PATH="'+str(node.parent)+':$PATH"\nexec "'+str(node)+'" "'+str(root/'bin/nemoclaw.js')+'" "$@"\n').encode()


def stop_exact(ctx):
 ctx.check_registry();ctx.check_container()
 if ctx.observe()!='Ready':raise ValueError('exact ready stop target required')
 # Stop at the native OpenShell lifecycle boundary; the server gracefully
 # stops the owned Docker container rather than guessing an inner phase.
 ctx.run([ctx.openshell,'sandbox','stop','-g',SUP.GATEWAY,SUP.NAME],'managed-native-stop',timeout=180)
 ctx.check_registry();ctx.check_container()
 if ctx.observe()!='Stopped':raise ValueError('positive native stopped state required')
 code,raw=ctx.run(['docker','inspect','--format','{{.State.Status}}',ctx.container],'stopped-container-observation')
 if code or raw.decode().strip() not in ('exited','dead'):raise ValueError('cold native backup requires stopped container')


def require_idle(ctx,record):
 if record.get('model_smoke_pending'):raise ValueError('unresolved model probe requires read-only review before mutation')
 ctx.check_registry();ctx.check_container()
 if ctx.observe()!='Ready' or not ctx.ready():raise ValueError('authenticated ready runtime required before idle inspection')
 for sample in range(2):
  code,raw=ctx.run([ctx.cli,SUP.NAME,'exec','--','openclaw','gateway','call','homecompute-safety.llm.activity','--params','{}','--json'],'managed-idle-inventory',timeout=60)
  try:value,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])
  except (ValueError,TypeError):raise ValueError('native idle inventory unavailable') from None
  if code or value.get('active_turns')!=0:raise ValueError('native turns must be idle before mutation')
  if sample==0:time.sleep(1)


def select_runtime(cli_root,revision):
 profile=SUP.PROFILES[revision]
 replace(HOME/'.local/bin/nemoclaw',wrapper(cli_root),0o755)
 replacements={'NEMOCLAW_INSTALL_REF':revision,'NEMOCLAW_INSTALL_TAG':profile['tag'],'NEMOCLAW_CPU':'2'}
 lines=[]
 for line in (ETC/'agent-openclaw.env').read_text().splitlines():
  key=line.split('=',1)[0];lines.append(key+'='+replacements.pop(key) if key in replacements else line)
 lines.extend(key+'='+value for key,value in replacements.items())
 replace(ETC/'agent-openclaw.env',('\n'.join(lines)+'\n').encode())


def rebuild_observed(tx,cli_root,revision,label,id_key,generation_key):
 """Journal positively observed replacement ownership even after CLI failure."""
 code,_=tx.ctx.run([str(HOME/'.nvm/versions/node/v22.23.3/bin/node'),str(cli_root/'bin/nemoclaw.js'),SUP.NAME,'rebuild','--yes','--verbose'],label,onboarding=True,timeout=1200)
 rc,raw=tx.ctx.run([tx.ctx.openshell,'sandbox','get','-g',SUP.GATEWAY,SUP.NAME,'-o','json'],label+'-observation')
 if rc:raise ValueError('native rebuild outcome unresolved; never replay')
 meta=json.loads(raw);registry=json.loads(tx.ctx.registry.read_bytes())['sandboxes'][SUP.NAME]
 identifier=str(uuid.UUID(meta['id']));generation=str(uuid.UUID(registry['lifecycleGeneration']));profile=SUP.PROFILES[revision]
 if (meta.get('name')!=SUP.NAME or identifier==tx.ctx.uuid or registry.get('workload',{}).get('reference')!=profile['image']
  or registry.get('workload',{}).get('sourceRevision')!=revision or registry.get('agentVersion')!=profile['core']):raise ValueError('native replacement ownership unresolved; never replay')
 tx.record.update({id_key:identifier,generation_key:generation,'nativeRebuildExitCode':code})
 if id_key=='rollbackId':tx.record['rollback_checkpoint']['phase']='replacement-created'
 tx.stage(label+'-identity-observed')
 if code or meta.get('phase')!='Ready':raise ValueError('observed replacement needs reviewed continuation')
 return identifier,generation


def ownership_merge(original,actual):
 """Preserve authority; admit only native ownership/install/bookkeeping fields."""
 expected=TEAM.paused(original)
 for cfg in (original,actual):
  inference=cfg.get('models',{}).get('providers',{}).get('inference',{})
  if inference.get('baseUrl')!='https://inference.local/v1' or inference.get('apiKey')!='unused':raise ValueError('managed inference boundary changed')
 expected['gateway']['auth']=copy.deepcopy(actual['gateway']['auth']);expected['proxy']=copy.deepcopy(actual['proxy'])
 expected['models']['providers']['inference']=copy.deepcopy(actual['models']['providers']['inference'])
 if 'installs' in actual.get('plugins',{}):expected['plugins']['installs']=copy.deepcopy(actual['plugins']['installs'])
 if 'meta' in actual:expected['meta']=copy.deepcopy(actual['meta'])
 TEAM.retain_mcp_handle(expected,actual)
 if expected!=actual:raise ValueError('unreviewed retained configuration drift')
 result=copy.deepcopy(actual)
 for key in ('agents','tools','channels','cron','hooks','browser'):result[key]=copy.deepcopy(original.get(key))
 return result


class ManagedTransaction(TEAM.RetainedTransaction):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.use_native_stop=True
 def stage(self,stage,status='active'):
  if stage=='complete-active-team':status='active'
  super().stage(stage,status)
 def command(self,argv,label,**kwargs):
  if label in ('start-qualified-runtime-ports','start-retained-stopped-version','start-paused-upgrade-source','start-paused-rollback-source'):
   code,raw=self.ctx.run(argv,label,**kwargs)
   self.ctx.check_registry();self.ctx.check_container()
   if self.ctx.observe()!='Ready' or not self.ctx.ready():raise ValueError('exact authenticated native start required')
   return raw
  return super().command(argv,label,**kwargs)


def native_config(tx,label):
 target=tx.ctx.state/('upgrade-config-'+uuid.uuid4().hex+'.json')
 tx.command(['docker','cp',tx.ctx.container+':/sandbox/.openclaw/openclaw.json',str(target)],label)
 return json.loads(target.read_bytes()),target


def snapshot(directory):
 spec=importlib.util.spec_from_file_location('retained_state_projection',ROOT/'deploy/openclaw/qualification/restored-upgrade.py')
 worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker);worker.ROOT=ROOT;worker.STATE=directory
 return worker.snapshot()


def state_permissions(tx):
 code="import pathlib,json,stat;r=pathlib.Path('/state');print(json.dumps({str(p.relative_to(r)):{'uid':p.lstat().st_uid,'gid':p.lstat().st_gid,'mode':stat.S_IMODE(p.lstat().st_mode)} for p in r.rglob('*')}))"
 raw=tx.command(['docker','run','--rm','--pull','never','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--security-opt','no-new-privileges','--entrypoint','/usr/bin/python3','--mount','type=bind,src='+BASE.VOLUME+',dst=/state,readonly',tx.image,'-c',code],'observe-native-state-permissions',timeout=180)
 return json.loads(raw)


def workspace_permissions(rows):
 return {key:value for key,value in rows.items() if key.split('/')[0]=='workspace' or key.split('/')[0].startswith('workspace-')}


def ports(tx,core,expected_mattermost):
 ctx=tx.ctx;ctx.check_registry();ctx.check_container()
 queue=module('setup-openclaw-llm-queue')
 stage='/tmp/homecompute-managed-upgrade-'+uuid.uuid4().hex
 tx.command(['docker','exec','--user','0',ctx.container,'mkdir','--mode=700',stage],'prepare-public-runtime-ports')
 for name in ('setup-openclaw-child-results.py','setup-openclaw-yield-acknowledgment.py','setup-openclaw-llm-queue.py'):
  tx.command(['docker','cp',str(ROOT/'scripts'/name),ctx.container+':'+stage+'/'+name],'stage-public-runtime-patcher')
 tx.command(['docker','cp',str(ROOT/'deploy/openclaw/safety-plugin/llm-queue.mjs'),ctx.container+':'+stage+'/llm-queue.mjs'],'stage-public-activity-module')
 tx.command(['docker','exec','--user','0',ctx.container,'python3',stage+'/setup-openclaw-child-results.py','--runtime-version',core,'--apply'],'install-qualified-child-port')
 if core=='2026.9.1':tx.command(['docker','exec','--user','0',ctx.container,'python3',stage+'/setup-openclaw-yield-acknowledgment.py','--apply'],'restore-qualified-yield-port')
 raw=tx.command([ctx.cli,SUP.NAME,'exec','--','openclaw','plugins','inspect','mattermost','--runtime','--json'],'inspect-active-mattermost',timeout=120)
 report,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):]);record=report['install'];version=record.get('version',record.get('resolvedVersion'))
 if version!=expected_mattermost or record.get('source')!='npm':raise ValueError('qualified active Mattermost version required')
 safety=tx.command(['docker','exec','--user','0',ctx.container,'cat','/sandbox/.openclaw/extensions/homecompute-safety/index.mjs'],'read-protected-safety-index')
 if safety!=queue.safety_overlay((ROOT/'deploy/openclaw/safety-plugin/index.mjs').read_bytes()):raise ValueError('unreviewed protected safety source')
 tx.command(['docker','exec','--user','0',ctx.container,'python3',stage+'/setup-openclaw-llm-queue.py','--runtime-version',version,'--core-version',core,
  '--plugin-root',record['installPath'],'--source-module',stage+'/llm-queue.mjs','--module-sha256',digest((ROOT/'deploy/openclaw/safety-plugin/llm-queue.mjs').read_bytes()),
  '--expected-safety-sha256',digest(safety),'--apply'],'install-qualified-channel-activity-ports')


def verify_runtime(ctx,original):
 expected={'homecompute-safety','homecompute-agent-office','memory-core','mattermost','nemoclaw'}
 desired=original.get('channels',{}).get('mattermost',{})
 accounts={name for name,value in desired.get('accounts',{}).items() if value.get('enabled') is not False} if desired.get('enabled') is not False else set()
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  code,raw=ctx.run([ctx.cli,SUP.NAME,'exec','--','openclaw','gateway','health','--timeout','10000','--json'],'qualified-runtime-health',timeout=60)
  if code:time.sleep(2);continue
  try:health,_=json.JSONDecoder().raw_decode(raw.decode()[raw.index(b'{'):])
  except ValueError:time.sleep(2);continue
  plugins=health.get('plugins',{});mm=health.get('channels',{}).get('mattermost',{}).get('accounts',{})
  connected={name for name,value in mm.items() if value.get('enabled') is True and value.get('connected') is True}
  if health.get('ok') is True and expected<=set(plugins.get('loaded',[])) and not plugins.get('errors') and accounts<=connected:return
  time.sleep(2)
 raise ValueError('required plugins or enabled Mattermost connections unverified')


def native_smoke(ctx,session):
 # A real local model turn is necessary for the client path. Keep its output in
 # memory, never diagnostic logs; the native conversation remains durable data.
 argv=[ctx.cli,SUP.NAME,'exec','--','openclaw','agent','--agent','main','--session-id',session,
  '--message','Reply only with the single word ATLAS_READY.','--json']
 process=subprocess.Popen(argv,env=ctx.env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 try:out,_=process.communicate(timeout=180)
 except subprocess.TimeoutExpired:
  os.killpg(process.pid,15)
  try:process.communicate(timeout=10)
  except subprocess.TimeoutExpired:os.killpg(process.pid,9);process.communicate()
  raise ValueError('native model smoke unresolved; do not replay') from None
 if process.returncode or len(out)>4*1024**2:raise ValueError('native model smoke failed')
 try:
  value,_=json.JSONDecoder().raw_decode(out.decode()[out.index(b'{'):]);result=value['result'];meta=result['meta'];agent=meta['agentMeta']
  valid=value['status']=='ok' and agent['sessionId']==session and agent['provider']=='inference' and agent['model']=='automation-moe'
  valid=valid and any('ATLAS_READY' in p.get('text','') and p.get('isError') is not True for p in result['payloads'])
 except (KeyError,ValueError,TypeError):valid=False
 if not valid:raise ValueError('authenticated native model receipt required')


def activate(tx,backup,original,expected_projection,policy):
 ctx=tx.ctx
 if ctx.observe()!='Ready' or not ctx.ready():raise ValueError('paused replacement gateway required')
 current,path=native_config(tx,'observe-paused-qualified-policy')
 active=ownership_merge(original,current)
 ports(tx,policy['openclaw_version'],policy['mattermost_version'])
 stop_exact(ctx)
 tx.command([ctx.cli,SUP.NAME,'start'],'start-qualified-runtime-ports',timeout=420)
 if ctx.observe()!='Ready' or not ctx.ready():raise ValueError('ported paused gateway not ready')
 verified=backup/'verification-ready'
 if verified.exists():verified.rename(backup/('verification-ready-before-'+str(time.time_ns())))
 verified.mkdir(mode=0o700)
 tx.command(['docker','cp',ctx.container+':/sandbox/.openclaw',str(verified)],'capture-ported-paused-verification',timeout=300)
 if snapshot(verified/'.openclaw')!=expected_projection:raise ValueError('complete immutable role or queue state drift')
 expected_permissions=json.loads((backup/'permissions.json').read_bytes())
 if workspace_permissions(state_permissions(tx))!=workspace_permissions(expected_permissions):raise ValueError('native workspace ownership or mode drift')
 current,_=native_config(tx,'read-final-paused-native-ownership');tx.policy=ownership_merge(original,current)
 tx.previous_auth=copy.deepcopy(original['gateway']['auth'])
 module('approve-native-openclaw-cli').ensure_cli(ctx)
 tx.commit_identity();tx.stage('qualified-before-active-policy')
 # Reuse the already reviewed fixed-provider/policy/DNS restoration and active
 # team transition. The transaction remains fenced until final verification.
 tx.finish_active(backup,backup/'.openclaw',TEAM.complete_hashes(backup/'.openclaw'),(ctx.uuid,ctx.generation))
 actual,_=native_config(tx,'verify-active-authority')
 for key in ('agents','tools','channels','cron','hooks','browser'):
  if actual.get(key)!=original.get(key):raise ValueError('active authority drift')
 verify_runtime(ctx,original)
 if tx.record.get('model_smoke_pending'):raise ValueError('unresolved native model probe must not be replayed')
 session=str(uuid.uuid4());tx.record.update(model_smoke_pending=True,model_smoke_session=session)
 tx.stage('model-smoke-starting')
 native_smoke(ctx,session)
 tx.record['model_smoke_pending']=False;tx.record['model_smoke_verified']=True
 tx.stage('complete-qualified-managed-upgrade','complete')
 user_service('start')
 return {'phase':'Ready','complete_team_restored':True,'image':ctx.image,'core':ctx.core_version,'source_revision':tx.record['sourceRevision']}


def production_catalog(ctx,cli_root,policy):
 node=str(HOME/'.nvm/versions/node/v22.23.3/bin/node')
 result=subprocess.run([node,'-',str(cli_root),'0.0.140',policy['target_revision']],input=ASSETS.CATALOG_JS.encode(),env=ctx.env,
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
 if result.returncode or not 2<=len(result.stdout)<=65536:raise ValueError('authenticated production catalog unavailable')
 catalog=json.loads(result.stdout);ASSETS.catalog_valid(catalog,'0.0.140')
 if catalog['openclaw']['reference']!=policy['image']:raise ValueError('production catalog differs from qualified workload')
 path=ctx.state/('production-catalog-'+uuid.uuid4().hex+'.json');BASE.atomic_json(path,catalog)
 ownership="const f=require(process.argv[1]).resolveGatewayRebuildAuthority;const a=f({gatewayName:'nemoclaw-9123',gatewayPort:9123});if(a.gatewayName!=='nemoclaw-9123'||a.gatewayPort!==9123)process.exitCode=1"
 authority=subprocess.run([node,'-e',ownership,str(cli_root/'dist/lib/onboard/gateway-teardown-authority.js')],env=ctx.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
 if authority.returncode:raise ValueError('exact native gateway ownership unresolved')
 return path


def apply(args,policy,proof):
 qualified(policy,proof)
 env=environment();ctx=SUP.Supervisor(env)
 if ctx.revision!=SUP.REVISION:raise ValueError('qualified prior tuple required')
 ctx.check_registry();ctx.check_container()
 if ctx.observe()!='Ready' or not ctx.ready():raise ValueError('healthy exact prior runtime required')
 tx=ManagedTransaction(ctx,args.expected_config_sha256);tx.path=ctx.state/'managed-upgrade-transaction.json'
 if tx.path.exists() and json.loads(SUP.private_file(tx.path)).get('status') not in ('complete','refused'):raise ValueError('retained upgrade needs reviewed continuation')
 before,path=native_config(tx,'upgrade-preflight-config')
 if digest(path.read_bytes())!=args.expected_config_sha256 or len(before['agents']['entries'])!=12:raise ValueError('selected complete policy required')
 projected={key:before.get(key) for key in ('agents','tools','channels','cron','hooks','browser')}
 if digest(json.dumps(projected,sort_keys=True,separators=(',',':')).encode())!=policy['qualification_policy_sha256']:raise ValueError('production authority differs from qualified policy')
 tx.inspect()
 layer=tx.command(['docker','diff',ctx.container],'review-prior-runtime-layer',timeout=90).decode()
 if TEAM.unreviewed_layer_paths(layer,tx.patches):raise ValueError('unreviewed runtime layer drift')
 tx.browser_scratch=any(line[2:].startswith('/var/tmp/') for line in layer.splitlines())
 safety=tx.command(['docker','exec','--user','0',ctx.container,'cat','/sandbox/.openclaw/extensions/homecompute-safety/index.mjs'],'review-prior-safety-source')
 queue=module('setup-openclaw-llm-queue')
 if safety!=queue.safety_overlay((ROOT/'deploy/openclaw/safety-plugin/index.mjs').read_bytes()):raise ValueError('prior safety source drift')
 if (digest((HOME/'.local/bin/nemoclaw').read_bytes())!=LEGACY_WRAPPER_SHA
  or digest((ETC/'supervise-openclaw-nemoclaw.py').read_bytes())!=LEGACY_SUPERVISOR_SHA
  or digest((ETC/'restore-openclaw-nemoclaw.py').read_bytes())!=LEGACY_RECOVERY_SHA
  or digest(Path('/etc/homecompute-public-dns/openclaw-public-dns.py').read_bytes())!=LEGACY_DNS_SHA):raise ValueError('unreviewed host CLI or recovery program drift')
 require_idle(ctx,{})
 cli140=stage_cli(args.build_dir,'0.0.140');cli129=stage_cli(args.build_dir,'0.0.129')
 catalog=production_catalog(ctx,cli140,policy)
 backup=ctx.state/('managed-upgrade-backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
 for name in ('agent-openclaw.env','supervisor-identity.env','supervise-openclaw-nemoclaw.py','restore-openclaw-nemoclaw.py'):
  (backup/name).write_bytes((ETC/name).read_bytes())
 (backup/'nemoclaw-wrapper').write_bytes((HOME/'.local/bin/nemoclaw').read_bytes())
 tx.record={'schemaVersion':1,'startedAt':time.time(),'sourceRevision':args.revision,'oldId':ctx.uuid,'oldGeneration':ctx.generation,'backup':str(backup),'cli140':str(cli140),'cli129':str(cli129),'qualificationSha256':args.expected_qualification_sha256,'productionCatalog':str(catalog)}
 user_service('stop')
 tx.stage('guarded-upgrade-starting')
 replace(ETC/'supervise-openclaw-nemoclaw.py',(ROOT/'scripts/supervise-openclaw-nemoclaw.py').read_bytes())
 replace(ETC/'restore-openclaw-nemoclaw.py',(ROOT/'scripts/restore-openclaw-nemoclaw.py').read_bytes())
 try:
  stop_exact(ctx);tx.stage('prior-native-stopped')
  tx.command(['docker','cp',ctx.container+':/sandbox/.openclaw',str(backup)],'capture-cold-complete-team',timeout=300)
  retained=backup/'.openclaw';hashes=TEAM.complete_hashes(retained);expected_projection=snapshot(retained)
  BASE.atomic_json(backup/'immutable-projection.json',expected_projection)
  if json.loads((retained/'openclaw.json').read_bytes())!=before:raise ValueError('policy changed before cold capture')
  BASE.atomic_json(backup/'complete-hashes.json',hashes)
  tx.permissions=state_permissions(tx);tx.stage('backup-verified')
  # The complete private backup and native's own state-preserving journal are
  # retained. No version switch or native deletion precedes this validation.
  paused=backup/'paused-config.json';BASE.atomic_json(paused,TEAM.paused(before))
  tx.command(['docker','cp',str(paused),ctx.container+':/sandbox/.openclaw/openclaw.json'],'quarantine-upgrade-channels')
  tx.volume_command("import os;os.chown('/state/openclaw.json',998,998);os.chmod('/state/openclaw.json',0o600)",'paused-policy-owner-mode')
  tx.stage('verified-backup-before-native-rebuild')
  tx.command([ctx.cli,SUP.NAME,'start'],'start-paused-upgrade-source',timeout=420)
  ownership_merge(before,native_config(tx,'verify-ready-paused-upgrade-source')[0])
  require_idle(ctx,tx.record);tx.stage('ready-paused-source-before-native-rebuild')
  next_env=dict(ctx.env,OPENCLAW_NEMOCLAW_MODEL_KEY_FILE=str(ctx.keyfile),NEMOCLAW_INSTALL_REF=policy['target_revision'],NEMOCLAW_INSTALL_TAG=policy['target_tag'],NEMOCLAW_CPU='2')
  ctx.env.update({key:next_env[key] for key in ('NEMOCLAW_INSTALL_REF','NEMOCLAW_INSTALL_TAG','NEMOCLAW_CPU')})
  identifier,generation=rebuild_observed(tx,cli140,policy['target_revision'],'managed-qualified-rebuild','newId','newGeneration')
  next_env.update(OPENCLAW_EXPECTED_SANDBOX_ID=identifier,OPENCLAW_EXPECTED_GENERATION=generation)
  next_ctx=SUP.Supervisor(next_env,operator_resume=True)
  next_ctx.check_registry();next_ctx.check_container()
  tx.ctx=next_ctx;tx.image=next_ctx.image
  tx.command(['docker','update','--cpus','2','--memory',str(policy['memory_bytes']),'--memory-swap',str(policy['memory_bytes']),next_ctx.container],'retain-approved-resource-budget')
  tx.inspect()
  tx.stage('qualified-replacement-paused')
  # All remaining work runs under the exact new CLI without changing the
  # retained original global installation used by the rollback tuple.
  select_runtime(cli140,policy['target_revision'])
  tx.record['expectedProjectionSha256']=digest((backup/'immutable-projection.json').read_bytes())
  tx.policy=ownership_merge(before,native_config(tx,'verify-paused-policy-before-ports')[0]);tx.previous_auth=copy.deepcopy(before['gateway']['auth'])
  return activate(tx,backup,before,expected_projection,policy)
 except Exception as error:
  tx.record['failedStage']=tx.record.get('stage');tx.record['failureCode']=str(error) if isinstance(error,(ValueError,BASE.RecoveryRefused)) else type(error).__name__
  tx.stage('failed-awaiting-reviewed-continuation','failed');raise


def observed_context(record):
 env=environment();openshell=str(HOME/'.local/bin/openshell')
 raw=subprocess.check_output([openshell,'sandbox','get','-g',SUP.GATEWAY,SUP.NAME,'-o','json'],env=env,timeout=30)
 meta=json.loads(raw);registry=json.loads((HOME/'.nemoclaw/gateways/9123/sandboxes.json').read_bytes())['sandboxes'][SUP.NAME]
 identifier=str(uuid.UUID(meta['id']));revision=registry.get('workload',{}).get('sourceRevision')
 expected_generation=recorded_generation(record,identifier)
 if meta.get('name')!=SUP.NAME or revision not in SUP.PROFILES or registry.get('lifecycleGeneration')!=expected_generation:raise ValueError('retained transaction identity mismatch')
 env.update(NEMOCLAW_INSTALL_REF=revision,NEMOCLAW_INSTALL_TAG=SUP.PROFILES[revision]['tag'],OPENCLAW_EXPECTED_SANDBOX_ID=identifier,
  OPENCLAW_EXPECTED_GENERATION=str(uuid.UUID(registry['lifecycleGeneration'])))
 ctx=SUP.Supervisor(env,operator_resume=True);ctx.check_registry();ctx.check_container()
 return ctx


def recorded_generation(record,identifier):
 for label in ('old','new','rollback'):
  if record.get(label+'Id')==identifier:
   return str(uuid.UUID(record[label+'Generation']))
 raise ValueError('unrecorded native replacement identity')


def retained_transaction():
 state=HOME/'.local/state/homecompute-openclaw-supervisor';path=state/'managed-upgrade-transaction.json'
 record=json.loads(SUP.private_file(path));backup=Path(record['backup'])
 if backup.is_symlink() or backup.resolve().parent!=state.resolve():raise ValueError('retained backup path refused')
 original=backup/'.openclaw'
 if TEAM.complete_hashes(original)!=json.loads((backup/'complete-hashes.json').read_bytes()):raise ValueError('retained complete backup drift')
 return record,path,backup


def resume(args,policy,proof):
 qualified(policy,proof);record,path,backup=retained_transaction()
 if record.get('status') not in ('failed','active') or not record.get('newId'):raise ValueError('exact paused replacement continuation required')
 ctx=observed_context(record)
 if ctx.revision!=policy['target_revision'] or ctx.uuid!=record['newId']:raise ValueError('exact candidate continuation required')
 # Never replay an unresolved real model probe or reopen an already active team.
 stages={x.get('stage') for x in record.get('history',[])}
 if 'complete-active-team' in stages or 'model-smoke-starting' in stages:raise ValueError('active team or unresolved model probe needs read-only review')
 if ctx.observe()!='Ready':raise ValueError('ready paused replacement required')
 cli_root=Path(record['cli140'])
 if cli_root!=HOME/'.nemoclaw/releases'/('v0.0.140-'+policy['target_revision']):raise ValueError('qualified continuation CLI path required')
 select_runtime(cli_root,policy['target_revision'])
 original=json.loads((backup/'.openclaw/openclaw.json').read_bytes());current,_=native_config(ManagedTransaction(ctx,'unused'),'resume-policy-review')
 ownership_merge(original,current)
 expected=json.loads((backup/'immutable-projection.json').read_bytes())
 tx=ManagedTransaction(ctx,digest(json.dumps(current).encode()));tx.path=path;tx.record=record
 tx.record['continuationSourceRevision']=args.revision
 try:return activate(tx,backup,original,expected,policy)
 except Exception as error:
  tx.record['failedStage']=tx.record.get('stage');tx.record['failureCode']=str(error) if isinstance(error,(ValueError,BASE.RecoveryRefused)) else type(error).__name__
  tx.stage('failed-awaiting-reviewed-continuation','failed');raise


def rollback_transition(args,policy,proof):
 qualified(policy,proof);record,path,backup=retained_transaction()
 if record.get('rollback_checkpoint'):raise ValueError('retained rollback checkpoint requires resume-rollback')
 ctx=observed_context(record)
 if ctx.observe() not in ('Ready','Stopped'):raise ValueError('native retained rollback requires ready or stopped exact target')
 if record.get('model_smoke_pending'):raise ValueError('unresolved model probe requires read-only review before rollback')
 if ctx.observe()=='Ready':require_idle(ctx,record)
 prior_status=record.get('status')
 tx=ManagedTransaction(ctx,'rollback');tx.path=path;tx.record=record
 user_service('stop')
 record['rollback_attempt_active']=True
 tx.stage('rollback-starting')
 if ctx.observe()=='Ready':stop_exact(ctx)
 cold=backup/('rollback-'+str(time.time_ns()));cold.mkdir(mode=0o700)
 tx.command(['docker','cp',ctx.container+':/sandbox/.openclaw',str(cold)],'capture-current-state-before-version-rollback',timeout=300)
 current=json.loads((cold/'.openclaw/openclaw.json').read_bytes())
 if prior_status=='complete':original=current
 else:
  original=json.loads((backup/'.openclaw/openclaw.json').read_bytes());ownership_merge(original,TEAM.paused(current))
 expected=snapshot(cold/'.openclaw');BASE.atomic_json(cold/'immutable-projection.json',expected)
 BASE.atomic_json(cold/'permissions.json',state_permissions(tx))
 BASE.atomic_json(cold/'desired-active-policy.json',original);BASE.atomic_json(cold/'complete-hashes.json',TEAM.complete_hashes(cold/'.openclaw'))
 record['rollback_checkpoint']={'cold':str(cold),'phase':'captured','prior_status':prior_status,'projection_sha256':digest((cold/'immutable-projection.json').read_bytes()),'policy_sha256':digest((cold/'desired-active-policy.json').read_bytes()),'permissions_sha256':digest((cold/'permissions.json').read_bytes())}
 tx.stage('rollback-cold-state-verified')
 paused=cold/'paused-config.json';BASE.atomic_json(paused,TEAM.paused(current))
 tx.command(['docker','cp',str(paused),ctx.container+':/sandbox/.openclaw/openclaw.json'],'pause-version-rollback-channels')
 tx.volume_command("import os;os.chown('/state/openclaw.json',998,998);os.chmod('/state/openclaw.json',0o600)",'rollback-paused-config-owner')
 previous=SUP.PROFILES[SUP.REVISION];legacy=Path(record['cli129'])
 if legacy!=HOME/'.nemoclaw/releases'/('v0.0.129-'+SUP.REVISION):raise ValueError('retained qualified CLI path required')
 if ctx.revision!=SUP.REVISION:
  tx.command([ctx.cli,SUP.NAME,'start'],'start-paused-rollback-source',timeout=420)
  ownership_merge(original,native_config(tx,'verify-ready-paused-rollback-source')[0])
  require_idle(ctx,record)
  tx.ctx.env.update(NEMOCLAW_INSTALL_REF=SUP.REVISION,NEMOCLAW_INSTALL_TAG=previous['tag'],NEMOCLAW_CPU='2')
  record['rollback_checkpoint']['phase']='native-rebuild-starting';tx.stage('rollback-native-rebuild-starting')
  rebuild_observed(tx,legacy,SUP.REVISION,'qualified-retained-version-rebuild','rollbackId','rollbackGeneration')
 else:
  select_runtime(legacy,SUP.REVISION)
  tx.command([ctx.cli,SUP.NAME,'start'],'start-retained-stopped-version',timeout=420)
 return finish_rollback(args,policy,record,path,backup,cold,original,expected)



def finish_rollback(args,policy,record,path,backup,cold,original,expected):
 ctx=observed_context(record);tx=ManagedTransaction(ctx,'rollback');tx.path=path;tx.record=record
 legacy=Path(record['cli129']);previous=SUP.PROFILES[SUP.REVISION]
 if ctx.revision!=SUP.REVISION:raise ValueError('retained tuple rollback mismatch')
 tx.command(['docker','update','--cpus','2','--memory',str(8*1024**3),'--memory-swap',str(8*1024**3),ctx.container],'retain-rollback-resource-budget')
 select_runtime(legacy,SUP.REVISION)
 retained_policy={**policy,'openclaw_version':'2026.9.1'}
 result=activate(tx,cold,original,expected,retained_policy)
 record['rollbackSourceRevision']=args.revision;record['rollback_data']=str(cold);record['rollback_attempt_active']=False;record['rollback_checkpoint']['phase']='complete';tx.stage('complete-retained-version-rollback','complete')
 return result

def rollback(args,policy,proof):
 try:return rollback_transition(args,policy,proof)
 except Exception as error:
  state=HOME/'.local/state/homecompute-openclaw-supervisor';path=state/'managed-upgrade-transaction.json'
  if path.exists():
   record=json.loads(SUP.private_file(path))
   if record.get('rollback_attempt_active') is not True:raise
   record['rollback_failed_stage']=record.get('stage');record['status']='failed'
   record['rollback_failure_code']=str(error) if isinstance(error,(ValueError,BASE.RecoveryRefused)) else type(error).__name__
   BASE.atomic_json(path,record)
  raise


def resume_rollback(args,policy,proof):
 qualified(policy,proof);record,path,backup=retained_transaction();checkpoint=record.get('rollback_checkpoint',{})
 if checkpoint.get('phase')!='replacement-created':raise ValueError('rollback native outcome needs read-only review; never replay rebuild')
 cold=Path(checkpoint['cold'])
 if cold.is_symlink() or cold.resolve().parent!=backup.resolve():raise ValueError('rollback cold checkpoint path refused')
 if digest((cold/'desired-active-policy.json').read_bytes())!=checkpoint['policy_sha256'] or digest((cold/'immutable-projection.json').read_bytes())!=checkpoint['projection_sha256']:raise ValueError('rollback desired authority checkpoint drift')
 if digest((cold/'permissions.json').read_bytes())!=checkpoint['permissions_sha256']:raise ValueError('rollback native permissions checkpoint drift')
 if TEAM.complete_hashes(cold/'.openclaw')!=json.loads((cold/'complete-hashes.json').read_bytes()):raise ValueError('rollback cold state drift')
 original=json.loads((cold/'desired-active-policy.json').read_bytes());expected=json.loads((cold/'immutable-projection.json').read_bytes())
 if record.get('model_smoke_pending'):raise ValueError('unresolved model probe must be inspected before continuing')
 return finish_rollback(args,policy,record,path,backup,cold,original,expected)


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('plan','apply','resume','rollback','resume-rollback'),default='plan',nargs='?')
 p.add_argument('--revision');p.add_argument('--expected-hash');p.add_argument('--qualification-proof',type=Path);p.add_argument('--expected-qualification-sha256');p.add_argument('--expected-config-sha256');p.add_argument('--build-dir',type=Path);a=p.parse_args()
 os.umask(0o077)
 policy=declaration()
 if a.action=='plan':print(json.dumps({'bundle_hash':bundle(),'declaration':policy,'deployment_enabled':policy.get('production_qualified') is True}));return
 for name,value,length in (('revision',a.revision,40),('expected-hash',a.expected_hash,64),('expected-qualification-sha256',a.expected_qualification_sha256,64)):
  if not re.fullmatch('[0-9a-f]{'+str(length)+'}',value or ''):p.error(name+' requires a complete reviewed hash')
 if not a.qualification_proof:p.error('qualification-proof is required')
 source_guard(a.revision,a.expected_hash)
 raw=SUP.private_file(a.qualification_proof)
 if len(raw)>65536 or digest(raw)!=a.expected_qualification_sha256:raise ValueError('selected private qualification receipt mismatch')
 proof=json.loads(raw);qualified(policy,proof)
 if a.action=='apply' and (not a.build_dir or not re.fullmatch('[0-9a-f]{64}',a.expected_config_sha256 or '')):p.error('apply requires reviewed build-dir and exact config hash')
 state=HOME/'.local/state/homecompute-openclaw-supervisor'
 handles=[]
 try:
  # Match the existing error-recovery lock first; keep every destructive and
  # continuation edge exclusive, including the final health verification.
  for name in ('error-recovery.lock','managed-upgrade.lock'):
   fd=os.open(state/name,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600);handles.append(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  result={'apply':apply,'resume':resume,'rollback':rollback,'resume-rollback':resume_rollback}[a.action](a,policy,proof)
  print(json.dumps(result,sort_keys=True))
 finally:
  for fd in reversed(handles):os.close(fd)


if __name__=='__main__':main()
