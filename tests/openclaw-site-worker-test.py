#!/usr/bin/env python3
"""Private browser and network-policy fixtures; never use real credentials."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_bitwarden import Login
import openclaw_site_worker as worker
from openclaw_site_drivers import CHALLENGE, MFA_PROMPT, SiteStopped, validated_parameters
from openclaw_site_registry import ACTIONS, DEFAULT_HOTELS, validate_profile

EMAIL, PASSWORD = 'synthetic@example.invalid', 'synthetic-password-never-output'
PARAMETERS = {'query': 'hotel fixture'}


def profile():
    rule = {'query': {'type': 'string', 'minLength': 1, 'maxLength': 64}}
    return {'account': 'shop.example.com', 'label': 'Synthetic shop', 'driver': 'form',
            'domains': ['shop.example.com', 'identity.example.com'],
            'scopes': ['search', 'prepare_purchase', 'read'], 'actions': sorted(ACTIONS), 'qualified': False,
            'operations': {'search': {'scope': 'search', 'parameters': rule, 'required': ['query']},
                           'prepare': {'scope': 'prepare_purchase', 'parameters': rule, 'required': ['query']},
                           'read': {'scope': 'read', 'parameters': {}, 'required': []}},
            'driver_config': {'login_url': 'https://shop.example.com/login',
                              'login': {'username_selector': '#email', 'password_selector': '#password',
                                        'submit_selector': '#signin', 'success_selector': '#signed-in'},
                              'operations': {'search': {'url': 'https://shop.example.com/search',
                                                        'fields': {'query': '#query'},
                                                        'submit_selector': '#search', 'result_selector': '.offer'},
                                             'prepare': {'url': 'https://shop.example.com/prepare',
                                                         'fields': {'query': '#query'}, 'result_selector': '.offer'},
                                             'read': {'url': 'https://shop.example.com/', 'fields': {},
                                                      'result_selector': '.offer'}},
                              'network_posts': {'https://shop.example.com/login': ['LOGIN'],
                                                'https://shop.example.com/search': ['READ']}}}


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('8.8.8.8', 443))]


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def count(self):
        if self.selector == CHALLENGE:
            return int(self.page.challenge)
        if self.selector == MFA_PROMPT:
            return int(self.page.push_challenge)
        if self.selector == '#signed-in':
            return int(self.page.signed_in)
        if self.selector == '.offer':
            return self.page.count
        if self.selector == '#password' and self.page.two_stage and self.page.stage == 0:
            return 0
        return 1

    def wait_for(self, **_kwargs):
        if self.count() < 1:
            raise TimeoutError(PASSWORD)

    def nth(self, _index): return self
    def is_visible(self): return not self.page.challenge_hidden

    def fill(self, value, **_kwargs):
        self.page.actions.append(('fill', self.selector, value))

    def set_checked(self, value, **_kwargs):
        self.page.actions.append(('check', self.selector, value))

    def click(self, **_kwargs):
        self.page.actions.append(('click', self.selector))
        if self.selector == '#continue':
            self.page.stage = 1
        if self.selector == '#signin':
            self.page.signed_in = self.page.authenticate

    def inner_text(self):
        raise AssertionError('Private page text must never be extracted')

    def get_attribute(self, _name):
        raise AssertionError('Private attributes must never be extracted')


class Page:
    def __init__(self):
        self.actions, self.events = [], {}
        self.challenge, self.signed_in, self.authenticate = False, False, True
        self.push_challenge, self.challenge_hidden = False, False
        self.frames = [self]
        self.two_stage, self.stage, self.count = False, 0, 7

    def locator(self, selector):
        return Locator(self, selector)

    def get_by_text(self, value): return Locator(self, value)

    def goto(self, url, **_kwargs):
        self.actions.append(('navigate', url))

    def on(self, event, callback):
        self.events[event] = callback


class Context:
    def __init__(self, page):
        self.page, self.closed, self.events = page, False, {}

    def set_default_timeout(self, _value): pass
    def route(self, pattern, callback): self.route_rule = (pattern, callback)
    def route_web_socket(self, pattern, callback): self.websocket_rule = (pattern, callback)
    def new_page(self): return self.page
    def on(self, event, callback): self.events[event] = callback
    def close(self): self.closed = True


class Engine:
    def __init__(self, page=None):
        self.page = page or Page()
        self.context, self.closed = Context(self.page), False
        self.launch_options, self.context_options = None, None

    def __enter__(self):
        # Native/runtime diagnostics must be silenced even if they contain data.
        os.write(1, PASSWORD.encode()); os.write(2, PASSWORD.encode())
        return SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))

    def __exit__(self, *_args): pass

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return SimpleNamespace(new_context=self.new_context, close=self.close)

    def new_context(self, **kwargs):
        self.context_options = kwargs
        return self.context

    def close(self): self.closed = True


class Route:
    def __init__(self, url, method='GET', replies=None):
        self.request = SimpleNamespace(url=url, method=method, post_data_buffer=b'private-body',
            all_headers=lambda: {'authorization': 'private-auth', 'cookie': 'private-cookie', 'host': 'old-host'})
        self.replies = list(replies or [(200, {})])
        self.calls, self.fulfilled, self.aborted = [], False, False

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        status, headers = self.replies.pop(0)
        return SimpleNamespace(status=status, headers=headers)

    def fulfill(self, **_kwargs): self.fulfilled = True
    def abort(self, _reason): self.aborted = True


class WorkerTests(unittest.TestCase):
    def invoke(self, operation='search', parameters=None, value=None, engine=None, deadline=140):
        self.engine = engine or Engine()
        return worker.run(value or profile(), operation,
                          PARAMETERS if parameters is None else parameters,
                          Login(EMAIL, PASSWORD), deadline, playwright_factory=lambda: self.engine,
                          resolver=public_dns, clock=lambda: 100)

    def test_private_pipe_context_returns_only_status_operation_and_count(self):
        result = self.invoke()
        self.assertEqual(result, {'status': 'ok', 'operation': 'search', 'results_count': 7})
        self.assertNotIn(PASSWORD, json.dumps(result)); self.assertNotIn(EMAIL, json.dumps(result))
        launch, context = self.engine.launch_options, self.engine.context_options
        self.assertIs(launch['chromium_sandbox'], True)
        self.assertEqual(launch['proxy'], {'server': worker.PROXY, 'bypass': ''})
        self.assertFalse(any('debugging-port' in arg for arg in launch['args']))
        self.assertEqual(context['service_workers'], 'block'); self.assertFalse(context['accept_downloads'])
        self.assertEqual(context['permissions'], [])
        self.assertTrue(self.engine.context.closed); self.assertTrue(self.engine.closed)
        self.assertIn(('fill', '#password', PASSWORD), self.engine.page.actions)

    def test_prepare_never_submits_and_read_cannot_accept_fields(self):
        result = self.invoke('prepare')
        self.assertEqual(result['status'], 'ok')
        clicks = [item[1] for item in self.engine.page.actions if item[0] == 'click']
        self.assertEqual(clicks, ['#signin'])
        self.assertEqual(self.invoke('read', {})['status'], 'ok')
        self.assertEqual(self.invoke('read', PARAMETERS)['status'], 'unavailable')
        self.assertEqual(self.invoke('book')['status'], 'unavailable')

    def test_cleanup_failure_discards_success_count_and_diagnostic(self):
        engine = Engine()
        def failed_close():
            raise RuntimeError(PASSWORD)
        engine.close = failed_close
        result = self.invoke(engine=engine)
        self.assertEqual(result, {'status': 'unavailable', 'operation': 'search', 'results_count': None})
        self.assertNotIn(PASSWORD, json.dumps(result))
        engine = Engine(); engine.context.close = failed_close
        self.assertEqual(self.invoke(engine=engine)['status'], 'unavailable')
        self.assertTrue(engine.closed)

    def test_challenge_or_missing_positive_login_marker_require_human(self):
        engine = Engine(); engine.page.challenge = True
        self.assertEqual(self.invoke(engine=engine)['status'], 'human_sign_in_required')
        self.assertFalse(any(item[0] == 'fill' for item in engine.page.actions))
        engine = Engine(); engine.page.authenticate = False
        result = self.invoke(engine=engine)
        self.assertEqual(result, {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None})
        self.assertTrue(engine.closed)

    def test_reviewed_two_stage_login_and_expired_deadline(self):
        value = profile(); value['driver_config']['login']['username_continue_selector'] = '#continue'
        engine = Engine(); engine.page.two_stage = True
        self.assertEqual(self.invoke(value=value, engine=engine)['status'], 'ok')
        self.assertIn(('click', '#continue'), engine.page.actions)
        self.assertEqual(self.invoke(deadline=100)['status'], 'expired')
        self.assertIsNone(self.engine.launch_options)

    def test_invalid_parameters_or_transactions_never_start_a_browser(self):
        for parameters in [dict(PARAMETERS, password=PASSWORD), {'query': 'https://attacker.invalid'},
                           {'query': {'nested': 'value'}}, {'query': 'x' * 1000}]:
            self.assertEqual(self.invoke(parameters=parameters)['status'], 'unavailable')
            self.assertIsNone(self.engine.launch_options)
        value = profile(); value['driver_config']['operations']['prepare']['submit_selector'] = '#pay'
        self.assertEqual(self.invoke('prepare', value=value)['status'], 'unavailable')

    def test_every_request_target_checks_public_dns_and_transaction_paths(self):
        guard = worker.Guard(validate_profile(profile()), 140, resolver=public_dns, clock=lambda: 100)
        guard.phase = 'search'
        guard.allowed('https://shop.example.com/search', 'GET')
        for target in ['http://shop.example.com/', 'https://127.0.0.1/', 'https://shop.example.com.evil/',
                       'https://shop.example.com:444/', 'https://shop.example.com/checkout',
                       'https://shop.example.com/%252fcheckout/complete',
                       'https://shop.example.com/payment', 'https://shop.example.com/reserve',
                       'https://shop.example.com/book', 'https://shop.example.com/confirmation']:
            with self.subTest(target=target):
                with self.assertRaises(SiteStopped): guard.allowed(target, 'GET')
        for address in ['127.0.0.1', '10.77.20.1', '169.254.169.254', '100.110.248.102', '::1', '::ffff:8.8.8.8']:
            guard.resolver = lambda *_a, **_kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', (address, 443))]
            with self.assertRaises(SiteStopped): guard.allowed('https://shop.example.com/', 'GET')

    def test_post_rules_are_phase_bound_and_prepare_can_never_post(self):
        guard = worker.Guard(validate_profile(profile()), 140, resolver=public_dns, clock=lambda: 100)
        guard.allowed('https://shop.example.com/login', 'POST')
        with self.assertRaises(SiteStopped): guard.allowed('https://shop.example.com/search', 'POST')
        guard.phase = 'search'; guard.allowed('https://shop.example.com/search', 'POST')
        with self.assertRaises(SiteStopped): guard.allowed('https://shop.example.com/login', 'POST')
        guard.phase = 'prepare'
        with self.assertRaises(SiteStopped): guard.allowed('https://shop.example.com/search', 'POST')

    def test_redirect_hops_strip_cross_origin_credentials_and_block_unsafe_targets(self):
        guard = worker.Guard(validate_profile(profile()), 140, resolver=public_dns, clock=lambda: 100)
        route = Route('https://shop.example.com/login', replies=[(302, {'location': 'https://identity.example.com/start'}), (200, {})])
        guard.intercept(route)
        self.assertTrue(route.fulfilled)
        self.assertNotIn('authorization', route.calls[1]['headers'])
        self.assertNotIn('cookie', route.calls[1]['headers'])
        self.assertTrue(all(item['max_redirects'] == 0 for item in route.calls))
        for target in ['https://shop.example.com/checkout', 'https://attacker.invalid/', 'http://shop.example.com/']:
            guard.refused = False
            route = Route('https://shop.example.com/login', replies=[(302, {'location': target})])
            guard.intercept(route)
            self.assertTrue(route.aborted); self.assertEqual(len(route.calls), 1)
        guard.refused = False
        route = Route('https://shop.example.com/login', 'POST', [(307, {'location': 'https://identity.example.com/start'})])
        guard.intercept(route)
        self.assertTrue(route.aborted); self.assertEqual(len(route.calls), 1)

    def test_redirect_loop_and_mid_request_expiry_fail_closed(self):
        tick = [100]
        guard = worker.Guard(validate_profile(profile()), 140, resolver=public_dns, clock=lambda: tick[0])
        route = Route('https://shop.example.com/login', replies=[(302, {'location': '/login'})] * 5)
        guard.intercept(route); self.assertTrue(route.aborted); self.assertEqual(len(route.calls), 5)
        guard.refused = False
        route = Route('https://shop.example.com/login')
        fetch = route.fetch
        def expire(**kwargs):
            reply = fetch(**kwargs); tick[0] = 140; return reply
        route.fetch = expire
        guard.intercept(route); self.assertTrue(route.aborted); self.assertFalse(route.fulfilled)

    def test_private_stdin_cli_suppresses_native_diagnostics_and_never_reflects_secrets(self):
        program = '''import importlib.util,sys
sys.path.insert(0,sys.argv[1]+"/scripts")
spec=importlib.util.spec_from_file_location("fixtures",sys.argv[1]+"/tests/openclaw-site-worker-test.py")
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
import openclaw_site_worker as worker
original=worker._run
worker._run=lambda *a,**kw: original(*a,**kw,playwright_factory=lambda:fixtures.Engine(),resolver=fixtures.public_dns)
sys.argv=["worker"]
worker.main()
'''
        envelope = {'profile': profile(), 'operation': 'search', 'parameters': PARAMETERS,
                    'login': {'username': EMAIL, 'password': PASSWORD}, 'timeout_seconds': 40, 'proxy': worker.PROXY}
        child = subprocess.run([sys.executable, '-c', program, str(ROOT)], input=json.dumps(envelope).encode(),
                               capture_output=True, timeout=5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(json.loads(child.stdout), {'status': 'ok', 'operation': 'search', 'results_count': 7})
        self.assertEqual(child.stderr, b'')
        self.assertNotIn(PASSWORD.encode(), child.stdout)
        self.assertNotIn(EMAIL.encode(), child.stdout)
        envelope['password'] = PASSWORD
        child = subprocess.run([sys.executable, '-c', program, str(ROOT)], input=json.dumps(envelope).encode(),
                               capture_output=True, timeout=5)
        self.assertEqual(json.loads(child.stdout)['status'], 'unavailable')
        self.assertNotIn(PASSWORD.encode(), child.stdout + child.stderr)


if __name__ == '__main__':
    unittest.main()
