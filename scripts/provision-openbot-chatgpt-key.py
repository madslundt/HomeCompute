#!/usr/bin/env python3
"""Create a model-only ChatGPT-alias key and stream it into the OpenBot guest.

Run from the operator workstation using a clean exact published source checkout.
The LiteLLM master key stays inside LiteLLM; the generated key is held only in
process memory and never printed or placed in an argument.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / '.ssh/id_ed25519_ai-services-01'
GUEST = 'hermes-operator@10.77.20.2'
MODELS = ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest']
OWNER = 'openbot-chatgpt-models'
DESTINATION = '/home/hermes-operator/.config/homecompute-openbot/secrets/chatgpt-models-key'

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
models = ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest']
body = {'models': models, 'key_alias': 'openbot-chatgpt-models',
        'user_id': 'openbot-chatgpt-models', 'duration': None,
        'object_permission': {'mcp_servers': ['no-mcp-servers']},
        'metadata': {'purpose': 'OpenBot ChatGPT aliases only; no MCP access'}}
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
                  'user_id': info.get('user_id'), 'expires': info.get('expires'),
                  'mcp_servers': info.get('mcp_servers')}))
'''


def valid_receipt(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    key = value.get('key')
    return (
        isinstance(key, str) and key.startswith('sk-') and not any(ch.isspace() for ch in key)
        and value.get('models') == MODELS and value.get('user_id') == OWNER
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
    print('Created and installed a non-expiring LiteLLM key for the three ChatGPT aliases, with MCP access denied.')


if __name__ == '__main__':
    try:
        run()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f'OpenBot ChatGPT key provisioning failed ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(1) from None
