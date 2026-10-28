#!/usr/bin/env python3
"""Owner-only review HTTP/UI tests with synthetic grants and transactions."""
from __future__ import annotations

import copy
import http.client
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_access_grants import AccessGrants
from openclaw_site_transactions import SiteTransactions, TransactionRefused
from openclaw_site_transaction_page import amount, review_transaction

SPEC = importlib.util.spec_from_file_location('transaction_access_service', ROOT / 'scripts/openclaw-access.py')
SERVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.now = [1000]
        self.grants = AccessGrants(self.base / 'grants', clock=lambda: self.now[0])
        grant, _ = self.grants.request('travel', {'account': 'hotels.com', 'scope': 'prepare_booking',
                                                 'minutes': 30, 'request_key': 'transaction-page-fixture'})
        self.grants.decide(grant['request_id'], grant['request_digest'], 'approve', 30,
                           self.grants.challenge(grant['request_id'], 'approve'))
        self.transactions = SiteTransactions(self.base / 'transactions',
            lambda actor, ref: self.grants.status(ref, actor=actor), clock=lambda: self.now[0])
        self.item = self.transactions.propose('travel', grant['request_id'], {
            'account': 'hotels.com', 'kind': 'booking', 'payee': 'Synthetic hotel merchant',
            'amount_minor': 12345, 'currency': 'DKK',
            'items': [{'description': 'One synthetic room including taxes', 'quantity': 1, 'unit_amount_minor': 12345}],
            'terms': ['Free cancellation before the displayed deadline.', 'Pay the exact total shown.'], 'expires_at': 2500})
        self.assistant = {'travel': 'synthetic-travel-' + 'a' * 40}
        self.operator = 'synthetic-operator-' + 'b' * 40
        self.servers = [SERVICE.make_server(self.grants, role, 0, self.assistant, self.operator,
                                           transactions=self.transactions) for role in ('assistant', 'operator')]
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in self.servers]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server, thread in zip(self.servers, self.threads):
            server.shutdown(); server.server_close(); thread.join()
        self.temporary.cleanup()

    def http(self, role, method, path, body=None, *, token=None, headers=None):
        server = self.servers[0 if role == 'assistant' else 1]
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        fields = dict(headers or {})
        if token:
            fields['Authorization'] = 'Bearer ' + token
        if isinstance(body, dict):
            body = urlencode(body)
            fields.setdefault('Content-Type', 'application/x-www-form-urlencoded')
        try:
            connection.request(method, path, body=body, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read().decode()
        finally:
            connection.close()

    def page(self):
        code, _headers, raw = self.http('operator', 'GET', '/transactions/review/' + self.item['transaction_id'], token=self.operator)
        self.assertEqual(code, 200)
        return raw

    def decision(self, action='approve'):
        raw = self.page()
        forms = re.findall(r'<form[^>]*>(.*?)</form>', raw, re.S)
        selected = next(form for form in forms if f'name="action" value="{action}"' in form)
        return {'action': action, 'digest': self.item['proposal_digest'],
                'csrf': re.search(r'name="csrf" value="([^"]+)"', selected)[1]}

    def test_only_owner_can_view_exact_quote_and_unqualified_banner_is_truthful(self):
        path = '/transactions/review/' + self.item['transaction_id']
        self.assertEqual(self.http('operator', 'GET', path)[0], 401)
        self.assertEqual(self.http('operator', 'GET', path, token=self.assistant['travel'])[0], 401)
        self.assertEqual(self.http('assistant', 'GET', path, token=self.assistant['travel'])[0], 404)
        code, headers, raw = self.http('operator', 'GET', path, token=self.operator)
        self.assertEqual(code, 200)
        for text in ['Synthetic hotel merchant', 'DKK 123.45', 'One synthetic room including taxes',
                     'Free cancellation', 'No transaction finalizer is qualified', 'cannot purchase or book anything']:
            self.assertIn(text, raw)
        self.assertNotIn(self.operator, raw)
        self.assertNotIn(self.assistant['travel'], raw)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])

    def test_transaction_decision_requires_exact_origin_csrf_digest_and_is_single_use(self):
        path = '/transactions/decision/' + self.item['transaction_id']
        form = self.decision()
        for fields in [{}, {'Origin': 'https://attacker.invalid'},
                       {'Origin': self.servers[1].operator_origin, 'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.http('operator', 'POST', path, form, token=self.operator, headers=fields)[0], 403)
        headers = {'Origin': self.servers[1].operator_origin}
        for changed in [dict(form, csrf='wrong'), dict(form, digest='0' * 64), dict(form, action='deny'),
                        dict(form, amount_minor='1'), dict(form, minutes='999')]:
            self.assertEqual(self.http('operator', 'POST', path, changed, token=self.operator, headers=headers)[0], 400)
        code, fields, raw = self.http('operator', 'POST', path, form, token=self.operator, headers=headers)
        self.assertEqual(code, 303)
        self.assertEqual(fields['Location'], '/transactions/review/' + self.item['transaction_id'])
        approved = self.transactions.review(self.item['transaction_id'])
        self.assertEqual(approved['status'], 'approved')
        self.assertFalse(approved['execution_available'])
        self.assertEqual(self.http('operator', 'POST', path, form, token=self.operator, headers=headers)[0], 400)

    def test_no_agent_creation_claim_or_decision_endpoint_exists(self):
        for path in ['/transactions/propose', '/transactions/claim', '/transactions/decision/' + self.item['transaction_id']]:
            self.assertEqual(self.http('assistant', 'POST', path, {'action': 'approve'}, token=self.assistant['travel'])[0], 403)
        self.assertEqual(self.transactions.review(self.item['transaction_id'])['status'], 'pending')

    def test_owner_can_deny_and_revoke_but_cannot_execute(self):
        path = '/transactions/decision/' + self.item['transaction_id']
        origin = {'Origin': self.servers[1].operator_origin}
        self.assertEqual(self.http('operator', 'POST', path, self.decision(), token=self.operator, headers=origin)[0], 303)
        self.assertEqual(self.http('operator', 'POST', path, self.decision('revoke'), token=self.operator, headers=origin)[0], 303)
        self.assertEqual(self.transactions.review(self.item['transaction_id'])['status'], 'revoked')
        self.assertEqual(self.http('operator', 'POST', '/transactions/claim', {}, token=self.operator, headers=origin)[0], 403)

    def test_owner_denial_is_terminal_and_has_no_approval_controls(self):
        path = '/transactions/decision/' + self.item['transaction_id']
        form = self.decision('deny')
        origin = {'Origin': self.servers[1].operator_origin}
        self.assertEqual(self.http('operator', 'POST', path, form, token=self.operator, headers=origin)[0], 303)
        self.assertEqual(self.transactions.review(self.item['transaction_id'])['status'], 'denied')
        self.assertNotIn('Approve exact transaction', self.page())

    def test_expired_quote_has_no_approval_buttons(self):
        self.now[0] = self.item['expires_at']
        raw = self.page()
        self.assertIn('Proposal expired', raw)
        self.assertNotIn('Approve exact transaction', raw)

    def test_rendering_escapes_every_upstream_text_and_never_guesses_unknown_currency_precision(self):
        item = copy.deepcopy(self.item)
        item['payee'] = '<script>merchant</script>'
        item['items'][0]['description'] = '<img src=x onerror=anything>'
        item['terms'] = ['<a href="https://attacker.invalid">terms</a>']
        raw = review_transaction(item, {})
        self.assertIn('&lt;script&gt;merchant&lt;/script&gt;', raw)
        self.assertNotIn('<script>', raw)
        self.assertNotIn('<img', raw)
        self.assertNotIn('<a href="https://attacker.invalid">', raw)
        self.assertEqual(amount(12345, 'XYZ'), '12,345 minor units · XYZ')
        self.assertEqual(amount(12345, 'JPY'), 'JPY 12345')
        self.assertEqual(amount(12345, 'KWD'), 'KWD 12.345')
        item['quote_expires_at'] = 2**53
        self.assertIn(str(2**53) + ' Unix seconds', review_transaction(item, {}))


class WiringTests(unittest.TestCase):
    def test_cli_shares_registry_with_ledger_notifier_and_keeps_runtime_gate_false_by_default(self):
        for worker_ready in [False, True]:
            with self.subTest(worker_ready=worker_ready):
                registry = MagicMock()
                registry.profile.return_value = {'qualified': True}
                registry.validate_scope.return_value = 'search'
                argv = ['openclaw-access.py', '--state', '/synthetic/state', '--assistant-tokens-file', '/synthetic/assistant-tokens.json',
                        '--operator-token-file', '/synthetic/operator-token', '--notify-config', '/synthetic/notify.json',
                        '--notify-state', '/synthetic/notify-state', '--sites-config', '/synthetic/sites.json',
                        '--transactions-state', '/synthetic/transaction-state', '--operator-origin', 'http://127.0.0.1:18813']
                if worker_ready:
                    argv.append('--site-worker-ready')
                responses = [json.dumps({'travel': 'synthetic-' + 'a' * 40}).encode(), ('synthetic-' + 'b' * 40).encode()]
                with patch.object(sys, 'argv', argv), patch.object(SERVICE.os, 'geteuid', return_value=0), \
                     patch.object(SERVICE, 'read_private', side_effect=responses), \
                     patch.object(SERVICE.SiteRegistry, 'from_file', return_value=registry), \
                     patch.object(SERVICE, 'AccessGrants') as grants, patch.object(SERVICE, 'SiteTransactions') as transactions, \
                     patch('openclaw_access_notifications.MattermostNotifier') as notifier, \
                     patch.object(SERVICE, 'make_server', side_effect=[MagicMock(), MagicMock()]) as servers, \
                     patch.object(SERVICE.threading, 'Thread'):
                    SERVICE.main()
                self.assertIs(grants.call_args.kwargs['registry'], registry)
                self.assertIs(notifier.call_args.kwargs['registry'], registry)
                gate = grants.call_args.kwargs['execution_available']
                self.assertEqual(gate('hotels.com', 'search'), worker_ready)
                registry.profile.return_value = {'qualified': False}
                self.assertFalse(gate('hotels.com', 'search'))
                self.assertEqual(transactions.call_args.args[0], Path('/synthetic/transaction-state'))
                self.assertNotIn('qualified_drivers', transactions.call_args.kwargs)
                self.assertTrue(all(call.kwargs['transactions'] is transactions.return_value for call in servers.call_args_list))


if __name__ == '__main__':
    unittest.main()
