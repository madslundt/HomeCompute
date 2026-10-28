"""Profile-isolated evidence retrieval for fixed scheduled n8n workflows."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re
import time
import threading
from typing import Any, Callable
from urllib.parse import urlsplit

from embedding_client import EmbeddingClient, bounded_text
from semantic_retrieval import RetrievalIndex, SemanticRanker, cached_vector

PROFILES = {'shopping', 'storage', 'research', 'news', 'calendar', 'school', 'battery'}
TASKS = {'shopping': 'shopping', 'storage': 'storage', 'research': 'research', 'news': 'research',
         'calendar': 'calendar', 'school': 'school', 'battery': 'operations'}
CATEGORIES = ('Frugt & grønt', 'Mejeri', 'Kød & fisk', 'Brød', 'Kolonial', 'Frost',
              'Drikkevarer', 'Husholdning', 'Personlig pleje', 'Andet')
METADATA = {'title', 'url', 'observed_at', 'published_at', 'published_date', 'kind', 'category',
            'revision', 'source', 'source_tool', 'source_record_id', 'source_timestamp',
            'calendar_id', 'day', 'role', 'start', 'end', 'captured_at'}


def text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value.encode()) > maximum or (not empty and not value.strip()):
        raise ValueError('Bounded text required')
    if any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise ValueError('Control characters refused')
    return value


def records(value: Any, maximum: int = 128) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Bounded records required')
    output, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) - {'id', 'text', 'metadata'}:
            raise ValueError('Finite record fields required')
        identity = text(row.get('id'), 512)
        if identity in seen:
            raise ValueError('Duplicate record identity')
        seen.add(identity)
        metadata = row.get('metadata', {})
        if not isinstance(metadata, dict) or set(metadata) - METADATA:
            raise ValueError('Finite source metadata required')
        for key, item in metadata.items():
            if item is None:
                continue
            if type(item) in {int, float} and math.isfinite(item):
                continue
            text(item, 2000, empty=True)
            if key == 'url':
                url = urlsplit(item)
                from openclaw_services import public_url
                if url.scheme not in {'http', 'https'} or public_url(item) is None:
                    raise ValueError('Source URL required')
        if len(json.dumps(metadata, allow_nan=False).encode()) > 7000:
            raise ValueError('Bounded metadata required')
        output.append({'id': identity, 'text': text(row.get('text'), 16000), 'metadata': metadata})
    return output


def storage_examples() -> list[dict[str, Any]]:
    """Only confirmed categories from the existing finite HA stock snapshot."""
    from openclaw_services import private
    from openclaw_household import TOKEN_PATH
    from openclaw_ha_websocket import HomeAssistantSocket
    token = private(TOKEN_PATH, 4096).decode().strip()
    with HomeAssistantSocket(token) as client:
        snapshot = client.snapshot()
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('items'), list) or len(snapshot['items']) > 5000:
        raise ValueError('Storage snapshot unavailable')
    return [{'id': row['id'], 'name': row['name'], 'category': row['category'],
             'revision': row.get('revision', 0)} for row in snapshot['items']
            if isinstance(row, dict) and row.get('category') in CATEGORIES
            and isinstance(row.get('id'), str) and isinstance(row.get('name'), str)]


class WorkflowRetrieval:
    def __init__(self, root: Path, *, ranker: Any = None,
                 stock: Callable[[], list[dict]] | None = storage_examples):
        self.root = root.resolve()
        self.ranker = ranker or SemanticRanker()
        self.stock = stock
        self.indexes: dict[str, RetrievalIndex] = {}
        self.lock = threading.RLock()

    def index(self, profile: str) -> RetrievalIndex:
        with self.lock:
            return self._index(profile)

    def _index(self, profile: str) -> RetrievalIndex:
        if profile not in self.indexes:
            if profile == 'school':
                path, namespace = self.root / 'owner-school.sqlite3', 'owner-school'
            else:
                path, namespace = self.root / ('workflow-' + profile + '.sqlite3'), 'workflow-' + profile
            self.indexes[profile] = RetrievalIndex(path, namespace=namespace, ranker=self.ranker, background=True)
        return self.indexes[profile]

    def request(self, body: Any, profile: str) -> dict[str, Any]:
        if (profile not in PROFILES or not isinstance(body, dict) or body.get('profile') != profile
                or set(body) - {'profile', 'operation', 'query', 'source_id', 'records', 'items', 'examples', 'limit'}):
            raise ValueError('Fixed workflow profile required')
        operation = body.get('operation')
        limit = body.get('limit', 5)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Bounded result limit required')
        if operation == 'categorize':
            if profile not in {'shopping', 'storage'}:
                raise ValueError('Category profile required')
            return self.categorize(body, profile)
        if operation == 'pairs':
            if profile != 'calendar':
                raise ValueError('Calendar pairs only')
            return self.pairs(records(body.get('records', []), 64))
        if operation == 'rank':
            rows = records(body.get('records', []))
            if not rows:
                return {'results': [], 'available': False}
            query = text(body.get('query'), 1200)
            ranked = self.ranker.rank(query, rows, task=TASKS[profile], limit=limit)
            returned = {row['id'] for row in ranked}
            return {'results': (ranked + [{**row, 'retrieval_score': None, 'lexical_score': None, 'semantic_score': None}
                                         for row in rows if row['id'] not in returned])[:limit],
                    'available': any(row.get('semantic_score') is not None for row in ranked)}
        if operation == 'school_ingest':
            if profile != 'school':
                raise ValueError('School archive required')
            rows = records(body.get('records', []))
            if any(len(row['id']) > 502 for row in rows):
                raise ValueError('Bounded scheduled source identity required')
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            for row in rows:
                row['metadata'].update(source='aula-scheduled', captured_at=now)
                row['metadata'].setdefault('source_tool', 'scheduled_aula_collector')
            for row in rows:
                # Identified posts survive shifting partial pages; edits replace
                # their own passages without deleting other retrieved records.
                self.index(profile).replace('scheduled:' + row['id'], [row])
            return {'remembered': len(rows), 'available': True}
        if operation not in {'remember', 'search'} or profile == 'calendar':
            raise ValueError('Workflow operation refused')
        source_id = body.get('source_id')
        if source_id is not None:
            text(source_id, 512)
        if profile == 'research' and (not isinstance(source_id, str)
                or not re.fullmatch(r'notion:(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', source_id)):
            raise ValueError('Exact Notion page scope required')
        if operation == 'remember':
            text(source_id, 512)
            rows = records(body.get('records', []))
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            for row in rows:
                row['metadata'].setdefault('captured_at', now)
                if profile == 'school':
                    row['metadata'].setdefault('source', 'aula-scheduled')
                    row['metadata'].setdefault('source_tool', 'scheduled_aula_collector')
            self.index(profile).replace(source_id, rows)
            return {'remembered': len(rows), 'available': True}
        query = text(body.get('query'), 1200)
        hits = self.index(profile).search(query, task=TASKS[profile], limit=limit, source_id=source_id)
        return {'results': hits, 'available': any(row.get('semantic_score') is not None for row in hits)}

    def categorize(self, body: dict, profile: str) -> dict:
        items, examples = body.get('items', []), body.get('examples', [])
        if not isinstance(items, list) or len(items) > 50 or not isinstance(examples, list) or len(examples) > 5000:
            raise ValueError('Bounded categories required')
        seen, validated = set(), []
        for item in items:
            if not isinstance(item, dict) or set(item) != {'id', 'name'}:
                raise ValueError('Category item identity required')
            identity, name = text(item['id'], 512), text(item['name'], 1000)
            if identity in seen:
                raise ValueError('Duplicate category item')
            seen.add(identity)
            validated.append((identity, name))
        if profile == 'storage' and self.stock is not None:
            try:
                examples = self.stock()
            except (OSError, ValueError):
                examples = None  # Retain prior confirmed examples on outage.
        if examples is not None and (profile == 'storage' or 'examples' in body):
            prepared, seen = [], set()
            for row in examples:
                if not isinstance(row, dict) or set(row) - {'id', 'name', 'category', 'revision'} or row.get('category') not in CATEGORIES:
                    raise ValueError('Confirmed labeled example required')
                identity = text(row.get('id'), 512)
                revision = row.get('revision', 0)
                if identity in seen or type(revision) not in {str, int}:
                    raise ValueError('Unique confirmed example and revision required')
                seen.add(identity)
                if isinstance(revision, str):
                    text(revision, 512, empty=True)
                prepared.append({'id': identity, 'text': text(row.get('name'), 1000),
                                 'metadata': {'category': row['category'], 'revision': row.get('revision', 0)}})
            self.index(profile).replace('confirmed-categories', prepared[:200])
        output, deadline = [], time.monotonic() + 11
        for identity, name in validated:
            hits = self.index(profile).search(name, task=TASKS[profile], limit=5) if time.monotonic() < deadline else []
            matches = [{'name': hit['text'], 'category': hit['metadata']['category'],
                        'score': hit.get('semantic_score')} for hit in hits if hit['metadata'].get('category') in CATEGORIES]
            votes = Counter(row['category'] for row in matches[:3])
            best = matches[0] if matches else None
            score = best['score'] if best else None
            runner_up = next((row.get('score') for row in matches if best and row['category'] != best['category']), None)
            margin = score - runner_up if score is not None and runner_up is not None else None
            category = (best['category'] if best and score is not None and score >= 0.82 and margin is not None
                        and margin >= 0.08 and votes[best['category']] >= 2 else None)
            output.append({'id': identity, 'category': category, 'score': score, 'margin': margin, 'examples': matches})
        return {'matches': output, 'available': any(row['score'] is not None for row in output)}

    def pairs(self, rows: list[dict]) -> dict:
        if len(rows) < 2:
            return {'pairs': [], 'available': False}
        try:
            client = (EmbeddingClient(timeout=10) if isinstance(self.ranker, SemanticRanker)
                      and isinstance(self.ranker.client, EmbeddingClient) else self.ranker)
            vectors = client.embed([bounded_text(row['text']) for row in rows])
            if len(vectors) != len(rows) or any(cached_vector(vector) is None for vector in vectors):
                raise ValueError('Missing pair vectors')
            output = []
            for left, row in enumerate(rows):
                for right in range(left + 1, len(rows)):
                    other = rows[right]
                    if any(row['metadata'].get(key) != other['metadata'].get(key) for key in ('calendar_id', 'day', 'role')):
                        continue
                    score = sum(a * b for a, b in zip(vectors[left], vectors[right]))
                    if math.isfinite(score) and score >= 0.80:
                        output.append({'left_id': row['id'], 'right_id': other['id'], 'score': score})
            return {'pairs': sorted(output, key=lambda row: -row['score'])[:200], 'available': True}
        except (OSError, ValueError, TypeError, IndexError):
            return {'pairs': [], 'available': False}
