"""Operations retrieval consumes metadata receipts and reviewed runbooks only."""
import hashlib
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_notifications import Outbox
from semantic_operations import OperationsArchive, RUNBOOKS

SPEC = importlib.util.spec_from_file_location('operations_receipt_fixtures', Path(__file__).with_name('openclaw-communication-test.py'))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class KeywordRanker:
    def rank(self, query, records, *, task, limit, **kwargs):
        assert task == 'operations'
        return [{**r, 'retrieval_score': 1, 'lexical_score': 1, 'semantic_score': None}
                for r in records if query.casefold() in r['text'].casefold()][:limit]


class OperationsArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        clock_patch = patch('semantic_operations.time.time', return_value=FIXTURE.NOW + 10)
        self.clock = clock_patch.start(); self.addCleanup(clock_patch.stop)
        self.store = Outbox(self.root / 'outbox.sqlite3', FIXTURE.SETTINGS)
        self.archive = OperationsArchive(self.store, self.root / 'retrieval.sqlite3', self.root, ranker=KeywordRanker())

    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def observe(self, status, at):
        evidence = {'state': 'healthy' if status == 'healthy' else 'unhealthy'}
        self.store.observations(FIXTURE.report(status, at, evidence), FIXTURE.ALLOWED, at)

    def test_incident_and_recovery_are_searchable_without_conversation_or_model_text(self):
        self.observe('degraded', FIXTURE.NOW)
        # Synthetic receipt: an undelivered incident intentionally suppresses
        # recovery notifications in the outbox owner's contract.
        with self.store.db:
            self.store.db.execute("UPDATE events SET state='delivered' WHERE kind='incident'")
        self.observe('healthy', FIXTURE.NOW + 1)
        self.store.reserve_turn('owner:turn-1', 'a' * 64)
        self.store.finish_turn('owner:turn-1', 'PRIVATE_MODEL_TRANSCRIPT_N8N')
        with self.store.db:
            self.store.add('task-1', 'task', 'task', {'observation': {'system_id': 'PRIVATE_TASK_N8N'}}, FIXTURE.NOW)
            self.store.add('user-1', 'conversation', 'user_turn', {'observation': {'system_id': 'PRIVATE_USER_N8N'}}, FIXTURE.NOW)
            for event in self.store.db.execute("SELECT key,body FROM events WHERE kind IN ('incident','recovery')").fetchall():
                body = json.loads(event['body']); body['raw_diagnostics'] = 'PRIVATE_RAW_N8N'
                body['text'] = 'PRIVATE_RENDERED_MESSAGE_N8N'
                self.store.db.execute('UPDATE events SET body=? WHERE key=?', (json.dumps(body), event['key']))
        result = self.archive.search({'query': 'n8n'})
        self.assertEqual({r['metadata']['kind'] for r in result['results']}, {'incident', 'recovery'})
        self.assertTrue(result['untrusted_content'])
        self.assertNotIn('PRIVATE_', json.dumps(result))
        for row in result['results']:
            self.assertEqual(row['metadata']['source'], 'infrastructure-observations')
            self.assertTrue(row['metadata']['receipt'].startswith(('observation:', 'recovery:')))
            self.assertEqual(row['metadata']['principal_scope'], 'owner')
            self.assertIn('home-core', row['text']); self.assertIn('observed_at', row['text'])
        self.assertIn('does not establish a successful repair', result['freshness'])
        self.assertIn('system_read', result['freshness'])

    def test_runbook_results_retain_reviewed_path_content_hash_and_replace_old_text(self):
        path = self.root / RUNBOOKS[0]; path.parent.mkdir(parents=True)
        path.write_text('Reviewed instruction: restart n8n only with the approved plan.')
        unlisted = path.parent / 'unreviewed.md'; unlisted.write_text('PRIVATE_UNREVIEWED_N8N')
        original = self.archive.search({'query': 'n8n'})
        row = original['results'][0]
        self.assertEqual(row['metadata']['kind'], 'runbook')
        self.assertEqual(row['metadata']['source'], RUNBOOKS[0])
        self.assertEqual(row['metadata']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn('PRIVATE_UNREVIEWED', json.dumps(original))
        path.write_text('Reviewed instruction: inspect n8n health before selecting a plan.')
        current = self.archive.search({'query': 'n8n'})['results']
        self.assertEqual(len(current), 1); self.assertNotIn('restart', current[0]['text'])
        self.assertEqual(current[0]['metadata']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_symlink_and_oversized_runbooks_are_not_imported(self):
        outside = self.root / 'outside'; outside.write_text('PRIVATE_N8N_LINK')
        first = self.root / RUNBOOKS[0]; first.parent.mkdir(parents=True); first.symlink_to(outside)
        second = self.root / RUNBOOKS[1]; second.write_text('n8n ' * 40000)
        self.assertEqual(self.archive.search({'query': 'n8n'})['results'], [])

    def test_query_scope_and_limit_are_finite_and_cannot_accept_an_action(self):
        for arguments in [{}, {'query': ''}, {'query': ' '}, {'query': 'x' * 501}, {'query': 'n8n\nrestart'},
                          {'query': 'n8n', 'limit': True}, {'query': 'n8n', 'limit': 0}, {'query': 'n8n', 'limit': 11},
                          {'query': 'n8n', 'principal': 'hermes'}, {'query': 'n8n', 'command': 'restart'}]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.archive.search(arguments)

    def test_reopened_archive_retains_provenance_without_new_domain_requests(self):
        self.observe('degraded', FIXTURE.NOW)
        self.archive.search({'query': 'n8n'})
        reopened = OperationsArchive(self.store, self.root / 'retrieval.sqlite3', self.root, ranker=KeywordRanker())
        result = reopened.search({'query': 'n8n', 'limit': 1})
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['metadata']['kind'], 'incident')

    def test_repeated_search_does_not_extend_original_incident_retention_or_resurrect_expired_receipts(self):
        self.observe('degraded', FIXTURE.NOW)
        original_expiry = FIXTURE.NOW + 30 * 86400
        self.clock.return_value = original_expiry - 120
        self.assertEqual(len(self.archive.search({'query': 'n8n'})['results']), 1)
        with closing(self.archive.index._db()) as database:
            first_expiry = database.execute("SELECT expires FROM sources WHERE id LIKE 'event:%'").fetchone()[0]
        self.assertEqual(first_expiry, original_expiry)
        self.clock.return_value += 30
        self.assertEqual(len(self.archive.search({'query': 'n8n'})['results']), 1)
        with closing(self.archive.index._db()) as database:
            self.assertEqual(database.execute("SELECT expires FROM sources WHERE id LIKE 'event:%'").fetchone()[0], original_expiry)
        for now in [original_expiry + 1, original_expiry + 86400]:
            self.clock.return_value = now
            self.assertEqual(self.archive.search({'query': 'n8n'})['results'], [])


if __name__ == '__main__': unittest.main()
