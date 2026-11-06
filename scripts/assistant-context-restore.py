#!/usr/bin/env python3
"""Offline assistant-context restore staging and deletion-ledger replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_recovery import (SCOPES, complete_restore, stage_restore,
    write_deletion_ledger)
from assistant_context_store import ContextStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('stage', help='verify an archive in isolated scratch and leave it fenced')
    stage.add_argument('--archive', required=True, type=Path)
    stage.add_argument('--target', required=True, type=Path)
    export = commands.add_parser('export-ledger', help='export content-free tombstones; freshness remains unverified')
    export.add_argument('--state', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--scope', action='append', choices=sorted(SCOPES), required=True)
    complete = commands.add_parser('complete', help='replay separately admitted current source tombstones')
    complete.add_argument('--target', required=True, type=Path)
    complete.add_argument('--ledger', required=True, type=Path)
    complete.add_argument('--ledger-sha256', required=True)
    complete.add_argument('--admission', required=True, type=Path)
    complete.add_argument('--admission-sha256', required=True)
    args = parser.parse_args(argv)
    if args.command == 'stage':
        result = stage_restore(args.archive, args.target)
    elif args.command == 'export-ledger':
        database = args.state / 'context.sqlite3'
        if database.is_symlink() or not database.is_file():
            raise ValueError('existing private ContextStore database required')
        store = ContextStore(database)
        digest = write_deletion_ledger(store, [SCOPES[key] for key in args.scope], args.output)
        result = {'freshness': 'unverified', 'ledger_sha256': digest, 'scope_count': len(args.scope)}
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
