"""Hybrid local retrieval; source owners retain identity, scope and write authority."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Any
import unicodedata

from embedding_client import EmbeddingClient, REVISION, TASKS, bounded_text, query_text, DIMENSIONS


def cached_vector(value: Any) -> list[float] | None:
    try:
        vector = json.loads(value) if isinstance(value, str) else value
        if (not isinstance(vector, list) or len(vector) != DIMENSIONS
                or any(type(number) not in {int, float} or not math.isfinite(number) for number in vector)
                or not 0.99 <= sum(number * number for number in vector) <= 1.01):
            return None
        return vector
    except (ValueError, TypeError, OverflowError):
        return None


def tokens(value: str) -> set[str]:
    return set(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


def lexical(query: str, text: str) -> float:
    query_tokens = tokens(query)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & tokens(text)) / len(query_tokens)
    return max(overlap, 1.0 if query.casefold() in text.casefold() else 0.0)


class SemanticRanker:
    def __init__(self, client: Any = None):
        self.client = client or EmbeddingClient()
        self.available = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(texts)

    def rank(self, query: str, records: list[dict[str, Any]], *, task: str,
             text_key: str = 'text', limit: int = 20) -> list[dict[str, Any]]:
        if task not in TASKS or not isinstance(query, str) or not query.strip() or not 1 <= limit <= 5000:
            raise ValueError('Bounded query and retrieval task required')
        if len(records) > 5000 or any(not isinstance(row.get(text_key), str) for row in records):
            raise ValueError('Bounded text records required')
        # Include lexical hits before the finite semantic candidate budget.
        ordered = sorted(enumerate(records), key=lambda pair: (-lexical(query, pair[1][text_key]), pair[0]))
        candidates = [(index, row) for index, row in ordered if cached_vector(row.get('_vector')) is not None]
        candidates.extend((index, row) for index, row in ordered
                          if cached_vector(row.get('_vector')) is None and len(candidates) < 80)
        lexical_scores = {index: lexical(query, row[text_key]) for index, row in candidates}
        semantic_scores: dict[int, float] = {}
        if candidates:
            try:
                missing = [(index, row) for index, row in candidates if cached_vector(row.get('_vector')) is None
                           and not row.get('_lexical_only')]
                encoded = self.embed([query_text(query, task)] + [bounded_text(row[text_key]) for _, row in missing])
                query_vector = encoded[0]
                document_vectors = {index: vector for (index, _), vector in zip(missing, encoded[1:])}
                for index, row in candidates:
                    vector = cached_vector(row.get('_vector')) or document_vectors.get(index)
                    if vector is None and row.get('_lexical_only'):
                        continue
                    if not isinstance(vector, list) or len(vector) != len(query_vector):
                        raise ValueError('Incompatible cached embedding')
                    semantic_scores[index] = sum(a * b for a, b in zip(query_vector, vector))
                self.available = True
            except (OSError, ValueError, TimeoutError, KeyError, TypeError, IndexError):
                semantic_scores = {}
                self.available = False
        # Reciprocal-rank fusion retains exact matches alongside semantic neighbors.
        semantic_order = sorted(semantic_scores, key=lambda index: (-semantic_scores[index], index))
        if task in {'shopping', 'storage'}:
            semantic_order = semantic_order[:10]
        semantic_rank = {index: rank for rank, index in enumerate(semantic_order, 1)}
        lexical_order = sorted((index for index, score in lexical_scores.items() if score > 0),
                               key=lambda index: (-lexical_scores[index], index))
        lexical_rank = {index: rank for rank, index in enumerate(lexical_order, 1)}
        output = []
        # Beyond the semantic budget, retain every existing lexical match.
        for index, row in ordered:
            if index not in lexical_scores:
                lexical_scores[index] = lexical(query, row[text_key])
                if lexical_scores[index] > 0:
                    lexical_rank[index] = len(lexical_rank) + 1
            score = ((1 / (60 + lexical_rank[index]) if index in lexical_rank else 0)
                     + (1 / (60 + semantic_rank[index]) if index in semantic_rank else 0))
            if score <= 0:
                continue
            output.append({**{key: value for key, value in row.items() if key not in {'_vector', '_lexical_only'}},
                           'retrieval_score': score, 'lexical_score': lexical_scores[index],
                           'semantic_score': semantic_scores.get(index)})
        return sorted(output, key=lambda row: -row['retrieval_score'])[:limit]


def default_ranker() -> SemanticRanker:
    return SemanticRanker()


def rank_candidates(query: str, rows: list[dict[str, Any]], *, task: str,
                    text_key: str = 'text', limit: int = 20) -> list[dict[str, Any]]:
    return default_ranker().rank(query, rows, task=task, text_key=text_key, limit=limit)


def chunks(text: str) -> list[str]:
    """UTF-8 byte cap is conservative even for Danish and byte-fallback tokens."""
    result = []
    while text:
        chunk = bounded_text(text, 1400)
        result.append(chunk)
        if len(chunk) == len(text):
            break
        text = text[max(1, len(chunk) - 80):]
    return result


class RetrievalIndex:
    """One private SQLite file per trusted principal/domain, with expiring sources."""
    def __init__(self, path: Path, *, namespace: str, ranker: Any = None, background: bool = False):
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', namespace):
            raise ValueError('Trusted retrieval namespace required')
        self.path, self.namespace = Path(path), namespace
        self.ranker = ranker or default_ranker()
        self.lock = threading.RLock()
        self.background, self._backfill_running = background, False
        if any(parent.is_symlink() for parent in (self.path, *self.path.parents)):
            raise ValueError('Retrieval symlinks refused')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Private retrieval directory required')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('Private retrieval file required')
        finally:
            os.close(fd)
        with closing(self._db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS scope (namespace TEXT PRIMARY KEY)')
            previous = db.execute('SELECT namespace FROM scope').fetchall()
            if previous and previous != [(namespace,)]:
                raise ValueError('Retrieval scope mismatch')
            db.execute('INSERT OR IGNORE INTO scope VALUES (?)', (namespace,))
            db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS tombstones (id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS passages (source TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE, '
                       'id TEXT PRIMARY KEY, text TEXT NOT NULL, metadata TEXT NOT NULL, hash TEXT NOT NULL, vector TEXT, revision TEXT)')

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=3)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        return db

    def replace(self, source_id: str, records: list[dict[str, Any]], *, ttl_seconds: int = 2592000) -> None:
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 512 or not 60 <= ttl_seconds <= 31536000:
            raise ValueError('Bounded source and retention required')
        if not isinstance(records, list) or len(records) > 200:
            raise ValueError('Bounded source records required')
        prepared = []
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
                    or not 1 <= len(record['id']) <= 512 or not isinstance(record.get('text'), str)
                    or len(record['text'].encode()) > 64000 or not isinstance(record.get('metadata', {}), dict)):
                raise ValueError('Invalid source passage')
            metadata = json.dumps(record.get('metadata', {}), allow_nan=False)
            if len(metadata.encode()) > 8192:
                raise ValueError('Source metadata budget exceeded')
            for number, text in enumerate(chunks(record['text'])):
                identity = hashlib.sha256(json.dumps([source_id, record['id'], number]).encode()).hexdigest()
                prepared.append((source_id, identity, text, metadata, hashlib.sha256(text.encode()).hexdigest()))
        if len(prepared) > 500:
            raise ValueError('Source passage budget exceeded')
        with self.lock, closing(self._db()) as db, db:
            if db.execute('SELECT 1 FROM tombstones WHERE id=?', (source_id,)).fetchone():
                return  # Replay cannot resurrect a deleted source.
            now = time.time()
            db.execute('DELETE FROM sources WHERE expires<=?', (now,))
            db.execute('INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET expires=excluded.expires',
                       (source_id, now + ttl_seconds))
            retained = {row[1] for row in prepared}
            for (identity,) in db.execute('SELECT id FROM passages WHERE source=?', (source_id,)).fetchall():
                if identity not in retained:
                    db.execute('DELETE FROM passages WHERE id=?', (identity,))
            for row in prepared:
                db.execute('INSERT INTO passages(source,id,text,metadata,hash) VALUES (?,?,?,?,?) '
                           'ON CONFLICT(id) DO UPDATE SET text=excluded.text,metadata=excluded.metadata,hash=excluded.hash,'
                           'vector=CASE WHEN passages.hash=excluded.hash THEN passages.vector ELSE NULL END,'
                           'revision=CASE WHEN passages.hash=excluded.hash THEN passages.revision ELSE NULL END', row)
            # Bounded per-domain working archive; canonical sources remain elsewhere.
            db.execute('DELETE FROM sources WHERE id IN (SELECT id FROM sources ORDER BY expires DESC LIMIT -1 OFFSET 2000)')
            db.execute('DELETE FROM passages WHERE id IN (SELECT p.id FROM passages p JOIN sources s ON s.id=p.source '
                       'ORDER BY s.expires DESC,p.id LIMIT -1 OFFSET 2000)')
        self.start_backfill()

    def start_backfill(self) -> None:
        if not self.background or not isinstance(self.ranker, SemanticRanker):
            return
        with self.lock:
            if self._backfill_running:
                return
            self._backfill_running = True
        def run() -> None:
            try:
                while self.warm(1):
                    pass
            except (OSError, ValueError, TimeoutError, sqlite3.Error):
                pass  # A later source read or search resumes backfill.
            finally:
                with self.lock:
                    self._backfill_running = False
        threading.Thread(target=run, name='local-embedding-backfill', daemon=True).start()

    def search(self, query: str, *, task: str, limit: int = 5,
               source_id: str | None = None) -> list[dict[str, Any]]:
        if task not in TASKS or not isinstance(query, str) or not query.strip() or not 1 <= limit <= 100:
            raise ValueError('Bounded retrieval query required')
        self.start_backfill()
        with self.lock, closing(self._db()) as db, db:
            db.execute('DELETE FROM sources WHERE expires<=?', (time.time(),))
            rows = db.execute('SELECT p.id,p.text,p.metadata,p.hash,p.vector,p.revision,p.source FROM passages p '
                              'JOIN sources s ON s.id=p.source ' + ('WHERE p.source=? ' if source_id is not None else '') +
                              'ORDER BY s.expires DESC,p.id LIMIT 2000',
                              (source_id,) if source_id is not None else ()).fetchall()
            records = [{'id': row[0], 'text': row[1], 'metadata': json.loads(row[2]),
                        'source_id': row[6], **({'_vector': cached_vector(row[4])}
                            if row[5] == REVISION and cached_vector(row[4]) is not None else {})}
                       for row in rows]
            # Warm missing vectors separately so repeated archive searches embed only the query.
            selected = sorted(records, key=lambda row: -lexical(query, row['text']))
            missing = [row for row in selected if '_vector' not in row][:80]
            warm_failed = False
            if self.background:
                for row in selected:
                    if '_vector' not in row:
                        row['_lexical_only'] = True
            if missing and hasattr(self.ranker, 'embed') and not self.background:
                try:
                    encoded = self.ranker.embed([bounded_text(row['text']) for row in missing])
                    if len(encoded) != len(missing):
                        raise ValueError('Missing source vectors')
                    if any(cached_vector(vector) is None for vector in encoded):
                        raise ValueError('Invalid source vectors')
                    for row, vector in zip(missing, encoded):
                        row['_vector'] = vector
                        db.execute('UPDATE passages SET vector=?,revision=? WHERE id=?',
                                   (json.dumps(vector), REVISION, row['id']))
                except (OSError, ValueError, TimeoutError, KeyError, TypeError):
                    warm_failed = True
            if warm_failed and isinstance(self.ranker, SemanticRanker):
                # Do not repeat a failed network call during the same read.
                lexical_rows = [{**{key: value for key, value in row.items() if key != '_vector'},
                                 'retrieval_score': lexical(query, row['text']),
                                 'lexical_score': lexical(query, row['text']), 'semantic_score': None}
                                for row in selected if lexical(query, row['text']) > 0]
                return lexical_rows[:limit]
            return self.ranker.rank(query, selected, task=task, limit=limit)

    def delete(self, source_id: str) -> None:
        with self.lock, closing(self._db()) as db, db:
            db.execute('INSERT OR IGNORE INTO tombstones VALUES (?)', (source_id,))
            db.execute('DELETE FROM sources WHERE id=?', (source_id,))

    def warm(self, limit: int = 80) -> int:
        """Operator backfill embeds a bounded batch without any user query."""
        if type(limit) is not int or not 1 <= limit <= 80:
            raise ValueError('Bounded backfill required')
        with self.lock, closing(self._db()) as db, db:
            db.execute('DELETE FROM sources WHERE expires<=?', (time.time(),))
            rows = db.execute('SELECT id,text,hash,vector,revision FROM passages ORDER BY id').fetchall()
            pending = [row for row in rows if row[4] != REVISION or cached_vector(row[3]) is None][:limit]
            if not pending:
                return 0
        # Long ingestion runs use their own budget and never hold the database
        # lock across inference, so live reads and deletion remain available.
        embed = (EmbeddingClient(timeout=60).embed if isinstance(self.ranker, SemanticRanker)
                 and isinstance(self.ranker.client, EmbeddingClient) else self.ranker.embed)
        encoded = embed([bounded_text(row[1]) for row in pending])
        if len(encoded) != len(pending) or any(cached_vector(vector) is None for vector in encoded):
            raise ValueError('Invalid backfill vectors')
        with self.lock, closing(self._db()) as db, db:
            for row, vector in zip(pending, encoded):
                db.execute('UPDATE passages SET vector=?,revision=? WHERE id=? AND hash=?',
                           (json.dumps(vector), REVISION, row[0], row[2]))
            return len(pending)
