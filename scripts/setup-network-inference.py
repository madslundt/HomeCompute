#!/usr/bin/env python3
"""Provision the restricted gateway credential without exposing it to clients."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess


KEY_FILE = Path('/srv/state/control-plane/caddy-data/network-inference-key')
TEXT_MODELS = ['automation', 'home', 'automation-moe']
STT_MODELS = [*TEXT_MODELS, 'stt-danish']
MODELS = [*STT_MODELS, 'whisper-turbo']
TTS_MODELS = [*MODELS, 'plapre']
CONTAINER = 'homecompute-control-plane-litellm-1'


def inspect_existing_key(key):
    code = '''
import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key').read().strip()
query = urllib.parse.urlencode({'key': %s})
req = urllib.request.Request('http://127.0.0.1:4000/key/info?' + query,
    headers={'Authorization': 'Bearer ' + master})
info = json.load(urllib.request.urlopen(req, timeout=30))['info']
print(json.dumps({k: info.get(k) for k in ('models', 'expires', 'user_id')}))
''' % repr(key)
    # Pass credential-bearing source through stdin, never process arguments.
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python3', '-'],
                            input=code, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Existing network credential is invalid in LiteLLM; reconcile private key/database state.')
    return json.loads(result.stdout)


def provision(enable_stt=False, enable_whisper=False, enable_plapre=False):
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    if sum((enable_stt, enable_whisper, enable_plapre)) > 1:
        raise SystemExit('Migrate one model-scope change at a time.')
    if KEY_FILE.exists() or KEY_FILE.is_symlink():
        info = KEY_FILE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise SystemExit('Network inference credential must be a root-owned regular file, mode 0600.')
        if not KEY_FILE.read_text().strip():
            raise SystemExit('Network inference credential is empty.')
        key = KEY_FILE.read_text().strip()
        data = inspect_existing_key(key)
        if data['expires'] is not None or data['user_id'] != 'network-inference':
            raise SystemExit('Existing network credential has an unexpected scope, owner, or expiry.')
        scope = set(data['models'] or [])
        target = None
        if enable_stt and scope == set(TEXT_MODELS):
            target = STT_MODELS
        elif enable_whisper and scope == set(STT_MODELS):
            target = MODELS
        elif enable_plapre and scope == set(MODELS):
            target = TTS_MODELS
        if target is not None:
            code = '''
import json, urllib.request
master = open('/run/secrets/litellm_master_key').read().strip()
body = {'key': %s, 'models': %s}
req = urllib.request.Request('http://127.0.0.1:4000/key/update',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).close()
''' % (repr(key), repr(target))
            result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python3', '-'],
                                    input=code, capture_output=True, text=True)
            if result.returncode:
                raise SystemExit('Network speech credential migration failed.')
            updated = inspect_existing_key(key)
            if updated['expires'] is not None or updated['user_id'] != 'network-inference' or \
                    set(updated['models'] or []) != set(target):
                raise SystemExit('Network credential migration did not apply the reviewed scope, owner, and expiry.')
            print('Migrated network inference credential for: ' + ', '.join(target))
            return
        if scope == set(STT_MODELS) and enable_stt:
            print('Network STT credential already provisioned.')
            return
        if scope != set(MODELS):
            if scope != set(TTS_MODELS):
                raise SystemExit('Existing network credential has an unexpected scope; migrate text-only, STT, and Plapre scopes one at a time with --enable-stt, --enable-whisper, and --enable-plapre.')
        if scope == set(MODELS) and enable_plapre:
            raise SystemExit('Plapre scope migration did not apply; inspect the LiteLLM key state.')
        print('Network inference credential already provisioned.')
        return
    # The master key stays inside LiteLLM. Capture the new virtual key directly
    # into a private file; never print credentials in deployment output.
    code = '''
import json, urllib.request
master = open('/run/secrets/litellm_master_key').read().strip()
body = {'models': %s, 'key_alias': 'network-inference',
        'user_id': 'network-inference', 'duration': None,
        'metadata': {'purpose': 'keyless private-network inference'}}
req = urllib.request.Request('http://127.0.0.1:4000/key/generate',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
result = json.load(urllib.request.urlopen(req, timeout=30))
print(json.dumps({'key': result['key'], 'models': result['models'], 'expires': result.get('expires')}))
''' % repr(TTS_MODELS if enable_plapre else MODELS)
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python3', '-c', code],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('LiteLLM network credential provisioning failed; inspect the internal service.')
    data = json.loads(result.stdout)
    expected_models = TTS_MODELS if enable_plapre else MODELS
    if set(data['models']) != set(expected_models) or data['expires'] is not None or not data['key'].startswith('sk-'):
        raise SystemExit('LiteLLM returned an unexpected network credential scope or expiry.')
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(data['key'] + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    print('Provisioned network inference credential for: ' + ', '.join(expected_models))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-stt', action='store_true', help='Migrate the reviewed text-only network key to include stt-danish.')
    parser.add_argument('--enable-whisper', action='store_true', help='Migrate the reviewed text and Danish STT network key to include whisper-turbo.')
    parser.add_argument('--enable-plapre', action='store_true', help='Migrate the reviewed network key to include candidate Plapre TTS.')
    args = parser.parse_args()
    provision(enable_stt=args.enable_stt, enable_whisper=args.enable_whisper,
              enable_plapre=args.enable_plapre)
