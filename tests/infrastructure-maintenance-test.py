#!/usr/bin/env python3
"""Maintenance authority boundaries and durable, one-attempt execution checks."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import infrastructure_maintenance as maintenance


def digest(value):
    return hashlib.sha256(value).hexdigest()


def plan():
    return {'schema_version': 1,
            'policy': {'operation': 'update', 'target': 'home-core',
                       'updates': [{'component': 'homepage', 'scheme': 'semver',
                                    'installed': '1.2.3', 'candidate': '1.2.4'}]},
            'prechecks': [{'argv': ['/usr/bin/docker', 'inspect', 'homepage'],
                           'stdout_sha256': digest(b'snapshot')}],
            'steps': [['/usr/bin/docker', 'pull', 'trusted/image@sha256:' + 'a' * 64]],
            'postchecks': [['/usr/bin/docker', 'inspect', 'homepage']],
            'timeout_seconds': 60, 'expires_at': 2000}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        # /tmp is deliberately rejected: every plan/state ancestor must be
        # non-writable by other users, including sticky directories.
        self.temp = tempfile.TemporaryDirectory(prefix='.maintenance-test-', dir=Path.home())
        self.root = Path(self.temp.name).resolve()
        self.calls, self.now = [], 1000
        self.controller = self.new_controller()

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertIs(kwargs['shell'], False)
        self.assertNotIn('OPENCLAW_UNTRUSTED', kwargs['env'])
        return subprocess.CompletedProcess(argv, 0, b'snapshot', b'private-diagnostic')

    def new_controller(self, **kwargs):
        return maintenance.Controller(self.root / 'state', uid=os.geteuid(),
                                      runner=kwargs.pop('runner', self.runner),
                                      clock=lambda: self.now, **kwargs)

    def register(self, value=None):
        path = self.root / 'operator-plan.json'
        path.write_text(json.dumps(value or plan()))
        path.chmod(0o600)
        return self.controller.register(path)

    def test_routine_single_attempt_never_auto_runs_on_register(self):
        receipt = self.register()
        self.assertEqual([], self.calls)
        self.assertEqual('routine', receipt['classification']['classification'])
        self.assertEqual('succeeded', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual(3, len(self.calls))
        self.assertEqual('succeeded', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual(3, len(self.calls))
        self.assertNotIn('argv', json.dumps(self.controller.status(receipt['plan_id'])))
        self.assertNotIn('private-diagnostic', json.dumps(self.controller.list()))

    def test_fix_major_unknown_and_root_override_need_exact_approval(self):
        for kind in ('fix', 'major', 'unknown', 'protected'):
            with self.subTest(kind=kind):
                value = plan()
                if kind == 'fix':
                    value['policy']['operation'] = 'fix'
                elif kind == 'major':
                    value['policy']['updates'][0]['candidate'] = '2.0.0'
                elif kind == 'unknown':
                    value['policy']['updates'][0]['candidate'] = 'latest'
                else:
                    value['requires_approval'] = True
                receipt = self.register(value)
                before = len(self.calls)
                refused = self.controller.run(receipt['plan_id'])
                self.assertEqual('registered', refused['status'])
                self.assertEqual('exact_root_approval_required', refused['reason'])
                self.assertEqual(before, len(self.calls))
                with self.assertRaises(ValueError):
                    self.controller.approve(receipt['plan_id'], '0' * 64)
                self.controller.approve(receipt['plan_id'], receipt['digest'])
                self.assertEqual('succeeded', self.controller.run(receipt['plan_id'])['status'])
                with self.assertRaises(ValueError):
                    self.controller.approve(receipt['plan_id'], receipt['digest'])

    def test_prechecks_bind_current_snapshot_before_any_effect(self):
        value = plan()
        value['prechecks'][0]['stdout_sha256'] = digest(b'old snapshot')
        receipt = self.register(value)
        self.assertEqual('precheck_failed', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual(1, len(self.calls))
        self.controller.run(receipt['plan_id'])
        self.assertEqual(1, len(self.calls))

    def test_postchecks_verify_expected_exact_versions(self):
        value = plan()
        value['postchecks'] = [{'argv': ['/usr/bin/dpkg-query', '--show', 'package'],
                                'stdout_sha256': digest(b'expected version')}]
        receipt = self.register(value)
        self.assertEqual('uncertain', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual(3, len(self.calls))
        self.controller.run(receipt['plan_id'])
        self.assertEqual(3, len(self.calls))

    def test_effect_failure_is_uncertain_and_no_retry(self):
        receipt = self.register()
        def failing(argv, **kwargs):
            if argv[1] == 'pull':
                raise subprocess.TimeoutExpired(argv, 1)
            return self.runner(argv, **kwargs)
        self.controller.runner = failing
        result = self.controller.run(receipt['plan_id'])
        self.assertEqual('uncertain', result['status'])
        self.assertEqual('operator_reconciliation_required', result['reason'])
        self.assertEqual(result, self.new_controller().run(receipt['plan_id']))

    def test_durable_marker_precedes_effect_and_restart_is_uncertain(self):
        receipt = self.register()
        class SimulatedCrash(BaseException):
            pass
        def crashed(argv, **kwargs):
            self.assertEqual('executing', self.controller.status(receipt['plan_id'])['status'])
            if argv[1] == 'pull':
                raise SimulatedCrash()
            return self.runner(argv, **kwargs)
        self.controller.runner = crashed
        with self.assertRaises(SimulatedCrash):
            self.controller.run(receipt['plan_id'])
        recovered = self.new_controller()
        self.assertEqual('uncertain', recovered.status(receipt['plan_id'])['status'])
        self.assertEqual('uncertain', recovered.run(receipt['plan_id'])['status'])
        self.assertEqual(1, len(self.calls))

    def test_expiry_prevents_register_approve_and_execution(self):
        receipt = self.register()
        self.now = 2000
        with self.assertRaises(ValueError):
            self.register()
        with self.assertRaises(ValueError):
            self.controller.approve(receipt['plan_id'], receipt['digest'])
        self.assertEqual('expired', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual([], self.calls)

    def test_expiry_between_precheck_and_effect_stops_changes(self):
        receipt = self.register()
        def expires(argv, **kwargs):
            output = self.runner(argv, **kwargs)
            self.now = 2000
            return output
        self.controller.runner = expires
        self.assertEqual('precheck_failed', self.controller.run(receipt['plan_id'])['status'])
        self.assertEqual(1, len(self.calls))

    def test_operator_plan_permissions_symlinks_duplicates_and_parents(self):
        self.register()
        path = self.root / 'operator-plan.json'
        path.chmod(0o622)
        with self.assertRaises(ValueError):
            self.controller.register(path)
        path.chmod(0o600)
        link = self.root / 'plan-link.json'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            self.controller.register(link)
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):
            self.controller.register(path)
        path.write_text(json.dumps(plan()))
        self.root.chmod(0o722)
        with self.assertRaises(ValueError):
            self.controller.register(path)
        self.root.chmod(0o700)

    def test_state_private_and_not_symlink_or_shared_file(self):
        self.assertEqual(0o700, self.controller.state_dir.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.controller.database.stat().st_mode & 0o777)
        self.controller.database.chmod(0o644)
        with self.assertRaises(ValueError):
            self.new_controller()
        self.controller.database.chmod(0o600)
        with self.assertRaises(PermissionError):
            maintenance.Controller(self.root / 'other', uid=os.geteuid() + 1)

    def test_stored_exact_plan_digest_is_checked(self):
        receipt = self.register()
        with self.controller.connect() as db:
            db.execute('UPDATE plans SET digest=? WHERE id=?', ('0' * 64, receipt['plan_id']))
        with self.assertRaises(ValueError):
            self.controller.run(receipt['plan_id'])
        self.assertEqual([], self.calls)

    def test_mcp_wrapper_only_allows_exact_local_candidate_operations(self):
        command = maintenance.MCP_COMMAND
        for verb in ('snapshot', 'apply', 'verify'):
            maintenance.argv_scope([command, verb, 'a' * 32], 'home-core')
        for argv in ([command, 'apply', '../candidate'], [command, 'prepare', 'aula-mcp'],
                     [command, 'apply', 'a' * 32, '--shell'], [command, 'apply', 'A' * 32]):
            with self.assertRaises(ValueError):
                maintenance.argv_scope(argv, 'home-core')
        for target, local in (('home-spark', 'home-core'), ('home-assistant', 'home-core'),
                              ('home-core', 'home-spark')):
            with self.assertRaises(ValueError):
                maintenance.argv_scope([command, 'apply', 'a' * 32], target, local)

    def test_invalid_schema_scope_argv_and_remote_targets(self):
        for mutation in (
                lambda p: p.update(steps=[['/bin/sh', '-c', 'id']]),
                lambda p: p.update(steps=[['docker', 'pull', 'x']]),
                lambda p: p.update(steps=[['/usr/bin/docker', '--host=tcp://attacker', 'info']]),
                lambda p: p.update(steps=[['/usr/bin/docker', 'a' * 4097]]),
                lambda p: p.update(steps=[['/usr/bin/docker', '\0']]),
                lambda p: p.update(steps=[]),
                lambda p: p.update(requires_approval='false'),
                lambda p: p.update(timeout_seconds=True),
                lambda p: p.update(expires_at=float('nan')),
                lambda p: p.update(agent_approval=True),
                lambda p: p['policy'].update(target='untrusted')):
            value = plan()
            mutation(value)
            with self.assertRaises(ValueError):
                maintenance.validate_plan(value)
        value = plan()
        value['policy']['target'] = 'home-spark'
        with self.assertRaises(ValueError):
            maintenance.validate_plan(value)
        maintenance.validate_plan(value, local_target='home-spark')
        value['policy']['target'] = 'home-assistant'
        with self.assertRaises(ValueError):
            maintenance.validate_plan(value)
        for phase in ('prechecks', 'steps', 'postchecks'):
            if phase == 'prechecks':
                value[phase][0]['argv'] = [maintenance.HA_COMMAND, 'check']
            else:
                value[phase] = [[maintenance.HA_COMMAND, phase]]
        maintenance.validate_plan(value)

    def test_spark_fixed_ssh_no_proxy_or_model_remote_string(self):
        prefix = ['/usr/bin/ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                  '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=8',
                  '-o', 'UserKnownHostsFile=/var/lib/homecompute-infrastructure-maintenance/ssh/known_hosts',
                  '-i', '/var/lib/homecompute-infrastructure-maintenance/ssh/id_ed25519', 'root@home-spark']
        argv = prefix + [maintenance.SPARK_COMMAND + ' check ' + 'a' * 32]
        maintenance.argv_scope(argv, 'home-spark')
        for bad in (argv[:-1] + ['id'], argv[:-1] + [argv[-1] + ';id'],
                    argv[:-2] + ['root@attacker', argv[-1]],
                    [argv[0], '-o', 'ProxyCommand=id', *argv[1:]]):
            with self.assertRaises(ValueError):
                maintenance.argv_scope(bad, 'home-spark')
        with self.assertRaises(ValueError):
            maintenance.argv_scope(argv, 'home-core')
        with self.assertRaises(ValueError):
            maintenance.argv_scope(argv, 'home-core', local_target='home-spark')

    def test_async_receipt_and_serialized_execution(self):
        receipt = self.register()
        started, finish = threading.Event(), threading.Event()
        def paused(argv, **kwargs):
            if argv[1] == 'pull':
                started.set()
                self.assertTrue(finish.wait(5))
            return self.runner(argv, **kwargs)
        self.controller.runner = paused
        try:
            current = self.controller.run_async(receipt['plan_id'])
            self.assertEqual('executing', current['status'])
            self.assertTrue(started.wait(5))
            self.assertEqual('executing', self.controller.status(receipt['plan_id'])['status'])
            self.assertIn('error', self.controller.run_async(receipt['plan_id']))
        finally:
            finish.set()
        deadline = time.monotonic() + 5
        while self.controller.status(receipt['plan_id'])['status'] == 'executing' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual('succeeded', self.controller.status(receipt['plan_id'])['status'])
        self.assertEqual(3, len(self.calls))

    def test_public_list_bounded_and_truncation_explicit(self):
        value = plan()
        value['policy']['updates'] = [{**value['policy']['updates'][0], 'component': 'package' + str(i)} for i in range(32)]
        receipt = self.register(value)
        self.assertTrue(receipt['updates_truncated'])
        self.assertEqual(16, len(receipt['policy']['updates']))
        for _ in range(18):
            self.register(value)
        listing = self.controller.list()
        self.assertLessEqual(len(listing), 16)
        self.assertLessEqual(len(json.dumps(listing).encode()), 65536)
        self.assertEqual(10, len(listing[0]['policy']['updates']))

    def test_deployment_lock_collision_precedes_claim_and_all_effects(self):
        self.controller.deploy_lock = self.root / 'deploy.lock'
        receipt = self.register()
        with self.controller.deploy_lock.open('w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.controller.run(receipt['plan_id'])
            self.assertEqual('registered', self.controller.status(receipt['plan_id'])['status'])
            self.assertEqual([], self.calls)
        self.assertEqual('succeeded', self.controller.run(receipt['plan_id'])['status'])

    def test_nix_profile_and_switch_bound_to_immutable_core_system(self):
        candidate = '/nix/store/' + 'a' * 32 + '-nixos-system-home-core-26.05.1234'
        switch = [candidate + '/bin/switch-to-configuration', 'switch']
        profile = ['/run/current-system/sw/bin/nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', candidate]
        maintenance.argv_scope(profile, 'home-core')
        maintenance.argv_scope(switch, 'home-core')
        for bad in (switch + ['--install-bootloader'], [switch[0], 'test'],
                    [switch[0].replace('home-core', 'home-spark'), 'switch'],
                    profile[:2] + ['/root/.nix-profile', *profile[3:]],
                    profile[:-1] + ['/tmp/system'],
                    [profile[0], '--expr', 'import <nixpkgs> {}']):
            with self.assertRaises(ValueError):
                maintenance.argv_scope(bad, 'home-core')
        with self.assertRaises(ValueError):
            maintenance.argv_scope(switch, 'home-spark', local_target='home-spark')
        with self.assertRaises(ValueError):
            maintenance.argv_scope(profile, 'home-spark', local_target='home-spark')

    def test_prepare_only_passes_finite_selectors_to_trusted_callback(self):
        calls = []
        def prepared(controller, target, component):
            self.assertIs(controller, self.controller)
            if (target, component) not in {('home-core', 'homepage'), ('home-spark', 'packages')}:
                raise ValueError('component absent from root configuration')
            calls.append((target, component))
            return {'status': 'prepared', 'plan_id': 'a' * 32}
        self.controller.preparer = prepared
        self.assertEqual('prepared', self.controller.prepare('home-core', 'homepage')['status'])
        self.assertEqual('prepared', self.controller.prepare('home-spark', 'packages')['status'])
        for target, component in (('home-core', '/etc/shadow'), ('home-core', '../plan'),
                                  ('home-core', 'docker;id'), ('home-core', 'x' * 129),
                                  ('home-core', ''), ('home-core', []),
                                  ('other-host', 'homepage'), ([], 'homepage'),
                                  ('home-core', 'unconfigured')):
            with self.assertRaises(ValueError):
                self.controller.prepare(target, component)
        self.assertEqual([('home-core', 'homepage'), ('home-spark', 'packages')], calls)
        self.assertEqual([], self.calls)

    def test_async_prepare_serialized_receipt_and_failure_is_sanitized(self):
        started, finish = threading.Event(), threading.Event()
        def prepared(_controller, _target, _component):
            started.set()
            self.assertTrue(finish.wait(5))
            return {'plan_id': 'a' * 32}
        self.controller.preparer = prepared
        try:
            receipt = self.controller.prepare('home-core', 'homepage', _async=True)
            self.assertEqual('preparing', receipt['status'])
            self.assertTrue(started.wait(5))
            self.assertEqual(receipt, self.controller.prepare('home-spark', 'packages', _async=True))
        finally:
            finish.set()
        deadline = time.monotonic() + 5
        while self.controller.preparation_status['status'] == 'preparing' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual('prepared', self.controller.preparation_status['status'])
        self.assertEqual('a' * 32, self.controller.preparation_status['plan_id'])
        def failed(*_):
            raise ValueError('private token must not leak')
        self.controller.preparer = failed
        self.controller.prepare('home-core', 'homepage', _async=True)
        deadline = time.monotonic() + 5
        while self.controller.preparation_status['status'] == 'preparing' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual('preparation_failed', self.controller.preparation_status['status'])
        self.assertNotIn('private token', json.dumps(self.controller.preparation_status))

    def test_remote_root_approval_forwards_before_local_approval_is_recorded(self):
        prefix = ['/usr/bin/ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                  '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=8',
                  '-o', 'UserKnownHostsFile=/var/lib/homecompute-infrastructure-maintenance/ssh/known_hosts',
                  '-i', '/var/lib/homecompute-infrastructure-maintenance/ssh/id_ed25519', 'root@home-spark']
        remote = lambda verb: prefix + [maintenance.SPARK_COMMAND + ' ' + verb + ' ' + 'b' * 32]
        value = plan()
        value['policy']['target'] = 'home-spark'
        value['policy']['updates'][0]['candidate'] = '2.0.0'
        value['prechecks'][0]['argv'] = remote('check')
        value['steps'] = [remote('run')]
        value['postchecks'] = [remote('verify')]
        receipt = self.register(value)
        import maintenance_plan_preparation
        def approval(controller, row):
            self.assertIs(controller, self.controller)
            self.assertEqual(receipt['digest'], row['digest'])
            self.assertFalse(controller.status(receipt['plan_id'])['approved'])
        with mock.patch.object(maintenance_plan_preparation, 'approve_remote', side_effect=ValueError('refused'), create=True):
            with self.assertRaises(ValueError):
                self.controller.approve(receipt['plan_id'], receipt['digest'])
        self.assertFalse(self.controller.status(receipt['plan_id'])['approved'])
        with mock.patch.object(maintenance_plan_preparation, 'approve_remote', side_effect=approval, create=True) as forwarded:
            self.assertTrue(self.controller.approve(receipt['plan_id'], receipt['digest'])['approved'])
            self.assertEqual(1, forwarded.call_count)
        with self.assertRaises(ValueError):
            maintenance.argv_scope(remote('approve'), 'home-spark')

    def test_container_fix_helper_only_accepts_exact_local_finite_commands(self):
        command = maintenance.CONTAINER_COMMAND
        for verb in ('snapshot', 'restart', 'verify'):
            maintenance.argv_scope([command, verb, 'home-core-piper-tts'], 'home-core')
            maintenance.argv_scope([command, verb, 'spark-roster-container'], 'home-spark', local_target='home-spark')
        for argv in ([command, 'exec', 'home-core-piper-tts'],
                     [command, 'restart', 'home-core-piper-tts', '--force'],
                     [command, 'restart', '../container'], [command, 'restart', 'container;id'],
                     [command, 'restart', '--host'], [command, 'restart'],
                     [command + '-copy', 'restart', 'home-core-piper-tts']):
            with self.assertRaises(ValueError):
                maintenance.argv_scope(argv, 'home-core')
        with self.assertRaises(ValueError):
            maintenance.argv_scope([command, 'restart', 'home-core-piper-tts'], 'home-spark')
        with self.assertRaises(ValueError):
            maintenance.argv_scope([command, 'restart', 'home-core-piper-tts'], 'home-assistant')

    def test_bounded_runner_discards_inherited_environment(self):
        os.environ['OPENCLAW_UNTRUSTED'] = 'test-value'
        try:
            result = maintenance.bounded_run(['/usr/bin/env'], timeout=2)
        finally:
            del os.environ['OPENCLAW_UNTRUSTED']
        self.assertEqual(0, result.returncode)
        self.assertNotIn(b'OPENCLAW_UNTRUSTED', result.stdout)

    @unittest.skipUnless(hasattr(socket, 'SO_PEERCRED'), 'production socket uses Linux peer credentials')
    def test_linux_socket_only_allows_finite_request_ids(self):
        receipt = self.register()
        socket_path = self.root / 'agent.sock'
        self.controller.preparer = lambda _controller, target, component: {'status': 'prepared', 'target': target, 'component': component}
        process = multiprocessing.get_context('fork').Process(
            target=maintenance.serve, args=(self.controller, socket_path, os.geteuid()))
        process.start()
        def request(path, value):
            body = json.dumps(value).encode()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(str(socket_path))
                connection.sendall(b'POST ' + path.encode() + b' HTTP/1.0\r\nContent-Length: '
                                   + str(len(body)).encode() + b'\r\n\r\n' + body)
                data = b''
                while chunk := connection.recv(65536):
                    data += chunk
            head, payload = data.split(b'\r\n\r\n', 1)
            return int(head.split()[1]), json.loads(payload)
        try:
            deadline = time.monotonic() + 5
            while not socket_path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(socket_path.exists())
            self.assertEqual(0o660, socket_path.stat().st_mode & 0o777)
            self.assertEqual(200, request('/list', {})[0])
            self.assertEqual(200, request('/status', {'plan_id': receipt['plan_id']})[0])
            self.assertEqual(200, request('/prepare', {'target': 'home-core', 'component': 'homepage'})[0])
            self.assertEqual(200, request('/preparation', {})[0])
            for path, value in (('/approve', {'plan_id': receipt['plan_id'], 'digest': receipt['digest']}),
                                ('/register', plan()), ('/run', {'argv': ['/bin/sh']}),
                                ('/run', {'plan_id': receipt['plan_id'], 'approved': True}),
                                ('/run', {'plan_id': '../operator-plan.json'}),
                                ('/prepare', {'target': 'home-core', 'component': '../bad'}),
                                ('/prepare', {'target': 'home-core', 'component': 'homepage', 'argv': ['id']})):
                self.assertEqual(400, request(path, value)[0])
            self.assertEqual(200, request('/run', {'plan_id': receipt['plan_id']})[0])
        finally:
            process.terminate()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())


if __name__ == '__main__':
    unittest.main()
