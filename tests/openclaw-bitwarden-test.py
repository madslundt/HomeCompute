#!/usr/bin/env python3
"""Custody boundary tests use disposable synthetic credentials only."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_bitwarden import BitwardenLogin, CustodyRefused, provision, provision_account, qualified_bws, read_private, validate_token


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.directory = self.base / 'custody'
        self.config = {'schema_version': 1, 'server': 'https://vault.bitwarden.com',
                       'organization_id': '00000000-0000-4000-8000-000000000001',
                       'project_id': '00000000-0000-4000-8000-000000000002',
                       'password_secret_id': '00000000-0000-4000-8000-000000000003',
                       'username': 'synthetic@example.invalid'}
        self.token = '0.00000000-0000-4000-8000-000000000004.' + 'a' * 44 + ':' + 'b' * 44
        self.password = 'synthetic-password-Never-Print'
        self.binary = patch('openclaw_bitwarden.qualified_bws', return_value=Path('/trusted/bws'))
        self.binary.start()
        self.addCleanup(self.binary.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def response(self, **changes):
        value = {'id': self.config['password_secret_id'], 'organizationId': self.config['organization_id'],
                 'projectId': self.config['project_id'], 'key': 'hotels.com.password', 'value': self.password}
        value.update(changes)
        return subprocess.CompletedProcess([], 0, json.dumps(value).encode())

    def test_intake_creates_private_files_without_printing_or_overwriting(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            provision(self.directory, self.config, self.token)
            with self.assertRaises(CustodyRefused):
                provision(self.directory, self.config, self.token.replace('a', 'x'))
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for name in ('hotels.json', 'bitwarden-access-token'):
            self.assertEqual(stat.S_IMODE((self.directory / name).stat().st_mode), 0o600)
        self.assertEqual(read_private(self.directory, 'bitwarden-access-token', 4097).decode().strip(), self.token)

    def test_symlinked_directory_or_ancestor_and_existing_secret_are_refused(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        self.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            provision(self.directory, self.config, self.token)
        with self.assertRaises(OSError):
            provision(self.directory / 'nested', self.config, self.token)
        self.assertEqual(list(target.iterdir()), [])
        self.directory.unlink()
        self.directory.mkdir(mode=0o700)
        victim = target / 'victim'
        victim.write_text('unchanged')
        (self.directory / 'bitwarden-access-token').symlink_to(victim)
        with self.assertRaises(CustodyRefused):
            provision(self.directory, self.config, self.token)
        self.assertEqual(victim.read_text(), 'unchanged')
        self.assertFalse((self.directory / 'hotels.json').exists())

    def test_relaxed_modes_and_hardlinks_fail_closed(self):
        provision(self.directory, self.config, self.token)
        path = self.directory / 'bitwarden-access-token'
        path.chmod(0o640)
        with self.assertRaises(CustodyRefused):
            read_private(self.directory, path.name, 4097)
        path.chmod(0o600)
        os.link(path, self.base / 'second-link')
        with self.assertRaises(CustodyRefused):
            read_private(self.directory, path.name, 4097)

    def test_provider_uses_exact_identity_and_no_inherited_environment_or_token_arguments(self):
        provision(self.directory, self.config, self.token)
        with patch.dict(os.environ, {'HTTP_PROXY': 'https://synthetic.invalid', 'OPENAI_API_KEY': 'sentinel'}):
            with patch('openclaw_bitwarden.subprocess.run', return_value=self.response()) as run:
                login = BitwardenLogin(self.directory, Path('/trusted/bws')).retrieve()
        command, = run.call_args.args
        kwargs = run.call_args.kwargs
        self.assertEqual(command[:4], ['/trusted/bws', 'secret', 'get', self.config['password_secret_id']])
        self.assertNotIn(self.token, command)
        self.assertEqual(kwargs['env']['BWS_ACCESS_TOKEN'], self.token)
        self.assertNotIn('HTTP_PROXY', kwargs['env'])
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertEqual(login.password, self.password)
        self.assertNotIn(self.password, repr(login))
        self.assertNotIn(self.config['username'], repr(login))
        self.assertFalse(Path(kwargs['cwd']).exists())

    def test_wrong_identity_and_secret_bearing_failures_never_escape(self):
        provision(self.directory, self.config, self.token)
        results = [self.response(projectId='wrong'), self.response(organizationId='wrong'),
                   self.response(id='wrong'), self.response(key='another.password'),
                   subprocess.CompletedProcess([], 1, self.password.encode()),
                   subprocess.CompletedProcess([], 0, self.password.encode()),
                   subprocess.CompletedProcess([], 0, b'x' * 65537)]
        provider = BitwardenLogin(self.directory, Path('/trusted/bws'))
        for response in results:
            with self.subTest(response_type=type(response).__name__):
                with patch('openclaw_bitwarden.subprocess.run', return_value=response):
                    with self.assertRaises(CustodyRefused) as error:
                        provider.retrieve()
                self.assertEqual(str(error.exception), 'Bitwarden credential retrieval refused')
                self.assertTrue(error.exception.__suppress_context__)
        with patch('openclaw_bitwarden.subprocess.run', side_effect=RuntimeError(self.password)):
            with self.assertRaises(CustodyRefused) as error:
                provider.retrieve()
        self.assertNotIn(self.password, str(error.exception))

    def test_another_website_uses_its_own_mapping_and_exact_secret_name(self):
        provision(self.directory, self.config, self.token)
        provision_account(self.directory, 'shop.example', dict(self.config, username='synthetic-user'))
        with self.assertRaises(FileExistsError):
            provision_account(self.directory, 'shop.example', self.config)
        self.assertEqual(read_private(self.directory, 'bitwarden-access-token', 4097).decode().strip(), self.token)
        provider = BitwardenLogin(self.directory, Path('/trusted/bws'), account='shop.example')
        with patch('openclaw_bitwarden.subprocess.run', return_value=self.response(key='shop.example.password')):
            self.assertEqual(provider.retrieve().username, 'synthetic-user')
        with patch('openclaw_bitwarden.subprocess.run', return_value=self.response()):
            with self.assertRaises(CustodyRefused):
                provider.retrieve()
        for account in ['../hotels', 'https://hotels.com', 'hotels.com/other', 'user@hotels.com']:
            with self.assertRaises(CustodyRefused):
                BitwardenLogin(self.directory, Path('/trusted/bws'), account=account)

    def test_arbitrary_origin_and_unknown_mapping_fields_are_refused(self):
        for change in ({'server': 'https://attacker.invalid'}, {'command': 'anything'},
                       {'password_secret_id': '../../elsewhere'}, {'username': 'x@y\nInjected'}):
            with self.assertRaises((CustodyRefused, ValueError)):
                provision(self.directory, dict(self.config, **change), self.token)
        self.assertFalse(self.directory.exists())

    def test_non_store_executable_is_refused(self):
        candidate = self.base / 'bws'
        candidate.write_text('synthetic')
        candidate.chmod(0o755)
        with self.assertRaises(CustodyRefused):
            qualified_bws(candidate)

    def test_generic_passwords_and_personal_api_keys_are_not_machine_tokens(self):
        for value in ['ordinary-vault-password', 'a' * 128, 'user.synthetic:' + 'b' * 80,
                      self.token + '\n', self.token.replace('00000000-', 'bad-reference-', 1)]:
            with self.assertRaises(CustodyRefused):
                validate_token(value)


if __name__ == '__main__':
    unittest.main()
