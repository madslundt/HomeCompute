#!/usr/bin/env python3
"""Hash-bound owner speech admission; preserve the earlier MCP installation receipt."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path('/opt/homecompute/openclaw-communication/scripts/openclaw-communication.py')
PREVIOUS = '4c694e4828ac81f15ce046038246a51ac5d78f7a3f3c5c8c6e04ce8134e7cb9d'
STATE = Path('/var/lib/homecompute-hermes-speech-bridge')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(revision):
    before = sha(TARGET.read_bytes())
    after = sha((ROOT / 'scripts/openclaw-communication.py').read_bytes())
    if before not in {PREVIOUS, after}:
        raise ValueError('unknown installed adapter; preserve and reconcile drift')
    return {'revision': revision, 'before': before, 'after': after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('speech_bridge_core', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    native = core.native(); native.guard(); core.published(args.revision)
    native.regular(TARGET, 0)
    with Path('/var/lib/homecompute/deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = plan(args.revision)
        digest = sha(json.dumps(selected, sort_keys=True).encode())
        if not args.apply:
            print(json.dumps({**selected, 'plan_sha256': digest, 'applied': False})); return
        if args.expected_plan_sha256 != digest:
            raise ValueError('exact reviewed speech bridge plan required')
        native.directory(STATE, 0o700)
        receipt = STATE / 'receipt.json'
        if args.rollback:
            native.regular(receipt, 0, 0o600)
            record = json.loads(receipt.read_bytes())
            backup = STATE / record['backup']
            native.regular(backup, 0, 0o600)
            if sha(TARGET.read_bytes()) != record['after'] or sha(backup.read_bytes()) != record['before']:
                raise ValueError('verified speech preimage required; later changes block rollback')
            desired = backup.read_bytes()
        else:
            if selected['before'] == selected['after']:
                core.probe(native)
                print(json.dumps({'already_current': True, 'revision': args.revision})); return
            if receipt.exists():
                raise ValueError('retained speech receipt requires review before another replacement')
            backup = STATE / ('before-' + str(time.time_ns()) + '.py')
            native.write(backup, TARGET.read_bytes(), mode=0o600)
            record = {**selected, 'backup': backup.name}
            desired = (ROOT / 'scripts/openclaw-communication.py').read_bytes()
        info = TARGET.stat(); original = TARGET.read_bytes()
        core.call(['systemctl', 'stop', core.ADAPTER])
        try:
            if sha(TARGET.read_bytes()) != selected['before']:
                raise ValueError('adapter changed during speech preparation')
            native.write(TARGET, desired, info.st_uid, info.st_gid, info.st_mode & 0o777)
            core.call(['systemctl', 'start', core.ADAPTER])
            core.probe(native)
        except BaseException:
            core.call(['systemctl', 'stop', core.ADAPTER])
            native.write(TARGET, original, info.st_uid, info.st_gid, info.st_mode & 0o777)
            core.call(['systemctl', 'start', core.ADAPTER])
            raise
        record['rolled_back'] = args.rollback
        native.write(receipt, json.dumps(record, sort_keys=True).encode(), mode=0o600)
        print(json.dumps({'revision': args.revision, 'applied': True, 'rolled_back': args.rollback}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else type(error).__name__) from None
