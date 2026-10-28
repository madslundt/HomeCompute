#!/usr/bin/env python3
"""Stable upstream selection and finite read-only check boundaries."""
import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from homepage_update_check import Checker
from homepage_maintenance import Denied
from openclaw_maintenance import validate

OLD = 'ghcr.io/gethomepage/homepage@sha256:' + '1' * 64
NEW = 'ghcr.io/gethomepage/homepage@sha256:' + '2' * 64


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.executor = Mock()
        self.executor.paths = SimpleNamespace(state=root, candidate=root / 'candidate')
        self.executor.paths.candidate.write_text('{}')
        self.executor.trusted.side_effect = lambda p: p
        self.executor.write.side_effect = lambda p, value: p.write_text(json.dumps(value))
        self.executor.pin.side_effect = lambda p: p
        self.executor.inspect.side_effect = lambda kind, _: ({'Config': {'Image': OLD}, 'Image': 'sha256:' + '1' * 64}
                if kind == 'container' else {'Config': {'Labels': {'org.opencontainers.image.version': 'v2.4.0'}}, 'Os': 'linux', 'Architecture': 'amd64'})
        self.executor.locked.side_effect = contextlib.nullcontext
        self.release('v2.5.0')
        self.executor.registry.platform.side_effect = lambda ref, target, *args: {
            'index_digest': 'sha256:' + ('1' if target.startswith('sha256:') else '2') * 64,
            'platform_digest': 'sha256:' + ('1' if target.startswith('sha256:') else '2') * 64,
            'config_digest': 'sha256:' + '3' * 64}
        self.job = {'id': 'a' * 32, 'proposal_hash': 'b' * 64, 'status': 'prepared', 'events': [{'time': 123}],
                    'proposal': {'baseline': {'image': OLD}, 'candidate_image': NEW,
                                 'review': {'release_notes': 'https://github.com/gethomepage/homepage/releases/tag/v2.5.0'}, 'expires_at': 4000}}
        def prepared():
            self.job['proposal']['review'] = json.loads(self.executor.paths.candidate.read_text())
            return self.job
        self.executor.prepare.side_effect = prepared
        self.checker = Checker(self.executor, root / 'status.json', clock=lambda: 123)

    def tearDown(self):
        self.temp.cleanup()

    def release(self, version, **extra):
        release = {'tag_name': version, 'html_url': 'https://github.com/gethomepage/homepage/releases/tag/' + version,
                   'draft': False, 'prerelease': False, **extra}
        self.executor.registry.http.return_value = (200, {}, json.dumps(release).encode())

    def test_prepares_exact_platform_without_docker_mutation(self):
        report = validate(self.checker.check())
        candidate = json.loads(self.executor.paths.candidate.read_text())
        self.assertEqual(candidate['candidate_image'], NEW)
        self.assertEqual(report['status'], 'prepared')
        self.executor.run.assert_not_called()
        self.executor.approve.assert_not_called()
        self.executor.execute.assert_not_called()
        self.assertEqual(self.executor.registry.http.call_args.args[:2], ('api.github.com', '/repos/gethomepage/homepage/releases/latest'))

    def test_major_change_and_downgrade_need_manual_review(self):
        for version in ('v3.0.0', 'v2.3.0'):
            self.release(version)
            self.assertEqual(self.checker.check()['status'], 'manual_review_required')
        self.executor.prepare.assert_not_called()
        self.assertEqual(self.executor.paths.candidate.read_text(), '{}')

    def test_untrusted_release_fields_fail_before_proposal(self):
        for version, extra in [('latest', {}), ('v2.5.0', {'draft': True}), ('v2.5.0', {'prerelease': True}),
                               ('v2.5.0', {'html_url': 'https://evil.invalid/'})]:
            self.release(version, **extra)
            with self.assertRaises(Denied): self.checker.check()
        self.executor.prepare.assert_not_called()

    def test_same_platform_does_not_prepare(self):
        self.executor.registry.platform.side_effect = None
        self.executor.registry.platform.return_value = {'index_digest': 'sha256:' + '1'*64, 'platform_digest': 'sha256:' + '1'*64}
        self.assertEqual(self.checker.check()['status'], 'up_to_date')
        self.executor.prepare.assert_not_called()

    def test_uncertain_attempt_is_reported_before_check(self):
        (self.executor.paths.state / ('a'*32 + '.json')).write_text('{}')
        self.job['status'] = 'reconciliation_required'
        self.executor.load.return_value = self.job
        self.assertEqual(self.checker.check()['status'], 'reconciliation_required')
        self.executor.inspect.assert_not_called()
        self.executor.prepare.assert_not_called()

    def test_projection_matches_effects_and_keeps_unknown_state_unknown(self):
        self.job['status'] = 'succeeded'
        self.assertEqual(validate(self.checker.project(self.job))['current_image'], NEW)
        self.job['status'] = 'reconciliation_required'
        self.assertNotIn('current_image', validate(self.checker.project(self.job)))
        self.assertEqual(self.checker.project(self.job)['checked_at'], 123)

    def test_service_never_executes_or_accepts_request_arguments(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/setup-homepage-maintenance.py'
        spec = importlib.util.spec_from_file_location('setup_homepage', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        unit = module.service()
        self.assertIn('homepage_update_check.py check\n', unit)
        self.assertNotIn('execute', unit)
        self.assertNotIn('sudo', unit)
        self.assertIn('CapabilityBoundingSet=\n', unit)
        self.assertIn('PathChanged=', module.path_unit())
        deployment = (path.parent / 'deploy-home-core.sh').read_text()
        self.assertLess(deployment.index('active-image.json'), deployment.index('nixos-rebuild build'))


if __name__ == '__main__':
    unittest.main()
