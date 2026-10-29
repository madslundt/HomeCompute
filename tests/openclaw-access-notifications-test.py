#!/usr/bin/env python3
"""Owner-only notification tests. No live Mattermost or real credentials."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_access_notifications import MattermostNotifier, NotificationRefused, ORIGIN, render


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.config = {'schema_version': 1, 'bot_id': 'b' * 26, 'owner_id': 'o' * 26,
                       'channel_id': 'd' * 26, 'bot_token': 'synthetic-bot-token-' + 'x' * 40,
                       'review_origin': 'https://home-core.tail479ad.ts.net:18814'}
        self.config_file = self.base / 'notification.json'
        self.save()
        self.calls = []
        self.responses = {
            '/users/me': {'id': self.config['bot_id'], 'is_bot': True, 'roles': 'system_user', 'delete_at': 0},
            '/channels/' + self.config['channel_id']: {'id': self.config['channel_id'], 'type': 'D', 'delete_at': 0},
            '/channels/' + self.config['channel_id'] + '/members?page=0&per_page=3': [
                {'channel_id': self.config['channel_id'], 'user_id': identity}
                for identity in (self.config['bot_id'], self.config['owner_id'])],
        }
        immutable = {'request_id': '00000000-0000-4000-8000-000000000001', 'actor': 'travel',
                     'account': 'hotels.com', 'scope': 'search', 'requested_minutes': 30, 'requested_at': 1000}
        self.item = {**immutable, 'request_digest': hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                     'status': 'pending', 'approved_at': None, 'expires_at': None,
                     'account_execution_available': False, 'transaction_execution_available': False}

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        self.config_file.write_text(json.dumps(self.config))
        self.config_file.chmod(0o600)

    def transport(self, method, path, body):
        self.calls.append((method, path, body))
        if method == 'GET':
            return self.responses[path]
        self.assertEqual((method, path), ('POST', '/posts'))
        return {'id': 'p' * 26, 'channel_id': self.config['channel_id'], 'user_id': self.config['bot_id']}

    def notifier(self, **kwargs):
        return MattermostNotifier(self.config_file, self.base / 'state', transport=kwargs.get('transport', self.transport))

    def test_exact_owner_dm_deterministic_message_and_private_durable_receipt(self):
        notify = self.notifier()
        receipt = notify(self.item)
        self.assertEqual(receipt['delivery'], 'delivered')
        self.assertFalse(receipt['duplicate'])
        post = self.calls[-1]
        self.assertEqual(post[:2], ('POST', '/posts'))
        self.assertEqual(set(post[2]), {'channel_id', 'message'})
        self.assertEqual(post[2]['channel_id'], self.config['channel_id'])
        self.assertIn('Lyra (travel)', post[2]['message'])
        self.assertIn('30 minutes', post[2]['message'])
        self.assertEqual(re.findall(r'\[([^]]+)\]\(([^)]+)\)', post[2]['message']),
                         [('Review request', 'https://mattermost.home.arpa:18814/review/' + self.item['request_id'])])
        self.assertNotIn(self.config['review_origin'], post[2]['message'])
        self.assertIn('adapter is not connected', post[2]['message'])
        self.assertNotIn(self.config['bot_token'], json.dumps(receipt) + post[2]['message'])
        self.assertEqual(stat.S_IMODE((self.base / 'state').stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.base / 'state/notifications.sqlite').stat().st_mode), 0o600)
        previous = len(self.calls)
        self.assertTrue(self.notifier().reconcile(self.item)['duplicate'])
        self.assertEqual(previous, len(self.calls))

    def test_shared_bridge_principal_is_named_without_claiming_specialist_identity(self):
        self.item['actor'] = 'openclaw'
        immutable = {key: self.item[key] for key in ('request_id', 'actor', 'account', 'scope', 'requested_minutes', 'requested_at')}
        self.item['request_digest'] = hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.notifier()(self.item)
        self.assertIn('Agent: OpenClaw (openclaw)', self.calls[-1][2]['message'])
        self.assertNotIn('Lyra', self.calls[-1][2]['message'])

    def test_push_lead_and_readable_body_keep_all_approval_limits(self):
        text = render(self.item, self.config['review_origin'])
        lead = text.splitlines()[0]
        self.assertEqual(lead, 'Account access requested: Lyra needs your review for hotels.com (30 minutes).')
        self.assertLessEqual(len(lead), 220)
        self.assertIn('\n\n**Request details**\n\n- Agent:', text)
        for caveat in ('Replies here do not approve access.',
                       'Current setup records permissions; the site adapter is not connected or qualified yet.',
                       'Access approval submits no order; bookings and payments require separate approval.'):
            self.assertIn(caveat, text)
        registry = type('Registry', (), {
            'profile': lambda self, account: {'qualified': True},
            'validate_scope': lambda self, account, scope: None})()
        available = render(dict(self.item, account_execution_available=True), self.config['review_origin'], registry)
        self.assertIn('A reviewed site adapter is available for this account and task.', available)
        self.assertIn('This does not mean the account is signed in. A private owner sign-in step may be required.', available)

    def test_audience_or_admin_token_mismatch_never_posts(self):
        changes = [('/users/me', {'id': self.config['bot_id'], 'is_bot': True, 'roles': 'system_admin system_user'}),
                   ('/users/me', {'id': 'a' * 26, 'is_bot': True, 'roles': 'system_user'}),
                   ('/channels/' + self.config['channel_id'], {'id': self.config['channel_id'], 'type': 'P'}),
                   ('/channels/' + self.config['channel_id'] + '/members?page=0&per_page=3',
                    [{'channel_id': self.config['channel_id'], 'user_id': self.config['bot_id']},
                     {'channel_id': self.config['channel_id'], 'user_id': 'x' * 26}])]
        for path, changed in changes:
            original = self.responses[path]
            self.responses[path] = changed
            with self.assertRaises(NotificationRefused):
                self.notifier()(self.item)
            self.responses[path] = original
        self.assertFalse(any(method == 'POST' for method, _path, _body in self.calls))

    def test_uncertain_post_and_ambiguous_receipt_are_never_retried(self):
        def broken(method, path, body):
            if method == 'POST':
                self.calls.append((method, path, body))
                raise RuntimeError(self.config['bot_token'])
            return self.transport(method, path, body)
        notify = self.notifier(transport=broken)
        with self.assertRaises(NotificationRefused) as error:
            notify(self.item)
        self.assertNotIn(self.config['bot_token'], str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        with self.assertRaisesRegex(NotificationRefused, 'uncertain'):
            self.notifier().reconcile(self.item)
        self.assertEqual(sum(method == 'POST' for method, _path, _body in self.calls), 1)

    def test_reconcile_absent_reservation_permits_only_one_first_delivery(self):
        # Models a grant already committed before a service crash prevented its
        # original notification callback from being invoked at all.
        notify = self.notifier()
        receipt = notify.reconcile(self.item)
        self.assertEqual(receipt['delivery'], 'delivered')
        self.assertFalse(receipt['duplicate'])
        self.assertTrue(self.notifier().reconcile(self.item)['duplicate'])
        self.assertEqual(sum(method == 'POST' for method, _path, _body in self.calls), 1)

    def test_concurrent_callbacks_cannot_duplicate_a_post(self):
        notify = self.notifier()
        def call(_index):
            try:
                return notify(self.item)
            except NotificationRefused:
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(call, range(6)))
        self.assertTrue(any(receipt and receipt['delivery'] == 'delivered' for receipt in receipts))
        self.assertEqual(sum(method == 'POST' for method, _path, _body in self.calls), 1)

    def test_ambiguous_post_receipt_stays_uncertain_and_is_not_retried(self):
        def ambiguous(method, path, body):
            if method == 'POST':
                self.calls.append((method, path, body))
                return {'id': 'p' * 26, 'channel_id': self.config['channel_id'], 'user_id': 'x' * 26}
            return self.transport(method, path, body)
        with self.assertRaises(NotificationRefused):
            self.notifier(transport=ambiguous)(self.item)
        with self.assertRaisesRegex(NotificationRefused, 'uncertain'):
            self.notifier()(self.item)
        self.assertEqual(sum(method == 'POST' for method, _path, _body in self.calls), 1)

    def test_delivered_reference_cannot_be_rebound_to_changed_request(self):
        notify = self.notifier()
        notify(self.item)
        changed = dict(self.item, requested_minutes=31)
        immutable = {key: changed[key] for key in ('request_id', 'actor', 'account', 'scope', 'requested_minutes', 'requested_at')}
        changed['request_digest'] = hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        previous = len(self.calls)
        with self.assertRaisesRegex(NotificationRefused, 'already bound'):
            notify(changed)
        self.assertEqual(previous, len(self.calls))

    def test_untrusted_fields_scope_origin_and_digest_are_refused(self):
        notify = self.notifier()
        for change in [{'actor': '@all'}, {'actor': []}, {'account': 'elsewhere'}, {'scope': 'book'}, {'scope': []}, {'password': 'synthetic'},
                       {'requested_minutes': True}, {'requested_minutes': 0}, {'request_id': '../secret'},
                       {'request_digest': '0' * 64}, {'account_execution_available': True}]:
            with self.assertRaises(NotificationRefused):
                notify(dict(self.item, **change))
        self.assertEqual(self.calls, [])
        for origin in ['http://home-core.tail479ad.ts.net:18814', 'https://attacker.invalid:443',
                       'https://mattermost.home.arpa:18814',
                       'https://home-core.tail479ad.ts.net:18815',
                       self.config['review_origin'] + '?token=synthetic', 'https://home-core.tail479ad.ts.net:65536']:
            with self.assertRaises(NotificationRefused):
                render(self.item, origin)

    def test_private_configuration_and_unknown_keys_are_required(self):
        self.config_file.chmod(0o644)
        with self.assertRaises(NotificationRefused):
            self.notifier()
        self.config['operator_session'] = 'synthetic-admin-secret'
        self.save()
        with self.assertRaises(NotificationRefused):
            self.notifier()

    def test_http_transport_uses_only_loopback_origin_and_suppresses_raw_errors(self):
        notify = self.notifier()
        notify.transport = notify._http
        with patch('openclaw_access_notifications.urllib.request.build_opener') as build:
            build.return_value.open.side_effect = RuntimeError(self.config['bot_token'])
            with self.assertRaises(NotificationRefused) as error:
                notify(self.item)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, ORIGIN + '/users/me')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.config['bot_token'])
        self.assertNotIn(self.config['bot_token'], str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
