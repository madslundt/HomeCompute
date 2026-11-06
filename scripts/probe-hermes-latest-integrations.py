#!/usr/bin/env python3
"""Probe only the fixed upgrade sandbox; never start a second Mattermost bot."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = 'hermes-v023-verify'
OS = ['openshell', '-g', 'nemoclaw']
PYTHON = '/opt/hermes/.venv/bin/python'


def call(args, *, program=None):
    result = subprocess.run(args, input=program, capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise RuntimeError('Upgrade probe step failed; private command output withheld: ' + ' '.join(args[:3]))
    return result.stdout


def execute(program):
    return call(['nemohermes', NAME, 'exec', '--stdin', '--', PYTHON, '-'], program=program)


def main():
    # The guarded shell entrypoint validates the owner config and readiness gates.
    if os.environ.get('OPENSHELL_GATEWAY') != 'nemoclaw' or os.environ.get('NEMOCLAW_GATEWAY_PORT') != '8080':
        raise RuntimeError('Use setup-hermes-guest.sh probe-latest with the protected owner config')
    status = json.loads(call(['nemohermes', NAME, 'status', '--json']))
    if status.get('agent') != 'hermes' or status.get('phase') != 'Ready':
        raise RuntimeError('Ready synthetic upgrade sandbox required')
    stamp = json.loads(execute("import json; print(open('/opt/hermes/install-stamp.json').read())"))
    expected = json.loads((ROOT / 'deploy/hermes/latest/qualification.json').read_text())['upstream']
    if stamp.get('commit') != expected['commit'] or stamp.get('baseVersion') != expected['version']:
        raise RuntimeError('Pinned candidate runtime required')
    browser = json.loads(call(['docker', 'inspect', 'homecompute-hermes-browser']))[0]
    release = json.loads((ROOT / 'config/hermes-release.json').read_text())
    if not browser['State']['Running'] or browser['Image'] != release['browser_worker_image_id']:
        raise RuntimeError('Qualified dedicated Hermes browser required')
    for kind in ('mattermost', 'browser'):
        expected_profile = json.loads((ROOT / f'deploy/hermes/{kind}-provider.yaml').read_text())
        profile = json.loads(call(OS + ['provider', 'profile', 'export', expected_profile['id'], '-o', 'json']))
        if {key: profile.get(key) for key in expected_profile} != expected_profile:
            raise RuntimeError('Reviewed provider profile required')
        provider = 'hermes-personal-' + kind
        metadata = call(OS + ['provider', 'get', provider])
        if expected_profile['id'] not in metadata:
            raise RuntimeError('Reviewed existing provider required')
        call(OS + ['sandbox', 'provider', 'attach', NAME, provider])
        policy = 'latest/mattermost-probe-policy.yaml' if kind == 'mattermost' else 'browser-policy.yaml'
        host = 'home-core.tail479ad.ts.net' if kind == 'mattermost' else '172.18.0.1'
        call(['nemohermes', NAME, 'policy', 'add', '--from-file', str(ROOT / 'deploy/hermes' / policy),
              '--trusted-private-host', host, '--yes'])
    execute('''import pathlib, yaml
home = pathlib.Path('/sandbox/.hermes')
path = home / 'config.yaml'
config = yaml.safe_load(path.read_text()) or {}
config.setdefault('platforms', {}).setdefault('mattermost', {})['enabled'] = False
config.setdefault('agent', {})['reasoning_effort'] = 'medium'
browser = config.setdefault('browser', {})
browser['cdp_url'] = 'ws://172.18.0.1:18810/devtools/browser/homecompute'
browser['allow_private_urls'] = False
browser.pop('backend', None)
path.write_text(yaml.safe_dump(config, sort_keys=False))
path = home / '.env'
lines = [s for s in path.read_text().splitlines() if not s.startswith(('MATTERMOST_', 'BROWSER_CDP_URL='))]
lines.append('BROWSER_CDP_URL=ws://172.18.0.1:18810/devtools/browser/homecompute')
path.write_text('\\n'.join(lines) + '\\n'); path.chmod(0o640)
print('Synthetic candidate browser configured; Mattermost listener disabled')
''')
    call(['nemohermes', NAME, 'stop'])
    call(['nemohermes', NAME, 'start'])
    for filename in ('probe_inference.py', 'probe_mattermost.py'):
        print(execute((ROOT / 'deploy/hermes' / filename).read_text()).strip())
    print(execute('''import json
from tools.browser_use_cli import browser_exec, stop_harness_daemons
session = 'hermes-v023-qualification'
try:
    result = json.loads(browser_exec('new_tab("https://example.com/"); print(page_info())', session=session, timeout_s=60, task_id=session))
    assert result.get('success') and 'Example Domain' in result.get('output', ''), 'Public page probe failed'
    denied = json.loads(browser_exec('new_tab("http://192.168.30.30/")', session=session, timeout_s=10, task_id=session))
    assert 'private' in denied.get('error', '').lower(), 'Private destination admitted'
    browser_exec('close_tab()', session=session, timeout_s=10, task_id=session)
    print('{"public_browsing":true,"private_browsing_denied":true,"tool":"browser_exec"}')
finally:
    stop_harness_daemons()
''').strip())
    print('Candidate inference, authentication, browser and native restart probes passed; owner DM and boot remain required.')


if __name__ == '__main__':
    main()
