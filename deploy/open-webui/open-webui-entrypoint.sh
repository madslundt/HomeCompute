#!/bin/bash
set -euo pipefail

read_required_secret() {
  local secret_path="$1"
  if [[ ! -r "$secret_path" ]]; then
    echo "required secret is not readable: $secret_path" >&2
    exit 1
  fi
  local value
  value="$(<"$secret_path")"
  if [[ -z "$value" ]]; then
    echo "required secret is empty: $secret_path" >&2
    exit 1
  fi
  printf '%s' "$value"
}

export OPENAI_API_KEYS="$(read_required_secret /run/secrets/litellm_api_key)"
export RAG_OPENAI_API_KEY="$(read_required_secret /run/secrets/embedding_api_key)"
export WEBUI_SECRET_KEY="$(read_required_secret /run/secrets/webui_secret_key)"

if (( ${#WEBUI_SECRET_KEY} < 32 )); then
  echo "WEBUI_SECRET_KEY must contain at least 32 characters" >&2
  exit 1
fi

# v0.11.4 persists per-key settings; environment defaults alone do not migrate
# an existing installation. Run before the server starts; preserve other keys.
python - <<'PY_MIGRATE'
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

root = Path(os.environ.get('DATA_DIR', '/app/backend/data'))
database = root / 'webui.db'
if database.is_symlink() or root.is_symlink():
    raise SystemExit('Open WebUI embedding migration refused a symlink')
desired = {
    'rag.embedding_engine': 'openai', 'rag.embedding_model': 'embedding',
    'rag.enable_hybrid_search': True,
    'rag.openai.api_base_url': 'http://litellm:4000/v1',
    'rag.openai.api_key': os.environ['RAG_OPENAI_API_KEY'],
    'rag.embedding_batch_size': 16, 'rag.embedding_concurrent_requests': 1,
    'rag.text_splitter': '', 'rag.chunk_size': 512, 'rag.chunk_overlap': 64,
}
identity_keys = set(desired) - {'rag.openai.api_key', 'rag.embedding_batch_size',
                                'rag.embedding_concurrent_requests', 'rag.enable_hybrid_search'}
identity = {key: desired[key] for key in sorted(identity_keys)}
identity['query_prefix'] = os.environ.get('RAG_EMBEDDING_QUERY_PREFIX')
identity['content_prefix'] = os.environ.get('RAG_EMBEDDING_CONTENT_PREFIX', '')
fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
marker = root / '.homecompute-embedding-identity'
if marker.is_symlink():
    raise SystemExit('Open WebUI embedding migration refused a symlink')
if database.exists():
    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute('PRAGMA table_info(config)')}
    if not {'key', 'value', 'updated_at'} <= columns:
        raise SystemExit('Open WebUI embedding migration requires the pinned v0.11.4 schema')
    current = {key: json.loads(value) if isinstance(value, str) else value for key, value in connection.execute('SELECT key,value FROM config')}
    changed = any(current.get(key) != value for key, value in desired.items())
    identity_changed = any(current.get(key) != desired[key] for key in identity_keys)
    identity_changed |= not marker.exists() or marker.read_text().strip() != fingerprint
    if identity_changed:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        corpus = any(connection.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
                     for table in ('file', 'knowledge', 'memory', 'note') if table in tables)
        chroma = root / 'vector_db' / 'chroma.sqlite3'
        if chroma.is_symlink():
            raise SystemExit('Open WebUI embedding migration refused a symlink')
        if chroma.exists():
            vectors = sqlite3.connect('file:' + str(chroma) + '?mode=ro', uri=True)
            vector_tables = {row[0] for row in vectors.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            corpus |= 'embeddings' in vector_tables and bool(vectors.execute('SELECT count(*) FROM embeddings').fetchone()[0])
            vectors.close()
        elif (root / 'vector_db').exists():
            corpus |= any((root / 'vector_db').iterdir())
        if corpus:
            raise SystemExit('Open WebUI embedding identity changed with existing content; back up data and rebuild all document, note, and memory indexes before enabling Harrier')
    if changed:
        backups = root / 'embedding-config-backups'
        if backups.is_symlink():
            raise SystemExit('Open WebUI embedding migration refused a symlink')
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / (str(uuid.uuid4()) + '.sqlite3')
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        saved = sqlite3.connect(backup)
        connection.backup(saved)
        saved.close()
        with connection:
            connection.executemany('INSERT INTO config(key,value,updated_at) VALUES(?,?,?) '
                                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                                   [(key, json.dumps(value), int(time.time())) for key, value in desired.items()])
    connection.close()
else:
    identity_changed = True
if identity_changed:
    temporary = root / ('.embedding-identity-' + str(uuid.uuid4()))
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(fingerprint + '\n')
    os.replace(temporary, marker)
PY_MIGRATE

exec "$@"
