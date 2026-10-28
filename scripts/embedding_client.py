"""Bounded Harrier requests through the fixed private HomeCompute gateway."""
from __future__ import annotations

import json
import math
from pathlib import Path
import ssl
import time
from typing import Any
import urllib.request

ENDPOINT = 'https://ai.home.arpa/v1/embeddings'
MODEL = 'embedding'
REVISION = 'harrier-f9b9dc8d367d443f2479d27aa5d8d2850c0774ee-1024'
DIMENSIONS = 1024
MAX_TEXT_BYTES = 1600
TASKS = {
    'memory': 'Given a question, retrieve relevant confirmed preferences and decisions from assistant memory',
    'school': 'Given a question about school, retrieve relevant school posts, messages and weekly letters',
    'research': 'Given a question, retrieve relevant source passages that answer the question',
    'shopping': 'Given a household shopping request, retrieve matching products including synonyms',
    'storage': 'Given a household stock request, retrieve matching inventory items including synonyms',
    'travel': 'Given holiday preferences, retrieve matching hotel and destination descriptions',
    'operations': 'Given an infrastructure problem, retrieve relevant past incidents, repairs and runbooks',
    'calendar': 'Given an event title, retrieve descriptions of the same event expressed in different words',
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def bounded_text(value: str, maximum: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str):
        raise ValueError('Text required')
    return value.encode('utf-8')[:maximum].decode('utf-8', errors='ignore')


def query_text(query: str, task: str) -> str:
    if task not in TASKS or not isinstance(query, str) or not query.strip():
        raise ValueError('Known retrieval task and nonempty query required')
    return f'Instruct: {TASKS[task]}\nQuery: ' + bounded_text(query, 1200)


def vectors(payload: Any, count: int) -> list[list[float]]:
    """Reject reordered, repeated, nonfinite, degenerate or malformed vectors."""
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('Invalid embedding response')
    rows = payload['data']
    if len(rows) != count or any(not isinstance(row, dict) or type(row.get('index')) is not int for row in rows):
        raise ValueError('Invalid embedding rows')
    if sorted(row['index'] for row in rows) != list(range(count)):
        raise ValueError('Invalid embedding indexes')
    output = []
    for row in sorted(rows, key=lambda row: row['index']):
        vector = row.get('embedding')
        if (not isinstance(vector, list) or len(vector) != DIMENSIONS
                or any(type(value) not in {float, int} or not math.isfinite(value) for value in vector)):
            raise ValueError('Invalid embedding dimensions or values')
        norm = math.sqrt(sum(value * value for value in vector))
        if not 0.99 <= norm <= 1.01:
            raise ValueError('Invalid embedding normalization')
        output.append([value / norm for value in vector])
    return output


class EmbeddingClient:
    """No redirects, proxy environment, configurable origins or external fallback."""
    def __init__(self, *, opener: Any = None, timeout: float = 6.0):
        certificate = Path(__file__).resolve().parents[1] / 'config/embedding-ca.crt'
        cafile = None
        if certificate.exists():
            if certificate.is_symlink() or certificate.stat().st_uid != 0 or certificate.stat().st_mode & 0o022:
                raise ValueError('Trusted embedding CA required')
            cafile = str(certificate)
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))
        self.timeout = timeout

    def embed(self, inputs: list[str]) -> list[list[float]]:
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 96:
            raise ValueError('Bounded embedding input required')
        if any(not isinstance(value, str) or not value or len(value.encode()) > 1900 for value in inputs):
            raise ValueError('Embedding text exceeds serving budget')
        deadline = time.monotonic() + self.timeout
        output = []
        for offset in range(0, len(inputs), 16):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Embedding request budget exhausted')
            batch = inputs[offset:offset + 16]
            request = urllib.request.Request(ENDPOINT, data=json.dumps({
                'model': MODEL, 'input': batch, 'encoding_format': 'float',
            }).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            with self.opener.open(request, timeout=remaining) as response:
                raw = response.read(1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 1024 * 1024 or time.monotonic() > deadline:
                    raise ValueError('Embedding response exceeds budget')
                output.extend(vectors(json.loads(raw), len(batch)))
        return output


def openai_embeddings(body: Any, client: EmbeddingClient | None = None) -> dict[str, Any]:
    """Native memory adapter: instructions apply only to explicit query inputs."""
    if (not isinstance(body, dict) or set(body) - {
            'model', 'input', 'input_type', 'encoding_format', 'dimensions', 'output_dimensionality'}
            or body.get('model') != MODEL or body.get('encoding_format', 'float') != 'float'
            or body.get('input_type') not in {'query', 'document'}
            or body.get('dimensions', DIMENSIONS) != DIMENSIONS
            or body.get('output_dimensionality', DIMENSIONS) != DIMENSIONS):
        raise ValueError('Embedding contract refused')
    inputs = body.get('input')
    inputs = [inputs] if isinstance(inputs, str) else inputs
    if (not isinstance(inputs, list) or not 1 <= len(inputs) <= 64
            or any(not isinstance(value, str) or not value or len(value.encode()) > 32768 for value in inputs)
            or sum(len(value.encode()) for value in inputs) > 98304):
        raise ValueError('Embedding input budget exceeded')
    # Native memory owns chunk sizes. Preserve all document text by embedding
    # bounded subchunks and pooling their normalized vectors deterministically.
    encoded, groups = [], []
    for value in inputs:
        begin = len(encoded)
        while value:
            part = bounded_text(value, 1200 if body['input_type'] == 'query' else 1600)
            encoded.append(query_text(part, 'memory') if body['input_type'] == 'query' else part)
            value = value[len(part):]
        groups.append((begin, len(encoded)))
    if len(encoded) > 96:
        raise ValueError('Embedding chunk budget exceeded')
    encoded_vectors = (client or EmbeddingClient(timeout=45)).embed(encoded)
    result = []
    for begin, end in groups:
        pooled = [sum(values) / (end - begin) for values in zip(*encoded_vectors[begin:end])]
        norm = math.sqrt(sum(value * value for value in pooled))
        if norm < 1e-12:
            raise ValueError('Degenerate pooled embedding')
        result.append([value / norm for value in pooled])
    return {'object': 'list', 'model': MODEL, 'data': [
        {'object': 'embedding', 'index': index, 'embedding': vector} for index, vector in enumerate(result)]}
