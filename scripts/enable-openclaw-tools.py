#!/usr/bin/env python3
"""Run inside the managed OpenClaw sandbox after gateway recovery.

Default validates a candidate without applying config or exec approvals.
The separate browser token must be staged outside the readable workspace.
"""
import argparse,json,os,subprocess,uuid,hashlib,shutil
from pathlib import Path

MCP_TOOLS = {'homecompute__homecompute_'+name for name in
             ('systems','system_read','task_submit','task_status','task_cancel','ha_propose','ha_status','ha_cancel',
              'maintenance_list','maintenance_status','maintenance_run','maintenance_prepare','maintenance_preparation')}

def browser_ssrf_policy():
 # Remote Chromium cannot enforce native DNS pinning. Exact public sources
 # are explicit exceptions; the browser's forced egress proxy still rejects
 # private IPv4/IPv6 destinations on every connection, including redirects.
 return {'dangerouslyAllowPrivateNetwork':False,'allowedHostnames':[
  '127.0.0.1','example.com','docs.openclaw.ai','docs.nvidia.com',
  'docs.litellm.ai','docs.n8n.io','www.home-assistant.io',
  'developers.home-assistant.io','nixos.org','wiki.nixos.org','nix.dev',
  'search.nixos.org','github.com','raw.githubusercontent.com','duckduckgo.com','marginalia-search.com',
  'www.afbudsrejser.dk','www.solfaktor.dk','www.spies.dk',
  'www.tui.dk','www.apollorejser.dk','www.sunweb.dk']}

def protected_guard(guard, digests):
 assert not guard.is_symlink() and guard.stat().st_uid==0 and not guard.stat().st_mode & 0o022
 for filename,expected in digests.items():
  path=guard/filename
  assert not path.is_symlink() and path.stat().st_uid==0 and not path.stat().st_mode & 0o222
  assert expected and hashlib.sha256(path.read_bytes()).hexdigest()==expected, 'reviewed protected browser policy required'

def retained_tools(before, base):
 main=set(before['agents']['entries']['main']['tools'].get('allow',[]))
 extra=set(before['tools'].get('alsoAllow',[]))
 assert main <= set(base)|MCP_TOOLS, 'unknown agent tools require reconciliation'
 assert extra <= set(base)|{'bundle-mcp'}, 'unknown global tools require reconciliation'
 return sorted(set(base)|(main&MCP_TOOLS)), list(base)+sorted(extra&{'bundle-mcp'})

# Leave space for the running gateway within its 8 GiB hard limit.
os.environ["NODE_OPTIONS"]="--max-old-space-size=384"
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply",action="store_true")
parser.add_argument("--helper-sha256", help="reviewed helper digest required for apply")
parser.add_argument("--safety-plugin-sha256", help="reviewed protected browser guard digest required for apply")
parser.add_argument("--browser-relay-sha256", help="reviewed protected browser relay digest required for apply")
parser.add_argument("--safety-manifest-sha256", help="reviewed protected plugin manifest digest required for apply")
parser.add_argument("--safety-package-sha256", help="reviewed protected plugin package digest required for apply")
parser.add_argument("--public-dns-sha256", help="reviewed protected finite DNS module digest required for apply")
args=parser.parse_args()
config=Path('/sandbox/.openclaw/openclaw.json')
before=json.loads(config.read_text())
assert before['agents']['defaults']['workspace']=='/sandbox/.openclaw/workspace'
assert set(before['agents']['entries'])=={'main'}
assert before['tools']['elevated']['enabled'] is False
assert before['tools']['fs']['workspaceOnly'] is True, 'workspace-only filesystem required'
names=list(dict.fromkeys(before['agents']['defaults']['skills']+['browser-automation']))
allow=['read','write','edit','apply_patch','exec','browser','session_status','memory_search','memory_get']
main_allow,global_allow=retained_tools(before,allow)
def deny(values):
 out=[n for n in values if n not in set(allow)|{'*','group:runtime','group:ui'}]
 for name in ['process','canvas','screen','terminal']:
  if name not in out:out.append(name)
 return out
plugins=list(dict.fromkeys(before['plugins']['allow']+['browser']))
token=Path('/sandbox/browser-token').read_text().strip()
assert len(token)>=32
browser={'enabled':True,'defaultProfile':'homecompute','evaluateEnabled':False,
 'ssrfPolicy':browser_ssrf_policy(),
 'profiles':{'homecompute':{'cdpUrl':'http://127.0.0.1:18800/?token='+token,'attachOnly':True}}}
tools=dict(before['tools']);tools.pop('allow',None)
tools.update({'profile':'minimal','alsoAllow':global_allow,'deny':deny(before['tools']['deny']),
 'fs':{**before['tools']['fs'],'workspaceOnly':True},
 'exec':{'mode':'allowlist','host':'gateway','timeoutSeconds':15,'safeBins':[],
               'pathPrepend':['/sandbox/.openclaw/bin']}})
changes={'tools':tools,
 'agents.entries.main.tools.allow':main_allow,'agents.entries.main.tools.deny':deny(before['agents']['entries']['main']['tools']['deny']),
 'agents.defaults.skills':names,'plugins.allow':plugins,'plugins.entries.browser.enabled':True,'browser':browser}
batch=[{'path':key,'value':value} for key,value in changes.items()]
r=subprocess.run(['openclaw','config','set','--batch-json',json.dumps(batch),'--dry-run'],capture_output=True,text=True,timeout=35)
print(json.dumps({'config_dry_run':r.returncode}),flush=True)
if r.returncode:
 print(r.stderr.replace(token,'[redacted]')[:1500]);raise SystemExit(r.returncode)
if not args.apply:
 print(json.dumps({'applied':False,'candidate_tools':allow,'approval_pattern':'/sandbox/.openclaw/bin/homecompute-cli'}));raise SystemExit(0)
guard=Path('/sandbox/.openclaw/extensions/homecompute-safety')
assert before['plugins']['entries'].get('homecompute-safety',{}).get('enabled') is True, 'protected browser guard must be enabled first'
assert 'homecompute-safety' in before['plugins']['allow'] and str(guard) in before['plugins']['load']['paths']
protected_guard(guard,{'index.mjs':args.safety_plugin_sha256,'browser-relay.mjs':args.browser_relay_sha256,
 'openclaw.plugin.json':args.safety_manifest_sha256,'package.json':args.safety_package_sha256,
 'public-dns.mjs':args.public_dns_sha256})
private=Path('/sandbox/.openclaw/tool-install-backups')/str(uuid.uuid4());private.mkdir(mode=0o700,parents=True)
backup=private/'config-before-tools.json'
fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as out:json.dump(before,out)
source=Path('/sandbox/.openclaw/workspace/bin/homecompute-cli')
assert not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest()==args.helper_sha256, 'reviewed helper required'
helper=Path('/sandbox/.openclaw/bin/homecompute-cli')
assert not helper.parent.is_symlink() and not helper.is_symlink()
helper.parent.mkdir(mode=0o700,exist_ok=True)
shutil.copyfile(source,helper);helper.chmod(0o555)
for args in [['versions'],['calculate','6*7']]:
 r=subprocess.run([str(helper),*args],capture_output=True,text=True,timeout=5)
 assert r.returncode==0,r.stdout
 print(json.dumps({'cli_probe':args[0],'result':json.loads(r.stdout)}),flush=True)
# Stage the bundled skill before activation; native install is unnecessary.
assert Path('/sandbox/.openclaw/workspace/skills/browser-automation').is_dir()
r=subprocess.run(['openclaw','approvals','get','--gateway','--json'],capture_output=True,text=True,timeout=25)
assert r.returncode==0
snapshot=json.loads(r.stdout)['file']
fd=os.open(private/'approvals-before.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as out:json.dump(snapshot,out)
assert not snapshot.get('agents'), 'existing agent approvals require reconciliation'
snapshot['defaults']={'security':'deny','ask':'off','askFallback':'deny','autoAllowSkills':False}
snapshot['agents']={'main':{'security':'allowlist','ask':'off','askFallback':'deny','autoAllowSkills':False,'allowlist':[{'pattern':str(helper)}]}}
# Install executable restrictions before enabling exec in the configuration.
r=subprocess.run(['openclaw','approvals','set','--gateway','--stdin','--json'],input=json.dumps(snapshot),capture_output=True,text=True,timeout=25)
assert r.returncode==0, 'executable policy installation refused'
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
