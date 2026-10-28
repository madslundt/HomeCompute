#!/usr/bin/env python3
"""Prepare one private calendar-route credential and inspectable inactive workflow."""
from __future__ import annotations

import argparse
import copy
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

from openclaw_calendar_workflow import build, sdk, GOOGLE_CREDENTIAL, ROUTE_CREDENTIAL

CONFIG = Path('/var/lib/homecompute-openclaw/config')
TOKEN = CONFIG / 'calendar-route-token'
DIRECTORY = CONFIG / 'calendar-bridge'
DATABASE = '/srv/state/automation/n8n/database.sqlite'
N8N = 'homecompute-automation-n8n-1'
MAP_WORKFLOW = '4gGkCRqxgsFn4TSc'
ALIASES = {'mads', 'kristina', 'alvira', 'nora', 'family'}
SERVICE_UID = 986
ADMIN_UID = 0


def private(path: Path, limit: int = 262144) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in {ADMIN_UID, SERVICE_UID}
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
            raise ValueError('Protected calendar state required')
        return os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)


def save(path: Path, content: bytes, uid: int | None = None) -> None:
    uid = ADMIN_UID if uid is None else uid
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(4))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.fchown(descriptor, uid, uid)
        os.replace(temporary, path)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def json_save(path: Path, value: Any, uid: int | None = None) -> None:
    save(path, (json.dumps(value, indent=2) + '\n').encode(), uid)


def read_mapping(nodes: Any) -> dict[str, str]:
    if not isinstance(nodes, list):
        raise ValueError('Calendar source nodes missing')
    selected = [n for n in nodes if n.get('name') == 'Prepare incoming calendar event']
    if len(selected) != 1:
        raise ValueError('Exact existing calendar map required')
    source = selected[0].get('parameters', {}).get('jsCode', '')
    match = re.search(r'const\s+calendars\s*=\s*\{([^{}]+)\}\s*;', source)
    if match is None:
        raise ValueError('Existing calendar map format differs')
    entries = re.findall(r"(?:'([a-z]+)'|\"([a-z]+)\"|([a-z]+))\s*:\s*['\"]([^'\"\r\n]+)['\"]\s*,?", match[1])
    result = {(a or b or c): value for a, b, c, value in entries}
    if (set(result) != ALIASES or len(entries) != 5 or len(set(result.values())) != 5
            or any(not re.fullmatch(r'[A-Za-z0-9._@+-]{3,512}', value) or '@' not in value for value in result.values())):
        raise ValueError('Only the exact five household calendars are supported')
    return result


def instantiate(directory: Path, mapping: dict[str, str]) -> tuple[dict[str, Any], str]:
    workflow = copy.deepcopy(build(directory))
    gate = next(n for n in workflow['nodes'] if n['name'] == 'Validate calendar request')
    source = gate['parameters']['jsCode']
    if source.count('const configured = false;') != 1 or source.count('__CALENDAR_IDS__') != 1:
        raise ValueError('Calendar artifact setup fence differs')
    gate['parameters']['jsCode'] = source.replace('const configured = false;', 'const configured = true;').replace('__CALENDAR_IDS__', json.dumps(mapping, sort_keys=True))
    return workflow, sdk(workflow)


def run(argv: list[str], content: str | None = None) -> str:
    result = subprocess.run(argv, input=content, capture_output=True, text=True, timeout=120)
    if result.returncode:
        json_save(DIRECTORY / 'last-failure.json', {'returncode': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr})
        raise ValueError('Calendar provisioning failed; inspect protected diagnostics')
    return result.stdout


def import_credential(db: sqlite3.Connection, token: str, project: str) -> None:
    identifier, name = ROUTE_CREDENTIAL['id'], ROUTE_CREDENTIAL['name']
    existing = db.execute('SELECT id,name,type FROM credentials_entity WHERE id=? OR name=?', (identifier, name)).fetchall()
    if existing and existing != [(identifier, name, 'httpHeaderAuth')]:
        raise ValueError('Calendar credential identity collision')
    expected = {'name': 'Authorization', 'value': 'Bearer ' + token}
    prefix = '/tmp/homecompute-calendar-' + secrets.token_hex(8)
    incoming, exported = prefix + '-import.json', prefix + '-export.json'
    node = ['docker', 'exec', '-i', N8N, 'node', '-e']
    create = "require('fs').writeFileSync(process.argv[1],require('fs').readFileSync(0),{mode:0o600,flag:'wx'})"
    try:
        if not existing:
            payload = [{'id': identifier, 'name': name, 'type': 'httpHeaderAuth', 'data': expected}]
            run(node + [create, incoming], json.dumps(payload))
            run(['docker', 'exec', N8N, 'n8n', 'import:credentials', '--input=' + incoming, '--projectId=' + project])
        run(node + [create, exported], '')
        run(['docker', 'exec', N8N, 'n8n', 'export:credentials', '--id=' + identifier,
             '--decrypted', '--output=' + exported])
        saved = json.loads(run(node + ["process.stdout.write(require('fs').readFileSync(process.argv[1]))", exported]))
        if len(saved) != 1 or saved[0]['id'] != identifier or saved[0]['data'] != expected:
            raise ValueError('Retained calendar credential differs; no rotation permitted')
        encrypted = db.execute('SELECT data FROM credentials_entity WHERE id=?', (identifier,)).fetchone()
        shared = db.execute('SELECT projectId,role FROM shared_credentials WHERE credentialsId=?', (identifier,)).fetchall()
        if not encrypted or token in encrypted[0] or shared != [(project, 'credential:owner')]:
            raise ValueError('Calendar credential encryption or project differs')
    finally:
        run(node + ["for(const p of process.argv.slice(1))try{require('fs').unlinkSync(p)}catch(e){if(e.code!=='ENOENT')throw e}", incoming, exported])


def prepare(source: Path) -> dict[str, Any]:
    info = CONFIG.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != SERVICE_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Existing private core configuration directory required')
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ADMIN_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Root-private calendar provisioning directory required')
    lock = DIRECTORY / 'provision.lock'
    if not lock.exists():
        save(lock, b'')
    private(lock)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect('file:' + DATABASE + '?mode=ro', uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            project = db.execute("SELECT p.id FROM project p JOIN project_relation r ON r.projectId=p.id "
                "JOIN user u ON u.id=r.userId WHERE p.type='personal' AND u.roleSlug='global:owner' "
                "AND r.role='project:personalOwner'").fetchall()
            if len(project) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', project[0][0]):
                raise ValueError('Exact n8n owner project required')
            oauth = db.execute('SELECT name,type FROM credentials_entity WHERE id=?', (GOOGLE_CREDENTIAL['id'],)).fetchall()
            if oauth != [(GOOGLE_CREDENTIAL['name'], 'googleCalendarOAuth2Api')]:
                raise ValueError('Existing Google OAuth credential identity differs')
            row = db.execute('SELECT nodes FROM workflow_entity WHERE id=? AND isArchived=0', (MAP_WORKFLOW,)).fetchone()
            if not row:
                raise ValueError('Existing family calendar source missing')
            mapping = read_mapping(json.loads(row[0]))
            retained_map = DIRECTORY / 'calendar-map.json'
            if retained_map.exists() and json.loads(private(retained_map)) != mapping:
                raise ValueError('Retained household calendar identities changed')
            expected_config = {'schema_version': 1, 'bridge_token_file': str(TOKEN)}
            config_path = CONFIG / 'calendar.json'
            if config_path.exists() and json.loads(private(config_path)) != expected_config:
                raise ValueError('Retained calendar configuration changed')
            workflow, code = instantiate(source, mapping)
            if TOKEN.exists() or TOKEN.is_symlink():
                token = private(TOKEN, 256).decode().strip()
                if not re.fullmatch(r'[a-f0-9]{64}', token):
                    raise ValueError('Retained route token differs')
            else:
                # Refuse to generate replacement authority for an existing encrypted route.
                if db.execute('SELECT 1 FROM credentials_entity WHERE id=? OR name=?', tuple(ROUTE_CREDENTIAL.values())).fetchone():
                    raise ValueError('Retained route credential needs original token recovery')
                token = secrets.token_hex(32)
                save(TOKEN, (token + '\n').encode(), SERVICE_UID)
            import_credential(db, token, project[0][0])
            json_save(config_path, expected_config, SERVICE_UID)
            json_save(DIRECTORY / 'calendar-map.json', mapping)
            json_save(DIRECTORY / 'workflow.json', workflow)
            save(DIRECTORY / 'workflow.ts', code.encode())
            receipt = {'credential': ROUTE_CREDENTIAL, 'credential_type': 'httpHeaderAuth', 'project_id': project[0][0],
                       'calendar_aliases': sorted(mapping), 'oauth_exported': False, 'google_writes': 0,
                       'workflow_created': False, 'required_settings': workflow['settings'],
                       'workflow_sha256': hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()}
            json_save(DIRECTORY / 'provision-receipt.json', receipt)
            return {k: receipt[k] for k in ('credential', 'credential_type', 'project_id', 'calendar_aliases', 'google_writes', 'workflow_created')}
        finally:
            db.close()
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-directory', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        print(json.dumps(prepare(args.artifact_directory)))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        raise SystemExit('Calendar setup refused or failed; inspect protected state before retrying.') from None
