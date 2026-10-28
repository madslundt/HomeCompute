#!/usr/bin/env python3
"""Synthetic owner-only, one-shot Fill feedback; no browser or real credentials."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_owner_bridge as bridge_module
from openclaw_site_owner_bridge import BrowserOwnerBridge, TOKEN_FILE

SPEC = importlib.util.spec_from_file_location('fill_access_service', ROOT / 'scripts/openclaw-access.py')
service = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(service)
TOKEN = 'synthetic-owner-only-backend-key-' + 'p' * 32


class FillFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tick = 100.0
        self.grant = {'request_id': str(uuid4()), 'actor': 'openclaw', 'account': 'hotels.com', 'scope': 'read',
                      'status': 'active', 'approved_at': 90, 'expires_at': 600, 'request_digest': 'a' * 64,
                      'requested_minutes': 10, 'requested_at': 80,
                      'account_execution_available': True, 'transaction_execution_available': False}
        self.grants = {self.grant['request_id']: self.grant}
        config = {'schema_version': 1, 'endpoint': 'http://127.0.0.1:18816', 'token_file': TOKEN_FILE}
        def private(directory, name, size):
            return TOKEN.encode() if name == 'browser-owner-token' else json.dumps(config).encode()
        with patch.object(bridge_module, 'read_private', private):
            self.bridge = BrowserOwnerBridge(Path('/synthetic/config.json'), lambda ref: copy.deepcopy(self.grants[ref]),
                                            clock=lambda: self.tick, wall=lambda: self.tick)
        self.calls, self.fill_value = [], None
        self.after_fill = None
        def upstream(reference, action, **options):
            self.calls.append((reference, action, options))
            if options.get('method') == 'POST':
                if action != 'fill': return b'ignored synthetic upstream text'
                value = self.receipt() if self.fill_value is None else self.fill_value
                if self.after_fill: self.after_fill()
                return value if isinstance(value, bytes) else json.dumps(value).encode()
            if action.startswith('vendor/'): return b'export default class Synthetic {}'
            item = self.grants[reference]
            return json.dumps({'status': 'awaiting_owner', 'request_id': reference, 'account': item['account']}).encode()
        self.bridge.request = upstream
        self.server = service.make_server(None, 'operator', 0, {'openclaw': 's' * 40}, None,
            operator_auth=lambda headers: headers.get('Cookie') == 'MMAUTHTOKEN=synthetic-owner', browser=self.bridge)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.path = '/browser/' + self.grant['request_id'] + '/'

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def receipt(self, **changes):
        return dict(status='awaiting_owner', request_id=self.grant['request_id'],
                    account=self.grant['account'], filled_fields=2, **changes)

    def http(self, method='GET', path=None, fields=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        values = {'Cookie': 'MMAUTHTOKEN=synthetic-owner'}
        if method == 'POST':
            values.update({'Content-Type': 'application/x-www-form-urlencoded', 'Origin': self.server.operator_origin,
                           'Sec-Fetch-Site': 'same-origin'})
        values.update(headers)
        try:
            connection.request(method, path or self.path, urlencode(fields) if fields is not None else None, values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def fields(self, action='fill'):
        return {'digest': self.grant['request_digest'], 'csrf': self.bridge.challenge(self.grant, action)}

    def test_each_count_sets_finite_notice_once_after_private_prg_without_url_or_upstream_data(self):
        messages = []
        for count, code in ((0, 'no_fields'), (1, 'filled_one'), (2, 'filled_two')):
            self.fill_value = dict(self.receipt(), filled_fields=count)
            status, headers, raw = self.http('POST', self.path + 'fill', self.fields())
            self.assertEqual((status, headers['Location'], json.loads(raw)), (303, self.path, {}))
            self.assertNotIn('?', headers['Location'])
            with patch.object(bridge_module, 'private_browser', wraps=bridge_module.private_browser) as page:
                status, headers, raw = self.http()
                self.assertEqual(status, 200)
                self.assertEqual(page.call_args.kwargs, {'fill_result': code})
            self.assertIn(b'id="fill-result"', raw)
            messages.append(raw.split(b'id="fill-result"', 1)[1].split(b'</', 1)[0])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            for private in (TOKEN.encode(), b'filled_fields', b'synthetic-owner', code.encode()):
                self.assertNotIn(private, raw)
            self.assertNotIn(b'id="fill-result"', self.http()[2])
        self.assertEqual(len(set(messages)), 3)

    def test_polling_and_scripts_do_not_consume_notice(self):
        self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 303)
        for suffix in ('state', 'client.js', 'vendor/core/rfb.js'):
            status, _, raw = self.http(path=self.path + suffix)
            self.assertEqual(status, 200); self.assertNotIn(b'filled_fields', raw)
        self.assertIn(b'id="fill-result"', self.http()[2])

    def test_invalid_count_state_reference_account_extra_duplicate_or_private_fields_never_flash(self):
        base = self.receipt()
        values = [dict(base, filled_fields=value) for value in (True, False, -1, 3, 1.0, '2', None)]
        values += [dict(base, status='ready'), dict(base, request_id=str(uuid4())), dict(base, account='other.example'),
                   dict(base, password='synthetic-private-value'), dict(base, detail='synthetic-private-value'),
                   {key: value for key, value in base.items() if key != 'filled_fields'}, b'not-json',
                   json.dumps(base).replace('"filled_fields": 2', '"filled_fields": 0, "filled_fields": 2').encode()]
        for value in values:
            self.fill_value = value
            status, _, raw = self.http('POST', self.path + 'fill', self.fields())
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(raw), {'error': 'access_request_refused'})
            self.assertNotIn(b'synthetic-private-value', raw)
            self.assertFalse(self.bridge._fill_notices)
        self.assertNotIn(b'id="fill-result"', self.http()[2])

    def test_failed_new_fill_clears_unseen_feedback_from_previous_attempt(self):
        self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 303)
        self.fill_value = dict(self.receipt(), password='synthetic-private-value')
        self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 400)
        self.assertNotIn(b'id="fill-result"', self.http()[2])

    def test_owner_auth_exact_origin_fetch_metadata_digest_and_single_use_csrf_still_required(self):
        for headers in ({'Cookie': ''}, {'Cookie': 'MMAUTHTOKEN=other'}, {'Origin': 'null'},
                        {'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'same-site'}):
            status, _, _ = self.http('POST', self.path + 'fill', self.fields(), **headers)
            self.assertIn(status, {401, 403})
        for fields in ({}, dict(self.fields(), digest='b' * 64), dict(self.fields(), password='synthetic-private')):
            self.assertEqual(self.http('POST', self.path + 'fill', fields)[0], 400)
        self.assertFalse(self.calls); self.assertFalse(self.bridge._fill_notices)
        fields = self.fields()
        self.assertEqual(self.http('POST', self.path + 'fill', fields)[0], 303)
        self.assertEqual(self.http('POST', self.path + 'fill', fields)[0], 400)
        self.assertEqual(len(self.calls), 1)

    def test_notice_is_digest_account_reference_and_time_bound_and_rollback_cannot_revive_it(self):
        for mutation in ('digest', 'account', 'expired', 'rollback'):
            self.tick = 100
            self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 303)
            original = copy.deepcopy(self.grant)
            if mutation == 'digest': self.grant['request_digest'] = 'b' * 64
            if mutation == 'account': self.grant['account'] = 'other.example'
            if mutation == 'expired': self.tick = 400
            if mutation == 'rollback': self.tick = 99
            self.assertNotIn(b'id="fill-result"', self.http()[2])
            self.grant.update(original); self.tick = 100
            self.assertNotIn(b'id="fill-result"', self.http()[2])
        self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 303)
        other = dict(self.grant, request_id=str(uuid4()))
        self.grants[other['request_id']] = other
        self.assertNotIn(b'id="fill-result"', self.http(path='/browser/' + other['request_id'] + '/')[2])
        self.assertIn(b'id="fill-result"', self.http()[2])

    def test_notice_concurrent_reads_are_one_shot_and_capacity_stays_bounded(self):
        self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 303)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pages = list(pool.map(lambda _: self.http()[2], range(2)))
        self.assertEqual(sum(b'id="fill-result"' in raw for raw in pages), 1)
        for _ in range(1001):
            self.bridge._save_fill_notice(dict(self.grant, request_id=str(uuid4())), 'filled_one')
        self.assertEqual(len(self.bridge._fill_notices), 1000)
        self.tick = 400
        self.bridge._save_fill_notice(self.grant, 'no_fields')
        self.assertEqual(len(self.bridge._fill_notices), 1)

    def test_expiry_or_revocation_during_fill_cannot_publish_feedback_and_other_actions_unchanged(self):
        for failure in ('expired', 'revoked'):
            self.tick = 100; self.grant['status'] = 'active'
            self.after_fill = (lambda: setattr(self, 'tick', 600)) if failure == 'expired' else lambda: self.grant.update(status='revoked')
            self.assertEqual(self.http('POST', self.path + 'fill', self.fields())[0], 400)
            self.assertFalse(self.bridge._fill_notices)
        self.grant['status'] = 'active'; self.tick = 100; self.after_fill = None
        for action in ('start', 'ready', 'close'):
            self.assertEqual(self.http('POST', self.path + action, self.fields(action))[0], 303)
        self.assertFalse(self.bridge._fill_notices)


if __name__ == '__main__':
    unittest.main()
