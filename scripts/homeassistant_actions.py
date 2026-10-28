#!/usr/bin/env python3
"""Bounded HA proposals: agent IPC cannot approve or execute physical actions.

The controller owns SQLite and an executor-only n8n credential. The shared MCP
adapter assigns actor identity; agents never receive either credential or DB
access. Root CLI is the only approval/execution interface. The fixed n8n route
must independently validate this same finite action contract, fetch selected
state only, use the existing HA credential, and disable retries/data retention.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import socketserver
import sqlite3
import stat
import struct
import sys
import time
from typing import Any, Callable
import urllib.request

STATE = Path('/var/lib/homecompute-ha-actions')
SOCKET = Path('/run/homecompute-ha-actions/agent.sock')
ROUTE = 'http://127.0.0.1:15678/webhook/homecompute-approved-ha-actions'
ACTORS = {'openclaw', 'hermes'}
SERVICES = {'light': {'turn_on', 'turn_off'},
            'climate': {'set_temperature', 'set_hvac_mode'},
            'cover': {'open_cover', 'close_cover', 'stop_cover'}}
MODES = {'off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only'}
TTL, SNAPSHOT_TTL, MAX_BYTES = 300, 30, 16384
ACTOR_LIMIT, TOTAL_LIMIT, RECORD_LIMIT = 32, 64, 10000
ID = re.compile(r'^[a-z_]+\.[a-z0-9_]{1,128}$')


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def bounded_number(value: Any, low: float, high: float) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and low <= value <= high


def action(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {'domain', 'service', 'entity_ids', 'data'}:
        raise ValueError('exact action fields required')
    domain, service = value['domain'], value['service']
    if not isinstance(domain, str) or not isinstance(service, str) or service not in SERVICES.get(domain, set()):
        raise ValueError('HA service outside approved finite policy')
    entities, data = value['entity_ids'], value['data']
    if (not isinstance(entities, list) or not 1 <= len(entities) <= 8
            or any(not isinstance(e, str) or not ID.fullmatch(e) or e.split('.')[0] != domain for e in entities)
            or len(set(entities)) != len(entities) or not isinstance(data, dict)):
        raise ValueError('bounded explicit matching entity targets required')
    if domain == 'light' and service == 'turn_on':
        if (set(data) - {'brightness', 'transition'}
                or 'brightness' in data and (type(data['brightness']) is not int or not 0 <= data['brightness'] <= 255)
                or 'transition' in data and not bounded_number(data['transition'], 0, 30)):
            raise ValueError('invalid light data')
    elif domain == 'climate' and service == 'set_temperature':
        if set(data) != {'temperature'} or not bounded_number(data['temperature'], 5, 35):
            raise ValueError('temperature must be between 5 and 35 Celsius')
    elif domain == 'climate' and service == 'set_hvac_mode':
        if set(data) != {'hvac_mode'} or not isinstance(data['hvac_mode'], str) or data['hvac_mode'] not in MODES:
            raise ValueError('invalid HVAC mode')
    elif data:
        raise ValueError('service takes no extra data')
    normalized = {k: int(v) if type(v) is float and v.is_integer() else v for k, v in data.items()}
    return {'domain': domain, 'service': service, 'entity_ids': sorted(entities), 'data': normalized}


def snapshot(raw: Any, request: dict[str, Any], now: float) -> dict[str, Any]:
    if (not isinstance(raw, dict) or set(raw) != {'observed_at', 'entities'}
            or not bounded_number(raw['observed_at'], now - SNAPSHOT_TTL, now + 2)
            or not isinstance(raw['entities'], dict) or set(raw['entities']) != set(request['entity_ids'])):
        raise ValueError('fresh exact-target HA snapshot required')
    for entity, row in raw['entities'].items():
        domain = entity.split('.')[0]
        if not isinstance(row, dict) or 'state' not in row or set(row) - {'state', 'brightness', 'temperature', 'temperature_unit', 'hvac_modes'}:
            raise ValueError('unbounded HA snapshot')
        states = {'light': {'on', 'off'}, 'climate': MODES, 'cover': {'open', 'closed', 'opening', 'closing'}}
        if not isinstance(row['state'], str) or row['state'] not in states[domain]:
            raise ValueError('target state unavailable or unsupported')
        allowed = {'light': {'state', 'brightness'}, 'climate': {'state', 'temperature', 'temperature_unit', 'hvac_modes'}, 'cover': {'state'}}
        if set(row) - allowed[domain]:
            raise ValueError('snapshot field/domain mismatch')
        if 'brightness' in row and (type(row['brightness']) is not int or not 0 <= row['brightness'] <= 255):
            raise ValueError('invalid brightness snapshot')
        if 'temperature' in row and not bounded_number(row['temperature'], -50, 100):
            raise ValueError('invalid temperature snapshot')
        if 'temperature_unit' in row and row['temperature_unit'] != '°C':
            raise ValueError('Celsius target required')
        if request['service'] == 'set_temperature' and row.get('temperature_unit') != '°C':
            raise ValueError('verified Celsius target required')
        if 'hvac_modes' in row and (not isinstance(row['hvac_modes'], list) or not row['hvac_modes']
                or len(row['hvac_modes']) > len(MODES) or any(not isinstance(m, str) or m not in MODES for m in row['hvac_modes'])):
            raise ValueError('invalid supported HVAC modes')
        if request['service'] == 'set_hvac_mode' and request['data']['hvac_mode'] not in row.get('hvac_modes', []):
            raise ValueError('requested HVAC mode unsupported by target')
    return raw


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Transport:
    def __init__(self, token_file: Path):
        mode = token_file.lstat()
        if (not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077
                or mode.st_uid not in {0, os.geteuid()}):
            raise ValueError('private controller-owned executor credential required')
        self.token = token_file.read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', self.token):
            raise ValueError('invalid executor credential')

    def __call__(self, operation: str, request: dict[str, Any], **extra: Any) -> Any:
        if operation not in {'snapshot', 'execute'}:
            raise ValueError('invalid transport operation')
        body = canonical({'operation': operation, 'action': action(request), **extra}).encode()
        req = urllib.request.Request(ROUTE, data=body, method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=8) as response:
            if response.status != 200:
                raise ValueError('HA executor route unavailable')
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('HA response exceeds budget')
        return json.loads(data)


class Controller:
    def __init__(self, database: Path, transport: Callable[..., Any], clock: Callable[[], float] = time.time):
        self.transport, self.clock = transport, clock
        if database.is_symlink():
            raise ValueError('symlink database refused')
        self.db = sqlite3.connect(database, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS proposals (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, snapshot TEXT,
          digest TEXT, reviewed REAL, approved REAL, expires REAL, result TEXT)''')

    def row(self, proposal_id: str, actor: str | None = None) -> sqlite3.Row:
        if not isinstance(proposal_id, str) or not re.fullmatch(r'[a-f0-9]{32}', proposal_id):
            raise ValueError('invalid proposal ID')
        row = self.db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone()
        if not row or actor is not None and row['actor'] != actor:
            raise ValueError('proposal unavailable to actor')
        return row

    def public(self, row: sqlite3.Row) -> dict[str, Any]:
        state = row['state']
        if state == 'approved' and self.clock() >= row['expires']:
            state = 'expired'
        return {'proposal_id': row['id'], 'actor': row['actor'], 'action': json.loads(row['action']),
                'state': state, 'created_at': row['created'], 'approval_expires_at': row['expires'],
                'result': json.loads(row['result']) if row['result'] else None}

    def propose(self, body: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(body, dict) or set(body) != {'actor', 'action'}
                or not isinstance(body['actor'], str) or body['actor'] not in ACTORS):
            raise ValueError('trusted adapter actor and exact action required')
        request = action(body['action'])
        normalized, now, actor = canonical(request), self.clock(), body['actor']
        self.db.execute('BEGIN IMMEDIATE')
        try:
            active = "(state IN ('proposed','reviewed','executing') OR (state='approved' AND expires>?))"
            existing = self.db.execute('SELECT id FROM proposals WHERE actor=? AND action=? AND ' + active,
                                       (actor, normalized, now)).fetchone()
            if existing:
                proposal_id = existing['id']
            else:
                total = self.db.execute('SELECT count(*) FROM proposals WHERE ' + active, (now,)).fetchone()[0]
                actor_total = self.db.execute('SELECT count(*) FROM proposals WHERE actor=? AND ' + active, (actor, now)).fetchone()[0]
                records = self.db.execute('SELECT count(*) FROM proposals').fetchone()[0]
                if total >= TOTAL_LIMIT or actor_total >= ACTOR_LIMIT or records >= RECORD_LIMIT:
                    raise ValueError('proposal budget reached; operator reconciliation/archive required')
                proposal_id = secrets.token_hex(16)
                self.db.execute('INSERT INTO proposals(id,actor,action,state,created) VALUES(?,?,?,?,?)',
                                (proposal_id, actor, normalized, 'proposed', now))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return self.get(proposal_id, body['actor'])

    def status(self, proposal_id: str) -> dict[str, Any]:
        return self.public(self.row(proposal_id))

    def get(self, proposal_id: str, actor: str) -> dict[str, Any]:
        if actor not in ACTORS:
            raise ValueError('unknown actor')
        return self.public(self.row(proposal_id, actor))

    def list(self) -> list[dict[str, Any]]:
        return [self.public(r) for r in self.db.execute('SELECT * FROM proposals ORDER BY created DESC LIMIT 100')]

    def cancel(self, proposal_id: str, actor: str | None = None) -> dict[str, Any]:
        row = self.row(proposal_id, actor)
        if row['state'] not in {'proposed', 'reviewed', 'approved'}:
            raise ValueError('proposal already consumed or cancelled')
        changed = self.db.execute("UPDATE proposals SET state='cancelled' WHERE id=? AND state IN ('proposed','reviewed','approved')", (proposal_id,))
        if changed.rowcount != 1:
            raise ValueError('proposal no longer cancellable')
        return self.public(self.row(proposal_id))

    def review(self, proposal_id: str) -> dict[str, Any]:
        row = self.row(proposal_id)
        if row['state'] not in {'proposed', 'reviewed'}:
            raise ValueError('review requires unapproved proposal')
        request = action(json.loads(row['action']))
        current = snapshot(self.transport('snapshot', request), request, self.clock())
        digest = hashlib.sha256(canonical({'proposal_id': proposal_id, 'actor': row['actor'],
                                         'action': request, 'snapshot': current}).encode()).hexdigest()
        changed = self.db.execute("UPDATE proposals SET state='reviewed',snapshot=?,digest=?,reviewed=? WHERE id=? AND state IN ('proposed','reviewed')",
                                 (canonical(current), digest, self.clock(), proposal_id))
        if changed.rowcount != 1:
            raise ValueError('proposal changed during review')
        return dict(self.public(self.row(proposal_id)), snapshot=current, approval_digest=digest)

    def approve(self, proposal_id: str, digest: str) -> dict[str, Any]:
        row = self.row(proposal_id)
        now = self.clock()
        if (row['state'] != 'reviewed' or not isinstance(digest, str) or not secrets.compare_digest(digest, row['digest'])
                or not 0 <= now - row['reviewed'] <= SNAPSHOT_TTL):
            raise ValueError('approval requires exact fresh reviewed digest')
        changed = self.db.execute("UPDATE proposals SET state='approved',approved=?,expires=? WHERE id=? AND state='reviewed' AND digest=?",
                                 (now, now + TTL, proposal_id, digest))
        if changed.rowcount != 1:
            raise ValueError('proposal changed during approval')
        return self.public(self.row(proposal_id))

    def execute(self, proposal_id: str) -> dict[str, Any]:
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.row(proposal_id)
            if row['state'] != 'approved' or self.clock() >= row['expires']:
                raise ValueError('unexpired unused approval required')
            request = action(json.loads(row['action']))
            approved_snapshot = json.loads(row['snapshot'])
            expected = hashlib.sha256(canonical({'proposal_id': proposal_id, 'actor': row['actor'],
                                     'action': request, 'snapshot': approved_snapshot}).encode()).hexdigest()
            if not secrets.compare_digest(expected, row['digest']):
                raise ValueError('approved arguments changed')
            current = snapshot(self.transport('snapshot', request), request, self.clock())
            if current['entities'] != approved_snapshot['entities']:
                raise ValueError('target state changed; new proposal and review required')
            if self.clock() >= row['expires']:
                raise ValueError('approval expired during preflight')
            self.db.execute("UPDATE proposals SET state='executing' WHERE id=?", (proposal_id,))
            self.db.execute('COMMIT')  # Consume before the physical effect; never retry an uncertain call.
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        try:
            receipt = self.transport('execute', request, proposal_id=proposal_id,
                                     approval_digest=row['digest'], expected_entities=current['entities'],
                                     approved_at=row['approved'], approval_expires_at=row['expires'])
            if receipt != {'executed': True, 'proposal_id': proposal_id}:
                raise ValueError('unrecognized effect receipt')
            result, state = {'executed': True}, 'executed'
        except Exception:
            result, state = {'executed': None, 'reason': 'transport_uncertain_no_retry'}, 'uncertain'
        self.db.execute('UPDATE proposals SET state=?,result=? WHERE id=? AND state=?',
                        (state, canonical(result), proposal_id, 'executing'))
        return self.public(self.row(proposal_id))


class AgentHandler(BaseHTTPRequestHandler):
    """Only the trusted shared adapter UID may call agent-only endpoints."""
    def log_message(self, *_: Any) -> None:
        pass

    def setup(self) -> None:
        self.request.settimeout(5)
        super().setup()

    def do_POST(self) -> None:
        try:
            _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.server.adapter_uid:
                raise ValueError('trusted adapter required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= MAX_BYTES or self.headers.get('Transfer-Encoding'):
                raise ValueError('bounded JSON required')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON object required')
            controller = self.server.controller
            if self.path == '/propose':
                result = controller.propose(body)
            elif (self.path in {'/status', '/cancel'} and set(body) == {'proposal_id', 'actor'}
                    and isinstance(body['actor'], str) and body['actor'] in ACTORS):
                method = controller.get if self.path == '/status' else controller.cancel
                result = method(body['proposal_id'], body['actor'])
            else:
                raise ValueError('agent operation unavailable')
            status = 200
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
            status, result = 400, {'error': 'HA proposal operation refused'}
        data = canonical(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def prepare_socket(path: Path) -> None:
    """Reclaim only our socket after a crash; never replace an active listener."""
    try:
        original = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(original.st_mode) or original.st_uid != os.geteuid():
        raise ValueError('existing socket is not controller-owned')
    with socket.socket(socket.AF_UNIX) as probe:
        probe.settimeout(0.5)
        try:
            probe.connect(str(path))
        except OSError as error:
            if error.errno != errno.ECONNREFUSED:
                raise ValueError('existing socket cannot safely be reclaimed') from None
        else:
            raise ValueError('controller socket already has an active listener')
    current = path.lstat()
    if (current.st_ino, current.st_dev, current.st_uid, current.st_mode) != (
            original.st_ino, original.st_dev, original.st_uid, original.st_mode):
        raise ValueError('socket changed during stale-listener check')
    path.unlink()


def enter_identity(command: str) -> None:
    """Authorize operators while root; all files and SQLite journals stay service-owned."""
    if command != 'serve' and os.geteuid() != 0:
        raise ValueError('trusted root operator required')
    owner = pwd.getpwnam('homecompute-ha-actions')
    if owner.pw_uid == 0 or owner.pw_gid == 0:
        raise ValueError('dedicated unprivileged controller identity required')
    if command == 'serve':
        if os.geteuid() != owner.pw_uid or os.getegid() != owner.pw_gid:
            raise ValueError('service must run as its dedicated controller identity')
    else:
        os.setgroups([])
        os.setgid(owner.pw_gid)
        os.setuid(owner.pw_uid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'list', 'status', 'review', 'approve', 'execute', 'cancel'])
    parser.add_argument('proposal_id', nargs='?')
    parser.add_argument('--digest')
    parser.add_argument('--adapter-user', default='homecompute-openclaw')
    args = parser.parse_args()
    try:
        enter_identity(args.command)
        os.umask(0o077)
        if (STATE.is_symlink() or not STATE.is_dir() or STATE.stat().st_mode & 0o077
                or STATE.stat().st_uid != os.geteuid()):
            raise ValueError('private controller state directory required')
        controller = Controller(STATE / 'actions.sqlite3', Transport(STATE / 'executor-token'))
        if args.command == 'serve':
            if args.proposal_id or args.digest:
                raise ValueError('serve takes no proposal arguments')
            prepare_socket(SOCKET)
            with socketserver.UnixStreamServer(str(SOCKET), AgentHandler) as server:
                server.controller = controller
                server.adapter_uid = pwd.getpwnam(args.adapter_user).pw_uid
                os.chmod(SOCKET, 0o660)
                server.serve_forever()
        else:
            if args.command == 'list':
                if args.proposal_id or args.digest:
                    raise ValueError('list takes no arguments')
                result = controller.list()
            else:
                if not args.proposal_id or (args.command == 'approve') != bool(args.digest):
                    raise ValueError('exact operator arguments required')
                method = getattr(controller, args.command)
                result = method(args.proposal_id, args.digest) if args.command == 'approve' else method(args.proposal_id)
            print(canonical(result))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        print('HA controller operation refused; inspect private operator state.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
