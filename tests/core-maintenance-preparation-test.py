#!/usr/bin/env python3
"""Complete immutable closure accounting and Nix profile/runtime binding."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('prepare_core_maintenance', ROOT / 'scripts/prepare-core-maintenance.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
from infrastructure_maintenance import validate_plan

def store(name, hash='a'):
    return '/nix/store/' + hash * 32 + '-' + name

OLD = store('nixos-system-home-core-26.05.20260901.a311611')
NEW = store('nixos-system-home-core-26.05.20261013.b123456', 'b')

def closure(paths, list_format=False):
    return json.dumps([{'path': path, 'narHash': 'sha256-synthetic'} for path in paths] if list_format
                      else {path: {'narHash': 'sha256-synthetic'} for path in paths}).encode()


class ParsingTests(unittest.TestCase):
    def test_sticky_nix_store_is_allowed_but_mutable_objects_and_nonsticky_store_are_not(self):
        selected = Path(NEW) / 'bin/switch-to-configuration'
        def metadata(path):
            mode = stat.S_IFREG | 0o555 if path == selected else stat.S_IFDIR | (0o1775 if str(path) == '/nix/store' else 0o755)
            return SimpleNamespace(st_uid=0, st_mode=mode)
        with patch.object(Path, 'lstat', metadata):
            self.assertEqual(core.immutable_store_path(selected), selected)
        for target, permission in [(Path('/nix/store'), 0o775), (Path('/nix/store'), 0o1777), (selected, 0o775)]:
            def unsafe(path):
                row = metadata(path)
                if path == target:
                    row.st_mode = (stat.S_IFREG if path == selected else stat.S_IFDIR) | permission
                return row
            with patch.object(Path, 'lstat', unsafe), self.assertRaises(ValueError):
                core.immutable_store_path(selected)

    def test_both_nix_json_formats_require_complete_unique_immutable_roots(self):
        paths = [OLD, store('curl-8.15.0')]
        for old_format in (False, True):
            self.assertEqual(core.closure(closure(paths, old_format), OLD), frozenset(paths))
        for raw in (b'[]', b'null', closure(paths[1:]), closure([OLD, '/tmp/arbitrary']),
                    closure([OLD, OLD], True), json.dumps({OLD: {'path': NEW}}).encode(),
                    ('{"' + OLD + '":{},"' + OLD + '":{}}').encode()):
            with self.assertRaises(ValueError): core.closure(raw, OLD)

    def test_semver_and_output_names_preserve_all_changed_components(self):
        old = frozenset([OLD, store('curl-8.15.0'), store('curl-8.15.0-dev'), store('openssl-3.5.1'), store('unchanged-1.0.0')])
        new = frozenset([NEW, store('curl-8.16.0', 'b'), store('curl-8.16.0-dev', 'b'), store('openssl-3.5.2', 'b'), store('unchanged-1.0.0')])
        updates, reasons = core.changes(old, new, OLD, NEW)
        self.assertEqual(reasons, [])
        self.assertEqual(updates, [
            {'component':'nixos-system','scheme':'nixos','installed':'26.05','candidate':'26.05'},
            {'component':'curl','scheme':'semver','installed':'8.15.0','candidate':'8.16.0'},
            {'component':'curl-dev','scheme':'semver','installed':'8.15.0','candidate':'8.16.0'},
            {'component':'openssl','scheme':'semver','installed':'3.5.1','candidate':'3.5.2'}])

    def test_additions_removals_duplicates_rebuilds_and_unknown_versions_are_retained_for_review(self):
        old = frozenset([OLD, store('removed-1.0.0'), store('duplicate-1.0.0'), store('duplicate-2.0.0'),
                         store('etc-secrets.env'), store('glibc-2.41'), store('rebuilt-1.0.0')])
        new = frozenset([NEW, store('added-1.0.0', 'b'), store('duplicate-3.0.0', 'b'),
                         store('etc-secrets.env', 'b'), store('glibc-2.42', 'b'), store('rebuilt-1.0.0', 'b')])
        updates, reasons = core.changes(old, new, OLD, NEW)
        rows = {row['component']: row for row in updates}
        self.assertEqual(rows['added']['installed'], 'absent')
        self.assertEqual(rows['removed']['candidate'], 'absent')
        self.assertEqual(rows['duplicate']['installed'], '1.0.0,2.0.0')
        self.assertEqual(rows['glibc']['candidate'], '2.42')
        self.assertEqual(rows['etc-secrets.env']['candidate'], 'unversioned')
        for reason in ('added: added_or_removed', 'removed: added_or_removed', 'duplicate: ambiguous_duplicates',
                       'glibc: unknown_version', 'rebuilt: artifact_changed_without_version_change'):
            self.assertIn(reason, reasons)

    def test_channel_changes_and_unrecognized_release_are_never_routine(self):
        self.assertEqual(core.release(OLD), '26.05')
        self.assertEqual(core.release(store('nixos-system-home-core-unstable')), 'unknown')
        self.assertEqual(core.release(store('nixos-system-home-core-26.11.20261201.abcdef')), '26.11')

    def test_no_subset_when_full_change_exceeds_component_budget(self):
        old = frozenset([OLD, *[store(f'package-{i}-1.0.0') for i in range(4096)]])
        new = frozenset([NEW, *[store(f'package-{i}-1.0.1', 'b') for i in range(4096)]])
        with self.assertRaisesRegex(ValueError, 'no subset'):
            core.changes(old, new, OLD, NEW)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.current, self.profile, self.candidate = OLD, OLD, NEW
        self.old_paths = [OLD, store('curl-8.15.0')]
        self.new_paths = [NEW, store('curl-8.16.0', 'b')]
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv == [core.READLINK, '-f', core.CURRENT]:
            output = (self.current + '\n').encode()
        elif argv == [core.READLINK, '-f', core.PROFILE]:
            output = (self.profile + '\n').encode()
        elif argv[0] == core.NIX:
            self.assertEqual(argv[1:6], ['path-info', '--offline', '--recursive', '--json', argv[-1]])
            output = closure(self.old_paths if argv[-1] == self.current else self.new_paths)
        elif argv[0] == core.CHECKSUM:
            output = ('c' * 64 + '  ' + argv[1] + '\n').encode()
        else:
            raise AssertionError('preparation attempted a mutation')
        return subprocess.CompletedProcess(argv, 0, output, b'')

    def prepare(self):
        with patch.object(core.os, 'geteuid', return_value=0), \
             patch.object(core, 'activation_script', return_value=Path(self.candidate) / 'bin/switch-to-configuration'):
            return core.prepare(self.candidate, runner=self.runner, clock=lambda: 1000)

    def test_plan_binds_profile_and_runtime_and_activates_profile_before_switch(self):
        plan, reasons = self.prepare()
        self.assertEqual(reasons, [])
        self.assertFalse(validate_plan(plan)['approval_required'])
        self.assertEqual(plan['steps'], [[core.NIX_ENV, '--profile', core.PROFILE, '--set', NEW], [NEW + '/bin/switch-to-configuration', 'switch']])
        self.assertEqual(plan['prechecks'][0]['stdout_sha256'], hashlib.sha256((OLD + '\n').encode()).hexdigest())
        self.assertEqual(plan['prechecks'][1]['argv'], [core.READLINK, '-f', core.PROFILE])
        self.assertEqual(plan['prechecks'][2]['argv'], [core.CHECKSUM, NEW + '/bin/switch-to-configuration'])
        self.assertEqual([row['stdout_sha256'] for row in plan['postchecks']], [hashlib.sha256((NEW + '\n').encode()).hexdigest()] * 2)
        self.assertEqual(plan['expires_at'], 4600)
        self.assertTrue(all(kwargs['shell'] is False for _, kwargs in self.calls))
        self.assertFalse(any('build' in argv or 'switch' in argv or '--set' in argv for argv, _ in self.calls))

    def test_ambiguous_closure_or_diverged_boot_profile_requires_approval(self):
        self.new_paths.append(store('added-package-1.0.0', 'b'))
        plan, reasons = self.prepare()
        self.assertTrue(validate_plan(plan)['approval_required'])
        self.assertIn('added-package: added_or_removed', reasons)
        self.new_paths.pop()
        self.profile = store('nixos-system-home-core-26.05.20261001.abcdef', 'c')
        plan, reasons = self.prepare()
        self.assertTrue(validate_plan(plan)['approval_required'])
        self.assertIn('active_system_and_boot_profile_differ', reasons)

    def test_major_package_and_os_channel_change_require_approval(self):
        self.new_paths = [NEW, store('curl-9.0.0', 'b')]
        self.assertTrue(validate_plan(self.prepare()[0])['approval_required'])
        self.candidate = store('nixos-system-home-core-26.11.20261201.abcdef', 'b')
        self.new_paths = [self.candidate, store('curl-8.16.0', 'b')]
        self.assertTrue(validate_plan(self.prepare()[0])['approval_required'])

    def test_wrong_host_user_target_or_already_active_refuse_without_effects(self):
        with patch.object(core.os, 'geteuid', return_value=501), self.assertRaises(ValueError):
            core.prepare(NEW, runner=self.runner)
        with patch.object(core.os, 'geteuid', return_value=0):
            with self.assertRaises(ValueError): core.prepare(NEW, host='home-spark', runner=self.runner)
            for path in ('/tmp/candidate', NEW + ';reboot', store('nixos-system-home-spark-26.05')):
                with self.assertRaises(ValueError): core.prepare(path, runner=self.runner)
        self.assertEqual(self.calls, [])
        self.current = NEW
        with self.assertRaisesRegex(ValueError, 'already active'): self.prepare()

    def test_failed_or_unbounded_metadata_read_never_prepares_a_subset(self):
        with patch.object(core.os, 'geteuid', return_value=0), \
             patch.object(core, 'activation_script', return_value=Path(NEW) / 'bin/switch-to-configuration'):
            for result in (subprocess.CompletedProcess([], 1, b'', b'failure'),
                           subprocess.CompletedProcess([], 0, b'x' * (core.MAX_CLOSURE_BYTES + 1), b'')):
                with self.assertRaises(ValueError): core.prepare(NEW, runner=lambda *a, **kw: result)


if __name__ == '__main__':
    unittest.main()
