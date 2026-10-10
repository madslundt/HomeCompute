#!/usr/bin/env python3
"""Run inside the managed OpenClaw sandbox after gateway recovery.

Default validates a candidate without applying config or exec approvals.
The separate browser token must be staged outside the readable workspace.
"""
import argparse,json,os,subprocess,uuid
# Leave space for the running gateway within its existing 2 GiB limit.
os.environ["NODE_OPTIONS"]="--max-old-space-size=384"
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply",action="store_true")
args=parser.parse_args()
from pathlib import Path
config=Path('/sandbox/.openclaw/openclaw.json')
before=json.loads(config.read_text())
names=list(dict.fromkeys(before['agents']['defaults']['skills']+['browser-automation']))
allow=['read','exec','browser']
def deny(values):
 out=[n for n in values if n not in ('group:runtime','group:ui')]
 for name in ['process','canvas','screen','terminal']:
  if name not in out:out.append(name)
 return out
plugins=list(dict.fromkeys(before['plugins']['allow']+['browser']))
token=Path('/sandbox/browser-token').read_text().strip()
assert len(token)>=32
browser={'enabled':True,'defaultProfile':'homecompute','evaluateEnabled':False,
 'ssrfPolicy':{'dangerouslyAllowPrivateNetwork':False,'allowedHostnames':['172.18.0.1']},
 'profiles':{'homecompute':{'cdpUrl':'http://172.18.0.1:18800/?token='+token,'attachOnly':True}}}
changes={'tools.allow':allow,'tools.deny':deny(before['tools']['deny']),
 'tools.exec':{'mode':'allowlist','host':'gateway','timeoutSeconds':15,'safeBins':[],
               'pathPrepend':['/sandbox/.openclaw/workspace/bin']},
 'agents.entries.main.tools.allow':allow,'agents.entries.main.tools.deny':deny(before['agents']['entries']['main']['tools']['deny']),
 'agents.defaults.skills':names,'plugins.allow':plugins,'plugins.entries.browser.enabled':True,'browser':browser}
batch=[{'path':key,'value':value} for key,value in changes.items()]
r=subprocess.run(['openclaw','config','set','--batch-json',json.dumps(batch),'--dry-run'],capture_output=True,text=True,timeout=35)
print(json.dumps({'config_dry_run':r.returncode}),flush=True)
if r.returncode:
 print(r.stderr.replace(token,'[redacted]')[:1500]);raise SystemExit(r.returncode)
if not args.apply:
 print(json.dumps({'applied':False,'candidate_tools':allow,'approval_pattern':'/sandbox/.openclaw/workspace/bin/homecompute-cli'}));raise SystemExit(0)
private=Path('/sandbox/.openclaw/tool-install-backups')/str(uuid.uuid4());private.mkdir(mode=0o700,parents=True)
backup=private/'config-before-tools.json'
fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as out:json.dump(before,out)
helper=Path('/sandbox/.openclaw/workspace/bin/homecompute-cli');helper.chmod(0o755)
for args in [['versions'],['calculate','6*7']]:
 r=subprocess.run([str(helper),*args],capture_output=True,text=True,timeout=5)
 assert r.returncode==0,r.stdout
 print(json.dumps({'cli_probe':args[0],'result':json.loads(r.stdout)}),flush=True)
# Copy the bundled browser skill into the workspace so read stays confined there.
source=Path('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/extensions/browser/skills/browser-automation')
if not (Path('/sandbox/.openclaw/workspace/skills/browser-automation')).exists():
 r=subprocess.run(['openclaw','skills','install',str(source),'--agent','main'],capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stderr
r=subprocess.run(['openclaw','approvals','get','--gateway','--json'],capture_output=True,text=True,timeout=25)
assert r.returncode==0
snapshot=json.loads(r.stdout)['file']
fd=os.open(private/'approvals-before.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as out:json.dump(snapshot,out)
assert not snapshot.get('agents'), 'existing agent approvals require reconciliation'
r=subprocess.run(['openclaw','config','set','--batch-json',json.dumps(batch)],capture_output=True,text=True,timeout=35)
print(json.dumps({'config_apply':r.returncode}),flush=True)
if r.returncode:print(r.stderr.replace(token,'[redacted]')[:1500]);raise SystemExit(r.returncode)
after=json.loads(config.read_text())
for owned in ('models','gateway','proxy','mcp'):assert after.get(owned)==before.get(owned),owned
assert after['plugins']['entries']['homecompute-broker']['enabled'] is False
snapshot.setdefault('agents',{})['main']={'security':'allowlist','ask':'off','askFallback':'deny','autoAllowSkills':False,'allowlist':[{'pattern':str(helper)}]}
r=subprocess.run(['openclaw','approvals','set','--gateway','--stdin','--json'],input=json.dumps(snapshot),capture_output=True,text=True,timeout=25)
print(json.dumps({'approval_apply':r.returncode}),flush=True)
if r.returncode:print(r.stderr[:1200]);raise SystemExit(r.returncode)
Path('/sandbox/browser-token').unlink()
print(json.dumps({'tools':allow,'cli_mode':'allowlist','browser':'authenticated isolated worker','skills':names,'native_ownership_preserved':True}),flush=True)
