#!/usr/bin/env python3
"""Exercise trusted Docker cloning, durable attempts and data-restoring rollback."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import docker_maintenance as docker
import docker_maintenance_backup as backup

OLD, NEW = 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64
REF = 'ghcr.io/example/app@' + NEW
CID = '1' * 64


def container():
    return {'Id': CID, 'Name': '/service', 'Image': OLD, 'RestartCount': 0,
            'Config': {'Image': 'ghcr.io/example/app:1.2.3@' + OLD, 'Env': ['TOKEN=PRIVATE'],
                       'Cmd': ['serve'], 'User': '1000', 'Volumes': {'/data': {}}},
            'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                           'NetworkMode': 'appnet', 'PortBindings': {'80/tcp': [{'HostPort': '8080'}]},
                           'Memory': 1024 ** 3, 'CpuShares': 256, 'Binds': None},
            'Mounts': [{'Type': 'volume', 'Name': 'existing-anonymous-volume',
                        'Source': '/var/lib/docker/volumes/existing-anonymous-volume/_data',
                        'Destination': '/data', 'RW': True}],
            'NetworkSettings': {'Networks': {'appnet': {'Aliases': ['service', 'private-app'],
                                                       'IPAMConfig': {'IPv4Address': '172.20.0.10'}}}},
            'State': {'Running': True, 'Status': 'running', 'Health': {'Status': 'healthy'}}}


class FakeEngine:
    def __init__(self):
        self.old = container()
        self.rows, self.calls, self.fail_new, self.fail_disconnect = {CID: self.old}, [], False, False

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        url = urlsplit(path); parts = [unquote(part) for part in url.path.split('/') if part]
        if parts == ['containers', 'json']:
            return [{'Id': row['Id'], 'State': row['State']['Status']} for row in self.rows.values()]
        if parts[0] == 'images':
            image = OLD if parts[1] == OLD else NEW
            return {'Id': image, 'Os': 'linux', 'Architecture': 'amd64'}
        if parts[:2] == ['containers', 'create']:
            row = copy.deepcopy(self.old)
            row.update(Id='2' * 64, Name='/' + parse_qs(url.query)['name'][0], Image=NEW)
            row['Config'] = {k: v for k, v in copy.deepcopy(body).items() if k not in {'HostConfig', 'NetworkingConfig'}}
            row['HostConfig'] = copy.deepcopy(body['HostConfig'])
            row['NetworkSettings']['Networks'] = copy.deepcopy(body['NetworkingConfig']['EndpointsConfig'])
            row['State'] = {'Running': False, 'Status': 'created', 'Health': {'Status': 'starting'}}
            self.rows[row['Id']] = row
            return {'Id': row['Id']}
        if parts[0] == 'networks':
            if self.fail_disconnect and parts[2] == 'disconnect':
                self.fail_disconnect = False
                raise ValueError('network disconnect failed')
            return None
        row = next((r for r in self.rows.values() if parts[1] in {r['Id'], r['Name'].lstrip('/')}), None)
        if row is None:
            raise ValueError('container not found')
        if method == 'GET':
            return copy.deepcopy(row)
        if method == 'DELETE':
            del self.rows[row['Id']]
        elif parts[2] == 'stop':
            row['State'].update(Running=False, Status='exited')
        elif parts[2] == 'start':
            row['State'].update(Running=True, Status='running')
            row['State']['Health']['Status'] = 'unhealthy' if row['Image'] == NEW and self.fail_new else 'healthy'
        elif parts[2] == 'rename':
            row['Name'] = '/' + parse_qs(url.query)['name'][0]
        elif parts[2] == 'update':
            row['HostConfig'].update(body)
        return None


class FakeRegistry:
    def platform(self, ref, tag, *platform):
        image = OLD if tag == '1.2.3' else NEW
        return {'config_digest': image, 'platform_digest': image, 'index_digest': image}


class Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / 'private'; self.state.mkdir(mode=0o700)
        self.engine = FakeEngine()
        self.backups, self.restores = [], []
        for target, value in [('geteuid', lambda: 0)]:
            p = patch.object(docker.os, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(docker, 'trusted_path', lambda path, *a, **kw: Path(path)); p.start(); self.addCleanup(p.stop)
        p = patch.object(docker, 'root_json', lambda path, uid: json.loads(Path(path).read_text())); p.start(); self.addCleanup(p.stop)
        p = patch.object(backup, 'scan', return_value={'bytes': 10, 'entries': 1}); p.start(); self.addCleanup(p.stop)
        def create_backup(src, dest):
            self.backups.append((src, dest)); Path(dest).write_bytes(b'private backup')
        p = patch.object(backup, 'create', side_effect=create_backup); p.start(); self.addCleanup(p.stop)
        p = patch.object(backup, 'restore', side_effect=lambda src, path: self.restores.append((src, path))); p.start(); self.addCleanup(p.stop)
        self.tick = 0
        def monotonic():
            self.tick += 60
            return self.tick
        self.helper = docker.Maintenance('home-core', state=self.state, engine=self.engine,
                                         registry=FakeRegistry(), clock=lambda: 1000,
                                         sleep=lambda seconds: None, monotonic=monotonic)

    def stage(self, **changes):
        args = {'container': 'service', 'candidate_ref': REF, 'candidate_version': '1.2.4',
                'installed_version': '1.2.3', 'evidence': {'installed_tag': '1.2.3', 'candidate_tag': '1.2.4'}}
        args.update(changes)
        return self.helper.stage(**args)

    def test_success_clones_settings_volume_identity_networks_and_keeps_old_stopped(self):
        before = copy.deepcopy(self.engine.old)
        identity = self.stage()
        plan = self.helper.plan(identity)
        self.assertFalse(plan['requires_approval'])
        self.assertEqual(plan['prechecks'][0]['stdout_sha256'], hashlib.sha256(
            (docker.canonical(self.helper.snapshot(identity)) + '\n').encode()).hexdigest())
        self.assertTrue(self.helper.apply(identity)['verified'])
        new = self.helper.row('service')
        self.assertEqual(new['Config']['Env'], ['TOKEN=PRIVATE'])
        self.assertEqual(new['HostConfig']['Memory'], before['HostConfig']['Memory'])
        self.assertEqual(new['HostConfig']['PortBindings'], before['HostConfig']['PortBindings'])
        self.assertEqual(new['HostConfig']['Binds'], ['existing-anonymous-volume:/data:rw'])
        self.assertEqual(docker.mount_identity(new), docker.mount_identity(before))
        self.assertEqual(docker.endpoint_settings(new), docker.endpoint_settings(before))
        self.assertFalse(self.engine.old['State']['Running'])
        self.assertEqual(self.engine.old['HostConfig']['RestartPolicy'], {'Name': 'no'})
        self.assertEqual(len(self.backups), 1)
        self.assertTrue((self.state / (identity + '.active')).exists())
        self.assertNotIn('PRIVATE', json.dumps(self.helper.verify(identity)))
        self.assertEqual(stat_mode(self.state / (identity + '.json')), 0o600)

    def test_failed_health_restores_data_prior_name_network_and_restart_policy(self):
        self.engine.fail_new = True
        identity = self.stage()
        with self.assertRaisesRegex(ValueError, 'attempt failed'):
            self.helper.apply(identity)
        self.assertEqual(self.helper.load(identity)['status'], 'rolled_back')
        self.assertEqual(self.engine.old['Name'], '/service')
        self.assertTrue(self.engine.old['State']['Running'])
        self.assertEqual(self.engine.old['HostConfig']['RestartPolicy']['Name'], 'unless-stopped')
        self.assertEqual(len(self.restores), 1)
        self.assertEqual(len(self.engine.rows), 1)
        self.assertTrue(any('/connect' in path for _, path, _ in self.engine.calls))
        with self.assertRaises(ValueError): self.helper.apply(identity)
        with self.assertRaisesRegex(ValueError, 'already attempted'): self.stage()

    def test_stop_timeout_after_effect_starts_original_without_retrying_stop(self):
        identity = self.stage()
        request, first = self.engine.request, True
        def timeout_after_stop(method, path, body=None):
            nonlocal first
            result = request(method, path, body)
            if first and path == '/containers/' + CID + '/stop?t=30':
                first = False
                raise OSError('response lost after stopping')
            return result
        self.engine.request = timeout_after_stop
        with self.assertRaises(ValueError): self.helper.apply(identity)
        self.assertTrue(self.engine.old['State']['Running'])
        self.assertEqual(self.helper.load(identity)['status'], 'rolled_back')
        self.assertEqual(sum('/stop?' in path for _, path, _ in self.engine.calls), 1)
        self.assertFalse(any('/containers/create' in path for _, path, _ in self.engine.calls))

    def test_dormant_and_unhealthy_containers_are_never_started(self):
        for running, status, health in [(False, 'exited', 'healthy'), (False, 'created', 'starting'),
                                         (True, 'running', 'unhealthy')]:
            self.engine.old['State'] = {'Running': running, 'Status': status, 'Health': {'Status': health}}
            identity = self.stage()
            self.assertTrue(self.helper.plan(identity)['requires_approval'])
            with self.assertRaises(ValueError): self.helper.apply(identity)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.engine.calls))

    def test_backup_hold_is_no_effect_even_with_explicit_apply(self):
        with patch.object(backup, 'scan', side_effect=ValueError('too large')):
            identity = self.stage()
            self.assertIn('backup_requires_review', self.helper.load(identity)['reasons'])
            with self.assertRaises(ValueError): self.helper.apply(identity)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.engine.calls))

    def test_engine_or_version_provenance_mismatch_refuses_preparation(self):
        for args in ({'candidate_ref': 'ghcr.io/other/app@' + NEW}, {'candidate_ref': 'ghcr.io/example/app:latest'},
                     {'candidate_version': '1.2.9'}, {'evidence': {'installed_tag': '1.2.3', 'candidate_tag': '1.2.4-alpine'}},
                     {'evidence': {'installed_tag': '1.2.3', 'candidate_tag': '1.2.4-rc1'}}, {'evidence': None}):
            with self.assertRaises(ValueError): self.stage(**args)
        with patch.object(self.helper.registry, 'platform', return_value={'config_digest': 'sha256:' + 'c' * 64}):
            with self.assertRaises(ValueError): self.stage()
        self.assertEqual(list(self.state.glob('*.json')), [])
        with self.assertRaises(ValueError): docker.tag_version('16.15-alpine')

    def test_moving_two_part_alias_cannot_invent_an_installed_patch_version(self):
        for tag in ['3.12', '3.12-slim-bookworm', '0.11', 'v0.11', '16.15-alpine']:
            with self.assertRaises(ValueError): docker.tag_version(tag)
        with self.assertRaisesRegex(ValueError, 'major.minor.patch'):
            self.stage(installed_version='1.2.0', evidence={'installed_tag': '1.2', 'candidate_tag': '1.2.4'})
        self.assertEqual(list(self.state.glob('*.json')), [])
        self.assertTrue(all('/images/' not in path for _, path, _ in self.engine.calls))

    def test_wrong_ids_and_arbitrary_arguments_rejected_before_daemon_calls(self):
        for name in ['../service', '--host=tcp://evil', 'service;reboot', '/etc/shadow']:
            with self.assertRaises(ValueError): self.stage(container=name)
        for identity in ['../root', 'a' * 31, 'a' * 32 + ';id', ['all']]:
            for method in [self.helper.apply, self.helper.snapshot, self.helper.verify]:
                with self.assertRaises(ValueError): method(identity)
        self.assertEqual(self.engine.calls, [])

    def test_changed_baseline_cannot_deploy_or_create_attempt_marker(self):
        identity = self.stage()
        self.engine.old['HostConfig']['Memory'] = 17
        with self.assertRaises(ValueError): self.helper.apply(identity)
        self.assertEqual(list(self.state.glob('*.attempt')), [])

    def test_shared_writable_data_requires_review_and_no_effects(self):
        other = copy.deepcopy(self.engine.old); other['Id'] = '3' * 64; other['Name'] = '/other'
        self.engine.rows[other['Id']] = other
        identity = self.stage()
        self.assertIn('shared_writable_data', self.helper.load(identity)['reasons'])
        with self.assertRaises(ValueError): self.helper.apply(identity)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.engine.calls))

    def test_no_healthcheck_requires_three_stable_reads_and_resource_drift_fails_verify(self):
        self.engine.old['State'].pop('Health')
        self.assertTrue(self.helper.wait('service', OLD, timeout=300)['State']['Running'])
        self.assertEqual(sum(method == 'GET' for method, _, _ in self.engine.calls), 3)
        self.engine.old['State']['Health'] = {'Status': 'healthy'}
        identity = self.stage(); self.helper.apply(identity)
        self.helper.row('service')
        self.engine.rows['2' * 64]['HostConfig']['Memory'] = 1
        with self.assertRaises(ValueError): self.helper.verify(identity)

    def test_unsupported_clones_fail_without_effects_and_preserve_explicit_mounts(self):
        original = container()
        original['HostConfig']['Mounts'] = [{'Type': 'volume', 'Source': 'named', 'Target': '/data', 'ReadOnly': False}]
        result = docker.clone(original, REF)
        self.assertIsNone(result['HostConfig']['Binds'])
        self.assertEqual(result['HostConfig']['Mounts'], original['HostConfig']['Mounts'])
        for key, value in [('AutoRemove', True), ('VolumesFrom', ['other']), ('NetworkMode', 'container:other')]:
            row = container(); row['HostConfig'][key] = value
            with self.assertRaises(ValueError): docker.clone(row, REF)


def stat_mode(path):
    return path.stat().st_mode & 0o777


class BackupTests(unittest.TestCase):
    def test_archive_and_restore_links_without_following_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'; source.mkdir()
            outside = Path(temporary) / 'outside'; outside.write_text('untouched')
            (source / 'nested').mkdir(); (source / 'nested/data').write_text('before')
            (source / 'link').symlink_to(outside)
            archive = Path(temporary) / 'backup.tar'
            @contextmanager
            def local_directory(path):
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: yield fd
                finally: os.close(fd)
            with patch.object(backup, 'directory', local_directory), patch.object(backup.os, 'listxattr', return_value=[], create=True):
                self.assertEqual(backup.scan([source])['entries'], 3)
                backup.create(source, archive)
                (source / 'nested/data').write_text('after')
                (source / 'new').write_text('delete during rollback')
                backup.restore(source, archive)
            self.assertEqual((source / 'nested/data').read_text(), 'before')
            self.assertFalse((source / 'new').exists())
            self.assertTrue((source / 'link').is_symlink())
            self.assertEqual(outside.read_text(), 'untouched')

    def test_system_mount_and_symlink_ancestor_are_rejected(self):
        for path in ['/', '/etc', '/srv', '/srv/../etc', '/var/run/docker.sock']:
            with self.assertRaises(ValueError):
                with backup.directory(path): pass

    def test_archive_traversal_is_rejected_before_data_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'; source.mkdir(); (source / 'data').write_text('keep')
            archive = Path(temporary) / 'bad.tar'
            with tarfile.open(archive, 'w') as stream:
                root = tarfile.TarInfo('.'); root.type = tarfile.DIRTYPE; stream.addfile(root)
                stream.addfile(tarfile.TarInfo('../outside'))
            @contextmanager
            def local_directory(path):
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                try: yield fd
                finally: os.close(fd)
            with patch.object(backup, 'directory', local_directory), self.assertRaises(ValueError):
                backup.restore(source, archive)
            self.assertEqual((source / 'data').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
