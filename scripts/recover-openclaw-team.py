#!/usr/bin/env python3
"""Operator-only restoration of a hash-selected complete native team backup.

The automatic canary recovery policy is unchanged. No phase database, native
checkpoint, gateway owner, tool grant, model route, or installed version is edited.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

BASE = module('restore-openclaw-nemoclaw')
SUPERVISOR = module('supervise-openclaw-nemoclaw')
PATCHES = [module('setup-openclaw-child-results'), module('setup-openclaw-yield-acknowledgment')]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def retained_config(old, native):
    """Retain every operator setting; only fresh native ownership may change."""
    result = copy.deepcopy(old)
    for key in ('models', 'proxy'):
        result[key] = copy.deepcopy(native[key])
    result['gateway']['auth'] = copy.deepcopy(native['gateway']['auth'])
    # Both source and replacement must retain the same local inference contract.
    for config in (old, native):
        providers = config.get('models', {}).get('providers', {})
        if (set(providers) != {'inference'} or providers['inference'].get('apiKey') != 'unused'
                or providers['inference'].get('baseUrl') != 'https://inference.local/v1'):
            raise ValueError('managed-inference-mismatch')
    if old.get('plugins', {}).get('entries', {}).get('nemoclaw', {}).get('enabled') is not True:
        raise ValueError('native-plugin-required')
    # The pinned native plugin is replaced by the same version. Preserve other
    # plugin install records, load paths, allowlists and channel credentials.
    installs = native.get('plugins', {}).get('installs', {})
    if 'nemoclaw' in installs:
        result.setdefault('plugins', {}).setdefault('installs', {})['nemoclaw'] = copy.deepcopy(installs['nemoclaw'])
    return result


def paused(config):
    result = copy.deepcopy(config)
    result.setdefault('cron', {})['enabled'] = False
    for key, value in result.get('channels', {}).items():
        if key != 'defaults' and isinstance(value, dict):
            value['enabled'] = False
    return result


def safe_link(relative, target):
    if target.startswith('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw'):
        return target == '/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw' or target.startswith('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/')
    if target.startswith('/'):
        return False
    # Resolve relative links lexically, never against the operator filesystem.
    parts = list(Path(relative).parent.parts)
    for part in Path(target).parts:
        if part == '..':
            if not parts:
                return False
            parts.pop()
        elif part != '.':
            parts.append(part)
    return bool(parts)


def complete_hashes(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        key = path.relative_to(directory).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = str(path.readlink())
            if not safe_link(key, target):
                raise ValueError('backup-link-outside-state')
            result[key] = 'link:' + target
        elif stat.S_ISREG(info.st_mode):
            with path.open('rb') as stream:
                result[key] = hashlib.file_digest(stream, 'sha256').hexdigest()
            if key.startswith(('agents/', 'state/', 'memory/')):
                with path.open('rb') as stream:
                    sqlite = stream.read(16) == b'SQLite format 3\0'
                if sqlite:
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                        db.execute('PRAGMA query_only=ON')
                        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                            raise ValueError('active-backup-sqlite-corrupt')
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError('backup-special-file-refused')
    if not any(k.startswith('agents/') for k in result) or 'openclaw.json' not in result:
        raise ValueError('complete-state-required')
    return result


class RetainedTransaction(BASE.Transaction):
    def __init__(self, context, expected):
        super().__init__(context)
        self.expected = expected
        self.allowed_configs = {expected}
        self.policy = None

    def verify_backup(self, directory):
        if digest((directory / 'openclaw.json').read_bytes()) not in self.allowed_configs:
            raise ValueError('retained-config-hash-mismatch')
        # Config is intentionally merged with fresh native auth. All other files
        # are checked byte-for-byte before any replacement is started.
        return {k: v for k, v in complete_hashes(directory).items() if k != 'openclaw.json'}

    def merge_config(self, old, native):
        self.policy = retained_config(old, native)
        value = paused(self.policy)
        self.allowed_configs.add(digest((json.dumps(value, sort_keys=True) ).encode()))
        # atomic_json uses json.dump(sort_keys=True), without an added newline.
        return value

    def command(self, argv, label, **kwargs):
        if label == 'cap-replacement-memory':
            argv = [*argv[:-1], '--cpus', '2', argv[-1]]
        if label in {'native-restored-start', 'start-retained-team'}:
            code, output = self.ctx.run(argv, label, **kwargs)
            if code and (self.ctx.observe() != 'Ready' or not self.ctx.ready()):
                raise BASE.RecoveryRefused('native-start-not-ready')
            return output
        # Native finalization can return nonzero for the retained onboarding
        # reservation. Accept only positive exact Ready identity, never a marker.
        if label == 'native-replacement-onboard':
            code, output = self.ctx.run(argv, label, **kwargs)
            if code:
                rc, raw = self.ctx.run([self.ctx.openshell, 'sandbox', 'get', '-g', BASE.GATEWAY,
                                       BASE.NAME, '-o', 'json'], 'replacement-positive-observation')
                meta = json.loads(raw) if rc == 0 else {}
                if meta.get('phase') != 'Ready' or meta.get('name') != BASE.NAME or meta.get('id') == self.ctx.uuid:
                    raise BASE.RecoveryRefused('native-onboard-not-ready')
            return output
        return super().command(argv, label, **kwargs)

    def restore_files(self, backup, candidate):
        code, _ = self.ctx.run([self.ctx.cli, BASE.NAME, 'stop'], 'retained-replacement-stop', timeout=180)
        if self.ctx.observe() != 'Stopped':
            raise ValueError('replacement-positive-stopped-required')
        self.inspect()
        self.command(['/usr/bin/docker', 'cp', str(backup) + '/.',
                      self.ctx.container + ':/sandbox/.openclaw/'], 'restore-complete-team-state', timeout=300)
        self.command(['/usr/bin/docker', 'cp', str(candidate),
                      self.ctx.container + ':/sandbox/.openclaw/openclaw.json'], 'restore-retained-config')
        # Docker cp maps local ownership to root. Restore exact original ownership
        # and modes captured separately from the mounted source volume, offline.
        permissions = backup.parent / 'permissions.json'
        self.command(['/usr/bin/docker', 'cp', str(permissions), self.ctx.container + ':/sandbox/.openclaw/.recovery-permissions.json'], 'stage-original-permissions')
        code = '''import json,os,pathlib,stat
r=pathlib.Path('/state')
p=r/'.recovery-permissions.json'
rows=json.loads(p.read_text())
for key,row in rows.items():
 q=r/key
 if '..' in pathlib.PurePosixPath(key).parts or key.startswith('/'):raise SystemExit('path-refused')
 if not q.exists() and not q.is_symlink():raise SystemExit('restored-path-missing')
 os.chown(q,row['uid'],row['gid'],follow_symlinks=False)
 if not q.is_symlink():os.chmod(q,row['mode'])
p.unlink()
q=r/'openclaw.json';os.chown(q,998,998);os.chmod(q,0o600)
'''
        self.volume_command(code, 'restore-original-permissions')
        # Recreate the two already-installed, hash-qualified native fixes from
        # their tracked patch builders, rather than copying an opaque image layer.
        for patch in PATCHES:
            target = str(patch.RUNTIME)
            path = backup.parent / patch.RUNTIME.name
            data = path.read_bytes()
            if patch.overlay(data) != data:
                raise ValueError('retained-runtime-patch-unqualified')
            self.command(['/usr/bin/docker', 'cp', str(path), self.ctx.container + ':' + target], 'restore-qualified-runtime-patch')

    def execute(self):
        # Capture original modes/owners without reading any private file contents.
        code = '''import pathlib,json,stat
r=pathlib.Path('/state');print(json.dumps({str(p.relative_to(r)):{'uid':p.lstat().st_uid,'gid':p.lstat().st_gid,'mode':stat.S_IMODE(p.lstat().st_mode)} for p in r.rglob('*')}))'''
        raw = self.command(['/usr/bin/docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                            '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges',
                            '--entrypoint', '/usr/bin/python3', '--mount', 'type=bind,src=' + BASE.VOLUME + ',dst=/state,readonly',
                            BASE.IMAGE, '-c', code], 'capture-state-permissions', timeout=180)
        self.permissions = json.loads(raw)
        # The base transaction captures the complete state, then calls our
        # verifier. Stage sidecars at that seam, before its native destroy edge.
        return super().execute()

    def preflight(self):
        super().preflight()
        # Refuse running gateway replacement, unknown image-layer changes, or
        # retry after any mutation-stage failure. The default is plan-only.
        rc, raw = self.ctx.run(['/usr/bin/docker', 'diff', self.ctx.container], 'retained-layer-inventory', timeout=90)
        if rc:
            raise ValueError('layer-inventory-unavailable')
        changed = {line[2:] for line in raw.decode().splitlines() if line[:2] == 'C '}
        roots = ('/tmp', '/etc', '/usr/local/share/ca-certificates', '/usr/local/share/nemoclaw',
                 '/run', '/sandbox', '/opt/openshell', '/opt/homecompute-public-dns',
                 '/var/lib/nemoclaw', '/var/log')
        approved = {str(p.RUNTIME) for p in PATCHES}
        unexpected = [p for p in changed if not any(p == x or x.startswith(p + '/') for x in approved)
                      and not any(p == x or p.startswith(x + '/') or x.startswith(p + '/') for x in roots)]
        if unexpected:
            raise ValueError('unreviewed-runtime-layer-change')

    def stage(self, stage, status='active'):
        if stage == 'backup-verified':
            backup_root = Path(self.record['backup'])
            BASE.atomic_json(backup_root / 'permissions.json', self.permissions)
            for patch in PATCHES:
                target = backup_root / patch.RUNTIME.name
                self.command(['/usr/bin/docker', 'cp', self.ctx.container + ':' + str(patch.RUNTIME), str(target)], 'capture-qualified-runtime-patch')
                if patch.overlay(target.read_bytes()) != target.read_bytes():
                    raise ValueError('existing-runtime-patch-unqualified')
                target.chmod(0o644)  # Code is public; its backup parent remains 0700.
        super().stage(stage, status)

    def complete_restore(self, backup_root, backup, candidate, hashes, conversations):
        # The base path verifies every restored file before start, read-only
        # readiness, stable main conversation state, and the exact native CLI.
        result = super().complete_restore(backup_root, backup, candidate, hashes, conversations)
        self.command(['sudo', '/usr/bin/python3', str(ROOT / 'scripts/setup-openclaw-public-dns.py'),
                      '--replacement-id', self.ctx.uuid, '--replacement-generation', self.ctx.generation],
                     'rebind-existing-native-public-dns', timeout=120)
        self.command(['sudo', 'systemctl', 'daemon-reload'], 'dns-unit-reload')
        self.command(['sudo', 'systemctl', 'restart', 'homecompute-public-dns.service'], 'dns-replacement-start')
        after = backup_root / 'verification-ready/.openclaw'
        observed = complete_hashes(after)
        for role in json.loads((backup / 'openclaw.json').read_text())['agents']['entries']:
            # Include every role's workspace, not just the original canary.
            prefix = 'workspace/' if role == 'main' else 'workspace-' + role + '/'
            if any(observed.get(k) != v for k,v in hashes.items() if k.startswith(prefix)):
                raise ValueError('retained-workspace-drift')
        final = backup_root / 'restored-active-config.json'
        BASE.atomic_json(final, self.policy)
        code, _ = self.ctx.run([self.ctx.cli, BASE.NAME, 'stop'], 'verified-team-stop', timeout=180)
        if self.ctx.observe() != 'Stopped':
            raise ValueError('verified-team-stopped-required')
        self.command(['/usr/bin/docker', 'cp', str(final), self.ctx.container + ':/sandbox/.openclaw/openclaw.json'], 'restore-active-team-policy')
        self.volume_command("import os;os.chown('/state/openclaw.json',998,998);os.chmod('/state/openclaw.json',0o600)", 'active-config-permissions')
        self.command([self.ctx.cli, BASE.NAME, 'start'], 'start-retained-team', timeout=420)
        if self.ctx.observe() != 'Ready' or not self.ctx.ready():
            raise ValueError('active-team-not-ready')
        self.stage('complete-active-team', 'complete')
        return result


def source_release(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('full-published-revision-required')
    def git(*args):
        return subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),*args],stderr=subprocess.DEVNULL).decode().strip()
    if git('rev-parse','HEAD') != revision or git('status','--porcelain','--untracked-files=no'):
        raise ValueError('immutable-release-required')
    subprocess.run(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'merge-base','--is-ancestor',revision,'origin/master'],check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--expected-config-sha256',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Resume only a journaled, stopped replacement')
    args=parser.parse_args()
    source_release(args.revision)
    if socket.gethostname() != 'agents' or os.getuid() != 1000 or not re.fullmatch('[0-9a-f]{64}',args.expected_config_sha256):
        raise ValueError('exact-operator-host-and-config-required')
    env=dict(os.environ)
    for name in ('agent-openclaw.env','supervisor-identity.env'):
        for line in SUPERVISOR.private_file(Path('/etc/homecompute-openclaw')/name).decode().splitlines():
            if line and not line.startswith('#'):
                key,value=line.split('=',1);env[key]=value.strip('"')
    ctx=SUPERVISOR.Supervisor(env, operator_resume=args.resume)
    tx=RetainedTransaction(ctx,args.expected_config_sha256)
    if not args.resume:
        tx.preflight()
    if not args.apply:
        print(json.dumps({'revision':args.revision,'sandbox_id':ctx.uuid,'phase':'Error','expected_config_sha256':args.expected_config_sha256,'apply':False}))
        return
    # Preserve the supported managed gateway process when pausing the watchdog.
    subprocess.run(['systemctl','--user','stop','homecompute-openclaw-supervisor.service'],check=True)
    fd=os.open(ctx.state/'error-recovery.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    import fcntl
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=tx.resume() if args.resume else tx.execute()
    finally:
        os.close(fd)
    subprocess.run(['systemctl','--user','start','homecompute-openclaw-supervisor.service'],check=True)
    print(json.dumps({'revision':args.revision,'phase':'Ready','sandbox_id':result[0],'restored_complete_team':True}))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Native logs and credential-bearing config remain in private diagnostics.
        print('Retained team recovery refused: '+(str(error) if isinstance(error,(ValueError,BASE.RecoveryRefused,SUPERVISOR.Refused)) else type(error).__name__),file=sys.stderr)
        sys.exit(1)
