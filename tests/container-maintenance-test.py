#!/usr/bin/env python3
"""Finite local root restart selection and exact proposal/recovery evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('container_maintenance', ROOT / 'scripts/container-maintenance.py')
container = importlib.util.module_from_spec(spec)
spec.loader.exec_module(container)
from infrastructure_update_policy import classify_plan
from infrastructure_maintenance import validate_plan
from maintenance_plan_preparation import DOCKER

CORE = 'homecompute-automation-n8n-1'
SPARK = 'homecompute-plapre-plapre-primary-1'

def inspect(name=CORE, **changes):
    return {'Name': '/' + name, 'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64,
            'RestartCount': 0, 'Config': {'Env': ['TOKEN=PRIVATE'], 'Image': 'example@sha256:' + 'b' * 64},
            'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}, 'Mounts': [],
            'State': {'Running': True, 'Health': {'Status': 'healthy', 'Log': ['PRIVATE']}, 'StartedAt': 'changing'},
            **changes}


class Tests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.row = inspect()
        self.root = patch.object(container.os, 'geteuid', return_value=0)
        self.root.start()
        self.addCleanup(self.root.stop)
        self.helper = container.Maintenance('home-core', runner=self.runner, clock=lambda: 1000)

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[1] == 'inspect':
            output = json.dumps([self.row]).encode()
        elif argv[1] == 'restart':
            output = (argv[-1] + '\n').encode()
        else:
            raise AssertionError('unexpected command')
        return subprocess.CompletedProcess(argv, 0, output, b'')

    def test_core_catalogue_matches_registered_deployments_and_spark_stays_separate(self):
        self.assertEqual(container.CONTAINERS['home-core'], {row[0] for row in DOCKER.values()})
        self.assertEqual(container.CONTAINERS['home-spark'], {'qwen38-flash-ultrafast', 'gb10-hviske-primary', SPARK})

    def test_root_and_host_boundaries_precede_any_command(self):
        with patch.object(container.os, 'geteuid', return_value=501), self.assertRaises(ValueError):
            container.Maintenance('home-core', runner=self.runner)
        with self.assertRaises(ValueError): container.Maintenance('other-host', runner=self.runner)
        for name in (SPARK, 'other', CORE + ';reboot', ['all'], '--host=tcp://evil', 'https://evil'):
            for method in (self.helper.snapshot, self.helper.restart, self.helper.prepare):
                with self.assertRaises(ValueError): method(name)
        self.assertEqual(self.calls, [])

    def test_snapshot_is_deterministic_binds_configuration_and_redacts_private_inspect(self):
        first = self.helper.snapshot(CORE)
        self.assertEqual(set(first), {'id', 'image', 'running', 'health', 'restart_count', 'config_hash'})
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.assertEqual(first['config_hash'], hashlib.sha256(container.canonical({key: self.row[key] for key in ('Config', 'HostConfig', 'Mounts')}).encode()).hexdigest())
        self.row['State']['StartedAt'] = 'different timestamp'
        self.assertEqual(self.helper.snapshot(CORE), first)
        self.row['Config']['Env'] = ['TOKEN=OTHER_PRIVATE']
        self.assertNotEqual(self.helper.snapshot(CORE)['config_hash'], first['config_hash'])

    def test_identity_extra_rows_and_invalid_raw_metadata_fail_closed(self):
        for changes in ({'Name': '/other'}, {'Id': 'arbitrary'}, {'Image': 'latest'}, {'RestartCount': True},
                        {'State': {'Running': 'yes'}}, {'Mounts': {}}, {'Config': None}):
            self.row = inspect(**changes)
            with self.assertRaises(ValueError): self.helper.snapshot(CORE)
        for raw in (b'[]', b'{}', json.dumps([inspect(), inspect()]).encode(), b'invalid'):
            helper = container.Maintenance('home-core', runner=lambda *a, **kw: subprocess.CompletedProcess([], 0, raw, b''))
            with self.assertRaises(ValueError): helper.snapshot(CORE)

    def test_prepare_reads_only_and_binds_exact_snapshot_while_fix_requires_approval(self):
        self.row['State']['Health']['Status'] = 'unhealthy'
        plan = self.helper.prepare(CORE)
        self.assertTrue(classify_plan(plan['policy'])['approval_required'])
        self.assertTrue(validate_plan(plan)['approval_required'])
        self.assertTrue(plan['requires_approval'])
        self.assertEqual(plan['expires_at'], 1900)
        self.assertEqual(plan['timeout_seconds'], 180)
        self.assertEqual(plan['prechecks'], [{'argv': [container.COMMAND, 'snapshot', CORE], 'stdout_sha256': hashlib.sha256(container.output(self.helper.snapshot(CORE))).hexdigest()}])
        self.assertEqual(plan['steps'], [[container.COMMAND, 'restart', CORE]])
        self.assertEqual(plan['postchecks'], [[container.COMMAND, 'verify', CORE]])
        self.assertTrue(all(argv[1] == 'inspect' for argv, _ in self.calls))

    def test_restart_is_single_fixed_local_command_with_no_daemon_environment(self):
        with patch.dict(container.os.environ, {'DOCKER_HOST': 'tcp://evil', 'DOCKER_CONFIG': '/private'}):
            self.assertEqual(self.helper.restart(CORE), {'restarted': True, 'container': CORE})
        self.assertEqual(len(self.calls), 1)
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, ['/run/current-system/sw/bin/docker', 'restart', '--timeout', '20', CORE])
        self.assertEqual(kwargs['timeout'], 40)
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['cwd'], '/')
        self.assertEqual(kwargs['env'], container.ENV)
        self.assertNotIn('DOCKER_HOST', kwargs['env'])
        self.assertNotIn('DOCKER_CONFIG', kwargs['env'])
        spark = container.Maintenance('home-spark', runner=self.runner)
        spark.restart(SPARK)
        self.assertEqual(self.calls[-1][0], ['/usr/bin/docker', 'restart', '--timeout', '20', SPARK])

    def test_failed_restart_is_never_retried_or_claimed_as_recovery(self):
        calls = []
        def fail(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, b'', b'PRIVATE')
        helper = container.Maintenance('home-core', runner=fail)
        with self.assertRaises(ValueError): helper.restart(CORE)
        self.assertEqual(len(calls), 1)

    def test_verify_polls_only_reads_and_requires_running_and_health(self):
        values = iter([inspect(State={'Running': True, 'Health': {'Status': 'starting'}}),
                       inspect(State={'Running': True, 'Health': {'Status': 'healthy'}})])
        calls, slept = [], []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps([next(values)]).encode(), b'')
        helper = container.Maintenance('home-core', runner=runner)
        result = helper.verify(CORE, monotonic=lambda: 0, sleep=slept.append)
        self.assertTrue(result['verified'])
        self.assertEqual(slept, [5])
        self.assertTrue(all(argv == [helper.docker, 'inspect', CORE] for argv in calls))
        for state in ({'Running': False}, {'Running': True, 'Restarting': True}, {'Running': True, 'Paused': True},
                      {'Running': True, 'Health': {'Status': 'unhealthy'}}, {'Running': True, 'Health': {'Status': 'unknown'}}):
            self.row = inspect(State=state)
            with self.assertRaises(ValueError): self.helper.verify(CORE, timeout=0, monotonic=lambda: 0)
        self.row = inspect(State={'Running': True})
        self.assertTrue(self.helper.verify(CORE, timeout=0, monotonic=lambda: 0)['verified'])

    def test_prepare_flag_does_not_invoke_restart_or_verify(self):
        helper = unittest.mock.Mock()
        helper.prepare.return_value = {'synthetic': True}
        with patch.object(sys, 'argv', ['container', '--prepare', CORE, '--output', '/root/fix.json']), \
             patch.object(container, 'Maintenance', return_value=helper), \
             patch.object(container, 'write_plan') as write, patch.object(sys, 'stdout'):
            self.assertEqual(container.main(), 0)
        helper.prepare.assert_called_once_with(CORE)
        helper.restart.assert_not_called()
        helper.verify.assert_not_called()
        write.assert_called_once_with(Path('/root/fix.json'), {'synthetic': True})


if __name__ == '__main__':
    unittest.main()
