"""Synthetic profile, source, category and HTTP authorization boundaries."""
from __future__ import annotations

from contextlib import closing
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from semantic_retrieval import SemanticRanker
from workflow_retrieval import WorkflowRetrieval, PROFILES


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


server = module('retrieval_server', 'workflow-retrieval-server.py')
setup = module('retrieval_setup', 'setup-workflow-retrieval.py')


class Client:
    def embed(self, inputs):
        return [[1.0] + [0.0] * 1023 for _ in inputs]


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = WorkflowRetrieval(self.root, ranker=SemanticRanker(Client()), stock=lambda: [])
        # Explicit warming keeps tests deterministic, with no background work.
        for profile in PROFILES:
            self.service.index(profile).background = False

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, profile, operation, **fields):
        return self.service.request({'profile': profile, 'operation': operation, **fields}, profile)

    def row(self, identity='one', text='synthetic evidence'):
        return {'id': identity, 'text': text, 'metadata': {'title': 'fixture'}}

    def test_profiles_and_exact_notion_page_isolation(self):
        a, b = 'notion:' + 'a' * 32, 'notion:' + 'b' * 32
        self.request('research', 'remember', source_id=a, records=[self.row(text='first page')])
        self.request('research', 'remember', source_id=b, records=[self.row(text='second page')])
        hits = self.request('research', 'search', source_id=a, query='page')['results']
        self.assertEqual({hit['source_id'] for hit in hits}, {a})
        self.assertEqual(self.request('news', 'search', query='page')['results'], [])
        for source in (None, 'notion:' + '-' * 36, 'file:///etc/passwd'):
            with self.assertRaises(ValueError):
                self.request('research', 'search', source_id=source, query='page')
        with self.assertRaises(ValueError):
            self.service.request({'profile': 'school', 'operation': 'search', 'query': 'page'}, 'news')

    def test_school_partial_pages_replace_only_same_record_and_delete_blocks_replay(self):
        self.request('school', 'school_ingest', records=[self.row('post:1', 'old post'), self.row('post:2', 'retained post')])
        self.request('school', 'school_ingest', records=[self.row('post:1', 'edited post')])
        hits = self.request('school', 'search', query='post')['results']
        self.assertEqual({hit['text'] for hit in hits}, {'edited post', 'retained post'})
        self.service.index('school').delete('scheduled:post:1')
        self.request('school', 'school_ingest', records=[self.row('post:1', 'replayed post')])
        self.assertEqual([hit['text'] for hit in self.request('school', 'search', query='post')['results']], ['retained post'])

    def test_category_refresh_empty_and_invalid_input_cannot_poison_snapshot(self):
        self.request('shopping', 'categorize', examples=[{'id': 'milk', 'name': 'milk', 'category': 'Mejeri'}])
        with self.assertRaises(ValueError):
            self.request('shopping', 'categorize', examples=[], items=[{'id': 'bad', 'name': ''}])
        result = self.request('shopping', 'categorize', items=[{'id': 'one', 'name': 'milk'}])
        self.assertEqual(result['matches'][0]['examples'][0]['category'], 'Mejeri')
        self.request('shopping', 'categorize', examples=[])
        self.assertEqual(self.request('shopping', 'categorize', items=[{'id': 'one', 'name': 'milk'}])['matches'][0]['examples'], [])

    def test_storage_uses_authoritative_snapshot_not_client_labels(self):
        self.service.stock = lambda: [{'id': 'bread', 'name': 'bread', 'category': 'Brød'}]
        result = self.request('storage', 'categorize', examples=[{'id': 'bread', 'name': 'bread', 'category': 'Andet'}], items=[{'id': 'item', 'name': 'bread'}])
        self.assertEqual(result['matches'][0]['examples'][0]['category'], 'Brød')
        self.service.stock = lambda: []
        self.assertEqual(self.request('storage', 'categorize', items=[{'id': 'item', 'name': 'bread'}])['matches'][0]['examples'], [])

    def test_pairs_are_ephemeral_and_group_scoped(self):
        rows = [self.row(str(i), 'same event') for i in range(3)]
        for row in rows:
            row['metadata'] = {'calendar_id': 'opaque', 'day': '2026-10-26', 'role': 'family'}
        rows[2]['metadata']['day'] = '2026-10-27'
        self.assertEqual(self.request('calendar', 'pairs', records=rows)['pairs'], [{'left_id': '0', 'right_id': '1', 'score': 1.0}])
        with closing(self.service.index('calendar')._db()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM passages').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.request('calendar', 'remember', source_id='bad', records=rows)

    def test_finite_records_and_empty_outage_fallback(self):
        for invalid in ([self.row(), self.row()], [{'id': 'x', 'text': 'x', 'metadata': {'token': 'secret'}}],
                        [{'id': 'x', 'text': 'x', 'metadata': {'url': 'https://user:pass@example.com'}}],
                        [{'id': 'x', 'text': 'x', 'metadata': {'url': 'https://private.home.arpa'}}]):
            with self.assertRaises(ValueError):
                self.request('news', 'remember', source_id='hn:1', records=invalid)
        self.assertEqual(self.request('calendar', 'pairs', records=[])['pairs'], [])
        self.assertEqual(self.request('news', 'rank', records=[], query='x')['results'], [])
        public = self.row(); public['metadata']['url'] = 'http://example.com/article'
        self.assertEqual(self.request('news', 'remember', source_id='hn:1', records=[public])['remembered'], 1)

    def test_invalid_school_source_budget_does_not_partially_ingest(self):
        with self.assertRaises(ValueError):
            self.request('school', 'school_ingest', records=[self.row('good'), self.row('x' * 503)])
        self.assertEqual(self.request('school', 'search', query='synthetic')['results'], [])

    def test_http_auth_profile_binding_and_private_error(self):
        config = self.root / 'tokens.json'
        tokens = {p: p.ljust(48, 'x') for p in PROFILES}
        config.write_text(json.dumps({'schema_version': 1, 'tokens': tokens})); config.chmod(0o600)
        httpd = server.serve(config, self.service, ('127.0.0.1', 0))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        def call(body, token='missing', path='/internal/workflow-retrieval'):
            with closing(http.client.HTTPConnection(*httpd.server_address)) as conn:
                conn.request('POST', path, json.dumps(body), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                result = conn.getresponse()
                return result.status, json.loads(result.read())
        try:
            self.assertEqual(call({})[0], 401)
            self.assertEqual(call({'profile': 'storage'}, tokens['shopping'])[0], 403)
            self.assertEqual(call({'profile': 'shopping', 'operation': 'rank', 'records': [], 'query': 'x'}, tokens['shopping'])[0], 200)
            self.assertEqual(call({}, tokens['shopping'], '/other')[0], 404)
            status, body = call({'profile': 'shopping', 'operation': 'remember', 'source_id': 'x', 'records': [{'text': '/private/secret'}]}, tokens['shopping'])
            self.assertEqual(status, 400); self.assertNotIn('secret', json.dumps(body))
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_gateway_patch_preserves_other_routes_and_is_idempotent(self):
        original = '(ai_api) {\n encode gzip\n handle /other { respond 200 }\n}\n'
        result = setup.patch_caddy(original)
        self.assertIn('handle /other { respond 200 }', result)
        self.assertIn('remote_ip 172.28.201.2', result)
        self.assertEqual(setup.patch_caddy(result), result)


if __name__ == '__main__':
    unittest.main()
