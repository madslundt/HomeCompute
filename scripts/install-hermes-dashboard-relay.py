#!/usr/bin/env python3
"""Install the fixed guest relay only after native public authentication is ready."""
import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UNIT = 'homecompute-hermes-dashboard-relay.service'
LIB = Path('/usr/local/lib/homecompute-hermes-dashboard')
STATE = Path('/var/lib/homecompute-hermes-dashboard-relay')
UNIT_PATH = Path('/etc/systemd/system') / UNIT
LEGACY_START = ('ExecStart=/usr/bin/socat TCP4-LISTEN:18804,bind=10.77.20.2,reuseaddr,fork '
                'TCP4:127.0.0.1:18794')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def protected(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('root-protected regular file required')
    return path.read_bytes()


def directory(path, mode=0o700):
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('root-protected directory required')
        else:
            parent.mkdir(mode=mode)


def replace(path, content, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix='.relay-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render(revision):
    return (ROOT / 'deploy/hermes/hermes-dashboard-relay.service').read_bytes().replace(
        b'@RELAY@', str(LIB / revision / 'dashboard-relay.py').encode())


def known_unit(raw):
    template = (ROOT / 'deploy/hermes/hermes-dashboard-relay.service').read_bytes()
    legacy = template.replace(b'ExecStart=/usr/bin/python3 @RELAY@', LEGACY_START.encode())
    legacy = legacy.replace(b'MemoryMax=128M\nTasksMax=32\n', b'')
    if raw == legacy:
        return True
    match = re.search(rb'ExecStart=/usr/bin/python3 ' + re.escape(str(LIB).encode())
                      + rb'/([0-9a-f]{40})/dashboard-relay.py\n', raw)
    return match is not None and raw == render(match[1].decode())


def plan(revision):
    before = protected(UNIT_PATH) if UNIT_PATH.exists() or UNIT_PATH.is_symlink() else None
    if before is not None and not known_unit(before):
        raise ValueError('unknown relay unit must be reconciled explicitly')
    source = (ROOT / 'deploy/hermes/dashboard-relay.py').read_bytes()
    after = render(revision)
    report = {'revision': revision, 'unit_before_sha256': digest(before) if before else None,
              'unit_after_sha256': digest(after), 'relay_sha256': digest(source)}
    return report, before, after, source


def check_auth():
    # Use the same readiness gate as the installed relay, including bounded
    # retries of the anonymous probe before any browser request is forwarded.
    import asyncio
    spec = importlib.util.spec_from_file_location('relay', ROOT / 'deploy/hermes/dashboard-relay.py')
    relay = importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
    async def probe():
        _, writer = await relay.ready(relay.PORT, 8, 0.1)
        await relay.close(writer)
    asyncio.run(probe())
    # The account provider must also be available; an uncertain check holds the
    # installation without changing the currently installed service.
    conn = http.client.HTTPConnection('127.0.0.1', 18794, timeout=10)
    try:
        conn.request('GET', '/api/auth/providers', headers={'Host': 'agents.home.arpa'})
        response = conn.getresponse(); body = response.read(65537)
        if response.status != 200 or len(body) > 65536 or b'"basic"' not in body:
            raise ValueError('native password provider must be available before exposure')
    finally:
        conn.close()


def activate():
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', UNIT], check=True)
    subprocess.run(['systemctl', 'restart', UNIT], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', UNIT], check=True)


def apply(revision, expected):
    report, before, after, source = plan(revision)
    if digest(json.dumps(report, sort_keys=True).encode()) != expected:
        raise ValueError('reviewed relay plan changed')
    check_auth()
    directory(STATE)
    directory(LIB / revision, 0o755)
    script = LIB / revision / 'dashboard-relay.py'
    if script.exists() or script.is_symlink():
        if protected(script) != source:
            raise ValueError('installed immutable relay differs')
    else:
        replace(script, source)
    if (STATE / 'receipt.json').exists():
        previous = json.loads(protected(STATE / 'receipt.json'))
        if (before == after and previous['plan']['unit_after_sha256'] == digest(after)
                and previous['plan']['relay_sha256'] == digest(source)):
            activate()
            return report  # Preserve the original rollback receipt on rerun.
    backup = STATE / str(time.time_ns())
    directory(backup)
    candidate = backup / UNIT
    replace(candidate, after)
    subprocess.run(['systemd-analyze', 'verify', str(candidate)], check=True, capture_output=True)
    if before is not None:
        replace(backup / 'unit.before', before, 0o600)
    if plan(revision)[1] != before:
        raise ValueError('relay unit changed during installation')
    try:
        replace(UNIT_PATH, after)
        activate()
    except BaseException:
        if before is not None:
            replace(UNIT_PATH, before)
            activate()
        else:
            subprocess.run(['systemctl', 'disable', '--now', UNIT], check=True)
            UNIT_PATH.unlink()
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
        raise
    receipt = {'plan': report, 'backup': str(backup), 'had_previous_unit': before is not None}
    replace(STATE / 'receipt.json', (json.dumps(receipt, indent=2) + '\n').encode(), 0o600)
    return report


def rollback():
    receipt = json.loads(protected(STATE / 'receipt.json'))
    if digest(protected(UNIT_PATH)) != receipt['plan']['unit_after_sha256']:
        raise ValueError('installed relay differs; rollback held')
    backup = Path(receipt['backup'])
    if backup.parent != STATE or not backup.name.isdigit():
        raise ValueError('managed backup required')
    if receipt['had_previous_unit']:
        before = protected(backup / 'unit.before')
        if digest(before) != receipt['plan']['unit_before_sha256']:
            raise ValueError('rollback preimage differs')
        replace(UNIT_PATH, before)
        activate()
    else:
        subprocess.run(['systemctl', 'disable', '--now', UNIT], check=True)
        UNIT_PATH.unlink()
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    return {'rolled_back': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or pwd.getpwnam('hermes-operator').pw_uid != 1000:
        raise ValueError('agents guest root required')
    if args.apply and args.rollback:
        raise ValueError('select apply or rollback')
    spec = importlib.util.spec_from_file_location('setup_mcp', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    core.published(args.revision)
    for path in ('scripts/install-hermes-dashboard-relay.py', 'deploy/hermes/dashboard-relay.py',
                 'deploy/hermes/hermes-dashboard-relay.service'):
        expected = subprocess.check_output(['git', '-C', str(ROOT), 'show', 'HEAD:' + path])
        if (ROOT / path).read_bytes() != expected:
            raise ValueError('relay source differs from published release')
    directory(UNIT_PATH.parent)
    # Serialize installation and rollback without creating a persistent override.
    import fcntl
    directory(STATE)
    with (STATE / 'operation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            result = rollback()
        elif args.apply:
            result = dict(apply(args.revision, args.expected_sha256), applied=True)
        else:
            report = plan(args.revision)[0]
            result = dict(report, plan_sha256=digest(json.dumps(report, sort_keys=True).encode()), applied=False)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
