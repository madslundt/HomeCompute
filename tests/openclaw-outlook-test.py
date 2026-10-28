"""Direct Outlook transport, private token rotation and read-only boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_outlook as outlook


class OutlookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.directory.chmod(0o700)
        self.config = self.directory / 'client.json'
        self.tokens = self.directory / 'token.json'
        outlook.atomic_json(self.tokens, self.token())
        outlook.atomic_json(self.config, {'schema_version': 1,
            'client_id': '00000000-1111-2222-3333-444444444444',
            'authority': 'consumers', 'token_file': str(self.tokens)})
        self.client = outlook.Outlook(self.config)
        self.args = {'account': 'outlook_mads', 'query': 'invoice'}

    def token(self, expired=False):
        return {'access_token': 'original-access', 'refresh_token': 'original-refresh',
                'scope': 'Mail.Read openid offline_access',
                'expires_at': time.time() + (-30 if expired else 3600)}

    def response(self, **overrides):
        return {'token_type': 'Bearer', 'access_token': 'rotated-access',
                'refresh_token': 'rotated-refresh', 'scope': 'Mail.Read openid offline_access',
                'expires_in': 3600, **overrides}

    def row(self, **overrides):
        return {'id': 'message', 'conversationId': 'conversation', 'subject': 'Subject',
                'from': {'emailAddress': {'address': 'sender@example.invalid'}},
                'receivedDateTime': '2026-10-28T12:00:00Z', 'bodyPreview': 'Preview', **overrides}

    def test_read_only_scope_requires_mail_read_and_rejects_all_broader_grants(self):
        accepted = outlook.validate_token_response(self.response(scope=outlook.SCOPES))
        self.assertEqual(accepted['scope'], outlook.SCOPES)
        for scope in ['Mail.ReadWrite', 'Mail.Read Mail.Send', 'Mail.Read User.Read',
                      'Mail.Read Files.Read', 'openid offline_access', '',
                      'Mail.Read https://evil.invalid/Mail.Read', 'Mail.Read.Shared']:
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'permission'):
                outlook.validate_token_response(self.response(scope=scope))

    def test_token_response_cannot_inject_headers_or_malformed_lifetimes(self):
        for overrides in [{'access_token': 'token\nAuthorization: leak'}, {'refresh_token': ''},
                          {'expires_in': True}, {'expires_in': 0}, {'expires_in': 86401},
                          {'token_type': 'mac'}, {'token_type': None}, {'scope': None}]:
            with self.subTest(overrides=list(overrides)), self.assertRaises(ValueError):
                outlook.validate_token_response(self.response(**overrides))
        response = self.response()
        del response['refresh_token']
        self.assertEqual(outlook.validate_token_response(response, 'retained')['refresh_token'], 'retained')

    def test_arbitrary_operations_accounts_urls_and_control_characters_do_not_reach_network(self):
        cases = [('send', {}), ('delete', {}), ([], {}), ('search', {**self.args, 'url': 'https://evil.invalid'}),
                 ('search', {**self.args, 'account': 'outlook_kristina'}),
                 ('search', {**self.args, 'query': 'x\nAuthorization: leak'}),
                 ('search', {**self.args, 'query': 'x' * 201}),
                 ('search', {**self.args, 'query': ' '}),
                 ('thread', {'account': 'outlook_mads', 'thread_id': '../arbitrary'})]
        with patch.object(outlook, '_fetch') as fetch:
            for operation, arguments in cases:
                with self.subTest(operation=operation, arguments=arguments), self.assertRaises(ValueError):
                    self.client.request(operation, arguments)
            fetch.assert_not_called()

    def test_search_is_get_for_current_mailbox_with_escaped_query_and_bounded_projection(self):
        row = self.row(subject='a' * 300, oauth='credential', attachments=['secret'])
        with patch.object(outlook, '_fetch', return_value={'value': [row, row], '@odata.nextLink': 'https://evil.invalid'}) as fetch:
            result = self.client.request('search', {**self.args, 'query': 'invoice & $top=999 "quote"'})
        request = fetch.call_args.args[0]
        url = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(url.query)
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'graph.microsoft.com', '/v1.0/me/messages'))
        self.assertEqual(query['$top'], ['10'])
        self.assertEqual(query['$search'], ['"invoice & $top=999 \\"quote\\""'])
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header('Prefer'), 'outlook.body-content-type="text"')
        self.assertEqual(len(result['threads']), 1)
        self.assertEqual(len(result['threads'][0]['subject']), 200)
        self.assertTrue(result['truncated'])
        self.assertNotIn('credential', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))

    def test_thread_returns_only_its_conversation_and_plaintext(self):
        arguments = {'account': 'outlook_mads', 'thread_id': 'conversation'}
        row = self.row(body={'contentType': 'text', 'content': 'a' * 1800}, attachments=['PRIVATE'])
        with patch.object(outlook, '_fetch', return_value={'value': [row]}):
            result = self.client.request('thread', arguments)
        self.assertEqual(len(result['messages'][0]['body']), 1400)
        self.assertTrue(result['messages'][0]['body_truncated'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        row['body'] = {'contentType': 'html', 'content': '<script>PRIVATE</script>'}
        with patch.object(outlook, '_fetch', return_value={'value': [row]}):
            result = self.client.request('thread', arguments)
        self.assertEqual(result['messages'][0]['body'], '')
        row['conversationId'] = 'other'
        with patch.object(outlook, '_fetch', return_value={'value': [row]}), self.assertRaises(ValueError):
            self.client.request('thread', arguments)

    def test_expired_tokens_refresh_and_rotated_tokens_persist_atomically(self):
        outlook.atomic_json(self.tokens, self.token(expired=True))
        with patch.object(outlook, '_fetch', side_effect=[self.response(), {'value': []}]) as fetch:
            self.client.request('search', self.args)
        token_request, graph_request = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(token_request.full_url, 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token')
        payload = urllib.parse.parse_qs(token_request.data.decode())
        self.assertEqual(payload['grant_type'], ['refresh_token'])
        self.assertEqual(payload['scope'], [outlook.SCOPES])
        self.assertNotIn('client_secret', payload)
        self.assertEqual(graph_request.get_header('Authorization'), 'Bearer rotated-access')
        saved = outlook.private_json(self.tokens)
        self.assertEqual(saved['refresh_token'], 'rotated-refresh')
        self.assertEqual(saved['access_token'], 'rotated-access')
        self.assertEqual(self.tokens.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.directory.glob('*.new-*')))

    def test_all_returned_conversation_evidence_is_checked_before_truncating(self):
        rows = [self.row(id='message' + str(index)) for index in range(11)]
        with patch.object(outlook, '_fetch', return_value={'value': rows}):
            result = self.client.request('thread', {'account': 'outlook_mads', 'thread_id': 'conversation'})
        self.assertEqual(len(result['messages']), 10)
        self.assertTrue(result['truncated'])
        rows[-1]['conversationId'] = 'other'
        with patch.object(outlook, '_fetch', return_value={'value': rows}), self.assertRaises(ValueError):
            self.client.request('thread', {'account': 'outlook_mads', 'thread_id': 'conversation'})

    def test_scope_expansion_during_refresh_is_rejected_without_overwriting_token(self):
        original = self.token(expired=True)
        outlook.atomic_json(self.tokens, original)
        with patch.object(outlook, '_fetch', return_value=self.response(scope='Mail.Read Mail.Send')) as fetch:
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                self.client.request('search', self.args)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(outlook.private_json(self.tokens), original)

    def test_401_refreshes_once_and_provider_errors_never_leak_tokens(self):
        failure = urllib.error.HTTPError(outlook.GRAPH, 401, 'original-access', {}, None)
        self.addCleanup(failure.close)
        with patch.object(outlook, '_fetch', side_effect=[failure, self.response(), {'value': []}]) as fetch:
            self.assertEqual(self.client.request('search', self.args)['threads'], [])
        self.assertEqual(fetch.call_count, 3)
        with patch.object(outlook, '_fetch', side_effect=[failure, self.response(), failure]) as fetch:
            with self.assertRaisesRegex(ValueError, '^Outlook unavailable; no message was sent or marked read$'):
                self.client.request('search', self.args)
        self.assertEqual(fetch.call_count, 3)

    def test_existing_state_and_locks_refuse_symlinks_hardlinks_and_wrong_modes(self):
        self.tokens.chmod(0o640)
        with self.assertRaises(ValueError): outlook.Outlook(self.config)
        self.tokens.chmod(0o600)
        link = self.directory / 'token-link.json'
        link.symlink_to(self.tokens)
        with self.assertRaises(OSError): outlook.private_json(link)
        link.unlink()
        os.link(self.tokens, link)
        with self.assertRaises(ValueError): outlook.private_json(self.tokens)
        link.unlink()
        lock = self.tokens.with_name(self.tokens.name + '.lock')
        lock.symlink_to(self.tokens)
        with patch.object(outlook, '_fetch') as fetch, self.assertRaisesRegex(ValueError, 'unavailable'):
            self.client.request('search', self.args)
        fetch.assert_not_called()

    def test_config_denies_custom_hosts_and_shared_parent_directory(self):
        config = outlook.private_json(self.config)
        for value in ['common', 'https://evil.invalid', '../tenant']:
            outlook.atomic_json(self.config, {**config, 'authority': value})
            with self.assertRaises(ValueError): outlook.Outlook(self.config)
        self.directory.chmod(0o750)
        with self.assertRaises(ValueError): outlook.private_json(self.tokens)

    def test_http_reader_disables_proxy_redirects_and_caps_provider_response(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'x' * (outlook.MAX_RESPONSE + 1)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(outlook.urllib.request, 'build_opener', return_value=opener) as build:
            with self.assertRaisesRegex(ValueError, 'response refused'):
                outlook._fetch(outlook.urllib.request.Request(outlook.GRAPH))
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], outlook.NoRedirect)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))
        response.read.assert_called_once_with(outlook.MAX_RESPONSE + 1)


if __name__ == '__main__': unittest.main()
