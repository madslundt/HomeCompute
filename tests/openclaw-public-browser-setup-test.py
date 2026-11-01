"""Public browsing upgrades preserve native ownership and unrelated grants."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import shutil
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_browser', ROOT / 'scripts/setup-openclaw-public-browser.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublicBrowserSetup(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'Root-only module staging requires Linux/root qualification')
    def test_root_module_staging_is_fenced_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            root = base / 'state'
            guard = root / 'extensions/homecompute-safety'
            guard.mkdir(parents=True)
            dns = base / 'dns/public-dns.mjs'
            dns.parent.mkdir()
            source = base / 'release'
            originals = source / 'deploy/openclaw/safety-plugin'
            originals.mkdir(parents=True)
            for name in ('index.mjs', 'public-dns.mjs', 'browser-schema.mjs'):
                (originals / name).write_text('reviewed-' + name)
            for path in (guard / 'index.mjs', guard / 'public-dns.mjs', dns):
                path.write_text('previous')
                path.chmod(0o555)
            old = hashlib.sha256(b'previous').hexdigest()
            with patch.object(module, 'NATIVE_ROOT', root), patch.object(module, 'DNS_MODULE', dns):
                with self.assertRaises(ValueError):
                    module.stage_modules(root, source, expected_guard_sha256='changed', expected_dns_sha256=old)
                self.assertEqual((guard / 'index.mjs').read_text(), 'previous')
                result = module.stage_modules(root, source, expected_guard_sha256=old, expected_dns_sha256=old)
                try:
                    paths = json.loads((Path(result['backup']) / 'paths.json').read_text())
                    self.assertIn(str(guard / 'index.mjs'), paths)
                    self.assertEqual((Path(result['backup']) / str(paths.index(str(guard / 'index.mjs')))).read_text(), 'previous')
                    self.assertEqual(os.stat(result['backup']).st_mode & 0o777, 0o700)
                    for path in (guard / 'index.mjs', guard / 'public-dns.mjs', dns):
                        self.assertEqual(path.stat().st_mode & 0o777, 0o555)
                        self.assertTrue(path.read_text().startswith('reviewed-'))
                    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (guard / 'index.mjs', dns)]
                    again = module.stage_modules(root, source, expected_guard_sha256=hashes[0], expected_dns_sha256=hashes[1])
                    self.assertEqual(again['changed_files'], 0)
                finally:
                    shutil.rmtree(result['backup'])

    def fixture(self):
        return {'browser': {'enabled': True, 'evaluateEnabled': False, 'defaultProfile': 'homecompute',
                'ssrfPolicy': {'dangerouslyAllowPrivateNetwork': False, 'allowedHostnames': ['127.0.0.1', 'example.com']},
                'profiles': {'homecompute': {'attachOnly': True, 'cdpUrl': 'http://127.0.0.1:18800/?token=' + 'q' * 48}}},
                'plugins': {'entries': {'homecompute-safety': {'enabled': True}}},
                'agents': {'entries': {'researcher': {'tools': {'allow': ['browser']}},
                                       'family': {'tools': {'deny': ['browser']}}}},
                'gateway': {'auth': {'token': 'synthetic-private'}}, 'models': {'unchanged': True}}

    def test_upgrade_changes_only_policy_and_is_idempotent(self):
        before = self.fixture()
        original = copy.deepcopy(before)
        after = module.candidate(before)
        expected = copy.deepcopy(before)
        expected['browser']['ssrfPolicy'] = {'allowedHostnames': ['127.0.0.1']}
        self.assertEqual(after, expected)
        self.assertEqual(before, original)
        self.assertEqual(module.candidate(after), after)

    def test_private_opt_in_unknown_policy_and_profile_drift_refused(self):
        for key, value in [('dangerouslyAllowPrivateNetwork', True), ('allowedHostnames', ['*']),
                           ('hostnameAllowlist', ['restricted.com']), ('blockedHostnames', ['blocked.com'])]:
            before = self.fixture()
            before['browser']['ssrfPolicy'][key] = value
            with self.assertRaises(ValueError): module.candidate(before)
        for url in ['http://127.0.0.1:18800/?token=short', 'http://10.0.0.1:18800/?token=' + 'q' * 48,
                    'http://127.0.0.1:18800/?token=' + 'q' * 48 + '&extra=1']:
            before = self.fixture()
            before['browser']['profiles']['homecompute']['cdpUrl'] = url
            with self.assertRaises(ValueError): module.candidate(before)

    def test_dry_run_apply_digest_fence_backup_and_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder).resolve() / 'openclaw.json'
            config.write_text(json.dumps(self.fixture()))
            config.chmod(0o600)
            raw = config.read_bytes()
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                self.assertEqual(kwargs['env']['OPENCLAW_CONFIG_PATH'], str(config))
                if '--dry-run' not in command:
                    config.write_text(json.dumps(module.candidate(json.loads(config.read_bytes()))))
                return types.SimpleNamespace(returncode=0)

            result = module.setup(config, ROOT, runner=runner)
            self.assertFalse(result['applied'])
            self.assertEqual(config.read_bytes(), raw)
            with self.assertRaises(ValueError): module.setup(config, ROOT, apply=True, runner=runner)
            with self.assertRaises(ValueError):
                module.setup(config, ROOT, apply=True, expected_config_sha256='changed', runner=runner)
            with patch.object(module, 'protected_modules') as guard:
                applied = module.setup(config, ROOT, apply=True,
                                       expected_config_sha256=result['source_config_sha256'], runner=runner)
                guard.assert_called_once_with(ROOT, config.parent)
            self.assertTrue(applied['applied'])
            self.assertEqual(Path(applied['backup']).read_bytes(), raw)
            self.assertEqual(os.stat(applied['backup']).st_mode & 0o777, 0o600)
            self.assertEqual(sum('--dry-run' not in cmd for cmd in calls), 1)

    def test_failed_native_validation_does_not_change_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder).resolve() / 'openclaw.json'
            config.write_text(json.dumps(self.fixture()))
            config.chmod(0o600)
            original = config.read_bytes()
            with self.assertRaises(ValueError):
                module.setup(config, ROOT, runner=lambda *a, **k: types.SimpleNamespace(returncode=1))
            self.assertEqual(config.read_bytes(), original)


if __name__ == '__main__': unittest.main()
