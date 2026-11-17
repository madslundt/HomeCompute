#!/usr/bin/env python3
"""Stage a pinned extension outside OpenShell and apply it via native exec."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / 'config/hermes-offline-extensions.json'
PAYLOAD = ROOT / 'deploy/hermes/offline/install.py'
REMOTE = '/sandbox/.local/share/homecompute/offline-extensions'
MAX_ARCHIVE = 8 * 1024 * 1024


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fetch(asset, pin):
    if asset.exists() or asset.is_symlink():
        raise ValueError('asset already exists')
    url = 'https://codeload.github.com/' + pin['repo'] + '/tar.gz/' + pin['commit']
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != pin['sha256']:
        raise ValueError('download differs from the pinned archive')
    fd = os.open(asset, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def qualified_runtime(addons, sandbox):
    cli = ['nemohermes', sandbox['sandbox']]
    state = json.loads(addons.call(cli + ['status', '--json']))
    if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    ids = addons.call(['docker', 'ps', '--filter', 'name=openshell-default--' + sandbox['sandbox'] + '-',
                       '--format', '{{.ID}}']).decode().splitlines()
    if len(ids) != 1:
        raise ValueError('one running owner runtime required')
    image = addons.call(['docker', 'inspect', ids[0], '--format', '{{.Image}}']).decode().strip()
    if image != sandbox['runtime_image_id']:
        raise ValueError('owner image differs from the qualified image')
    return cli


def payload():
    return (
        'import sys, types\n'
        'omh_config = types.ModuleType("omh_config")\n'
        'sys.modules["omh_config"] = omh_config\n'
        'exec(' + repr((ROOT / 'deploy/hermes/omh/configure.py').read_text()) + ', omh_config.__dict__)\n'
        + PAYLOAD.read_text() + '\n'
        'import sys\n'
        'r = json.load(sys.stdin)\n'
        'try:\n'
        '    print(json.dumps(run(Path("/sandbox/.hermes"), Path(r["archive"]), r["name"],\n'
        '                         r["pin"], r["revision"], r["operation"])))\n'
        'except Exception as exc:\n'
        '    print("offline extension failed: " + type(exc).__name__, file=sys.stderr)\n'
        '    raise SystemExit(1)\n'
    )


def main():
    manifest = json.loads(PINS.read_bytes())
    if manifest['schema_version'] != 1:
        raise ValueError('unknown extension manifest schema')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('fetch', 'plan', 'apply', 'verify', 'rollback'))
    parser.add_argument('extension', choices=manifest['extensions'])
    parser.add_argument('--assets', type=Path)
    parser.add_argument('--revision')
    args = parser.parse_args()
    pin = manifest['extensions'][args.extension]
    asset_name = args.extension + '-' + pin['sha256'] + '.tar.gz'
    asset = args.assets / asset_name if args.assets else None
    if args.operation == 'fetch':
        if not asset:
            parser.error('fetch requires --assets')
        fetch(asset, pin)
        print(asset)
        return

    addons = module(ROOT / 'scripts/setup-hermes-addons.py')
    if args.operation in ('plan', 'apply'):
        if not asset:
            parser.error('plan and apply require --assets')
        addons.private(asset)
        module(PAYLOAD).checked_archive(asset, pin)
    if not args.revision:
        parser.error('guest operations require --revision')
    module(ROOT / 'scripts/setup-hermes-mcp.py').published(args.revision)
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain']).strip():
        raise ValueError('clean published release required')
    sandbox = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())
    cli = qualified_runtime(addons, sandbox)
    if args.operation in ('plan', 'apply'):
        module(ROOT / 'scripts/connect-hermes-mcp.py').gates()
    if args.operation == 'plan':
        print(json.dumps({'revision': args.revision, 'sandbox': sandbox['sandbox'],
                          'extension': args.extension, 'commit': pin['commit'], 'applied': False}))
        return

    if args.operation == 'apply':
        addons.upload_asset(cli, asset, pin['sha256'], remote=REMOTE)
    request = {'name': args.extension, 'revision': args.revision, 'pin': pin,
               'archive': REMOTE + '/' + asset_name, 'operation': args.operation}
    try:
        response = addons.call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python',
                                      '-c', payload()], json.dumps(request).encode())
    except ValueError as error:
        raise ValueError('offline extension operation held; check installed tree and private receipt') from error
    print(response.decode().strip())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
