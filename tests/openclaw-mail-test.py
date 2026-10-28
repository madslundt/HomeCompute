"""Mail facade, principal isolation and actual n8n Code-node fixture checks."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_mail as mail
from openclaw_mail_workflow import build, sdk, SOURCES

spec = importlib.util.spec_from_file_location('mail_setup', ROOT / 'scripts/setup-openclaw-mail.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def javascript(source, row, context=None):
    harness = """const fs=require('fs');const r=JSON.parse(fs.readFileSync(0,'utf8'));
try{const value=new Function('$json','$',r.source)(r.row,()=>({first:()=>({json:r.context})}));process.stdout.write(JSON.stringify({value}));}
catch(e){process.stdout.write(JSON.stringify({error:'refused'}));}"""
    result = subprocess.run(['node', '-e', harness], input=json.dumps({'source': source, 'row': row, 'context': context}),
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def gate(operation, args, principal='owner', configured=True):
    workflow = build(principal=principal, credential={'id': 'synthetic', 'name': 'Synthetic'}, configured=configured)
    source = workflow['nodes'][1]['parameters']['jsCode']
    return javascript(source, {'body': {'operation': operation, 'arguments': args}})


def projected(raw, context, status=200):
    source = (ROOT / 'automations/mail/project-mail-response.js').read_text()
    return javascript(source, {'statusCode': status, 'body': raw}, context)['value'][0]['json']


class MailTests(unittest.TestCase):
    def facade(self, principal='owner', connected=False):
        alias, _, domain = mail.SOURCES[principal]
        config = {'schema_version': 1, 'bridge_token_file': str(mail.CONFIG_ROOT / ('mail-' + principal + '-route-token')),
                  'principal_scope': principal, 'data_domain': domain, 'accounts': [alias] if connected else []}
        with patch.object(mail, 'private', side_effect=[json.dumps(config).encode()] + ([b'a' * 64] if connected else [])) as read:
            result = mail.Mail(Path('/synthetic/config'), Path('/unused'), principal_scope=principal)
        return result, read

    def test_status_only_needs_no_token_and_reads_no_mail(self):
        facade, read = self.facade()
        facade._bridge = Mock(side_effect=AssertionError('No mailbox reads'))
        result = facade.request(mail.PREFIX + 'list_accounts', {})
        self.assertEqual(read.call_count, 1)
        self.assertFalse(result['mailbox_content_read'])
        self.assertEqual(result['accounts'], [{'alias': 'outlook_mads', 'provider': 'outlook', 'connected': False, 'access': 'not-configured'}])
        with self.assertRaisesRegex(ValueError, 'not connected'):
            facade.request(mail.PREFIX + 'search', {'account': 'outlook_mads', 'query': 'invoice'})
        facade._bridge.assert_not_called()

    def test_tools_and_runtime_fence_each_mailbox_to_its_authenticated_principal(self):
        for principal, (alias, _, _) in mail.SOURCES.items():
            facade, _ = self.facade(principal, True)
            for definition in facade.tools:
                if 'account' in definition['inputSchema']['properties']:
                    self.assertEqual(definition['inputSchema']['properties']['account']['enum'], [alias])
            for other in set(mail.ACCOUNTS) - {alias}:
                with self.assertRaisesRegex(ValueError, 'principal'):
                    facade.validate(mail.PREFIX + 'search', {'account': other, 'query': 'anything'})

    def test_owner_refuses_partner_config_even_with_matching_files(self):
        config = {'schema_version': 1, 'bridge_token_file': str(mail.TOKEN_PATH),
                  'principal_scope': 'partner', 'data_domain': 'personal', 'accounts': []}
        with patch.object(mail, 'private', return_value=json.dumps(config).encode()), self.assertRaises(ValueError):
            mail.Mail(Path('/synthetic/config'), Path('/unused'))

    def test_direct_owner_routes_to_graph_connector_without_n8n(self):
        config = {'schema_version': 2, 'provider': 'outlook-direct',
                  'outlook_config_file': str(mail.CONFIG_ROOT / 'outlook-owner/client.json'),
                  'principal_scope': 'owner', 'data_domain': 'personal', 'accounts': ['outlook_mads']}
        with patch.object(mail, 'private', return_value=json.dumps(config).encode()), \
             patch('openclaw_outlook.Outlook') as connector, patch.object(mail.urllib.request, 'build_opener') as network:
            connector.return_value.request.return_value = {'ok': True, 'account': 'outlook_mads',
                                                          'threads': [], 'truncated': False}
            facade = mail.Mail(Path('/synthetic/config'), Path('/unused'))
            result = facade.request(mail.PREFIX + 'search', {'account': 'outlook_mads', 'query': 'invoice'})
            self.assertEqual(result['threads'], [])
            connector.return_value.request.assert_called_once_with('search', {'account': 'outlook_mads', 'query': 'invoice'})
            network.assert_not_called()
            status = facade.request(mail.PREFIX + 'list_accounts', {})
            self.assertEqual(status['backend'], 'outlook-direct')
            self.assertNotIn('n8n', status['setup_url'])

    def test_direct_setup_status_needs_no_tokens_and_refuses_foreign_paths(self):
        config = {'schema_version': 2, 'provider': 'outlook-direct',
                  'outlook_config_file': str(mail.CONFIG_ROOT / 'outlook-owner/client.json'),
                  'principal_scope': 'owner', 'data_domain': 'personal', 'accounts': []}
        with patch.object(mail, 'private', return_value=json.dumps(config).encode()), \
             patch('openclaw_outlook.Outlook') as connector:
            facade = mail.Mail(Path('/synthetic/config'), Path('/unused'))
            self.assertFalse(facade.request(mail.PREFIX + 'list_accounts', {})['accounts'][0]['connected'])
            connector.assert_not_called()
        for key, value in [('outlook_config_file', '/tmp/other.json'), ('principal_scope', 'partner'),
                           ('provider', 'other'), ('schema_version', True)]:
            with patch.object(mail, 'private', return_value=json.dumps({**config, key: value}).encode()), \
                 self.assertRaises(ValueError):
                mail.Mail(Path('/synthetic/config'), Path('/unused'))

    def test_unknown_properties_sends_and_invalid_ids_are_refused(self):
        for operation, args in [('send', {}), ('search', {'account': 'outlook_mads', 'query': 'x', 'url': 'https://evil.invalid'}),
                                ('search', {'account': 'outlook_mads', 'query': 'x\nAuthorization: secret'}),
                                ('thread', {'account': 'gmail_work', 'thread_id': '../../any'})]:
            with self.assertRaises(ValueError): mail.validate(mail.PREFIX + operation, args)
        for definition in mail.TOOLS:
            self.assertTrue(definition['annotations']['readOnlyHint'])

    def test_facade_projects_only_bounded_result_fields(self):
        facade, _ = self.facade(connected=True)
        facade._bridge = Mock(return_value={'ok': True, 'account': 'outlook_mads', 'truncated': False,
            'threads': [{'thread_id': 'synthetic', 'subject': 'a' * 500, 'oauth': 'secret', 'headers': {'Authorization': 'secret'}}]})
        result = facade.request(mail.PREFIX + 'search', {'account': 'outlook_mads', 'query': 'x'})
        self.assertEqual(len(result['threads'][0]['subject']), 200)
        self.assertNotIn('secret', json.dumps(result))
        self.assertTrue(result['untrusted_content'])

    def test_bridge_wrong_account_and_oversized_results_fail_closed(self):
        facade, _ = self.facade(connected=True)
        for raw in [{'ok': True, 'account': 'outlook_kristina', 'truncated': False, 'threads': []},
                    {'ok': True, 'account': 'outlook_mads', 'truncated': False, 'threads': [{}] * 11}]:
            facade._bridge = Mock(return_value=raw)
            with self.assertRaises(ValueError):
                facade.request(mail.PREFIX + 'search', {'account': 'outlook_mads', 'query': 'x'})

    def test_workflow_is_inactive_without_oauth_and_separate_for_each_principal(self):
        paths = set(); routes = set()
        for principal in SOURCES:
            workflow = build(principal=principal)
            self.assertFalse(workflow['active'])
            self.assertEqual(workflow['settings']['saveDataSuccessExecution'], 'none')
            self.assertEqual(workflow['settings']['saveDataErrorExecution'], 'none')
            paths.add(workflow['nodes'][0]['parameters']['path'])
            routes.add(workflow['nodes'][0]['credentials']['httpHeaderAuth']['id'])
            http = next(n for n in workflow['nodes'] if n['type'].endswith('.httpRequest'))
            self.assertNotIn('credentials', http)
            self.assertEqual(http['parameters']['method'], 'GET')
            self.assertFalse(http['parameters']['sendBody'])
            self.assertFalse(http['parameters']['options']['redirect']['redirect']['followRedirects'])
            self.assertIn('const configured = false;', sdk(workflow))
        self.assertEqual(len(paths), 3); self.assertEqual(len(routes), 3)

    def test_workflow_gate_refuses_foreign_accounts_mutations_and_unconfigured_source(self):
        for operation, args in [('send', {'account': 'outlook_mads', 'query': 'x'}),
                                ('search', {'account': 'outlook_kristina', 'query': 'x'}),
                                ('thread', {'account': 'outlook_mads', 'thread_id': '../../other'}),
                                ('search', {'account': 'outlook_mads', 'query': 'x', 'url': 'https://evil.invalid'})]:
            self.assertIn('error', gate(operation, args))
        self.assertIn('error', gate('search', {'account': 'outlook_mads', 'query': 'x'}, configured=False))

    def test_outlook_fixed_me_target_and_search_encoding(self):
        row = gate('search', {'account': 'outlook_mads', 'query': 'invoice & $top=999'})['value'][0]['json']
        self.assertTrue(row['request']['url'].startswith('https://graph.microsoft.com/v1.0/me/messages?$top=10&'))
        self.assertNotIn('& $top=999', row['request']['url'])
        self.assertEqual(row['request']['headers']['Prefer'], 'outlook.body-content-type="text"')

    def test_projection_refuses_other_conversation_and_does_not_return_attachments(self):
        context = {'account': 'outlook_mads', 'provider': 'outlook', 'operation': 'thread', 'thread_id': 'thread'}
        raw = {'value': [{'id': 'message', 'conversationId': 'other', 'body': {'contentType': 'text', 'content': 'Private'}}]}
        self.assertFalse(projected(raw, context)['ok'])
        raw['value'][0]['conversationId'] = 'thread'
        raw['value'][0]['attachments'] = ['credential']
        result = projected(raw, context)
        self.assertEqual(result['messages'][0]['body'], 'Private')
        self.assertNotIn('credential', json.dumps(result))

    def test_gmail_plaintext_only_and_explicit_truncation(self):
        context = {'account': 'gmail_work', 'provider': 'gmail', 'operation': 'thread', 'thread_id': 'abc'}
        raw = {'id': 'abc', 'messages': [{'id': 'abc', 'payload': {'mimeType': 'multipart/mixed', 'parts': [
            {'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(('Text' * 1000).encode()).decode()}},
            {'mimeType': 'application/pdf', 'body': {'data': 'SECRET-ATTACHMENT'}}]}, 'snippet': 'Summary'}]}
        result = projected(raw, context)
        self.assertTrue(result['messages'][0]['body_truncated'])
        self.assertEqual(len(result['messages'][0]['body']), 1400)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertFalse(projected({'oauth': 'secret'}, context, 401)['ok'])


class SetupTests(unittest.TestCase):
    def test_status_staging_creates_no_credentials_and_preserves_three_scopes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); root.chmod(0o700)
            with patch.object(setup, 'CONFIG', root), patch.object(setup, 'SERVICE_UID', os.geteuid()), \
                 patch.object(setup, 'ADMIN_UID', os.geteuid()), patch.object(setup.os, 'fchown'), \
                 patch.object(setup, 'run') as command:
                for principal in SOURCES:
                    receipt = setup.prepare(ROOT / 'automations/mail', principal=principal)
                    self.assertEqual(receipt['status'], 'oauth-sign-in-required')
                    config = json.loads((root / ('mail.json' if principal == 'owner' else 'mail-' + principal + '.json')).read_text())
                    self.assertEqual(config['accounts'], [])
                    self.assertEqual(config['principal_scope'], principal)
                    self.assertFalse(receipt['oauth_exported'])
                    self.assertEqual(receipt['mailbox_reads'], 0)
                command.assert_not_called()
                self.assertFalse(list(root.glob('*route-token')))

    def test_credential_admission_needs_isolated_runtime_and_local_work_permission(self):
        for principal, options in [('owner', {}), ('partner', {}), ('work', {'isolated_runtime': True})]:
            with self.assertRaisesRegex(ValueError, 'admission'):
                setup.prepare(ROOT / 'automations/mail', principal=principal, credential_id='synthetic', **options)

    def test_retained_route_verifies_only_its_own_secret_without_exporting_provider_oauth(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('CREATE TABLE credentials_entity(id,name,type,data); CREATE TABLE shared_credentials(credentialsId,projectId,role);')
        route = setup.route_credential('owner')
        db.execute('INSERT INTO credentials_entity VALUES(?,?,?,?)', (route['id'], route['name'], 'httpHeaderAuth', 'ENCRYPTED'))
        db.execute('INSERT INTO shared_credentials VALUES(?,?,?)', (route['id'], 'project', 'credential:owner'))
        token = 'b' * 64
        payload = [{'id': route['id'], 'data': {'name': 'Authorization', 'value': 'Bearer ' + token}}]
        commands = []
        def command(argv, content=None):
            commands.append(argv)
            return json.dumps(payload) if any('process.stdout.write' in str(x) for x in argv) else ''
        with patch.object(setup, 'run', side_effect=command):
            setup.import_route(db, 'owner', token, 'project')
        exports = [argv for argv in commands if 'export:credentials' in argv]
        self.assertEqual(len(exports), 1)
        self.assertIn('--id=' + route['id'], exports[0])
        self.assertFalse(any('import:credentials' in argv for argv in commands))
        self.assertIn('unlinkSync', ' '.join(commands[-1]))
        payload[0]['data']['value'] = 'Bearer changed'
        with patch.object(setup, 'run', side_effect=command), self.assertRaisesRegex(ValueError, 'no rotation'):
            setup.import_route(db, 'owner', token, 'project')


if __name__ == '__main__': unittest.main()
