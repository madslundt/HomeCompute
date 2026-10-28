"""Pinned WebUI persistent settings migrate without mixing embedding identities."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'deploy/open-webui/open-webui-entrypoint.sh').read_text().split("python - <<'PY_MIGRATE'\n", 1)[1].split('\nPY_MIGRATE', 1)[0]


class WebUIMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'webui.db'
        connection = sqlite3.connect(self.database)
        connection.execute('CREATE TABLE config(key TEXT PRIMARY KEY,value JSON NOT NULL,updated_at BIGINT)')
        connection.execute('CREATE TABLE file(id TEXT)')
        connection.execute('CREATE TABLE knowledge(id TEXT)')
        connection.execute('INSERT INTO config VALUES(?,?,?)', ('unrelated.setting', json.dumps({'keep': True}), 1))
        connection.execute('INSERT INTO config VALUES(?,?,?)', ('rag.embedding_model', json.dumps('old-model'), 1))
        connection.commit(); connection.close()
        self.environment = dict(os.environ, DATA_DIR=str(self.root), RAG_OPENAI_API_KEY='synthetic-embedding-secret',
                                RAG_EMBEDDING_QUERY_PREFIX='Instruct: Retrieve relevant document passages\nQuery: ',
                                RAG_EMBEDDING_CONTENT_PREFIX='')

    def tearDown(self):
        self.temp.cleanup()

    def run_migration(self):
        return subprocess.run(['python3', '-c', SCRIPT], env=self.environment, capture_output=True, text=True)

    def values(self):
        connection = sqlite3.connect(self.database)
        try:
            return {key: json.loads(value) if isinstance(value, str) else value for key, value in connection.execute('SELECT key,value FROM config')}
        finally:
            connection.close()

    def test_migrate_empty_installation_and_preserve_unknown_keys_idempotently(self):
        first = self.run_migration()
        self.assertEqual(first.returncode, 0, first.stderr)
        values = self.values()
        self.assertEqual(values['unrelated.setting'], {'keep': True})
        self.assertEqual(values['rag.embedding_model'], 'embedding')
        self.assertEqual(values['rag.embedding_engine'], 'openai')
        self.assertEqual(values['rag.openai.api_key'], 'synthetic-embedding-secret')
        self.assertEqual(values['rag.chunk_size'], 512)
        self.assertEqual(values['rag.embedding_batch_size'], 16)
        backups = list((self.root / 'embedding-config-backups').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_migration().returncode, 0)
        self.assertEqual(list((self.root / 'embedding-config-backups').iterdir()), backups)

    def test_fresh_install_records_identity_before_application_creates_database(self):
        self.database.unlink()
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.homecompute-embedding-identity').is_file())
        self.assertFalse(self.database.exists())

    def test_existing_content_requires_explicit_rebuild_before_identity_change(self):
        connection = sqlite3.connect(self.database)
        connection.execute('INSERT INTO file VALUES(?)', ('private-document',))
        connection.commit(); connection.close()
        before = self.database.read_bytes()
        result = self.run_migration()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rebuild all', result.stderr)
        self.assertNotIn('synthetic-embedding-secret', result.stderr)
        self.assertEqual(self.database.read_bytes(), before)

    def test_key_rotation_keeps_existing_documents_and_identity(self):
        self.assertEqual(self.run_migration().returncode, 0)
        connection = sqlite3.connect(self.database)
        connection.execute('INSERT INTO file VALUES(?)', ('private-document',))
        connection.commit(); connection.close()
        self.environment['RAG_OPENAI_API_KEY'] = 'replacement-embedding-secret'
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.values()['rag.openai.api_key'], 'replacement-embedding-secret')
        self.environment['RAG_EMBEDDING_QUERY_PREFIX'] += 'changed'
        self.assertNotEqual(self.run_migration().returncode, 0)

    def test_unattached_existing_vectors_also_prevent_mixing(self):
        vector_root = self.root / 'vector_db'; vector_root.mkdir()
        connection = sqlite3.connect(vector_root / 'chroma.sqlite3')
        connection.execute('CREATE TABLE embeddings(id TEXT)')
        connection.execute('INSERT INTO embeddings VALUES(?)', ('stale-vector',))
        connection.commit(); connection.close()
        self.assertNotEqual(self.run_migration().returncode, 0)
        self.assertEqual(self.values()['rag.embedding_model'], 'old-model')


class WebUIKeyProvisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('webui_key_setup', ROOT / 'scripts/setup-open-webui-embeddings.py')
        cls.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.setup)

    def test_environment_patch_preserves_unknown_values_and_rejects_duplicates(self):
        before = 'OPEN_WEBUI_IMAGE=existing-image\nUNRELATED=keep\n'
        after = self.setup.environment_candidate(before, self.setup.KEY_FILE)
        self.assertTrue(after.startswith(before))
        self.assertEqual(self.setup.environment_candidate(after, self.setup.KEY_FILE), after)
        with self.assertRaises(self.setup.Refused):
            self.setup.environment_candidate(after + 'OPEN_WEBUI_EMBEDDING_API_KEY_FILE=other\n', self.setup.KEY_FILE)

    def test_key_scope_must_be_exact_embedding_with_expected_owner_and_no_expiry(self):
        good = {'info': {'models': ['embedding'], 'user_id': self.setup.OWNER, 'expires': None}}
        with patch.object(self.setup, 'api', return_value=good):
            self.setup.validate_key('synthetic-secret', None)
        for info in ({'models': ['embedding', 'automation'], 'user_id': self.setup.OWNER},
                     {'models': ['embedding'], 'user_id': 'other'},
                     {'models': ['embedding'], 'user_id': self.setup.OWNER, 'expires': 'soon'}):
            with patch.object(self.setup, 'api', return_value={'info': info}), self.assertRaises(self.setup.Refused):
                self.setup.validate_key('synthetic-secret', None)

    def test_secrets_are_sent_to_internal_api_over_stdin_never_process_arguments(self):
        commands = []
        def runner(arguments, **kwargs):
            commands.append((arguments, kwargs))
            return subprocess.CompletedProcess(arguments, 0, stdout='{"info":{}}')
        self.setup.api('/key/info', {'key': 'synthetic-sensitive-key'}, query=True, runner=runner)
        self.assertNotIn('synthetic-sensitive-key', json.dumps(commands[0][0]))
        self.assertIn('synthetic-sensitive-key', commands[0][1]['input'])


if __name__ == '__main__':
    unittest.main()
