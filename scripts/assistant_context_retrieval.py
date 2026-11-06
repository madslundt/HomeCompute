"""Scope-filtered hybrid retrieval over current assistant-context projections."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import time
from typing import Any
import urllib.request

from assistant_context_store import ContextStore, Scope
from assistant_context_tools import private_bytes
from embedding_client import REVISION, MODEL, ENDPOINT, vectors, query_text, bounded_text
from semantic_retrieval import lexical, cached_vector


class AuthenticatedHarrierClient:
    """Fixed HomeCompute gateway transport with an independent bearer secret."""
    def __init__(self, credential_file: str | os.PathLike[str], *, opener: Any = None, timeout: float = 6.0):
        path = Path(credential_file)
        if not path.is_absolute():
            raise ValueError("Private Harrier credential required")
        token = private_bytes(path, 512).decode("ascii").strip()
        # LiteLLM's generated sk- keys can be 25 characters; authorization is
        # checked by the fixed gateway, rather than an assumed token length.
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token):
            raise ValueError("Harrier credential refused")
        certificate = Path(__file__).resolve().parents[1] / "config/embedding-ca.crt"
        cafile = None
        if certificate.exists():
            if certificate.is_symlink() or certificate.stat().st_uid != 0 or certificate.stat().st_mode & 0o022:
                raise ValueError("Trusted embedding CA required")
            cafile = str(certificate)
        self.token, self.timeout = token, timeout
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))

    def embed(self, inputs: list[str]) -> list[list[float]]:
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 81 or any(
                not isinstance(value, str) or not value or len(value.encode()) > 1900 for value in inputs):
            raise ValueError("Bounded Harrier embedding request required")
        output = []
        deadline = time.monotonic() + self.timeout
        for offset in range(0, len(inputs), 16):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Harrier request exhausted its total time budget')
            batch = inputs[offset:offset + 16]
            request = urllib.request.Request(ENDPOINT, data=json.dumps({"model": MODEL, "input": batch,
                "encoding_format": "float"}).encode(), method="POST", headers={
                    "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                    "Accept": "application/json"})
            with self.opener.open(request, timeout=remaining) as response:
                raw = response.read(1_048_577)
                if response.status != 200 or len(raw) > 1_048_576:
                    raise ValueError("Harrier embedding response exceeded bounds")
            output.extend(vectors(json.loads(raw), len(batch)))
        return output


def _scope_tuple(scope: Scope) -> tuple[str, str, str]:
    if not isinstance(scope, Scope):
        raise ValueError("Trusted retrieval scope required")
    return scope.principal_scope, scope.data_domain, scope.visibility


def _projection_text(row: dict[str, Any]) -> str:
    # Stable source IDs and dates are part of the lexical candidate text.
    return json.dumps({"source": row["source"], "object_id": row["object_id"],
                       "event_type": row["event_type"], "occurred_at": row["occurred_at"],
                       "source_ref": row["source_ref"], "payload": row["payload"]},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssistantContextRetrieval:
    """Hybrid RRF over authorized current records with a private scoped vector cache.

    `embedding_client` is optional and must be explicitly injected by the owner
    of the retrieval capability. The default is lexical-only and has no network
    behavior. Cache keys include scope, source revision and embedding revision.
    """
    def __init__(self, store: ContextStore, cache_path: str | os.PathLike[str], *,
                 embedding_client: Any = None, embedding_revision: str = REVISION,
                 candidate_limit: int = 80):
        if (not isinstance(embedding_revision, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,160}", embedding_revision)
                or type(candidate_limit) is not int or not 1 <= candidate_limit <= 80):
            raise ValueError("Bounded retrieval configuration required")
        self.store, self.embedding_client = store, embedding_client
        self.embedding_revision, self.candidate_limit = embedding_revision, candidate_limit
        supplied = Path(cache_path)
        if supplied.is_symlink():
            raise ValueError("Retrieval cache symlinks refused")
        path = supplied.parent.resolve() / supplied.name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Private retrieval cache directory required")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Private retrieval cache file required")
        finally:
            os.close(fd)
        self.cache_path = path
        with closing(self._db()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors (principal TEXT NOT NULL, domain TEXT NOT NULL, visibility TEXT NOT NULL, "
                       "source TEXT NOT NULL, object_id TEXT NOT NULL, source_revision INTEGER NOT NULL, source_hash TEXT NOT NULL, "
                       "embedding_revision TEXT NOT NULL, vector TEXT NOT NULL, created_at REAL NOT NULL, "
                       "PRIMARY KEY(principal,domain,visibility,source,object_id,source_revision,source_hash,embedding_revision))")

    def _db(self):
        db = sqlite3.connect(self.cache_path, timeout=3)
        db.execute("PRAGMA secure_delete=ON")
        return db

    def invalidate(self, scope: Scope, source: str, object_id: str) -> int:
        key = _scope_tuple(scope)
        with closing(self._db()) as db, db:
            cursor = db.execute("DELETE FROM vectors WHERE principal=? AND domain=? AND visibility=? AND source=? AND object_id=?",
                                (*key, source, object_id))
            return cursor.rowcount

    def purge_source(self, scope: Scope, source: str) -> int:
        key = _scope_tuple(scope)
        with closing(self._db()) as db, db:
            cursor = db.execute("DELETE FROM vectors WHERE principal=? AND domain=? AND visibility=? AND source=?",
                                (*key, source))
            return cursor.rowcount

    def purge_scope(self, scope: Scope) -> int:
        key = _scope_tuple(scope)
        with closing(self._db()) as db, db:
            cursor = db.execute("DELETE FROM vectors WHERE principal=? AND domain=? AND visibility=?", key)
            return cursor.rowcount

    def invalidate_after_restore(self, scope: Scope) -> int:
        """Drop every derived vector for the scope before retrieval resumes.

        Vectors are rebuilt lazily from the post-replay current source projection;
        archived cache entries are never trusted across a restore.
        """
        return self.purge_scope(scope)

    def invalidate_all_after_restore(self) -> int:
        """Drop every cached vector when the restored source scopes are unknown."""
        with closing(self._db()) as db, db:
            cursor = db.execute("DELETE FROM vectors")
            return cursor.rowcount

    def _read_vector(self, db, scope_key, row):
        digest = hashlib.sha256(row["text"].encode()).hexdigest()
        value = db.execute("SELECT vector FROM vectors WHERE principal=? AND domain=? AND visibility=? AND source=? "
                           "AND object_id=? AND source_revision=? AND source_hash=? AND embedding_revision=?",
                           (*scope_key, row["source"], row["object_id"], row["revision"], digest,
                            self.embedding_revision)).fetchone()
        return cached_vector(value[0]) if value else None

    def search(self, scope: Scope, query: str, *, source_grants: Any, limit: int = 10) -> dict[str, Any]:
        key = _scope_tuple(scope)
        if (not isinstance(query, str) or not query.strip() or len(query.encode()) > 1200
                or type(limit) is not int or not 1 <= limit <= 50):
            raise ValueError("Bounded assistant context query required")
        # Validate grants and select scope-authorized rows before any embedding call.
        granted = self.store._sources(source_grants)
        projections = self.store.retrieval_snapshot(scope, sources=granted, limit=2000)
        rows = []
        for projection in projections:
            text = _projection_text(projection)
            rows.append({**projection, "text": text, "_source_hash": hashlib.sha256(text.encode()).hexdigest()})
        lexical_order = sorted(range(len(rows)), key=lambda i: (-lexical(query, rows[i]["text"]), i))
        lexical_rank = {i: rank for rank, i in enumerate((i for i in lexical_order if lexical(query, rows[i]["text"])), 1)}
        candidates = lexical_order[:self.candidate_limit]
        semantic_scores: dict[int, float] = {}
        degraded = self.embedding_client is None
        with closing(self._db()) as db, db:
            vectors_by_index = {i: self._read_vector(db, key, rows[i]) for i in candidates}
            missing = [i for i in candidates if vectors_by_index[i] is None]
            if self.embedding_client is not None and candidates:
                try:
                    # One query plus a fixed maximum candidate count bounds tokens and latency.
                    encoded = self.embedding_client.embed([query_text(query, "memory")] +
                                                           [bounded_text(rows[i]["text"]) for i in missing])
                    if len(encoded) != 1 + len(missing):
                        raise ValueError("Embedding result count mismatch")
                    query_vector = cached_vector(encoded[0])
                    if query_vector is None:
                        raise ValueError("Malformed query embedding")
                    for i, vector in zip(missing, encoded[1:]):
                        normalized = cached_vector(vector)
                        if normalized is None:
                            raise ValueError("Malformed document embedding")
                        vectors_by_index[i] = normalized
                        db.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (*key, rows[i]["source"], rows[i]["object_id"], rows[i]["revision"],
                                    rows[i]["_source_hash"], self.embedding_revision,
                                    json.dumps(normalized, separators=(",", ":")), time.time()))
                    db.execute("DELETE FROM vectors WHERE principal=? AND domain=? AND visibility=? "
                               "AND embedding_revision!=?", (*key, self.embedding_revision))
                    db.execute("DELETE FROM vectors WHERE rowid IN (SELECT rowid FROM vectors WHERE principal=? "
                               "AND domain=? AND visibility=? ORDER BY created_at DESC LIMIT -1 OFFSET 2000)", key)
                    for i in candidates:
                        vector = vectors_by_index[i]
                        if vector is not None:
                            semantic_scores[i] = sum(a * b for a, b in zip(query_vector, vector))
                    degraded = False
                except (OSError, ValueError, TimeoutError, KeyError, TypeError, IndexError):
                    semantic_scores = {}
                    degraded = True
        semantic_order = sorted(semantic_scores, key=lambda i: (-semantic_scores[i], i))
        semantic_rank = {i: rank for rank, i in enumerate(semantic_order, 1)}
        ranked = []
        for i, row in enumerate(rows):
            if i not in lexical_rank and i not in semantic_rank:
                continue
            score = (1 / (60 + lexical_rank[i]) if i in lexical_rank else 0) + \
                    (1 / (60 + semantic_rank[i]) if i in semantic_rank else 0)
            ranked.append({**{k: v for k, v in row.items() if k not in {"text", "_source_hash"}},
                           "retrieval_score": score, "lexical_score": lexical(query, row["text"]),
                           "semantic_score": semantic_scores.get(i)})
        ranked.sort(key=lambda row: -row["retrieval_score"])
        return {"results": ranked[:limit], "degraded": degraded,
                "degradation": "lexical_only_embedding_unavailable" if degraded else None,
                "embedding_revision": self.embedding_revision if not degraded else None}
