#!/usr/bin/env python3
"""Attach the dedicated browser using an OpenShell-held CDP credential."""
import importlib.util
import json
import os
import stat
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('messaging', ROOT / 'scripts/connect-hermes-mattermost.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
call = module.call
NAME = json.loads((ROOT / 'config/hermes-release.json').read_text())['household_sandbox']
PROVIDER = 'hermes-personal-browser'
PROFILE = 'homecompute-hermes-browser-v1'
OS = ['openshell', '-g', 'nemoclaw']


def main():
    row = json.loads(call(['docker', 'inspect', 'homecompute-hermes-browser']))[0]
    expected_image = json.loads((ROOT / 'config/hermes-release.json').read_text())['browser_worker_image_id']
    if not row['State']['Running'] or row['Image'] != expected_image:
        raise RuntimeError('Qualified dedicated Hermes browser required')
    mounts = [m for m in row['Mounts'] if m['Destination'] == '/run/browser-token']
    if len(mounts) != 1 or mounts[0]['RW'] or mounts[0]['Type'] != 'bind':
        raise RuntimeError('Read-only dedicated browser credential mount required')
    path = Path(mounts[0]['Source'])
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 32 <= info.st_size <= 4096
            or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077 or info.st_uid != os.getuid()):
        raise RuntimeError('Private operator-owned browser credential required')
    token = path.read_text().strip()
    profile_file = ROOT / 'deploy/hermes/browser-provider.yaml'
    expected = json.loads(profile_file.read_text())
    exported = subprocess.run(OS + ['provider', 'profile', 'export', PROFILE, '-o', 'json'],
                              capture_output=True, text=True, timeout=30)
    if exported.returncode:
        if 'not found' not in (exported.stdout + exported.stderr).lower():
            raise RuntimeError('Cannot establish browser profile absence')
        call(OS + ['provider', 'profile', 'import', '--file', str(profile_file)])
    elif {k: json.loads(exported.stdout).get(k) for k in expected} != expected:
        raise RuntimeError('Existing browser profile differs from source')
    existing = subprocess.run(OS + ['provider', 'get', PROVIDER], capture_output=True, text=True, timeout=30)
    if existing.returncode:
        if 'not found' not in (existing.stdout + existing.stderr).lower():
            raise RuntimeError('Cannot establish browser provider absence')
        call(OS + ['provider', 'create', '--name', PROVIDER, '--type', PROFILE,
                   '--credential', 'BROWSER_TOKEN'], env={**os.environ, 'BROWSER_TOKEN': token})
    elif PROFILE not in existing.stdout:
        raise RuntimeError('Existing browser provider differs from source')
    token = ''
    call(OS + ['sandbox', 'provider', 'attach', NAME, PROVIDER])
    call(['nemohermes', NAME, 'policy', 'add', '--from-file',
          str(ROOT / 'deploy/hermes/browser-policy.yaml'),
          '--trusted-private-host', '172.18.0.1', '--yes'])
    program = '''import os, pathlib, yaml
token = os.environ['BROWSER_TOKEN']
assert token.startswith('openshell:resolve:env:')
url = 'ws://172.18.0.1:18810/devtools/browser/homecompute'
home = pathlib.Path('/sandbox/.hermes')
path = home / '.env'
lines = [line for line in path.read_text().splitlines() if not line.startswith('BROWSER_CDP_URL=')]
lines.append('BROWSER_CDP_URL=' + url)
path.write_text('\\n'.join(lines) + '\\n'); path.chmod(0o640)
path = home / 'config.yaml'
config = yaml.safe_load(path.read_text()) or {}
config.setdefault('browser', {})['cdp_url'] = url
# Keep the pinned bundled browser tools; avoid auto-installing Browser Use.
config['browser'].pop('backend', None)
config['browser']['allow_private_urls'] = False
path.write_text(yaml.safe_dump(config, sort_keys=False))
print('Browser configured with an opaque gateway credential reference')
'''
    call(['nemohermes', NAME, 'exec', '--stdin', '--', 'python3', '-'], input=program)
    call(['nemohermes', NAME, 'stop'])
    call(['nemohermes', NAME, 'start'])
    print('Dedicated browser connected; public-page and private-network denial probes remain required.')


if __name__ == '__main__':
    main()
