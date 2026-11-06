#!/usr/bin/env python3
"""Offline incident repair for the qualified OpenShell 0.0.116 owner sandbox.

Only Error -> Stopped is permitted. Ready must be established by native start.
No identity, policy, credential, recreate journal or application data is replaced.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
DB = Path.home() / '.local/state/nemoclaw/openshell-docker-gateway/openshell.db'
ID = 'd90b7fc4-1506-4d12-b055-f0ab2aa4dcc7'
NAME = 'hermes-personal-v23'
CONTAINER = 'ba08f69f253d44bb5f8f16e1eceb329ea93b20a1d3b5cfcd3c5a22bdb89bd2f0'
IMAGE = 'sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311'


def varint(data, offset):
    value = shift = 0
    for _ in range(10):
        byte = data[offset]; offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
    raise ValueError('invalid protobuf varint')


def fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError('invalid protobuf field')
        if wire == 0:
            start = offset
            value, offset = varint(data, offset)
        elif wire == 2:
            size, offset = varint(data, offset)
            start = offset
            if offset + size > len(data):
                raise ValueError('truncated protobuf')
            value = data[offset:offset + size]; offset += size
        else:
            raise ValueError('unexpected qualified protobuf wire type')
        yield field, wire, value, start, offset


def one(rows, field, wire):
    found = [row for row in rows if row[0] == field and row[1] == wire]
    if len(found) != 1:
        raise ValueError('missing or duplicate qualified protobuf field')
    return found[0]


def candidate(data):
    rows = list(fields(data))
    metadata = list(fields(one(rows, 1, 2)[2]))
    if one(metadata, 1, 2)[2] != ID.encode() or one(metadata, 2, 2)[2] != NAME.encode():
        raise ValueError('owner identity differs')
    runtime = one(rows, 3, 2)
    state = list(fields(runtime[2]))
    if (one(state, 1, 2)[2] != ('openshell-default--' + NAME + '-' + ID).encode()
            or one(state, 2, 2)[2] != CONTAINER.encode()):
        raise ValueError('original retained container identity differs')
    condition = list(fields(one(state, 5, 2)[2]))
    if [one(condition, n, 2)[2] for n in (1, 2, 3, 4)] != [
            b'Ready', b'False', b'MainProcessExited', b'Canonical main process exited']:
        raise ValueError('not the diagnosed canonical-main startup failure')
    phase = one(state, 6, 0)
    if phase[2] not in (3, 7) or phase[4] - phase[3] != 1 or one(state, 7, 0)[2] != 8:
        raise ValueError('not the diagnosed terminal Error/Stopped state')
    offset = runtime[3] + phase[3]
    after = data[:offset] + b'\x07' + data[offset + 1:]
    assert len(after) == len(data)
    return after


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('recovery guard failed: ' + args[0])
    return result.stdout


def repair(backup, revision, apply=False):
    if command(['openshell', '--version']).strip() != b'openshell 0.0.116':
        raise ValueError('qualified OpenShell version required')
    container = json.loads(command(['docker', 'inspect', CONTAINER]))[0]
    if container['Image'] != IMAGE or container['Name'] != '/openshell-default--' + NAME + '-' + ID:
        raise ValueError('qualified retained container required')
    if any(p.is_symlink() for p in (backup, *backup.parents)) or backup.stat().st_mode & 0o077:
        raise ValueError('private whole-sandbox archive required')
    with tarfile.open(backup) as archive:
        if archive.extractfile('sandbox/.hermes/runtime/state.db').read(16) != b'SQLite format 3\x00':
            raise ValueError('real application database missing from backup')
        archived_config = archive.extractfile('sandbox/.hermes/config.yaml').read()
    spec = importlib.util.spec_from_file_location('native_config', ROOT / 'scripts/recover-hermes-webapp-port.py')
    incident = importlib.util.module_from_spec(spec); spec.loader.exec_module(incident)
    if incident.document(CONTAINER, 'sandbox/.hermes/config.yaml')[0] != archived_config:
        raise ValueError('application config changed since the selected full backup')
    if DB.is_symlink():
        raise ValueError('regular native database required')
    connection = sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True)
    row = connection.execute('SELECT id, payload FROM objects WHERE object_type=? AND name=? AND workspace=?',
                             ('sandbox', NAME, 'default')).fetchall()
    if len(row) != 1 or row[0][0] != ID:
        raise ValueError('unique original native owner record required')
    before = bytes(row[0][1]); after = candidate(before)
    with backup.open('rb') as source:
        backup_digest = hashlib.file_digest(source, 'sha256').hexdigest()
    report = {'revision': revision, 'sandbox': NAME, 'container': CONTAINER,
              'before_sha256': hashlib.sha256(before).hexdigest(),
              'after_sha256': hashlib.sha256(after).hexdigest(),
              'backup_sha256': backup_digest,
              'applied': apply, 'already_stopped': before == after}
    if not apply:
        connection.close(); return report
    state = subprocess.run(['systemctl', '--user', 'is-active', 'nemoclaw-openshell-gateway.service'],
                           capture_output=True, timeout=10).stdout.strip()
    if state != b'inactive' or container['State']['Running']:
        raise ValueError('stop the selected gateway and retained container before offline repair')
    if before == after:
        connection.close(); return report
    directory = backup.parent / ('native-state-recovery-' + str(time.time_ns()))
    directory.mkdir(mode=0o700)
    saved = directory / 'openshell.db'
    saved.touch(mode=0o600)
    with sqlite3.connect(saved) as destination:
        connection.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('native database backup integrity failed')
    connection.close()
    connection = sqlite3.connect(DB)
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        count = connection.execute('UPDATE objects SET payload=? WHERE id=? AND object_type=? AND payload=?',
                                   (after, ID, 'sandbox', before)).rowcount
        if count != 1:
            raise ValueError('native state changed before guarded repair')
        if connection.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('native database integrity failed')
    connection.close()
    report['native_database_backup'] = str(saved)
    receipt = directory / 'receipt.json'
    receipt.write_text(json.dumps(report)); receipt.chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('native_publication', ROOT / 'scripts/setup-hermes-mcp.py')
    publication = importlib.util.module_from_spec(spec); spec.loader.exec_module(publication)
    publication.published(args.revision)
    print(json.dumps(repair(args.backup, args.revision, args.apply)))


if __name__ == '__main__': main()
