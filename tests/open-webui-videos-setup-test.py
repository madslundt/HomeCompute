#!/usr/bin/env python3
"""Exercise video-function installation without a real container or admin session."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('video_setup', Path(__file__).resolve().parents[1] / 'scripts/setup-open-webui-videos.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)
CODE = 'class Pipe:\n    async def pipe(self, body):\n        return "video"\n'
OLD_CODE = CODE.replace('"video"', '"old video"')


class WebUI:
    def __init__(self, existing=False):
        self.record = None
        self.model = None
        self.valves = None
        self.preserved = {'images': 'image-config-hash', 'providers': 'provider-config-hash', 'tasks': 'task-config-hash'}
        self.calls = []
        self.missing_model = False
        self.change_valves_on_update = False
        if existing:
            desired = SETUP.candidate(self.snapshot(), OLD_CODE)
            self.record = {**desired, 'type': 'pipe', 'user_id': 'admin', 'is_active': True, 'is_global': False,
                           'updated_at': 1, 'created_at': 1}
            self.valves = {'private_value': 'synthetic-valve-value'}

    def snapshot(self):
        return deepcopy({'function': self.record, 'valves': self.valves, 'model': self.model, 'preserved': self.preserved})

    def api(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        prefix = f'/api/v1/functions/id/{SETUP.PIPE_ID}'
        if method == 'POST' and path in ('/api/v1/functions/create', prefix + '/update'):
            self.record = {**(self.record or {}), **deepcopy(body), 'type': 'pipe', 'user_id': 'admin',
                           'is_active': self.record.get('is_active', False) if self.record else False,
                           'is_global': False, 'updated_at': 2, 'created_at': 1}
            self.record['meta']['manifest'] = {'title': 'server-derived manifest'}
            self.valves = {'changed': True} if self.change_valves_on_update else self.valves or {}
        elif method == 'POST' and path == prefix + '/toggle':
            self.record['is_active'] = not self.record['is_active']
        elif method == 'POST' and path == prefix + '/valves/update':
            self.valves = deepcopy(body['valves'])
        elif method == 'DELETE' and path == prefix + '/delete':
            self.record = self.valves = None
        elif method == 'POST' and path in ('/api/v1/models/create', '/api/v1/models/model/update'):
            self.model = {**deepcopy(body), 'user_id': 'admin', 'created_at': 1, 'updated_at': 2}
            for index, grant in enumerate(self.model['access_grants']):
                grant['id'] = 'server-generated-' + str(index)
        elif method == 'POST' and path == '/api/v1/models/model/delete':
            assert body == {'id': SETUP.PIPE_ID}
            self.model = None
        elif method == 'GET' and path == '/api/models?refresh=true':
            return {'data': [] if self.missing_model or not self.record or not self.record['is_active'] else [{'id': SETUP.PIPE_ID, 'info': self.model}]}
        else:
            raise AssertionError(f'Unexpected administrative API: {method} {path}')
        return deepcopy(self.record)


class FunctionTest(unittest.TestCase):
    def test_workspace_capabilities_disable_inherited_image_tools_and_share_same_id_read_only(self):
        before = WebUI().snapshot()
        model = SETUP.model_candidate(before)
        self.assertEqual(model['id'], 'homecompute_videos')
        self.assertIsNone(model['base_model_id'])
        self.assertEqual(model['meta']['capabilities'], {
            'vision': True, 'image_generation': False, 'builtin_tools': False,
            'web_search': False, 'code_interpreter': False, 'file_context': False})
        self.assertEqual(model['meta']['defaultFeatureIds'], [])
        self.assertTrue(model['meta'][SETUP.MARKER])
        self.assertEqual(model['meta']['description'], SETUP.DESCRIPTION)
        self.assertIn('Video mode starts after active work finishes', model['meta']['description'])
        self.assertIn('upload a starting picture and describe its motion', model['meta']['description'])
        self.assertTrue(model['meta']['suggestion_prompts'][0]['content'])
        self.assertIn('Animate the uploaded photo', model['meta']['suggestion_prompts'][1]['content'])
        self.assertEqual(model['access_grants'], [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}])

    def test_adding_only_workspace_metadata_preserves_the_live_function_and_existing_valves(self):
        webui = WebUI(existing=True)
        before = webui.snapshot()
        desired = SETUP.candidate(before, OLD_CODE)
        result = SETUP.apply_function(before, desired, webui.api, webui.snapshot)
        self.assertTrue(result['ok'])
        self.assertEqual(webui.record, before['function'])
        self.assertEqual(webui.valves, before['valves'])
        self.assertEqual([(method, path) for method, path, _ in webui.calls], [
            ('POST', '/api/v1/models/create'), ('GET', '/api/models?refresh=true')])
        self.assertEqual(webui.model['base_model_id'], None)

    def test_owned_workspace_update_preserves_custom_params_and_permission_meaning(self):
        webui = WebUI(existing=True)
        webui.model = {'id': SETUP.PIPE_ID, 'base_model_id': None, 'name': 'Old video name', 'is_active': True,
                       'params': {'custom_setting': 'preserve me'},
                       'meta': {SETUP.MARKER: True, 'custom_note': 'preserve me', 'defaultFeatureIds': ['image_generation']},
                       'access_grants': [{'id': 'old-grant-id', 'principal_type': 'user', 'principal_id': 'admin', 'permission': 'write'}]}
        before = webui.snapshot()
        desired = SETUP.model_candidate(before)
        result = SETUP.apply_function(before, SETUP.candidate(before, OLD_CODE), webui.api, webui.snapshot, desired)
        self.assertTrue(result['ok'])
        self.assertEqual(webui.model['params'], {'custom_setting': 'preserve me'})
        self.assertEqual(webui.model['meta']['custom_note'], 'preserve me')
        self.assertEqual(webui.model['meta']['defaultFeatureIds'], [])
        self.assertTrue(SETUP.model_matches(webui.model, desired))
        self.assertEqual({(grant['principal_id'], grant['permission']) for grant in webui.model['access_grants']},
                         {('admin', 'write'), ('*', 'read')})

    def test_unmanaged_workspace_id_collision_is_never_overwritten(self):
        webui = WebUI(existing=True)
        webui.model = {'id': SETUP.PIPE_ID, 'meta': {}, 'name': 'Someone else owns this'}
        before = webui.snapshot()
        with self.assertRaisesRegex(ValueError, 'unmanaged video Workspace model'):
            SETUP.apply_function(before, SETUP.candidate(before, OLD_CODE), webui.api, webui.snapshot)
        self.assertEqual(webui.calls, [])
        self.assertEqual(webui.model, before['model'])

    def test_model_failure_restores_prior_owned_capabilities_without_rewriting_the_function(self):
        webui = WebUI(existing=True)
        webui.model = SETUP.model_candidate(webui.snapshot())
        webui.model['meta']['capabilities']['image_generation'] = True
        webui.model['meta']['defaultFeatureIds'] = ['image_generation']
        webui.missing_model = True
        before = webui.snapshot()
        result = SETUP.apply_function(before, SETUP.candidate(before, OLD_CODE), webui.api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'complete')
        self.assertTrue(SETUP.model_matches(webui.model, SETUP.model_form(before['model'])))
        self.assertEqual(webui.record, before['function'])
        self.assertEqual(webui.valves, before['valves'])
        self.assertFalse(any('/functions/' in path for _, path, _ in webui.calls))

    def test_recovery_preserves_concurrent_workspace_metadata_changes(self):
        webui = WebUI(existing=True)
        before, original_api = webui.snapshot(), webui.api
        def api(method, path, body=None):
            result = original_api(method, path, body)
            if method == 'GET':
                webui.model['meta']['manual_note'] = 'another administrator changed this'
                raise OSError('verification failed')
            return result
        result = SETUP.apply_function(before, SETUP.candidate(before, OLD_CODE), api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'failed; private backup retained')
        self.assertEqual(webui.model['meta']['manual_note'], 'another administrator changed this')
        self.assertEqual(webui.record, before['function'])
        self.assertFalse(any(path.endswith('/model/delete') for _, path, _ in webui.calls))

    def test_new_pipe_is_registered_active_once_without_changing_other_settings(self):
        webui = WebUI()
        before = webui.snapshot()
        desired = SETUP.candidate(before, CODE)
        result = SETUP.apply_function(before, desired, webui.api, webui.snapshot)
        self.assertTrue(result['ok'])
        self.assertTrue(result['changed'])
        self.assertTrue(webui.record['is_active'])
        self.assertEqual(webui.record['id'], 'homecompute_videos')
        self.assertTrue(SETUP.owned(webui.record))
        self.assertEqual(webui.preserved, before['preserved'])
        self.assertEqual([(method, path) for method, path, _ in webui.calls], [
            ('POST', '/api/v1/functions/create'),
            ('POST', '/api/v1/functions/id/homecompute_videos/toggle'),
            ('POST', '/api/v1/models/create'),
            ('GET', '/api/models?refresh=true')])
        self.assertNotIn('access_grants', desired)

    def test_managed_update_preserves_valves_and_server_manifest_is_not_configuration_drift(self):
        webui = WebUI(existing=True)
        before = webui.snapshot()
        result = SETUP.apply_function(before, SETUP.candidate(before, CODE), webui.api, webui.snapshot)
        self.assertTrue(result['ok'])
        self.assertEqual(webui.record['content'], CODE)
        self.assertEqual(webui.valves, before['valves'])
        self.assertEqual(webui.preserved, before['preserved'])
        self.assertFalse(any(path.endswith('/toggle') for _, path, _ in webui.calls))

    def test_repeat_install_is_idempotent_and_does_not_toggle_or_rewrite(self):
        webui = WebUI()
        desired = SETUP.candidate(webui.snapshot(), CODE)
        SETUP.apply_function(webui.snapshot(), desired, webui.api, webui.snapshot)
        webui.calls.clear()
        before = webui.snapshot()
        result = SETUP.apply_function(before, SETUP.candidate(before, CODE), webui.api, webui.snapshot)
        self.assertTrue(result['ok'])
        self.assertFalse(result['changed'])
        self.assertEqual(webui.calls, [('GET', '/api/models?refresh=true', None)])
        self.assertEqual(webui.snapshot(), before)

    def test_managed_disabled_pipe_is_activated_without_broadening_global_access(self):
        webui = WebUI(existing=True)
        webui.record['is_active'] = False
        before = webui.snapshot()
        result = SETUP.apply_function(before, SETUP.candidate(before, OLD_CODE), webui.api, webui.snapshot)
        self.assertTrue(result['ok'])
        self.assertTrue(webui.record['is_active'])
        self.assertFalse(webui.record['is_global'])

    def test_colliding_unmanaged_or_manually_edited_function_is_refused(self):
        for change in ('unmanaged', 'edited', 'wrong-type'):
            webui = WebUI(existing=True)
            if change == 'unmanaged':
                webui.record['meta'].pop(SETUP.MARKER)
            elif change == 'edited':
                webui.record['content'] += '\n# manual edit\n'
            else:
                webui.record['type'] = 'filter'
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'unmanaged or manually modified'):
                SETUP.candidate(webui.snapshot(), CODE)
            self.assertEqual(webui.calls, [])

    def test_unexpected_multi_model_source_or_invalid_python_is_rejected(self):
        for code in ('class Other: pass', 'class Pipe: pass', 'class Pipe:\n    def pipes(self): return []', 'this is invalid python!!!'):
            with self.subTest(code=code), self.assertRaises((ValueError, SyntaxError)):
                SETUP.candidate(WebUI().snapshot(), code)

    def test_changed_snapshot_is_rejected_before_mutation(self):
        webui = WebUI(existing=True)
        before = webui.snapshot()
        desired = SETUP.candidate(before, CODE)
        webui.preserved['tasks'] = 'changed by another administrator'
        result = SETUP.apply_function(before, desired, webui.api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'preflight')
        self.assertEqual(webui.calls, [])

    def test_failed_model_verification_deletes_only_the_new_owned_function(self):
        webui = WebUI()
        before = webui.snapshot()
        webui.missing_model = True
        result = SETUP.apply_function(before, SETUP.candidate(before, CODE), webui.api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'complete')
        self.assertIsNone(webui.record)
        self.assertIsNone(webui.model)
        self.assertEqual(webui.preserved, before['preserved'])
        self.assertIn(('DELETE', '/api/v1/functions/id/homecompute_videos/delete', None), webui.calls)
        self.assertIn(('POST', '/api/v1/models/model/delete', {'id': SETUP.PIPE_ID}), webui.calls)

    def test_update_failure_restores_prior_content_activation_and_valves(self):
        webui = WebUI(existing=True)
        webui.change_valves_on_update = True
        before = webui.snapshot()
        result = SETUP.apply_function(before, SETUP.candidate(before, CODE), webui.api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'complete')
        self.assertEqual(webui.record['content'], before['function']['content'])
        self.assertEqual(webui.record['is_active'], before['function']['is_active'])
        self.assertEqual(webui.valves, before['valves'])
        self.assertIn(('POST', '/api/v1/functions/id/homecompute_videos/valves/update', {'valves': before['valves']}), webui.calls)

    def test_recovery_refuses_to_overwrite_a_concurrently_modified_function(self):
        webui = WebUI(existing=True)
        before, original_api = webui.snapshot(), webui.api
        def api(method, path, body=None):
            result = original_api(method, path, body)
            if method == 'GET':
                webui.record['content'] += '\n# concurrent manual edit'
                raise OSError('verification failed')
            return result
        result = SETUP.apply_function(before, SETUP.candidate(before, CODE), api, webui.snapshot)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'failed; private backup retained')
        self.assertTrue(webui.record['content'].endswith('# concurrent manual edit'))


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.function = self.source / SETUP.SOURCE
        self.function.parent.mkdir(parents=True)
        self.function.write_text(CODE)
        self.state = self.base / 'state'
        self.webui = WebUI()
        self.calls = []
        self.patches = [patch.object(SETUP, 'STATE', self.state),
                        patch.object(SETUP.platform, 'system', return_value='Linux'),
                        patch.object(SETUP.socket, 'gethostname', return_value='home-core'),
                        patch.object(SETUP.os, 'geteuid', return_value=0),
                        patch.object(SETUP, 'remote', side_effect=self.remote)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def remote(self, action, **values):
        self.calls.append(action)
        if action == 'read':
            return self.webui.snapshot()
        self.assertEqual(values['expected'], SETUP.digest(self.webui.snapshot()))
        return SETUP.apply_function(self.webui.snapshot(), values['candidate'], self.webui.api, self.webui.snapshot, values['model'])

    def test_default_plan_has_no_mutations_or_private_configuration_output(self):
        self.webui = WebUI(existing=True)
        result = SETUP.run(self.source)
        self.assertTrue(result['read_only'])
        self.assertTrue(result['changes_needed'])
        self.assertEqual(self.calls, ['read'])
        self.assertFalse(self.state.exists())
        self.assertNotIn('synthetic-valve-value', json.dumps(result))
        self.assertNotIn(OLD_CODE, json.dumps(result))
        self.assertEqual(result['model_id'], 'homecompute_videos')

    def test_apply_requires_matching_plan_and_private_backup_precedes_publication(self):
        plan = SETUP.run(self.source)
        with self.assertRaisesRegex(ValueError, 'matching --expected-plan'):
            SETUP.run(self.source, apply=True)
        self.assertFalse(self.state.exists())
        original_remote = self.remote
        def remote(action, **values):
            if action == 'apply':
                self.assertEqual(len(list(self.state.glob('*/function.before.private.json'))), 1)
            return original_remote(action, **values)
        with patch.object(SETUP, 'safe'), patch.object(SETUP, 'remote', side_effect=remote):
            result = SETUP.run(self.source, apply=True, expected_plan=plan['plan_sha256'])
        self.assertTrue(result['ok'])
        backup = Path(result['backup'])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        for path in backup.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(json.loads((backup / 'function.before.private.json').read_text())['function'])

    def test_source_drift_refuses_apply_and_repeat_apply_does_not_create_backups(self):
        plan = SETUP.run(self.source)
        self.function.write_text(CODE + '\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'matching --expected-plan'):
            SETUP.run(self.source, apply=True, expected_plan=plan['plan_sha256'])
        self.assertFalse(self.state.exists())
        self.function.write_text(CODE)
        desired = SETUP.candidate(self.webui.snapshot(), CODE)
        SETUP.apply_function(self.webui.snapshot(), desired, self.webui.api, self.webui.snapshot)
        plan = SETUP.run(self.source)
        result = SETUP.run(self.source, apply=True, expected_plan=plan['plan_sha256'])
        self.assertFalse(result['changed'])
        self.assertIsNone(result['backup'])
        self.assertFalse(self.state.exists())

    def test_wrong_host_non_root_and_symlink_source_refuse_without_admin_calls(self):
        for target, value in ((SETUP.socket, 'home-spark'),):
            with patch.object(target, 'gethostname', return_value=value), self.assertRaisesRegex(ValueError, 'home-core'):
                SETUP.run(self.source, apply=True)
        with patch.object(SETUP.os, 'geteuid', return_value=1000), self.assertRaisesRegex(ValueError, 'root'):
            SETUP.run(self.source)
        actual = self.function.with_suffix('.original')
        self.function.rename(actual)
        self.function.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            SETUP.run(self.source)
        self.assertEqual(self.calls, [])

    def test_symlink_backup_directory_is_not_followed(self):
        target = self.base / 'outside'
        target.mkdir()
        self.state.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            SETUP.private_backup({}, {}, {})
        self.assertEqual(list(target.iterdir()), [])


class RemoteTest(unittest.TestCase):
    def test_real_source_is_a_dedicated_pipe_and_admin_program_compiles(self):
        content = (Path(__file__).resolve().parents[1] / SETUP.SOURCE).read_text()
        desired = SETUP.candidate(WebUI().snapshot(), content)
        self.assertEqual(desired['id'], 'homecompute_videos')
        def subprocess_run(command, **options):
            self.assertEqual(command, ['docker', 'exec', '-i', SETUP.CONTAINER, 'python', '-'])
            compile(options['input'], '<container-admin-program>', 'exec')
            self.assertIn("listing=api('GET','/api/v1/functions/list')", options['input'])
            return SimpleNamespace(returncode=0, stdout='{"ok":true}')
        with patch.object(SETUP.subprocess, 'run', side_effect=subprocess_run):
            self.assertTrue(SETUP.remote('read')['ok'])

    def test_remote_errors_never_expose_captured_credentials_or_tracebacks(self):
        result = SimpleNamespace(returncode=1, stdout='synthetic-private-credential', stderr='sensitive traceback')
        with patch.object(SETUP.subprocess, 'run', return_value=result), self.assertRaises(RuntimeError) as raised:
            SETUP.remote('read')
        self.assertNotIn('synthetic-private-credential', str(raised.exception))
        self.assertNotIn('sensitive traceback', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
