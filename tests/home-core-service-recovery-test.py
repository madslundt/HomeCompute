#!/usr/bin/env python3
"""Exercise recovery ordering and the deliberately-stopped boundary."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'scripts/home-core-service-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class Runtime:
    def __init__(self, target='immich', *, nas=False, lan=True, status='exited', error=None):
        self.calls = []
        self.target, self.nas, self.lan, self.status = target, nas, lan, status
        self.error = error if error is not None else (
            'error mounting library: no such device' if target == 'immich'
            else 'failed to bind host port: cannot assign requested address')
        self.wrong_mount = False

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            _, project, service = recovery.TARGETS[self.target]
            return json.dumps([{'Config': {'Labels': {
                'com.docker.compose.project': project, 'com.docker.compose.service': service}},
                'State': {'Status': self.status, 'Error': self.error, 'Health': {'Status': 'healthy'}},
                'Mounts': [{'Source': '/mnt/immich-nas', 'Destination': '/data'}]}])
        if args[0] == 'ip':
            return json.dumps([{'addr_info': [{'local': '192.168.30.122' if self.lan else '127.0.0.1'}]}])
        if args[0] == 'findmnt':
            if not self.nas:
                raise subprocess.CalledProcessError(1, args)
            return json.dumps({'filesystems': [{'source': '//wrong/share' if self.wrong_mount
                else '//192.168.30.236/immich-library', 'fstype': 'cifs'}]})
        if args[:2] == ['systemctl', 'start']:
            self.nas = True
            return ''
        if args[:2] == ['docker', 'start']:
            return args[2]
        raise AssertionError(args)


class RecoveryTests(unittest.TestCase):
    def test_mount_is_established_and_rechecked_before_starting_immich(self):
        run = Runtime()
        self.assertEqual(recovery.recover('immich', repair=True, run=run)['status'], 'started')
        commands = [x[0] for x in run.calls]
        self.assertEqual(commands, ['docker', 'ip', 'findmnt', 'systemctl', 'findmnt', 'docker'])

    def test_wrong_nas_share_never_starts_server(self):
        run = Runtime(nas=True)
        run.wrong_mount = True
        with self.assertRaises(ValueError):
            recovery.recover('immich', repair=True, run=run)
        self.assertFalse(any(x[:2] == ['docker', 'start'] for x in run.calls))

    def test_check_does_not_mount_or_start(self):
        run = Runtime()
        self.assertEqual(recovery.recover('immich', repair=False, run=run)['status'], 'waiting-for-nas')
        self.assertFalse(any(x[:2] in [['docker', 'start'], ['systemctl', 'start']] for x in run.calls))

    def test_wait_for_lan_without_attempting_container_start(self):
        run = Runtime('moss', lan=False)
        self.assertEqual(recovery.recover('moss', repair=True, run=run)['status'], 'waiting-for-lan')
        self.assertFalse(any(x[:2] == ['docker', 'start'] for x in run.calls))

    def test_moss_recovers_after_lan_arrives(self):
        run = Runtime('moss')
        self.assertEqual(recovery.recover('moss', repair=True, run=run)['status'], 'started')
        self.assertEqual(run.calls[-1], ['docker', 'start', 'home-core-moss-tts'])

    def test_never_restart_a_running_or_intentionally_stopped_container(self):
        for options in [{'status': 'running'}, {'error': ''}, {'status': 'created'}]:
            with self.subTest(options=options):
                run = Runtime(**options)
                recovery.recover('immich', repair=True, run=run)
                self.assertEqual(len(run.calls), 1)

    def test_other_container_failures_are_held(self):
        run = Runtime(error='application failed')
        self.assertEqual(recovery.recover('immich', repair=True, run=run)['status'], 'held')
        self.assertEqual(len(run.calls), 1)

    def test_unauthorized_target_is_not_a_shell_or_docker_interface(self):
        with self.assertRaises(KeyError):
            recovery.recover('postgres', repair=True, run=Runtime())

    def test_only_failed_funnel_is_retried_and_enablement_never_changes(self):
        for initial in ['active', 'inactive', 'failed']:
            calls = []
            active = initial
            def run(args):
                nonlocal active
                calls.append(args)
                if args[:2] == ['systemctl', 'restart']:
                    active = 'active'
                    return ''
                return f'LoadState=loaded\nActiveState={active}\n'
            result = recovery.recover_funnel(repair=True, run=run)
            self.assertEqual(result['status'], {'active': 'healthy', 'inactive': 'held', 'failed': 'started'}[initial])
            self.assertEqual(any(x[:2] == ['systemctl', 'restart'] for x in calls), initial == 'failed')
            self.assertFalse(any('enable' in x for x in calls))


if __name__ == '__main__':
    unittest.main()
