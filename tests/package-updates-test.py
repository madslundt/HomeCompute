"""Package metadata projections and isolated APT configuration; no installs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('packages', ROOT / 'scripts/package_updates.py')
packages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packages)


class PackageTests(unittest.TestCase):
    def source(self, directory: Path, extra: str = '') -> None:
        directory.mkdir(exist_ok=True)
        (directory / 'sources.list').write_text('deb https://archive.ubuntu.com/ubuntu noble main\n' + extra)

    def fixture(self, directory: Path, candidates: list[dict] | None = None, extra: str = '') -> dict:
        apt_dir, run_dir = directory / 'apt', directory / 'run'
        self.source(apt_dir, extra)
        run_dir.mkdir(exist_ok=True)
        rows = candidates if candidates is not None else [{'name': 'openssl', 'installed': '3.0.1', 'candidate': '3.0.2', 'security': True}]
        with patch.object(packages.subprocess, 'check_output', return_value='arm64\n'):
            return packages.apt_report(apt_dir=apt_dir, run_dir=run_dir, reader=lambda config: (rows, 150), refresher=lambda config: None,
                                       resolver=lambda config, candidates: dict(eligible_count=len(candidates), phased_count=0, deferred_count=0))

    def test_public_sources_reject_credentials_private_urls_and_trust_bypass(self):
        for uri in ('https://token@archive.ubuntu.com/ubuntu', 'https://archive.ubuntu.com/x?key=secret',
                    'http://169.254.169.254/latest', 'http://10.0.0.1/repo', 'https://unapproved.invalid/x'):
            self.assertFalse(packages.public_uri(uri))
        for line in ('deb [trusted=yes] https://archive.ubuntu.com/ubuntu noble main',
                     'deb [signed-by=/run/secrets/worker_token] https://archive.ubuntu.com/ubuntu noble main'):
            rows, skipped = packages.source_lines(line, False)
            self.assertEqual((rows, skipped), ([], 1))

    def test_deb822_projects_only_finite_signed_fields_and_security_pocket(self):
        source = 'Types: deb deb-src\nURIs: https://archive.ubuntu.com/ubuntu\nSuites: noble noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\nArchitectures: amd64 arm64\n'
        rows, skipped = packages.source_lines(source, True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped, 0)
        self.assertIn('noble-security', rows[1])
        self.assertIn('[signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg arch=amd64,arm64]', rows[0])
        self.assertEqual(packages.source_lines(source + 'Trusted: yes\n', True), ([], 1))

    def test_flat_vendor_sources_and_bounded_inline_public_keys_remain_signed(self):
        source = ('Types: deb\nURIs: https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/sbsa/\n'
                  'Suites: /\nSigned-By: /usr/share/keyrings/cuda-archive-keyring.gpg\n')
        rows, skipped = packages.source_lines(source, True)
        self.assertEqual(skipped, 0)
        self.assertTrue(rows[0].endswith('/ /'))
        source = ('Types: deb\nURIs: https://workbench.download.nvidia.com/stable/linux/debian\n'
                  'Suites: default\nComponents: proprietary\nSigned-By:\n'
                  '  -----BEGIN PGP PUBLIC KEY BLOCK-----\n  .\n  YWJj\n  =YWJj\n  -----END PGP PUBLIC KEY BLOCK-----\n')
        keys = {}
        rows, skipped = packages.source_lines(source, True, keys)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(keys), 1)
        self.assertIn('signed-by=/usr/share/keyrings/hc-inline-', rows[0])
        self.assertNotIn('YWJj', rows[0])
        self.assertEqual(packages.source_lines(source.replace('PUBLIC KEY', 'PRIVATE KEY'), True), ([], 1))
        self.assertEqual(packages.source_lines(source.replace('YWJj', '";malicious-hook'), True), ([], 1))

    def test_apt_configuration_blocks_system_hooks_auth_and_persistent_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(packages.subprocess, 'check_output', return_value='amd64\n'):
                config = packages.apt_config(directory).read_text()
            self.assertIn('Dir::Etc::main "/dev/null"', config)
            self.assertIn('Dir::Etc::parts "' + str(directory / 'empty'), config)
            self.assertIn('Dir::Etc::netrc "/dev/null"', config)
            self.assertIn('Dir::State::status "/var/lib/dpkg/status"', config)
            for field in ('lists', 'archives', 'extended_states'):
                self.assertIn(str(directory / field), config)
            self.assertIn('APT::Update::Error-Mode "any"', config)
            self.assertIn('Acquire::http::Proxy "DIRECT"', config)
            self.assertNotIn('Post-Invoke', config)
            self.assertNotIn('TOKEN', json.dumps(packages.ENV))

    def test_python_apt_configuration_clear_uses_supported_keys_and_candidate_origins(self):
        config_calls = []
        class Configuration(dict):
            def list(self): return ['APT', 'Dir']
            def clear(self, key): config_calls.append(key)
        config = Configuration()
        installed = SimpleNamespace(ver_str='1')
        ubuntu = SimpleNamespace(archive='noble-security', origin='Ubuntu')
        vendor = SimpleNamespace(archive='stable', origin='NVIDIA')
        pkg = SimpleNamespace(current_ver=installed, get_fullname=lambda pretty: 'openssl:arm64')
        candidate = SimpleNamespace(ver_str='2', file_list=[(ubuntu, 1), (vendor, 1)])
        def init_config():
            self.assertEqual(packages.os.environ['APT_CONFIG'], '/tmp/isolated-config')
            self.assertEqual(config_calls, ['APT', 'Dir'])
            config['Acquire::IndexTargets::deb::Packages::MetaKey'] = '$(COMPONENT)/binary-$(ARCHITECTURE)/Packages'
        apt = SimpleNamespace(init_config=init_config, config=config,
                              init_system=lambda: None, Cache=lambda progress: SimpleNamespace(packages=[pkg]),
                              DepCache=lambda cache: SimpleNamespace(get_candidate_ver=lambda package: candidate),
                              version_compare=lambda new, old: 1)
        with patch.dict(sys.modules, apt_pkg=apt):
            rows, installed_count = packages.apt_candidates(Path('/tmp/isolated-config'))
        self.assertEqual(config_calls, ['APT', 'Dir'])
        self.assertIn('Acquire::IndexTargets::deb::Packages::MetaKey', config)
        self.assertEqual(installed_count, 1)
        self.assertEqual(rows, [{'name': 'openssl:arm64', 'installed': '1', 'candidate': '2', 'security': True}])

    def test_native_pins_are_copied_without_loading_hooks_or_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native, disposable = root / 'apt', root / 'isolated'
            (native / 'preferences.d').mkdir(parents=True)
            disposable.mkdir()
            pin = 'Package: libnvidia-*580\n# harmless comment\npin: release l=NVIDIA CUDA\npin-priority: -1\n'
            (native / 'preferences.d/driver.pref').write_text(pin)
            (native / 'apt.conf').write_text('DPkg::Post-Invoke {"unexpected-hook";};')
            real_fstat = packages.os.fstat
            def owned(fd):
                metadata = real_fstat(fd)
                return SimpleNamespace(st_mode=metadata.st_mode, st_uid=0, st_size=metadata.st_size)
            with patch.object(packages.os, 'fstat', side_effect=owned), patch.object(packages.subprocess, 'check_output', return_value='arm64\n'):
                config = packages.apt_config(disposable, native).read_text()
            copied = (disposable / 'preferences.d/driver.pref').read_text()
            self.assertEqual(copied, pin.replace('# harmless comment\n', ''))
            self.assertIn(str(disposable / 'preferences.d'), config)
            self.assertNotIn('unexpected-hook', config + copied)
            (native / 'preferences.d/driver.pref').chmod(0o666)
            with patch.object(packages.os, 'fstat', side_effect=owned):
                with self.assertRaises(ValueError): packages.copy_preferences(native, disposable)
            (native / 'preferences.d/driver.pref').unlink()
            (native / 'preferences.d/driver.pref').symlink_to(native / 'apt.conf')
            with self.assertRaises(OSError): packages.copy_preferences(native, disposable)

    def test_resolver_distinguishes_phased_deferrals_from_other_kept_updates(self):
        rows = [{'name': name, 'installed': '1', 'candidate': '2', 'security': False} for name in ('openssl:arm64', 'samba', 'held-package')]
        calls = []
        def process(argv, **kwargs):
            calls.append((argv, kwargs))
            kwargs['stdout'].write(b'The following upgrades have been deferred due to phasing:\n  samba\nThe following packages have been kept back:\n  held-package\nInst openssl [1] (2 Ubuntu:24.04/noble-security [arm64])\n')
            kwargs['stdout'].flush()
            return SimpleNamespace(pid=99999999, returncode=0, poll=lambda: 0, wait=lambda: 0)
        with patch.object(packages.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), patch.object(packages.subprocess, 'Popen', side_effect=process):
            counts = packages.eligibility(Path('/tmp/disposable-config'), rows)
        self.assertEqual(counts, dict(eligible_count=1, phased_count=1, deferred_count=2))
        self.assertEqual(calls[0][0], ['apt-get', '-s', '--no-remove', 'full-upgrade'])
        self.assertNotIn('Always-Include-Phased-Updates', str(calls))
        with patch.object(packages.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2)), patch.object(packages.subprocess, 'Popen') as parser:
            self.assertEqual(packages.eligibility(Path('/tmp/x'), rows), dict(eligible_count=None, phased_count=None, deferred_count=None))
            parser.assert_not_called()
        with patch.object(packages.subprocess, 'Popen', side_effect=OSError('private failure')):
            self.assertEqual(packages.eligibility(Path('/tmp/x'), rows), dict(eligible_count=None, phased_count=None, deferred_count=None))

    def test_fresh_counts_security_unknowns_and_reboot_are_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'run').mkdir()
            (directory / 'run/reboot-required').touch()
            (directory / 'run/reboot-required.pkgs').write_text('linux-image-6.8\nlinux-image-6.8\n')
            rows = [{'name': 'openssl', 'installed': '1', 'candidate': '2', 'security': True},
                    {'name': 'nvidia-dgx', 'installed': '1', 'candidate': '2', 'security': None}]
            report = self.fixture(directory, rows)
            self.assertEqual(report['status'], 'updates_available')
            self.assertTrue(report['refreshed'])
            self.assertTrue(report['coverage_complete'])
            self.assertEqual((report['candidate_count'], report['security_count'], report['security_unknown_count']), (2, 1, 1))
            self.assertEqual((report['reboot_required'], report['reboot_package_count']), (True, 1))
            self.assertEqual(packages.project_report(report, datetime.now(timezone.utc))['status'], 'updates_available')

    def test_missing_and_partial_sources_never_report_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = self.fixture(directory, [], 'deb https://private.invalid/repo noble main\n')
            self.assertEqual(report['status'], 'unknown')
            self.assertFalse(report['coverage_complete'])
            self.assertEqual(report['provenance']['skipped_sources'], 1)
            (directory / 'apt/sources.list').write_text('deb https://private.invalid/repo noble main\n')
            report = packages.apt_report(apt_dir=directory / 'apt')
            self.assertEqual((report['status'], report['candidate_count']), ('unknown', None))

    def test_partial_refresh_failure_and_tool_absence_are_not_zero_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.source(directory / 'apt')
            with patch.object(packages.subprocess, 'check_output', return_value='amd64\n'):
                report = packages.apt_report(apt_dir=directory / 'apt', refresher=lambda config: (_ for _ in ()).throw(ValueError('private upstream body')))
                self.assertEqual((report['status'], report['reason'], report['candidate_count']), ('error', 'refresh_failed', None))
                self.assertNotIn('private', json.dumps(report))
                report = packages.apt_report(apt_dir=directory / 'apt', reader=lambda config: (_ for _ in ()).throw(ImportError()), refresher=lambda config: None)
                self.assertEqual((report['status'], report['reason']), ('unknown', 'tool_missing'))

    def test_preexec_failure_is_categorical(self):
        with tempfile.TemporaryDirectory() as temporary:
            apt_dir = Path(temporary)
            self.source(apt_dir)
            with patch.object(packages.subprocess, 'check_output', return_value='arm64\n'):
                report = packages.apt_report(apt_dir=apt_dir, refresher=lambda config: (_ for _ in ()).throw(subprocess.SubprocessError('private preexec body')))
            self.assertEqual((report['status'], report['reason']), ('error', 'refresh_failed'))
            self.assertNotIn('private', json.dumps(report))

    def test_capabilities_are_cleared_after_identity_drop(self):
        calls = []
        class LibC:
            def capset(self, header, data):
                self_header, self_data = header._obj, data._obj
                self_test.assertEqual(list(self_header), [0x20080522, 0])
                self_test.assertEqual(list(self_data), [0] * 6)
                calls.append('clear-caps')
                return 0
        self_test = self
        with patch.object(packages.os, 'setgroups', side_effect=lambda values: calls.append(('groups', values))), \
                patch.object(packages.os, 'setgid', side_effect=lambda gid: calls.append(('gid', gid))), \
                patch.object(packages.os, 'setuid', side_effect=lambda uid: calls.append(('uid', uid))), \
                patch.object(packages.ctypes, 'CDLL', return_value=LibC()):
            packages.drop_index_privileges(42, 65534)
        self.assertEqual(calls, [('groups', []), ('gid', 65534), ('uid', 42), 'clear-caps'])

    def test_full_inventory_digest_detects_hidden_candidate_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rows = [{'name': f'package{i:03}', 'installed': '1', 'candidate': '2', 'security': False} for i in range(40)]
            first = self.fixture(directory, rows)
            rows[-1]['candidate'] = '3'
            second = self.fixture(directory, rows)
            self.assertEqual(first['candidate_count'], 40)
            self.assertTrue(first['truncated'])
            self.assertEqual(first['candidates'], second['candidates'])
            self.assertNotEqual(first['candidate_digest_sha256'], second['candidate_digest_sha256'])

    def test_projection_unknown_stale_error_and_invalid_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = self.fixture(Path(temporary), [])
            now = datetime.now(timezone.utc)
            self.assertEqual(packages.project_report(report, now)['status'], 'current')
            self.assertEqual(packages.project_report(report, now + timedelta(days=2))['status'], 'stale')
            report['collected_at'] = '2026-10-09'
            self.assertEqual(packages.project_report(report, now)['reason'], 'metadata_invalid')
            unknown = packages.empty('apt', 'tool_missing')
            error = packages.empty('apt', 'refresh_failed', 'error')
            self.assertEqual(packages.project_report(unknown, now)['status'], 'unknown')
            self.assertEqual(packages.project_report(error, now)['status'], 'error')

    def test_projection_never_passes_untrusted_strings_urls_or_booleans_as_counts(self):
        report = packages.empty('apt')
        report['candidate_count'] = True
        self.assertEqual(packages.project_report(report, datetime.now(timezone.utc))['reason'], 'metadata_invalid')
        report = packages.empty('apt')
        report['candidates'] = [{'name': 'secret https://token@host', 'installed': '1', 'candidate': '2', 'security': None}]
        result = packages.project_report(report, datetime.now(timezone.utc))
        self.assertNotIn('token', json.dumps(result))

    def test_nix_catalog_uses_exact_pinned_branch_without_build_lock_or_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = {'root': 'root', 'nodes': {'root': {'inputs': {'nixpkgs': 'nixpkgs'}},
                    'nixpkgs': {'original': {'type': 'github', 'owner': 'NixOS', 'repo': 'nixpkgs', 'ref': 'nixos-26.05'},
                                'locked': {'type': 'github', 'owner': 'NixOS', 'repo': 'nixpkgs', 'rev': 'a' * 40}}}}
            (directory / 'flake.lock').write_text(json.dumps(lock))
            calls = []
            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, 'b' * 40 + '\trefs/heads/nixos-26.05\n', '')
            report = packages.nix_report(directory, runner)
            self.assertEqual((report['status'], report['scope'], report['candidate_count']), ('updates_available', 'package_catalog', 1))
            self.assertIsNone(report['security_count'])
            self.assertIsNone(report['reboot_required'])
            self.assertIn('/run/current-system/sw/bin', calls[0][1]['env']['PATH'])
            self.assertEqual(calls[0][0][-1], 'refs/heads/nixos-26.05')
            self.assertEqual(calls[0][1]['cwd'], '/')
            self.assertNotIn('update', calls[0][0])
            self.assertNotIn('switch', calls[0][0])
            self.assertEqual(json.loads((directory / 'flake.lock').read_text()), lock)


if __name__ == '__main__':
    unittest.main()
