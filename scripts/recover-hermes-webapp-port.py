#!/usr/bin/env python3
"""Guarded incident repair of the retained Hermes container; never delete its state or records."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311'
CONFIG = 'sandbox/.hermes/config.yaml'
RECEIPT = 'sandbox/.local/share/homecompute/speech/receipt.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError('guarded recovery operation failed: ' + args[0])
    return result.stdout


def document(container, path):
    data = run(['docker', 'cp', container + ':/' + path, '-'])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        rows = [row for row in archive.getmembers() if row.isfile()]
        if len(rows) != 1:
            raise ValueError('one regular managed document required')
        row = rows[0]
        return archive.extractfile(row).read(), row


def candidate(raw):
    import yaml
    spec = importlib.util.spec_from_file_location('speech_candidate', ROOT / 'deploy/hermes/speech/configure.py')
    payload = importlib.util.module_from_spec(spec); spec.loader.exec_module(payload)
    before = yaml.safe_load(raw)
    if (before['model']['default'] != 'automation-moe' or before['webapp']['port'] not in (8646, 18796)
            or before['webapp']['public_url'] != 'https://agents.home.arpa/hermes'
            or not before['dashboard']['basic_auth']):
        raise ValueError('known authenticated owner speech configuration required')
    after = payload.candidate(before)
    # Its only semantic corrections are the reserved port and the persisted
    # restart-scoped MCP handle. All other fields must already be source-owned.
    expected = json.loads(json.dumps(before))
    expected['webapp']['port'] = 18796
    header = expected['mcp_servers']['homecompute']['headers']['Authorization']
    if header.startswith('Bearer openshell:resolve:') and 'HOMECOMPUTE_HERMES_MCP_TOKEN' in header:
        expected['mcp_servers']['homecompute']['headers']['Authorization'] = 'Bearer ${HOMECOMPUTE_HERMES_MCP_TOKEN}'
    if after != expected:
        raise ValueError('repair would change settings outside the diagnosed incident')
    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False)
    return yaml.dump(after, Dumper=Dumper, sort_keys=False).encode()


def repair(container, backup, revision, apply=False):
    import re
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('immutable complete container ID required')
    state = json.loads(run(['docker', 'inspect', container]))[0]
    if state['Image'] != IMAGE or not state['Name'].startswith('/openshell-default--hermes-personal-v23-'):
        raise ValueError('retained qualified owner container required')
    if any(p.is_symlink() for p in (backup, *backup.parents)) or backup.stat().st_mode & 0o077:
        raise ValueError('private retained whole-sandbox archive required')
    with tarfile.open(backup) as archive:
        original = archive.extractfile(CONFIG).read()
        # Require the real state database as well as the config before repair.
        if not any(row.isfile() and row.name.endswith('/runtime/state.db') for row in archive.getmembers()):
            raise ValueError('retained application database required')
    after = candidate(original)
    raw, config_meta = document(container, CONFIG)
    if raw == after and state['State']['Running']:
        receipt = json.loads(document(container, RECEIPT)[0])
        if receipt['config_after'] != sha(raw):
            raise ValueError('recovered receipt differs')
        return {'revision': revision, 'already_repaired': True, 'state_and_native_records_retained': True}
    if state['State']['Running']:
        raise ValueError('stop before modifying an unreconciled container')
    if raw != original:
        raise ValueError('container changed since its complete retained archive')
    receipt_raw, receipt_meta = document(container, RECEIPT)
    receipt = json.loads(receipt_raw)
    if receipt['config_after'] != sha(raw):
        raise ValueError('verified source speech receipt required')
    receipt.update(config_after=sha(after), recovery_revision=revision)
    report = {'revision': revision, 'container': container, 'backup_sha256': sha(backup.read_bytes()),
              'config_before': sha(raw), 'config_after': sha(after), 'applied': apply,
              'state_and_native_records_retained': True}
    if not apply:
        return report
    directory = backup.parent / ('port-recovery-' + str(time.time_ns()))
    directory.mkdir(mode=0o700)
    for name, data in [('config-before.yaml', raw), ('receipt-before.json', receipt_raw)]:
        path = directory / name; path.write_bytes(data); path.chmod(0o600)
    encoded = io.BytesIO()
    with tarfile.open(fileobj=encoded, mode='w') as archive:
        for name, data, meta in [(CONFIG, after, config_meta),
                                 (RECEIPT, json.dumps(receipt).encode(), receipt_meta)]:
            info = tarfile.TarInfo(name.removeprefix('sandbox/'))
            info.size = len(data); info.uid = meta.uid; info.gid = meta.gid; info.mode = meta.mode
            archive.addfile(info, io.BytesIO(data))
    run(['docker', 'cp', '-', container + ':/sandbox'], encoded.getvalue())
    if document(container, CONFIG)[0] != after:
        raise ValueError('incident config readback failed; retain the archive')
    # Direct driver restart is limited to this incident's existing identity.
    # It does not reset the OpenShell database, recreate resources or import data.
    run(['docker', 'start', container])
    path = directory / 'receipt.json'; path.write_text(json.dumps(report)); path.chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--container-id', required=True)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('recovery_publication', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    core.published(args.revision)
    print(json.dumps(repair(args.container_id, args.backup, args.revision, args.apply)))


if __name__ == '__main__': main()
