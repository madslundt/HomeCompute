#!/usr/bin/env python3
"""Synthetic owner-handoff, document-bound fill and readonly network tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_session_worker as worker

SPEC = importlib.util.spec_from_file_location('session_fixture', Path(__file__).with_name('openclaw-site-worker-test.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
LOGIN = {'username': fixtures.EMAIL, 'password': fixtures.PASSWORD}


class Element:
    def __init__(self, page, selector):
        self.page, self.selector, self.document = page, selector, page.url
    def owner_frame(self): return self.page.main_frame
    def is_visible(self):
        if self.page.navigate_during_fill:
            self.page.url = 'https://identity.example.com/foreign'
        return True
    def fill(self, value, **_kwargs):
        if self.document != self.page.url:
            raise RuntimeError(fixtures.PASSWORD)
        self.page.actions.append(('fill', self.selector, value))
    def get_attribute(self, name):
        return self.page.field_attributes.get(self.selector, {}).get(name)


class Page(fixtures.Page):
    def __init__(self):
        super().__init__()
        self.url, self.navigate_during_fill = 'https://shop.example.com/login', False
        self.field_attributes = {'#email': {'type': 'email', 'autocomplete': 'username', 'name': 'email'},
                                 '#password': {'type': 'password', 'autocomplete': 'current-password', 'name': 'password'}}
        page = self
        class Frame:
            @property
            def url(self): return page.url
        self.main_frame = Frame()
        self.pumps = []
    def locator(self, selector):
        locator = super().locator(selector)
        locator.element_handle = lambda **_kwargs: Element(self, selector)
        return locator
    def goto(self, url, **kwargs):
        self.url = url
        super().goto(url, **kwargs)
    def wait_for_timeout(self, milliseconds): self.pumps.append(milliseconds)


class Context(fixtures.Context):
    def __init__(self):
        super().__init__(Page())
        self.pages = [self.page]
    def storage_state(self):
        return {'cookies': [{'value': fixtures.PASSWORD}], 'origins': []}


class Browser:
    def __init__(self): self.contexts, self.options = [], []
    def new_context(self, **kwargs):
        self.options.append(kwargs)
        context = Context(); self.contexts.append(context)
        return context


class Display:
    def __init__(self): self.disabled, self.enabled = 0, 0
    def disable(self): self.disabled += 1
    def enable(self): self.enabled += 1


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.browser, self.display = Browser(), Display()
        self.session = worker.Session(fixtures.profile(), self.browser, self.display, 140,
                                      resolver=fixtures.public_dns, clock=lambda: 100)

    def test_manual_session_requires_owner_ready_before_any_agent_operation(self):
        result = self.session.command({'command': 'run', 'operation': 'search', 'parameters': fixtures.PARAMETERS})
        self.assertEqual(result, {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None})
        self.assertEqual(self.session.page.actions, [])
        self.assertEqual(self.session.command({'command': 'status'}),
                         {'status': 'human_sign_in_required', 'results_count': None})

    def test_owner_diagnostics_are_readonly_manual_only_and_clear_on_ready_or_close(self):
        request = SimpleNamespace(url='https://private-value.cdn.example.com/private?password=private-value',
                                  resource_type='image', failure='net::ERR_HTTP2_PROTOCOL_ERROR private-value')
        self.session.context.events['requestfailed'](request)
        result = self.session.command({'command': 'diagnostics'})
        self.assertEqual(set(result), {'status', 'resources'})
        self.assertEqual(result['resources'][0]['domain'], 'example.com')
        self.assertEqual(result['resources'][0]['error'], 'ERR_HTTP2_PROTOCOL_ERROR')
        self.assertNotIn('private-value', json.dumps(result))
        self.assertEqual(self.session.page.actions, []); self.assertEqual(self.session.page.pumps, [1])
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'ready')
        self.session.context.events['requestfailed'](request)
        self.assertEqual(self.session.command({'command': 'diagnostics'}), {'status': 'ready', 'resources': []})
        self.session.command({'command': 'manual'})
        self.session.context.events['requestfailed'](request)
        self.assertTrue(self.session.health.records)
        self.session.command({'command': 'close'})
        self.assertEqual(self.session.health.records, {})

    def test_inactive_or_invalid_diagnostics_never_return_stale_records(self):
        with patch.object(self.session.page, 'wait_for_timeout', side_effect=RuntimeError(fixtures.PASSWORD)):
            self.assertEqual(self.session.command({'command': 'diagnostics'}), {'status': 'unavailable', 'resources': []})
        self.assertEqual(self.session.command({'command': 'diagnostics', 'url': 'https://attacker.invalid'}),
                         {'status': 'unavailable', 'resources': []})
        self.session.guard.clock = lambda: 140
        self.assertEqual(self.session.command({'command': 'diagnostics'}), {'status': 'expired', 'resources': []})

    def test_fill_is_document_bound_to_exact_registered_login_origin_and_never_submits(self):
        result = self.session.command({'command': 'fill', 'login': LOGIN})
        self.assertEqual(result, {'status': 'filled', 'filled_fields': 2})
        self.assertEqual([action[0] for action in self.session.page.actions], ['fill', 'fill'])
        self.assertNotIn(fixtures.PASSWORD, json.dumps(result))
        for url in ['https://identity.example.com/login', 'https://shop.example.com.evil/login',
                    'http://shop.example.com/login', 'https://shop.example.com:444/login']:
            self.session.page.url = url
            self.session.page.actions.clear()
            result = self.session.command({'command': 'fill', 'login': LOGIN})
            self.assertEqual(result['status'], 'human_sign_in_required')
            self.assertEqual(self.session.page.actions, [])

    def test_owner_fill_can_populate_login_fields_alongside_a_visible_challenge_without_submitting(self):
        self.session.page.challenge = True
        result = self.session.command({'command': 'fill', 'login': LOGIN})
        self.assertEqual(result, {'status': 'filled', 'filled_fields': 2})
        self.assertEqual([action[0] for action in self.session.page.actions], ['fill', 'fill'])
        self.assertTrue(self.session.page.challenge)
        self.assertNotIn(fixtures.PASSWORD, json.dumps(result))

    def test_username_input_without_type_uses_html_text_default_with_login_semantics(self):
        for attributes in [{'autocomplete': 'username'}, {'name': 'email'}]:
            self.session.page.field_attributes['#email'] = attributes
            self.assertEqual(self.session.command({'command': 'fill', 'login': LOGIN}),
                             {'status': 'filled', 'filled_fields': 2})

    def test_navigation_after_origin_check_cannot_retarget_a_credential(self):
        self.session.page.navigate_during_fill = True
        result = self.session.command({'command': 'fill', 'login': LOGIN})
        self.assertEqual(result, {'status': 'unavailable', 'filled_fields': 0})
        self.assertEqual(self.session.page.actions, [])
        self.assertNotIn(fixtures.PASSWORD, json.dumps(result))

    def test_ready_closes_manual_context_popups_and_sockets_and_keeps_storage_private(self):
        original = self.session.context
        popup = Page(); popup.url = 'https://identity.example.com/owner-popup'
        original.pages.append(popup)
        self.session.guard.observe(SimpleNamespace(url='https://identity.example.com/asset'))
        result = self.session.command({'command': 'ready'})
        self.assertEqual(result, {'status': 'ready', 'observed_hosts': ['identity.example.com', 'shop.example.com']})
        self.assertTrue(original.closed); self.assertFalse(self.session.guard.manual)
        self.assertEqual(self.display.disabled, 1)
        self.assertEqual(self.browser.options[-1]['storage_state']['cookies'][0]['value'], fixtures.PASSWORD)
        self.assertEqual(self.session.context.websocket_rule[0], '**/*')
        self.assertNotIn(fixtures.PASSWORD, json.dumps(result))
        self.assertEqual(self.session.command({'command': 'run', 'operation': 'search', 'parameters': fixtures.PARAMETERS}),
                         {'status': 'ok', 'operation': 'search', 'results_count': 7})

    def test_ready_requires_registered_page_and_manual_return_disables_automation_again(self):
        self.session.page.url = 'https://identity.example.com/login'
        # Identity is allowlisted, but its foreign origin is not a fill target.
        self.assertEqual(self.session.command({'command': 'fill', 'login': LOGIN})['status'], 'human_sign_in_required')
        self.session.page.url = 'https://unregistered.example.com/'
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'human_sign_in_required')
        self.session.page.url = 'https://shop.example.com/'
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'ready')
        original = self.session.context
        self.session.guard.refused = True
        self.assertEqual(self.session.command({'command': 'manual'})['status'], 'human_sign_in_required')
        self.assertFalse(original.closed); self.assertIs(self.session.context, original)
        self.assertEqual(self.display.enabled, 1)
        self.assertEqual(self.session.command({'command': 'run', 'operation': 'read', 'parameters': {}})['status'],
                         'human_sign_in_required')

    def test_invalid_commands_are_finite_and_expiry_prevents_filling(self):
        for command in [{'command': 'fill', 'login': LOGIN, 'url': 'https://attacker.invalid'},
                        {'command': 'eval', 'script': fixtures.PASSWORD}, {'command': 'ready', 'url': 'https://attacker.invalid'},
                        {'command': 'run', 'operation': 'book', 'parameters': {}}, {'command': 'fill', 'login': {'password': fixtures.PASSWORD}}]:
            result = self.session.command(command)
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn(fixtures.PASSWORD, json.dumps(result))
        self.session.guard.clock = lambda: 140
        self.assertEqual(self.session.command({'command': 'fill', 'login': LOGIN})['status'], 'expired')
        self.assertEqual(self.session.page.actions, [])

    def test_visible_code_or_push_challenge_blocks_ready_and_password_fill_privately(self):
        self.session.page.field_attributes = {
            '#email': {'type': 'text', 'autocomplete': 'one-time-code'},
            '#password': {'type': 'password', 'name': 'totp'}}
        for kind in ['challenge', 'push_challenge']:
            setattr(self.session.page, kind, True)
            original, page = self.session.context, self.session.page
            result = self.session.command({'command': 'ready'})
            self.assertEqual(result, {'status': 'human_sign_in_required', 'results_count': None})
            self.assertIs(self.session.context, original); self.assertIs(self.session.page, page)
            self.assertEqual(self.display.disabled, 0)
            self.assertEqual(self.session.command({'command': 'fill', 'login': LOGIN}),
                             {'status': 'human_sign_in_required', 'filled_fields': 0})
            self.assertEqual(page.actions, [])
            setattr(self.session.page, kind, False)

    def test_misconfigured_recipe_never_fills_code_seed_consent_or_new_password_fields(self):
        forbidden = [{'type': 'text', 'autocomplete': 'one-time-code'}, {'type': 'password', 'name': 'totp'},
                     {'type': 'password', 'autocomplete': 'new-password'}, {'type': 'text', 'name': 'secretSeed'},
                     {'type': 'checkbox', 'name': 'terms'}, {'type': 'text', 'name': 'verificationCode'}, {}]
        for attributes in forbidden:
            self.session.page.field_attributes = {'#email': attributes, '#password': attributes}
            result = self.session.command({'command': 'fill', 'login': LOGIN})
            self.assertEqual(result, {'status': 'human_sign_in_required', 'filled_fields': 0})
        self.assertEqual(self.session.page.actions, [])

    def test_mid_task_mfa_handoff_preserves_exact_document_until_owner_ready(self):
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'ready')
        context, page = self.session.context, self.session.page
        page.url = 'https://shop.example.com/challenge'; page.challenge = True
        page.actions.clear()
        result = self.session.command({'command': 'run', 'operation': 'search', 'parameters': fixtures.PARAMETERS})
        self.assertEqual(result, {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None})
        self.assertEqual(page.actions, [])
        self.assertEqual(self.session.command({'command': 'manual'})['status'], 'human_sign_in_required')
        self.assertIs(self.session.context, context); self.assertIs(self.session.page, page)
        self.assertEqual(page.url, 'https://shop.example.com/challenge'); self.assertFalse(context.closed)
        page.challenge = False
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'ready')

    def test_ready_navigation_challenge_restores_viewer_and_preserves_challenge(self):
        original = Page.goto
        def challenge(page, url, **kwargs):
            original(page, url, **kwargs); page.challenge = True
        with patch.object(Page, 'goto', challenge):
            result = self.session.command({'command': 'ready'})
        self.assertEqual(result, {'status': 'human_sign_in_required', 'results_count': None})
        self.assertTrue(self.session.guard.manual); self.assertEqual(self.display.enabled, 1)
        self.assertTrue(self.session.page.challenge)

    def test_any_ready_failure_after_viewer_disable_restores_owner_control_except_expiry(self):
        with patch.object(Page, 'goto', side_effect=RuntimeError(fixtures.PASSWORD)):
            result = self.session.command({'command': 'ready'})
        self.assertEqual(result, {'status': 'unavailable', 'results_count': None})
        self.assertTrue(self.session.guard.manual); self.assertEqual(self.display.enabled, 1)
        websocket = SimpleNamespace(connect_to_server=lambda: 'owner-connected', close=lambda: self.fail('Manual websocket closed'))
        self.session.guard.websocket(websocket)
        popup = SimpleNamespace(close=lambda: self.fail('Manual popup closed'))
        self.session.guard.block_extra_page(popup)

    def test_expired_ready_failure_keeps_viewer_disabled(self):
        def expired(*_args, **_kwargs):
            self.session.guard.clock = lambda: 140
            raise RuntimeError(fixtures.PASSWORD)
        with patch.object(Page, 'goto', side_effect=expired):
            result = self.session.command({'command': 'ready'})
        self.assertEqual(result, {'status': 'expired', 'results_count': None})
        self.assertFalse(self.session.guard.manual); self.assertEqual(self.display.enabled, 0)

    def test_hidden_challenge_template_does_not_block_ready(self):
        self.session.page.challenge = True; self.session.page.challenge_hidden = True
        self.assertEqual(self.session.command({'command': 'ready'})['status'], 'ready')


class NetworkTests(unittest.TestCase):
    def guard(self):
        guard = worker.SessionGuard(fixtures.profile(), 140, resolver=fixtures.public_dns, clock=lambda: 100)
        guard.freeze(); guard.phase = 'search'
        return guard

    @unittest.skipUnless(importlib.util.find_spec('graphql'), 'requires pinned graphql-core dependency')
    def test_actual_ast_accepts_queries_and_refuses_mutations_subscriptions_and_hash_only(self):
        def accepted(value): return worker.pure_query(json.dumps(value).encode())
        for query in ['query Hotels { search { id } }', '{ hotels { id } }',
                      'query Hotels { hotels { ...Result } } fragment Result on Hotel { id }',
                      'query Hotels($text:String){search(text:$text){id}}']:
            self.assertTrue(accepted({'query': query}))
        for query in ['mutation Book { reserve { id } }', 'subscription Watch { hotels { id } }',
                      'query Read { hotels { id } } mutation Hidden { pay { id } }', 'query Broken {',
                      'schema { query: X }', 'query A{hotels{id}} query B{hotels{id}}']:
            self.assertFalse(accepted({'query': query}))
        self.assertFalse(accepted({'extensions': {'persistedQuery': {'sha256Hash': 'a' * 64}}}))
        self.assertFalse(accepted([{'query': '{hotels{id}}'}, {'query': 'mutation{pay{id}}'}]))
        self.assertFalse(worker.pure_query(b'{"query":"{hotels{id}}","query":"mutation{pay{id}}"}'))
        self.assertFalse(accepted({'query': '{hotels{id}}', 'operationName': 'Unknown'}))

    @unittest.skipUnless(importlib.util.find_spec('graphql'), 'requires pinned graphql-core dependency')
    def test_readonly_query_posts_pass_and_unknown_writes_or_transactions_do_not_poison_reads(self):
        guard = self.guard()
        route = fixtures.Route('https://shop.example.com/graphql', 'POST')
        route.request.post_data_buffer = b'{"query":"query Hotels { hotels { id } }"}'
        guard.intercept(route); self.assertTrue(route.fulfilled)
        for url, method, body in [('https://shop.example.com/graphql', 'POST', b'{"query":"mutation{pay{id}}"}'),
                                  ('https://shop.example.com/graphql', 'POST', b'{"extensions":{}}'),
                                  ('https://shop.example.com/telemetry', 'POST', b'{}'),
                                  ('https://shop.example.com/checkout', 'GET', b''),
                                  ('https://unregistered.example.com/', 'GET', b'')]:
            route = fixtures.Route(url, method); route.request.post_data_buffer = body
            guard.intercept(route)
            self.assertTrue(route.aborted); self.assertFalse(guard.refused)
            guard.step()
        with self.assertRaises(Exception):
            guard.allowed('https://shop.example.com/graphql?query=mutation%7Bpay%7Bid%7D%7D', 'GET')

    def test_manual_phase_allows_only_public_hostname_https_via_proxy_and_bounds_observations(self):
        guard = self.guard(); guard.manual = True
        for url in ['http://shop.example.com/', 'https://127.0.0.1/', 'https://user:pass@shop.example.com/']:
            route = fixtures.Route(url); route.continued = False
            route.continue_ = lambda: setattr(route, 'continued', True)
            guard.intercept(route); self.assertTrue(route.aborted); self.assertFalse(route.continued)
        for i in range(100):
            guard.observe(SimpleNamespace(url=f'https://h{i}.example.com/'))
        self.assertEqual(len(guard.observed), 64)


class PrivatePipeTests(unittest.TestCase):
    def test_process_native_diagnostics_and_private_exceptions_never_reach_output(self):
        program = '''import os,sys
sys.path.insert(0,sys.argv[1]+"/scripts")
import openclaw_site_session_worker as worker
def serve(emit):
    os.write(1,b"synthetic-password-never-output")
    os.write(2,b"synthetic-password-never-output")
    emit({"status":"human_sign_in_required","results_count":None})
    raise RuntimeError("synthetic-password-never-output")
worker.serve=serve
worker.main()
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stderr, b'')
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], [
            {'status': 'human_sign_in_required', 'results_count': None},
            {'status': 'unavailable', 'results_count': None}])
        self.assertNotIn(fixtures.PASSWORD.encode(), result.stdout)

    def test_malformed_initial_envelope_returns_only_fixed_failure_without_starting_display(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/openclaw_site_session_worker.py')],
            input=b'{"profile":"synthetic-password-never-output"}\n', capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout), {'status': 'unavailable', 'results_count': None,
                                                    'stage': 'validate', 'exception': 'invalid_input'})

    def test_initial_diagnostics_are_whitelisted_stage_and_exception_codes_only(self):
        for error in [PermissionError(fixtures.PASSWORD), ValueError(fixtures.PASSWORD),
                      RuntimeError(fixtures.PASSWORD), type(fixtures.PASSWORD, (Exception,), {})(fixtures.PASSWORD)]:
            diagnostic = worker.InitialFailure(fixtures.PASSWORD, error)
            self.assertEqual(diagnostic.stage, 'validate')
            self.assertIn(diagnostic.code, {'permission', 'invalid_input', 'unknown'})
            self.assertNotIn(fixtures.PASSWORD, str(diagnostic))


if __name__ == '__main__':
    unittest.main()
