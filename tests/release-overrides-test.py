"""Published pins and exact maintenance provenance fence guard retirement."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reconcile_overrides', ROOT / 'scripts/reconcile-release-overrides.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.state = self.root / 'release', self.root / 'state'
        self.docker = self.state / 'homecompute-docker-maintenance'
        self.homepage = self.state / 'homecompute/homepage-maintenance'
        self.docker.mkdir(parents=True)
        self.homepage.mkdir(parents=True)
        for p in [self.state / 'homecompute/deploy.lock', self.docker / 'operation.lock']:
            p.touch()
        self.rows, self.images = {}, {}
        self.runtime = self.root / 'mattermost-runtime.env'
        runtime_patch = patch.object(module, 'MATTERMOST_ENV', self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        for i, (name, (env, key)) in enumerate(module.TARGETS.items(), 1):
            image = 'sha256:' + str(i) * 64
            pin = 'example.invalid/' + name + ':1.2.3@' + image
            path = self.source / env
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(key + '=' + pin + '\n')
            self.images[pin] = {'Id': image}
            self.rows[name] = {'Id': str(i) * 64, 'Image': image,
                               'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                               'Config': {'Image': pin}, 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}, 'Mounts': []}
            if 'mattermost' in name:
                self.runtime.write_text('RETAINED_SETTING=value\nMATTERMOST_IMAGE=' + pin + '\n')
        services = self.source / 'deploy/homepage/config/services.yaml'
        services.parent.mkdir(parents=True)
        services.write_text('- Helpers: []\n')
        (self.homepage / 'services.yaml').write_bytes(services.read_bytes())
        override = {'services': {'homepage': {'volumes': [{'type': 'bind', 'source': str(self.homepage / 'services.yaml'),
                     'target': '/app/config/services.yaml', 'read_only': True}]}}}
        (self.homepage / 'active-config.json').write_text(json.dumps(override))
        self.rows['homecompute-homepage-homepage-1']['Mounts'] = [{'Type': 'bind', 'Source': str(self.homepage / 'services.yaml'),
                    'Destination': '/app/config/services.yaml', 'RW': False}]
        current = self.rows['homecompute-automation-n8n-1']['Image']
        self.marker('a' * 32, 'sha256:' + 'a' * 64, 'sha256:' + '0' * 64)
        self.marker('b' * 32, current, 'sha256:' + 'a' * 64)

    def marker(self, identity, image, old, container='homecompute-automation-n8n-1'):
        candidate = 'example.invalid/n8n:1.2.3@' + image
        row = {'id': identity, 'host': 'home-core', 'status': 'complete', 'container': container,
               'candidate': candidate, 'candidate_image': image, 'old': {'Image': old}}
        (self.docker / (identity + '.json')).write_text(json.dumps(row))
        (self.docker / (identity + '.active')).write_text(json.dumps({'container': row['container'], 'candidate': candidate}))

    def inspect(self, kind, name):
        return copy.deepcopy((self.images if kind == 'image' else self.rows)[name])

    def plan(self):
        return module.plan(self.source, self.state, self.inspect, os.getuid())

    def apply(self, digest):
        return module.apply(self.source, digest, self.state, self.inspect, os.getuid())

    def test_exact_source_and_supersession_chain_archive_only_guards(self):
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        plan = self.plan()
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        result = self.apply(plan['plan_sha256'])
        self.assertEqual(result['archived'], 3)
        self.assertFalse(result['containers_changed'])
        for name in ['a' * 32, 'b' * 32]:
            self.assertEqual((self.docker / (name + '.json')).read_bytes(), before[self.docker / (name + '.json')])
            self.assertFalse((self.docker / (name + '.active')).exists())
        self.assertEqual(len(list(Path(result['receipt']).parent.rglob('*.active'))), 2)
        self.assertEqual(self.apply(self.plan()['plan_sha256'])['archived'], 0)

    def test_stale_review_preserves_guards(self):
        plan = self.plan()
        self.rows['homecompute-automation-n8n-1']['Config']['setting'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'plan changed'):
            self.apply(plan['plan_sha256'])
        self.assertTrue((self.docker / ('a' * 32 + '.active')).exists())

    def test_runtime_pin_reconciliation_preserves_other_bytes_and_backup(self):
        old = 'example.invalid/mattermost:1.2.2@sha256:' + '4' * 64
        self.images[old] = {'Id': 'sha256:' + '4' * 64}
        self.runtime.write_text('RETAINED_SETTING=value\nMATTERMOST_IMAGE=' + old + '\n')
        before = self.runtime.read_bytes()
        self.marker('c' * 32, 'sha256:' + '3' * 64, 'sha256:' + '4' * 64, 'homecompute-mattermost-mattermost-1')
        reviewed = self.plan()
        result = self.apply(reviewed['plan_sha256'])
        self.assertTrue(result['mattermost_runtime_pin_reconciled'])
        self.assertEqual(self.runtime.read_bytes(), module.image_environment(before, reviewed['runtime_update']['image'])[1])
        self.assertEqual((Path(result['receipt']).parent / 'mattermost-runtime.before').read_bytes(), before)

    def test_runtime_pin_is_restored_when_guard_archival_fails(self):
        old = 'example.invalid/mattermost:1.2.2@sha256:' + '4' * 64
        self.images[old] = {'Id': 'sha256:' + '4' * 64}
        self.runtime.write_text('RETAINED_SETTING=value\nMATTERMOST_IMAGE=' + old + '\n')
        before = self.runtime.read_bytes()
        self.marker('c' * 32, 'sha256:' + '3' * 64, 'sha256:' + '4' * 64, 'homecompute-mattermost-mattermost-1')
        reviewed = self.plan()
        original = Path.rename
        def interrupted(path, target):
            if path == self.docker / ('b' * 32 + '.active'):
                raise OSError('Synthetic rename failure')
            return original(path, target)
        with patch.object(Path, 'rename', interrupted), self.assertRaises(OSError):
            self.apply(reviewed['plan_sha256'])
        self.assertEqual(self.runtime.read_bytes(), before)

    def test_unhealthy_or_different_image_refuses_retirement(self):
        for mutation in ['health', 'image']:
            row = copy.deepcopy(self.rows['homecompute-automation-n8n-1'])
            if mutation == 'health':
                self.rows['homecompute-automation-n8n-1']['State']['Health']['Status'] = 'unhealthy'
            else:
                self.rows['homecompute-automation-n8n-1']['Image'] = 'sha256:' + 'f' * 64
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'healthy running container'):
                self.plan()
            self.rows['homecompute-automation-n8n-1'] = row

    def test_uncertain_record_and_unknown_guard_are_not_retired(self):
        path = self.docker / ('a' * 32 + '.json')
        record = json.loads(path.read_text())
        record['status'] = 'uncertain'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'completed matching'):
            self.plan()
        (self.docker / ('0' * 32 + '.active')).write_text(json.dumps({'container': 'unreviewed', 'candidate': 'x'}))
        with self.assertRaisesRegex(ValueError, 'unknown maintenance'):
            self.plan()

    def test_unproven_supersession_is_not_archived(self):
        path = self.docker / ('b' * 32 + '.json')
        record = json.loads(path.read_text())
        record['old']['Image'] = 'sha256:' + 'c' * 64
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'superseded marker'):
            self.plan()

    def test_homepage_drift_or_unreviewed_mount_is_not_retired(self):
        self.rows['homecompute-homepage-homepage-1']['Mounts'][0]['RW'] = True
        with self.assertRaisesRegex(ValueError, 'read-only Homepage'):
            self.plan()
        self.rows['homecompute-homepage-homepage-1']['Mounts'][0]['RW'] = False
        (self.homepage / 'services.yaml').write_text('Pending operator changes\n')
        with self.assertRaisesRegex(ValueError, 'match published source'):
            self.plan()

    def test_symlink_input_is_refused(self):
        path = self.homepage / 'services.yaml'
        path.unlink()
        path.symlink_to(self.source / 'deploy/homepage/config/services.yaml')
        with self.assertRaisesRegex(ValueError, 'protected bounded'):
            self.plan()

    def test_failed_archive_restores_preimages(self):
        plan = self.plan()
        original = Path.rename
        def interrupted(path, target):
            if path == self.docker / ('b' * 32 + '.active'):
                raise OSError('Synthetic rename failure')
            return original(path, target)
        with patch.object(Path, 'rename', interrupted), self.assertRaises(OSError):
            self.apply(plan['plan_sha256'])
        self.assertTrue((self.docker / ('a' * 32 + '.active')).exists())
        self.assertTrue((self.docker / ('b' * 32 + '.active')).exists())
        self.assertTrue((self.homepage / 'active-config.json').exists())

    def test_container_change_at_retirement_boundary_restores_guards(self):
        plan = self.plan()
        reads = 0
        def changing(kind, name):
            nonlocal reads
            row = self.inspect(kind, name)
            if kind == 'container' and name == 'homecompute-automation-n8n-1':
                reads += 1
                if reads == 2:
                    row['Config']['changed'] = True
            return row
        with self.assertRaisesRegex(ValueError, 'live container changed'):
            module.apply(self.source, plan['plan_sha256'], self.state, changing, os.getuid())
        self.assertTrue((self.docker / ('a' * 32 + '.active')).exists())
        self.assertTrue((self.homepage / 'active-config.json').exists())


if __name__ == '__main__':
    unittest.main()
