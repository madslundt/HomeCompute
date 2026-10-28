#!/usr/bin/env python3
"""One exact-plan startup probe, no benchmark; always restore the baseline."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://github.com/madslundt/HomeCompute.git'
DEPLOY_LOCK = Path('/var/lib/homecompute/home-spark-deploy.lock')


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, timeout=60)
    if result.returncode: raise ValueError('published-source-verification-failed')
    return result.stdout.strip()


def verify(revision):
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('root-on-home-spark-required')
    if not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise ValueError('exact-protected-release-required')
    for path in [*reversed(ROOT.parents), ROOT]:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('protected-release-required')
    if (git('rev-parse', 'HEAD') != revision or git('status', '--porcelain', '--untracked-files=all') or
            git('remote', 'get-url', 'origin') != REPOSITORY):
        raise ValueError('clean-published-source-required')
    git('merge-base', '--is-ancestor', revision, 'origin/master')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def report_target(directory):
    uid = int(os.environ.get('SUDO_UID', str(os.getuid())))
    info = directory.lstat()
    if (not re.fullmatch(r'/tmp/homecompute-speech-startup\.[A-Za-z0-9]+', str(directory)) or
            not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077):
        raise ValueError('private-operator-staging-required')
    target = directory / 'startup-result.json'
    if target.exists() or target.is_symlink(): raise ValueError('fresh-result-directory-required')
    return target


def interrupt(*_): raise InterruptedError('startup-diagnostic-interrupted')


@contextlib.contextmanager
def deferred_signals():
    events = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, events)
    try: yield
    finally: signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def execute(q, r, revision, approved):
    # Hash/source/container/image/command/approval guards are identical to apply.
    plan = q.prepare(revision)[0]
    if plan['plan_sha256'] != approved: raise ValueError('fresh-reviewed-plan-required')
    q.STATE.mkdir(parents=True, exist_ok=True, mode=0o700); q.protected(q.STATE, directory=True)
    if (q.STATE / 'active.json').exists(): raise ValueError('existing-capacity-override-requires-recovery')
    r.STATE.mkdir(parents=True, exist_ok=True, mode=0o700); q.protected(r.STATE, directory=True)
    result = {'source_revision': revision, 'plan_sha256': approved, 'startup_only': True,
              'capacity_qualified': False, 'accepted': False, 'full_voice_accepted': False,
              'rollback_state': 'not-required', 'state': 'startup-pending', 'failure': None, 'startup_attempt': None}
    with (r.STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep the old deploy guard/overlap exclusion used by normal rollout.
        with DEPLOY_LOCK.open('a') as deploy_lock:
            fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with r.paused_ingress():
                try:
                    previous = sys.argv
                    try:
                        sys.argv = ['qualify-speech-capacity.py', 'diagnose', '--plan-sha256', approved, '--speech-idle']
                        q.main()
                        result['rollback_state'] = 'restored'
                        result['state'] = 'startup-passed-baseline-restored'
                    finally: sys.argv = previous
                except BaseException as error:
                    result['state'] = 'startup-failed'
                    from speech_capacity import failure_metadata
                    from speech_startup_diagnostics import attempt_metadata
                    result['failure'] = failure_metadata(getattr(error, 'diagnostic', None))
                    result['startup_attempt'] = attempt_metadata(getattr(error, 'attempt', None))
                    if result['startup_attempt'] is not None:
                        result['rollback_state'] = result['startup_attempt']['rollback_state']
                    elif (q.STATE / 'active.json').exists(): result['rollback_state'] = 'incomplete'
                finally:
                    # Defer a second terminal signal only during bounded owned
                    # recovery. Unknown or failed restoration retains its marker.
                    if (q.STATE / 'active.json').exists():
                        with deferred_signals():
                            try:
                                r.idle(restoring=True)
                                with (q.STATE / 'lock').open('a') as recovery_lock:
                                    fcntl.flock(recovery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                    q.restore()
                                result['rollback_state'] = 'restored'
                            except BaseException:
                                result['rollback_state'] = 'incomplete'
    path = q.STATE / approved / 'attempt-result.json'
    if path.is_file():
        result['private_receipt'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--plan', action='store_true', help='Read-only exact plan; no model or ingress changes')
    action.add_argument('--plan-sha256', help='Explicit approval of this fresh exact plan; always restore baseline')
    parser.add_argument('--result-directory', type=Path, required=True)
    args = parser.parse_args()
    verify(args.revision); target = report_target(args.result_directory)
    # Imports are exclusively from the verified protected release, never the
    # user-owned staging directory or PYTHONPATH; isolated Python is required.
    if not sys.flags.isolated: raise ValueError('python-isolated-mode-required')
    for key in list(os.environ):
        if key.startswith('PYTHON'): os.environ.pop(key)
    sys.path.insert(0, str(ROOT / 'scripts'))
    q = load('startup_qualifier', ROOT / 'scripts/qualify-speech-capacity.py')
    r = load('startup_rollout', ROOT / 'scripts/spark-rollout.py')
    if args.plan:
        result = {'source_revision': args.revision, 'read_only': True, 'plan': q.prepare(args.revision)[0],
                  'accepted': False, 'full_voice_accepted': False, 'capacity_qualified': False}
    else:
        if not re.fullmatch('[0-9a-f]{64}', args.plan_sha256): raise ValueError('exact-plan-hash-required')
        for event in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(event, interrupt)
        try:
            result = execute(q, r, args.revision, args.plan_sha256)
        except BaseException:
            result = {'source_revision': args.revision, 'plan_sha256': args.plan_sha256,
                      'startup_only': True, 'capacity_qualified': False, 'accepted': False,
                      'full_voice_accepted': False, 'state': 'guard-or-recovery-failed',
                      'rollback_state': 'unverified'}
    r.publish_result(target, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    if args.plan: return 0
    passed = (result.get('state') == 'startup-passed-baseline-restored'
              and result.get('rollback_state') == 'restored' and result.get('failure') is None)
    return 0 if passed else 1


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print(json.dumps({'startup_only': True, 'state': 'guard-or-recovery-failed',
                          'capacity_qualified': False, 'accepted': False, 'full_voice_accepted': False}), file=sys.stderr)
        raise SystemExit(1) from None
