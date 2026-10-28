#!/usr/bin/env python3
"""Finite trusted preparation, remote identity binding and operator approval."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import maintenance_plan_preparation as prep
from infrastructure_update_policy import classify_plan

NOW = 1000


def receipt(count=1, candidate='1.2.4'):
    policy = {'operation': 'update', 'target': 'home-spark', 'updates': [
        {'component': f'package-{index}', 'scheme': 'debian', 'installed': '1.2.3', 'candidate': candidate}
        for index in range(count)]}
    return {'plan_id': 'a' * 32, 'digest': 'b' * 64, 'status': 'registered', 'reason': None,
            'expires_at': 1900, 'policy': policy, 'updates_truncated': False,
            'requires_approval': False, 'classification': classify_plan(policy), 'approved': False}


def encoded(value):
    return (prep.canonical(value) + '\n').encode()


class RemoteTest(unittest.TestCase):
    def setUp(self):
        self.current = receipt()
        self.calls = []
        self.controller = SimpleNamespace(uid=0, local_target='home-core', clock=lambda: NOW, runner=self.runner)

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        command = argv[-1]
        if ' check ' in command:
            value = {key: item for key, item in self.current.items() if key != 'approved'}
        elif ' approve ' in command:
            value = dict(self.current, approved=True)
        else:
            value = self.current
        return subprocess.CompletedProcess(argv, 0, encoded(value), b'')

    def test_full_remote_package_policy_preserved_including_more_than_16_rows(self):
        self.current = receipt(count=80)
        plan = prep.remote_plan(self.controller)
        self.assertEqual(len(plan['policy']['updates']), 80)
        self.assertEqual(plan['steps'], [prep.ssh('run', 'a' * 32)])
        self.assertEqual(plan['postchecks'], [prep.ssh('verify', 'a' * 32)])
        expected = encoded({key: value for key, value in self.current.items() if key != 'approved'})
        self.assertEqual(plan['prechecks'][0]['stdout_sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(plan['expires_at'], 1900)
        self.assertTrue(all(' run ' not in argv[-1] for argv in self.calls))

    def test_classification_cannot_claim_major_is_routine(self):
        bad = receipt(candidate='2.0.0')
        bad['classification'] = receipt()['classification']
        with self.assertRaisesRegex(ValueError, 'classification disagrees'):
            prep.remote_receipt(encoded(bad), NOW)

    def test_image_selection_binds_remote_kind_and_exact_container(self):
        self.current['policy']['updates'] = [{'component': 'service', 'scheme': 'semver',
                                             'installed': '1.2.3', 'candidate': '1.2.4'}]
        self.current['classification'] = classify_plan(self.current['policy'])
        self.assertEqual(prep.remote_plan(self.controller, 'image-service')['policy'], self.current['policy'])
        with self.assertRaisesRegex(ValueError, 'different container'):
            prep.remote_plan(self.controller, 'image-other')
        with self.assertRaisesRegex(ValueError, 'different update kind'):
            prep.remote_plan(self.controller, 'packages')

    def test_truncated_malformed_expired_wrong_target_and_argv_receipts_rejected(self):
        bad = [dict(self.current, updates_truncated=True), dict(self.current, status='executing'),
               dict(self.current, expires_at=NOW), dict(self.current, requires_approval=1),
               dict(self.current, argv=['reboot']), dict(self.current, digest='bad')]
        target = copy.deepcopy(self.current)
        target['policy']['target'] = 'home-core'
        target['classification'] = classify_plan(target['policy'])
        bad.append(target)
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): prep.remote_receipt(encoded(value), NOW)
        with self.assertRaises(ValueError): prep.decode(b'{"digest":1,"digest":2}')

    def test_root_override_remains_approval_required(self):
        value = receipt()
        value['requires_approval'] = True
        value['classification'] = dict(value['classification'], approval_required=True, reason='root_plan_requires_approval')
        self.assertTrue(prep.remote_receipt(encoded(value), NOW)['requires_approval'])

    def test_remote_fixed_restart_prepares_fix_requiring_operator_approval(self):
        self.current['policy'] = {'operation': 'fix', 'target': 'home-spark', 'updates': []}
        self.current['classification'] = classify_plan(self.current['policy'])
        plan = prep.remote_plan(self.controller, 'restart-plapre')
        self.assertEqual(plan['policy']['operation'], 'fix')
        self.assertTrue(classify_plan(plan['policy'])['approval_required'])
        self.assertEqual(self.calls[0], prep.ssh('prepare', 'restart-plapre'))
        with self.assertRaises(ValueError): prep.ssh('prepare', 'restart-arbitrary')

    def test_operator_approval_propagates_exact_remote_id_digest_without_execution(self):
        plan = prep.remote_plan(self.controller)
        self.calls.clear()
        with patch.object(prep.os, 'geteuid', return_value=0):
            prep.approve_remote(self.controller, {'plan': prep.canonical(plan)})
        self.assertEqual(self.calls[-1], prep.ssh('approve', 'a' * 32, 'b' * 64))
        self.assertFalse(any(' run ' in argv[-1] for argv in self.calls))

    def test_changed_remote_approval_snapshot_and_forged_transport_rejected(self):
        plan = prep.remote_plan(self.controller)
        self.current['digest'] = 'c' * 64
        with patch.object(prep.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                prep.approve_remote(self.controller, {'plan': prep.canonical(plan)})
        plan['steps'][0][1] = '-oProxyCommand=sh'
        with patch.object(prep.os, 'geteuid', return_value=0):
            with self.assertRaises(ValueError): prep.approve_remote(self.controller, {'plan': prep.canonical(plan)})


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls, self.registered = [], []
        self.controller = SimpleNamespace(uid=0, local_target='home-core', state_dir=Path(self.temp.name),
            clock=lambda: NOW, runner=self.runner, register=self.register)
        self.ha = SimpleNamespace(ENTITIES={'update.home_assistant_core_update'})
        self.core = SimpleNamespace(READLINK='/run/current-system/sw/bin/readlink', PROFILE='/nix/var/nix/profiles/system',
            resolved_system=lambda raw: raw.decode().strip(), prepare=Mock(return_value=({'prepared': 'nixos'}, [])),
            write_plan=self.write)
        self.docker = SimpleNamespace(prepare=self.docker_prepare)
        self.labels = {'com.docker.compose.project.config_files': '/srv/homecompute/releases/test/deploy/homepage/compose.yaml',
                       'com.docker.compose.project.environment_file': '/etc/homecompute/homepage.env'}
        self.ha_bad = False
        self.patches = [patch.object(prep.os, 'geteuid', return_value=0),
                        patch.object(prep, 'trusted_path', side_effect=lambda path, *args, **kwargs: Path(path)),
                        patch.object(prep, 'module', side_effect=lambda kind: {'ha': self.ha, 'core': self.core, 'docker': self.docker}[kind])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write(self, path, plan):
        Path(path).write_text(json.dumps(plan))

    def register(self, path):
        self.registered.append(json.loads(path.read_text()))
        return {'plan_id': 'a' * 32, 'status': 'registered'}

    def docker_prepare(self, container, candidate, files, environment, output, **kwargs):
        self.docker_parameters = (container, candidate, files, environment, kwargs)
        self.write(output, {'prepared': 'docker'})

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] in {prep.HA_COMMAND, prep.CONTAINER_COMMAND}:
            output = Path(argv[-1])
            self.write(output, {'prepared': 'ha'})
            raw = encoded({'plan_file': str(output), 'installed_changes': self.ha_bad})
        elif argv[0] == self.core.READLINK:
            raw = b'/nix/store/already-built-system\n'
        elif argv[1:2] == ['inspect']:
            raw = encoded(self.labels)
        elif argv[1:2] == ['pull']:
            raw = b'staged immutable image\n'
        else:
            raise AssertionError('unexpected preparation command')
        return subprocess.CompletedProcess(argv, 0, raw, b'')

    def test_mcp_preparation_uses_fixed_build_lane_without_running_update(self):
        helper = SimpleNamespace(prepare=Mock(side_effect=lambda controller, component, output:
                                              self.write(output, {'prepared': component})))
        with patch.dict(sys.modules, {'mcp_maintenance': helper}):
            for component in ('aula-mcp', 'tilbudstrolden-mcp'):
                result = prep.prepare(self.controller, 'home-core', component)
                self.assertEqual(result['status'], 'registered')
        self.assertEqual(self.registered, [{'prepared': 'aula-mcp'}, {'prepared': 'tilbudstrolden-mcp'}])
        self.assertEqual(self.calls, [])

    def test_core_nixos_builds_private_candidate_then_registers_without_activation(self):
        helper = SimpleNamespace(prepare=Mock(side_effect=lambda controller, output:
                                              self.write(output, {'prepared': 'nixos'})))
        with patch.dict(sys.modules, {'core_update_build': helper}):
            result = prep.prepare(self.controller, 'home-core', 'nixos')
        self.assertEqual(result['status'], 'registered')
        self.assertEqual(helper.prepare.call_args.args[0], self.controller)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.registered, [{'prepared': 'nixos'}])

    def test_ha_only_prepares_finite_update_entities_with_matching_receipt(self):
        prep.prepare(self.controller, 'home-assistant', 'update.home_assistant_core_update')
        self.assertEqual(self.calls[0][:3], [prep.HA_COMMAND, 'prepare', 'update.home_assistant_core_update'])
        self.assertEqual(self.registered, [{'prepared': 'ha'}])
        self.ha_bad = True
        with self.assertRaisesRegex(ValueError, 'receipt mismatch'):
            prep.prepare(self.controller, 'home-assistant', 'update.home_assistant_core_update')

    def test_core_restart_prepares_only_catalogued_container_without_effect(self):
        prep.prepare(self.controller, 'home-core', 'restart-n8n')
        self.assertEqual(self.calls[0][:3], [prep.CONTAINER_COMMAND, 'prepare', 'homecompute-automation-n8n-1'])
        self.assertFalse(any('run' in argv or 'restart' in argv for argv in self.calls))
        with self.assertRaises(ValueError): prep.prepare(self.controller, 'home-core', 'restart-arbitrary')

    def test_unknown_cross_host_and_nonroot_selectors_create_no_plans(self):
        for target, component in [('home-core', '../nixos'), ('home-assistant', 'update.other'),
                                  ('other-host', 'nixos'), ('home-spark', 'curl'), ('home-core', 'arbitrary')]:
            with self.assertRaises(ValueError): prep.prepare(self.controller, target, component)
        self.controller.local_target = 'home-spark'
        with self.assertRaises(ValueError): prep.prepare(self.controller, 'home-core', 'nixos')
        with patch.object(prep.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError): prep.prepare(self.controller, 'home-spark', 'packages')
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.controller.state_dir.iterdir()), [])

    def test_docker_catalogue_stages_only_observed_digest_from_fixed_repository(self):
        candidate = 'ghcr.io/gethomepage/homepage@sha256:' + 'c' * 64
        with patch.object(prep, 'image_candidate', return_value=candidate), patch.object(prep.shutil, 'which', return_value='/run/current-system/sw/bin/docker'):
            prep.prepare(self.controller, 'home-core', 'homepage')
        self.assertEqual(self.calls[-1], ['/run/current-system/sw/bin/docker', 'pull', candidate])
        container, actual, files, environment, kwargs = self.docker_parameters
        self.assertEqual(container, 'homecompute-homepage-homepage-1')
        self.assertEqual(actual, candidate)
        self.assertEqual(files, [Path('/srv/homecompute/releases/test/deploy/homepage/compose.yaml')])
        self.assertEqual(environment, Path('/etc/homecompute/homepage.env'))
        self.assertEqual(kwargs['target'], 'home-core')

    def test_n8n_existing_tunnel_sources_allowed_and_other_paths_refused(self):
        prefix = '/srv/homecompute/releases/automation-tunnel-20260926/deploy/automation/'
        route = '/srv/state/openclaw-communication/deployment/n8n-private-route.yaml'
        configured = prefix + 'compose.yaml,' + prefix + 'production.yaml,' + route
        self.labels = {'com.docker.compose.project.config_files': configured,
                       'com.docker.compose.project.environment_file': '/etc/homecompute/automation-tunnel.env'}
        files, env = prep.docker_inputs(self.controller, 'n8n', '/run/current-system/sw/bin/docker')
        self.assertEqual([str(path) for path in files], configured.split(','))
        self.assertEqual(str(env), '/etc/homecompute/automation-tunnel.env')
        for bad in [configured.replace('/srv/homecompute/releases/', '/tmp/releases/'),
                    configured.replace('production.yaml', 'other.yaml'), configured + ',/tmp/extra.yaml',
                    configured.replace('n8n-private-route.yaml', 'other-route.yaml')]:
            self.labels['com.docker.compose.project.config_files'] = bad
            with self.assertRaises(ValueError): prep.docker_inputs(self.controller, 'n8n', '/run/current-system/sw/bin/docker')


class ImageEvidenceTest(unittest.TestCase):
    def report(self):
        stamp = datetime.fromtimestamp(NOW, timezone.utc).isoformat()
        return {'schema_version': 1, 'host': 'home-core', 'mode': 'observe-only', 'automatic_actions': False,
                'generated_at': stamp, 'docker_image_updates': {'images': [{'host': 'home-core',
                    'container': 'homecompute-homepage-homepage-1', 'state': 'update_available', 'checked_at': stamp,
                    'image': 'ghcr.io/gethomepage/homepage@sha256:' + 'a' * 64, 'registry': 'ghcr.io',
                    'repository': 'gethomepage/homepage', 'remote_index_digest': 'sha256:' + 'b' * 64}]}}

    def test_fresh_root_report_selects_exact_candidate_and_stale_unknown_refused(self):
        report = self.report()
        with patch.object(prep, 'root_json', return_value=report) as root:
            self.assertEqual(prep.image_candidate('homepage', NOW), 'ghcr.io/gethomepage/homepage@sha256:' + 'b' * 64)
            root.assert_called_once_with(prep.REPORT, 0)
        for mutate in [lambda value: value.update(generated_at=datetime.fromtimestamp(NOW - 86401, timezone.utc).isoformat()),
                       lambda value: value['docker_image_updates']['images'][0].update(state='unknown'),
                       lambda value: value['docker_image_updates']['images'][0].update(repository='other/repo'),
                       lambda value: value['docker_image_updates']['images'][0].update(remote_index_digest='latest')]:
            report = self.report()
            mutate(report)
            with patch.object(prep, 'root_json', return_value=report):
                with self.assertRaises(ValueError): prep.image_candidate('homepage', NOW)


if __name__ == '__main__':
    unittest.main()
