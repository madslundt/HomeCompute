#!/usr/bin/env python3
"""Create a maintenance-only LiteLLM key for the isolated OpenBot operator server."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / '.ssh/id_ed25519_ai-services-01'
GUEST = 'hermes-operator@10.77.20.2'
DESTINATION = '/home/hermes-operator/.config/homecompute-openbot-operator/secrets/litellm-api-key'
MODEL = 'automation-moe'
GROUP = 'openbot-maintenance'
SERVER = 'homecompute_openbot_maintenance'
OWNER = 'openbot-maintenance'

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
def api(path, body):
    request = urllib.request.Request('http://127.0.0.1:4000' + path,
        data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
body = {'models': ['automation-moe'], 'key_alias': 'openbot-maintenance',
        'user_id': 'openbot-maintenance', 'duration': None,
        'object_permission': {'mcp_access_groups': ['openbot-maintenance'],
                              'mcp_servers': ['homecompute_openbot_maintenance']},
        'metadata': {'purpose': 'Isolated OpenBot operator; automation-moe and maintenance-only MCP'}}
result = api('/key/generate', body)
key = result.get('key')
try:
    if not isinstance(key, str) or not key.startswith('sk-') or any(c.isspace() for c in key):
        raise RuntimeError('LiteLLM returned an invalid key')
    query = urllib.parse.urlencode({'key': key})
    check = urllib.request.Request('http://127.0.0.1:4000/key/info?' + query,
        headers={'Authorization': 'Bearer ' + master})
    info = json.load(urllib.request.urlopen(check, timeout=30)).get('info', {})
    permission = info.get('object_permission') or {}
    groups = permission.get('mcp_access_groups') if isinstance(permission, dict) else None
    servers = permission.get('mcp_servers') if isinstance(permission, dict) else None
    if (groups != ['openbot-maintenance'] or servers != ['homecompute_openbot_maintenance']
            or info.get('models') != ['automation-moe'] or info.get('user_id') != 'openbot-maintenance'
            or info.get('expires') is not None):
        raise RuntimeError('OpenBot maintenance key MCP scope did not verify')
except BaseException:
    if isinstance(key, str):
        api('/key/delete', {'keys': [key]})
    raise
print(json.dumps({'key': key, 'models': info.get('models'), 'user_id': info.get('user_id'),
                  'expires': info.get('expires'), 'mcp_access_groups': groups,
                  'mcp_servers': servers}))
'''

REVOKE_SCRIPT = r'''import json,urllib.request
master=open('/run/secrets/litellm_master_key',encoding='utf-8').read().strip()
key=%r
req=urllib.request.Request('http://127.0.0.1:4000/key/delete',data=json.dumps({'keys':[key]}).encode(),
 headers={'Authorization':'Bearer '+master,'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as response:response.read()
print('revoked')
'''

CLEAN_GUEST_SCRIPT = r'''import json,os,pathlib,sys
p=pathlib.Path('/home/hermes-operator/.config/homecompute-openbot-operator/secrets/litellm-api-key')
key=json.loads(sys.stdin.readline())['key'].encode()+b'\n'
if not p.exists():print('absent');raise SystemExit(0)
assert key.endswith(b'\n') and key.count(b'\n')==1
assert p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid()
assert p.stat().st_mode&0o077==0 and p.read_bytes()==key
p.unlink();print('removed')
'''


def revoke_key(key: str) -> bool:
    result = subprocess.run(
        ['ssh', 'home-core', 'sudo -n docker exec -i homecompute-control-plane-litellm-1 python3 -'],
        input=REVOKE_SCRIPT % key, text=True, capture_output=True, timeout=45, check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == 'revoked'


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
    deployed = subprocess.run(['ssh', 'home-core', 'sudo -n cat /var/lib/homecompute/deployed-revision'],
                              capture_output=True, text=True, check=True).stdout.strip()
    if deployed != revision:
        raise RuntimeError('Home Core must be deployed from this exact published revision first')
    existing = subprocess.run(
        ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
         '-i', str(GUEST_KEY), GUEST,
         f'sudo -n systemctl --quiet is-active homecompute-openbot.service && '
         f'sudo -n docker inspect --format "{{{{.State.Running}}}}" homecompute-openbot-operator | grep -qx true && '
         f'test ! -e {DESTINATION} && test ! -L {DESTINATION}'],
        capture_output=True, text=True, check=False,
    )
    if existing.returncode:
        raise RuntimeError('The isolated OpenBot operator server must be active and its key path unused')
    host = subprocess.run(
        ['ssh', 'home-core', 'sudo -n docker exec -i homecompute-control-plane-litellm-1 python3 -'],
        input=LITELLM_SCRIPT, text=True, capture_output=True, timeout=45, check=False,
    )
    if host.returncode:
        raise RuntimeError('LiteLLM key creation failed; inspect and revoke any partially created key')
    try:
        value = json.loads(host.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('LiteLLM returned an unreadable key receipt') from None
    key = value.get('key')
    if (not isinstance(key, str) or not key.startswith('sk-') or any(ch.isspace() for ch in key)
            or value.get('models') != [MODEL] or value.get('user_id') != OWNER
            or value.get('expires') is not None or value.get('mcp_access_groups') != [GROUP]
            or value.get('mcp_servers') != [SERVER]):
        revoked = (revoke_key(key) if isinstance(key, str) and key.startswith('sk-')
                   and not any(ch.isspace() for ch in key) else False)
        if not revoked:
            raise RuntimeError('LiteLLM key scope failed verification; revoke the returned key manually')
        raise RuntimeError('LiteLLM returned a key with an unexpected scope; key was revoked')
    receiver = (
        'set -eu; install -d -m 0700 ~/.config/homecompute-openbot-operator/secrets; '
        f'test ! -e {DESTINATION} && test ! -L {DESTINATION}; '
        f'umask 077; cat > {DESTINATION}; chmod 0600 {DESTINATION}; '
        f'test -f {DESTINATION} && test ! -L {DESTINATION} && '
        f'test "$(stat -c %a {DESTINATION})" = 600 && '
        f'test "$(wc -l < {DESTINATION})" = 1 && echo key-installed'
    )
    guest = subprocess.run(
        ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
         '-i', str(GUEST_KEY), GUEST, receiver], input=key + '\n', text=True,
        capture_output=True, timeout=30, check=False,
    )
    if guest.returncode or guest.stdout.strip() != 'key-installed':
        subprocess.run(
            ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
             '-i', str(GUEST_KEY), GUEST, 'python3 -'],
            input=CLEAN_GUEST_SCRIPT + '\n' + json.dumps({'key': key}) + '\n',
            text=True, capture_output=True, timeout=30, check=False,
        )
        revoked = revoke_key(key)
        if not revoked:
            raise RuntimeError('Guest key installation failed and LiteLLM key revocation needs operator review')
        raise RuntimeError('Guest key installation failed; inspect protected guest state')
    print('Created and installed a non-expiring LiteLLM key scoped to automation-moe and the OpenBot maintenance MCP server.')


if __name__ == '__main__':
    try:
        run()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f'OpenBot maintenance key provisioning failed ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(1) from None
