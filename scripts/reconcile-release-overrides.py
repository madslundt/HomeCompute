#!/usr/bin/env python3
"""Retain verified maintenance history after its live settings enter Git source.

Root-only, read-only plan by default. No container or application settings change.
The reviewed apply archives guards; it never deletes maintenance records or data.
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

MATTERMOST_ENV = Path('/srv/state/mattermost/runtime.env')

TARGETS = {
    'homecompute-automation-n8n-1': ('config/automation.env.example', 'N8N_IMAGE'),
    'homecompute-homepage-homepage-1': ('config/homepage.env.example', 'HOMEPAGE_IMAGE'),
    'homecompute-mattermost-mattermost-1': ('config/mattermost.env.example', 'MATTERMOST_IMAGE'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def regular(path, owner=0):
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != owner or info.st_mode & 0o022 or info.st_size > 4_000_000:
        raise ValueError('protected bounded regular input required')
    return path.read_bytes()


def inspect(kind, target):
    args = ['docker', 'inspect', target] if kind == 'container' else ['docker', 'image', 'inspect', target]
    result = subprocess.run(args, check=True, capture_output=True, timeout=30)
    if len(result.stdout) > 2_000_000:
        raise ValueError('bounded daemon inspection required')
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise ValueError('exact daemon identity required')
    return rows[0]


def image_environment(data, image):
    lines = data.decode().splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith('MATTERMOST_IMAGE=')]
    if len(matches) != 1:
        raise ValueError('one existing Mattermost image setting required')
    i = matches[0]
    ending = '\r\n' if lines[i].endswith('\r\n') else '\n' if lines[i].endswith('\n') else ''
    old = lines[i].strip().split('=', 1)[1]
    lines[i] = 'MATTERMOST_IMAGE=' + image + ending
    return old, ''.join(lines).encode()


def atomic_environment(path, data):
    info = path.stat()
    fd, temporary = tempfile.mkstemp(prefix='.source-image-', dir=path.parent)
    try:
        os.fchmod(fd, info.st_mode & 0o777)
        os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def plan(source, state=Path('/var/lib'), inspector=inspect, owner=0):
    docker = state / 'homecompute-docker-maintenance'
    homepage = state / 'homecompute/homepage-maintenance'
    inputs, markers, containers, records = {}, [], {}, {}

    def read(path):
        data = regular(path, owner)
        inputs[str(path)] = digest(data)
        return data

    for path in sorted(docker.glob('*.active')):
        marker = json.loads(read(path))
        identity = path.stem
        if not re.fullmatch('[a-f0-9]{32}', identity) or set(marker) != {'candidate', 'container'} or marker['container'] not in TARGETS:
            raise ValueError('unknown maintenance guard requires separate reconciliation')
        record = json.loads(read(docker / (identity + '.json')))
        if (record['status'] != 'complete' or record['host'] != 'home-core' or record['id'] != identity
                or record['container'] != marker['container'] or record['candidate'] != marker['candidate']):
            raise ValueError('completed matching maintenance record required')
        records[identity] = record
        markers.append({'path': str(path), 'archive': 'docker/' + path.name})

    for name, (env_path, key) in TARGETS.items():
        values = [line.split('=', 1)[1] for line in read(source / env_path).decode().splitlines() if line.startswith(key + '=')]
        if len(values) != 1 or not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}', values[0]):
            raise ValueError('one immutable source image required')
        image = inspector('image', values[0])['Id']
        row = inspector('container', name)
        health = row['State']
        if (row['Image'] != image or not health.get('Running') or health.get('Health', {}).get('Status') != 'healthy'
                or any(health.get(k) for k in ('Paused', 'Restarting', 'Dead', 'OOMKilled')) or health.get('Error')):
            raise ValueError('healthy running container must match the published source image')
        containers[name] = {'id': row['Id'], 'image': image, 'source_image': values[0],
                            'configuration_sha256': digest(canonical([row['Config'], row['HostConfig'], row['Mounts']]))}
        selected = [r for r in records.values() if r['container'] == name]
        for record in selected:
            current, seen = record['candidate_image'], set()
            while current != image:
                if current in seen:
                    raise ValueError('cyclic maintenance history refused')
                seen.add(current)
                successors = [r for r in selected if r['old']['Image'] == current]
                if len(successors) != 1:
                    raise ValueError('superseded marker must lead to the exact live image')
                current = successors[0]['candidate_image']

    path = homepage / 'active-config.json'
    if path.exists() or path.is_symlink():
        expected = {'services': {'homepage': {'volumes': [{'type': 'bind', 'source': str(homepage / 'services.yaml'),
                    'target': '/app/config/services.yaml', 'read_only': True}]}}}
        if json.loads(read(path)) != expected:
            raise ValueError('unknown Homepage override refused')
        if read(homepage / 'services.yaml') != read(source / 'deploy/homepage/config/services.yaml'):
            raise ValueError('Homepage configuration must already match published source')
        mounts = inspector('container', 'homecompute-homepage-homepage-1')['Mounts']
        if not any(m['Type'] == 'bind' and m['Source'] == str(homepage / 'services.yaml')
                   and m['Destination'] == '/app/config/services.yaml' and not m['RW'] for m in mounts):
            raise ValueError('exact read-only Homepage override mount required')
        markers.append({'path': str(path), 'archive': 'homepage/' + path.name})
    if (homepage / 'active-image.json').exists() or (homepage / 'active-image.json').is_symlink():
        raise ValueError('separate approved Homepage image override requires review')
    runtime = read(MATTERMOST_ENV)
    pin = containers['homecompute-mattermost-mattermost-1']['source_image']
    old, projected = image_environment(runtime, pin)
    update = None
    if projected != runtime:
        old_image = inspector('image', old)['Id']
        if not any(r['container'] == 'homecompute-mattermost-mattermost-1' and r['old']['Image'] == old_image for r in records.values()):
            raise ValueError('Mattermost runtime image differs from retained maintenance baseline')
        update = {'path': str(MATTERMOST_ENV), 'image': pin, 'sha256': digest(projected)}
    result = {'schema_version': 1, 'source': str(source), 'inputs': inputs, 'containers': containers,
              'markers': markers, 'runtime_update': update}
    result['plan_sha256'] = digest(canonical(result))
    return result


def apply(source, expected, state=Path('/var/lib'), inspector=inspect, owner=0):
    with ExitStack() as stack:
        for path in (state / 'homecompute/deploy.lock', state / 'homecompute-docker-maintenance/operation.lock'):
            regular(path, owner)
            handle = stack.enter_context(path.open('r+'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reviewed = plan(source, state, inspector, owner)
        if reviewed['plan_sha256'] != expected:
            raise ValueError('reviewed plan changed; no guards archived')
        if not reviewed['markers'] and not reviewed['runtime_update']:
            return {'archived': 0, 'plan_sha256': expected}
        archive = state / 'homecompute/reconciled-overrides' / expected
        archive.mkdir(parents=True, mode=0o700, exist_ok=False)
        receipt = archive / 'receipt.json'
        with receipt.open('x') as stream:
            os.chmod(receipt, 0o600)
            json.dump(reviewed, stream, indent=2)
        moved, original_environment, environment_written = [], None, False
        try:
            if reviewed['runtime_update']:
                path = Path(reviewed['runtime_update']['path'])
                original_environment = regular(path, owner)
                if digest(original_environment) != reviewed['inputs'][str(path)]:
                    raise ValueError('runtime environment changed during retirement')
                backup = archive / 'mattermost-runtime.before'
                with backup.open('xb') as stream:
                    os.chmod(backup, 0o600)
                    stream.write(original_environment)
                _, projected = image_environment(original_environment, reviewed['runtime_update']['image'])
                atomic_environment(path, projected)
                environment_written = True
            for marker in reviewed['markers']:
                path, target = Path(marker['path']), archive / marker['archive']
                target.parent.mkdir(mode=0o700, exist_ok=True)
                # Compare again at the rename boundary; retain every preimage.
                if digest(regular(path, owner)) != reviewed['inputs'][str(path)]:
                    raise ValueError('maintenance guard changed during retirement')
                path.rename(target)
                moved.append((path, target))
            for name, saved in reviewed['containers'].items():
                row = inspector('container', name)
                current = digest(canonical([row['Config'], row['HostConfig'], row['Mounts']]))
                if (row['Id'] != saved['id'] or row['Image'] != saved['image'] or current != saved['configuration_sha256']
                        or not row['State'].get('Running') or row['State'].get('Health', {}).get('Status') != 'healthy'):
                    raise ValueError('live container changed during retirement; guards restored')
        except BaseException:
            for path, target in reversed(moved):
                if path.exists() or path.is_symlink():
                    raise RuntimeError('concurrent guard appeared; inspect retained archive')
                target.rename(path)
            if environment_written:
                path = Path(reviewed['runtime_update']['path'])
                if digest(regular(path, owner)) != reviewed['runtime_update']['sha256']:
                    raise RuntimeError('outside runtime environment edit preserved; inspect retained backup')
                atomic_environment(path, original_environment)
            raise
        return {'archived': len(moved), 'plan_sha256': expected, 'receipt': str(receipt), 'containers_changed': False,
                'mattermost_runtime_pin_reconciled': original_environment is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    if (os.geteuid() != 0 or socket.gethostname() != 'home-core' or not re.fullmatch('[a-f0-9]{40}', args.revision)
            or source != Path('/srv/homecompute/releases') / args.revision):
        parser.error('run as root on home-core from the exact published release checkout')
    for directory in [source, *source.parents]:
        info = directory.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('protected release ancestry required')
    def git(*command):
        return subprocess.check_output(['git', '-C', str(source), *command], timeout=30).decode().strip()
    if git('rev-parse', 'HEAD') != args.revision or git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('clean exact source checkout required')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', args.revision, 'origin/master'], check=True)
    result = apply(source, args.expected_plan_sha256) if args.apply else plan(source)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
