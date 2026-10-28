#!/usr/bin/env python3
"""Fail-closed destination and custody tests; no NAS writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nas_backup", ROOT / "scripts/nas-recovery-backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
CONFIG = {"address": "192.168.30.236", "share": "homecompute-backups",
          "credentialsFile": "/run/secrets/nas-backup/credentials"}


class NASBackupTest(unittest.TestCase):
    def test_dedicated_share_validates(self):
        self.assertEqual(backup.configuration(CONFIG), CONFIG)

    def test_media_traversal_and_injected_targets_refused(self):
        for share in ["immich-library", "photos", "uploads", "video", "../backup", "x/y", "x\nfoo", "x;foo"]:
            with self.subTest(share=share), self.assertRaises(ValueError):
                backup.configuration({**CONFIG, "share": share})
        for address in ["nas.local", "192.168.30.236;echo", "::1"]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                backup.configuration({**CONFIG, "address": address})

    def test_credential_paths_and_unknown_fields_refused(self):
        for path in ["relative", "/run/../secret", "/run/foo\nbar", "/run/x;bad"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                backup.configuration({**CONFIG, "credentialsFile": path})
        with self.assertRaises(ValueError):
            backup.configuration({**CONFIG, "password": "forbidden"})

    def test_local_fallback_wrong_mount_or_wrong_share_refused(self):
        for entry in [
            {"target": "/", "source": "/dev/nvme0", "fstype": "ext4"},
            {"target": str(backup.MOUNT), "source": "//192.168.30.236/immich-library", "fstype": "cifs"},
            {"target": str(backup.MOUNT), "source": "//192.168.30.236/homecompute-backups", "fstype": "nfs"},
        ]:
            with patch.object(backup.subprocess, "check_output", return_value=json.dumps({"filesystems": [entry]})):
                with self.assertRaises(ValueError):
                    backup.require_mount(CONFIG)

    def test_valid_mount_checks_private_credentials(self):
        entry = {"target": str(backup.MOUNT), "source": "//192.168.30.236/homecompute-backups", "fstype": "cifs"}
        with patch.object(backup.subprocess, "check_output", return_value=json.dumps({"filesystems": [entry]})), \
                patch.object(backup, "private_file") as custody:
            backup.require_mount(CONFIG)
            custody.assert_called_once_with(Path(CONFIG["credentialsFile"]))

    def test_exact_ordered_systemd_automount_and_cifs_stack_is_admitted(self):
        target=str(backup.MOUNT)
        entries=[{'target':target,'source':'systemd-1','fstype':'autofs'},
                 {'target':target,'source':'//192.168.30.236/homecompute-backups','fstype':'cifs'}]
        with patch.object(backup.subprocess,'check_output',return_value=json.dumps({'filesystems':entries})), \
                patch.object(backup,'private_file') as custody:
            backup.require_mount(CONFIG)
            custody.assert_called_once()

    def test_foreign_reverse_duplicate_and_incomplete_mount_stacks_are_refused(self):
        target=str(backup.MOUNT)
        underlay={'target':target,'source':'systemd-1','fstype':'autofs'}
        cifs={'target':target,'source':'//192.168.30.236/homecompute-backups','fstype':'cifs'}
        for entries in [[],[underlay],[cifs,underlay],[cifs,cifs],[underlay,underlay,cifs],
                        [{**underlay,'source':'foreign'},cifs],
                        [underlay,{**cifs,'source':'//192.168.30.236/immich-library'}],
                        [underlay,{**cifs,'target':'/'}],[{**cifs,'fstype':'ext4'}]]:
            with self.subTest(entries=entries), \
                    patch.object(backup.subprocess,'check_output',return_value=json.dumps({'filesystems':entries})), \
                    patch.object(backup,'private_file') as custody:
                with self.assertRaises(ValueError):backup.require_mount(CONFIG)
                custody.assert_not_called()

    def test_existing_repo_is_never_initialized(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(backup, "REPOSITORY", Path(temporary)), \
                patch.object(backup, "restic") as restic:
            with self.assertRaises(ValueError):
                backup.initialize()
            restic.assert_not_called()

    def test_initialization_never_generates_or_replaces_a_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(backup, 'REPOSITORY', root / 'new-repository'), \
                    patch.object(backup, 'PASSWORD', root / 'missing-password'), \
                    patch.object(backup, 'root_directory'), \
                    patch.object(backup, 'private_file', side_effect=ValueError('password absent')) as custody, \
                    patch.object(backup, 'restic') as restic:
                with self.assertRaisesRegex(ValueError, 'password absent'): backup.initialize()
                custody.assert_called_once_with(root / 'missing-password')
                self.assertFalse((root / 'missing-password').exists())
                self.assertFalse((root / 'new-repository').exists())
                restic.assert_not_called()

    def test_symlinked_repo_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "repo"
            link.symlink_to(Path(temporary) / "missing")
            with patch.object(backup, "REPOSITORY", link), patch.object(backup, "restic") as restic:
                with self.assertRaises(ValueError):
                    backup.initialize()
                restic.assert_not_called()

    def test_nonroot_never_reaches_configuration_or_mount(self):
        with patch.object(backup.os, "geteuid", return_value=1000), \
                patch("sys.argv", ["backup", "init"]), patch.object(backup, "load_configuration") as loader:
            with self.assertRaises(PermissionError):
                backup.main()
            loader.assert_not_called()

    def test_restore_requires_exact_snapshot_name_and_new_scratch_target(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(backup, "root_directory"), \
                patch.object(backup, "RESTORE_ROOT", Path(temporary)), patch.object(backup, "restic") as restic:
            for snapshot, archive, target in [
                ('latest', 'homecompute-applications-20261028T120000.000000Z.tar', Path(temporary) / 'new'),
                ('a' * 64, '../other.tar', Path(temporary) / 'new'),
                ('a' * 64, 'homecompute-applications-20261028T120000.000000Z.tar', Path('/srv/state/immich')),
                ('a' * 64, 'homecompute-applications-20261028T120000.000000Z.tar', Path(temporary)),
            ]:
                with self.subTest(snapshot=snapshot, archive=archive), self.assertRaises(ValueError):
                    backup.restore_application(snapshot, archive, target)
            restic.assert_not_called()

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which("restic"), "real root/Restic integration")
    def test_real_encrypted_init_snapshot_and_restore_without_nas_or_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state = directory / "private"
            repository = directory / "repo"
            state.mkdir(mode=0o700)
            password = state / "restic-password"
            password.write_text("synthetic-test-password\n")
            password.chmod(0o600)
            with patch.object(backup, "STATE", state), patch.object(backup, "PASSWORD", state / "restic-password"), \
                    patch.object(backup, "REPOSITORY", repository):
                backup.initialize()
                backup.proof()
                result = json.loads((state / "last-restore-proof.json").read_text())
                self.assertEqual(result["bytes"], 128)
                self.assertTrue(result["matched"])
                self.assertEqual((state / "restic-password").stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):
                    backup.initialize()


if __name__ == "__main__":
    unittest.main()
