"""Exact Compose preparation never runs effects and conservatively gates state."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('docker_prepare', ROOT / 'scripts/prepare-docker-maintenance.py')
docker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docker)
from infrastructure_maintenance import validate_plan


class DockerPreparationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.compose = self.root / 'compose.yaml'; self.compose.write_text('trusted compose')
        self.env = self.root / 'app.env'; self.env.write_text('trusted env')
        self.old = 'ghcr.io/gethomepage/homepage@sha256:' + 'a' * 64
        self.new = 'ghcr.io/gethomepage/homepage@sha256:' + 'b' * 64
        self.calls = []
        self.container = {'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {'Image': self.old, 'Labels': {'com.docker.compose.project': 'app',
                'com.docker.compose.service': 'app', 'com.docker.compose.project.config_files': str(self.compose)}},
            'Image': 'sha256:' + 'c' * 64, 'Mounts': []}
        self.candidate_version = '1.2.4'

    def tearDown(self): self.tmp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1:3] == ['image', 'inspect']:
            new = argv[-1] == self.new
            data = [{'Config': {'Labels': {'org.opencontainers.image.version': self.candidate_version if new else '1.2.3',
                                         'org.opencontainers.image.source': 'https://github.com/gethomepage/homepage'}},
                     'Os': 'linux', 'Architecture': 'arm64', 'RepoDigests': [self.new if new else self.old]}]
        elif argv[1] == 'inspect' and '--format' not in argv:
            data = [self.container]
        elif 'compose' in argv:
            data = {'services': {'app': {'image': self.old}}}
        else:
            return subprocess.CompletedProcess(argv, 0, b'invariant evidence\n', b'')
        return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'')

    def prepare(self):
        with patch.object(docker, 'trusted_path', side_effect=lambda p, *_args, **_kwargs: Path(p)), \
             patch.object(docker.shutil, 'which', side_effect=lambda name, **_kwargs: '/usr/bin/' + name):
            docker.prepare('app-1', self.new, [self.compose], self.env, self.root / 'plan.json',
                           target='home-core', runner=self.runner, clock=lambda: 1000, uid=os.geteuid())
        return json.loads((self.root / 'plan.json').read_text())

    def test_minor_stateless_pinned_image_plan_prepared_without_effects(self):
        plan = self.prepare()
        self.assertFalse(validate_plan(plan)['approval_required'])
        self.assertEqual(plan['policy']['updates'][0]['candidate'], '1.2.4')
        self.assertIn('--no-deps', plan['steps'][0])
        self.assertFalse(any('up' in argv or 'pull' in argv for argv in self.calls))
        self.assertEqual(len(plan['prechecks']), 3)
        self.assertEqual(plan['postchecks'][0]['argv'][-1], 'app-1')

    def test_stateful_service_forces_exact_review(self):
        self.container['Mounts'] = [{'Type': 'volume', 'RW': True}]
        self.assertTrue(validate_plan(self.prepare())['approval_required'])

    def test_unknown_or_major_image_version_requires_approval(self):
        for version in ['unknown', '2.0.0']:
            with self.subTest(version=version):
                self.candidate_version = version
                plan = self.prepare()
                self.assertTrue(validate_plan(plan)['approval_required'])
                (self.root / 'plan.json').unlink(); (self.root / 'plan.image.json').unlink()

    def test_unhealthy_baseline_cannot_be_disguised_as_routine_update(self):
        self.container['State']['Health']['Status'] = 'unhealthy'
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse((self.root / 'plan.json').exists())


if __name__ == '__main__': unittest.main()
