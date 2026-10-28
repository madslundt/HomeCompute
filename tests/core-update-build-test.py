#!/usr/bin/env python3
"""Private package-refresh build provenance and existing closure-policy reuse."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_update_build as build

spec = importlib.util.spec_from_file_location('core_preparation', ROOT / 'scripts/prepare-core-maintenance.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
SHA = 'a' * 40
CANDIDATE = '/nix/store/' + 'b' * 32 + '-nixos-system-home-core-26.05.1'
CURRENT = '/nix/store/' + 'c' * 32 + '-nixos-system-home-core-26.05.0'


def lock():
    return {'version': 7, 'root': 'root', 'nodes': {
        'root': {'inputs': {'nixpkgs': 'nixpkgs'}},
        'nixpkgs': {'original': {'type': 'github', 'owner': 'NixOS', 'repo': 'nixpkgs', 'ref': 'nixos-26.05'},
                    'locked': {'type': 'github', 'owner': 'NixOS', 'repo': 'nixpkgs', 'rev': '1' * 40,
                               'narHash': 'sha256-original', 'lastModified': 1}}}}


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.releases = self.base / 'releases'
        self.source = self.releases / SHA
        (self.source / '.git').mkdir(parents=True)
        (self.source / 'flake.nix').write_text('{ inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05"; }\n')
        (self.source / 'flake.lock').write_text(json.dumps(lock()))
        self.current = self.base / 'current'
        self.current.symlink_to(self.source)
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        self.staging = self.state / 'nixos'
        self.output = self.state / 'plan.json'
        self.calls = []
        self.source_dirty = b''
        self.head = SHA
        self.active = CURRENT
        self.build_output = (CANDIDATE + '\n').encode()
        self.refresh = lambda value: value['nodes']['nixpkgs']['locked'].update(rev='2' * 40, lastModified=2)
        self.rewrite_flake = False
        self.extra_source_change = False
        self.stage = None
        self.plan = {'requires_approval': True, 'policy': {'full_closure': 'preserved'}}
        self.helper = SimpleNamespace(resolved_system=core.resolved_system,
                                      prepare=Mock(return_value=(self.plan, ['opaque: unknown_version'])),
                                      write_plan=Mock(side_effect=lambda output, plan: output.write_text(json.dumps(plan))))
        self.controller = SimpleNamespace(uid=0, local_target='home-core', runner=self.runner, clock=lambda: 1000)
        original_lstat = Path.lstat
        def lstat(path):
            info = original_lstat(path)
            if path == self.current:
                return SimpleNamespace(st_uid=0, st_mode=info.st_mode)
            return info
        def trusted(path, uid, *, directory=False):
            path = Path(path)
            # Exercise symlink refusal while treating test directories as root
            # controlled. Production uses the shared ancestor/owner validator.
            for part in [*path.parents, path]:
                if part.is_symlink():
                    raise ValueError('path must be privately controlled by root')
            if directory and not path.is_dir():
                raise ValueError('trusted directory required')
            return path
        self.patches = [patch.object(build, 'CURRENT', self.current), patch.object(build, 'RELEASES', self.releases),
                        patch.object(build, 'STAGING', self.staging), patch.object(build, 'trusted_path', side_effect=trusted),
                        patch.object(build, 'root_json', side_effect=lambda path, uid: json.loads(Path(path).read_text())),
                        patch.object(build.os, 'geteuid', return_value=0), patch.object(Path, 'lstat', new=lstat),
                        patch.object(build, 'core_helper', return_value=self.helper)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        out, err = b'', b''
        if argv[0] == build.GIT:
            if 'clone' in argv:
                self.stage = Path(argv[-1])
                shutil.copytree(self.source, self.stage)
            elif 'rev-parse' in argv:
                out = (self.head + '\n').encode()
            elif 'status' in argv:
                directory = Path(argv[argv.index('-C') + 1])
                if directory == self.source:
                    out = self.source_dirty
                elif self.extra_source_change:
                    out = b' M hosts/home-core/default.nix\n'
                elif (directory / 'flake.lock').read_bytes() != (self.source / 'flake.lock').read_bytes():
                    out = b' M flake.lock\n'
        elif argv[:3] == [build.NIX, 'flake', 'update']:
            value = json.loads((self.stage / 'flake.lock').read_text())
            self.refresh(value)
            (self.stage / 'flake.lock').write_text(json.dumps(value))
            if self.rewrite_flake:
                (self.stage / 'flake.nix').write_text('unapproved configuration')
        elif argv[:2] == [build.NIX, 'build']:
            out, err = self.build_output, b'bounded Nix build diagnostic\n'
        elif argv == [build.READLINK, '-f', build.ACTIVE]:
            out = (self.active + '\n').encode()
        else:
            raise AssertionError('unexpected command: ' + repr(argv))
        return subprocess.CompletedProcess(argv, 0, out, err)

    def test_fixed_private_build_writes_full_closure_plan_without_activation(self):
        original = (self.source / 'flake.lock').read_bytes()
        receipt = build.prepare(self.controller, self.output)
        self.assertEqual(receipt['candidate'], CANDIDATE)
        self.assertEqual(receipt['source_pinned_sha'], SHA)
        self.assertTrue(receipt['plan_written'])
        self.assertFalse(receipt['installed_changes'])
        self.assertEqual(receipt['staged_lock_sha256'], hashlib.sha256((self.stage / 'flake.lock').read_bytes()).hexdigest())
        self.assertEqual(Path(receipt['build_log']).read_bytes(), b'bounded Nix build diagnostic\n')
        self.assertEqual(Path(receipt['build_log']).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.source / 'flake.lock').read_bytes(), original)
        self.assertEqual(json.loads(self.output.read_text()), self.plan)
        self.helper.prepare.assert_called_once_with(CANDIDATE, runner=self.controller.runner, clock=self.controller.clock)
        self.helper.write_plan.assert_called_once_with(self.output, self.plan)
        clone = next(argv for argv, _ in self.calls if 'clone' in argv)
        self.assertIn('--no-hardlinks', clone)
        commands = [argv for argv, _ in self.calls]
        self.assertIn([build.NIX, 'flake', 'update', '--accept-flake-config', '--flake', 'path:' + str(self.stage)], commands)
        self.assertIn([build.NIX, 'build', '--accept-flake-config',
                       'path:' + str(self.stage) + '#nixosConfigurations.home-core.config.system.build.toplevel',
                       '--no-link', '--print-out-paths'], commands)
        self.assertFalse(any('switch' in argv or '--set' in argv or 'push' in argv or 'commit' in argv for argv in commands))
        self.assertTrue(all(kwargs['shell'] is False for _, kwargs in self.calls))
        self.assertTrue(all(kwargs['timeout'] == 3600 for argv, kwargs in self.calls if argv[0] == build.NIX))

    def test_current_candidate_has_distinct_no_work_result(self):
        self.build_output = (CURRENT + '\n').encode()
        with self.assertRaisesRegex(ValueError, '^no NixOS package updates$'):
            build.prepare(self.controller, self.output)
        self.helper.prepare.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_dirty_release_or_wrong_head_refused_before_clone(self):
        for dirty, head in [(b' M flake.nix\n', SHA), (b'?? unmanaged\n', SHA), (b'', 'd' * 40)]:
            self.source_dirty, self.head = dirty, head
            self.calls.clear()
            with self.subTest(dirty=dirty, head=head), self.assertRaises(ValueError):
                build.prepare(self.controller, self.output)
            self.assertFalse(any('clone' in argv for argv, _ in self.calls))

    def test_source_symlink_wrong_release_name_and_owner_refused(self):
        self.current.unlink()
        self.current.symlink_to(self.base / 'arbitrary')
        with self.assertRaisesRegex(ValueError, 'exact approved release'):
            build.prepare(self.controller, self.output)
        self.current.unlink()
        self.current.symlink_to(self.source)
        with patch.object(build, 'trusted_path', side_effect=ValueError('root ownership required')):
            with self.assertRaisesRegex(ValueError, 'root ownership'):
                build.prepare(self.controller, self.output)
        self.source.rename(self.source.with_name('actual'))
        self.source.symlink_to(self.source.with_name('actual'))
        with self.assertRaisesRegex(ValueError, 'privately controlled'):
            build.prepare(self.controller, self.output)

    def test_channel_repository_or_input_graph_changes_refused_before_build(self):
        changes = [lambda x: x['nodes']['nixpkgs']['original'].update(ref='nixos-unstable'),
                   lambda x: x['nodes']['nixpkgs']['locked'].update(repo='different'),
                   lambda x: x['nodes']['root']['inputs'].update(extra='nixpkgs'),
                   lambda x: x['nodes'].update(extra=copy.deepcopy(x['nodes']['nixpkgs']))]
        for change in changes:
            self.refresh = change
            self.calls.clear()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'URLs, channels or dependency edges'):
                build.prepare(self.controller, self.output)
            self.assertFalse(any(argv[:2] == [build.NIX, 'build'] for argv, _ in self.calls))

    def test_changed_configuration_or_other_tracked_files_refused(self):
        self.rewrite_flake = True
        with self.assertRaisesRegex(ValueError, 'flake declarations'):
            build.prepare(self.controller, self.output)
        self.rewrite_flake = False
        self.extra_source_change = True
        with self.assertRaisesRegex(ValueError, 'private flake lock'):
            build.prepare(self.controller, self.output)

    def test_malformed_build_output_and_excess_diagnostics_refused(self):
        for output in [b'', (CANDIDATE + '\n' + CURRENT + '\n').encode(), b'/tmp/result\n']:
            self.build_output = output
            with self.subTest(output=output), self.assertRaises(ValueError):
                build.prepare(self.controller, self.output)
        runner = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'', b'x' * (build.MAX_OUTPUT + 1))
        with self.assertRaises(ValueError):
            build.command(SimpleNamespace(runner=runner), [build.NIX, 'build'])

    def test_caller_target_and_existing_destination_refused_before_commands(self):
        for target, uid in [('home-spark', 0), ('home-core', 1000)]:
            self.controller.local_target, self.controller.uid = target, uid
            with self.subTest(target=target, uid=uid), self.assertRaises(ValueError):
                build.prepare(self.controller, self.output)
        self.controller.local_target, self.controller.uid = 'home-core', 0
        self.output.write_text('preserve')
        with self.assertRaises(ValueError):
            build.prepare(self.controller, self.output)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
