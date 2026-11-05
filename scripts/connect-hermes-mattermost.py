#!/usr/bin/env python3
"""Configure native Hermes Mattermost using a gateway-held credential."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = 'hermes-owner'
PROVIDER = NAME + '-mattermost'
PROFILE = 'homecompute-hermes-mattermost-v1'


def call(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        # Credential-aware command diagnostics stay private to the guest.
        raise RuntimeError('Hermes Mattermost step failed: ' + ' '.join(args[:3]))
    return result.stdout


def main():
    # Called only after setup-hermes-guest.sh validates owner, secret-file mode,
    # configuration and household readiness records.
    token = os.environ.pop('MATTERMOST_TOKEN')
    gateway = os.environ['HERMES_OPENSHELL_GATEWAY']
    os_args = ['openshell', '-g', gateway]
    call(os_args + ['provider', 'profile', 'import', '--file',
                    str(ROOT / 'deploy/hermes/mattermost-provider.yaml')])
    existing = subprocess.run(os_args + ['provider', 'get', PROVIDER],
                              capture_output=True, text=True, timeout=30)
    if existing.returncode:
        if 'not found' not in (existing.stdout + existing.stderr).lower():
            raise RuntimeError('Cannot establish Mattermost provider absence')
        call(os_args + ['provider', 'create', '--name', PROVIDER, '--type', PROFILE,
                        '--credential', 'MATTERMOST_TOKEN'],
             env={**os.environ, 'MATTERMOST_TOKEN': token})
    elif PROFILE not in existing.stdout:
        raise RuntimeError('Existing Mattermost provider has a different profile')
    token = ''
    call(['nemohermes', NAME, 'policy', 'add', '--from-file',
          str(ROOT / 'deploy/hermes/mattermost-policy.yaml'),
          '--trusted-private-host', 'home-core.tail479ad.ts.net', '--yes'])
    call(os_args + ['sandbox', 'provider', 'attach', NAME, PROVIDER])
    settings = {key: os.environ[key] for key in (
        'MATTERMOST_URL', 'MATTERMOST_ALLOWED_USERS', 'MATTERMOST_REPLY_MODE',
        'MATTERMOST_REQUIRE_MENTION')}
    program = '''import os, pathlib, yaml
home = pathlib.Path('/sandbox/.hermes')
settings = SETTINGS
path = home / '.env'
lines = path.read_text().splitlines() if path.exists() else []
lines = [line for line in lines if not line.startswith('MATTERMOST_')]
lines.extend(key + '=' + value for key, value in settings.items())
path.write_text('\\n'.join(lines) + '\\n'); path.chmod(0o600)
path = home / 'config.yaml'
config = yaml.safe_load(path.read_text()) or {}
config.setdefault('platforms', {}).setdefault('mattermost', {})['enabled'] = True
path.write_text(yaml.safe_dump(config, sort_keys=False))
print('Mattermost non-secret settings installed')
'''.replace('SETTINGS', repr(settings))
    call(['nemohermes', NAME, 'exec', '--stdin', '--', 'python3', '-'], input=program)
    call(['nemohermes', NAME, 'stop'])
    call(['nemohermes', NAME, 'start'])
    print('Native Hermes Mattermost configured; live reply verification still required.')


if __name__ == '__main__':
    main()
