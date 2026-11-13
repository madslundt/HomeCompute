#!/usr/bin/env python3
"""Create owner Hermes profiles pinned to the three ChatGPT LiteLLM aliases."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import runpy

ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / 'config/hermes-chatgpt-profiles.json'
KEY_DIR = Path.home() / '.config/homecompute-hermes/secrets'
KEY_ENV = 'HOMECOMPUTE_CHATGPT_API_KEY'
PYTHON = '/opt/hermes/.venv/bin/python'
INSPECT = r'''import hashlib,json,pathlib,re,sys,yaml
root=pathlib.Path('/sandbox/.hermes')
owner=yaml.safe_load((root/'config.yaml').read_text()) or {}
expected=json.loads(sys.stdin.read())
result={}
for item in expected['profiles']:
 name=item['name'];p=root/'profiles'/name
 if p.is_symlink():result[name]={'state':'conflict'};continue
 if not p.exists():result[name]={'state':'absent'};continue
 config_path=p/'config.yaml';env_path=p/'.env';marker_path=p/'.homecompute-chatgpt-profile.json'
 if any(x.is_symlink() for x in (p,config_path,env_path,marker_path)) or not all(x.is_file() for x in (config_path,env_path,marker_path)):
  result[name]={'state':'conflict'};continue
 config=yaml.safe_load(config_path.read_text()) or {};model=config.get('model') or {}
 marker=json.loads(marker_path.read_text())
 env=env_path.read_text();matches=re.findall(r'(?m)^HOMECOMPUTE_CHATGPT_API_KEY=(.*)$',env)
 checks={
  'marker':marker.get('schema_version')==1 and marker.get('name')==name and marker.get('model')==item['model'] and marker.get('endpoint')==expected['endpoint'],
  'model':model.get('default')==item['model'] and model.get('provider')=='custom'
          and model.get('base_url')==expected['endpoint'] and model.get('key_env')=='HOMECOMPUTE_CHATGPT_API_KEY'
          and not model.get('api_key'),
  'owner_tools':config.get('mcp_servers')==owner.get('mcp_servers')
          and config.get('memory')==owner.get('memory')
          and config.get('toolsets')==owner.get('toolsets')
          and config.get('platform_toolsets')==owner.get('platform_toolsets'),
  'key':len(matches)==1 and matches[0].startswith('sk-') and not any(c.isspace() for c in matches[0]),
 }
 repairable_marker=(marker.get('schema_version')==1 and marker.get('name')=='HOMECOMPUTE_CHATGPT_API_KEY'
                    and marker.get('model')==item['model'] and marker.get('endpoint')==expected['endpoint'])
 other_checks=all(value for key,value in checks.items() if key!='marker')
 state='managed' if all(checks.values()) else 'repairable' if not checks['marker'] and repairable_marker and other_checks else 'conflict'
 result[name]={'state':state,'checks':checks,
               'config_sha256':hashlib.sha256(config_path.read_bytes()).hexdigest(),
               'env_sha256':hashlib.sha256(env_path.read_bytes()).hexdigest(),
               'marker_sha256':hashlib.sha256(marker_path.read_bytes()).hexdigest()}
print(json.dumps(result,sort_keys=True))
'''

PATCH = r'''import json,os,pathlib,re,stat,sys,tempfile,yaml
spec=json.loads(sys.stdin.read());root=pathlib.Path('/sandbox/.hermes')
for item in spec['profiles']:
 name=item['name'];p=root/'profiles'/name
 assert p.is_dir() and not p.is_symlink()
 for q in (p/'config.yaml',p/'.env'):
  assert q.is_file() and not q.is_symlink()
 config=yaml.safe_load((p/'config.yaml').read_text()) or {}
 owner=yaml.safe_load((root/'config.yaml').read_text()) or {}
 assert config.get('mcp_servers')==owner.get('mcp_servers')
 assert config.get('memory')==owner.get('memory')
 assert config.get('toolsets')==owner.get('toolsets')
 assert config.get('platform_toolsets')==owner.get('platform_toolsets')
 model=config.setdefault('model',{})
 model['default']=item['model'];model['provider']='custom';model['base_url']=spec['endpoint']
 model.pop('api_key',None);model['key_env']='HOMECOMPUTE_CHATGPT_API_KEY'
 path=p/'config.yaml';data=yaml.safe_dump(config,sort_keys=False).encode()
 temp=path.with_name('.config.yaml.homecompute-tmp')
 with temp.open('xb') as f:os.chmod(temp,0o600);f.write(data);f.flush();os.fsync(f.fileno())
 os.replace(temp,path)
 envpath=p/'.env';env=envpath.read_text();env_name='HOMECOMPUTE_CHATGPT_API_KEY'
 lines=env.splitlines();matches=[i for i,line in enumerate(lines) if line.startswith(env_name+'=')]
 if len(matches)>1:raise ValueError('duplicate profile key environment variable')
 line=env_name+'='+spec['keys'][item['name']]
 if matches:lines[matches[0]]=line
 else:lines.append(line)
 content=('\n'.join(lines)+'\n').encode();temp=envpath.with_name('.env.homecompute-tmp')
 with temp.open('xb') as f:os.chmod(temp,0o600);f.write(content);f.flush();os.fsync(f.fileno())
 os.replace(temp,envpath)
 marker={'schema_version':1,'name':name,'model':item['model'],'endpoint':spec['endpoint']}
 markerpath=p/'.homecompute-chatgpt-profile.json';temp=markerpath.with_name('.profile.homecompute-tmp')
 with temp.open('xb') as f:os.chmod(temp,0o600);f.write(json.dumps(marker,sort_keys=True).encode());f.flush();os.fsync(f.fileno())
 os.replace(temp,markerpath)
print(json.dumps({'configured':[item['name'] for item in spec['profiles']]}))
'''


def roster() -> dict:
    value = json.loads(ROSTER.read_bytes())
    if (value.get('schema_version') != 1 or value.get('sandbox') != 'hermes-personal-v2'
            or value.get('endpoint') != 'http://ai.home.arpa:18080/v1'
            or value.get('owner_profile') != 'default' or len(value.get('profiles', [])) != 3):
        raise ValueError('Hermes ChatGPT profile roster refused')
    if [x.get('model') for x in value['profiles']] != [
            'chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest']:
        raise ValueError('Hermes ChatGPT alias order or identities changed')
    return value


def plan_digest(source_revision: str, desired: dict, observed: dict) -> str:
    payload = {'revision': source_revision, 'desired': desired, 'observed': observed}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reconcile(call, cli: list[str], desired: dict, keys: dict[str, str], operation: str,
              expected_plan_sha256: str | None = None, revision: str | None = None) -> dict:
    def inspect():
        result = json.loads(call(cli + ['exec', '--stdin', '--', PYTHON, '-c', INSPECT],
                                 json.dumps(desired).encode()))
        return result
    before = inspect()
    for item in desired['profiles']:
        state = before[item['name']]['state']
        if state not in ('absent', 'managed', 'repairable'):
            raise ValueError(f"existing profile {item['name']} differs; preserve it for review")
    if operation == 'plan':
        return before
    if operation != 'apply':
        raise ValueError('unknown operation')
    if not expected_plan_sha256 or not re.fullmatch(r'[0-9a-f]{64}', expected_plan_sha256):
        raise ValueError('reviewed --expected-plan-sha256 required')
    if not revision or plan_digest(revision, desired, before) != expected_plan_sha256:
        raise ValueError('Hermes profile state changed since the reviewed plan')
    for item in desired['profiles']:
        name=item['name'];state=inspect()[name]['state']
        if state == 'managed':
            continue
        if state == 'absent':
            call(cli + ['exec', '--', '/opt/hermes/.venv/bin/hermes', 'profile', 'create',
                        name, '--clone', '--description', item['display_name'] + ' owner profile'])
        payload={'endpoint':desired['endpoint'],'profiles':[item],'keys':{name:keys[name]}}
        call(cli + ['exec', '--stdin', '--', PYTHON, '-c', PATCH], json.dumps(payload).encode())
        if inspect()[name]['state'] != 'managed':
            raise ValueError(f"native Hermes readback differs for {name}")
    return inspect()


def private_key(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError('private Hermes ChatGPT key file required')
    info=path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
        raise ValueError('Hermes ChatGPT key file ownership or mode refused')
    key=path.read_text().strip()
    if not key.startswith('sk-') or any(c.isspace() for c in key):
        raise ValueError('Hermes ChatGPT key file content refused')
    return key


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--expected-plan-sha256')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    operation='apply' if args.apply else 'plan'
    setup=runpy.run_path(str(ROOT/'scripts/setup-hermes-mcp.py'))
    setup['published'](args.revision)
    if subprocess.run(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=all'],
                      capture_output=True,text=True,check=True).stdout.strip():
        raise ValueError('clean published release required')
    gates=runpy.run_path(str(ROOT/'scripts/connect-hermes-mcp.py'))['gates']
    if operation=='apply':gates()
    addons=__import__('runpy').run_path(str(ROOT/'scripts/setup-hermes-addons.py'))
    call=addons['call'];cli=['nemohermes',roster()['sandbox']]
    pin=__import__('json').loads((ROOT/'config/hermes-omh.json').read_bytes())
    status=json.loads(call(cli+['status','--json']))
    if status.get('agent')!='hermes' or status.get('phase')!='Ready' or status.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    container=call(['docker','ps','--filter','name=openshell-default--'+roster()['sandbox']+'-','--format','{{.ID}}']).decode().splitlines()
    if len(container)!=1 or call(['docker','inspect',container[0],'--format','{{.Image}}']).decode().strip()!=pin['runtime_image_id']:
        raise ValueError('qualified owner Hermes image required')
    desired=roster();observed=reconcile(call,cli,desired,{},'plan')
    digest=plan_digest(args.revision,desired,observed)
    if operation=='plan':
        print(json.dumps({'revision':args.revision,'operation':'plan','plan_sha256':digest,
                          'profiles':{k:v['state'] for k,v in observed.items()}},sort_keys=True));return 0
    keys={item['name']:private_key(KEY_DIR/(item['name'].removeprefix('chatgpt-')))
          for item in desired['profiles']}
    result=reconcile(call,cli,desired,keys,'apply',args.expected_plan_sha256,args.revision)
    print(json.dumps({'revision':args.revision,'operation':'apply','profiles':{k:v['state'] for k,v in result.items()}},sort_keys=True))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error,ValueError) else type(error).__name__,file=sys.stderr)
        print('Hermes ChatGPT profile state is unverified; inspect the read-only plan.',file=sys.stderr)
        raise SystemExit(2) from None
