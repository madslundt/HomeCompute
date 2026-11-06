#!/usr/bin/env python3
"""Install pinned OMH and native dashboard authentication into the owner sandbox.

Run as the guest operator from a clean published release. Assets are transferred
through native exec stdin; no Docker mutation, private key argv or runtime rebuild.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/sandbox/.local/share/homecompute/omh'
ENV = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
       'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}


def call(args, data=None, timeout=300):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=ENV, timeout=timeout)
    if result.returncode:
        raise ValueError('guarded operation failed: ' + Path(args[0]).name + '; inspect private native state')
    return result.stdout


def private(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('real private path required')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private operator-owned regular file required')


def upload_asset(cli, path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('pinned asset changed before transfer')
    # Stay below both the 4 MiB stdin budget and the qualified gateway's
    # 1 MiB gRPC message limit on every
    # native exec; bounded, hashed parts are assembled only after verification.
    parts = []
    for index, offset in enumerate(range(0, len(data), 512 * 1024)):
        chunk = data[offset:offset + 512 * 1024]
        name = path.name + '.part-' + str(index)
        digest = hashlib.sha256(chunk).hexdigest()
        program = '''import hashlib,os,pathlib,sys
p=pathlib.Path(sys.argv[1]);data=sys.stdin.buffer.read(524289)
assert len(data)<=524288 and hashlib.sha256(data).hexdigest()==sys.argv[2]
assert not any(x.is_symlink() for x in (p,*p.parents))
p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
assert p.parent.stat().st_uid==os.getuid() and not p.parent.stat().st_mode&0o077
assert not p.exists() or p.read_bytes()==data
if not p.exists():
 with p.open('xb') as f:os.chmod(p,0o600);f.write(data)
'''
        call(cli + ['exec', '--stdin', '--', 'python3', '-c', program, REMOTE + '/' + name, digest], chunk)
        parts.append({'name': name, 'sha256': digest})
    program = '''import hashlib,json,os,pathlib,sys
p=pathlib.Path(sys.argv[1]);parts=json.loads(sys.argv[3]);blocks=[]
assert not any(x.is_symlink() for x in (p,*p.parents))
for part in parts:
 q=p.parent/part['name'];assert q.parent==p.parent and not q.is_symlink()
 block=q.read_bytes();assert hashlib.sha256(block).hexdigest()==part['sha256'];blocks.append(block)
data=b''.join(blocks);assert hashlib.sha256(data).hexdigest()==sys.argv[2]
assert not p.exists() or p.read_bytes()==data
if not p.exists():
 with p.open('xb') as f:os.chmod(p,0o600);f.write(data)
'''
    call(cli + ['exec', '--', 'python3', '-c', program, REMOTE + '/' + path.name,
                expected, json.dumps(parts)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('setup_mcp', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    core.published(args.revision)
    if call(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no']).strip():
        raise ValueError('clean published release required')
    spec = importlib.util.spec_from_file_location('connect_mcp', ROOT / 'scripts/connect-hermes-mcp.py')
    gates = importlib.util.module_from_spec(spec); spec.loader.exec_module(gates)
    gates.gates()
    pin = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())
    cli = ['nemohermes', pin['sandbox']]
    state = json.loads(call(cli + ['status', '--json']))
    if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    rows = [json.loads(line) for line in call(['docker', 'ps', '--filter', 'name=openshell-default--' + pin['sandbox'] + '-', '--format', '{{json .}}']).splitlines()]
    if len(rows) != 1:
        raise ValueError('one owner runtime required')
    image = call(['docker', 'inspect', rows[0]['ID'], '--format', '{{.Image}}']).decode().strip()
    if image != pin['runtime_image_id']:
        raise ValueError('qualified household image required')
    assets = {pin['wheel']['name']: pin['wheel']['sha256'], 'install.sh': pin['installer_sha256']}
    for name, digest in assets.items():
        path = args.assets / name
        private(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('pinned asset mismatch')
    if not args.apply:
        print(json.dumps({'revision': args.revision, 'sandbox': pin['sandbox'], 'commit': pin['commit'],
                          'dashboard': pin['dashboard_url'], 'applied': False})); return
    directory = Path.home() / '.config/homecompute-hermes/secrets'
    if directory.is_symlink() or directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid():
        raise ValueError('existing private secrets directory required')
    with (directory / 'addons.lock').open('a') as lock:
        os.chmod(directory / 'addons.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            # Removing dashboard auth while retaining its relay would expose a
            # loopback-only server. Close this hop before restoring the config.
            call(['sudo', '-n', 'systemctl', 'disable', '--now', 'homecompute-hermes-dashboard-relay.service'])
            payload = (ROOT / 'deploy/hermes/omh/configure.py').read_text()
            report = json.loads(call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', payload],
                                     json.dumps({'pin': pin, 'rollback': True}).encode()))
            call(cli + ['stop']); call(cli + ['start'])
            print(json.dumps({**report, 'revision': args.revision})); return
        credential = directory / 'dashboard-login.json'
        if not credential.exists():
            with credential.open('x') as stream:
                os.chmod(credential, 0o600)
                json.dump({'url': pin['dashboard_url'], 'username': pin['dashboard_username'],
                           'password': secrets.token_urlsafe(24), 'secret': secrets.token_urlsafe(48)}, stream)
        private(credential)
        secret = json.loads(credential.read_bytes())
        if secret['url'] != pin['dashboard_url'] or secret['username'] != pin['dashboard_username']:
            raise ValueError('dashboard credential identity differs')
        for name, digest in assets.items():
            upload_asset(cli, args.assets / name, digest)
        payload = (ROOT / 'deploy/hermes/omh/configure.py').read_text()
        report = json.loads(call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', payload],
                                 json.dumps({'pin': pin, 'dashboard': secret}).encode()))
        secret.clear()
        call(cli + ['stop'])
        call(cli + ['start'])
        # Native lifecycle owns all processes, inference handles and forwarding.
        report.update(revision=args.revision, credential_file=str(credential), restart='native')
        print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
