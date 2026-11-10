#!/usr/bin/env python3
"""Allow the approved tailnet hostname in the private Hermes dashboard.

The default action plans. Apply captures a private .env preimage on the agents
VM, updates only the patched Hermes host allowlist, and restarts through the
native NemoClaw lifecycle. Credentials are never parsed or printed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
NODE_BIN = Path.home() / '.nvm/versions/node/v22.23.3/bin'
ENV = {**os.environ,
       'PATH': str(Path.home() / '.local/bin') + ':' + str(NODE_BIN) + ':' + os.environ.get('PATH', '/usr/bin:/bin'),
       'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
       'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
PIN = json.loads((ROOT / 'config/hermes-dashboard-access.json').read_bytes())
NAME = PIN['sandbox']
KEY = '_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST'
ENV_PATH = '/sandbox/.hermes/.env'
STATE = Path.home() / '.local/state/homecompute/hermes-dashboard-host'
LOCK = STATE / 'operation.lock'
RECEIPT = STATE / 'receipt.json'
TARGET = PIN['tailnet_hostname']
ALLOWED_OLD = {'', PIN['local_hostname'], TARGET}
MATCH = re.compile(rb'^[ \t]*(?:export[ \t]+)?' + re.escape(KEY.encode()) + rb'[ \t]*=(.*?)[ \t]*\r?$', re.M)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def assignment_value(raw: bytes) -> str | None:
    rows = MATCH.findall(raw)
    if len(rows) > 1:
        raise ValueError('duplicate dashboard external-host assignments need review')
    if not rows:
        return None
    value = rows[0].decode().strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if value not in ALLOWED_OLD:
        raise ValueError('existing dashboard external host differs from reviewed settings')
    return value


def updated_env(raw: bytes) -> bytes:
    assignment_value(raw)
    line = KEY.encode() + b'=' + TARGET.encode()
    if MATCH.search(raw):
        result = MATCH.sub(line, raw, count=1)
    else:
        result = raw + (b'' if not raw or raw.endswith(b'\n') else b'\n') + line + b'\n'
    if assignment_value(result) != TARGET:
        raise ValueError('dashboard external-host rendering did not verify')
    return result


def call(args: list[str], *, data: bytes | None = None, stdout=None, timeout: int = 120) -> bytes:
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE if stdout is None else stdout,
                            stderr=subprocess.PIPE, timeout=timeout, env=ENV)
    if result.returncode:
        raise ValueError('guarded Hermes dashboard-host operation failed; inspect private state')
    return result.stdout or b''


def published(revision: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('full published revision required')
    if call(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip() != revision:
        raise ValueError('selected checkout revision differs')
    call(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    files = ['scripts/setup-hermes-dashboard-host.py', 'config/hermes-dashboard-access.json',
             'deploy/hermes/README.md', 'tests/hermes-dashboard-host-test.py',
             'scripts/validate-repository.sh']
    call(['git', '-C', str(ROOT), 'diff', '--quiet', 'HEAD', '--', *files])


def secure_state() -> None:
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private operator-owned state directory required')
    LOCK.touch(mode=0o600, exist_ok=True)
    os.chmod(LOCK, 0o600)


def cli() -> list[str]:
    return ['nemohermes', NAME]


def sandbox_status() -> dict:
    row = json.loads(call(cli() + ['status', '--json']).decode())
    if row.get('agent') != 'hermes' or row.get('phase') != 'Ready' or row.get('hostMounts'):
        raise ValueError('ready isolated owner Hermes required')
    return row


def container() -> str:
    rows = call(['docker', 'ps', '--filter', 'name=openshell-default--' + NAME + '-',
                 '--format', '{{.ID}}']).decode().splitlines()
    if len(rows) != 1:
        raise ValueError('one running owner Hermes container required')
    image = call(['docker', 'inspect', rows[0], '--format', '{{.Image}}']).decode().strip()
    if image != json.loads((ROOT / 'config/hermes-omh.json').read_bytes())['runtime_image_id']:
        raise ValueError('qualified owner Hermes image required')
    return rows[0]


def inspect_env(container_id: str) -> dict:
    program = '''import hashlib,json,os,pathlib,re,stat
p=pathlib.Path("/sandbox/.hermes/.env")
assert not p.is_symlink() and p.is_file()
s=p.stat(); raw=p.read_bytes(); key=b"_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST"
rows=[m.group(1).decode().strip().strip("\\\"\\'") for m in re.finditer(rb"^[ \\t]*(?:export[ \\t]+)?"+re.escape(key)+rb"[ \\t]*=(.*?)[ \\t]*\\r?$",raw,re.M)]
assert len(rows)<=1
value=rows[0] if rows else None
allowed={"", "agents.home.arpa", "home-core.tail479ad.ts.net", None}
assert value in allowed
print(json.dumps({"sha256":hashlib.sha256(raw).hexdigest(),"uid":s.st_uid,"gid":s.st_gid,"mode":stat.S_IMODE(s.st_mode),"host_value":value}))
'''
    result = call(['docker', 'exec', container_id, '/opt/hermes/.venv/bin/python', '-c', program])
    return json.loads(result)


def send_update(container_id: str, *, restore: bytes | None = None) -> dict:
    action = json.dumps({'restore': restore.decode() if restore is not None else None,
                         'target': TARGET})
    program = '''import hashlib,json,os,pathlib,re,stat,sys
p=pathlib.Path("/sandbox/.hermes/.env"); assert not p.is_symlink() and p.is_file()
st=p.stat(); assert st.st_uid==998 and st.st_gid==999 and stat.S_IMODE(st.st_mode)==0o640
raw=p.read_bytes(); action=json.loads(sys.argv[1]); key=b"_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST"
if action["restore"] is not None:
 new=action["restore"].encode()
 assert new and b"\\0" not in new
 restored=True
else:
 wanted=action["target"]
 restored=False
 pattern=re.compile(rb"^[ \\t]*(?:export[ \\t]+)?"+re.escape(key)+rb"[ \\t]*=(.*?)[ \\t]*\\r?$",re.M)
 rows=pattern.findall(raw); assert len(rows)<=1
 old=rows[0].decode().strip().strip("\\\"\\'") if rows else None
 assert old in (None,"","agents.home.arpa","home-core.tail479ad.ts.net")
 line=key+b"="+wanted.encode()
 new=pattern.sub(line,raw,count=1) if rows else raw+(b"" if not raw or raw.endswith(b"\\n") else b"\\n")+line+b"\\n"
tmp=p.with_name(p.name+".homecompute-new"); assert not tmp.exists() and not tmp.is_symlink()
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
 with os.fdopen(fd,"wb") as f:f.write(new);f.flush();os.fsync(f.fileno())
 os.chown(tmp,st.st_uid,st.st_gid);os.chmod(tmp,stat.S_IMODE(st.st_mode));os.replace(tmp,p)
except BaseException:
 try:tmp.unlink()
 except OSError:pass
 raise
print(json.dumps({"sha256":hashlib.sha256(new).hexdigest(),"restored":restored}))
'''
    return json.loads(call(['docker', 'exec', container_id, '/opt/hermes/.venv/bin/python', '-c',
                            program, action]).decode())


def save_preimage(container_id: str, expected_sha: str) -> Path:
    backup = STATE / ('before-' + expected_sha + '.env')
    if backup.exists() or backup.is_symlink():
        if not backup.is_file() or backup.is_symlink() or digest(backup.read_bytes()) != expected_sha:
            raise ValueError('existing private preimage differs')
        return backup
    with backup.open('xb') as stream:
        os.chmod(backup, 0o600)
        call(['docker', 'exec', container_id, 'cat', ENV_PATH], stdout=stream)
        stream.flush(); os.fsync(stream.fileno())
    if digest(backup.read_bytes()) != expected_sha:
        backup.unlink()
        raise ValueError('private .env preimage changed during capture')
    return backup


def stop_start() -> None:
    call(cli() + ['stop'], timeout=180)
    call(cli() + ['start'], timeout=300)
    sandbox_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--expected-env-sha256')
    args = parser.parse_args()
    if args.apply and args.rollback:
        raise ValueError('--apply and --rollback are mutually exclusive')
    published(args.revision)
    secure_state()
    with LOCK.open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sandbox_status()
        container_id = container()
        observed = inspect_env(container_id)
        if (observed['uid'], observed['gid'], observed['mode']) != (998, 999, 0o640):
            raise ValueError('Hermes .env owner/mode differs from the selected runtime')
        if args.rollback:
            if not RECEIPT.exists() or RECEIPT.is_symlink():
                raise ValueError('applied dashboard-host receipt required for rollback')
            receipt = json.loads(RECEIPT.read_bytes())
            if receipt.get('state') != 'applied' or observed['sha256'] != receipt['after_sha256']:
                raise ValueError('dashboard-host settings changed since apply; preserve for review')
            backup = Path(receipt['backup'])
            if backup.parent != STATE or backup.is_symlink() or digest(backup.read_bytes()) != receipt['before_sha256']:
                raise ValueError('verified private .env preimage required')
            send_update(container_id, restore=backup.read_bytes())
            if inspect_env(container_id)['sha256'] != receipt['before_sha256']:
                raise ValueError('restored Hermes .env does not match the retained preimage')
            stop_start()
            receipt['state'] = 'rolled-back'; receipt['rolled_back_at'] = int(time.time())
            RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n'); os.chmod(RECEIPT, 0o600)
            print(json.dumps({'rolled_back': True, 'revision': args.revision})); return
        old = observed['host_value']
        if old == TARGET:
            print(json.dumps({'already_configured': True, 'revision': args.revision,
                              'local_hostname': PIN['local_hostname'], 'tailnet_hostname': TARGET})); return
        if not args.apply:
            print(json.dumps({'revision': args.revision, 'sandbox': NAME,
                              'current_external_host': old, 'target_external_host': TARGET,
                              'local_hostname': PIN['local_hostname'], 'env_sha256': observed['sha256'],
                              'applied': False})); return
        if args.expected_env_sha256 != observed['sha256']:
            raise ValueError('reviewed .env SHA-256 required; rerun the plan and review its hash')
        if RECEIPT.exists() and json.loads(RECEIPT.read_bytes()).get('state') == 'applied':
            raise ValueError('existing applied receipt needs rollback or reconciliation')
        backup = save_preimage(container_id, observed['sha256'])
        if inspect_env(container_id)['sha256'] != observed['sha256']:
            raise ValueError('.env changed after planning')
        result = send_update(container_id)
        verified = inspect_env(container_id)
        if (result['sha256'] == observed['sha256'] or verified['sha256'] != result['sha256']
                or verified['host_value'] != TARGET):
            raise ValueError('tailnet host setting did not verify')
        receipt = {'state': 'applied', 'revision': args.revision, 'sandbox': NAME,
                   'before_sha256': observed['sha256'], 'after_sha256': result['sha256'],
                   'backup': str(backup), 'external_host': TARGET,
                   'local_hostname': PIN['local_hostname'], 'applied_at': int(time.time())}
        RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n'); os.chmod(RECEIPT, 0o600)
        stop_start()
        print(json.dumps({'applied': True, 'revision': args.revision, 'restart': 'native',
                          'local_hostname': PIN['local_hostname'], 'tailnet_hostname': TARGET,
                          'receipt': str(RECEIPT)}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
