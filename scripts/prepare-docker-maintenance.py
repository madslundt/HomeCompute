#!/usr/bin/env python3
"""Prepare an exact Compose image plan from already staged immutable images.

Does not pull, recreate, or restart containers. Root selects a trusted Compose
deployment and staged candidate; image labels supply version evidence. Stateful
services and unavailable version labels require approval even within a major.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

from docker_image_updates import reference
from infrastructure_maintenance import ENV, trusted_path, validate_plan

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
VERSION_SOURCES = {
    ('ghcr.io', 'gethomepage/homepage'): {'https://github.com/gethomepage/homepage'},
    ('docker.io', 'n8nio/n8n'): {'https://github.com/n8n-io/n8n'},
    ('docker.n8n.io', 'n8nio/n8n'): {'https://github.com/n8n-io/n8n'},
    ('ghcr.io', 'open-webui/open-webui'): {'https://github.com/open-webui/open-webui'},
    ('ghcr.io', 'immich-app/immich-server'): {'https://github.com/immich-app/immich'},
    ('ghcr.io', 'berriai/litellm'): {'https://github.com/BerriAI/litellm'},
}


def run(argv, runner=subprocess.run):
    result = runner(argv, capture_output=True, timeout=30, env=ENV, check=False)
    if result.returncode or len(result.stdout) > 1048576:
        raise ValueError('bounded Docker metadata query failed')
    return result.stdout


def prepare(container, candidate, compose_files, environment, output, *, target,
            scheme='semver', runner=subprocess.run, clock=time.time, uid=0):
    if os.geteuid() != uid or target not in {'home-core', 'home-spark'}:
        raise ValueError('root on the selected compute host required')
    if not isinstance(container, str) or not NAME.fullmatch(container):
        raise ValueError('exact container name required')
    new_ref = reference(candidate)
    if not new_ref['digest']:
        raise ValueError('candidate must be immutable')
    if not 1 <= len(compose_files) <= 4:
        raise ValueError('bounded explicit Compose files required')
    paths = [trusted_path(p, uid) for p in [*compose_files, environment]]
    if any(p.stat().st_uid != uid for p in paths):
        raise ValueError('root-owned deployment files required')
    output = Path(output)
    trusted_path(output.parent, uid, directory=True)
    if output.exists() or output.is_symlink():
        raise ValueError('new plan destination required')
    docker = shutil.which('docker', path=ENV['PATH'])
    checksum = shutil.which('sha256sum', path=ENV['PATH'])
    if not docker or not checksum:
        raise ValueError('Docker and sha256sum required')
    inspect = [docker, 'inspect', container]
    baseline_raw = run(inspect, runner)
    rows = json.loads(baseline_raw)
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('one existing container required')
    baseline = rows[0]
    if not baseline['State']['Running'] or baseline['State'].get('Health', {}).get('Status') not in {None, 'healthy'}:
        raise ValueError('unhealthy container requires a separately approved fix')
    old_ref = reference(baseline['Config']['Image'])
    if (old_ref['registry'], old_ref['repository']) != (new_ref['registry'], new_ref['repository']):
        raise ValueError('candidate must use the existing image repository')
    labels = baseline['Config'].get('Labels') or {}
    project, service = labels.get('com.docker.compose.project'), labels.get('com.docker.compose.service')
    if not all(isinstance(n, str) and NAME.fullmatch(n) for n in (project, service)):
        raise ValueError('existing Compose ownership required')
    compose = [docker, 'compose', '--project-name', project, '--env-file', str(paths[-1])]
    for path in paths[:-1]:
        compose += ['-f', str(path)]
    config_raw = run(compose + ['config', '--format', 'json'], runner)
    config = json.loads(config_raw)
    selected = config['services'][service]
    if selected.get('image') != baseline['Config']['Image'] or 'build' in selected:
        raise ValueError('running image must match trusted Compose')
    actual_files = labels.get('com.docker.compose.project.config_files', '').split(',')
    if actual_files != [str(p) for p in paths[:-1]]:
        raise ValueError('all active Compose files must be supplied in order')
    old_image = json.loads(run([docker, 'image', 'inspect', baseline['Image']], runner))[0]
    new_image = json.loads(run([docker, 'image', 'inspect', candidate], runner))[0]
    if (candidate not in new_image.get('RepoDigests', []) and
            not any(reference(p)['digest'] == new_ref['digest'] for p in new_image.get('RepoDigests', []))):
        raise ValueError('staged candidate lacks digest provenance')
    if (old_image['Os'], old_image['Architecture']) != (new_image['Os'], new_image['Architecture']):
        raise ValueError('candidate platform mismatch')
    def version(row):
        metadata = row['Config'].get('Labels') or {}
        # Base-image version labels cannot establish application compatibility.
        if metadata.get('org.opencontainers.image.source') not in VERSION_SOURCES.get((new_ref['registry'], new_ref['repository']), set()):
            return 'unknown'
        return metadata.get('org.opencontainers.image.version') or 'unknown'
    installed, proposed = version(old_image), version(new_image)
    stateful = any(m.get('RW') and m.get('Type') != 'tmpfs' for m in baseline.get('Mounts', []))
    override = output.with_suffix('.image.json')
    if override.exists() or override.is_symlink():
        raise ValueError('new override destination required')
    with override.open('x') as stream:
        os.chmod(override, 0o600)
        json.dump({'services': {service: {'image': candidate}}}, stream)
        stream.flush(); os.fsync(stream.fileno())
    # Full inspect contains changing timestamps, so bind invariant fields instead.
    fmt = '{{json .Id}} {{json .Image}} {{json .Config}} {{json .HostConfig}} {{json .Mounts}} {{json .State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
    probe = [docker, 'inspect', '--format', fmt, container]
    compose_probe = compose + ['config', '--format', 'json']
    checks = [probe, compose_probe, [checksum, *map(str, [*paths, override])]]
    plan = {'schema_version': 1,
            'policy': {'operation': 'update', 'target': target, 'updates': [
                {'component': container, 'scheme': scheme, 'installed': installed, 'candidate': proposed}]},
            'requires_approval': stateful,
            'prechecks': [{'argv': c, 'stdout_sha256': hashlib.sha256(run(c, runner)).hexdigest()} for c in checks],
            'steps': [compose + ['-f', str(override), 'up', '-d', '--no-deps', '--pull', 'never',
                                 '--wait', '--wait-timeout', '180', service]],
            'postchecks': [{'argv': [docker, 'inspect', '--format', '{{.Config.Image}}', container],
                            'stdout_sha256': hashlib.sha256((candidate + '\n').encode()).hexdigest()}],
            'timeout_seconds': 240, 'expires_at': clock() + 3600}
    validate_plan(plan, local_target=target)
    with output.open('x') as stream:
        os.chmod(output, 0o600)
        json.dump(plan, stream, sort_keys=True, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    return {'plan_file': str(output), 'override_file': str(override), 'stateful_approval': stateful}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container', required=True); p.add_argument('--candidate', required=True)
    p.add_argument('--compose-file', type=Path, action='append', required=True)
    p.add_argument('--environment', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scheme', choices=['semver', 'calendar'], default='semver')
    a = p.parse_args()
    try:
        result = prepare(a.container, a.candidate, a.compose_file, a.environment, a.output,
                         target=socket.gethostname().split('.')[0], scheme=a.scheme)
        print(json.dumps(result)); return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Docker plan preparation refused; reconcile staged images and deployment inputs.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
