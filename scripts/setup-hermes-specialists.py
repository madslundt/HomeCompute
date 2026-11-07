#!/usr/bin/env python3
"""Plan, install or remove the owner Hermes specialist skills from published source."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'deploy/hermes/skills'
NAMES = ('day-plan', 'follow-through', 'home-ops')
INSPECT = '''
import hashlib,json,pathlib,sys
root = pathlib.Path('/sandbox/.hermes/skills')
if any(path.is_symlink() for path in (root, *root.parents)):
    raise ValueError('unexpected skills root')
state = {}
for name in sys.argv[1:]:
    directory = root / name
    path = directory / 'SKILL.md'
    if directory.is_symlink():
        state[name] = 'conflict'
    elif not directory.exists():
        state[name] = 'absent'
    elif (directory.is_dir() and set(directory.iterdir()) == {path}
          and not path.is_symlink() and path.is_file()
          and path.stat().st_nlink == 1 and path.stat().st_size <= 16384):
        state[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        state[name] = 'conflict'
print(json.dumps(state))
'''


def source_hashes():
    hashes = {}
    for name in NAMES:
        directory = SKILLS / name
        path = directory / 'SKILL.md'
        if directory.is_symlink() or path.is_symlink() or set(directory.iterdir()) != {path}:
            raise ValueError(f'unexpected source layout for {name}')
        data = path.read_bytes()
        if not data.startswith(f'---\nname: {name}\n'.encode()) or len(data) > 16384:
            raise ValueError(f'unexpected skill metadata for {name}')
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def reconcile(call, cli, expected, operation):
    def inspect(names):
        state = json.loads(call(cli + ['exec', '--', 'python3', '-c', INSPECT, *names]))
        for name in names:
            if state[name] not in ('absent', expected[name]):
                raise ValueError(f'existing {name} differs from source; preserve it for review')
        return state

    before = inspect(expected)
    if operation == 'plan':
        return before
    for name, digest in expected.items():
        wanted = digest if operation == 'apply' else 'absent'
        if inspect([name])[name] == wanted:
            continue
        arguments = ['install', str(SKILLS / name)] if operation == 'apply' else ['remove', name]
        call(cli + ['skill', *arguments])
        if inspect([name])[name] != wanted:
            raise ValueError(f'native skill readback differs for {name}')
    after = inspect(expected)
    wanted = expected if operation == 'apply' else dict.fromkeys(expected, 'absent')
    if after != wanted:
        raise ValueError('final specialist readback differs')
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    operation = 'apply' if args.apply else 'rollback' if args.rollback else 'plan'

    runpy.run_path(str(ROOT / 'scripts/setup-hermes-mcp.py'))['published'](args.revision)
    call = runpy.run_path(str(ROOT / 'scripts/setup-hermes-addons.py'))['call']
    if call(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no']).strip():
        raise ValueError('clean published release required')
    runpy.run_path(str(ROOT / 'scripts/connect-hermes-mcp.py'))['gates']()
    pin = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())
    sandbox = pin['sandbox']
    cli = ['nemohermes', sandbox]
    state = json.loads(call(cli + ['status', '--json']))
    if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    containers = call(['docker', 'ps', '--filter', 'name=openshell-default--' + sandbox + '-',
                       '--format', '{{.ID}}']).decode().splitlines()
    if len(containers) != 1:
        raise ValueError('one owner runtime required')
    image = call(['docker', 'inspect', containers[0], '--format', '{{.Image}}']).decode().strip()
    if image != pin['runtime_image_id']:
        raise ValueError('qualified owner image required')
    expected = source_hashes()
    observed = reconcile(call, cli, expected, operation)
    print(json.dumps({'revision': args.revision, 'sandbox': sandbox, 'operation': operation,
                      'source_sha256': expected, 'observed': observed}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        print('Hermes specialist state is unverified. Inspect the plan before retrying.', file=sys.stderr)
        raise SystemExit(2)
