"""Direct Microsoft setup grants, polling fences and interrupted-write recovery."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_outlook as outlook
spec = importlib.util.spec_from_file_location('outlook_setup', ROOT / 'scripts/setup-openclaw-outlook.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
CLIENT_ID = '00000000-1111-2222-3333-444444444444'


class OutlookSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.directory = self.root / 'outlook-owner'
        values = {'CONFIG': self.root, 'DIRECTORY': self.directory,
                  'CLIENT': self.directory / 'client.json', 'TOKEN': self.directory / 'token.json',
                  'PENDING': self.directory / 'device.json', 'MAIL': self.root / 'mail.json'}
        for name, value in values.items():
            patcher = patch.object(setup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = patch.object(setup.time, 'time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        setup.configure(CLIENT_ID)

    def device(self, **overrides):
        return {'device_code': 'PRIVATE-DEVICE-CODE', 'user_code': 'ABC123DEF',
                'verification_uri': setup.VERIFY, 'expires_in': 900, 'interval': 5, **overrides}

    def token(self, **overrides):
        return {'token_type': 'Bearer', 'access_token': 'PRIVATE-ACCESS-TOKEN',
                'refresh_token': 'PRIVATE-REFRESH-TOKEN', 'scope': 'Mail.Read openid offline_access',
                'expires_in': 3600, **overrides}

    def begin(self):
        with patch.object(setup, 'post', return_value=self.device()):
            return setup.begin()

    def due(self):
        pending = outlook.private_json(setup.PENDING)
        pending['next_poll_at'] = 1000
        outlook.atomic_json(setup.PENDING, pending)

    def assert_not_enabled(self):
        self.assertEqual(outlook.private_json(setup.MAIL)['accounts'], [])

    def test_configure_and_status_never_request_tokens_or_mail(self):
        with patch.object(setup, 'post') as post:
            self.assertEqual(setup.configure(CLIENT_ID)['status'], 'authorization-required')
            self.assertEqual(setup.status()['status'], 'authorization-required')
        self.assert_not_enabled()
        self.assertFalse(setup.TOKEN.exists())
        self.assertEqual(outlook.private_json(setup.CLIENT)['authority'], 'consumers')
        post.assert_not_called()

    def test_stage_switches_unconnected_legacy_backend_without_client_or_tokens(self):
        setup.CLIENT.unlink()
        legacy = {'schema_version': 1, 'bridge_token_file': str(self.root / 'mail-owner-route-token'),
                  'principal_scope': 'owner', 'data_domain': 'personal', 'accounts': []}
        outlook.atomic_json(setup.MAIL, legacy)
        with patch.object(setup, 'post') as post:
            result = setup.stage()
            self.assertEqual(setup.status()['status'], 'application-registration-required')
            setup.stage()
        self.assertEqual(result['status'], 'application-registration-required')
        self.assertFalse(result['n8n_used'])
        self.assertEqual(outlook.private_json(setup.MAIL), setup.mail_configuration(False))
        self.assertEqual(outlook.private_json(setup.DIRECTORY / 'previous-mail.json'), legacy)
        self.assertFalse(setup.CLIENT.exists())
        self.assertFalse(setup.TOKEN.exists())
        self.assertFalse(setup.PENDING.exists())
        post.assert_not_called()

    def test_stage_refuses_any_connected_prior_backend(self):
        for configuration in [setup.mail_configuration(True),
                {'schema_version': 1, 'bridge_token_file': str(self.root / 'mail-owner-route-token'),
                 'principal_scope': 'owner', 'data_domain': 'personal', 'accounts': ['outlook_mads']}]:
            outlook.atomic_json(setup.MAIL, configuration)
            with self.assertRaises(ValueError): setup.stage()
            self.assertEqual(outlook.private_json(setup.MAIL), configuration)
        self.assertFalse((setup.DIRECTORY / 'previous-mail.json').exists())

    def test_client_replacement_and_other_connected_mailboxes_are_refused(self):
        before = setup.CLIENT.read_bytes()
        with self.assertRaises(ValueError):
            setup.configure('11111111-1111-2222-3333-444444444444')
        self.assertEqual(setup.CLIENT.read_bytes(), before)
        outlook.atomic_json(setup.MAIL, {**setup.mail_configuration(True), 'provider': 'other-provider'})
        with self.assertRaises(ValueError): setup.configure(CLIENT_ID)
        self.assertEqual(setup.CLIENT.read_bytes(), before)

    def test_begin_reuses_pending_and_outputs_only_user_entered_code(self):
        with patch.object(setup, 'post', return_value=self.device()) as post:
            first = setup.begin()
            second = setup.begin()
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[1], {'client_id': CLIENT_ID, 'scope': outlook.SCOPES})
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.assertEqual(setup.PENDING.stat().st_mode & 0o777, 0o600)
        self.assert_not_enabled()

    def test_device_response_refuses_untrusted_urls_codes_and_timestamps(self):
        for value in [{'verification_uri': 'https://evil.invalid'}, {'device_code': 'code\nsecret'},
                      {'user_code': '<script>'}, {'expires_in': True}, {'interval': 0},
                      {'expires_in': 1801}, {'error': 'invalid_client'}]:
            with self.subTest(value=list(value)), patch.object(setup, 'post', return_value=self.device(**value)):
                with self.assertRaises(ValueError): setup.begin()
        self.assertFalse(setup.PENDING.exists())

    def test_poll_observes_interval_before_network_and_slow_down(self):
        self.begin()
        with patch.object(setup, 'post') as post:
            result = setup.poll()
        self.assertEqual(result['status'], 'authorization-pending')
        post.assert_not_called()
        self.due()
        with patch.object(setup, 'post', return_value={'error': 'slow_down'}):
            result = setup.poll()
        self.assertEqual(result['retry_after'], 10)
        pending = outlook.private_json(setup.PENDING)
        self.assertEqual(pending['next_poll_at'], 1010)
        self.assert_not_enabled()

    def test_network_failures_still_reserve_next_poll_time(self):
        self.begin(); self.due()
        with patch.object(setup, 'post', side_effect=OSError('private provider detail')):
            with self.assertRaises(OSError): setup.poll()
        with patch.object(setup, 'post') as post:
            self.assertEqual(setup.poll()['status'], 'authorization-pending')
        post.assert_not_called()
        self.assert_not_enabled()

    def test_success_persists_only_read_grant_before_enabling_account(self):
        self.begin(); self.due()
        with patch.object(setup, 'post', return_value=self.token()) as post:
            result = setup.poll()
        self.assertEqual(result['status'], 'connected')
        self.assertEqual(post.call_args.args[1]['grant_type'], 'urn:ietf:params:oauth:grant-type:device_code')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertFalse(setup.PENDING.exists())
        self.assertEqual(outlook.private_json(setup.MAIL), setup.mail_configuration(True))
        self.assertEqual(setup.TOKEN.stat().st_mode & 0o777, 0o600)
        self.assertEqual(setup.status()['status'], 'connected')

    def test_extra_permissions_never_persist_or_enable(self):
        self.begin(); self.due()
        with patch.object(setup, 'post', return_value=self.token(scope='Mail.Read Mail.Send')):
            with self.assertRaises(ValueError): setup.poll()
        self.assertFalse(setup.TOKEN.exists())
        self.assert_not_enabled()

    def test_mail_write_failure_recovers_from_retained_tokens_without_redeeming_again(self):
        self.begin(); self.due()
        original = setup.atomic_json
        def fail_mail(path, value):
            if path == setup.MAIL:
                raise OSError('disk unavailable')
            return original(path, value)
        with patch.object(setup, 'post', return_value=self.token()), \
             patch.object(setup, 'atomic_json', side_effect=fail_mail):
            with self.assertRaises(OSError): setup.poll()
        self.assertTrue(setup.TOKEN.exists())
        self.assertTrue(setup.PENDING.exists())
        self.assert_not_enabled()
        with patch.object(setup, 'post') as post:
            self.assertEqual(setup.poll()['status'], 'connected')
            self.assertEqual(setup.begin()['status'], 'connected')
        post.assert_not_called()
        self.assertFalse(setup.PENDING.exists())

    def test_token_write_failure_never_enables_account(self):
        self.begin(); self.due()
        original = setup.atomic_json
        def fail_tokens(path, value):
            if path == setup.TOKEN:
                raise OSError('disk unavailable')
            return original(path, value)
        with patch.object(setup, 'post', return_value=self.token()), \
             patch.object(setup, 'atomic_json', side_effect=fail_tokens):
            with self.assertRaises(OSError): setup.poll()
        self.assertFalse(setup.TOKEN.exists())
        self.assert_not_enabled()
        with patch.object(setup, 'post', return_value=self.device()) as post:
            setup.begin()
        self.assertEqual(post.call_count, 1)

    def test_terminal_error_expires_pending_so_begin_can_start_fresh(self):
        self.begin(); self.due()
        with patch.object(setup, 'post', return_value={'error': 'authorization_declined'}):
            with self.assertRaises(ValueError): setup.poll()
        with patch.object(setup, 'post', return_value=self.device()) as post:
            setup.begin()
        self.assertEqual(post.call_count, 1)
        self.assert_not_enabled()

    def test_tampered_pending_fails_closed_before_post(self):
        self.begin()
        saved = outlook.private_json(setup.PENDING)
        for overrides in [{'scope': 'Mail.Read Mail.Send'}, {'client_id': 'other'}, {'interval': 0},
                          {'verification_uri': 'https://evil.invalid'}, {'expires_at': float('inf')},
                          {'device_code': 'token\nheader'}, {'extra': 'property'}]:
            if overrides.get('expires_at') == float('inf'):
                setup.PENDING.write_text(json.dumps({**saved, **overrides}))
            else:
                outlook.atomic_json(setup.PENDING, {**saved, **overrides})
            with self.subTest(overrides=list(overrides)), patch.object(setup, 'post') as post:
                with self.assertRaises(ValueError): setup.poll()
                post.assert_not_called()
        self.assert_not_enabled()

    def test_post_rejects_custom_endpoints_and_non_error_http_400(self):
        with self.assertRaises(ValueError): setup.post('https://evil.invalid', {'device_code': 'private'})
        response = Mock(status=400)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(self.token()).encode()
        with patch.object(setup.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                setup.post('https://login.microsoftonline.com/consumers/oauth2/v2.0/token', {})


if __name__ == '__main__': unittest.main()
