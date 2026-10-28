#!/usr/bin/env python3
"""Exercise the fixed-host updater with real temporary files and a fake controller."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location('controls_setup', Path(__file__).resolve().parents[1] / 'scripts/setup-comfyui-model-controls.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


def terminal(mode):
    return dict(mode=mode, text_running=mode == 'text', text_ready=mode == 'text',
                comfy_running=mode != 'text', comfy_ready=mode != 'text',
                queue_running=0, queue_pending=0, transition=None, error=None,
                idle_timeout_seconds=1800)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.root = self.base / 'installed'
        self.backups = self.base / 'backups'
        self.root.mkdir()
        self.original = {}
        for name, relative in SETUP.FILES.items():
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('NEW = True\n' if name.endswith('.py') else '<!doctype html><title>Spark models</title>')
            if name != 'comfyui_control_http.py':
                self.original[name] = b'OLD = True\n' if name.endswith('.py') else b'old page'
                (self.root / name).write_bytes(self.original[name])
        self.events = []
        self.state = terminal('text')
        self.logs = io.StringIO()
        self.patches = [
            patch.object(SETUP, 'ROOT', self.root), patch.object(SETUP, 'BACKUPS', self.backups),
            patch.object(SETUP.platform, 'system', return_value='Linux'),
            patch.object(SETUP.socket, 'gethostname', return_value='home-spark'),
            patch.object(SETUP.os, 'geteuid', return_value=0),
            patch.object(SETUP.sys, 'stderr', self.logs),
            # Temporary files belong to the developer, not root. Symlink/type
            # checks and real atomic writes remain active in these tests.
            patch.object(SETUP, 'root_owned'), patch.object(SETUP.os, 'fchown'), patch.object(SETUP.os, 'chown'),
            patch.object(SETUP, 'api', side_effect=self.api), patch.object(SETUP, 'restart', side_effect=self.restart),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def api(self, path, *, post=False):
        self.events.append(('POST' if post else 'GET', path))
        if not post:
            result = dict(self.state)
            if (self.root / 'comfyui_control_http.py').exists():
                result['target_mode'] = result.get('target_mode', result['mode'])
            return result
        if path == '/start':
            self.state = terminal('image')  # The old controller finishes its drain.
        elif path.startswith('/start/'):
            target = path.rsplit('/', 1)[-1]
            if target in ('image', 'video'):
                self.state = {**terminal(target), 'target_mode': target}
            else:
                self.state.update(mode='stopping', target_mode='text')
        elif path == '/stop':
            self.state.update(mode='stopping', target_mode='text')
        else:
            raise AssertionError(path)
        return dict(self.state)

    def restart(self):
        self.events.append(('restart', SETUP.UNIT))

    def assert_originals(self):
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, self.original)

    def test_default_plan_is_read_only_and_names_the_exact_five_files(self):
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*'))
        plan = SETUP.run(self.source)
        self.assertTrue(plan['read_only'])
        self.assertEqual(set(plan['source_sha256']), set(SETUP.FILES))
        self.assertEqual(plan['unit'], 'homecompute-comfyui-control.service')
        self.assertEqual(len(plan['plan_sha256']), 64)
        self.assertEqual(self.events, [])
        self.assertEqual(before, sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*')))
        self.assert_originals()

    def test_unprivileged_plan_does_not_traverse_the_private_backup_directory(self):
        original_safe = SETUP.safe
        def safe(path, **options):
            if path == self.backups:
                raise PermissionError('root-only backup directory')
            return original_safe(path, **options)
        with patch.object(SETUP.os, 'geteuid', return_value=1000), patch.object(SETUP, 'safe', side_effect=safe):
            plan = SETUP.run(self.source)
        self.assertTrue(plan['read_only'])
        self.assertEqual(self.events, [])
        self.assert_originals()
        with patch.object(SETUP, 'safe', side_effect=safe), self.assertRaisesRegex(PermissionError, 'root-only'):
            SETUP.run(self.source, do_apply=True)
        self.assertEqual(self.events, [])

    def test_text_is_drained_before_publication_and_restart_then_restored_async(self):
        original_publish = SETUP.publish
        def publish(path, data, mode=0o644):
            if path.parent == self.root:
                self.assertIn(('POST', '/start'), self.events)
                self.assertEqual(self.state['mode'], 'image')
                self.events.append(('write', path.name))
            return original_publish(path, data, mode)
        with patch.object(SETUP, 'publish', side_effect=publish):
            result = SETUP.run(self.source, do_apply=True)
        self.assertLess(self.events.index(('POST', '/start')), self.events.index(('write', 'comfyui_lifecycle.py')))
        self.assertLess(self.events.index(('write', 'index.html')), self.events.index(('restart', SETUP.UNIT)))
        self.assertEqual(self.events[-1], ('POST', '/start/text'))
        self.assertEqual(result['status']['mode'], 'stopping')
        self.assertEqual(result['restore_requested'], 'text')
        self.assertEqual([event for event in self.events if event[0] == 'POST'], [
            ('POST', '/start'), ('POST', '/start/video'), ('POST', '/start/image'), ('POST', '/start/text')])
        self.assertEqual(result['qualification'], {
            'performed': True, 'image_to_video': 'passed', 'video_to_image': 'passed'})
        manifest = json.loads((Path(result['backup']) / 'manifest.json').read_text())
        self.assertEqual(manifest['initial_mode'], 'text')
        self.assertEqual(manifest['installed_sha256']['comfyui_control_http.py'], None)
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)

    def test_apply_logs_clear_steps_to_stderr_without_polluting_stdout(self):
        output = io.StringIO()
        with patch.object(SETUP.sys, 'stdout', output):
            result = SETUP.run(self.source, do_apply=True)
            self.assertEqual(output.getvalue(), '')
            print(json.dumps(result))
        self.assertEqual(json.loads(output.getvalue()), result)
        messages = self.logs.getvalue()
        for step in ('Preflight', 'Draining LLM work safely', 'Backing up', 'Publishing the five',
                     'Restarting homecompute-comfyui-control.service', 'Qualifying image-to-video',
                     'Qualifying video-to-image', 'Requesting restoration', 'Update complete'):
            self.assertIn(step, messages)

    def test_idle_media_is_preserved_without_starting_text(self):
        for mode in ('image', 'video'):
            with self.subTest(mode=mode):
                self.state = terminal(mode)
                self.events.clear()
                result = SETUP.run(self.source, do_apply=True)
                self.assertEqual(result['restore_requested'], mode)
                self.assertFalse(result['qualification']['performed'])
                self.assertEqual([event for event in self.events if event[0] == 'POST'], [('POST', '/start/' + mode)])

    def test_resume_pending_text_drain_does_not_submit_a_duplicate_start(self):
        self.state = {**terminal('text'), 'mode': 'starting', 'transition': {
            'action': 'start', 'phase': 'waiting_workflows', 'workflows': 1, 'agent_turns': 0}}
        original_api, gets = self.api, 0
        def api(path, *, post=False):
            nonlocal gets
            if not post:
                gets += 1
                if gets == 2:
                    self.state = terminal('image')
            return original_api(path, post=post)
        with patch.object(SETUP, 'api', side_effect=api):
            result = SETUP.run(self.source, do_apply=True, resume_text_drain=True)
        self.assertEqual(result['restore_requested'], 'text')
        self.assertTrue(result['qualification']['performed'])
        self.assertEqual([event for event in self.events if event[0] == 'POST'], [
            ('POST', '/start/video'), ('POST', '/start/image'), ('POST', '/start/text')])

    def test_resume_idle_image_qualifies_media_and_restores_text(self):
        self.state = terminal('image')
        plan = SETUP.run(self.source)
        resumed_plan = SETUP.run(self.source, resume_text_drain=True)
        self.assertTrue(resumed_plan['resume_text_drain'])
        self.assertEqual(plan['plan_sha256'], resumed_plan['plan_sha256'])
        result = SETUP.run(self.source, do_apply=True, resume_text_drain=True, expected_plan=plan['plan_sha256'])
        self.assertEqual(result['restore_requested'], 'text')
        self.assertTrue(result['qualification']['performed'])
        self.assertNotIn(('POST', '/start'), self.events)
        manifest = json.loads((Path(result['backup']) / 'manifest.json').read_text())
        self.assertEqual(manifest['initial_mode'], 'text')

    def test_resume_rejects_unrelated_or_inconsistent_transitions_and_busy_media(self):
        pending = {**terminal('text'), 'mode': 'starting', 'target_mode': 'image',
                   'transition': {'action': 'start', 'phase': 'waiting_workflows'}}
        for state in (
            terminal('text'), terminal('video'),
            {**terminal('image'), 'queue_running': 1},
            {**terminal('image'), 'queue_pending': None},
            {**pending, 'mode': 'stopping'},
            {**pending, 'target_mode': 'video'},
            {**pending, 'comfy_running': True},
            {**pending, 'text_running': False},
            {**pending, 'transition': {'action': 'stop', 'phase': 'waiting_workflows'}},
            {**pending, 'transition': {'action': 'start', 'phase': 'waiting_text'}},
            {**pending, 'transition': {'action': 'start', 'phase': 'future_phase'}},
        ):
            with self.subTest(state=state):
                self.state = state
                self.events.clear()
                with self.assertRaises(ValueError):
                    SETUP.run(self.source, do_apply=True, resume_text_drain=True)
                self.assertFalse(any(event[0] in ('POST', 'restart') for event in self.events))
                self.assertFalse(self.backups.exists())
                self.assert_originals()

    def test_resume_still_rejects_changes_to_the_reviewed_source(self):
        self.state = terminal('image')
        plan = SETUP.run(self.source)
        (self.source / 'scripts/comfyui_control_http.py').write_text('CHANGED = True')
        with self.assertRaisesRegex(ValueError, 'differ from the reviewed plan'):
            SETUP.run(self.source, do_apply=True, resume_text_drain=True, expected_plan=plan['plan_sha256'])
        self.assertEqual(self.events, [])

    def test_media_qualification_failure_rolls_back_before_requesting_original_text(self):
        original_api = self.api
        def api(path, *, post=False):
            if post and path == '/start/video':
                self.events.append(('POST', path))
                raise OSError('video qualification failed')
            return original_api(path, post=post)
        with patch.object(SETUP, 'api', side_effect=api), self.assertRaisesRegex(RuntimeError, 'video qualification failed'):
            SETUP.run(self.source, do_apply=True)
        self.assert_originals()
        self.assertEqual(self.events.count(('restart', SETUP.UNIT)), 2)
        self.assertNotIn(('POST', '/start/image'), self.events)
        self.assertEqual(self.events[-1], ('POST', '/start/text'))

    def test_busy_or_unknown_queue_refuses_before_writes_or_actions(self):
        for count in (1, None, False):
            self.state = {**terminal('image'), 'queue_running': count}
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'queue is busy or unknown'):
                SETUP.run(self.source, do_apply=True)
            self.assertFalse(self.backups.exists())
            self.assertFalse(any(event[0] in ('POST', 'restart') for event in self.events))
            self.assert_originals()

    def test_final_queue_recheck_can_refuse_after_drain_without_publishing(self):
        normal_api = self.api
        gets = 0
        def api(path, *, post=False):
            nonlocal gets
            result = normal_api(path, post=post)
            if not post:
                gets += 1
                if gets == 3:
                    result['queue_pending'] = 1
            return result
        with patch.object(SETUP, 'api', side_effect=api), self.assertRaisesRegex(RuntimeError, 'queue is busy'):
            SETUP.run(self.source, do_apply=True)
        self.assert_originals()
        self.assertNotIn(('restart', SETUP.UNIT), self.events)
        self.assertEqual(self.events[-1], ('POST', '/start/text'))

    def test_partial_publication_failure_rolls_back_new_and_existing_files(self):
        original_publish = SETUP.publish
        failed = False
        def publish(path, data, mode=0o644):
            nonlocal failed
            if path == self.root / 'comfyui_progress.py' and not failed:
                failed = True
                raise OSError('simulated disk write failure')
            return original_publish(path, data, mode)
        with patch.object(SETUP, 'publish', side_effect=publish), self.assertRaisesRegex(RuntimeError, 'disk write failure'):
            SETUP.run(self.source, do_apply=True)
        self.assert_originals()
        self.assertEqual(self.events[-1], ('POST', '/start/text'))
        self.assertTrue(list(self.backups.glob('*/manifest.json')))

    def test_restart_failure_restores_original_service_files(self):
        count = 0
        def restart():
            nonlocal count
            count += 1
            self.events.append(('restart', SETUP.UNIT))
            if count == 1:
                raise OSError('restart failed')
        with patch.object(SETUP, 'restart', side_effect=restart), self.assertRaisesRegex(RuntimeError, 'restart failed'):
            SETUP.run(self.source, do_apply=True)
        self.assertEqual(count, 2)
        self.assert_originals()
        self.assertEqual(self.events[-1], ('POST', '/start/text'))

    def test_rollback_uses_legacy_stop_when_old_controller_has_no_profile_routes(self):
        original_api = self.api
        def api(path, *, post=False):
            if post and path == '/start/text':
                self.events.append(('POST', path))
                raise urllib.error.HTTPError('http://127.0.0.1:8189', 404, 'old controller', {}, None)
            return original_api(path, post=post)
        with patch.object(SETUP, 'api', side_effect=api), patch.object(SETUP, 'restart', side_effect=[OSError('new service failed'), None]):
            with self.assertRaisesRegex(RuntimeError, 'new service failed'):
                SETUP.run(self.source, do_apply=True)
        self.assert_originals()
        self.assertEqual(self.events[-1], ('POST', '/stop'))

    def test_symlinked_installation_directory_is_rejected(self):
        alias = self.base / 'installation-link'
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(SETUP, 'ROOT', alias), self.assertRaisesRegex(ValueError, 'Symlink'):
            SETUP.run(self.source, do_apply=True)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.events, [])
        self.assert_originals()

    def test_symlinked_target_source_and_backup_are_preserved_without_actions(self):
        outside = self.base / 'outside'
        outside.write_text('do not change')
        for path in (self.root / 'comfyui_control_http.py', self.source / 'scripts/comfyui_control_http.py', self.backups):
            with self.subTest(path=path):
                original = path.read_bytes() if path.exists() else None
                path.unlink(missing_ok=True)
                path.symlink_to(outside)
                with self.assertRaisesRegex(ValueError, 'Symlink'):
                    SETUP.run(self.source, do_apply=True)
                self.assertTrue(path.is_symlink())
                self.assertEqual(outside.read_text(), 'do not change')
                self.assertEqual(self.events, [])
                path.unlink()
                if original:
                    path.write_bytes(original)

    def test_wrong_host_and_non_root_apply_have_no_actions(self):
        with patch.object(SETUP.socket, 'gethostname', return_value='home-core'), self.assertRaisesRegex(ValueError, 'home-spark'):
            SETUP.run(self.source, do_apply=True)
        with patch.object(SETUP.platform, 'system', return_value='Darwin'), self.assertRaisesRegex(ValueError, 'Linux'):
            SETUP.run(self.source)
        with patch.object(SETUP.os, 'geteuid', return_value=1000), self.assertRaisesRegex(ValueError, 'root'):
            SETUP.run(self.source, do_apply=True)
        self.assertEqual(self.events, [])
        self.assert_originals()

    def test_missing_source_and_syntax_errors_fail_before_actions(self):
        path = self.source / 'scripts/comfyui_control_http.py'
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            SETUP.run(self.source, do_apply=True)
        path.write_text('this is invalid python !!!')
        with self.assertRaises(SyntaxError):
            SETUP.run(self.source, do_apply=True)
        self.assertEqual(self.events, [])
        self.assert_originals()

    def test_expected_plan_refuses_changed_source_or_baseline(self):
        plan = SETUP.run(self.source)
        (self.source / 'scripts/comfyui_control_http.py').write_text('CHANGED = True')
        with self.assertRaisesRegex(ValueError, 'differ from the reviewed plan'):
            SETUP.run(self.source, do_apply=True, expected_plan=plan['plan_sha256'])
        plan = SETUP.run(self.source)
        (self.root / 'index.html').write_text('changed outside updater')
        with self.assertRaisesRegex(ValueError, 'differ from the reviewed plan'):
            SETUP.run(self.source, do_apply=True, expected_plan=plan['plan_sha256'])
        self.assertEqual(self.events, [])


class OwnershipTest(unittest.TestCase):
    def test_untrusted_owner_and_writable_paths_are_rejected(self):
        with patch.object(SETUP, 'root_owned', wraps=SETUP.root_owned):
            SETUP.root_owned(Path('/example'), SimpleNamespace(st_uid=0, st_mode=0o100644))
            for uid, mode in ((1000, 0o100644), (0, 0o100664), (0, 0o100646)):
                with self.assertRaises(ValueError):
                    SETUP.root_owned(Path('/example'), SimpleNamespace(st_uid=uid, st_mode=mode))


class WaitLogTest(unittest.TestCase):
    def wait(self, values):
        now = 0
        def sleep(seconds):
            nonlocal now
            now += seconds
        stderr, stdout = io.StringIO(), io.StringIO()
        with (patch.object(SETUP, 'api', side_effect=values),
              patch.object(SETUP.time, 'monotonic', side_effect=lambda: now),
              patch.object(SETUP.time, 'sleep', side_effect=sleep),
              patch.object(SETUP.sys, 'stderr', stderr), patch.object(SETUP.sys, 'stdout', stdout)):
            result = SETUP.wait_for('image', seconds=120)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(result['mode'], 'image')
        return stderr.getvalue()

    def test_wait_reports_count_and_phase_changes_plus_30_second_heartbeat(self):
        waiting = lambda count: {**terminal('text'), 'mode': 'starting', 'transition': {
            'phase': 'waiting_workflows', 'workflows': count, 'agent_turns': 0}}
        draining = {**terminal('text'), 'mode': 'starting', 'transition': {
            'phase': 'draining_text', 'text_requests': {'running': 1, 'waiting': 0}}}
        logs = self.wait([waiting(2), waiting(1), *([draining] * 16), terminal('image')])
        self.assertIn('Waiting for healthy image mode', logs)
        self.assertIn('0s elapsed: mode=starting; phase=waiting_workflows; workflows=2', logs)
        self.assertIn('2s elapsed: mode=starting; phase=waiting_workflows; workflows=1', logs)
        self.assertIn('4s elapsed: mode=starting; phase=draining_text', logs)
        self.assertIn('text_running=1; text_queued=0', logs)
        self.assertIn('34s elapsed: mode=starting; phase=draining_text', logs)
        self.assertIn('Healthy image mode confirmed after 36s', logs)
        self.assertEqual(len(logs.splitlines()), 7, 'Stable status should not log at every two-second poll')

    def test_status_failures_have_bounded_retries_without_logging_error_contents(self):
        logs = self.wait([*([OSError('private backend error contents')] * 16), terminal('image')])
        self.assertIn('0s elapsed: Status unavailable', logs)
        self.assertIn('30s elapsed: Status unavailable', logs)
        self.assertIn('Healthy image mode confirmed after 32s', logs)
        self.assertNotIn('private backend error contents', logs)
        self.assertEqual(len(logs.splitlines()), 5)

    def test_extended_drain_allows_active_work_beyond_30_minutes(self):
        now = 0
        def sleep(_seconds):
            nonlocal now
            now += 2000
        pending = {**terminal('text'), 'mode': 'starting', 'transition': {
            'action': 'start', 'phase': 'waiting_workflows', 'workflows': 1, 'agent_turns': 0}}
        with (patch.object(SETUP, 'api', side_effect=[pending, pending, terminal('image')]),
              patch.object(SETUP.time, 'monotonic', side_effect=lambda: now),
              patch.object(SETUP.time, 'sleep', side_effect=sleep),
              patch.object(SETUP.sys, 'stderr', io.StringIO())):
            self.assertEqual(SETUP.wait_for('image', seconds=None)['mode'], 'image')
        self.assertEqual(now, 4000)

    def test_extended_wait_becomes_bounded_after_drain_or_status_loss(self):
        pending = {**terminal('text'), 'mode': 'starting', 'transition': {
            'action': 'start', 'phase': 'waiting_workflows', 'workflows': 1}}
        readiness = {**terminal('image'), 'mode': 'starting', 'transition': {
            'action': 'start', 'phase': 'waiting_comfyui'}}
        for after_drain in (readiness, OSError('status unavailable')):
            now = 0
            def sleep(_seconds):
                nonlocal now
                now += 1000
            with (self.subTest(after_drain=after_drain),
                  patch.object(SETUP, 'api', side_effect=[pending, after_drain, after_drain]),
                  patch.object(SETUP.time, 'monotonic', side_effect=lambda: now),
                  patch.object(SETUP.time, 'sleep', side_effect=sleep),
                  patch.object(SETUP.sys, 'stderr', io.StringIO()),
                  self.assertRaises(TimeoutError)):
                SETUP.wait_for('image', seconds=None)
            self.assertEqual(now, 3000)

    def test_unbounded_wait_is_not_available_for_model_readiness_or_arbitrary_transitions(self):
        for mode, upgraded in (('text', False), ('video', False), ('image', True)):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'only permitted'):
                SETUP.wait_for(mode, upgraded=upgraded, seconds=None)
        invalid = {**terminal('text'), 'mode': 'starting', 'transition': {'action': 'stop', 'phase': 'waiting_workflows'}}
        for value in (invalid, {**invalid, 'mode': 'image'}):
            with patch.object(SETUP, 'api', return_value=value), patch.object(SETUP.sys, 'stderr', io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'approved text-to-image drain'):
                    SETUP.wait_for('image', seconds=None)


if __name__ == '__main__':
    unittest.main()
