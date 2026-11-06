#!/usr/bin/env python3
"""Offline assistant-context restore staging and deletion-ledger replay."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_recovery import (ADMISSION_OUTPUT_ROOT, NAS_RECEIPT_ROOT, SCOPES,
    admit_empty_synthetic_ledger, complete_restore, export_live_deletion_ledger, stage_restore,
    stage_verified_extracted, write_deletion_ledger)
from assistant_context_store import ContextStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('stage', help='verify an archive in isolated scratch and leave it fenced')
    stage.add_argument('--archive', required=True, type=Path)
    stage.add_argument('--target', required=True, type=Path)
    verified_stage = commands.add_parser('stage-verified-extracted', help='fence an existing extraction bound to the NAS verifier receipt')
    verified_stage.add_argument('--target', required=True, type=Path)
    verified_stage.add_argument('--snapshot', required=True)
    verified_stage.add_argument('--archive', required=True)
    verified_stage.add_argument('--revision', required=True)
    export = commands.add_parser('export-ledger', help='export content-free tombstones; freshness remains unverified')
    export.add_argument('--state', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--scope', action='append', choices=sorted(SCOPES), required=True)
    live_export = commands.add_parser('export-live-ledger', help='root-only read-only export of all enabled live scopes; freshness remains unverified')
    live_export.add_argument('--output-name', required=True)
    complete = commands.add_parser('complete', help='replay separately admitted current source tombstones')
    complete.add_argument('--target', required=True, type=Path)
    complete.add_argument('--ledger', required=True, type=Path)
    complete.add_argument('--ledger-sha256', required=True)
    complete.add_argument('--admission', required=True, type=Path)
    complete.add_argument('--admission-sha256', required=True)
    admit = commands.add_parser('admit-empty-synthetic', help='root-only point-in-time proof for an empty synthetic-only restore')
    admit.add_argument('--target', required=True, type=Path)
    admit.add_argument('--ledger', required=True, type=Path)
    admit.add_argument('--ledger-sha256', required=True)
    admit.add_argument('--output-name', required=True)
    args = parser.parse_args(argv)
    if args.command == 'stage':
        result = stage_restore(args.archive, args.target)
    elif args.command == 'stage-verified-extracted':
        if os.geteuid() != 0:
            raise PermissionError('root-owned recovery operator required')
        if not re.fullmatch(r'[0-9a-f]{40}', args.revision) or ROOT.resolve() != Path('/srv/homecompute/releases') / args.revision:
            raise ValueError('selected published release path required')
        result = stage_verified_extracted(args.target, snapshot=args.snapshot, archive=args.archive,
            source_revision=args.revision, receipt_root=NAS_RECEIPT_ROOT)
    elif args.command == 'export-ledger':
        database = args.state / 'context.sqlite3'
        if database.is_symlink() or not database.is_file():
            raise ValueError('existing private ContextStore database required')
        store = ContextStore(database)
        digest = write_deletion_ledger(store, [SCOPES[key] for key in args.scope], args.output)
        result = {'freshness': 'unverified', 'ledger_sha256': digest, 'scope_count': len(args.scope)}
    elif args.command == 'export-live-ledger':
        if os.geteuid() != 0:
            raise PermissionError('root-owned recovery operator required')
        if not re.fullmatch(r'assistant-context-ledger-[A-Za-z0-9_-]{1,64}\.json', args.output_name):
            raise ValueError('bounded private ledger output name required')
        result = export_live_deletion_ledger(ADMISSION_OUTPUT_ROOT / args.output_name)
    elif args.command == 'admit-empty-synthetic':
        if os.geteuid() != 0:
            raise PermissionError('root-owned source-controller operator required')
        if not re.fullmatch(r'assistant-context-admission-[A-Za-z0-9_-]{1,64}\.json', args.output_name):
            raise ValueError('bounded private admission output name required')
        result = admit_empty_synthetic_ledger(args.target, args.ledger,
            expected_sha256=args.ledger_sha256,
            output=ADMISSION_OUTPUT_ROOT / args.output_name)
    else:
        result = complete_restore(args.target, args.ledger, expected_sha256=args.ledger_sha256,
            admission_path=args.admission, expected_admission_sha256=args.admission_sha256)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'error': str(error)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
