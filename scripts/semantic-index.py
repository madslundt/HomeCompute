#!/usr/bin/env python3
"""Inspect, backfill or delete one service-owned archive; no source text is printed."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from semantic_retrieval import RetrievalIndex

ROOT = Path('/var/lib/homecompute-openclaw')
INDEXES = {
    'school': (ROOT / 'config/retrieval/owner-school.sqlite3', 'owner-school'),
    'research': (ROOT / 'config/retrieval/public-research.sqlite3', 'public-research'),
    'operations': (ROOT / 'communication/adapter/retrieval/operations.sqlite3', 'owner-operations'),
}
INDEXES.update({
    'workflow-' + profile: (ROOT / ('config/retrieval/workflow-' + profile + '.sqlite3'), 'workflow-' + profile)
    for profile in ('shopping', 'storage', 'research', 'news', 'battery')
})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', choices=INDEXES)
    parser.add_argument('action', choices=('status', 'warm', 'delete'))
    parser.add_argument('--source', help='exact source ID from status; deletion also removes vectors and prevents replay')
    args = parser.parse_args()
    if (args.action == 'delete') != (args.source is not None):
        parser.error('--source is required only for delete')
    path, namespace = INDEXES[args.index]
    if not path.exists():
        print(json.dumps({'index': args.index, 'initialized': False})); return 0
    try:
        index = RetrievalIndex(path, namespace=namespace)
        if args.action == 'delete':
            index.delete(args.source)
            result = {'deleted': True, 'replay_blocked': True}
        elif args.action == 'warm':
            result = {'embedded_passages': index.warm()}
        else:
            with closing(index._db()) as db:
                result = {'sources': [row[0] for row in db.execute('SELECT id FROM sources ORDER BY id')],
                          'passages': db.execute('SELECT count(*) FROM passages').fetchone()[0],
                          'deleted_sources': db.execute('SELECT count(*) FROM tombstones').fetchone()[0]}
        print(json.dumps({'index': args.index, **result})); return 0
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({'error': 'archive operation unavailable; use its service owner account'})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
