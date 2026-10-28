#!/usr/bin/env python3
"""Credential provisioning never logs a password or overwrites existing custody."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('credentials', ROOT / 'scripts/provision-nas-backup-credentials.py')
credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credentials)


class CredentialTests(unittest.TestCase):
    def test_only_matching_single_line_dedicated_credentials_are_admitted(self):
        secret = 'synthetic-password-012345'
        value = credentials.credential(secret, secret, 'homecompute-backup')
        self.assertIn(b'username=homecompute-backup\n', value)
        for password, confirmation, user in [(secret, 'different', 'homecompute-backup'),
                (secret+'\nadmin=true', secret+'\nadmin=true', 'homecompute-backup'),
                (secret, secret, 'admin')]:
            with self.assertRaises(ValueError):
                credentials.credential(password, confirmation, user)

    def test_existing_or_symlink_credentials_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials'
            original = b'original'
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                credentials.provision(path, b'new')
            self.assertEqual(path.read_bytes(), original)
            link = path.with_name('link')
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                credentials.provision(link, b'new')

    def test_validation_projection_never_contains_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials'
            path.write_bytes(credentials.credential('synthetic-secret-012345', 'synthetic-secret-012345', 'homecompute-backup'))
            path.chmod(0o600)
            original_lstat = Path.lstat
            def root_stat(selected):
                info = original_lstat(selected)
                fields = list(info)
                fields[4] = 0
                return os.stat_result(fields)
            with patch.object(Path, 'lstat', root_stat):
                result = credentials.validate(path, 'homecompute-backup')
            self.assertTrue(result['credential_ready'])
            self.assertNotIn('synthetic-secret', str(result))


if __name__ == '__main__':
    unittest.main()
