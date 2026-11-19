#!/usr/bin/env python3
"""Manage only the OpenBot maintenance alias on the active Home Core gateway."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
ALIAS = 'homecompute_openbot_maintenance'
BLOCK = (
    f'  {ALIAS}:\n'
    '    url: https://homecompute-capabilities.home.arpa:19445/mcp\n'
    '    transport: http\n'
    '    auth_type: oauth_delegate\n'
    '    description: Host reports, Git upgrade proposals, and approved restarts for the isolated OpenBot operator\n'
    '    allow_all_keys: false\n'
    '    access_groups: [openbot-maintenance]\n'
    '    allowed_tools:\n'
    '      - homecompute_systems\n'
    '      - homecompute_system_read\n'
    '      - homecompute_maintenance_prepare\n'
    '      - homecompute_maintenance_preparation\n'
    '      - homecompute_maintenance_list\n'
    '      - homecompute_maintenance_status\n'
    '      - homecompute_maintenance_run\n'
    '      - homecompute_maintenance_proposal\n'
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('scoped_core', 'retire-openclaw-core.py')
gateway = load('scoped_gateway', 'deploy-litellm-chatgpt.py')
STATE = gateway.STATE / 'openbot-scoped'


def render(baseline, source):
    if source.count(BLOCK) != 1 or source.count(f'{ALIAS}:') != 1:
        raise ValueError('Published OpenBot alias differs from the exact scoped grant')
    if baseline.count('\nmcp_servers:\n') != 1:
        raise ValueError('Active LiteLLM config lacks one recognized MCP catalog')
    if f'{ALIAS}:' in baseline:
        if baseline.count(BLOCK) == 1 and baseline.count(f'{ALIAS}:') == 1:
            return baseline
        raise ValueError('Existing maintenance alias requires reconciliation')
    return baseline.replace('\nmcp_servers:\n', '\nmcp_servers:\n' + BLOCK, 1)


def receipt_path(revision):
    return STATE / (revision + '.json')


def write_receipt(path, report):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(report, sort_keys=True).encode() + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def inputs(revision):
    base = (gateway.STATE / 'deployed-revision').read_text().strip()
    release = gateway.RELEASES / base
    if (not gateway.SHA.fullmatch(base)
            or Path('/srv/homecompute/current').resolve(strict=True) != release
            or gateway.run('git', '-C', str(release), 'rev-parse', 'HEAD').strip() != base):
        raise ValueError('Active host release identity differs from its marker')
    # A scoped gateway rollout leaves unrelated tracked edits alone.
    gateway.run('git', '-C', str(release), 'diff', '--exit-code', 'HEAD', '--',
                str(gateway.COMPOSE), str(gateway.CONFIG))
    container = gateway.inspection()
    current = gateway.mounted_source(container)
    checksum = gateway.verify_container(release, current, container)
    desired = STATE / f'{base}-{revision}.yaml'
    return base, release, container, current, checksum, desired


def managed_baseline(path, base, release):
    if (path != release / gateway.CONFIG and not re.fullmatch(
            rf'{re.escape(str(gateway.STATE))}/litellm-(?:chatgpt|retirement)/{base}-[a-f0-9]{{40}}\.yaml',
            str(path))):
        raise ValueError('Current LiteLLM mount is outside the managed overlay paths')
    return core.protected(path).decode()


def run(operation, revision, expected=None):
    core.release(revision)
    with core.LOCK.open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        base, release, container, current, checksum, desired = inputs(revision)
        path = receipt_path(revision)
        if operation in {'verify', 'rollback'}:
            report = json.loads(core.protected(path, private=True))
            if (report['revision'] != revision or report['base_revision'] != base
                    or report['image_id'] != container['Image'] or report['after'] != str(desired)
                    or current != desired or checksum != report['after_sha256']):
                raise ValueError('The live gateway differs from its scoped rollout receipt')
            before = Path(report['before'])
            baseline = managed_baseline(before, base, release)
            source = (ROOT / gateway.CONFIG).read_text()
            if (hashlib.sha256(baseline.encode()).hexdigest() != report['before_sha256']
                    or core.protected(desired).decode() != render(baseline, source)):
                raise ValueError('Scoped overlay or prior configuration changed')
            if operation == 'rollback':
                gateway.change_config(release, desired, before, container['Image'])
            else:
                verify_controller()
            return {'revision': revision, 'base_revision': base, 'operation': operation, 'verified': True}

        if current == desired:
            raise ValueError('Scoped gateway config is already mounted; use verify')
        baseline = managed_baseline(current, base, release)
        candidate = render(baseline, (ROOT / gateway.CONFIG).read_text())
        if candidate == baseline:
            raise ValueError('Active gateway already has this alias; use the normal released lifecycle')
        report = {'revision': revision, 'base_revision': base, 'before': str(current),
                  'after': str(desired), 'before_sha256': checksum,
                  'after_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                  'container_id': container['Id'], 'image_id': container['Image']}
        digest = core.digest(report)
        if operation == 'plan':
            return {**report, 'plan_sha256': digest}
        if expected != digest:
            raise ValueError('Exact reviewed gateway plan required')
        os.umask(0o077)
        gateway.install_config(desired, candidate)
        write_receipt(path, report)
        gateway.change_config(release, current, desired, container['Image'])
        return {'revision': revision, 'base_revision': base, 'operation': operation, 'verified': True}


def verify_controller():
    for directory, names in (
            ('/opt/homecompute/infrastructure-maintenance',
             ('infrastructure_maintenance.py', 'maintenance_plan_preparation.py')),
            ('/opt/homecompute/openclaw-communication',
             ('scripts/openclaw-communication.py', 'scripts/openclaw_capabilities.py',
              'deploy/codex-worker/mcp_adapter.py'))):
        for name in names:
            source = ROOT / name if '/' in name else SCRIPTS / name
            if core.protected(Path(directory) / name) != source.read_bytes():
                raise ValueError('Installed maintenance controller or adapter differs from the scoped source')
    for unit in ('homecompute-infrastructure-maintenance.service',
                 'homecompute-openclaw-adapter.service', 'homecompute-native-mcp-relay.service'):
        core.command('systemctl', 'is-active', '--quiet', unit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'apply', 'verify', 'rollback'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.operation, args.revision, args.expected_plan_sha256)))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'OpenBot scoped gateway operation stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
