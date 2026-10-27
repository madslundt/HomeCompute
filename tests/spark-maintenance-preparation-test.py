#!/usr/bin/env python3
"""Complete resolver-effect accounting and root-only Spark plan preparation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('prepare_spark_maintenance', ROOT / 'scripts/prepare-spark-maintenance.py')
spark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark)


def simulation(rows, *, new=0, removed=0, extra=''):
    lines = ['Reading package lists...', 'Building dependency tree...', 'Reading state information...',
             'Calculating upgrade...']
    if rows:
        lines += ['The following packages will be upgraded:', '  ' + ' '.join(row[0] for row in rows)]
    lines += [f'{len(rows)} upgraded, {new} newly installed, {removed} to remove and 0 not upgraded.']
    lines += [f'Inst {name} [{installed}] ({candidate} Ubuntu:24.04/noble-updates [arm64])' for name, installed, candidate in rows]
    lines += [f'Conf {name} ({candidate} Ubuntu:24.04/noble-updates [arm64])' for name, installed, candidate in rows]
    if extra:
        lines.append(extra)
    return ('\n'.join(lines) + '\n').encode()


class SolverTest(unittest.TestCase):
    def test_all_candidates_accounted_sorted_and_installed_versions_retained(self):
        parsed = spark.parse_simulation(simulation([('zlib1g', '1:1.3-1', '1:1.3-2'), ('curl', '8.5-1', '8.5-2')]))
        self.assertEqual([row['component'] for row in parsed], ['curl', 'zlib1g'])
        self.assertEqual(parsed[1]['installed'], '1:1.3-1')
        self.assertEqual(parsed[1]['candidate'], '1:1.3-2')

    def test_rejects_new_packages_removals_unknown_and_incomplete_effects(self):
        good = simulation([('curl', '8.5-1', '8.5-2')])
        bad = [simulation([('curl', '8.5-1', '8.5-2')], new=1),
               simulation([('curl', '8.5-1', '8.5-2')], removed=1),
               good.replace(b'Inst curl [8.5-1]', b'Inst curl'),
               good.replace(b'Conf curl', b'Remv curl'),
               good.replace(b'Conf curl', b'Conf unexpected'),
               good.replace(b'1 upgraded', b'2 upgraded'),
               good.replace(b'Conf curl (8.5-2', b'Conf curl (8.5-3'),
               good + b'UNKNOWN solver behavior\n',
               good[:good.index(b'Conf')],
               good + good[good.index(b'Inst'):good.index(b'Conf')]]
        for output in bad:
            with self.subTest(output=output):
                with self.assertRaises(ValueError): spark.parse_simulation(output)

    def test_kept_back_and_phased_packages_do_not_add_unreviewed_effects(self):
        output = simulation([('curl', '8.5-1', '8.5-2')])
        output = output.replace(b'The following packages will be upgraded:',
            b'The following upgrades have been deferred due to phasing:\n  zlib1g\nThe following packages have been kept back:\n  libcurl4\nThe following packages will be upgraded:')
        self.assertEqual(len(spark.parse_simulation(output)), 1)

    def test_apt_transient_dependency_annotations_preserve_complete_effect_accounting(self):
        plain = simulation([('curl', '8.5-1', '8.5-2')])
        for suffix in (b' []', b' [libcurl4:arm64 ]'):
            annotated = plain.replace(b'[arm64])\nConf', b'[arm64])' + suffix + b'\nConf')
            self.assertEqual(spark.parse_simulation(plain), spark.parse_simulation(annotated))
            configured = plain.replace(b'Conf curl (8.5-2 Ubuntu:24.04/noble-updates [arm64])',
                                       b'Conf curl (8.5-2 Ubuntu:24.04/noble-updates [arm64])' + suffix)
            self.assertEqual(spark.parse_simulation(plain), spark.parse_simulation(configured))

    def test_configuration_annotation_rejects_malformed_or_unbounded_effects(self):
        plain = simulation([('curl', '8.5-1', '8.5-2')])
        for suffix in (b' [libcurl4:arm64', b' [$(command)]', b' [] Remv other', b' [' + b'pkg ' * 121 + b']'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                spark.parse_simulation(plain.rstrip(b'\n') + suffix + b'\n')

    def test_optional_suggestions_and_recommendations_do_not_add_solver_effects(self):
        plain = simulation([('curl', '8.5-1', '8.5-2')])
        optional = b'Suggested packages:\n  crash bind9 ntp | chrony\nRecommended packages:\n  python3-gpg attr\n'
        annotated = plain.replace(b'The following packages will be upgraded:', optional + b'The following packages will be upgraded:')
        self.assertEqual(spark.parse_simulation(plain), spark.parse_simulation(annotated))
        wrapped = optional.replace(b'ntp | chrony', b'ntp\n  | chrony').replace(b'python3-gpg attr', b'python3-gpg |\n  attr')
        self.assertEqual(spark.parse_simulation(plain), spark.parse_simulation(
            plain.replace(b'The following packages will be upgraded:', wrapped + b'The following packages will be upgraded:')))
        for invalid in (b'  | chrony\n', b'  ntp |\n', b'  ntp | | chrony\n', b'  $(command)\n'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                spark.parse_simulation(plain.replace(b'The following packages will be upgraded:', b'Suggested packages:\n' + invalid + b'The following packages will be upgraded:'))

    def test_no_subset_when_complete_upgrade_exceeds_command_budget(self):
        rows = [(f'package-{index}', '1.0-1', '1.0-2') for index in range(121)]
        with self.assertRaises(ValueError): spark.parse_simulation(simulation(rows))

    def test_dpkg_snapshot_architecture_status_and_precise_versions(self):
        updates = spark.parse_simulation(simulation([('curl:arm64', '8.5-1', '8.5-2')]))
        current = b'curl\tarm64\t8.5-1\tinstalled\n'
        self.assertEqual(spark.expected_versions(current, updates), b'curl\tarm64\t8.5-2\tinstalled\n')
        for bad in [current.replace(b'arm64', b'amd64'), current.replace(b'8.5-1', b'8.5-0'),
                    current.replace(b'installed', b'unpacked'), current + current, b'', current.rstrip()]:
            with self.subTest(output=bad):
                with self.assertRaises(ValueError): spark.expected_versions(bad, updates)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.rows = [('curl', '8.5-1', '8.5-2'), ('zlib1g', '1.3-1', '1.3-2')]
        self.calls = []
        self.exact_rows = None
        self.hostname = b'home-spark\n'

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv == [spark.HOSTNAME, '-s']:
            output = self.hostname
        elif argv[0] == spark.DPKG:
            output = ''.join(f'{name.split(":")[0]}\tarm64\t{old}\tinstalled\n' for name, old, _ in sorted(self.rows)
                             if name in argv[3:]).encode()
        elif argv[0] == spark.APT and '-s' in argv:
            output = simulation(self.exact_rows if 'install' in argv and self.exact_rows is not None else self.rows)
        else:
            raise AssertionError('preparation attempted a write command')
        return subprocess.CompletedProcess(argv, 0, output, b'')

    def prepare(self, **kwargs):
        with patch.object(spark.os, 'geteuid', return_value=0):
            return spark.prepare(runner=self.runner, clock=lambda: 1000, **kwargs)

    def test_root_plan_contains_exact_effect_and_fresh_rechecks_without_installing(self):
        plan, protected = self.prepare(ttl=300)
        self.assertEqual(protected, [])
        self.assertFalse(plan['requires_approval'])
        self.assertEqual(plan['expires_at'], 1300)
        self.assertEqual(plan['steps'], [[spark.APT, '--yes', '--no-remove', '--only-upgrade', 'install', 'curl=8.5-2', 'zlib1g=1.3-2']])
        self.assertEqual(len(plan['prechecks']), 3)
        self.assertEqual(plan['postchecks'][0]['stdout_sha256'], hashlib.sha256(b'curl\tarm64\t8.5-2\tinstalled\nzlib1g\tarm64\t1.3-2\tinstalled\n').hexdigest())
        self.assertTrue(all(kwargs['shell'] is False for _, kwargs in self.calls))
        self.assertFalse(any('update' in argv for argv, _ in self.calls))

    def test_exact_install_resolver_cannot_add_dependency_changes(self):
        self.exact_rows = self.rows + [('extra-dependency', '2.0-1', '2.0-2')]
        with self.assertRaisesRegex(ValueError, 'solver effects differ'):
            self.prepare()

    def test_exact_install_resolver_cannot_change_selected_versions(self):
        self.exact_rows = [('curl', '8.5-1', '8.5-3'), self.rows[1]]
        with self.assertRaisesRegex(ValueError, 'solver effects differ'):
            self.prepare()

    def test_routine_transaction_selects_only_verified_unprotected_minor_patch_effects(self):
        self.rows += [('linux-libc-dev', '6.8.0-1', '6.8.0-2'),
                      ('libxnvctrl0', '615.71.09-1', '615.71.10-1'),
                      ('major-package', '1.0-1', '2.0-1'),
                      ('complex-version', '1.0+dfsg-1', '1.0+dfsg-2')]
        self.exact_rows = self.rows[:2]
        plan, protected = self.prepare(routine_only=True)
        self.assertEqual(protected, [])
        self.assertFalse(plan['requires_approval'])
        self.assertEqual([row['component'] for row in plan['policy']['updates']], ['curl', 'zlib1g'])
        self.assertEqual(plan['steps'][0][-2:], ['curl=8.5-2', 'zlib1g=1.3-2'])
        self.assertEqual(plan['prechecks'][0]['stdout_sha256'], hashlib.sha256(simulation(self.rows)).hexdigest())

    def test_routine_transaction_rejects_protected_or_unknown_dependency_effects(self):
        for extra in [('nvidia-settings', '615.71.09-1', '615.71.10-1'),
                      ('dependency', '1.0-1', '2.0-1'), ('other-routine', '1.0-1', '1.0-2')]:
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'solver effects differ'):
                self.exact_rows = self.rows + [extra]
                self.prepare(routine_only=True)

    def test_no_routine_updates_has_distinct_error(self):
        for rows in [[], [('linux-libc-dev', '6.8.0-1', '6.8.0-2')], [('major-package', '1.0-1', '2.0-1')]]:
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, '^no routine package updates$'):
                self.rows = rows
                self.prepare(routine_only=True)

    def test_routine_selection_still_refuses_unknown_complete_inventory_effects(self):
        original = self.runner
        def malformed(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv == [spark.APT, '-s', '--no-remove', 'upgrade']:
                result.stdout += b'Remv protected-package\n'
            return result
        with patch.object(spark.os, 'geteuid', return_value=0), self.assertRaises(ValueError):
            spark.prepare(runner=malformed, routine_only=True)

    def test_vendor_driver_kernel_and_firmware_upgrade_always_requires_approval(self):
        names = ['nvidia-driver-580', 'libnvidia-compute-580', 'cuda-toolkit-13-0',
                 'libcudnn9', 'libnvinfer10', 'linux-image-6.8.0-90-generic',
                 'linux-signed-generic', 'dgx-tools', 'linux-firmware', 'mlnx-ofed-kernel-utils', 'libibverbs1', 'libxnvctrl0']
        for name in names:
            with self.subTest(name=name):
                self.rows = [(name, '1.2-1', '1.2-2')]
                plan, protected = self.prepare()
                self.assertEqual(protected, [name])
                self.assertTrue(plan['requires_approval'])
                self.assertEqual(plan['policy']['operation'], 'update')
                self.assertEqual(plan['policy']['updates'][0]['installed'], '1.2-1')

    def test_wrong_host_non_root_and_invalid_ttl_refused_before_package_queries(self):
        self.hostname = b'home-core\n'
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        with patch.object(spark.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError): spark.prepare(runner=self.runner)
        self.assertEqual(self.calls, [])
        for kwargs in [{'host': 'other'}, {'ttl': True}, {'ttl': 59}, {'ttl': 3601}]:
            with self.assertRaises(ValueError): self.prepare(**kwargs)
        self.assertEqual(self.calls, [])

    def test_read_command_failures_and_overflow_do_not_create_plans(self):
        for output, code in [(b'bad', 1), (b'x' * (spark.MAX_OUTPUT + 1), 0)]:
            runner = lambda argv, **kwargs: subprocess.CompletedProcess(argv, code, output, b'')
            with self.assertRaises(ValueError): spark.read_command([spark.APT, '-s', 'upgrade'], runner)

    def test_output_refuses_overwrite_and_unprivileged_writes(self):
        plan, _ = self.prepare()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'plan.json'
            with patch.object(spark.os, 'geteuid', return_value=0), patch.object(spark, 'trusted_path') as trusted:
                spark.write_plan(path, plan)
                trusted.assert_called_once_with(path.parent, 0, directory=True)
                self.assertEqual(json.loads(path.read_text()), plan)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError): spark.write_plan(path, plan)
            with patch.object(spark.os, 'geteuid', return_value=1000):
                with self.assertRaises(ValueError): spark.write_plan(path, plan)


if __name__ == '__main__':
    unittest.main()
