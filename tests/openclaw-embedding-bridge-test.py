"""Exercise real authenticated loopback routes with synthetic vectors only."""
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_notifications import Outbox
SPEC = importlib.util.spec_from_file_location('embedding_bridge_fixtures', Path(__file__).with_name('openclaw-communication-test.py'))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
communication = FIXTURE.communication

NATIVE = {actor: 'synthetic-native-' + actor + '-' * 40 for actor in ('openclaw', 'hermes')}
VALID = {'model': 'embedding', 'input': ['confirmed household preference'], 'input_type': 'document',
         'encoding_format': 'float', 'dimensions': 1024}


class FakeEmbedding:
    def __init__(self): self.inputs = []; self.failure = None

    def embed(self, inputs):
        self.inputs.append(inputs)
        if self.failure: raise self.failure
        return [[1.0] + [0.0] * 1023 for _ in inputs]


class EmbeddingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.store = Outbox(self.root / 'outbox.sqlite3', FIXTURE.SETTINGS)
        self.tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'broker-assistant', 'snapshot': 'broker-snapshot'})
        self.embedding = FakeEmbedding(); self.turn = Mock(side_effect=AssertionError('model must not be called'))
        self.operations = SimpleNamespace(search=Mock(return_value={'results': [], 'freshness': 'Historical evidence only'}))
        self.server = communication.serve(self.store, FIXTURE.TOKENS, FIXTURE.ALLOWED, self.turn,
            ('127.0.0.1', 0), lambda: FIXTURE.NOW, tasks=self.tasks, mcp_tokens=NATIVE,
            operations_search=self.operations, embedding_client=self.embedding)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store.db.close(); self.temp.cleanup()

    def post(self, body, *, path='/v1/embeddings', token=NATIVE['openclaw'], headers=None):
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        request_headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                           'Authorization': 'Bearer ' + token, **(headers or {})}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('POST', path, body=content, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()

    def rpc(self, method, params=None, actor='openclaw'):
        return self.post({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
                         path='/mcp', token=NATIVE[actor])

    def test_openclaw_native_token_embeds_document_and_instructs_only_queries(self):
        status, result = self.post(VALID)
        self.assertEqual(status, 200); self.assertEqual(result['model'], 'embedding')
        self.assertEqual(result['data'][0]['index'], 0); self.assertEqual(len(result['data'][0]['embedding']), 1024)
        self.assertEqual(self.embedding.inputs[0], VALID['input'])
        status, result = self.post({**VALID, 'input': 'What was the confirmed preference?', 'input_type': 'query'})
        self.assertEqual(status, 200)
        self.assertTrue(self.embedding.inputs[1][0].startswith('Instruct:'))
        self.assertIn('\nQuery: What was the confirmed preference?', self.embedding.inputs[1][0])
        self.turn.assert_not_called()

    def test_hermes_transport_and_unknown_tokens_never_reach_embedding_client(self):
        for token in [NATIVE['hermes'], *FIXTURE.TOKENS.values(), 'unknown-credential']:
            with self.subTest(token_kind=token.split('-')[0]):
                self.assertEqual(self.post(VALID, token=token), (401, {'error': 'unauthorized'}))
        self.assertEqual(self.embedding.inputs, []); self.turn.assert_not_called()

    def test_only_exact_embedding_route_is_exposed(self):
        for path in ['/v1/embeddings/', '/v1/embeddings?model=embedding', '/embeddings', '/v1/chat/completions']:
            self.assertEqual(self.post(VALID, path=path)[0], 404)
        self.assertEqual(self.embedding.inputs, [])

    def test_long_native_chunks_are_pooled_without_losing_document_text(self):
        document = 'Confirmed æøå household preference. ' * 150
        status, result = self.post({**VALID, 'input': [document, 'Another document']})
        self.assertEqual(status, 200); self.assertEqual(len(result['data']), 2)
        chunks = self.embedding.inputs[0]
        self.assertGreater(len(chunks), 2)
        self.assertEqual(''.join(chunks[:-1]), document)
        self.assertEqual(chunks[-1], 'Another document')
        self.assertTrue(all(len(chunk.encode()) <= 1600 for chunk in chunks))

    def test_invalid_model_input_type_shape_and_bounds_are_rejected_before_embedding(self):
        invalid = [[], None, {}, {**VALID, 'model': 'home'}, {**VALID, 'input_type': 'search'},
                   {k: v for k, v in VALID.items() if k != 'input_type'}, {**VALID, 'input': []},
                   {**VALID, 'input': ['']}, {**VALID, 'input': [123]}, {**VALID, 'input': ['x'] * 65},
                   {**VALID, 'input': 'x' * 32769}, {**VALID, 'input': ['x' * 26000] * 4}, {**VALID, 'dimensions': 768},
                   {**VALID, 'encoding_format': 'base64'}, {**VALID, 'endpoint': 'https://evil.invalid'}]
        for body in invalid:
            with self.subTest(body_kind=type(body).__name__): self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.post(b'{invalid-json')[0], 400)
        self.assertEqual(self.post(b'')[0], 400)
        self.assertEqual(self.post(b'x' * 131073)[0], 400)
        self.assertEqual(self.post(VALID, headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.embedding.inputs, [])

    def test_embedding_outage_returns_bounded_error_without_internal_diagnostics(self):
        self.embedding.failure = OSError('PRIVATE_GATEWAY_DIAGNOSTIC')
        status, result = self.post(VALID)
        self.assertEqual(status, 503); self.assertNotIn('PRIVATE_', json.dumps(result))
        self.assertEqual(result, {'error': 'local embeddings unavailable'})
        self.turn.assert_not_called()

    def test_operations_tool_is_openclaw_only_and_uses_existing_mcp_protocol(self):
        status, listed = self.rpc('tools/list')
        self.assertEqual(status, 200)
        tool = next(t for t in listed['result']['tools'] if t['name'] == 'homecompute_operations_search')
        self.assertTrue(tool['annotations']['readOnlyHint'])
        self.assertNotIn('homecompute_operations_search', {t['name'] for t in self.rpc('tools/list', actor='hermes')[1]['result']['tools']})
        self.assertFalse(self.rpc('tools/call', {'name': tool['name'], 'arguments': {'query': 'n8n unhealthy'}})[1]['result']['isError'])
        self.operations.search.reset_mock()
        status, result = self.rpc('tools/call', {'name': tool['name'], 'arguments': {'query': 'n8n unhealthy', 'limit': 3}})
        self.assertEqual(status, 200); self.assertIn('result', result, result)
        self.assertFalse(result['result']['isError'])
        self.operations.search.assert_called_once_with({'query': 'n8n unhealthy', 'limit': 3})
        for arguments in [{'query': 'n8n', 'actor': 'openclaw'}, {'query': 'n8n', 'limit': True}, {'query': 'n8n', 'limit': 11}]:
            self.assertEqual(self.rpc('tools/call', {'name': tool['name'], 'arguments': arguments})[1]['error']['code'], -32602)
        denied = self.rpc('tools/call', {'name': tool['name'], 'arguments': {'query': 'n8n'}}, actor='hermes')[1]
        self.assertEqual(denied['error']['code'], -32602)
        self.assertEqual(self.operations.search.call_count, 1); self.assertEqual(self.embedding.inputs, [])
        self.turn.assert_not_called()


if __name__ == '__main__': unittest.main()
