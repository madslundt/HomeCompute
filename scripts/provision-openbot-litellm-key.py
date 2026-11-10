#!/usr/bin/env python3
"""Create and install an OpenBot-only LiteLLM key without displaying it."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / '.ssh/id_ed25519_ai-services-01'
GUEST = 'hermes-operator@10.77.20.2'
MODEL = 'automation-moe'
OWNER = 'openbot-agent'
DESTINATION = '/home/hermes-operator/.config/homecompute-openbot/secrets/litellm-api-key'

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
body = {'models': ['automation-moe'], 'key_alias': 'openbot-agent',
        'user_id': 'openbot-agent', 'duration': None,
        'object_permission': {'mcp_servers': ['no-mcp-servers']},
        'metadata': {'purpose': 'OpenBot local model provider; automation-moe only'}}
request = urllib.request.Request('http://127.0.0.1:4000/key/generate',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
result = json.load(urllib.request.urlopen(request, timeout=30))
key = result.get('key')
query = urllib.parse.urlencode({'key': key})
check = urllib.request.Request('http://127.0.0.1:4000/key/info?' + query,
    headers={'Authorization': 'Bearer ' + master})
info = json.load(urllib.request.urlopen(check, timeout=30)).get('info', {})
print(json.dumps({'key': key, 'models': info.get('models'),
                  'user_id': info.get('user_id'), 'expires': info.get('expires')}))
'''


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
         f'sudo -n systemctl --quiet is-active homecompute-openbot.service && test ! -e {DESTINATION} && test ! -L {DESTINATION}'],
        capture_output=True, text=True, check=False,
    )
    if existing.returncode:
        raise RuntimeError('An OpenBot LiteLLM key already exists; preserve it and inspect its scope')
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
    key = value.get('key')
    if (not isinstance(key, str) or not key.startswith('sk-') or any(ch.isspace() for ch in key)
            or value.get('models') != [MODEL] or value.get('user_id') != OWNER
            or value.get('expires') is not None):
        raise RuntimeError('LiteLLM returned a key with an unexpected scope')

    receiver = (
        'set -eu; install -d -m 0700 ~/.config/homecompute-openbot/secrets; '
        f'test ! -e {DESTINATION} && test ! -L {DESTINATION}; '
        f'umask 077; cat > {DESTINATION}; chmod 0600 {DESTINATION}; '
        f'test -f {DESTINATION} && test ! -L {DESTINATION} && '
        f'test "$(stat -c %a {DESTINATION})" = 600 && '
        f'test "$(wc -l < {DESTINATION})" = 1 && echo key-installed'
    )
    guest = subprocess.run(
        ['ssh', '-J', 'home-core', '-o', 'BatchMode=yes', '-o',
         'StrictHostKeyChecking=yes', '-i', str(GUEST_KEY), GUEST, receiver],
        input=key + '\n', text=True, capture_output=True, timeout=30, check=False,
    )
    if guest.returncode or guest.stdout.strip() != 'key-installed':
        raise RuntimeError('Guest key installation failed; inspect protected guest state')
    print('Created and installed a non-expiring LiteLLM key scoped only to automation-moe.')


if __name__ == '__main__':
    try:
        run()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f'OpenBot key provisioning failed ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(1) from None
