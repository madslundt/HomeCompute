#!/usr/bin/env python3
"""Plan, stage or roll back the same-model .05/.05 speech capacity candidate.

Run from an immutable published release as root on home-spark. Activation only
stages a candidate; it never calls that candidate accepted or edits live env files.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import signal
import time
import uuid

sys.dont_write_bytecode = True
from speech_capacity import (BUDGETS, CONTAINERS, SERVICES, FAILURE_PREFIX, BaselineMismatch,
                             activate, activation_approved, candidate, engine_command, failure_metadata, verify_baseline)

from speech_startup_diagnostics import ATTEMPT_PREFIX, attempt_metadata, bounded_process, classify, snapshot

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute-speech-capacity')
PATHS = {'hviske': ('deploy/hviske-stt/compose.yaml', '/etc/gb10-ai/hviske.env'),
         'plapre': ('deploy/compute-node/plapre/compose.yaml', '/etc/gb10-ai/plapre-tts.env')}
RENDER_PROFILES = {'hviske': ('hviske',), 'plapre': ()}


class PlanFailure(RuntimeError):
    def __init__(self, stage: str, service: str | None, reason: str):
        self.diagnostic = failure_metadata({'stage': stage, 'service': service, 'reason': reason})
        if self.diagnostic is None:
            raise ValueError('Invalid failure category')
        super().__init__('Speech capacity guard failed: ' + stage + '/' + reason)


def guarded(stage: str, service: str | None, reason: str, action):
    try:
        return action()
    except PlanFailure:
        raise
    except Exception:
        raise PlanFailure(stage, service, reason) from None


class OperationFailure(RuntimeError):
    def __init__(self, code, reason):
        self.exit_code, self.reason = code, reason
        super().__init__('Bounded Docker operation failed')


def run(args: list[str], timeout=60) -> str:
    result = bounded_process(args, timeout=timeout)
    if result is None: raise OperationFailure(None, 'startup-operation')
    code, output, errors = result
    if code: raise OperationFailure(code, classify(errors + '\n' + output[-262144:]))
    return output


def digest(value) -> str:
    body = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


def protected(path: Path, directory=False) -> None:
    for ancestor in [*reversed(path.absolute().parents), path]:
        info = ancestor.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
                (ancestor == path and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))):
            raise ValueError('Untrusted deployment path: ' + str(ancestor))


def release() -> str:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('Run as root on home-spark')
    protected(ROOT, directory=True)
    revision = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise ValueError('Use the selected published immutable release directory')
    if run(['git', '-C', str(ROOT), 'status', '--porcelain']).strip():
        raise ValueError('The published release checkout is modified')
    return revision


def inspect(name: str) -> dict:
    value = json.loads(run(['docker', 'inspect', name]))[0]
    return value


def render(key: str) -> dict:
    relative, environment = PATHS[key]
    guarded('render', key, 'protected-path', lambda: protected(ROOT / relative))
    guarded('approval', key, 'protected-path', lambda: protected(Path(environment)))
    guarded('approval', key, 'approval-required',
            lambda: activation_approved(key, Path(environment).read_text()))
    arguments = ['docker', 'compose', '--env-file', environment, '-f', str(ROOT / relative)]
    for profile in RENDER_PROFILES[key]:
        arguments += ['--profile', profile]
    value = guarded('render', key, 'render-failed',
                    lambda: json.loads(run([*arguments, 'config', '--format', 'json'])))
    if not isinstance(value, dict):
        raise PlanFailure('render', key, 'render-failed')
    service = value.get('services', {}).get(SERVICES[key])
    if not isinstance(service, dict):
        raise PlanFailure('render', key, 'service-missing')
    # Resolve the configured immutable/local pinned tag to the installed image;
    # neither staging nor restoration may pull a replacement image.
    service['image'] = guarded('image', key, 'image-unavailable',
        lambda: json.loads(run(['docker', 'image', 'inspect', service['image']]))[0]['Id'])
    return value


def prepare(revision: str) -> tuple[dict, dict, dict]:
    original, running, identities = {}, {}, {}
    for key, name in CONTAINERS.items():
        data = guarded('inspect', key, 'inspect-unavailable', lambda: inspect(name))
        if not data['State']['Running'] or data['State'].get('Health', {}).get('Status') != 'healthy':
            raise PlanFailure('baseline', key, 'baseline-unhealthy')
        original[key] = render(key)
        labels = data['Config']['Labels']
        if labels.get('com.docker.compose.project') != original[key]['name']:
            raise PlanFailure('baseline', key, 'project-mismatch')
        running[key] = {'image': data['Image'], 'command': data['Config']['Cmd']}
        identities[key] = data['Id']
    try:
        verify_baseline(original, running)
    except BaselineMismatch as error:
        raise PlanFailure('baseline', error.service, error.field + '-mismatch') from None
    staged = guarded('candidate', None, 'budget-unrecognized', lambda: candidate(original))
    plan = {'schema_version': 1, 'profile': 'speech-05-05-candidate', 'state': 'qualification-required',
            'source_revision': revision, 'budgets': BUDGETS, 'container_ids': identities,
            'baseline_sha256': digest(original), 'candidate_sha256': digest(staged),
            'unchanged': ['models', 'images', 'voices', 'tempo', 'contexts', 'sequence counts', 'Flash runtime']}
    plan['plan_sha256'] = digest(plan)
    return plan, original, staged


def write(path: Path, value) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def start(directory: Path, key: str, profile: str) -> None:
    path = directory / (key + '-' + profile + '.json')
    protected(path)
    started = time.monotonic()
    try:
        run(['docker', 'compose', '-f', str(path), 'up', '-d', '--no-build', '--no-deps',
             '--pull', 'never', '--wait', '--wait-timeout', '900', SERVICES[key]], timeout=960)
    except BaseException as error:
        # Capture BEFORE activate() restores this service and deletes its failed
        # container. Failure to capture evidence must never prevent restoration.
        reason = 'startup-operation'
        try:
            expected = json.loads(path.read_text())['services'][SERVICES[key]]
            evidence = snapshot(CONTAINERS[key], expected)
            reason = evidence['reason']
            if reason in ('startup-operation', 'startup-container-exit', 'startup-identity') and isinstance(error, OperationFailure):
                if error.reason != 'startup-operation': reason = error.reason
            evidence['reason'] = reason
            write(directory / ('start-' + uuid.uuid4().hex + '.json'), {
                'schema_version': 1, 'source_revision': ROOT.name, 'service': key, 'profile': profile,
                'plan_sha256': directory.name if re.fullmatch('[0-9a-f]{64}', directory.name) else None,
                'compose_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'operation_exit_code': error.exit_code if isinstance(error, OperationFailure) else None, **evidence})
        except BaseException:
            pass
        if isinstance(error, (KeyboardInterrupt, SystemExit)): raise
        raise PlanFailure('startup', key, reason) from None


def validate_active() -> tuple[Path, dict]:
    marker = STATE / 'active.json'
    protected(marker)
    data = json.loads(marker.read_text())
    identifier = data.get('plan_sha256', '')
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('Invalid candidate receipt')
    directory = STATE / identifier
    protected(directory, directory=True)
    for profile in ['baseline', 'candidate']:
        values = {}
        for key in SERVICES:
            path = directory / (key + '-' + profile + '.json')
            protected(path)
            values[key] = json.loads(path.read_text())
        if digest(values) != data[profile + '_sha256']:
            raise ValueError('Candidate or rollback file changed')
    return directory, data


def restore() -> dict:
    directory, data = validate_active()
    failures = []
    for key in reversed(SERVICES):
        try:
            start(directory, key, 'baseline')
        except Exception:
            failures.append(key)
    if failures:
        raise RuntimeError('Speech baseline restoration failed: ' + ', '.join(failures))
    original = {key: json.loads((directory / (key + '-baseline.json')).read_text()) for key in SERVICES}
    if not baseline_restored(original):
        raise RuntimeError('Speech baseline restoration could not be verified')
    (STATE / 'active.json').unlink()
    return {'restored': 'baseline', 'plan_sha256': data['plan_sha256'], 'accepted': False}


def actual_profiles(directory: Path) -> dict:
    result = {}
    for key, name in CONTAINERS.items():
        data = inspect(name)
        profile = 'unrecognized'
        for label in ['baseline', 'candidate']:
            service = json.loads((directory / (key + '-' + label + '.json')).read_text())['services'][SERVICES[key]]
            if data['Image'] == service['image'] and data['Config']['Cmd'] == engine_command(service['command']):
                profile = label
        result[key] = {'profile': profile, 'healthy': profile != 'unrecognized' and runtime_healthy(data)}
    return result


def runtime_healthy(data: dict) -> bool:
    state = data.get('State', {})
    return state.get('Running') is True and state.get('Health', {}).get('Status') == 'healthy'


def baseline_restored(original: dict) -> bool:
    # A stopped container may retain its old healthy result. A failed or
    # unreadable restoration must keep the recovery marker, not hide drift.
    try:
        for key, name in CONTAINERS.items():
            data = inspect(name)
            expected = original[key]['services'][SERVICES[key]]
            if (not runtime_healthy(data) or data['Image'] != expected['image'] or
                    data['Config']['Cmd'] != engine_command(expected['command'])):
                return False
    except Exception:
        return False
    return True



def perform_attempt(directory: Path, plan: dict, original: dict, diagnose=False) -> dict:
    failure = None
    try:
        activate(lambda key, profile: start(directory, key, profile))
        if not diagnose:
            return {'candidate_active': True, 'accepted': False,
                    'plan_sha256': plan['plan_sha256'], 'state': 'qualification-required'}
    except BaseException as error:
        failure = error
    # activate() already restored attempted services on a startup failure. A
    # successful startup-only probe always restores BOTH before returning.
    if failure is None:
        for key in reversed(SERVICES):
            try: start(directory, key, 'baseline')
            except BaseException as error:
                if failure is None: failure = error
    restored = baseline_restored(original)
    primary = failure
    for _ in range(3):
        if isinstance(primary, PlanFailure) or primary is None or primary.__cause__ is None: break
        primary = primary.__cause__
    evidence = {'schema_version': 1, 'source_revision': plan['source_revision'],
                'plan_sha256': plan['plan_sha256'], 'rollback_state': 'restored' if restored else 'incomplete',
                'service': getattr(primary, 'diagnostic', {}).get('service'),
                'reason': getattr(primary, 'diagnostic', {}).get('reason'),
                'startup_only': bool(diagnose), 'capacity_qualified': False, 'accepted': False,
                'full_voice_accepted': False, 'start_receipts': []}
    for path in sorted(directory.glob('start-*.json')):
        evidence['start_receipts'].append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    # The terminal receipt is immutable and exists before the active marker can
    # be retired. If its write fails, retain that marker and surface uncertainty.
    write(directory / 'attempt-result.json', evidence)
    metadata = attempt_metadata({key: evidence[key] for key in ('plan_sha256','rollback_state','service','reason')} |
                               {'attempt_sha256': hashlib.sha256((directory / 'attempt-result.json').read_bytes()).hexdigest()})
    if restored: (STATE / 'active.json').unlink()
    if failure is not None:
        failure.attempt = metadata
        raise failure
    if not restored:
        failure = PlanFailure('startup', None, 'startup-operation'); failure.attempt = metadata
        raise failure
    return {**metadata, 'startup_only': True, 'capacity_qualified': False,
            'accepted': False, 'full_voice_accepted': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'apply', 'status', 'rollback', 'diagnose'])
    parser.add_argument('--plan-sha256')
    parser.add_argument('--speech-idle', action='store_true',
                        help='Coordinator has paused speech intake and verified no active speech request')
    args = parser.parse_args()
    revision = guarded('release', None, 'release-untrusted', release)
    if args.action == 'plan':
        print(json.dumps(prepare(revision)[0], sort_keys=True))
        return
    if args.action == 'status':
        if not (STATE / 'active.json').exists():
            print(json.dumps({'candidate_active': False, 'accepted': False}))
        else:
            directory, receipt = validate_active()
            print(json.dumps({**receipt, 'actual': actual_profiles(directory), 'accepted': False}, sort_keys=True))
        return
    if not args.speech_idle:
        raise ValueError('Pause speech intake and pass --speech-idle; no running request may be interrupted')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    protected(STATE, directory=True)
    with (STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'rollback':
            print(json.dumps(restore()))
            return
        if (STATE / 'active.json').exists():
            raise ValueError('A capacity candidate is already active; restore or reconcile it first')
        plan, original, staged = prepare(revision)
        if args.plan_sha256 != plan['plan_sha256']:
            raise ValueError('The reviewed plan changed; obtain and review a fresh plan')
        directory = STATE / plan['plan_sha256']
        directory.mkdir(mode=0o700)
        for key in SERVICES:
            write(directory / (key + '-baseline.json'), original[key])
            write(directory / (key + '-candidate.json'), staged[key])
        write(STATE / 'active.json', plan)
        print(json.dumps(perform_attempt(directory, plan, original, diagnose=args.action == 'diagnose')))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        diagnostic = error.diagnostic if isinstance(error, PlanFailure) else {
            'stage': 'operation', 'service': None, 'reason': 'guard-or-operation'}
        attempt = attempt_metadata(getattr(error, 'attempt', None))
        if attempt is not None:
            print(ATTEMPT_PREFIX + json.dumps(attempt, sort_keys=True), file=sys.stderr)
        print(FAILURE_PREFIX + json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
