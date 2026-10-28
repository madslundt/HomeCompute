#!/usr/bin/env python3
"""Optional real Chromium pipe/DOM test with entirely synthetic HTTP responses.

Run with HOMECOMPUTE_REAL_PLAYWRIGHT=1 in the pinned worker image, no network,
no real secrets or shared browser profile. Route.fetch is replaced by a fixture
transport, while worker guards, browser actions, cookies and DOM remain real.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_worker as worker
import openclaw_site_session_worker as session_worker
from openclaw_bitwarden import Login

SPEC = importlib.util.spec_from_file_location('site_worker_fixtures', Path(__file__).with_name('openclaw-site-worker-test.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
COOKIE = 'synthetic-http-only-session-never-output'
STORAGE = 'synthetic-local-storage-never-output'


class FixtureTransport:
    def __init__(self, challenge=False):
        self.challenge, self.calls = challenge, []
        self.saw_private_login, self.saw_private_cookie = False, False

    def fetch(self, original_request, options):
        url, method = options['url'], options['method']
        path = urlsplit(url).path
        self.calls.append((path, method))
        if path == '/login':
            return SimpleNamespace(status=302, headers={'location': '/login-page'}, body=b'')
        if path == '/login-page':
            content = ('<input autocomplete="one-time-code">' if self.challenge else '''
              <form method="POST" action="/session">
              <input id="email" name="email" type="email">
              <input id="password" name="password" type="password" autocomplete="current-password">
              <button id="signin" type="submit">Sign in</button></form>''')
        elif path == '/session':
            body = options.get('post_data', b'')
            self.saw_private_login = method == 'POST' and fixtures.PASSWORD.encode() in body
            return SimpleNamespace(status=303, headers={'location': '/welcome'}, body=b'')
        elif path == '/search':
            content = '''<form method="GET" action="/results"><input id="query" name="query">
                       <button id="search" type="submit">Search</button></form>'''
        elif path in {'/prepare', '/results', '/welcome', '/'}:
            private = original_request.all_headers().get('cookie', '')
            self.saw_private_cookie |= COOKIE in private
            content = '<div id="signed-in">Signed in</div><input id="query">' + ''.join(
                '<div class="offer">' + fixtures.PASSWORD + COOKIE + STORAGE + '</div>' for _ in range(7))
            content += ('<form method="POST" action="/checkout"><button id="pay" type="submit">Pay</button></form>'
                        '<script>localStorage.setItem("fixture_private","' + STORAGE + '");</script>')
        else:
            return SimpleNamespace(status=404, headers={}, body=b'')
        return SimpleNamespace(status=200, headers={'content-type': 'text/html; charset=utf-8',
            'set-cookie': 'fixture_session=' + COOKIE + '; Path=/; Secure; HttpOnly'},
            body=('<!doctype html><html><body>' + content + '</body></html>').encode())

    def route(self, real_route):
        transport = self
        class Route:
            request = real_route.request
            def fetch(self, **options):
                return transport.fetch(real_route.request, options)
            def fulfill(self, *, response):
                real_route.fulfill(status=response.status, headers=response.headers, body=response.body)
            def abort(self, reason):
                real_route.abort(reason)
        return Route()


@unittest.skipUnless(os.environ.get('HOMECOMPUTE_REAL_PLAYWRIGHT') == '1',
                     'run explicitly in the pinned isolated Playwright worker image')
class RealBrowserTests(unittest.TestCase):
    def invoke(self, operation='search', challenge=False):
        value = fixtures.profile()
        value['driver_config']['network_posts']['https://shop.example.com/session'] = ['LOGIN']
        transport = FixtureTransport(challenge)
        original = worker.Guard.intercept
        def intercepted(guard, route):
            return original(guard, transport.route(route))
        with patch.object(worker.Guard, 'intercept', intercepted):
            result = worker.run(value, operation, fixtures.PARAMETERS,
                Login(fixtures.EMAIL, fixtures.PASSWORD), time.monotonic() + 30, resolver=fixtures.public_dns)
        return result, transport

    def test_real_pipe_login_redirects_cookie_session_and_count_only_projection(self):
        result, transport = self.invoke()
        self.assertEqual(result, {'status': 'ok', 'operation': 'search', 'results_count': 7})
        self.assertTrue(transport.saw_private_login)
        self.assertTrue(transport.saw_private_cookie)
        self.assertNotIn(fixtures.PASSWORD, str(result))
        self.assertNotIn(COOKIE, str(result)); self.assertNotIn(STORAGE, str(result))
        self.assertNotIn(('/checkout', 'POST'), transport.calls)

    def test_real_prepare_never_submits_checkout(self):
        result, transport = self.invoke('prepare')
        self.assertEqual(result, {'status': 'ok', 'operation': 'prepare', 'results_count': 7})
        self.assertNotIn(('/checkout', 'POST'), transport.calls)

    def test_real_one_time_code_requires_human_before_any_password_submission(self):
        result, transport = self.invoke(challenge=True)
        self.assertEqual(result, {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None})
        self.assertFalse(transport.saw_private_login)
        self.assertNotIn(('/session', 'POST'), transport.calls)

    def test_private_session_mfa_preserves_live_challenge_and_requires_owner_resume(self):
        from playwright.sync_api import sync_playwright
        display = SimpleNamespace(disable=lambda: None, enable=lambda: None)
        # Synthetic pages only; no network or real credential provider.
        def fixture_route(_guard, route):
            route.fulfill(status=200, content_type='text/html', body='<h1>Fixture account</h1>')
        with sync_playwright() as runtime, patch.object(session_worker.SessionGuard, 'intercept', fixture_route):
            browser = runtime.chromium.launch(headless=True, chromium_sandbox=True)
            session = session_worker.Session(fixtures.profile(), browser, display, time.monotonic() + 30,
                                            resolver=fixtures.public_dns)
            session.page.goto('https://shop.example.com/login')
            challenge = '<input autocomplete="one-time-code" value="synthetic-mfa-code">'
            session.page.set_content(challenge)
            self.assertEqual(session.command({'command': 'ready'})['status'], 'human_sign_in_required')
            session.page.set_content('<h1>Fixture account</h1>')
            self.assertEqual(session.command({'command': 'ready'})['status'], 'ready')
            page, context = session.page, session.context
            page.set_content(challenge)
            result = session.command({'command': 'run', 'operation': 'search', 'parameters': fixtures.PARAMETERS})
            self.assertEqual(result, {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None})
            self.assertNotIn('synthetic-mfa-code', str(result))
            self.assertEqual(session.command({'command': 'manual'})['status'], 'human_sign_in_required')
            self.assertIs(session.page, page); self.assertIs(session.context, context)
            self.assertEqual(page.locator('input').input_value(), 'synthetic-mfa-code')
            page.set_content('<h1>Fixture account</h1>')
            self.assertEqual(session.command({'command': 'run', 'operation': 'read', 'parameters': {}})['status'],
                             'human_sign_in_required')
            self.assertEqual(session.command({'command': 'ready'})['status'], 'ready')
            browser.close()

    def test_owner_fill_with_captcha_terms_and_code_never_clicks_or_changes_challenges(self):
        from playwright.sync_api import sync_playwright
        display = SimpleNamespace(disable=lambda: None, enable=lambda: None)
        def fixture_route(_guard, route):
            route.fulfill(status=200, content_type='text/html', body='<h1>Synthetic account</h1>')
        launch = {'headless': True, 'chromium_sandbox': True,
                  'args': ['--disable-background-networking', '--disable-component-update', '--disable-quic']}
        if os.environ.get('HOMECOMPUTE_TEST_CHROMIUM_EXECUTABLE'):
            launch['executable_path'] = os.environ['HOMECOMPUTE_TEST_CHROMIUM_EXECUTABLE']
        with sync_playwright() as runtime, patch.object(session_worker.SessionGuard, 'intercept', fixture_route):
            browser = runtime.chromium.launch(**launch)
            try:
                session = session_worker.Session(fixtures.profile(), browser, display, time.monotonic() + 45,
                                                resolver=fixtures.public_dns)
                session.page.goto('https://shop.example.com/login')
                # Public Hotels sign-in is an email + Continue screen with
                # consent text. This fixture adds explicit visible challenges.
                email = '<input id="email" name="email" type="email" autocomplete="username">'
                password = '<input id="password" name="password" type="password" autocomplete="current-password">'
                challenge = ('<iframe src="https://identity.example.com/captcha" width="300" height="100"></iframe>'
                             '<input type="checkbox" name="terms"><span>Accept terms</span>'
                             '<button type="submit" onclick="this.dataset.clicked=\'yes\'">Continue</button>')
                session.page.set_content(email + challenge)
                login = {'username': fixtures.EMAIL, 'password': fixtures.PASSWORD}
                self.assertEqual(session.command({'command': 'fill', 'login': login}), {'status': 'filled', 'filled_fields': 1})
                self.assertEqual(session.page.locator('#email').input_value(), fixtures.EMAIL)
                self.assertFalse(session.page.locator('[name="terms"]').is_checked())
                self.assertIsNone(session.page.locator('button').get_attribute('data-clicked'))
                session.page.set_content('<input id="email" name="email" autocomplete="username">' + challenge)
                self.assertEqual(session.command({'command': 'fill', 'login': login}), {'status': 'filled', 'filled_fields': 1})
                self.assertEqual(session.page.locator('#email').input_value(), fixtures.EMAIL)
                session.page.set_content(email + password + challenge +
                    '<input id="otp" autocomplete="one-time-code" value="synthetic-code-never-output">')
                result = session.command({'command': 'fill', 'login': login})
                self.assertEqual(result, {'status': 'filled', 'filled_fields': 2})
                self.assertEqual(session.page.locator('#password').input_value(), fixtures.PASSWORD)
                self.assertEqual(session.page.locator('#otp').input_value(), 'synthetic-code-never-output')
                self.assertNotIn(fixtures.PASSWORD, str(result))
                self.assertEqual(session.command({'command': 'ready'})['status'], 'human_sign_in_required')
                session.page.set_content('<input id="email" type="text" autocomplete="one-time-code" value="synthetic-otp">'
                                         '<input id="password" type="password" name="totp" value="synthetic-otp">')
                self.assertEqual(session.command({'command': 'fill', 'login': login}),
                                 {'status': 'human_sign_in_required', 'filled_fields': 0})
                self.assertEqual(session.page.locator('#email').input_value(), 'synthetic-otp')
                self.assertEqual(session.page.locator('#password').input_value(), 'synthetic-otp')
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
