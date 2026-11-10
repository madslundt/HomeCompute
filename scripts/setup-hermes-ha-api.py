#!/usr/bin/env python3
"""Enable the narrowly routed Home Assistant API on the selected Hermes profile."""
from __future__ import annotations

import argparse
import fcntl
import http.client
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NAME = 'hermes-personal-v23'
KEY_PATH = Path.home() / '.config/homecompute-hermes/secrets/hermes-api-server-default'
STATE = Path.home() / '.local/state/homecompute/hermes-ha-api'
ENV = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
       'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
SNAPSHOT_PROGRAM = r'''import hashlib,json,pathlib
home=pathlib.Path('/sandbox/.hermes'); c=home/'config.yaml'; e=home/'.env'
assert not any(p.is_symlink() for p in (home,c,e)) and c.is_file() and e.is_file()
cb=c.read_bytes(); eb=e.read_bytes()
print(json.dumps({'config_hex':cb.hex(),'env_hex':eb.hex(),
 'config_sha256':hashlib.sha256(cb).hexdigest(),'env_sha256':hashlib.sha256(eb).hexdigest(),
 'config_mode':c.stat().st_mode & 0o777,'env_mode':e.stat().st_mode & 0o777}))
'''

REMOTE_PROGRAM = r'''import hashlib,json,os,pathlib,sys,yaml
payload=json.load(sys.stdin); home=pathlib.Path('/sandbox/.hermes')
config=home/'config.yaml'; envfile=home/'.env'
assert not any(p.is_symlink() for p in (home,config,envfile))
before_config=config.read_bytes(); before_env=envfile.read_bytes()
assert hashlib.sha256(before_config).hexdigest()==payload['config_sha256']
assert hashlib.sha256(before_env).hexdigest()==payload['env_sha256']
c=yaml.safe_load(before_config) or {}; gateway=c.setdefault('gateway',{})
api=gateway.setdefault('api_server',{})
assert api.get('host','127.0.0.1') in ('127.0.0.1','localhost')
assert int(api.get('port',8642)) == 8642
api.update({'enabled':True,'host':'127.0.0.1','port':8642})
gateway['multiplex_profiles']=True
lines=[line for line in before_env.decode().splitlines() if not line.startswith('API_SERVER_KEY=')]
lines.append('API_SERVER_KEY='+payload['key'])
after_config=yaml.safe_dump(c,sort_keys=False).encode(); after_env=('\n'.join(lines)+'\n').encode()
assert payload['key'] and len(payload['key']) == 64 and payload['key'].isascii()
config.write_bytes(after_config); os.chmod(config,0o600)
envfile.write_bytes(after_env); os.chmod(envfile,0o600)
print(json.dumps({'config_sha256':hashlib.sha256(after_config).hexdigest(),
 'env_sha256':hashlib.sha256(after_env).hexdigest(),'api_enabled':True,
 'host':'127.0.0.1','port':8642,'multiplex_profiles':True}))
'''

ROLLBACK_PROGRAM = r'''import hashlib,json,os,pathlib,sys
p=json.load(sys.stdin); h=pathlib.Path('/sandbox/.hermes'); c=h/'config.yaml'; e=h/'.env'
assert not any(x.is_symlink() for x in (h,c,e))
assert hashlib.sha256(c.read_bytes()).hexdigest()==p['applied_config_sha256']
assert hashlib.sha256(e.read_bytes()).hexdigest()==p['applied_env_sha256']
cb=bytes.fromhex(p['config_hex']); eb=bytes.fromhex(p['env_hex'])
c.write_bytes(cb); os.chmod(c,p['config_mode']); e.write_bytes(eb); os.chmod(e,p['env_mode'])
print(json.dumps({'rolled_back':True}))
'''


def call(args: list[str], *, data: bytes | None = None, timeout: int = 120) -> bytes:
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=ENV, timeout=timeout)
    if result.returncode:
        # The remote CLI can include diagnostics; never print them because they
        # may contain configuration details. Preserve them only in guest state.
        raise ValueError('guarded Hermes API operation failed; inspect private guest state')
    return result.stdout


def private_file(path: Path) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('real private key path required')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('API key file must be operator-owned mode 0600')
    value = path.read_bytes().strip()
    if len(value) != 64 or any(ch not in b'0123456789abcdef' for ch in value):
        raise ValueError('API key must be 32 random bytes encoded as lowercase hex')
    return value


def private_state_file(path: Path, *, required: bool = True) -> bytes | None:
    if path.is_symlink():
        raise ValueError('real private transaction file required')
    if not path.exists():
        if required:
            raise ValueError('private API transaction file is missing')
        return None
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('private API transaction file must be operator-owned mode 0600')
    return path.read_bytes()


def write_private_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    mode = 'x' if exclusive else 'w'
    with path.open(mode) as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())


def remote_json(program: str, payload: dict) -> dict:
    result = call(['nemohermes', NAME, 'exec', '--stdin', '--', 'python3', '-c', program],
                  data=json.dumps(payload).encode(), timeout=120)
    return json.loads(result)


def install_key_in_sandbox(key: bytes, before: dict) -> dict:
    payload = {'key': key.decode('ascii'), 'config_sha256': before['config_sha256'],
               'env_sha256': before['env_sha256']}
    return remote_json(REMOTE_PROGRAM, payload)


def rollback_sandbox(before: dict, receipt: dict) -> dict:
    payload = {'config_hex': before['config_hex'], 'env_hex': before['env_hex'],
               'applied_config_sha256': receipt['applied_config_sha256'],
               'applied_env_sha256': receipt['applied_env_sha256']}
    return remote_json(ROLLBACK_PROGRAM, payload)


def update_watcher(revision: str) -> None:
    unit = Path.home() / '.config/systemd/user/homecompute-hermes-dashboard-watch.service'
    source = (ROOT / 'deploy/hermes/hermes-dashboard-watch.service.in').read_text()
    rendered = source.replace('@RELEASE@', str(ROOT)).replace('@OPERATOR_HOME@', str(Path.home()))
    if unit.is_symlink():
        raise ValueError('unknown Hermes watcher symlink')
    if unit.exists() and unit.read_text() != rendered:
        # The previous unit must be an exact render from the source revision it
        # names. Keep a private before-file before installing this revision.
        current = unit.read_text()
        marker = '/.local/share/homecompute/releases/'
        if marker not in current or not current.endswith('/scripts/recover-hermes-dashboard.py --watch\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=15\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n'):
            raise ValueError('existing Hermes watcher differs from its source template')
        prior = current.split(marker, 1)[1].split('/', 1)[0]
        if len(prior) != 40 or any(ch not in '0123456789abcdef' for ch in prior):
            raise ValueError('existing Hermes watcher release is invalid')
        old_root = Path.home() / '.local/share/homecompute/releases' / prior
        old_source = old_root / 'deploy/hermes/hermes-dashboard-watch.service.in'
        if not old_source.is_file():
            raise ValueError('prior Hermes watcher source is unavailable')
        old_rendered = old_source.read_text().replace('@RELEASE@', str(old_root)).replace(
            '@OPERATOR_HOME@', str(Path.home()))
        if current != old_rendered:
            raise ValueError('existing Hermes watcher has unreviewed edits')
        backup = STATE / 'watcher-before.service'
        if not backup.exists():
            backup.write_text(current); backup.chmod(0o600)
    unit.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if unit.exists() and unit.read_text() == rendered:
        return
    unit.write_text(rendered); unit.chmod(0o644)
    subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'enable', '--now', unit.name], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'restart', unit.name], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def verify_api_profile(key: bytes) -> None:
    """Verify auth and profile multiplexing without printing response data."""
    headers = {'Authorization': 'Bearer ' + key.decode('ascii')}
    connection = http.client.HTTPConnection('127.0.0.1', 18642, timeout=15)
    try:
        connection.request('GET', '/p/default/v1/models', headers=headers)
        response = connection.getresponse()
        payload = response.read(65537)
        if response.status != 200 or len(payload) > 65536:
            raise ValueError('authenticated default Hermes profile probe failed')
        models = json.loads(payload).get('data')
        if not isinstance(models, list) or not models:
            raise ValueError('default Hermes profile did not advertise a model')
    finally:
        connection.close()
    connection = http.client.HTTPConnection('127.0.0.1', 18642, timeout=15)
    try:
        connection.request('GET', '/p/hermes-conversation-probe-does-not-exist/v1/models',
                           headers=headers)
        response = connection.getresponse()
        response.read(65537)
        if response.status != 404:
            raise ValueError('Hermes profile multiplexing probe failed')
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--refresh-watcher', action='store_true',
                        help='refresh only the source-owned dashboard watcher unit')
    parser.add_argument('--key-file', type=Path, default=KEY_PATH)
    args = parser.parse_args()
    if args.refresh_watcher and args.rollback:
        raise ValueError('watcher refresh cannot be combined with rollback')
    if args.apply and args.rollback:
        raise ValueError('select apply or rollback')
    if len(args.revision) != 40 or any(c not in '0123456789abcdef' for c in args.revision):
        raise ValueError('full release revision required')
    if args.refresh_watcher and not args.apply:
        print(json.dumps({'revision': args.revision, 'watcher_only': True,
                          'dependency': 'Wants=homecompute-hermes-personal.service',
                          'applied': False}))
        return
    if not args.apply and not args.rollback:
        print(json.dumps({'revision': args.revision, 'sandbox': NAME,
                          'api_host': '127.0.0.1', 'api_port': 8642,
                          'profile': 'default', 'applied': False}))
        return
    spec = importlib.util.spec_from_file_location('hermes_setup_core', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    core.published(args.revision)
    status = json.loads(call(['nemohermes', NAME, 'status', '--json']))
    if status.get('phase') != 'Ready' or status.get('agent') != 'hermes' or status.get('hostMounts'):
        raise ValueError('ready isolated owner sandbox required')
    if args.refresh_watcher:
        update_watcher(args.revision)
        call(['systemctl', '--user', 'reset-failed',
              'homecompute-hermes-dashboard-watch.service'])
        call(['systemctl', '--user', 'restart',
              'homecompute-hermes-dashboard-watch.service'])
        print(json.dumps({'revision': args.revision, 'watcher_only': True,
                          'applied': True, 'owner_sandbox_restarted': False}))
        return
    key = private_file(args.key_file) if args.apply else b''
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_info = STATE.lstat()
    if (stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode)
            or state_info.st_uid != os.getuid() or stat.S_IMODE(state_info.st_mode) != 0o700):
        raise ValueError('private Hermes API state directory required')
    lock_path = STATE / 'install.lock'
    with lock_path.open('a') as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before_path = STATE / 'before.json'
        receipt_path = STATE / 'receipt.json'
        if args.apply:
            existing_before = private_state_file(before_path, required=False)
            if existing_before is not None:
                existing_receipt = private_state_file(receipt_path)
                if existing_receipt is None:
                    raise ValueError('existing private API transaction needs reconciliation')
                receipt = json.loads(existing_receipt)
                current = remote_json(SNAPSHOT_PROGRAM, {})
                if (receipt.get('state') != 'applied'
                        or current['config_sha256'] != receipt.get('applied_config_sha256')
                        or current['env_sha256'] != receipt.get('applied_env_sha256')):
                    raise ValueError('existing API transaction differs; preserve and reconcile it')
                update_watcher(args.revision)
                verify_api_profile(key)
                print(json.dumps({'revision': args.revision, 'sandbox': NAME, 'profile': 'default',
                                  'api_host': '127.0.0.1', 'api_port': 8642,
                                  'credential_configured': True, 'applied': True,
                                  'already_applied': True}))
                return
            before = remote_json(SNAPSHOT_PROGRAM, {})
            write_private_json(before_path, before, exclusive=True)
            result = install_key_in_sandbox(key, before)
            receipt = {'revision': args.revision, 'api_host': '127.0.0.1', 'api_port': 8642,
                       'profile': 'default', 'applied_config_sha256': result['config_sha256'],
                       'applied_env_sha256': result['env_sha256'],
                       'applied_at': int(time.time()), 'state': 'applied'}
            write_private_json(receipt_path, receipt)
        else:
            before_raw = private_state_file(before_path)
            receipt_raw = private_state_file(receipt_path)
            before = json.loads(before_raw)
            receipt = json.loads(receipt_raw)
            if receipt.get('state') != 'applied':
                raise ValueError('API rollback is already complete or not applied')
            result = rollback_sandbox(before, receipt)
        call(['nemohermes', NAME, 'stop'], timeout=180)
        call(['nemohermes', NAME, 'start'], timeout=300)
        marker = Path.home() / '.config/homecompute-hermes/hermes-ha-api-enabled'
        if args.apply:
            marker.write_text('enabled\n'); marker.chmod(0o600)
            update_watcher(args.revision)
        else:
            marker.unlink(missing_ok=True)
            update_watcher(args.revision)
        call(['python3', str(ROOT / 'scripts/recover-hermes-dashboard.py')], timeout=180)
        if args.apply:
            verify_api_profile(key)
        if args.rollback:
            receipt.update({'state': 'rolled-back', 'rolled_back_at': int(time.time())})
            write_private_json(receipt_path, receipt)
        print(json.dumps({'revision': args.revision, 'sandbox': NAME, 'profile': 'default',
                          'api_host': '127.0.0.1', 'api_port': 8642,
                          'credential_configured': args.apply, 'applied': args.apply,
                          'rolled_back': args.rollback}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('Hermes HA API setup held (' + type(error).__name__ + '); preserve private state.', file=__import__('sys').stderr)
        raise SystemExit(2)
