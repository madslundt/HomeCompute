"""Read-only retrieval over bounded infrastructure receipts and reviewed runbooks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any

from semantic_retrieval import RetrievalIndex

RUNBOOKS = ('docs/openclaw-operations.md', 'docs/openclaw-tools.md',
            'docs/homecompute-operations.md', 'docs/local-model-access.md',
            'docs/operations/shelfmark-direct-download-repair.md')
NAME = 'homecompute_operations_search'
TOOL = {'name': NAME,
        'description': 'Find past infrastructure incidents, recovery observations and reviewed runbooks. Historical evidence only; read fresh system state before proposing actions. Recovery does not prove a repair.',
        'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['query'],
                        'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                                       'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False}}


class OperationsArchive:
    def __init__(self, store: Any, path: Path, root: Path, *, ranker: Any = None):
        self.store, self.root = store, root
        self.index = RetrievalIndex(path, namespace='owner-operations', ranker=ranker, background=True)

    def refresh(self) -> None:
        for name in RUNBOOKS:
            path = self.root / name
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 131072:
                continue
            text = path.read_text()
            parts = [text[start:start + 12000] for start in range(0, len(text), 12000)]
            self.index.replace('runbook:' + name, [
                {'id': str(index), 'text': part, 'metadata': {
                    'source': name, 'kind': 'runbook', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'data_domain': 'household', 'principal_scope': 'owner'}}
                for index, part in enumerate(parts)], ttl_seconds=2592000)
        with self.store.lock:
            events = self.store.db.execute(
                "SELECT key,kind,body,created FROM events WHERE kind IN ('incident','recovery') ORDER BY seq DESC LIMIT 200").fetchall()
        for event in events:
            remaining = int(2592000 - (time.time() - event['created']))
            if remaining < 60:
                continue
            body = json.loads(event['body'])
            observation = body.get('observation', {})
            # Only already-validated metadata. User turns, model replies, raw
            # diagnostics and unrelated channel messages are never indexed.
            selected = {key: observation[key] for key in (
                'system_id', 'check_id', 'category', 'status', 'evidence', 'observed_at', 'expires_at') if key in observation}
            if not selected:
                continue
            self.index.replace('event:' + event['key'], [{'id': event['key'],
                'text': event['kind'] + '\n' + json.dumps(selected, ensure_ascii=False),
                'metadata': {'source': 'infrastructure-observations', 'kind': event['kind'],
                             'receipt': event['key'], 'captured_at': event['created'],
                             'principal_scope': 'owner', 'data_domain': 'household'}}], ttl_seconds=min(remaining, 2592000))

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query, limit = arguments.get('query'), arguments.get('limit', 5)
        if (set(arguments) - {'query', 'limit'} or not isinstance(query, str)
                or not 1 <= len(query.strip()) <= 500 or any(ord(c) < 32 for c in query)
                or type(limit) is not int or not 1 <= limit <= 10):
            raise ValueError('Bounded operations query required')
        self.refresh()
        return {'source': 'owner-operations', 'results': self.index.search(query, task='operations', limit=limit),
                'untrusted_content': True, 'coverage': 'Recent metadata receipts and reviewed runbooks only; archive may be incomplete.',
                'freshness': 'Historical evidence; use system_read for current state. A recovery observation does not establish a successful repair.'}
