#!/usr/bin/env python3
"""Cold recovery boundaries, transactional SQLite copies, and failure resumption."""
import importlib.util
import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('application_recovery', ROOT / 'scripts/application-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class ApplicationRecoveryTest(unittest.TestCase):
    def test_sqlite_backup_includes_committed_wal_and_validates_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'original.sqlite'; target = root / 'copy.sqlite'
            with sqlite3.connect(source) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT)')
                db.execute('INSERT INTO fixture VALUES(1,?)', ('synthetic',)); db.commit()
                recovery.sqlite_backup(source, target)
                with sqlite3.connect(target) as copy:
                    self.assertEqual(copy.execute('SELECT value FROM fixture').fetchone(), ('synthetic',))
                    self.assertEqual(copy.execute('PRAGMA quick_check').fetchone(), ('ok',))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_sqlite_source_symlinks_and_destination_overwrite_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'db'; source.touch()
            link = root / 'link'; link.symlink_to(source)
            with self.assertRaises(ValueError): recovery.sqlite_backup(link, root / 'copy')
            with self.assertRaises(ValueError): recovery.sqlite_backup(source, source)

    def test_failure_resumes_only_previously_active_resources(self):
        calls = []
        def run(arguments, **kwargs):
            calls.append(arguments)
            if arguments[:2] == ['docker', 'ps']:
                return subprocess.CompletedProcess(arguments, 0, '', '')
            if arguments[:2] == ['docker', 'inspect']:
                return subprocess.CompletedProcess(arguments, 0, 'true\n' if arguments[2] == 'running' else 'false\n', '')
            return subprocess.CompletedProcess(arguments, 0, '', '')
        with patch.object(recovery, 'UNITS', ()), patch.object(recovery, 'CONTAINERS', ('running', 'stopped')), \
                patch.object(recovery, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                with recovery.quiesced():
                    raise RuntimeError('synthetic failure')
        self.assertIn(['docker', 'start', 'running'], calls)
        self.assertNotIn(['docker', 'start', 'stopped'], calls)

    def test_active_private_browser_defers_before_stopping_anything(self):
        with patch.object(recovery, 'run', return_value=subprocess.CompletedProcess([], 0, 'homecompute-site-view-synthetic\n', '')) as run:
            with self.assertRaisesRegex(ValueError, 'browser sessions'):
                with recovery.quiesced(): self.fail('must not enter quiesced section')
        self.assertEqual(run.call_count, 1)

    def test_resume_failure_is_reported(self):
        def run(arguments, **kwargs):
            if arguments[:2] == ['docker', 'inspect']:
                return subprocess.CompletedProcess(arguments, 0, 'true\n', '')
            if arguments[:2] == ['docker', 'start']:
                raise subprocess.CalledProcessError(1, arguments)
            return subprocess.CompletedProcess(arguments, 0, '', '')
        with patch.object(recovery, 'UNITS', ()), patch.object(recovery, 'CONTAINERS', ('running',)), \
                patch.object(recovery, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'could not resume'):
                with recovery.quiesced(): pass

    def test_archive_rejects_traversal_external_symlinks_and_special_files(self):
        for name, kind, target in [('../escape', tarfile.REGTYPE, ''), ('/absolute', tarfile.REGTYPE, ''),
                                   ('link', tarfile.SYMTYPE, '/etc/shadow'), ('pipe', tarfile.FIFOTYPE, '')]:
            with self.subTest(name=name), tempfile.TemporaryFile() as stream:
                with tarfile.open(fileobj=stream, mode='w') as archive:
                    row = tarfile.TarInfo(name); row.type = kind; row.linkname = target
                    archive.addfile(row)
                stream.seek(0)
                with tarfile.open(fileobj=stream) as archive, self.assertRaises(ValueError):
                    recovery.safe_members(archive)

    def test_nix_store_references_and_transient_sockets_are_not_archived(self):
        for name, target in [('state/qmp.sock', ''), ('config/tool', '/nix/store/synthetic/bin/tool')]:
            row = tarfile.TarInfo(name)
            if target: row.type = tarfile.SYMTYPE; row.linkname = target
            self.assertIsNone(recovery.archive_filter(row))

    def test_verify_refuses_existing_and_production_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root / 'archive.tar'; archive.touch()
            with patch.object(recovery, 'SCRATCH', root / 'scratch'):
                for destination in [root, Path('/srv/state/n8n'), root / 'other' / 'new']:
                    with self.subTest(destination=destination), self.assertRaises(ValueError):
                        recovery.verify(archive, destination)

    def test_isolated_restore_checks_real_sqlite_gzip_and_required_vm_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; source.mkdir()
            databases = source / 'databases'; databases.mkdir()
            with sqlite3.connect(databases / 'n8n.sqlite') as db:
                db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY)'); db.execute('INSERT INTO fixture VALUES(1)')
            (databases / 'n8n-config.json').write_text(json.dumps({'encryptionKey': 'synthetic-test-key'}))
            for name in recovery.POSTGRES:
                with gzip.open(databases / (name + '-postgres.sql.gz'), 'wb') as stream:
                    stream.write(b'-- synthetic PostgreSQL cluster dump\n')
            for disk in recovery.VM_DISKS:
                p = source / 'state' / disk; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'synthetic-disk')
            manifest = {'schema_version': 1, 'postgres': sorted(recovery.POSTGRES),
                        'vms': ['agents-vm', 'codex-vm'], 'sqlite': ['n8n']}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            archive_path = root / 'archive.tar'
            with tarfile.open(archive_path, 'w') as archive:
                for path in source.iterdir(): archive.add(path, arcname=path.name)
            scratch = root / 'scratch'; scratch.mkdir()
            with patch.object(recovery, 'SCRATCH', scratch), patch.object(recovery, 'run') as check:
                result = recovery.verify(archive_path, scratch / 'new')
            self.assertEqual(result['sqlite_integrity'], 'ok')
            self.assertEqual(result['gzip_dumps_checked'], 3)
            self.assertEqual(result['qcow2_disks_checked'], 3)
            self.assertEqual(check.call_count, 3)
            self.assertFalse(result['postgres_import_verified'])
            self.assertFalse(result['vm_boot_verified'])
            self.assertFalse(result['media_included'])

    def test_failure_never_publishes_partial_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archives = root / 'archives'
            archives.mkdir(mode=0o700)
            with patch.object(recovery, 'ARCHIVES', archives), patch.object(recovery, 'DEPLOY_LOCK', root / 'lock'), \
                    patch.object(recovery, 'RECOVERY_MARKER', root / 'marker'), \
                    patch.object(recovery, 'run', return_value=subprocess.CompletedProcess([], 0, 'ActiveState=inactive\n', '')), \
                    patch.object(recovery, 'private_directory'), patch.object(recovery, 'space_required', return_value=0), \
                    patch.object(recovery, 'quiesced', side_effect=RuntimeError('synthetic failure')):
                with self.assertRaises(RuntimeError): recovery.prepare()
            self.assertEqual(list(archives.iterdir()), [])
            self.assertFalse((root / 'marker').exists())

    def test_local_vm_backup_defers_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archives = root / 'archives'; archives.mkdir(mode=0o700)
            with patch.object(recovery, 'ARCHIVES', archives), patch.object(recovery, 'DEPLOY_LOCK', root / 'lock'), \
                    patch.object(recovery, 'RECOVERY_MARKER', root / 'marker'), patch.object(recovery, 'private_directory'), \
                    patch.object(recovery, 'space_required', return_value=0), patch.object(recovery, 'quiesced') as quiesced, \
                    patch.object(recovery, 'run', return_value=subprocess.CompletedProcess([], 0, 'ActiveState=active\n', '')):
                with self.assertRaisesRegex(ValueError, 'local VM backup'): recovery.prepare()
                quiesced.assert_not_called()
            self.assertFalse((root / 'marker').exists())

    def test_nonroot_cli_is_denied_before_preparation(self):
        with patch.object(recovery.os, 'geteuid', return_value=1000), patch('sys.argv', ['recovery', 'prepare']), \
                patch.object(recovery, 'prepare') as prepare:
            with self.assertRaises(PermissionError): recovery.main()
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
