#!/usr/bin/env python3
import importlib.util
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('activity', Path(__file__).resolve().parents[1] / 'scripts/llm_workflow_activity.py')
ACTIVITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTIVITY)


class ActivityTests(unittest.TestCase):
    def test_counts_running_protected_work_only_and_reads_live_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, workflows, turns = [root / name for name in ('policy.json', 'workflows.db', 'turns.db')]
            config.write_text(json.dumps({'schema_version': 1, 'workflow_ids': ['protected']}))
            with sqlite3.connect(workflows) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE execution_entity (status TEXT, deletedAt TEXT, workflowId TEXT)')
                db.executemany('INSERT INTO execution_entity VALUES (?,?,?)', [
                    ('running', None, 'protected'), ('waiting', None, 'protected'),
                    ('running', 'deleted', 'protected'), ('running', None, 'other')])
                db.commit()
                with sqlite3.connect(turns) as turn_db:
                    turn_db.execute('CREATE TABLE turns (state TEXT)')
                    turn_db.executemany('INSERT INTO turns VALUES (?)', [('running',), ('uncertain',), ('pending',)])
                result = ACTIVITY.snapshot(config, workflows, turns)
                self.assertEqual(result['active_workflows'], 1)
                self.assertEqual(result['active_turns'], 1)
                self.assertGreater(result['observed_at'], 0)
                self.assertEqual(set(result), {'active_workflows', 'active_turns', 'observed_at'})

    def test_missing_database_is_not_created_or_reported_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.sqlite'
            with self.assertRaises(sqlite3.Error):
                ACTIVITY.read_count(missing, 'SELECT 1')
            self.assertFalse(missing.exists())

    def test_native_failure_and_stale_snapshot_never_claim_idle(self):
        native = ACTIVITY.NativeActivity.__new__(ACTIVITY.NativeActivity)
        native.lock = threading.Lock()
        for value in (None, {'active_turns': 0, 'observed_at': time.time() - 60},
                      {'active_turns': False, 'observed_at': time.time()}):
            native.value = value
            with self.assertRaises(ValueError):
                native.get()
        native.value = {'active_turns': 2, 'observed_at': time.time()}
        self.assertEqual(native.get()['active_turns'], 2)


class AdmissionTests(unittest.TestCase):
    def test_isolated_admission_never_starts_native_polling_or_reads_databases(self):
        with patch.object(ACTIVITY, 'NativeActivity', side_effect=AssertionError('native polling')), \
                patch.object(ACTIVITY, 'snapshot', side_effect=AssertionError('database access')):
            service = ACTIVITY.make_server(Path('unused'), Path('unused'), Path('unused'),
                                          address=('127.0.0.1', 0), admission_only=True)
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            try:
                for path, ready, expected in [('/llm/admission', True, 200),
                                              ('/llm/admission', False, 503),
                                              ('/llm/activity', True, 503)]:
                    with patch.object(ACTIVITY, 'text_available', return_value=ready):
                        connection = http.client.HTTPConnection('127.0.0.1', service.server_port)
                        connection.request('GET', path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                        connection.close()
            finally:
                service.shutdown()
                service.server_close()
                thread.join(timeout=2)

    def test_fixed_readiness_check_fails_closed_without_touching_activity(self):
        observed = []

        class Readiness(BaseHTTPRequestHandler):
            status, body = 200, b'{}'

            def log_message(self, *_args):
                pass

            def do_GET(self):
                observed.append((self.command, self.path))
                if self.body is None:
                    self.close_connection = True
                    return
                self.send_response(self.status)
                self.end_headers()
                self.wfile.write(self.body)

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Readiness)
        service = ACTIVITY.make_server(Path('unused'), Path('unused'), Path('unused'),
                                      address=('127.0.0.1', 0), native=object())
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (upstream, service)]
        for thread in threads:
            thread.start()
        real_connection = http.client.HTTPConnection

        def connect(host, port, timeout):
            self.assertEqual((host, port, timeout), ('172.28.200.2', 8188, 3))
            return real_connection('127.0.0.1', upstream.server_port, timeout=timeout)

        cases = [
            (200, b'{"available":true,"state":"ready"}', 200),
            (200, b'{"available":false,"state":"draining"}', 503),
            (200, b'{"available":true,"state":"draining"}', 503),
            (200, b'{"available":1,"state":"ready"}', 503),
            (200, b'not json', 503), (200, b'[]', 503),
            (200, b' ' * 4097, 503),
            (503, b'{"available":true,"state":"ready"}', 503),
            (302, b'', 503), (200, None, 503),
        ]
        try:
            with patch.object(ACTIVITY.http.client, 'HTTPConnection', side_effect=connect), \
                    patch.object(ACTIVITY, 'snapshot', side_effect=AssertionError('activity read')):
                for upstream_status, body, expected_status in cases:
                    with self.subTest(upstream_status=upstream_status, body=body):
                        Readiness.status, Readiness.body = upstream_status, body
                        client = real_connection('127.0.0.1', service.server_port, timeout=5)
                        client.request('GET', '/llm/admission')
                        response = client.getresponse()
                        self.assertEqual(response.status, expected_status)
                        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                        result = json.loads(response.read())
                        if expected_status == 503:
                            self.assertEqual(response.getheader('Retry-After'), '30')
                            self.assertEqual(result, {'error': {
                                'message': 'Text model is temporarily unavailable',
                                'type': 'service_unavailable'}})
                        else:
                            self.assertEqual(result, {'available': True})
                        client.close()
            self.assertEqual(observed, [('GET', '/control/api/llm-status')] * len(cases))
        finally:
            for server in (service, upstream):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_network_timeout_denies_admission(self):
        with patch.object(ACTIVITY.http.client, 'HTTPConnection') as constructor:
            connection = constructor.return_value
            connection.request.side_effect = TimeoutError('unreachable metadata')
            self.assertFalse(ACTIVITY.text_available())
            connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
