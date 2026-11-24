#!/usr/bin/env python3
"""Create one model-only LiteLLM key for each selected owner Hermes profile."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import runpy
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / '.ssh/id_ed25519_ai-services-01'
GUEST = 'hermes-operator@10.77.20.2'
SECRETS = '/home/hermes-operator/.config/homecompute-hermes/secrets'
PROFILES = {
    'chatgpt-sol': 'chatgpt-sol-latest',
    'chatgpt-terra': 'chatgpt-terra-latest',
    'chatgpt-luna': 'chatgpt-luna-latest',
}
OWNER = 'hermes-chatgpt-profiles'
DESTINATIONS = {name: f'{SECRETS}/{name.removeprefix("chatgpt-")}' for name in PROFILES}

LITELLM_SCRIPT = r'''import hashlib,json,sys,urllib.parse,urllib.request
master=open('/run/secrets/litellm_master_key',encoding='utf-8').read().strip()
base='http://127.0.0.1:4000'
headers={'Authorization':'Bearer '+master,'Content-Type':'application/json'}
def req(path,body=None):
 data=json.dumps(body).encode() if body is not None else None
 with urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers=headers),timeout=30) as r:return json.load(r)
spec=json.load(sys.stdin)
created={}
try:
 for name,model in spec['profiles'].items():
  alias=spec['aliases'][name]
  listed=req('/key/list?'+urllib.parse.urlencode({'key_alias':alias,'size':1}))
  if listed.get('keys') != []:raise ValueError('existing Hermes key alias requires review')
  body={'models':[model],'key_alias':alias,'user_id':spec['owner'],'duration':None,
        'object_permission':{'mcp_servers':['no-mcp-servers']},
        'metadata':{'purpose':'Owner Hermes profile; one model alias only; MCP denied'}}
  key=req('/key/generate',body).get('key')
  if not isinstance(key,str) or not key.startswith('sk-') or any(c.isspace() for c in key):raise ValueError('key shape')
  created[name]=key
  digest=hashlib.sha256(key.encode()).hexdigest()
  info=req('/key/info?'+urllib.parse.urlencode({'key':digest})).get('info',{})
  if (info.get('models')!=[model] or info.get('user_id')!=spec['owner'] or info.get('key_alias')!=alias
      or info.get('expires') is not None
      or (info.get('object_permission') or {}).get('mcp_servers')!=['no-mcp-servers']):raise ValueError('key scope')
except Exception:
 if created:req('/key/delete',{'keys':list(created.values())})
 raise
print(json.dumps(created))
'''

RECEIVER = r'''import os,pathlib,stat,sys
p=pathlib.Path(sys.argv[1]);raw=sys.stdin.buffer.read(4097)
assert 4<=len(raw)<=4096 and raw.endswith(b'\n') and raw.count(b'\n')==1
key=raw[:-1];assert key.startswith(b'sk-') and not any(chr(c).isspace() for c in key)
assert not any(x.is_symlink() for x in (p,*p.parents))
p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
i=p.parent.stat();assert stat.S_ISDIR(i.st_mode) and i.st_uid==os.getuid() and stat.S_IMODE(i.st_mode)==0o700
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:os.write(fd,key+b'\n');os.fsync(fd)
finally:os.close(fd)
i=p.lstat();assert stat.S_ISREG(i.st_mode) and i.st_uid==os.getuid() and i.st_nlink==1 and stat.S_IMODE(i.st_mode)==0o600
assert p.read_bytes()==key+b'\n';print('key-installed')
'''

REMOVE = r'''import os,pathlib,sys
p=pathlib.Path(sys.argv[1]);raw=sys.stdin.buffer.read(4097)
assert raw.endswith(b'\n') and raw.count(b'\n')==1
key=raw[:-1];assert key.startswith(b'sk-') and not any(chr(c).isspace() for c in key)
if not p.exists():print('key-absent');raise SystemExit(0)
assert p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid() and p.stat().st_mode&0o077==0
assert p.read_bytes()==key+b'\n'
p.unlink();print('key-removed')
'''

REVOKE = r'''import json,sys,urllib.request
keys=json.loads(sys.stdin.read())
master=open('/run/secrets/litellm_master_key',encoding='utf-8').read().strip()
request=urllib.request.Request('http://127.0.0.1:4000/key/delete',data=json.dumps({'keys':keys}).encode(),
 headers={'Authorization':'Bearer '+master,'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=30) as response:response.read()
print('revoked')
'''


def revoke(keys: dict[str, str]) -> bool:
    payload = json.dumps(list(keys.values()))
    result = subprocess.run(['ssh','home-core','sudo','-n','docker','exec','-i',
                            'homecompute-control-plane-litellm-1','python3','-'],
                           input=REVOKE, text=True, capture_output=True, timeout=45, check=False)
    if result.returncode:
        return False
    # Ensure revocation actually reached the exact scoped keys without exposing them.
    return result.stdout.strip() == 'revoked'

def selection(desktop_bots: bool):
    if not desktop_bots:
        return PROFILES, DESTINATIONS, OWNER
    roster = runpy.run_path(str(ROOT / 'scripts/setup-hermes-desktop-bots.py'))['desired']()
    profiles = {bot['id']: bot['model'] for bot in roster['bots']}
    destinations = {name: f'{SECRETS}/desktop-{name}' for name in profiles}
    return profiles, destinations, 'hermes-desktop-bots'


def run(desktop_bots: bool = False) -> None:
    profiles, destinations, owner = selection(desktop_bots)
    aliases = {name: ('hermes-desktop-' if desktop_bots else 'hermes-') + name for name in profiles}
    if not GUEST_KEY.is_file() or GUEST_KEY.is_symlink():
        raise RuntimeError('The dedicated agents-VM SSH identity is unavailable')
    rev = subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    pub = subprocess.run(['git','-C',str(ROOT),'rev-parse','origin/master'],capture_output=True,text=True,check=True).stdout.strip()
    dirty = subprocess.run(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=all'],capture_output=True,text=True,check=True).stdout
    if rev != pub or dirty:
        raise RuntimeError('Use a clean checkout of the exact published origin/master revision')
    check = 'set -eu; ' + ' && '.join(f'test ! -e {p} && test ! -L {p}' for p in destinations.values())
    result = subprocess.run(['ssh','-J','home-core','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                             '-i',str(GUEST_KEY),GUEST,check],capture_output=True,text=True,check=False)
    if result.returncode:
        raise RuntimeError('Preserve existing Hermes key files; inspect their state')
    command = shlex.join(['sudo', '-n', 'docker', 'exec', '-i',
                          'homecompute-control-plane-litellm-1', 'python3', '-c', LITELLM_SCRIPT])
    host = subprocess.run(['ssh','home-core', command],
                          input=json.dumps({'profiles': profiles, 'aliases': aliases, 'owner': owner}),
                          text=True, capture_output=True, timeout=150, check=False)
    if host.returncode:
        raise RuntimeError('LiteLLM key creation failed; no credential was delivered')
    try: keys=json.loads(host.stdout)
    except (ValueError,TypeError): raise RuntimeError('LiteLLM returned an unreadable key receipt') from None
    if not isinstance(keys,dict) or set(keys)!=set(profiles):
        raise RuntimeError('LiteLLM returned an unexpected profile set')
    delivered_paths = []
    try:
        for name,path in destinations.items():
            key=keys[name]
            if not isinstance(key,str) or not key.startswith('sk-') or any(c.isspace() for c in key):
                raise RuntimeError('LiteLLM returned an invalid key')
            command='python3 -c '+shlex.quote(RECEIVER)+' '+shlex.quote(path)
            delivered=subprocess.run(['ssh','-J','home-core','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                                      '-i',str(GUEST_KEY),GUEST,command],input=key+'\n',text=True,
                                     capture_output=True,timeout=30,check=False)
            if delivered.returncode or delivered.stdout.strip()!='key-installed':
                raise RuntimeError(f'Guest rejected the {name} key installation')
            delivered_paths.append((name, path, key))
    except (OSError,subprocess.SubprocessError,RuntimeError):
        cleanup_ok = True
        for name,path,key in reversed(delivered_paths):
            command='python3 -c '+shlex.quote(REMOVE)+' '+shlex.quote(path)
            removed=subprocess.run(['ssh','-J','home-core','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                                    '-i',str(GUEST_KEY),GUEST,command],input=key+'\n',text=True,
                                   capture_output=True,timeout=30,check=False)
            cleanup_ok &= removed.returncode == 0 and removed.stdout.strip() == 'key-removed'
        cleanup_ok &= revoke(keys)
        if not cleanup_ok:
            raise RuntimeError('Partial Hermes key delivery could not be fully rolled back; inspect guest and LiteLLM state')
        raise RuntimeError('Hermes key delivery failed; generated keys were removed and revoked') from None
    print(f'Created {len(profiles)} non-expiring one-model Hermes keys with MCP access denied.')

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-bots', action='store_true')
    args = parser.parse_args()
    try:run(args.desktop_bots)
    except (OSError,subprocess.SubprocessError,RuntimeError) as exc:
        print(f'Hermes key provisioning failed ({type(exc).__name__})',file=sys.stderr)
        raise SystemExit(1) from None
