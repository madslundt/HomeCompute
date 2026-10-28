#!/usr/bin/env python3
"""Owner-session authentication with synthetic cookies, no real account access."""
import email.message
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_access_auth import MattermostOwnerAuth, cookie_token, ME_URL
from openclaw_access_page import review, sign_in, CSP


class Response(io.BytesIO):
    status = 200


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.directory.chmod(0o700)
        self.owner = 'a' * 26
        self.token = 'synthetic-private-cookie-' + 'b' * 30
        self.config = self.directory / 'owner.json'
        self.config.write_text(json.dumps({'schema_version': 1, 'owner_id': self.owner}))
        self.config.chmod(0o600)
        self.opener = Mock()
        self.auth = MattermostOwnerAuth(self.config, opener=self.opener)

    def tearDown(self):
        self.temp.cleanup()

    def headers(self, cookie=None):
        headers = email.message.Message()
        headers['Cookie'] = cookie or 'MMUSERID=ignored; MMAUTHTOKEN=' + self.token + '; MMCSRF=ignored'
        return headers

    def test_exact_owner_session_is_forwarded_only_to_fixed_local_endpoint(self):
        self.opener.open.return_value = Response(json.dumps({'id': self.owner, 'is_bot': False, 'delete_at': 0}).encode())
        self.assertTrue(self.auth(self.headers()))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ME_URL)
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.token)
        self.assertIsNone(request.get_header('Cookie'))

    def test_other_owner_bot_deleted_or_incomplete_user_never_authorizes(self):
        for user in [{'id': 'z' * 26, 'is_bot': False, 'delete_at': 0},
                     {'id': self.owner, 'is_bot': True, 'delete_at': 0},
                     {'id': self.owner, 'is_bot': False, 'delete_at': 10}, {'id': self.owner}, []]:
            self.opener.open.return_value = Response(json.dumps(user).encode())
            self.assertFalse(self.auth(self.headers()))

    def test_duplicate_and_malformed_cookie_values_are_refused_before_network_access(self):
        for cookie in ['MMUSERID=' + self.owner, 'MMAUTHTOKEN=bad',
                       'MMAUTHTOKEN=' + self.token + '; MMAUTHTOKEN=' + self.token,
                       'MMAUTHTOKEN="' + self.token + '"', 'MMAUTHTOKEN=' + self.token + '\n']:
            self.assertIsNone(cookie_token(self.headers(cookie)))
        headers = self.headers()
        headers['Cookie'] = 'MMAUTHTOKEN=' + self.token
        self.assertIsNone(cookie_token(headers))
        self.assertFalse(self.auth(headers))
        self.opener.open.assert_not_called()

    def test_network_and_parse_failures_are_private_and_fail_closed(self):
        self.opener.open.side_effect = RuntimeError(self.token)
        self.assertFalse(self.auth(self.headers()))
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response(self.token.encode())
        self.assertFalse(self.auth(self.headers()))

    def test_no_auth_cache_survives_user_revocation(self):
        self.opener.open.side_effect = [Response(json.dumps({'id': self.owner, 'is_bot': False, 'delete_at': 0}).encode()), RuntimeError('revoked')]
        self.assertTrue(self.auth(self.headers()))
        self.assertFalse(self.auth(self.headers()))
        self.assertEqual(self.opener.open.call_count, 2)

    def test_mattermost_omitted_false_bot_flag_is_supported(self):
        self.opener.open.return_value = Response(json.dumps({'id': self.owner, 'delete_at': 0}).encode())
        self.assertTrue(self.auth(self.headers()))

    def test_owner_policy_cannot_contain_passwords_or_endpoint_overrides(self):
        self.config.write_text(json.dumps({'schema_version': 1, 'owner_id': self.owner, 'endpoint': 'https://evil.invalid'}))
        with self.assertRaises(ValueError):
            MattermostOwnerAuth(self.config)

    def test_review_contains_task_duration_and_no_script_or_credentials(self):
        page = review({'request_id': '00000000-0000-4000-8000-000000000001', 'request_digest': 'a' * 64,
                       'account': 'hotels.com', 'actor': '<script>attack</script>', 'scope': 'search',
                       'requested_minutes': 30, 'status': 'pending', 'expires_at': None}, {'approve': 'synthetic-challenge'})
        self.assertIn('Allow access for', page)
        self.assertIn('This site adapter is not connected or qualified', page)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn(self.token, page + sign_in('https://example.invalid/login'))
        self.assertIn("default-src 'none'", CSP)


if __name__ == '__main__':
    unittest.main()
