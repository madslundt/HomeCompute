#!/usr/bin/env python3
"""Plan or install Home Planner and Researcher on the isolated owner runtime."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BOTS = ROOT / 'deploy/hermes/bots'
SECRETS = Path.home() / '.config/homecompute-hermes/secrets'


def desired():
    roster = json.loads((ROOT / 'config/hermes-desktop-bots.json').read_bytes())
    if (roster['endpoint'] != 'http://ai.home.arpa:18080/v1'
            or [(bot['id'], bot['model']) for bot in roster['bots']]
            != [('home-planner', 'automation-moe'), ('researcher', 'chatgpt-sol-latest')]):
        raise ValueError('unexpected bot identities or model routes')
    for bot in roster['bots']:
        bot['soul'] = (BOTS / f"{bot['id']}.md").read_text()
    return roster


def sandbox_cli(call):
    pin = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())
    cli = ['nemohermes', pin['sandbox']]
    state = json.loads(call(cli + ['status', '--json']))
    if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    containers = call(['docker', 'ps', '--filter', 'name=openshell-default--' + pin['sandbox'] + '-',
                       '--format', '{{.ID}}']).decode().splitlines()
    if (len(containers) != 1 or call(['docker', 'inspect', containers[0], '--format', '{{.Image}}'])
            .decode().strip() != pin['runtime_image_id']):
        raise ValueError('qualified owner Hermes image required')
    return cli


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    runpy.run_path(str(ROOT / 'scripts/setup-hermes-mcp.py'))['published'](args.revision)
    call = runpy.run_path(str(ROOT / 'scripts/setup-hermes-addons.py'))['call']
    if call(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']).strip():
        raise ValueError('clean published release required')
    if args.apply:
        runpy.run_path(str(ROOT / 'scripts/connect-hermes-mcp.py'))['gates']()

    cli = sandbox_cli(call)
    program = (BOTS / 'install.py').read_text()
    command = cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', program]
    roster = desired()
    observed = json.loads(call(command, json.dumps(roster).encode()))
    plan = {'revision': args.revision, 'sandbox': cli[1], 'profiles': observed,
            'source_sha256': hashlib.sha256(json.dumps(roster, sort_keys=True).encode()).hexdigest()}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    if args.apply:
        if args.expected_plan_sha256 != digest:
            raise ValueError('reviewed --expected-plan-sha256 required for this plan')
        read_key = runpy.run_path(str(ROOT / 'scripts/setup-hermes-chatgpt-profiles.py'))['private_key']
        keys = {bot['id']: read_key(SECRETS / f"desktop-{bot['id']}") for bot in roster['bots']}
        if len(set(keys.values())) != len(keys):
            raise ValueError('separate model credentials required for each bot')
        result = json.loads(call(command, json.dumps({**roster, 'expected': observed, 'keys': keys}).encode()))
        print(json.dumps({'revision': args.revision, 'profiles': result}, sort_keys=True))
    else:
        print(json.dumps({**plan, 'plan_sha256': digest}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(2)
