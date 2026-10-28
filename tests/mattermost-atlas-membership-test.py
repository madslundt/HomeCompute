"""Automatic owner-room reconciliation fixtures; no messages or live mutations."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('atlas_sync', ROOT / 'scripts/sync-mattermost-atlas.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
BOOT = importlib.util.spec_from_file_location('bootstrap_fixture', ROOT / 'scripts/bootstrap-mattermost.py')
b = importlib.util.module_from_spec(BOOT)
BOOT.loader.exec_module(b)
OWNER, TEAM, BOT, TOKEN_ID = (c * 26 for c in 'otbk')
PAT, SESSION = 'synthetic-atlas-token-' + 'x' * 26, 'synthetic-operator-session-' + 's' * 26


class Server:
    def __init__(self):
        self.channels = [{'id': 'a' * 26, 'type': 'O', 'team_id': TEAM, 'delete_at': 0}]
        self.joined = {'a' * 26}
        self.posts = []; self.calls = []; self.expired = False; self.bad_owner = False; self.bad_bot = False
        self.uncertain = False

    def request(self, token, method, path, body=None, **kwargs):
        self.calls.append((method, path))
        if path == '/users/login':
            self.expired = False
            return {'id': OWNER, 'username': 'mads', 'roles': 'system_user system_admin'}, {'Token': SESSION + '-renewed'}
        if path == '/users/me':
            if token == PAT:
                return {'id': BOT, 'username': 'atlas', 'is_bot': True,
                        'roles': 'system_admin' if self.bad_bot else 'system_user'}, {}
            if self.expired: raise b.BootstrapError('API GET failed with HTTP 401; inspect privately')
            return {'id': 'z' * 26 if self.bad_owner else OWNER, 'username': 'mads', 'roles': 'system_user system_admin'}, {}
        if path == '/teams/name/homecompute': return {'id': TEAM}, {}
        if path == '/bots/' + BOT: return {'user_id': BOT, 'owner_id': OWNER}, {}
        if path == f'/users/{OWNER}/teams/{TEAM}/channels': return self.channels, {}
        if path == f'/users/{BOT}/teams/{TEAM}/channels':
            return [ch for ch in self.channels if ch['id'] in self.joined], {}
        if method == 'POST' and path.startswith('/channels/') and path.endswith('/members'):
            self.joined.add(path.split('/')[2]); self.posts.append((path, body))
            if self.uncertain: raise b.BootstrapError('API outcome unavailable; inspect before retrying mutation')
            return None, {}
        raise AssertionError((method, path))


class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve(); self.directory.chmod(0o700)
        self.server = Server()
        self.credentials = {'operator_id': OWNER, 'operator_session': SESSION, 'username': 'mads',
                            'email': 'mads@home.arpa', 'password': 'synthetic-password-private',
                            'bot_id': BOT, 'bot_token': PAT, 'bot_token_id': TOKEN_ID}
        b.save(self.directory / 'credentials.json', self.credentials)
        b.save(self.directory / 'agent-account-state.json', {'provisioning_complete': True, 'accounts': [
            {'accountId': 'default', 'agentId': 'main', 'username': 'atlas', 'bot_id': BOT, 'token': PAT, 'token_id': TOKEN_ID}]})
        b.save(self.directory / 'n8n-channels.json', {'team_id': TEAM})
        server = self.server
        class API:
            def __init__(self, token=''): self.token = token
            def request(self, *args, **kwargs): return server.request(self.token, *args, **kwargs)
        self.b = SimpleNamespace(API=API, identity=b.identity, read_private=b.read_private,
                                 save=b.save, bootstrap_lock=b.bootstrap_lock, BootstrapError=b.BootstrapError)

    def run_sync(self): return sync.sync(self.b, self.directory)

    def channel(self, identifier='p', **extra):
        row = {'id': identifier * 26, 'type': 'P', 'team_id': TEAM, 'delete_at': 0, **extra}
        self.server.channels.append(row)
        return row

    def test_new_private_channel_joins_on_next_tick_without_account_or_message_writes(self):
        self.run_sync(); self.channel()
        result = self.run_sync()
        self.assertEqual(result['added'], 1); self.assertEqual(result['pending'], 0)
        self.assertEqual(self.server.posts, [(f"/channels/{'p' * 26}/members", {'user_id': BOT})])
        self.assertEqual(self.run_sync()['added'], 0)
        self.assertFalse(any(path.endswith('/tokens') or path == '/bots' or path == '/posts' for _, path in self.server.calls))
        self.assertFalse(any(method in {'PUT', 'DELETE', 'PATCH'} for method, _ in self.server.calls))
        self.assertEqual(b.read_private(self.directory / 'credentials.json'), self.credentials)

    def test_foreign_archived_and_direct_channels_never_join(self):
        self.channel('p', team_id='z' * 26); self.channel('q', delete_at=10)
        self.channel('r', type='D'); self.channel('s', type='G')
        self.assertEqual(self.run_sync()['added'], 0); self.assertEqual(self.server.posts, [])

    def test_trusted_family_channel_is_left_to_its_separate_assistant(self):
        self.channel('f')
        self.channel('p')
        b.save(self.directory / 'n8n-channels.json', {'team_id': TEAM, 'channels': {'family': 'f' * 26}})
        self.assertEqual(self.run_sync()['added'], 1)
        self.assertNotIn('f' * 26, self.server.joined)
        self.assertIn('p' * 26, self.server.joined)

    def test_pinned_identity_drift_and_incomplete_state_refuse_before_membership_change(self):
        self.run_sync(); self.channel()
        path = self.directory / 'atlas-membership-policy.json'; policy = b.read_private(path)
        policy['owner_id'] = 'z' * 26; b.save(path, policy)
        with self.assertRaisesRegex(b.BootstrapError, 'pinned'): self.run_sync()
        self.assertEqual(self.server.posts, [])

    def test_wrong_operator_or_bot_authority_refuses_without_membership_change(self):
        self.channel()
        for field in ['bad_owner', 'bad_bot']:
            setattr(self.server, field, True)
            with self.assertRaises(b.BootstrapError): self.run_sync()
            setattr(self.server, field, False)
        self.assertEqual(self.server.posts, [])

    def test_expired_session_renews_only_operator_session_and_keeps_atlas_pat(self):
        self.server.expired = True; self.run_sync(); updated = b.read_private(self.directory / 'credentials.json')
        self.assertEqual(updated, {**self.credentials, 'operator_session': SESSION + '-renewed'})
        self.assertEqual(sum(path == '/users/login' for _, path in self.server.calls), 1)
        self.run_sync()
        self.assertEqual(sum(path == '/users/login' for _, path in self.server.calls), 1)

    def test_uncertain_join_is_recovered_by_inventory_without_duplicate_post(self):
        self.channel(); self.server.uncertain = True
        with self.assertRaises(b.BootstrapError): self.run_sync()
        self.server.uncertain = False
        self.assertEqual(self.run_sync()['added'], 0); self.assertEqual(len(self.server.posts), 1)

    def test_full_bootstrap_lock_prevents_overlapping_membership_checks(self):
        with b.bootstrap_lock(self.directory):
            with self.assertRaisesRegex(b.BootstrapError, 'in progress'): self.run_sync()
        self.assertEqual(self.server.calls, [])

    def test_bulk_reconciliation_is_bounded_and_eventually_completes(self):
        for index in range(70): self.server.channels.append({'id': f'{index + 1:026d}', 'type': 'P', 'team_id': TEAM})
        first = self.run_sync(); second = self.run_sync()
        self.assertEqual(first['added'], 50); self.assertEqual(first['pending'], 20)
        self.assertEqual(second['added'], 20); self.assertEqual(second['pending'], 0)
        self.assertEqual(len(self.server.posts), 70)

    def test_private_policy_and_receipts_do_not_contain_plaintext_tokens(self):
        value = self.run_sync()
        for name in ['atlas-membership-policy.json', 'atlas-membership-status.json']:
            path = self.directory / name
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(PAT, path.read_text()); self.assertNotIn(SESSION, path.read_text())
        self.assertNotIn('password', json.dumps(value)); self.assertNotIn(PAT, json.dumps(value))


if __name__ == '__main__': unittest.main()
