#!/usr/bin/env python3
"""Synthetic session leases, concurrent owner transitions and container cleanup."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import os
from pathlib import Path
import sys
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_sessions as sessions_module
from openclaw_site_sessions import PrivateBrowser, SiteSessions
from openclaw_site_registry import DEFAULT_HOTELS, SiteRegistry
from openclaw_site_executor import SiteExecutionRefused


class ControlledPool:
    def __init__(self, **_kwargs):
        self.pending, self.lock = [], threading.Lock()
    def submit(self, function, *args):
        with self.lock: self.pending.append((function, args))
    def take(self):
        with self.lock: return self.pending.pop(0)
    def drain(self):
        while self.pending:
            function, args = self.take(); function(*args)
    def shutdown(self, **_kwargs): pass


class FakeBrowser:
    instances = []
    start_hook = None
    send_hook = None
    def __init__(self, directory, image, profile, expires, slot, guard):
        self.directory, self.image, self.profile, self.expires, self.slot = directory, image, profile, expires, slot
        self.guard, self.closed, self.ready = guard, threading.Event(), False
        self.events, self.lock = [], threading.RLock()
        self.browser_ip = '172.30.192.' + str(20 + slot)
        self.stop_failure = False
        self.instances.append(self)
    def start(self, request_id):
        self.events.append(('start', request_id))
        if type(self).start_hook: type(self).start_hook(self)
        return {'status': 'human_sign_in_required', 'results_count': None}
    def policy(self, allow_public, hosts):
        self.events.append(('policy', allow_public, tuple(hosts)))
    def send(self, value, **_kwargs):
        command = value['command']
        self.events.append(('command', command))
        if type(self).send_hook: type(self).send_hook(self, command)
        if command == 'ready':
            return {'status': 'ready', 'observed_hosts': ['da.hotels.com']}
        if command == 'manual':
            self.events.append(('ack', 'manual'))
            return {'status': 'human_sign_in_required', 'results_count': None}
        if command == 'fill': return {'status': 'filled', 'filled_fields': 2}
        return {'status': 'ok', 'operation': value['operation'], 'results_count': 2}
    def stop(self):
        self.closed.set(); self.ready = False
        self.events.extend([('remove', 'browser'), ('remove', 'proxy')])
        if self.stop_failure: raise SiteExecutionRefused('Synthetic cleanup unavailable')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        FakeBrowser.instances, FakeBrowser.start_hook, FakeBrowser.send_hook = [], None, None
        self.pool = ControlledPool()
        now = int(time.time())
        self.grants = {}
        self.reference = str(uuid4())
        self.grants[self.reference] = {'request_id': self.reference, 'actor': 'openclaw', 'account': 'hotels.com',
            'scope': 'search', 'status': 'active', 'approved_at': now - 1, 'expires_at': now + 600,
            'request_digest': 'a' * 64, 'transaction_execution_available': False}
        profile = copy.deepcopy(DEFAULT_HOTELS); profile['qualified'] = True; profile['login_mode'] = 'owner'
        self.registry = SiteRegistry([profile])
        self.real_watch = SiteSessions._watch
        self.patches = [patch.object(sessions_module, 'PrivateBrowser', FakeBrowser),
                        patch.object(sessions_module, 'ThreadPoolExecutor', return_value=self.pool),
                        patch.object(SiteSessions, '_watch', lambda _self: None)]
        for context in self.patches: context.start(); self.addCleanup(context.stop)
        self.sessions = SiteSessions(Path(self.directory.name).resolve() / 'sessions', 'sha256:' + 'a' * 64,
            self.registry, lambda actor, ref: copy.deepcopy(self.grants[ref]),
            lambda _account: SimpleNamespace(username='synthetic-user', password='synthetic-password'))
        self.addCleanup(self.sessions.stopping.set)

    def started(self, reference=None):
        reference = reference or self.reference
        self.sessions.start(reference); self.pool.drain()
        return self.sessions.entries[reference]

    def test_public_state_has_exact_finite_fields_without_session_material(self):
        self.assertEqual(self.sessions.state(self.reference), {'status': 'idle', 'account': 'hotels.com', 'request_id': self.reference})
        self.started()
        state = self.sessions.state(self.reference)
        self.assertEqual(set(state), {'status', 'account', 'request_id'})
        self.assertEqual(state['status'], 'awaiting_owner')
        self.assertNotIn('synthetic-password', json.dumps(state))

    def test_owner_fill_returns_only_finite_count_without_expanding_public_state(self):
        entry = self.started()
        for count in (0, 1, 2):
            with patch.object(entry['browser'], 'send', return_value={
                    'status': 'filled' if count else 'human_sign_in_required', 'filled_fields': count}):
                receipt = self.sessions.fill(self.reference)
            self.assertEqual(receipt, {'status': 'awaiting_owner', 'account': 'hotels.com',
                                      'request_id': self.reference, 'filled_fields': count})
            self.assertEqual(set(self.sessions.state(self.reference)), {'status', 'account', 'request_id'})
            self.assertNotIn('synthetic-password', json.dumps(receipt))

    def test_operator_diagnostics_are_sanitized_and_never_expand_public_state(self):
        entry = self.started()
        report = {'status': 'human_sign_in_required', 'resources': []}
        with patch.object(entry['browser'], 'send', return_value=report) as send:
            self.assertEqual(self.sessions.diagnostics(self.reference), report)
            send.assert_called_once_with({'command': 'diagnostics'})
        self.assertEqual(set(self.sessions.state(self.reference)), {'status', 'account', 'request_id'})
        with patch.object(entry['browser'], 'send', return_value={**report, 'cookies': 'synthetic-private'}):
            with self.assertRaises(ValueError): self.sessions.diagnostics(self.reference)
        self.sessions.ready(self.reference)
        with patch.object(entry['browser'], 'send') as send:
            with self.assertRaises(SiteExecutionRefused): self.sessions.diagnostics(self.reference)
            send.assert_not_called()

    def test_revocation_during_diagnostic_collection_suppresses_the_result(self):
        entry = self.started()
        def revoked(_value):
            self.grants[self.reference]['status'] = 'revoked'
            return {'status': 'human_sign_in_required', 'resources': []}
        with patch.object(entry['browser'], 'send', side_effect=revoked):
            with self.assertRaises(SiteExecutionRefused): self.sessions.diagnostics(self.reference)

    def test_concurrent_start_reserves_one_entry_one_browser_and_one_slot(self):
        barrier = threading.Barrier(16)
        def start(_index):
            barrier.wait(timeout=2)
            return self.sessions.start(self.reference)
        with ThreadPoolExecutor(max_workers=16) as threads:
            states = list(threads.map(start, range(16)))
        self.assertEqual(len(FakeBrowser.instances), 1)
        self.assertEqual(len(self.sessions.entries), 1)
        self.assertEqual(len(self.sessions.slots), 1)
        self.assertTrue(all(state['status'] == 'starting' for state in states))
        self.assertEqual(len(self.pool.pending), 1)

    def test_close_during_start_cannot_resurrect_and_restart_gets_new_lease(self):
        entered, release, errors = threading.Event(), threading.Event(), []
        def starting(_browser):
            entered.set()
            if not release.wait(3): raise TimeoutError('Synthetic start blocked')
        FakeBrowser.start_hook = starting
        self.sessions.start(self.reference)
        original = self.sessions.entries[self.reference]
        function, args = self.pool.take()
        thread = threading.Thread(target=function, args=args); thread.start()
        self.assertTrue(entered.wait(2))
        def close():
            try: self.sessions.close(self.reference)
            except Exception as error: errors.append(type(error).__name__)
        closer = threading.Thread(target=close); closer.start()
        try:
            self.assertTrue(original['browser'].closed.wait(2), 'Close must interrupt startup without waiting for its reply')
        finally:
            release.set(); thread.join(3); closer.join(3)
        self.assertFalse(thread.is_alive()); self.assertFalse(closer.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(self.sessions.state(self.reference)['status'], 'closed')
        self.assertEqual(self.sessions.slots, set())
        FakeBrowser.start_hook = None
        replacement = self.started()
        self.assertIsNot(replacement, original)
        self.assertNotEqual(replacement['browser'].directory, original['browser'].directory)
        self.assertEqual(replacement['status'], 'awaiting_owner')

    def test_old_terminal_entry_cannot_release_slot_reused_by_new_grant(self):
        old = self.started(); self.sessions.close(self.reference)
        self.assertTrue(old['slot_released'])
        other = str(uuid4()); self.grants[other] = dict(self.grants[self.reference], request_id=other)
        replacement = self.started(other)
        self.assertEqual(old['slot'], replacement['slot'])
        self.grants[self.reference]['status'] = 'expired'
        self.grants[self.reference]['expires_at'] = int(time.time()) - 1
        for _ in range(3): self.sessions.state(self.reference)
        self.assertEqual(self.sessions.slots, {replacement['slot']})
        self.assertFalse(replacement['browser'].closed.is_set())

    def test_take_control_acknowledges_manual_before_widening_egress(self):
        entry = self.started(); self.sessions.ready(self.reference)
        self.assertTrue(self.sessions.is_ready(self.reference))
        browser = entry['browser']; browser.events.clear()
        self.sessions.start(self.reference)
        self.assertFalse(self.sessions.is_ready(self.reference))
        self.assertNotIn(('policy', True, ()), browser.events, 'Manual transition should run asynchronously')
        self.pool.drain()
        self.assertLess(browser.events.index(('ack', 'manual')), browser.events.index(('policy', True, ())))
        self.assertEqual(self.sessions.state(self.reference)['status'], 'awaiting_owner')

    def test_mid_task_mfa_pauses_queued_operations_until_private_owner_handoff(self):
        entry = self.started(); self.sessions.ready(self.reference)
        browser = entry['browser']; original = browser.send
        entered, release = threading.Event(), threading.Event()
        calls, results = [], []
        def challenged(value, **kwargs):
            if value['command'] != 'run': return original(value, **kwargs)
            calls.append('run'); entered.set()
            if not release.wait(3): raise TimeoutError('Synthetic challenge blocked')
            return {'status': 'human_sign_in_required', 'operation': 'search', 'results_count': None}
        def run():
            results.append(self.sessions.run('openclaw', entry['initial'], 'search', {},
                                            time.monotonic() + 30, lambda: True))
        with patch.object(browser, 'send', challenged):
            first = threading.Thread(target=run); first.start()
            self.assertTrue(entered.wait(2))
            second = threading.Thread(target=run); second.start()
            release.set(); first.join(3); second.join(3)
            self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive())
            self.assertEqual(calls, ['run'])
            self.assertFalse(self.sessions.is_ready(self.reference))
            self.assertEqual(entry['status'], 'starting')
            self.assertTrue(all(result == {'status': 'human_sign_in_required', 'operation': 'search',
                                          'results_count': None} for result in results))
            self.pool.drain()
        self.assertEqual(entry['status'], 'awaiting_owner')
        self.assertFalse(self.sessions.is_ready(self.reference))
        self.assertLess(browser.events.index(('ack', 'manual')), browser.events.index(('policy', True, ())))
        self.sessions.ready(self.reference)
        self.assertTrue(self.sessions.is_ready(self.reference))

    def test_revocation_during_mfa_handoff_does_not_widen_egress(self):
        entry = self.started(); self.sessions.ready(self.reference)
        browser = entry['browser']; browser.events.clear()
        self.sessions.start(self.reference)
        def revoke(_browser, command):
            if command == 'manual': self.grants[self.reference]['status'] = 'revoked'
        FakeBrowser.send_hook = revoke
        self.pool.drain()
        self.assertTrue(browser.closed.is_set())
        self.assertFalse(self.sessions.is_ready(self.reference))
        self.assertNotIn(('policy', True, ()), browser.events)

    def test_close_during_ready_or_manual_reply_cannot_restore_closed_session(self):
        for command in ['ready', 'manual']:
            entry = self.started()
            if command == 'manual': self.sessions.ready(self.reference)
            entered, release, errors = threading.Event(), threading.Event(), []
            def delayed(_browser, actual):
                if actual == command:
                    entered.set()
                    if not release.wait(3): raise TimeoutError('Synthetic reply blocked')
            FakeBrowser.send_hook = delayed
            def transition():
                try:
                    if command == 'ready': self.sessions.ready(self.reference)
                    else: self.sessions.start(self.reference); self.pool.drain()
                except SiteExecutionRefused: errors.append('refused')
            thread = threading.Thread(target=transition); thread.start()
            self.assertTrue(entered.wait(2))
            self.sessions.close(self.reference)
            release.set(); thread.join(3)
            FakeBrowser.send_hook = None
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.sessions.state(self.reference)['status'], 'closed')
            self.assertFalse(self.sessions.is_ready(self.reference))
            if command == 'manual': self.assertNotIn(('policy', True, ()), entry['browser'].events)

    def test_revocation_and_expiry_watch_stop_both_container_roles(self):
        for revoked in [True, False]:
            reference = str(uuid4())
            self.grants[reference] = dict(self.grants[self.reference], request_id=reference)
            entry = self.started(reference)
            if revoked: self.grants[reference]['status'] = 'revoked'
            else: self.grants[reference]['expires_at'] = int(time.time()) - 1
            class OneStep:
                count = 0
                def wait(self, _timeout):
                    self.count += 1; return self.count > 1
            previous = self.sessions.stopping; self.sessions.stopping = OneStep()
            try: self.real_watch(self.sessions)
            finally: self.sessions.stopping = previous
            self.assertTrue(entry['browser'].closed.is_set())
            self.assertIn(('remove', 'browser'), entry['browser'].events)
            self.assertIn(('remove', 'proxy'), entry['browser'].events)
            self.assertNotIn(entry['slot'], self.sessions.slots)

    def test_unverified_cleanup_retains_capacity_reservation(self):
        entry = self.started(); entry['browser'].stop_failure = True
        self.sessions.close(self.reference)
        self.assertIn(entry['slot'], self.sessions.slots)
        self.assertFalse(entry['slot_released'])
        self.assertEqual(self.sessions.state(self.reference)['status'], 'unavailable')

    def test_owner_mode_and_qualified_profile_are_required_before_allocation(self):
        for changes in [{'qualified': False}, {'login_mode': 'automatic'}]:
            profile = copy.deepcopy(DEFAULT_HOTELS); profile['qualified'] = True; profile.update(changes)
            self.sessions.registry = SiteRegistry([profile])
            with self.assertRaises(SiteExecutionRefused): self.sessions.start(self.reference)
        self.assertEqual(FakeBrowser.instances, [])


class PrivateBrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.browser = PrivateBrowser(Path(self.directory.name) / 'lease', 'sha256:' + 'a' * 64,
                                      DEFAULT_HOTELS, int(time.time()) + 600, 0, lambda: True)

    def test_policy_is_atomic_public_hostname_only_and_independent_between_leases(self):
        self.browser.policy(True, [])
        original = self.browser.directory / 'policy/policy.json'
        other = PrivateBrowser(Path(self.directory.name) / 'other-lease', self.browser.image,
                               DEFAULT_HOTELS, self.browser.expires, 0, lambda: True)
        other.policy(False, ['da.hotels.com'])
        self.assertEqual(json.loads(original.read_text()), {'allow_public': True, 'hosts': []})
        self.assertEqual(json.loads((other.directory / 'policy/policy.json').read_text()),
                         {'allow_public': False, 'hosts': ['da.hotels.com']})
        self.assertFalse((original.parent / 'next.json').exists())
        with self.assertRaises(Exception): self.browser.policy(False, ['127.0.0.1'])

    def test_restrictive_service_umask_keeps_mounted_policy_readable_to_container_user(self):
        previous = os.umask(0o077)
        try:
            self.browser.policy(True, [])
            directory = self.browser.directory / 'policy'
            path = directory / 'policy.json'
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            path.chmod(0o600)
            self.browser.policy(False, ['da.hotels.com'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(json.loads(path.read_text()), {'allow_public': False, 'hosts': ['da.hotels.com']})
            self.assertFalse((directory / 'next.json').exists())
        finally:
            os.umask(previous)

    def test_closed_session_policy_cannot_reopen_public_or_registered_host_access(self):
        self.browser.policy(True, [])
        self.browser.stop()
        self.assertTrue(self.browser.closed.is_set()); self.assertFalse(self.browser.ready)
        for allow_public, hosts in [(True, []), (True, ['da.hotels.com']), (False, ['da.hotels.com'])]:
            self.browser.policy(allow_public, hosts)
            path = self.browser.directory / 'policy/policy.json'
            self.assertEqual(json.loads(path.read_text()), {'allow_public': False, 'hosts': []})

    def test_client_exit_race_never_skips_removing_browser_and_proxy(self):
        self.browser.ids = ['a' * 64, 'b' * 64]
        self.browser.process = SimpleNamespace(poll=lambda: None, pid=123456, wait=lambda **_kw: None,
            stdin=io.BytesIO(), stdout=io.BytesIO())
        with patch.object(sessions_module.os, 'killpg', side_effect=ProcessLookupError), \
             patch.object(sessions_module, 'docker', return_value='') as docker:
            self.browser.stop()
        self.assertEqual([call.args[0] for call in docker.call_args_list],
                         [['rm', '--force', 'b' * 64], ['rm', '--force', 'a' * 64]])
        self.assertTrue(self.browser.closed.is_set()); self.assertFalse(self.browser.ready)

    def test_uncertain_first_removal_still_attempts_other_container(self):
        self.browser.ids = ['a' * 64, 'b' * 64]
        calls = []
        def remove(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[-1] == 'b' * 64: raise SiteExecutionRefused('Synthetic daemon unavailable')
            return ''
        with patch.object(sessions_module, 'docker', side_effect=remove), \
             patch.object(sessions_module.subprocess, 'run', side_effect=TimeoutError):
            with self.assertRaises(Exception): self.browser.stop()
        self.assertEqual(calls, [['rm', '--force', 'b' * 64], ['rm', '--force', 'a' * 64]])

    def test_closed_or_revoked_create_does_not_start_or_pass_secret_arguments(self):
        self.browser.closed.set()
        with patch.object(sessions_module, 'docker') as docker:
            with self.assertRaises(SiteExecutionRefused): self.browser.create('synthetic', 'synthetic', ['python'])
            docker.assert_not_called()
        self.browser.closed.clear()
        answers = iter([True, False]); self.browser.guard = lambda: next(answers, False)
        with patch.object(sessions_module, 'docker', return_value='c' * 64) as docker:
            with self.assertRaises(SiteExecutionRefused): self.browser.create('synthetic', 'synthetic', ['python'])
        calls = [call.args[0] for call in docker.call_args_list]
        self.assertEqual(calls[0][0], 'create')
        self.assertEqual(calls[1], ['rm', '--force', 'c' * 64])
        self.assertFalse(any(call[0] == 'start' for call in calls))
        self.assertNotIn('synthetic-password', json.dumps(calls))


if __name__ == '__main__':
    unittest.main()
