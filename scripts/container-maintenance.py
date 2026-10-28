#!/run/current-system/sw/bin/python3 -I
"""Finite root container recovery helper, called by the independent controller.

The controller owns approval, the deployment lock and one-attempt consumption.
Preparation reads only metadata; restart never retries. Verification repeats
only bounded inspect reads and never claims recovery from a restart receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time

COMMAND = '/usr/local/libexec/homecompute-container-maintenance'
CONTAINERS = {
    'home-core': frozenset({'homecompute-homepage-homepage-1', 'homecompute-automation-n8n-1',
        'homecompute-control-plane-caddy-1', 'homecompute-control-plane-litellm-1',
        'homecompute-control-plane-postgres-1', 'homecompute-open-webui-open-webui-1',
        'homecompute-immich-immich-server-1', 'home-core-piper-tts', 'home-core-wyoming-stt'}),
    'home-spark': frozenset({'qwen38-flash-ultrafast', 'gb10-hviske-primary', 'homecompute-plapre-plapre-primary-1'}),
}
ENV = {'HOME': '/root', 'PATH': '/run/current-system/sw/bin:/usr/bin:/bin',
       'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
MAX_OUTPUT = 1048576


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def output(value):
    return (canonical(value) + '\n').encode()


class Maintenance:
    def __init__(self, host, *, runner=subprocess.run, clock=time.time):
        if os.geteuid() != 0 or host not in CONTAINERS:
            raise ValueError('root on a reviewed compute host required')
        self.host, self.runner, self.clock = host, runner, clock
        self.docker = '/run/current-system/sw/bin/docker' if host == 'home-core' else '/usr/bin/docker'

    def selected(self, name):
        if not isinstance(name, str) or name not in CONTAINERS[self.host]:
            raise ValueError('container outside reviewed local recovery policy')
        return name

    def run(self, argv, timeout=10):
        result = self.runner(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                             env=ENV.copy(), cwd='/', shell=False)
        if (result.returncode or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
                or len(result.stdout) + len(result.stderr) > MAX_OUTPUT):
            raise ValueError('fixed bounded container command unavailable')
        return result.stdout

    def snapshot(self, name):
        name = self.selected(name)
        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError('duplicate container metadata field')
                values[key] = value
            return values
        rows = json.loads(self.run([self.docker, 'inspect', name]), object_pairs_hook=unique)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError('one exact container snapshot required')
        row = rows[0]
        if (row.get('Name') != '/' + name or not isinstance(row.get('Id'), str)
                or not re.fullmatch('[a-f0-9]{64}', row['Id']) or not isinstance(row.get('Image'), str)
                or not re.fullmatch('sha256:[a-f0-9]{64}', row['Image'])
                or type(row.get('RestartCount')) is not int or not 0 <= row['RestartCount'] <= 2**32 - 1
                or not isinstance(row.get('State'), dict) or type(row['State'].get('Running')) is not bool
                or not isinstance(row.get('Config'), dict) or not isinstance(row.get('HostConfig'), dict)
                or not isinstance(row.get('Mounts'), list)):
            raise ValueError('finite selected container identity required')
        state = row['State']
        running = state['Running'] and not any(state.get(key, False) for key in ('Restarting', 'Dead', 'Paused'))
        health = state.get('Health')
        if health is None:
            health_status = 'absent'
        elif isinstance(health, dict) and health.get('Status') in {'healthy', 'unhealthy', 'starting'}:
            health_status = health['Status']
        else:
            health_status = 'unknown'
        config = {key: row[key] for key in ('Config', 'HostConfig', 'Mounts')}
        return {'id': row['Id'], 'image': row['Image'], 'running': running, 'health': health_status,
                'restart_count': row['RestartCount'], 'config_hash': hashlib.sha256(canonical(config).encode()).hexdigest()}

    def restart(self, name):
        name = self.selected(name)
        self.run([self.docker, 'restart', '--timeout', '20', name], timeout=40)
        return {'restarted': True, 'container': name}

    def verify(self, name, *, timeout=120, sleep=time.sleep, monotonic=time.monotonic):
        name = self.selected(name)
        deadline = monotonic() + timeout
        while True:
            try:
                current = self.snapshot(name)
                if current['running'] and current['health'] in {'healthy', 'absent'}:
                    return {'verified': True, 'container': name, **current}
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                pass
            if monotonic() >= deadline:
                raise ValueError('container recovery not independently verified; reconcile without restart retry')
            sleep(min(5, max(0, deadline - monotonic())))

    def prepare(self, name):
        name = self.selected(name)
        current = self.snapshot(name)
        return {'schema_version': 1, 'policy': {'operation': 'fix', 'target': self.host, 'updates': []},
                'requires_approval': True,
                'prechecks': [{'argv': [COMMAND, 'snapshot', name], 'stdout_sha256': hashlib.sha256(output(current)).hexdigest()}],
                'steps': [[COMMAND, 'restart', name]], 'postchecks': [[COMMAND, 'verify', name]],
                'expires_at': self.clock() + 900, 'timeout_seconds': 180}


def write_plan(path, plan):
    if os.geteuid() != 0:
        raise ValueError('root plan output required')
    path = Path(os.path.abspath(path))
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('fixed root-owned plan directory required')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(plan, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['snapshot', 'restart', 'verify', 'prepare'], nargs='?')
    parser.add_argument('name', nargs='?')
    parser.add_argument('--prepare', dest='prepare_name')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.prepare_name is not None:
            if args.operation is not None or args.name is not None:
                raise ValueError('one preparation selector required')
            args.operation, args.name = 'prepare', args.prepare_name
        if args.operation is None or args.name is None or (args.operation == 'prepare') != (args.output is not None):
            raise ValueError('exact operation and container required; prepare also requires output')
        helper = Maintenance(socket.gethostname().split('.')[0])
        if args.operation == 'prepare':
            write_plan(args.output, helper.prepare(args.name))
            result = {'plan_file': str(args.output), 'installed_changes': False}
        else:
            result = getattr(helper, args.operation)(args.name)
        sys.stdout.write(output(result).decode())
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Container maintenance unavailable or uncertain; reconcile through the trusted operator without retry.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
