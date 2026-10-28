#!/usr/bin/env python3
"""Grant boundaries and disposable runner tests using synthetic credentials."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_executor as executor
from openclaw_bitwarden import Login
from openclaw_site_registry import SiteRegistry

SPEC = importlib.util.spec_from_file_location('worker_fixture', Path(__file__).with_name('openclaw-site-worker-test.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


class InlinePool:
    def submit(self, function, *args):
        function(*args)


class RecordingRunner:
    def __init__(self):
        self.calls, self.effect = [], None

    def run(self, profile, operation, parameters, login, deadline, guard):
        self.calls.append((profile, operation, parameters, login, deadline))
        if not guard():
            raise executor.SiteExecutionRefused('Grant refused')
        if self.effect:
            self.effect()
        return {'status': 'ok', 'operation': operation, 'results_count': 7}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tick, self.monotonic = 100, 1000
        value = fixtures.profile(); value['qualified'] = True
        other = json.loads(json.dumps(value).replace('shop.example.com', 'library.example.com'))
        self.registry = SiteRegistry([value, other])
        self.grant = {'request_id': str(uuid4()), 'actor': 'openclaw', 'account': value['account'],
                      'scope': 'search', 'status': 'active', 'approved_at': 99, 'expires_at': 200,
                      'request_digest': 'a' * 64, 'transaction_execution_available': False}
        self.provider_calls, self.runner = [], RecordingRunner()
        self.provider_effect = None
        def authorize(_actor, _reference):
            return copy.deepcopy(self.grant)
        def provider(account):
            self.provider_calls.append(account)
            if self.provider_effect:
                self.provider_effect()
            return Login(fixtures.EMAIL, fixtures.PASSWORD)
        self.operations = executor.SiteOperations(Path(self.directory.name).resolve() / 'operations', self.registry,
            authorize, provider, self.runner, clock=lambda: self.tick, monotonic=lambda: self.monotonic)
        self.operations.pool.shutdown(wait=True)
        self.operations.pool = InlinePool()

    def run_operation(self, **changes):
        values = {'request_id': self.grant['request_id'], 'operation': 'search', 'parameters': fixtures.PARAMETERS}
        values.update(changes)
        return self.operations.run('openclaw', values)

    def test_active_grant_is_checked_before_credentials_and_receipt_is_finite(self):
        result = self.run_operation()
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['results_count'], 7)
        self.assertFalse(result['transaction_execution_available'])
        self.assertEqual(self.provider_calls, ['shop.example.com'])
        self.assertEqual(self.runner.calls[0][3], Login(fixtures.EMAIL, fixtures.PASSWORD))
        self.assertNotIn(fixtures.PASSWORD, json.dumps(result))
        self.assertNotIn(fixtures.EMAIL, json.dumps(result))

    def test_second_registered_form_site_uses_its_own_profile_and_account_provider(self):
        self.grant['account'] = 'library.example.com'
        result = self.run_operation()
        self.assertEqual(result['account'], 'library.example.com')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.provider_calls, ['library.example.com'])
        deployed = self.runner.calls[0][0]
        self.assertEqual(deployed['driver'], 'form')
        self.assertEqual(deployed['driver_config']['login_url'], 'https://library.example.com/login')
        self.assertNotIn('shop.example.com', json.dumps(deployed))

    def test_pending_expired_revoked_actor_scope_and_account_refuse_before_provider(self):
        original = copy.deepcopy(self.grant)
        cases = [{'status': state} for state in ['pending', 'expired', 'revoked', 'denied']]
        cases += [{'expires_at': 100}, {'approved_at': 101}, {'actor': 'another'},
                  {'scope': 'read'}, {'account': 'unregistered.example.com'},
                  {'request_id': str(uuid4())}]
        for changes in cases:
            self.grant = dict(original, **changes)
            with self.subTest(changes=changes), self.assertRaises(Exception):
                self.operations.run('openclaw', {'request_id': original['request_id'],
                    'operation': 'search', 'parameters': fixtures.PARAMETERS})
        self.assertEqual(self.provider_calls, []); self.assertEqual(self.runner.calls, [])

    def test_unqualified_profile_and_extra_agent_configuration_are_refused(self):
        value = fixtures.profile()
        self.operations.registry = SiteRegistry([value])
        with self.assertRaises(Exception): self.run_operation()
        self.assertEqual(self.provider_calls, [])
        self.operations.registry = self.registry
        for changes in [{'actor': 'another'}, {'account': 'library.example.com'}, {'url': 'https://shop.example.com/'},
                        {'operation': 'purchase'}, {'parameters': {'password': fixtures.PASSWORD}}]:
            with self.subTest(changes=changes), self.assertRaises(Exception): self.run_operation(**changes)
        self.assertEqual(self.provider_calls, [])

    def test_revocation_between_private_fetch_and_browser_prevents_runner(self):
        self.provider_effect = lambda: self.grant.update(status='revoked')
        receipt = self.run_operation()
        self.assertEqual(self.provider_calls, ['shop.example.com']); self.assertEqual(self.runner.calls, [])
        self.assertEqual(receipt['status'], 'revoked'); self.assertIsNone(receipt['results_count'])

    def test_midflight_revocation_expiry_or_binding_change_discards_results(self):
        original = copy.deepcopy(self.grant)
        for changes in [{'status': 'revoked'}, {'expires_at': 100}, {'request_digest': 'b' * 64},
                        {'account': 'library.example.com'}, {'approved_at': 98}]:
            self.grant = copy.deepcopy(original)
            self.runner.effect = lambda changes=changes: self.grant.update(changes)
            receipt = self.run_operation()
            self.assertNotEqual(receipt['status'], 'completed'); self.assertIsNone(receipt['results_count'])
        self.grant = copy.deepcopy(original)
        self.runner.effect = lambda: setattr(self, 'monotonic', 1045)
        self.assertIsNone(self.run_operation()['results_count'])

    def test_wall_clock_expiry_during_worker_suppresses_results(self):
        self.runner.effect = lambda: setattr(self, 'tick', 200)
        receipt = self.run_operation()
        self.assertIsNone(receipt['results_count']); self.assertNotEqual(receipt['status'], 'completed')

    def test_revoked_grant_never_returns_previous_history(self):
        self.assertEqual(self.run_operation()['results_count'], 7)
        self.grant['status'] = 'revoked'
        receipt = self.operations.status('openclaw', self.grant['request_id'])
        self.assertEqual(receipt['status'], 'revoked'); self.assertIsNone(receipt['results_count'])
        self.assertFalse(receipt['account_execution_available'])


class RunnerTests(unittest.TestCase):
    CONTAINER_ID = 'b' * 64

    def invoke(self, raw, guards=None, cleanup_effect=None, *, captured=None,
               process_alive=False, kill_error=None, wait_error=None):
        captured = {} if captured is None else captured
        captured['events'] = []
        input_file = tempfile.TemporaryFile()
        read_fd, write_fd = os.pipe()
        os.write(write_fd, raw); os.close(write_fd)
        stream = os.fdopen(read_fd, 'rb', buffering=0)
        class Process:
            stdout, returncode, pid, alive = stream, 0, 12345, process_alive
            def poll(self): return None if self.alive else 0
            def wait(self, **_kwargs):
                captured['events'].append('wait')
                if wait_error: raise wait_error
                self.alive = False
                return 0
        def popen(command, **kwargs):
            captured['events'].append('start')
            captured.update(command=command, options=kwargs)
            captured['private_input'] = json.loads(os.read(kwargs['stdin'], 65536))
            return Process()
        def run(command, **kwargs):
            captured['events'].append(command[1])
            if command[1] == 'create':
                captured.update(create_command=command, create_options=kwargs)
                return SimpleNamespace(returncode=0, stdout=self.CONTAINER_ID + '\n', stderr='')
            return cleanup_effect(command, **kwargs) if cleanup_effect else SimpleNamespace(returncode=0)
        def kill(*_args):
            captured['events'].append('kill')
            if kill_error: raise kill_error
        states = iter(guards) if guards else None
        guard = (lambda: next(states, False)) if states else (lambda: True)
        self.addCleanup(input_file.close)
        with patch.object(executor.os, 'memfd_create', lambda *_a: os.dup(input_file.fileno()), create=True), \
             patch.object(executor.os, 'MFD_CLOEXEC', 1, create=True), \
             patch.object(executor.os, 'killpg', kill), \
             patch.object(executor.subprocess, 'Popen', popen), \
             patch.object(executor.subprocess, 'run', side_effect=run) as cleanup, \
             patch.object(executor.select, 'select', return_value=([stream], [], [])):
            try:
                result = executor.ContainerRunner('sha256:' + 'a' * 64, monotonic=lambda: 100).run(
                    fixtures.profile(), 'search', fixtures.PARAMETERS, Login(fixtures.EMAIL, fixtures.PASSWORD), 140, guard)
                captured['cleanup_calls'] = [call for call in cleanup.call_args_list if call.args[0][1] != 'create']
                return result, captured, cleanup.call_args
            finally:
                stream.close()

    def test_exact_projection_and_fixed_container_arguments_keep_secrets_in_stdin(self):
        result, captured, cleanup = self.invoke(b'{"status":"ok","operation":"search","results_count":7}')
        self.assertEqual(result['results_count'], 7)
        command, options = captured['create_command'], captured['create_options']
        self.assertEqual(command[0], executor.DOCKER)
        for forbidden in [fixtures.PASSWORD, fixtures.EMAIL, 'private-cookie', '--publish', '--privileged', '--no-sandbox']:
            self.assertNotIn(forbidden, json.dumps(command) + json.dumps(options['env']))
        self.assertIn('--read-only', command); self.assertIn('--cap-drop', command)
        self.assertIn('ALL', command); self.assertIn('no-new-privileges:true', command)
        self.assertIn('--log-driver', command); self.assertIn('none', command)
        self.assertEqual(options['stdin'], executor.subprocess.DEVNULL)
        self.assertEqual(captured['command'], [executor.DOCKER, 'start', '--attach', '--interactive', self.CONTAINER_ID])
        self.assertEqual(cleanup.args[0][-1], self.CONTAINER_ID)
        self.assertEqual(captured['private_input']['login']['password'], fixtures.PASSWORD)
        self.assertEqual(cleanup.args[0][1:3], ['rm', '--force'])

    def test_stdout_smuggling_and_nonfinite_schema_are_refused(self):
        safe = {'status': 'ok', 'operation': 'search', 'results_count': 7}
        variants = [dict(safe, password=fixtures.PASSWORD), dict(safe, operation='purchase'),
                    dict(safe, results_count=True), dict(safe, results_count=101),
                    dict(safe, results_count=-1), dict(safe, status='expired'),
                    dict(safe, results_count=fixtures.PASSWORD), [safe]]
        values = [json.dumps(value).encode() for value in variants]
        values += [b'password=' + fixtures.PASSWORD.encode(), b'x' * 4097,
                   json.dumps(safe).encode() + b'\n' + fixtures.PASSWORD.encode(),
                   b'{"status":"expired","status":"ok","operation":"search","results_count":7}']
        for raw in values:
            with self.subTest(raw=raw[:40]), self.assertRaises(executor.SiteExecutionRefused) as error:
                self.invoke(raw)
            self.assertNotIn(fixtures.PASSWORD, str(error.exception))

    def test_revocation_during_runner_discards_stdout(self):
        with self.assertRaises(executor.SiteExecutionRefused):
            self.invoke(b'{"status":"ok","operation":"search","results_count":7}', guards=[True, True, False])

    def test_revoked_during_creation_never_starts_or_sends_private_input(self):
        captured = {}
        with self.assertRaises(executor.SiteExecutionRefused):
            self.invoke(b'', guards=[True, False], captured=captured)
        self.assertEqual(captured['events'], ['create', 'rm'])
        self.assertNotIn('private_input', captured); self.assertNotIn('command', captured)
        self.assertEqual(captured['create_options']['stdin'], executor.subprocess.DEVNULL)

    def test_attached_client_is_killed_and_waited_before_removing_captured_id(self):
        captured, removed = {}, []
        def cleanup(command, **_kwargs):
            removed.append(command)
            return SimpleNamespace(returncode=0)
        with self.assertRaises(executor.SiteExecutionRefused):
            self.invoke(b'', guards=[True, True, False], captured=captured,
                        process_alive=True, cleanup_effect=cleanup)
        self.assertEqual(captured['events'], ['create', 'start', 'kill', 'wait', 'rm'])
        self.assertEqual(removed, [[executor.DOCKER, 'rm', '--force', self.CONTAINER_ID]])

    def test_client_exit_race_and_wait_timeout_still_force_remove_captured_id(self):
        for kill_error, wait_error in [(ProcessLookupError(fixtures.PASSWORD), None),
                                       (None, TimeoutError(fixtures.PASSWORD))]:
            captured, removed = {}, []
            def cleanup(command, **_kwargs):
                removed.append(command)
                return SimpleNamespace(returncode=0)
            with self.subTest(kill_error=type(kill_error).__name__), self.assertRaises(executor.SiteExecutionRefused) as error:
                self.invoke(b'', guards=[True, True, False], captured=captured,
                            process_alive=True, kill_error=kill_error, wait_error=wait_error, cleanup_effect=cleanup)
            self.assertEqual(removed, [[executor.DOCKER, 'rm', '--force', self.CONTAINER_ID]])
            self.assertLess(captured['events'].index('wait'), captured['events'].index('rm'))
            self.assertNotIn(fixtures.PASSWORD, str(error.exception))

    def test_already_removed_container_is_accepted_only_after_exact_absence_verification(self):
        for missing in ['Error: No such container: ', 'Error: No such object: ']:
            def cleanup(command, **_kwargs):
                if command[1] == 'rm': return SimpleNamespace(returncode=1)
                return SimpleNamespace(returncode=1, stdout='', stderr=missing + command[-1] + '\n')
            result, captured, _last = self.invoke(
                b'{"status":"ok","operation":"search","results_count":7}', cleanup_effect=cleanup)
            self.assertEqual(result['status'], 'ok')
            calls = captured['cleanup_calls']
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1].args[0][1:5], ['container', 'inspect', '--format', '{{.Id}}'])
            self.assertEqual(calls[0].args[0][-1], calls[1].args[0][-1])
            self.assertEqual(calls[1].kwargs['timeout'], 2)

    def test_daemon_error_remaining_container_and_cleanup_timeout_suppress_success(self):
        replies = [SimpleNamespace(returncode=1, stdout='', stderr=fixtures.PASSWORD),
                   SimpleNamespace(returncode=0, stdout='a' * 64, stderr=''),
                   SimpleNamespace(returncode=1, stdout='', stderr='Error: No such container: another'),
                   TimeoutError(fixtures.PASSWORD)]
        for reply in replies:
            def cleanup(command, **_kwargs):
                if command[1] == 'rm': return SimpleNamespace(returncode=1)
                if isinstance(reply, Exception): raise reply
                return reply
            with self.subTest(reply=type(reply).__name__), self.assertRaises(executor.SiteExecutionRefused) as error:
                self.invoke(b'{"status":"ok","operation":"search","results_count":7}', cleanup_effect=cleanup)
            self.assertNotIn(fixtures.PASSWORD, str(error.exception))

    def test_mutable_image_is_refused(self):
        for image in ['worker:latest', 'sha256:' + 'a' * 63, 'sha256:' + 'A' * 64]:
            with self.assertRaises(executor.SiteExecutionRefused): executor.ContainerRunner(image)


class GrantClientTests(unittest.TestCase):
    def test_fixed_origin_actor_reference_and_transaction_projection_fail_closed(self):
        request_id = str(uuid4())
        base = {'request_id': request_id, 'actor': 'openclaw', 'transaction_execution_available': False}
        client = executor.GrantClient({'openclaw': 'synthetic-transport-token'})
        calls = []
        for value, status in [(base, 200), (dict(base, actor='another'), 200),
                              (dict(base, request_id=str(uuid4())), 200),
                              (dict(base, transaction_execution_available=True), 200), (base, 403)]:
            connection = SimpleNamespace(
                request=lambda *args, **kwargs: calls.append((args, kwargs)),
                getresponse=lambda: SimpleNamespace(status=status, read=lambda _n: json.dumps(value).encode()),
                close=lambda: None)
            with patch.object(executor.http.client, 'HTTPConnection', return_value=connection) as origin:
                if value == base and status == 200:
                    self.assertEqual(client.status('openclaw', request_id), base)
                else:
                    with self.assertRaises(executor.SiteExecutionRefused) as error:
                        client.status('openclaw', request_id)
                    self.assertNotIn('synthetic-transport-token', str(error.exception))
                origin.assert_called_once_with('127.0.0.1', 18812, timeout=0.6)
        self.assertTrue(all(call[0] == ('GET', '/access/' + request_id) for call in calls))
        with patch.object(executor.http.client, 'HTTPConnection') as origin:
            for actor, reference in [('another', request_id), ('openclaw', '../access')]:
                with self.assertRaises(Exception): client.status(actor, reference)
            origin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
