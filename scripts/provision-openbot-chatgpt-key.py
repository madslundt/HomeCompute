#!/usr/bin/env python3
"""Create a model-only ChatGPT-alias key and stream it into the OpenBot guest.

Run from the operator workstation using a clean exact published source checkout.
The LiteLLM master key stays inside LiteLLM; the generated key is held only in
process memory and never printed or placed in an argument.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / '.ssh/id_ed25519_ai-services-01'
GUEST = 'hermes-operator@10.77.20.2'
MODELS = ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest']
OWNER = 'openbot-chatgpt-models'
DESTINATION = '/home/hermes-operator/.config/homecompute-openbot/secrets/chatgpt-models-key'
GUEST_RECEIVER = r'''import os,pathlib,stat,sys
p=pathlib.Path('/home/hermes-operator/.config/homecompute-openbot/secrets/chatgpt-models-key')
raw=sys.stdin.buffer.read(4097)
assert 4 <= len(raw) <= 4096 and raw.endswith(b'\n') and raw.count(b'\n') == 1
key=raw[:-1]
assert key.startswith(b'sk-') and not any(chr(c).isspace() for c in key)
assert not any(part.is_symlink() for part in (p,*p.parents))
p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
info=p.parent.stat()
assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 os.write(fd,key+b'\n');os.fsync(fd)
finally:os.close(fd)
info=p.lstat()
assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
assert p.read_bytes() == key+b'\n'
print('key-installed')
'''

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
models = ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest']
base = 'http://127.0.0.1:4000'
headers = {'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'}
def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response: return json.load(response)
listed = request('/key/list')
aliases = set()
for digest in listed.get('keys', []):
    info = request('/key/info?' + urllib.parse.urlencode({'key': digest})).get('info', {})
    if info.get('key_alias'): aliases.add(info['key_alias'])
alias = 'openbot-chatgpt-models'
suffix = 2
while alias in aliases:
    alias = 'openbot-chatgpt-models-' + str(suffix); suffix += 1
body = {'models': models, 'key_alias': alias,
        'user_id': 'openbot-chatgpt-models', 'duration': None,
        'object_permission': {'mcp_servers': ['no-mcp-servers']},
        'metadata': {'purpose': 'OpenBot ChatGPT aliases only; no MCP access'}}
result = request('/key/generate', body)
key = result.get('key')
info = request('/key/info?' + urllib.parse.urlencode({'key': key})).get('info', {})
permissions = info.get('object_permission') or {}
receipt = {'key': key, 'key_alias': info.get('key_alias'), 'models': info.get('models'),
                  'user_id': info.get('user_id'), 'expires': info.get('expires'),
                  'mcp_servers': permissions.get('mcp_servers')}
if (receipt['models'] != models or receipt['user_id'] != 'openbot-chatgpt-models'
    or receipt['expires'] is not None or receipt['mcp_servers'] != ['no-mcp-servers']):
    request('/key/delete', {'keys': [key]})
    raise ValueError('generated key scope did not verify')
print(json.dumps(receipt))
'''

REVOKE_SCRIPT = r'''import json,sys,urllib.request
key=sys.stdin.read().strip();assert key.startswith('sk-') and not any(c.isspace() for c in key)
master=open('/run/secrets/litellm_master_key',encoding='utf-8').read().strip()
req=urllib.request.Request('http://127.0.0.1:4000/key/delete',data=json.dumps({'keys':[key]}).encode(),
 headers={'Authorization':'Bearer '+master,'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as response:response.read()
print('revoked')
'''

REMOVE_GUEST = r'''import os,pathlib,sys
p=pathlib.Path('/home/hermes-operator/.config/homecompute-openbot/secrets/chatgpt-models-key')
key=sys.stdin.buffer.read(4097);assert key.endswith(b'\n') and key.count(b'\n')==1
if not p.exists():print('absent');raise SystemExit(0)
assert p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid()
assert p.stat().st_mode&0o077==0 and p.read_bytes()==key
p.unlink();print('removed')
'''


def valid_receipt(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    key = value.get('key')
    alias = value.get('key_alias')
    return (
        isinstance(key, str) and key.startswith('sk-') and not any(ch.isspace() for ch in key)
        and value.get('models') == MODELS and value.get('user_id') == OWNER
        and isinstance(alias, str) and re.fullmatch(r'openbot-chatgpt-models(?:-(?:[2-9]|[1-9][0-9]+))?', alias)
        and value.get('expires') is None and value.get('mcp_servers') == ['no-mcp-servers']
    )


def run() -> None:
    if not GUEST_KEY.is_file() or GUEST_KEY.is_symlink():
        raise RuntimeError('The dedicated agents-VM SSH identity is unavailable')
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True).stdout.strip()
    published = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'origin/master'],
                               capture_output=True, text=True, check=True).stdout.strip()
    porcelain = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all'],
                               capture_output=True, text=True, check=True).stdout
    if revision != published or porcelain:
        raise RuntimeError('Use a clean checkout of the exact published origin/master revision')
    existing = subprocess.run(
        ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
         '-i', str(GUEST_KEY), GUEST,
         f'sudo -n systemctl --quiet is-active homecompute-openbot.service && test ! -e {DESTINATION} && test ! -L {DESTINATION}'],
        capture_output=True, text=True, check=False,
    )
    if existing.returncode:
        raise RuntimeError('OpenBot must be active and the key path must be absent; preserve existing state')

    host = subprocess.run(
        ['ssh', 'home-core', 'sudo -n docker exec -i homecompute-control-plane-litellm-1 python3 -'],
        input=LITELLM_SCRIPT, text=True, capture_output=True, timeout=45, check=False,
    )
    if host.returncode:
        raise RuntimeError('LiteLLM key creation failed; no credential was delivered')
    try:
        value = json.loads(host.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('LiteLLM returned an unreadable key receipt') from None
    if not valid_receipt(value):
        raise RuntimeError('LiteLLM returned a key with an unexpected scope')
    key = value['key']

    receiver = 'python3 -c ' + shlex.quote(GUEST_RECEIVER)
    guest = subprocess.run(
        ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o',
         'StrictHostKeyChecking=yes', '-i', str(GUEST_KEY), GUEST, receiver],
        input=key + '\n', text=True, capture_output=True, timeout=30, check=False,
    )
    if guest.returncode or guest.stdout.strip() != 'key-installed':
        remove = subprocess.run(
            ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
             '-i', str(GUEST_KEY), GUEST, 'python3 -c ' + shlex.quote(REMOVE_GUEST)],
            input=key + '\n', text=True, capture_output=True, timeout=30, check=False)
        revoked = subprocess.run(
            ['ssh', 'home-core', 'sudo', '-n', 'docker', 'exec', '-i',
             'homecompute-control-plane-litellm-1', 'python3', '-'],
            input=REVOKE_SCRIPT, text=True, capture_output=True, timeout=30, check=False)
        if (remove.returncode or remove.stdout.strip() not in ('removed', 'absent')
                or revoked.returncode or revoked.stdout.strip() != 'revoked'):
            raise RuntimeError('Delivery failed and credential rollback is unverified; inspect protected state')
        raise RuntimeError('Guest key installation failed; generated key was removed and revoked')
    print('Created and installed a non-expiring LiteLLM key for the three ChatGPT aliases, with MCP access denied.')


if __name__ == '__main__':
    try:
        run()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f'OpenBot ChatGPT key provisioning failed ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(1) from None
