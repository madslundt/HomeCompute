#!/usr/bin/env python3
"""Fail-closed destination and custody tests; no NAS writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nas_backup", ROOT / "scripts/nas-recovery-backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
CONFIG = {"address": "192.168.30.236", "share": "homecompute-backups",
          "credentialsFile": "/run/secrets/nas-backup/credentials",
          "hermesSshKeyFile": "/run/secrets/hermes-backup/ssh-key",
          "hermesKnownHostsFile": "/etc/homecompute/hermes-backup_known_hosts",
          "immichDbBackupScript": "/nix/store/abc123-immich-db-backup/scripts/immich-db-backup.sh",
          "immichComposeFile": "/nix/store/abc123-compose.yaml"}


class NASBackupTest(unittest.TestCase):
    def test_scheduled_backup_transfers_only_selected_state(self):
        with patch.object(backup, "backup") as transfer:
            backup.scheduled(CONFIG)
            transfer.assert_called_once_with(CONFIG)

    def test_backup_transfers_exactly_immich_media_database_and_hermes_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            dumps = directory / "db-backups"
            dumps.mkdir()
            helper = directory / "immich-db-backup.sh"
            helper.write_text("#!/bin/sh\nexit 0\n")
            helper.chmod(0o500)
            archive = directory / "hermes-native-home.tar.gz"
            archive.write_bytes(b"synthetic verified archive")
            summary = json.dumps({"message_type": "summary", "snapshot_id": "a" * 64})
            with patch.object(backup, "SOURCE", Path(temporary)), \
                    patch.object(backup, "HERMES_ARCHIVE", archive), \
                    patch.object(backup, "IMMICH_DATABASE_DUMPS", dumps), \
                    patch.object(backup, "root_directory"), \
                    patch.object(backup, "immich_library_mount"), \
                    patch.object(backup, "validate_immich_dumps", return_value=1), \
                    patch.object(backup, "export_hermes", return_value={"databases": 1}), \
                    patch.object(backup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(backup, "restic", return_value=summary) as restic, \
                    patch.object(backup, "prune_retention") as prune, \
                    patch.object(backup, "record") as record:
                backup.backup({**CONFIG, "immichDbBackupScript": str(helper)})
            args = restic.call_args.args[0]
            self.assertEqual(args[:5], ["backup", "--json", "--tag", "homecompute-selected-state",
                                       str(archive)])
            self.assertEqual(args[5:], [str(backup.IMMICH_LIBRARY), str(dumps)])
            prune.assert_called_once_with()
            self.assertEqual(record.call_args.args[0], "last-backup.json")
            self.assertEqual(set(record.call_args.args[1]["scope"]), {
                "Immich media library", "Immich database dumps", "Hermes native-home state"})
            self.assertFalse(archive.exists())

    def test_dedicated_share_validates(self):
        self.assertEqual(backup.configuration(CONFIG), CONFIG)
        source_path = "/nix/store/qyz0igh4lr0557h61p9kfialsc98svd7-source/deploy/immich/compose.yaml"
        self.assertEqual(backup.configuration({**CONFIG, "immichComposeFile": source_path})[
            "immichComposeFile"], source_path)

    def test_selected_helper_paths_are_strict(self):
        for field, path in [("hermesSshKeyFile", "/run/../bad"),
                            ("hermesKnownHostsFile", "relative"),
                            ("immichDbBackupScript", "/tmp/immich-db-backup.sh"),
                            ("immichComposeFile", "/tmp/compose.yaml")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                backup.configuration({**CONFIG, field: path})

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

    def test_private_directory_uses_root_only_directory_guard(self):
        path = Path("/srv/backups/homecompute-selected-state")
        with patch.object(backup, "root_directory") as guard:
            backup.private_directory(path, create=True)
            guard.assert_called_once_with(path, create=True)

    def test_export_hermes_creates_temporary_archive_and_promotes_verified_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "hermes-native-home.tar.gz"
            checked = {"scope": "Hermes native-home state", "databases": 1}

            def exporter(_command, *, stdout, stderr, timeout, check):
                stdout.write(b"verified archive")
                return subprocess.CompletedProcess([], 0)

            with patch.object(backup, "SOURCE", directory), \
                    patch.object(backup, "HERMES_ARCHIVE", archive), \
                    patch.object(backup, "private_file"), \
                    patch.object(backup, "pinned_known_hosts"), \
                    patch.object(backup, "root_directory") as directory_guard, \
                    patch.object(backup, "verify_hermes_archive", return_value=checked), \
                    patch.object(backup.subprocess, "run", side_effect=exporter):
                self.assertEqual(backup.export_hermes(CONFIG), checked)
            directory_guard.assert_called_once_with(directory, create=True)
            self.assertEqual(archive.read_bytes(), b"verified archive")
            self.assertFalse((directory / ".hermes-native-home.tar.gz.partial").exists())

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

    def test_restore_requires_exact_snapshot_and_new_scratch_target(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(backup, "root_directory"), \
                patch.object(backup, "RESTORE_ROOT", Path(temporary)), patch.object(backup, "restic") as restic:
            for snapshot, target in [('latest', Path(temporary) / 'new'),
                                     ('a' * 64, Path('/srv/state/immich')),
                                     ('a' * 64, Path(temporary))]:
                with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                    backup.restore_selected(snapshot, target)
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
