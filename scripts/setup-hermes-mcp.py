#!/usr/bin/env python3
"""Admit the Hermes owner to the existing private bridge; default prints a plan."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute-hermes-mcp')
ADAPTER = 'homecompute-openclaw-adapter.service'
FILES = {
    'scripts/openclaw-communication.py': (
        Path('/opt/homecompute/openclaw-communication/scripts/openclaw-communication.py'),
        'ec1645627758027ba8dd9ae9673445827bdecc7288ef51250bdd6a2d3cacd194'),
    'scripts/openclaw_capabilities.py': (
        Path('/opt/homecompute/openclaw-communication/scripts/openclaw_capabilities.py'),
        'cd270c88a5adb8a901995d293f862abf29883bede90bfde285da8e29d269337e'),
    'scripts/setup-native-capabilities.py': (
        Path('/opt/homecompute/native-capabilities/setup-native-capabilities.py'),
        'ccaf6a57bf6b6839f37fd8eed5d32f58d71c52049450708ee167d5346a8e1998'),
    'deploy/codex-worker/mcp_adapter.py': (
        Path('/opt/homecompute/openclaw-communication/deploy/codex-worker/mcp_adapter.py'),
        '02c1ffbc3a3e835fdc2a81a0e54b2e45d8857abd955c588709eeadbd3e4f27ec'),
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def call(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('fixed Hermes bridge operation failed; inspect private state')
    return result.stdout.strip()


def adapter_state() -> str:
    result = subprocess.run(['systemctl', 'is-active', ADAPTER], capture_output=True,
                            text=True, timeout=30)
    state = result.stdout.strip()
    if state not in {'active', 'inactive', 'failed', 'activating'}:
        raise ValueError('adapter state is not recognized')
    if state == 'active' and result.returncode != 0:
        raise ValueError('adapter state could not be verified')
    return state


def native():
    spec = importlib.util.spec_from_file_location('native_setup', ROOT / 'scripts/setup-native-capabilities.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def published(revision: str) -> None:
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('full published revision required')
    if call(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']) != revision:
        raise ValueError('selected checkout revision differs')
    call(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    call(['git', '-C', str(ROOT), 'diff', '--exit-code', 'HEAD', '--', *FILES,
          'scripts/setup-hermes-mcp.py', 'scripts/connect-hermes-mcp.py',
          'scripts/openclaw_capabilities.py',
          'deploy/hermes/skills/homecompute-household/SKILL.md',
          'deploy/hermes/skills/android-gateway/SKILL.md'])


def plan(module, revision: str) -> dict:
    module.regular(module.TOKENS, module.pwd.getpwnam('homecompute-openclaw').pw_uid, 0o600)
    files = {}
    for name, (target, previous) in FILES.items():
        module.regular(target, 0)
        before, after = digest(target.read_bytes()), digest((ROOT / name).read_bytes())
        if before not in {previous, after}:
            raise ValueError('installed source differs from the reviewed preimage; preserve and reconcile drift')
        files[name] = {'before': before, 'after': after}
    return {'revision': revision, 'files': files, 'credential_file_sha256': digest(module.TOKENS.read_bytes())}


def tools_for(token: str) -> list[dict]:
    connection = http.client.HTTPConnection('127.0.0.1', 18793, timeout=15)
    try:
        # A Type=simple unit is active before Python has imported its facades and
        # bound the listener. Wait for the socket, without retrying an RPC verdict.
        for attempt in range(50):
            try:
                connection.connect()
                break
            except ConnectionRefusedError:
                connection.close()
                if attempt == 49:
                    raise
                time.sleep(0.1)
        connection.request('POST', '/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}),
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                            'Accept': 'application/json, text/event-stream'})
        response = connection.getresponse()
        raw = response.read(262145)
        if response.status != 200 or len(raw) > 262144:
            raise ValueError('Hermes bridge authentication failed')
        tools = json.loads(raw)['result']['tools']
        if not isinstance(tools, list):
            raise ValueError('MCP tool list is invalid')
        return tools
    finally:
        connection.close()


def probe(module) -> None:
    tokens = json.loads(module.TOKENS.read_bytes())
    tools = tools_for(tokens['hermes-owner'])
    names = {row['name'] for row in tools}
    if not {'homecompute_school_discover', 'homecompute_shopping_search', 'homecompute_system_read',
            'homecompute_ha_propose'} <= names:
        raise ValueError('required household services unavailable')
    if not {'homecompute_android_status', 'homecompute_android_screen', 'homecompute_android_apps',
            'homecompute_android_visible_text', 'homecompute_android_screenshot'} <= names:
        raise ValueError('read-only Android MCP tools unavailable')
    if names & {'homecompute_ha_execute', 'homecompute_ha_approve', 'homecompute_maintenance_run'}:
        raise ValueError('unexpected privileged Hermes tool')
    openbot_tools = tools_for(tokens['openbot-android'])
    expected_android = {'homecompute_android_status', 'homecompute_android_screen', 'homecompute_android_apps',
                        'homecompute_android_visible_text', 'homecompute_android_screenshot'}
    if {row.get('name') for row in openbot_tools} != expected_android:
        raise ValueError('OpenBot Android principal is not restricted to the five read-only tools')
    if any(not row.get('annotations', {}).get('readOnlyHint') for row in openbot_tools):
        raise ValueError('OpenBot Android principal exposes a non-read-only tool')


def apply(module, reviewed: dict, expected: str) -> dict:
    if digest(json.dumps(reviewed, sort_keys=True).encode()) != expected:
        raise ValueError('reviewed plan changed')
    module.directory(STATE, 0o700)
    receipt_path = STATE / 'receipt.json'
    receipt = None
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        if receipt['state'] == 'applied':
            if any(row['before'] != row['after'] for row in reviewed['files'].values()):
                raise ValueError('existing installation needs reconciliation before replacement')
            probe(module)
            return receipt
    state = adapter_state()
    if state != 'active' and not (receipt and receipt.get('state') == 'rolled-back'
                                  and state in {'inactive', 'failed', 'activating'}):
        raise ValueError('existing adapter must be active outside guarded rollback recovery')
    backup = STATE / str(time.time_ns())
    module.directory(backup, 0o700)
    paths = {name: target for name, (target, _) in FILES.items()}
    paths['tokens'] = module.TOKENS
    metadata = {}
    for index, (name, target) in enumerate(paths.items()):
        info = target.stat()
        metadata[name] = {'backup': str(index), 'uid': info.st_uid, 'gid': info.st_gid,
                          'mode': info.st_mode & 0o777}
        module.write(backup / str(index), target.read_bytes(), mode=0o600)
    module.write(backup / 'metadata.json', json.dumps(metadata).encode(), mode=0o600)
    call(['systemctl', 'stop', ADAPTER])
    try:
        if plan(module, reviewed['revision']) != reviewed:
            raise ValueError('installed state changed during preparation')
        for name, (target, _) in FILES.items():
            row = metadata[name]
            module.write(target, (ROOT / name).read_bytes(), row['uid'], row['gid'], row['mode'])
        module.native_tokens(admit_hermes=True, admit_openbot_android=True)
        call(['systemctl', 'start', ADAPTER])
        probe(module)
        receipt = {'state': 'applied', 'revision': reviewed['revision'], 'plan_sha256': expected,
                   'backup': str(backup), 'files': {name: row['after'] for name, row in reviewed['files'].items()},
                   'tokens_sha256': digest(module.TOKENS.read_bytes())}
        module.write(STATE / 'receipt.json', json.dumps(receipt, sort_keys=True).encode(), mode=0o600)
        return receipt
    except BaseException:
        call(['systemctl', 'stop', ADAPTER])
        for name, target in paths.items():
            row = metadata[name]
            module.write(target, (backup / row['backup']).read_bytes(), row['uid'], row['gid'], row['mode'])
        call(['systemctl', 'start', ADAPTER])
        raise


def rollback(module) -> dict:
    module.regular(STATE / 'receipt.json', 0, 0o600)
    receipt = json.loads((STATE / 'receipt.json').read_bytes())
    if receipt['state'] != 'applied':
        raise ValueError('active installation receipt required')
    restore = {}
    preserve = []
    for name, (target, _) in FILES.items():
        module.regular(target, 0)
        current = digest(target.read_bytes())
        desired = digest((ROOT / name).read_bytes())
        if current == receipt['files'][name]:
            restore[name] = True
        elif current == desired:
            # A newer, published revision may already have installed this file.
            # Preserve it while rolling back only the files owned by this receipt.
            restore[name] = False
            preserve.append(name)
        else:
            raise ValueError('later source changes block rollback')
    if digest(module.TOKENS.read_bytes()) != receipt['tokens_sha256']:
        raise ValueError('later credential changes block rollback')
    backup = Path(receipt['backup'])
    if backup.parent != STATE:
        raise ValueError('private rollback directory required')
    metadata = json.loads((backup / 'metadata.json').read_bytes())
    call(['systemctl', 'stop', ADAPTER])
    for name, target in {**{name: target for name, (target, _) in FILES.items()}, 'tokens': module.TOKENS}.items():
        if name in restore and not restore[name]:
            continue
        row = metadata[name]
        module.write(target, (backup / row['backup']).read_bytes(), row['uid'], row['gid'], row['mode'])
    call(['systemctl', 'start', ADAPTER])
    receipt['state'] = 'rolled-back'
    receipt['preserved_source_files'] = preserve
    module.write(STATE / 'receipt.json', json.dumps(receipt, sort_keys=True).encode(), mode=0o600)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--rollback', action='store_true')
    actions.add_argument('--export-credential', action='store_true', help='private pipe to the guest connector only')
    actions.add_argument('--export-openbot-android-credential', action='store_true',
                         help='private pipe to OpenBot MCP settings only')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    try:
        module = native(); module.guard(); published(args.revision)
        os.umask(0o077)
        descriptor = os.open('/var/lib/homecompute/deploy.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.export_credential:
                module.regular(module.TOKENS, module.pwd.getpwnam('homecompute-openclaw').pw_uid, 0o600)
                sys.stdout.write(json.loads(module.TOKENS.read_bytes())['hermes-owner'])
                return 0
            if args.export_openbot_android_credential:
                module.regular(module.TOKENS, module.pwd.getpwnam('homecompute-openclaw').pw_uid, 0o600)
                sys.stdout.write(json.loads(module.TOKENS.read_bytes())['openbot-android'])
                return 0
            if args.rollback:
                result = rollback(module)
            else:
                reviewed = plan(module, args.revision)
                result = (apply(module, reviewed, args.expected_plan_sha256) if args.apply else
                          {**reviewed, 'plan_sha256': digest(json.dumps(reviewed, sort_keys=True).encode())})
            print(json.dumps(result, sort_keys=True))
            return 0
        finally:
            os.close(descriptor)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('Hermes MCP setup refused (' + type(error).__name__ +
              '); preserve private state and reconcile the exact plan.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
