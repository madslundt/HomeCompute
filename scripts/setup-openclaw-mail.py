#!/usr/bin/env python3
"""Stage separately scoped mail bridges; OAuth sign-in stays in n8n."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
from typing import Any

from openclaw_mail_workflow import build, sdk, route_credential, SOURCES

CONFIG = Path('/var/lib/homecompute-openclaw/config')
DATABASE = '/srv/state/automation/n8n/database.sqlite'
N8N = 'homecompute-automation-n8n-1'
SERVICE_UID = 986
ADMIN_UID = 0


def private(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in {ADMIN_UID, SERVICE_UID}
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 262144):
            raise ValueError('Protected mail state required')
        return os.read(fd, 262145)
    finally:
        os.close(fd)


def save(path: Path, content: bytes, uid: int = ADMIN_UID) -> None:
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
            os.fchown(handle.fileno(), uid, uid)
        if path.is_symlink():
            raise ValueError('Mail state symlink refused')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def json_save(path: Path, value: Any, uid: int = ADMIN_UID) -> None:
    save(path, (json.dumps(value, indent=2) + '\n').encode(), uid)


def run(argv: list[str], content: str | None = None) -> str:
    result = subprocess.run(argv, input=content, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('Mail credential operation failed; no provider OAuth was exported')
    return result.stdout


def import_route(db: sqlite3.Connection, principal: str, token: str, project: str) -> None:
    route = route_credential(principal)
    existing = db.execute('SELECT id,name,type FROM credentials_entity WHERE id=? OR name=?',
                          (route['id'], route['name'])).fetchall()
    if existing and existing != [(route['id'], route['name'], 'httpHeaderAuth')]:
        raise ValueError('Mail route credential identity collision')
    expected = {'name': 'Authorization', 'value': 'Bearer ' + token}
    prefix = '/tmp/homecompute-mail-' + secrets.token_hex(8)
    incoming, exported = prefix + '-import.json', prefix + '-export.json'
    node = ['docker', 'exec', '-i', N8N, 'node', '-e']
    create = "require('fs').writeFileSync(process.argv[1],require('fs').readFileSync(0),{mode:0o600,flag:'wx'})"
    try:
        if not existing:
            run(node + [create, incoming], json.dumps([{**route, 'type': 'httpHeaderAuth', 'data': expected}]))
            run(['docker', 'exec', N8N, 'n8n', 'import:credentials', '--input=' + incoming, '--projectId=' + project])
        run(node + [create, exported], '')
        run(['docker', 'exec', N8N, 'n8n', 'export:credentials', '--id=' + route['id'], '--decrypted', '--output=' + exported])
        saved = json.loads(run(node + ["process.stdout.write(require('fs').readFileSync(process.argv[1]))", exported]))
        if len(saved) != 1 or saved[0]['id'] != route['id'] or saved[0]['data'] != expected:
            raise ValueError('Retained mail route secret differs; no rotation permitted')
        row = db.execute('SELECT data FROM credentials_entity WHERE id=?', (route['id'],)).fetchone()
        shares = db.execute('SELECT projectId,role FROM shared_credentials WHERE credentialsId=?', (route['id'],)).fetchall()
        if not row or token in row[0] or shares != [(project, 'credential:owner')]:
            raise ValueError('Mail route encryption or project differs')
    finally:
        run(node + ["for(const p of process.argv.slice(1))try{require('fs').unlinkSync(p)}catch(e){if(e.code!=='ENOENT')throw e}", incoming, exported])


def prepare(source: Path, *, principal: str = 'owner', credential_id: str | None = None,
            isolated_runtime: bool = False, local_work_approved: bool = False) -> dict:
    if principal not in SOURCES:
        raise ValueError('Explicit mail principal required')
    if credential_id and (not isolated_runtime or principal == 'work' and not local_work_approved):
        raise ValueError('Mail admission requires its isolated principal runtime and permitted local work use')
    info = CONFIG.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != SERVICE_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Existing private configuration directory required')
    directory = CONFIG / ('mail-' + principal + '-bridge')
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ADMIN_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Root-private mail staging directory required')
    lock = directory / 'provision.lock'
    if not lock.exists(): save(lock, b'')
    private(lock)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected, token_path = SOURCES[principal], CONFIG / ('mail-' + principal + '-route-token')
        credential = None
        if credential_id:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', credential_id) or credential_id == 'iedceqfZ0ps7QCyX':
                raise ValueError('Requested source differs from the existing family Gmail account')
            for other in SOURCES.keys() - {principal}:
                receipt = CONFIG / ('mail-' + other + '-bridge') / 'provision-receipt.json'
                if receipt.exists() and json.loads(private(receipt)).get('provider_credential', {}).get('id') == credential_id:
                    raise ValueError('A mailbox credential cannot cross principal boundaries')
            with sqlite3.connect('file:' + DATABASE + '?mode=ro', uri=True) as db:
                db.execute('PRAGMA query_only=ON')
                kind = 'gmailOAuth2' if selected['provider'] == 'gmail' else 'microsoftOutlookOAuth2Api'
                rows = db.execute('SELECT id,name,type FROM credentials_entity WHERE id=?', (credential_id,)).fetchall()
                if len(rows) != 1 or rows[0][2] != kind:
                    raise ValueError('Selected mailbox OAuth credential is missing or has the wrong provider')
                credential = {'id': rows[0][0], 'name': rows[0][1]}
                previous_receipt = directory / 'provision-receipt.json'
                if previous_receipt.exists():
                    previous_source = json.loads(private(previous_receipt)).get('provider_credential')
                    if previous_source is not None and previous_source != credential:
                        raise ValueError('Retained mailbox identity differs; explicit source reconciliation required')
                project = db.execute("SELECT p.id FROM project p JOIN project_relation r ON r.projectId=p.id JOIN user u ON u.id=r.userId WHERE p.type='personal' AND u.roleSlug='global:owner' AND r.role='project:personalOwner'").fetchall()
                if len(project) != 1: raise ValueError('Exact n8n owner project required')
                if token_path.exists():
                    token = private(token_path).decode().strip()
                    if not re.fullmatch(r'[a-f0-9]{64}', token): raise ValueError('Retained mail token differs')
                else:
                    route = route_credential(principal)
                    if db.execute('SELECT 1 FROM credentials_entity WHERE id=? OR name=?', tuple(route.values())).fetchone():
                        raise ValueError('Original mail token recovery required')
                    token = secrets.token_hex(32)
                    save(token_path, (token + '\n').encode(), SERVICE_UID)
                import_route(db, principal, token, project[0][0])
        config_path = CONFIG / ('mail.json' if principal == 'owner' else 'mail-' + principal + '.json')
        config = {'schema_version': 1, 'bridge_token_file': str(token_path), 'principal_scope': principal,
                  'data_domain': selected['data_domain'], 'accounts': [selected['alias']] if credential else []}
        if config_path.exists():
            previous = json.loads(private(config_path))
            if previous.get('accounts') and not credential:
                raise ValueError('Refusing to silently disable or replace a configured mail source')
        workflow = build(source, principal=principal, credential=credential, configured=bool(credential))
        json_save(config_path, config, SERVICE_UID)
        json_save(directory / 'workflow.json', workflow)
        save(directory / 'workflow.ts', sdk(workflow).encode())
        receipt = {'principal_scope': principal, 'data_domain': selected['data_domain'], 'alias': selected['alias'],
                   'provider': selected['provider'], 'provider_credential': credential,
                   'status': 'prepared-not-published' if credential else 'oauth-sign-in-required',
                   'workflow_active': False, 'oauth_exported': False, 'mailbox_reads': 0, 'writes': 0,
                   'setup_url': 'https://n8n.home.arpa/home/credentials',
                   'required_scope': 'https://www.googleapis.com/auth/gmail.readonly' if selected['provider'] == 'gmail' else 'openid offline_access Mail.Read',
                   'required_credential_type': 'gmailOAuth2' if selected['provider'] == 'gmail' else 'microsoftOutlookOAuth2Api',
                   'required_settings': workflow['settings'],
                   'workflow_sha256': hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()}
        json_save(directory / 'provision-receipt.json', receipt)
        return {k: v for k, v in receipt.items() if k != 'provider_credential'}
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-directory', type=Path, required=True)
    parser.add_argument('--principal', choices=tuple(SOURCES), required=True)
    parser.add_argument('--credential-id')
    parser.add_argument('--isolated-runtime-confirmed', action='store_true')
    parser.add_argument('--local-work-approved', action='store_true')
    parser.add_argument('--enable-mail', action='store_true', help='explicitly re-enable the optional retired mail integration')
    args = parser.parse_args()
    if not args.enable_mail:
        parser.error('Mail integration is disabled; explicit --enable-mail is required for new setup')
    if os.geteuid() != 0: raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        print(json.dumps(prepare(args.artifact_directory, principal=args.principal,
            credential_id=args.credential_id, isolated_runtime=args.isolated_runtime_confirmed,
            local_work_approved=args.local_work_approved)))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError):
        raise SystemExit('Mail setup refused; inspect protected state and requested source identity.') from None
