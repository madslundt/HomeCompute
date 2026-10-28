#!/usr/bin/env python3
"""Metadata-only authorization tests with disposable synthetic credentials."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import http.client
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_access_grants import AccessGrants, GrantRefused

SPEC = importlib.util.spec_from_file_location('access_service', ROOT / 'scripts/openclaw-access.py')
SERVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVICE)


class GrantTest(unittest.TestCase):
    def test_startup_diagnostics_preserve_category_without_private_exception_text(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            SERVICE.report_failure(OSError(98, 'PRIVATE TOKEN OR URL'), 'operator_listener')
            SERVICE.report_failure(ValueError('PRIVATE CONFIGURATION'), 'private_configuration')
        values = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(values[0], {'event': 'access_service_failed', 'stage': 'operator_listener',
                                     'error_code': 'listener_unavailable'})
        self.assertEqual(values[1]['error_code'], 'invalid_configuration')
        self.assertNotIn('PRIVATE', output.getvalue())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.time = [1000]
        self.ledger = AccessGrants(self.base / 'state', clock=lambda: self.time[0])
        self.values = {'account': 'hotels.com', 'scope': 'search', 'minutes': 30,
                       'request_key': 'synthetic-request-0001'}

    def tearDown(self):
        self.temporary.cleanup()

    def requested(self, actor='travel', **changes):
        return self.ledger.request(actor, dict(self.values, **changes))[0]

    def decide(self, item, action='approve', minutes=15):
        return self.ledger.decide(item['request_id'], item['request_digest'], action, minutes,
                                  self.ledger.challenge(item['request_id'], action))

    def test_request_is_idempotent_and_isolated_per_actor(self):
        item = self.requested()
        again, created = self.ledger.request('travel', self.values)
        self.assertFalse(created)
        self.assertEqual(item, again)
        self.assertNotEqual(self.requested('researcher')['request_id'], item['request_id'])
        with self.assertRaises(GrantRefused):
            self.ledger.status(item['request_id'], actor='researcher')
        with self.assertRaises(GrantRefused):
            self.requested(minutes=60)
        self.assertEqual(stat.S_IMODE((self.base / 'state').stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.base / 'state/access.sqlite').stat().st_mode), 0o600)

    def test_only_explicit_scope_is_authorized_and_exact_deadline_expires(self):
        item = self.requested()
        with self.assertRaises(GrantRefused):
            self.ledger.authorize('travel', item['request_id'], 'search')
        approved = self.decide(item)
        self.assertEqual(approved['expires_at'], 1900)
        self.time[0] = 1899
        self.assertEqual(self.ledger.authorize('travel', item['request_id'], 'search')['status'], 'active')
        for actor, scope in [('researcher', 'search'), ('travel', 'prepare_booking'), ('travel', 'book'), ('travel', 'pay')]:
            with self.assertRaises(GrantRefused):
                self.ledger.authorize(actor, item['request_id'], scope)
        self.time[0] = 1900
        with self.assertRaises(GrantRefused):
            self.ledger.authorize('travel', item['request_id'], 'search')
        self.assertEqual(self.ledger.status(item['request_id'])['status'], 'expired')

    def test_revoke_and_deny_cannot_be_reapproved_or_revived_by_replay(self):
        item = self.requested()
        nonce = self.ledger.challenge(item['request_id'], 'approve')
        self.ledger.decide(item['request_id'], item['request_digest'], 'approve', 5, nonce)
        for action in ['approve', 'deny']:
            with self.assertRaises(GrantRefused):
                self.ledger.decide(item['request_id'], item['request_digest'], action, 5, nonce)
        self.assertEqual(self.decide(item, 'revoke')['status'], 'revoked')
        with self.assertRaises(GrantRefused):
            self.ledger.authorize('travel', item['request_id'], 'search')
        denied = self.requested(request_key='synthetic-request-0002')
        self.assertEqual(self.decide(denied, 'deny')['status'], 'denied')
        with self.assertRaises(GrantRefused):
            self.decide(denied)

    def test_digest_and_action_bound_single_use_challenges(self):
        item = self.requested()
        nonce = self.ledger.challenge(item['request_id'], 'approve')
        for changed_digest, action in [('0' * 64, 'approve'), (item['request_digest'], 'deny')]:
            with self.assertRaises(GrantRefused):
                self.ledger.decide(item['request_id'], changed_digest, action, 5, nonce)
        self.time[0] += 300
        with self.assertRaises(GrantRefused):
            self.ledger.decide(item['request_id'], item['request_digest'], 'approve', 5, nonce)
        self.time[0] = 4600
        with self.assertRaises(GrantRefused):
            self.decide(item)

    def test_clock_rollback_and_challenge_for_another_request_fail_closed(self):
        first = self.requested()
        second = self.requested(request_key='synthetic-request-0002')
        nonce = self.ledger.challenge(first['request_id'], 'approve')
        with self.assertRaises(GrantRefused):
            self.ledger.decide(second['request_id'], second['request_digest'], 'approve', 5, nonce)
        self.decide(first)
        self.time[0] = 999
        for item in [first, second]:
            with self.assertRaises(GrantRefused):
                self.ledger.authorize('travel', item['request_id'], 'search')

    def test_clock_rollback_inside_active_window_permanently_expires_grant(self):
        item = self.requested()
        self.decide(item, minutes=15)
        self.time[0] = 1500
        self.ledger.authorize('travel', item['request_id'], 'search')
        self.time[0] = 1400
        self.assertEqual(self.ledger.status(item['request_id'])['status'], 'expired')
        self.time[0] = 1600
        with self.assertRaises(GrantRefused):
            self.ledger.authorize('travel', item['request_id'], 'search')

    def test_new_request_expires_abandoned_actor_queue_before_applying_budget(self):
        for index in range(20):
            self.requested(request_key=f'synthetic-request-{index:04}')
        with self.assertRaises(GrantRefused):
            self.requested(request_key='synthetic-request-new1')
        self.time[0] = 4600
        item, created = self.ledger.request('travel', dict(self.values, request_key='synthetic-request-new1'))
        self.assertTrue(created)
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(sum(row['status'] == 'expired' for row in self.ledger.listing()), 20)

    def test_concurrent_replays_create_one_request_and_one_approval(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            requests = list(pool.map(lambda _n: self.ledger.request('travel', self.values), range(8)))
        self.assertEqual(sum(created for _item, created in requests), 1)
        self.assertEqual(len({item['request_id'] for item, _created in requests}), 1)
        item = requests[0][0]
        nonce = self.ledger.challenge(item['request_id'], 'approve')
        def approve(_n):
            try:
                self.ledger.decide(item['request_id'], item['request_digest'], 'approve', 5, nonce)
                return True
            except GrantRefused:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(approve, range(8))), 1)

    def test_invalid_requests_never_create_account_or_transaction_permissions(self):
        changes = [{'scope': 'book'}, {'scope': 'pay'}, {'account': 'expedia.com'}, {'minutes': True},
                   {'minutes': 0}, {'minutes': 1441}, {'actor': 'operator'}, {'password': 'synthetic-secret'}]
        for change in changes:
            with self.subTest(fields=list(change)):
                with self.assertRaises(GrantRefused):
                    self.ledger.request('travel', dict(self.values, **change))
        self.assertEqual(self.ledger.listing(), [])


class HttpTest(unittest.TestCase):
    requested = GrantTest.requested

    def setUp(self):
        GrantTest.setUp(self)
        self.assistant = {'travel': 'synthetic-travel-' + 'a' * 40, 'researcher': 'synthetic-research-' + 'b' * 40}
        self.operator = 'synthetic-operator-' + 'c' * 40
        self.notifications = []
        self.servers = [SERVICE.make_server(self.ledger, role, 0, self.assistant, self.operator,
                                           notify=lambda item: self.notifications.append(item))
                        for role in ('assistant', 'operator')]
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in self.servers]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server, thread in zip(self.servers, self.threads):
            server.shutdown()
            server.server_close()
            thread.join()
        GrantTest.tearDown(self)

    def http(self, role, method, path, body=None, *, token=None, headers=None):
        server = self.servers[0 if role == 'assistant' else 1]
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        fields = dict(headers or {})
        if token is not None:
            fields['Authorization'] = 'Bearer ' + token
        if isinstance(body, dict):
            body = json.dumps(body)
            fields.setdefault('Content-Type', 'application/json')
        try:
            connection.request(method, path, body=body, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read().decode()
        finally:
            connection.close()

    def test_assistant_identity_comes_from_auth_and_cross_role_calls_are_denied(self):
        status, _headers, raw = self.http('assistant', 'POST', '/access/request', self.values, token=self.assistant['travel'])
        self.assertEqual(status, 200)
        item = json.loads(raw)
        self.assertFalse(item['account_execution_available'])
        self.assertFalse(item['transaction_execution_available'])
        self.assertEqual(item['actor'], 'travel')
        self.http('assistant', 'POST', '/access/request', self.values, token=self.assistant['travel'])
        self.assertEqual(len(self.notifications), 1)
        reference = item['request_id']
        self.assertEqual(self.http('assistant', 'GET', '/access/' + reference, token=self.assistant['researcher'])[0], 400)
        self.assertEqual(self.http('assistant', 'POST', '/decision/' + reference, {}, token=self.assistant['travel'])[0], 403)
        self.assertEqual(self.http('operator', 'GET', '/', token=self.assistant['travel'])[0], 401)
        self.assertEqual(self.http('assistant', 'GET', '/access/' + reference, token=self.operator)[0], 401)
        self.assertEqual(self.http('assistant', 'POST', '/access/request', dict(self.values, actor='operator'), token=self.assistant['travel'])[0], 400)
        for secret in [self.operator, *self.assistant.values()]:
            self.assertNotIn(secret, raw)

    def test_operator_page_basic_prompt_and_csrf_origin_replay_checks(self):
        item = self.requested()
        reference = item['request_id']
        status, headers, raw = self.http('operator', 'GET', '/review/' + reference)
        self.assertEqual(status, 401)
        self.assertIn('Basic ', headers['WWW-Authenticate'])
        basic = 'Basic ' + base64.b64encode(('operator:' + self.operator).encode()).decode()
        status, headers, raw = self.http('operator', 'GET', '/review/' + reference, headers={'Authorization': basic})
        self.assertEqual(status, 200)
        self.assertIn('Approval records permission only', raw)
        self.assertNotIn(self.operator, raw)
        self.assertNotIn(basic, raw)
        nonce = re.search('name="csrf" value="([^"]+)"', raw)[1]
        body = urlencode({'action': 'approve', 'digest': item['request_digest'], 'csrf': nonce, 'minutes': '7'})
        fields = {'Content-Type': 'application/x-www-form-urlencoded'}
        for origin in [None, 'null', 'https://attacker.invalid']:
            if origin:
                fields['Origin'] = origin
            self.assertEqual(self.http('operator', 'POST', '/decision/' + reference, body, token=self.operator, headers=fields)[0], 403)
        fields['Origin'] = self.servers[1].operator_origin
        self.assertEqual(self.http('operator', 'POST', '/decision/' + reference, body, token=self.operator, headers=fields)[0], 303)
        self.assertEqual(self.ledger.authorize('travel', reference, 'search')['expires_at'], 1420)
        self.assertEqual(self.http('operator', 'POST', '/decision/' + reference, body, token=self.operator, headers=fields)[0], 400)

    def test_listener_can_restart_immediately_after_serving_a_browser(self):
        self.assertEqual(self.http('operator', 'GET', '/', token=self.operator)[0], 200)
        original = self.servers[1]; port = original.server_port
        original.shutdown(); original.server_close(); self.threads[1].join()
        server = SERVICE.make_server(self.ledger, 'operator', port, self.assistant, self.operator)
        self.servers[1] = server
        self.threads[1] = threading.Thread(target=server.serve_forever, daemon=True)
        self.threads[1].start()
        self.assertEqual(self.http('operator', 'GET', '/', token=self.operator)[0], 200)
        # Reusing a closed listener must not allow a second active listener.
        with self.assertRaises(OSError):
            SERVICE.make_server(self.ledger, 'operator', port, self.assistant, self.operator)

    def test_host_origin_duplicates_and_error_text_cannot_expand_access_or_leak(self):
        token = self.assistant['travel']
        for headers in [{'Host': 'attacker.invalid'}, {'Origin': 'http://127.0.0.1'}, {'Transfer-Encoding': 'chunked'}]:
            status, _fields, raw = self.http('assistant', 'POST', '/access/request', self.values, token=token, headers=headers)
            self.assertIn(status, {400, 403})
            self.assertNotIn(token, raw)
        duplicate = '{"account":"hotels.com","account":"expedia.com","scope":"search","minutes":30,"request_key":"synthetic-request-0001"}'
        self.assertEqual(self.http('assistant', 'POST', '/access/request', duplicate, token=token,
                                   headers={'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.ledger.listing(), [])

    def test_cookie_owner_mode_disables_operator_basic_and_bearer_fallback(self):
        server = self.servers[1]
        server.shutdown()
        server.server_close()
        self.threads[1].join()
        cookie = 'MMAUTHTOKEN=synthetic-owner-cookie'
        server = SERVICE.make_server(self.ledger, 'operator', 0, self.assistant, self.operator,
                                     operator_auth=lambda headers: headers.get('Cookie') == cookie,
                                     login_url='https://home-core.tail479ad.ts.net:9443/login')
        self.servers[1] = server
        self.threads[1] = threading.Thread(target=server.serve_forever, daemon=True)
        self.threads[1].start()
        item = self.requested()
        path = '/review/' + item['request_id']
        for token in [None, self.operator, self.assistant['travel']]:
            code, headers, raw = self.http('operator', 'GET', path, token=token)
            self.assertEqual(code, 401)
            self.assertNotIn('WWW-Authenticate', headers)
            self.assertIn('Sign in to Mattermost', raw)
        self.assertEqual(self.http('operator', 'GET', path, token=self.operator, headers={'Cookie': cookie})[0], 401)
        code, _headers, raw = self.http('operator', 'GET', path, headers={'Cookie': cookie})
        self.assertEqual(code, 200)
        nonce = re.search('name="csrf" value="([^"]+)"', raw)[1]
        body = urlencode({'action': 'approve', 'digest': item['request_digest'], 'csrf': nonce, 'minutes': '7'})
        fields = {'Cookie': cookie, 'Content-Type': 'application/x-www-form-urlencoded', 'Origin': server.operator_origin}
        self.assertEqual(self.http('operator', 'POST', '/decision/' + item['request_id'], body, headers=fields)[0], 303)
        self.assertEqual(self.ledger.status(item['request_id'])['expires_at'], 1420)

    def test_missing_and_failed_notification_delivery_are_explicit_and_never_leak(self):
        def failed(_item):
            raise RuntimeError('synthetic-private-notification-token')
        for index, callback, expected in [(1, None, 'not_configured'), (2, failed, 'delivery_uncertain')]:
            server = SERVICE.make_server(self.ledger, 'assistant', 0, self.assistant, self.operator, notify=callback)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            original = self.servers[0]
            self.servers[0] = server
            try:
                values = dict(self.values, request_key=f'notification-request-{index:04}')
                status, _headers, raw = self.http('assistant', 'POST', '/access/request', values,
                                                  token=self.assistant['travel'])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw)['notification_delivery'], expected)
                self.assertNotIn('synthetic-private-notification-token', raw)
            finally:
                self.servers[0] = original
                server.shutdown()
                server.server_close()
                thread.join()

    def test_duplicate_pending_request_uses_only_explicit_durable_reconciliation(self):
        item = self.requested()  # The ledger committed; its callback never ran.
        calls = []
        class DurableNotifier:
            def __call__(self, request):
                calls.append('ordinary')
                raise AssertionError('A duplicate must use reconciliation')

            def reconcile(self, request):
                calls.append(('reconcile', request['request_id']))
                return {'delivery': 'delivered'}

        server = SERVICE.make_server(self.ledger, 'assistant', 0, self.assistant, self.operator,
                                     notify=DurableNotifier())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = self.servers[0]
        self.servers[0] = server
        try:
            code, _headers, raw = self.http('assistant', 'POST', '/access/request', self.values,
                                            token=self.assistant['travel'])
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw)['request_id'], item['request_id'])
            self.assertEqual(json.loads(raw)['notification_delivery'], 'delivered')
            self.assertEqual(calls, [('reconcile', item['request_id'])])
            self.decide_for_reconcile_fixture(item)
            self.http('assistant', 'POST', '/access/request', self.values, token=self.assistant['travel'])
            self.assertEqual(len(calls), 1, 'Active grants must not initiate a notification')
        finally:
            self.servers[0] = original
            server.shutdown()
            server.server_close()
            thread.join()

    def decide_for_reconcile_fixture(self, item):
        self.ledger.decide(item['request_id'], item['request_digest'], 'approve', 5,
                           self.ledger.challenge(item['request_id'], 'approve'))


@unittest.skipUnless(os.environ.get('HOMECOMPUTE_REAL_PLAYWRIGHT') == '1',
                     'run in isolated Playwright image with synthetic owner cookies')
class BrowserApprovalTest(unittest.TestCase):
    def test_actual_desktop_and_mobile_forms_send_trusted_origin_and_approve(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                for mobile in (False, True):
                    with self.subTest(mobile=mobile), tempfile.TemporaryDirectory() as directory:
                        ledger = AccessGrants(Path(directory) / 'state')
                        item, _ = ledger.request('travel', {'account': 'hotels.com', 'scope': 'search',
                            'minutes': 15, 'request_key': 'synthetic-browser-approval'})
                        cookie = 'synthetic-owner-cookie-never-a-real-session'
                        server = SERVICE.make_server(ledger, 'operator', 0,
                            {'travel': 'synthetic-travel-' + 'a' * 40}, None,
                            operator_auth=lambda headers: headers.get('Cookie') == 'MMAUTHTOKEN=' + cookie)
                        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                        context = browser.new_context(is_mobile=mobile, has_touch=mobile,
                                                      viewport={'width': 390 if mobile else 1280, 'height': 844})
                        try:
                            origin = server.operator_origin
                            context.add_cookies([{'name': 'MMAUTHTOKEN', 'value': cookie, 'url': origin,
                                                  'httpOnly': True, 'sameSite': 'Lax'}])
                            page = context.new_page(); page.goto(origin + '/review/' + item['request_id'])
                            with page.expect_response(lambda response: '/decision/' in response.url) as decision:
                                page.get_by_role('button', name='Approve access', exact=True).click()
                            self.assertEqual(decision.value.status, 303,
                                'Actual form submission must pass exact-origin validation')
                            self.assertEqual(ledger.status(item['request_id'])['status'], 'active')
                        finally:
                            context.close(); server.shutdown(); server.server_close(); thread.join()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
