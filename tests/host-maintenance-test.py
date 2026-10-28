#!/usr/bin/env python3
"""Registry proof, complete coverage and durable routine-only deployment gates."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import host_maintenance as host
import host_maintenance_sources as sources

OLD = {'index_digest': 'sha256:' + 'a' * 64, 'platform_digest': 'sha256:' + 'b' * 64,
       'config_digest': 'sha256:' + 'c' * 64}
NEW = {key: 'sha256:' + character * 64 for key, character in
       [('index_digest', 'd'), ('platform_digest', 'e'), ('config_digest', 'f')]}


def inventory():
    return {'containers': [{'container': 'service', 'image': 'docker.io/library/example:1.2.3',
        'image_id': OLD['config_digest'], 'repo_digests': ['docker.io/library/example@' + OLD['index_digest']],
        'os': 'linux', 'architecture': 'amd64'}]}


def policy(lane='registry'):
    return {'schema_version': 1, 'containers': {'service': {'source_lane': lane, 'tracked_tag': 'latest'}}}


class DiscoveryTest(unittest.TestCase):
    def discover(self, snapshot=None, tracking=None, tags=None):
        registry = Mock()
        registry.platform.side_effect = lambda ref, tag, *platform: OLD if tag == '1.2.3' else NEW
        with patch.object(sources, 'image_tags', return_value=tags or ['1.2.3']):
            rows = sources.discover('home-core', tracking or policy(), inventory=snapshot or inventory(),
                                    registry_factory=lambda **kwargs: registry)
        return rows, registry

    def test_current_requires_matching_daemon_identity_repository_and_pin(self):
        rows, registry = self.discover()
        self.assertEqual(rows[0]['status'], 'current')
        registry.platform.assert_called_once()
        for mutation in ({'image_id': 'sha256:' + '9' * 64},
                         {'repo_digests': ['docker.io/library/unrelated@' + OLD['index_digest']]},
                         {'image': 'docker.io/library/example:1.2.3@sha256:' + '9' * 64},
                         {'repo_digests': []}):
            snapshot = inventory(); snapshot['containers'][0].update(mutation)
            rows, _ = self.discover(snapshot)
            self.assertEqual(rows[0]['status'], 'review')
            self.assertEqual(rows[0]['reason'], 'image_provenance_unavailable')

    def test_rebuilt_tag_and_source_policy_are_reviewed_before_replacement(self):
        snapshot = inventory()
        snapshot['containers'][0]['repo_digests'] = ['docker.io/library/example@sha256:' + '9' * 64]
        rows, _ = self.discover(snapshot, tags=['1.2.3', '1.3.0'])
        self.assertEqual(rows[0]['status'], 'review')
        for tracking, reason in ((policy('source_pin'), 'source_pin_requires_review'),
                                 ({'schema_version': 1, 'containers': {}}, 'image_tracking_unavailable')):
            rows, registry = self.discover(tracking=tracking, tags=['1.2.3', '1.3.0'])
            self.assertEqual(rows[0]['reason'], reason)
            self.assertEqual(rows[0]['status'], 'review')
            registry.platform.assert_not_called()

    def test_newer_candidates_retain_independent_version_and_manifest_evidence(self):
        rows, registry = self.discover(tags=['1.2.3', '1.9.0', '2.0.0'])
        row = rows[0]
        self.assertEqual((row['candidate'], row['major_candidate']), ('1.9.0', '2.0.0'))
        self.assertTrue(row['candidate_ref'].endswith(':1.9.0@' + NEW['index_digest']))
        self.assertEqual(row['evidence'], {'installed_tag': '1.2.3', 'candidate_tag': '1.9.0'})
        self.assertEqual(registry.platform.call_count, 2)
        public = host.public_item(dict(row, token='PRIVATE_TOKEN', environment={'secret': 'PRIVATE'}))
        self.assertFalse({'candidate_ref', 'evidence', 'token', 'environment'} & public.keys())

    def test_every_actual_container_has_a_result_including_new_and_custom_sources(self):
        snapshot = inventory()
        snapshot['containers'].extend([{'container': 'custom', 'image': 'homecompute/custom:source'},
                                       {'container': 'service-before-' + 'a' * 12}])
        rows, _ = self.discover(snapshot)
        self.assertEqual({row['component'] for row in rows}, {row['container'] for row in snapshot['containers']})
        self.assertEqual([row['status'] for row in rows], ['current', 'review', 'delegated'])

    def test_selection_preserves_variant_and_separates_unstable_major(self):
        _, routine, major = sources.choose_tags('1.2.3-alpine',
            ['1.2.3-alpine', '1.4.0-alpine', '2.0.0-alpine', '9.0.0-debian', '1.9.0-rc1-alpine'])
        self.assertEqual((routine['tag'], major['tag']), ('1.4.0-alpine', '2.0.0-alpine'))
        _, routine, major = sources.choose_tags('0.2.3', ['0.2.4', '0.3.0'])
        self.assertEqual((routine['version'], major['version']), ('0.2.4', '0.3.0'))
        for installed, tags in [('latest', []), ('1.2.3', ['1.2.3'] * (sources.MAX_TAGS + 1))]:
            with self.assertRaises(ValueError): sources.choose_tags(installed, tags)

    def test_moving_minor_aliases_cannot_prove_installed_or_candidate_patch_version(self):
        for tag in ('3.12', '3.12-slim', '0.11', 'v0.11'):
            self.assertIsNone(sources.version_tag(tag))
            snapshot = inventory()
            snapshot['containers'][0]['image'] = 'docker.io/library/example:' + tag
            rows, registry = self.discover(snapshot, tags=[tag, '3.12.1', '0.11.1'])
            self.assertEqual((rows[0]['status'], rows[0]['reason']), ('review', 'image_version_unproven'))
            registry.platform.assert_not_called()
        _, routine, major = sources.choose_tags('3.11.9', ['3.12', '3.12.0', '3.13'])
        self.assertEqual(routine['tag'], '3.12.0')
        self.assertIsNone(major)
        _, routine, major = sources.choose_tags('3.11.9', ['3.12', '3.13'])
        self.assertIsNone(routine)
        self.assertIsNone(major)

    def test_tag_pagination_is_complete_bounded_and_cannot_select_another_host(self):
        prefix = '/v2/library/example/tags/list'
        registry = Mock(tokens={('docker.io', 'library/example'): 'anonymous-token'})
        def page(tags, link=None):
            return (200, {'link': link} if link else {}, json.dumps({'name': 'library/example', 'tags': tags}).encode())
        registry.request.side_effect = [page(['1.2.3'], '<' + prefix + '?last=1.2.3>; rel="next"'), page(['1.3.0'])]
        self.assertEqual(sources.image_tags(registry, sources.reference('example:1.2.3')), ['1.2.3', '1.3.0'])
        self.assertEqual(registry.request.call_args.args[:2], ('registry-1.docker.io', prefix + '?last=1.2.3'))
        for link in ('<https://private.invalid' + prefix + '>; rel="next"',
                     '<' + prefix + '?n=1000>; rel="next"'):
            registry.request.side_effect = None; registry.request.return_value = page(['1.2.3'], link)
            with self.assertRaises(ValueError): sources.image_tags(registry, sources.reference('example:1.2.3'))
        registry.request.side_effect = None
        registry.request.return_value = page(['1.2.3'] * (sources.MAX_TAGS + 1))
        with self.assertRaises(ValueError): sources.image_tags(registry, sources.reference('example:1.2.3'))
        registry.request.reset_mock()
        registry.request.side_effect = lambda *args: page(['1.2.3'], '<' + prefix + '?last=' + str(registry.request.call_count) + '>; rel="next"')
        with self.assertRaises(ValueError): sources.image_tags(registry, sources.reference('example:1.2.3'))
        self.assertEqual(registry.request.call_count, 12)


class SchedulerTest(unittest.TestCase):
    def controller(self, classification='routine', approval=False, result='succeeded'):
        return SimpleNamespace(register=Mock(return_value={'plan_id': 'p', 'policy': {'updates': []},
            'classification': {'classification': classification, 'approval_required': classification in {'major', 'review'}},
            'requires_approval': approval}), run=Mock(return_value={'status': result}),
            runner=Mock(return_value=subprocess.CompletedProcess([], 0)), clock=lambda: 1000)

    def test_only_routine_unheld_plan_executes_and_marker_precedes_effect(self):
        for classification, approval, expected in [('major', False, 'approval_required'),
                ('review', False, 'approval_required'), ('unchanged', False, 'review'),
                ('routine', True, 'approval_required'), ('routine', False, 'succeeded')]:
            controller, writes, attempts = self.controller(classification, approval), [], {}
            def write(path, value): writes.append(copy.deepcopy(value))
            def run(plan):
                self.assertEqual(writes[0]['service']['status'], 'executing')
                return {'status': 'succeeded'}
            controller.run.side_effect = run
            with patch.object(host, 'write', side_effect=write):
                row = host.execute(controller, Path('plan'), 'service', 'docker', attempts)
            self.assertEqual(row['status'], expected)
            self.assertEqual(controller.run.call_count, int(expected == 'succeeded'))

    def test_failed_uncertain_and_interrupted_attempts_cannot_execute_again(self):
        for result in ('failed', 'uncertain', 'executing'):
            controller, attempts = self.controller(result=result), {}
            with patch.object(host, 'write'):
                host.execute(controller, Path('plan'), 'service', 'docker', attempts)
                self.assertEqual(host.execute(controller, Path('fresh-plan'), 'service', 'docker', attempts)['status'], 'blocked')
            self.assertEqual(controller.run.call_count, 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(host, 'STATE', Path(directory)), \
             patch.object(host, 'trusted_path'):
            controller = self.controller(); controller.run.side_effect = SystemExit('simulated process loss')
            with self.assertRaises(SystemExit): host.execute(controller, Path('plan'), 'service', 'docker', {})
            attempts = json.loads((Path(directory) / 'attempts.json').read_text())
            self.assertEqual(attempts['service']['status'], 'executing')
            self.assertEqual(host.execute(controller, Path('fresh-plan'), 'service', 'docker', attempts)['status'], 'blocked')
            self.assertEqual(controller.run.call_count, 1)

    def test_all_failure_paths_retain_independent_tool_coverage(self):
        core = host.package_rows('home-core', self.controller(), {'nixos': {'status': 'uncertain'}}, False)
        self.assertEqual({row['component'] for row in core}, {'nixos', 'agent-imperative-profile', 'source-pinned-tools'})
        self.assertEqual(core[0]['status'], 'blocked')
        for cause in ('metadata', 'solver', 'module'):
            controller = self.controller()
            helper = SimpleNamespace(prepare=Mock(side_effect=ValueError('PRIVATE solver error')))
            if cause == 'metadata': controller.runner.return_value = subprocess.CompletedProcess([], 1)
            with patch.object(host, 'module', side_effect=OSError('PRIVATE') if cause == 'module' else None, return_value=helper):
                rows = host.package_rows('home-spark', controller, {}, False)
            self.assertEqual({row['component'] for row in rows}, {'packages', 'snap', 'user-python-tools'})
            self.assertEqual(rows[0]['status'], 'failed')
            self.assertNotIn('PRIVATE', json.dumps(rows))

    def test_spark_full_protected_plan_is_registered_and_never_executed(self):
        controller = self.controller()
        helper = SimpleNamespace(prepare=Mock(side_effect=[ValueError('no routine package updates'), ({'protected': True}, ['nvidia-driver'])]),
                                 write_plan=Mock())
        with patch.object(host, 'module', return_value=helper), patch.object(host, 'output_path', return_value=Path('plan')):
            rows = host.package_rows('home-spark', controller, {}, False)
        self.assertEqual(rows[0]['status'], 'approval_required')
        controller.register.assert_called_once(); controller.run.assert_not_called()


if __name__ == '__main__': unittest.main()
