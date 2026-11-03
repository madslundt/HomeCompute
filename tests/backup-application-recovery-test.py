#!/usr/bin/env python3
"""Cold backup coordination with the real admission-gate and NAS lifecycle seams."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backup_operator', ROOT / 'scripts/backup-application-recovery.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class BackupCoordinationTest(unittest.TestCase):
    def exercise(self, *, status=None, drain_failure=False, backup_failure=False, cleanup_failure=False):
        events = []
        @contextmanager
        def locked(_):
            events.append('lock')
            yield
        @contextmanager
        def maintenance(timeout):
            events.append('admission-held')
            try:
                if drain_failure: raise ValueError('active inference')
                events.append('drained')
                yield
            finally: events.append('admission-released')
        def run(args, **kwargs):
            if args[:2] == ['systemctl', 'show']:
                fields = status or {'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','ExecMainStatus':'0'}
                return subprocess.CompletedProcess(args, 0, '\n'.join(k+'='+v for k,v in fields.items()), '')
            if args[:2] == ['systemctl', 'stop']:
                events.append('backend-stop-and-resume')
                if cleanup_failure: raise RuntimeError('resume failed')
            if args[:3] == ['systemctl', 'start', '--no-block']:
                events.append('guarded-backup')
            return subprocess.CompletedProcess(args, 0, '', '')
        def wait_phase(nas, started, deadline, *, until_cold):
            if backup_failure: raise InterruptedError('client interrupted')
            events.append('wait-cold' if until_cold else 'wait-NAS-completion')
            if until_cold: return None
            return {'time':'2026-11-03T00:00:00Z'}
        def busy():
            events.append('backend-idle-checked')
            if status and status['SubState'] == 'running':raise ValueError('active backup')
        nas = SimpleNamespace(STATE=Path('/private'), locked=locked,
                              installed=lambda h: {'version':'/reviewed','qemu_bin':'/reviewed/bin'},
                              safe=lambda p: None, PYTHON='/usr/bin/python3', environment=lambda p: {},
                              run=run, busy=busy)
        drain = SimpleNamespace(core_maintenance=maintenance,run=lambda *a,**k: '')
        with patch.object(helper, 'published'), patch.object(helper, 'load', side_effect=[nas, drain]), \
                patch.object(helper, 'wait_phase', side_effect=wait_phase):
            try:
                result = helper.backup(ROOT, 'a'*40, 'b'*64, 'c'*64, True, 120)
            except BaseException:
                self.events = events
                raise
        self.events = events
        self.assertEqual(drain.MAINTENANCE_CHAIN, 'HC-RECOVERY-DRAIN')
        return result

    def test_backup_cannot_start_before_admission_is_held_and_drained(self):
        result = self.exercise()
        self.assertTrue(result['backup_completed'])
        self.assertEqual(self.events, ['lock','backend-idle-checked','admission-held','drained',
                                       'backend-idle-checked','guarded-backup','wait-cold',
                                       'admission-released','wait-NAS-completion'])

    def test_active_inference_aborts_without_stopping_applications_or_starting_backup(self):
        with self.assertRaisesRegex(ValueError, 'active inference'):self.exercise(drain_failure=True)
        self.assertNotIn('guarded-backup', self.events)
        self.assertNotIn('backend-stop-and-resume', self.events)
        self.assertEqual(self.events[-1], 'admission-released')

    def test_interruption_resumes_backend_before_releasing_admission(self):
        with self.assertRaises(InterruptedError):self.exercise(backup_failure=True)
        self.assertEqual(self.events[-2:], ['backend-stop-and-resume','admission-released'])

    def test_failed_backend_cleanup_cannot_be_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, 'resume failed'):
            self.exercise(backup_failure=True, cleanup_failure=True)

    def test_only_idle_tempfail_retry_can_be_cancelled(self):
        self.exercise(status={'LoadState':'loaded','ActiveState':'activating','SubState':'auto-restart','ExecMainStatus':'75'})
        self.assertLess(self.events.index('drained'),self.events.index('backend-stop-and-resume'))
        with self.assertRaisesRegex(ValueError,'active inference'):
            self.exercise(status={'LoadState':'loaded','ActiveState':'activating','SubState':'auto-restart','ExecMainStatus':'75'},drain_failure=True)
        self.assertNotIn('backend-stop-and-resume',self.events)
        with self.assertRaisesRegex(ValueError, 'active backup'):
            self.exercise(status={'LoadState':'loaded','ActiveState':'active','SubState':'running','ExecMainStatus':'0'})
        self.assertNotIn('backend-stop-and-resume', self.events)

    def test_unacknowledged_or_unbounded_attempt_never_loads_lifecycle(self):
        for acknowledge, timeout in [(False,120),(True,2),(True,601)]:
            with patch.object(helper, 'published') as published, self.assertRaises(ValueError):
                helper.backup(ROOT,'a'*40,'b'*64,'c'*64,acknowledge,timeout)
            published.assert_not_called()

    def test_unpublished_or_mismatched_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'reviewed immutable'):
            helper.published(ROOT,'a'*40,helper.bundle(ROOT))

    def test_cold_phase_does_not_claim_NAS_completion(self):
        nas=SimpleNamespace(run=lambda *a,**k: subprocess.CompletedProcess([],0,'false\n',''))
        with patch.object(helper,'state',return_value={'ActiveState':'active','SubState':'running'}):
            self.assertIsNone(helper.wait_phase(nas,time.time(),time.monotonic()+1,until_cold=True))

    def test_success_requires_fresh_receipt_and_successful_unit_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'last-backup.json').write_text('{"time":"synthetic"}')
            nas=SimpleNamespace(STATE=root)
            for result,code in [('exit-code','0'),('success','75')]:
                with patch.object(helper,'state',return_value={'ActiveState':'inactive','Result':result,'ExecMainStatus':code}), \
                        self.assertRaisesRegex(RuntimeError,'successful backup settlement'):
                    helper.wait_phase(nas,time.time()-1,time.monotonic()+1,until_cold=False)
            with patch.object(helper,'state',return_value={'ActiveState':'inactive','Result':'success','ExecMainStatus':'0'}):
                self.assertEqual(helper.wait_phase(nas,time.time()-1,time.monotonic()+1,until_cold=False),{'time':'synthetic'})
            (root/'resume.json').write_text('[]')
            with patch.object(helper,'state',return_value={'ActiveState':'inactive','Result':'success','ExecMainStatus':'0'}), \
                    self.assertRaisesRegex(RuntimeError,'resumption'):
                helper.wait_phase(nas,time.time()-1,time.monotonic()+1,until_cold=False)

    def test_deferred_backend_cannot_be_replayed_while_gate_is_released(self):
        with patch.object(helper,'state',return_value={'ActiveState':'activating','SubState':'auto-restart'}), \
                self.assertRaisesRegex(RuntimeError,'no automatic replay'):
            helper.wait_phase(SimpleNamespace(),time.time(),time.monotonic()+1,until_cold=False)


if __name__ == '__main__':
    unittest.main()
