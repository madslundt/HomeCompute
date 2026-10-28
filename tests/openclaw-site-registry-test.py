#!/usr/bin/env python3
"""Registered-site policy, legacy grant migration and generic metadata fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_access_grants import AccessGrants, GrantRefused, digest
from openclaw_access_notifications import NotificationRefused, render
from openclaw_access_page import listing, review
from openclaw_bitwarden import CustodyRefused
from openclaw_site_registry import DEFAULT_HOTELS, SiteRegistry, SiteRefused, account_name, validate_profile


def shop_profile():
    return {'account': 'shop.example', 'label': 'Fixture Shop', 'driver': 'form', 'domains': ['shop.example'],
            'scopes': ['search', 'prepare_purchase', 'read'],
            'actions': ['navigate', 'login', 'fill', 'submit_search', 'read', 'prepare'],
            'operations': {
                'search': {'scope': 'search', 'parameters': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 80}}, 'required': ['query']},
                'prepare': {'scope': 'prepare_purchase', 'parameters': {'product': {'type': 'string', 'minLength': 1, 'maxLength': 64}}, 'required': ['product']},
                'read': {'scope': 'read', 'parameters': {}, 'required': []}},
            'driver_config': {
                'login_url': 'https://shop.example/login',
                'login': {'username_selector': 'input[name=email]', 'password_selector': 'input[type=password]',
                          'submit_selector': 'button[type=submit]', 'success_selector': 'a[href="/logout"]'},
                'operations': {
                    'search': {'url': 'https://shop.example/search', 'fields': {'query': 'input[name=q]'},
                               'submit_selector': 'button[type=submit]', 'result_selector': '[data-result]'},
                    'prepare': {'url': 'https://shop.example/cart', 'fields': {'product': 'input[name=product]'}},
                    'read': {'url': 'https://shop.example/account', 'fields': {}, 'result_selector': '[data-item]'}},
                'network_posts': {'https://shop.example/session': ['LOGIN'], 'https://shop.example/api/search': ['READ']}},
            'qualified': False}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.profiles = [copy.deepcopy(DEFAULT_HOTELS), shop_profile()]
        self.registry = SiteRegistry(self.profiles)

    def test_default_is_unqualified_and_other_sites_require_explicit_registration(self):
        self.assertEqual(SiteRegistry().accounts(), ('hotels.com',))
        self.assertFalse(SiteRegistry().profile('hotels.com')['qualified'])
        with self.assertRaises(SiteRefused):
            SiteRegistry().profile('shop.example')
        self.assertEqual(self.registry.accounts(), ('hotels.com', 'shop.example'))
        self.profiles[1]['domains'].append('unreviewed.example')
        external = self.registry.profile('shop.example')
        external['driver_config']['login_url'] = 'https://unreviewed.example/login'
        self.assertFalse(self.registry.allows_url('shop.example', 'https://unreviewed.example/'))

    def test_login_mode_defaults_automatic_for_legacy_profiles_and_hotels_owner_is_explicit(self):
        automatic = shop_profile()
        normalized = validate_profile(automatic)
        self.assertEqual(normalized['login_mode'], 'automatic')
        self.assertNotIn('login_mode', automatic)
        legacy_hotels = copy.deepcopy(DEFAULT_HOTELS)
        del legacy_hotels['login_mode']
        self.assertEqual(validate_profile(legacy_hotels)['login_mode'], 'automatic')
        hotel = self.registry.profile('hotels.com')
        self.assertEqual(hotel['login_mode'], 'owner')
        self.assertEqual(hotel['driver_config']['login_url'], 'https://da.hotels.com/login')
        self.assertFalse(hotel['qualified'])
        self.assertEqual(self.registry.profile('shop.example')['login_mode'], 'automatic')

    def test_login_mode_is_strict_trusted_configuration_and_owner_url_is_fixed(self):
        for mode in (None, True, 1, {}, [], 'Owner', 'interactive', 'owner;exec', ''):
            with self.subTest(mode=mode), self.assertRaises(SiteRefused):
                validate_profile(dict(shop_profile(), login_mode=mode))
        owner = shop_profile(); owner['login_mode'] = 'owner'
        self.assertEqual(validate_profile(owner)['login_mode'], 'owner')
        for login_url in (None, 'https://other.example/login', 'http://shop.example/login',
                          'https://user:secret@shop.example/login', 'https://shop.example:9443/login'):
            value = copy.deepcopy(owner)
            if login_url is None: value['driver_config'].pop('login_url')
            else: value['driver_config']['login_url'] = login_url
            with self.assertRaises(SiteRefused): validate_profile(value)

    def test_canonical_accounts_exact_domains_and_https_urls_only(self):
        for account in ['https://hotels.com', 'Hotels.com', '127.0.0.1', 'localhost', 'service.home.arpa',
                        'user@hotels.com', 'hotels.com:443', 'example.com-', '*.hotels.com']:
            with self.subTest(account=account), self.assertRaises(SiteRefused):
                account_name(account)
        for url in ['https://shop.example/', 'https://shop.example/search?q=public']:
            self.assertTrue(self.registry.allows_url('shop.example', url))
        for url in ['http://shop.example/', 'https://shop.example.evil/', 'https://evil.example/',
                    'https://user:password@shop.example/', 'https://shop.example:8443/', 'https://shop.example/{secret}']:
            self.assertFalse(self.registry.allows_url('shop.example', url))

    def test_per_site_scope_and_typed_parameter_schema_are_authoritative(self):
        self.assertEqual(self.registry.operation_scope('shop.example', 'prepare'), 'prepare_purchase')
        with self.assertRaises(SiteRefused):
            self.registry.validate_scope('hotels.com', 'prepare_purchase')
        with self.assertRaises(SiteRefused):
            self.registry.validate_scope('shop.example', 'prepare_booking')
        self.assertEqual(self.registry.validate_parameters('shop.example', 'search', {'query': 'coffee'}), {'query': 'coffee'})
        values = {'destination': 'Aarhus', 'check_in': '2026-11-01', 'check_out': '2026-11-03', 'adults': 2, 'rooms': 1}
        self.registry.validate_parameters('hotels.com', 'search', values)
        for change in [{'adults': True}, {'adults': 0}, {'check_out': '2026-10-31'}, {'check_in': '2026-02-30'},
                       {'destination': 'https://evil.example/'}, {'password': 'synthetic-private'}, {'selector': 'body'}]:
            with self.assertRaises((SiteRefused, ValueError)):
                self.registry.validate_parameters('hotels.com', 'search', dict(values, **change))
        for values in [{}, {'query': ['secret']}, {'query': 'x', 'token': 'synthetic'}, {'query': {'value': 'synthetic'}}]:
            with self.assertRaises(SiteRefused):
                self.registry.validate_parameters('shop.example', 'search', values)

    def test_preparation_cannot_submit_and_profiles_cannot_add_secret_or_script_fields(self):
        changes = []
        for operation in ['prepare', 'read']:
            profile = shop_profile()
            profile['driver_config']['operations'][operation]['submit_selector'] = '#pay'
            changes.append(profile)
        profile = shop_profile(); profile['credential_id'] = 'synthetic-secret-id'; changes.append(profile)
        profile = shop_profile(); profile['driver_config']['password'] = 'synthetic-secret'; changes.append(profile)
        profile = shop_profile(); profile['driver_config']['login_url'] = 'https://other.example/login'; changes.append(profile)
        profile = shop_profile(); profile['driver_config']['network_posts']['https://shop.example/api'] = ['MUTATION']; changes.append(profile)
        profile = shop_profile(); profile['driver_config']['network_posts']['https://shop.example/session?key=secret'] = ['LOGIN']; changes.append(profile)
        profile = shop_profile(); profile['actions'].append('evaluate'); changes.append(profile)
        profile = shop_profile(); profile['operations']['search']['parameters']['password'] = {'type': 'string'}; changes.append(profile)
        for profile in changes:
            with self.assertRaises(SiteRefused) as error:
                validate_profile(profile)
            self.assertNotIn('synthetic-secret', str(error.exception))

    def test_private_registry_file_schema_and_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve(); directory.chmod(0o700)
            path = directory / 'sites.json'
            path.write_text(json.dumps({'schema_version': 1, 'sites': self.profiles})); path.chmod(0o600)
            self.assertEqual(SiteRegistry.from_file(path).accounts(), self.registry.accounts())
            path.chmod(0o644)
            with self.assertRaises(CustodyRefused):
                SiteRegistry.from_file(path)
            path.chmod(0o600)
            path.write_text('{"schema_version":1,"schema_version":1,"sites":[]}')
            with self.assertRaises(SiteRefused):
                SiteRegistry.from_file(path)


class RegisteredGrantsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.registry = SiteRegistry([copy.deepcopy(DEFAULT_HOTELS), shop_profile()])
        self.ledger = AccessGrants(self.base / 'ledger', registry=self.registry, clock=lambda: 1000)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, account, scope='search', key='synthetic-request-0001', actor='travel'):
        return self.ledger.request(actor, {'account': account, 'scope': scope, 'minutes': 15, 'request_key': key})[0]

    def approve(self, item):
        return self.ledger.decide(item['request_id'], item['request_digest'], 'approve', 15,
                                  self.ledger.challenge(item['request_id'], 'approve'))

    def test_actor_account_and_operation_isolation_and_immutable_key_binding(self):
        hotel = self.request('hotels.com')
        shop = self.request('shop.example', 'prepare_purchase', key='synthetic-request-0002')
        with self.assertRaises(GrantRefused):
            self.request('shop.example')
        self.approve(hotel); self.approve(shop)
        self.assertEqual(self.ledger.authorize('travel', shop['request_id'], 'prepare_purchase', account='shop.example')['account'], 'shop.example')
        for actor, reference, scope, account in [('researcher', shop['request_id'], 'prepare_purchase', 'shop.example'),
                                               ('travel', hotel['request_id'], 'search', 'shop.example'),
                                               ('travel', shop['request_id'], 'prepare_booking', 'shop.example')]:
            with self.assertRaises(GrantRefused):
                self.ledger.authorize(actor, reference, scope, account=account)
        with self.assertRaises(GrantRefused):
            self.request('unregistered.example')
        self.assertNotEqual(hotel['request_digest'], shop['request_digest'])

    def test_qualification_alone_never_claims_a_deployed_executor(self):
        profile = shop_profile(); profile['qualified'] = True
        registry = SiteRegistry([profile])
        directory = self.base / 'available'
        ledger = AccessGrants(directory, registry=registry, clock=lambda: 1000)
        item = ledger.request('travel', {'account': 'shop.example', 'scope': 'search', 'minutes': 15,
                                        'request_key': 'synthetic-availability-0001'})[0]
        self.assertFalse(item['account_execution_available'])
        for callback, expected in [(lambda _a, _s: True, True), (lambda _a, _s: 1, False), (lambda _a, _s: False, False)]:
            ledger = AccessGrants(directory, registry=registry, clock=lambda: 1000, execution_available=callback)
            self.assertIs(ledger.status(item['request_id'])['account_execution_available'], expected)
        unqualified = AccessGrants(self.base / 'unqualified', registry=self.registry, clock=lambda: 1000,
                                   execution_available=lambda _a, _s: True)
        value = unqualified.request('travel', {'account': 'shop.example', 'scope': 'search', 'minutes': 15,
                                              'request_key': 'synthetic-availability-0001'})[0]
        self.assertFalse(value['account_execution_available'])

    def test_removed_profile_keeps_history_readable_but_cannot_authorize(self):
        item = self.approve(self.request('shop.example'))
        removed = AccessGrants(self.base / 'ledger', registry=SiteRegistry([]), clock=lambda: 1000)
        retained = removed.status(item['request_id'])
        self.assertEqual(retained['account'], 'shop.example')
        self.assertEqual(retained['status'], 'active')
        self.assertFalse(retained['account_execution_available'])
        with self.assertRaises(GrantRefused):
            removed.authorize('travel', item['request_id'], 'search')

    def test_legacy_migration_preserves_ids_digests_state_expiry_and_review_challenge(self):
        directory = self.base / 'legacy'; directory.mkdir(mode=0o700)
        path = directory / 'access.sqlite'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(descriptor)
        reference = '00000000-0000-4000-8000-000000000001'
        immutable = {'request_id': reference, 'actor': 'travel', 'account': 'hotels.com', 'scope': 'search',
                     'requested_minutes': 15, 'requested_at': 900}
        original_digest, nonce = digest(immutable), 'synthetic-migration-challenge'
        active_id = '00000000-0000-4000-8000-000000000002'
        active_digest = digest(dict(immutable, request_id=active_id, requested_at=800, scope='prepare_booking'))
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE requests(id TEXT PRIMARY KEY,actor TEXT,request_key TEXT,scope TEXT,'
                             'requested_minutes INTEGER,created INTEGER,digest TEXT,status TEXT,approved INTEGER,expires INTEGER,'
                             'last_seen INTEGER,UNIQUE(actor,request_key));'
                             'CREATE TABLE challenges(nonce_hash TEXT PRIMARY KEY,request_id TEXT,digest TEXT,action TEXT,expires INTEGER);')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (reference, 'travel', 'synthetic-legacy-0001', 'search', 15, 900, original_digest, 'pending', None, None, 1000))
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (active_id, 'travel', 'synthetic-legacy-0002', 'prepare_booking', 15, 800, active_digest, 'active', 900, 1800, 1000))
            db.execute('INSERT INTO challenges VALUES(?,?,?,?,?)',
                       (hashlib.sha256(nonce.encode()).hexdigest(), reference, original_digest, 'approve', 1200))
        migrated = AccessGrants(directory, clock=lambda: 1000)
        self.assertEqual(migrated.status(reference)['request_digest'], original_digest)
        self.assertEqual(migrated.status(reference)['account'], 'hotels.com')
        active = migrated.status(active_id)
        self.assertEqual((active['request_digest'], active['status'], active['approved_at'], active['expires_at']),
                         (active_digest, 'active', 900, 1800))
        approved = migrated.decide(reference, original_digest, 'approve', 7, nonce)
        self.assertEqual((approved['request_id'], approved['approved_at'], approved['expires_at']), (reference, 1000, 1420))
        reloaded = AccessGrants(directory, clock=lambda: 1000)
        self.assertEqual(reloaded.status(reference), approved)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT digest,account FROM requests ORDER BY id').fetchall(),
                             [(original_digest, 'hotels.com'), (active_digest, 'hotels.com')])

    def test_generic_review_and_notification_do_not_serialize_private_profiles(self):
        item = self.request('shop.example', 'prepare_purchase')
        message = render(item, 'https://home-core.tail479ad.ts.net:18814', self.registry)
        page = review(item, {'approve': 'synthetic-csrf'}) + listing([item])
        for output in [message, page]:
            self.assertIn('shop.example', output)
            self.assertNotIn('Hotels.com', output)
            self.assertNotIn('password_selector', output)
            self.assertNotIn('/session', output)
        self.assertIn('no order', message)
        self.assertIn('separate approval', page)
        with self.assertRaises(NotificationRefused):
            render(dict(item, password='synthetic-private'), 'https://home-core.tail479ad.ts.net:18814', self.registry)

    def test_connected_owner_mode_describes_private_sign_in_without_claiming_account_session(self):
        profile = copy.deepcopy(DEFAULT_HOTELS); profile['qualified'] = True
        registry = SiteRegistry([profile])
        ledger = AccessGrants(self.base / 'owner-ledger', registry=registry, clock=lambda: 1000,
                              execution_available=lambda *_: True)
        item = ledger.request('travel', {'account': 'hotels.com', 'scope': 'read', 'minutes': 15,
                                        'request_key': 'synthetic-owner-request-0001'})[0]
        self.assertTrue(item['account_execution_available'])
        self.assertNotIn('login_mode', item)
        for output in (render(item, 'https://home-core.tail479ad.ts.net:18814', registry), review(item, {})):
            self.assertIn('private owner sign-in step may be required', output)
            self.assertIn('does not mean the account is signed in', output)
            self.assertNotIn('not connected', output)
            self.assertNotIn('https://da.hotels.com/login', output)
            self.assertNotIn('login_mode', output)


if __name__ == '__main__':
    unittest.main()
