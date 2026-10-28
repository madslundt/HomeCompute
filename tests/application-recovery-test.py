#!/usr/bin/env python3
"""Cold recovery boundaries, transactional SQLite copies, and failure resumption."""
import importlib.util
from contextlib import closing
import gzip
import hashlib
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
    def setUp(self):
        self.journal_root = tempfile.TemporaryDirectory()
        self.journal_patch = patch.object(recovery, 'RESUME_FILE', Path(self.journal_root.name) / 'resume.json')
        self.journal_patch.start()
        broker=Path(self.journal_root.name)/'broker.sqlite'
        with closing(sqlite3.connect(broker)) as db:db.execute('CREATE TABLE tasks(state TEXT)');db.commit()
        self.broker_patch=patch.object(recovery,'BROKER_DATABASE',broker);self.broker_patch.start()

    def tearDown(self):
        self.journal_patch.stop()
        self.broker_patch.stop()
        self.journal_root.cleanup()

    def test_sqlite_backup_includes_committed_wal_and_validates_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'original.sqlite'; target = root / 'copy.sqlite'
            with closing(sqlite3.connect(source)) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT)')
                db.execute('INSERT INTO fixture VALUES(1,?)', ('synthetic',)); db.commit()
                recovery.sqlite_backup(source, target)
                with closing(sqlite3.connect(target)) as copy:
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
            self.assertTrue(recovery.RESUME_FILE.exists())

    def test_resume_journal_cannot_start_arbitrary_resources(self):
        recovery.RESUME_FILE.write_text(json.dumps([['unit', 'unreviewed.service']]))
        recovery.RESUME_FILE.chmod(0o600)
        with patch.object(recovery, 'run') as run, self.assertRaises(ValueError): recovery.resume()
        run.assert_not_called()

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
            with closing(sqlite3.connect(databases / 'n8n.sqlite')) as db:
                db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY)'); db.execute('INSERT INTO fixture VALUES(1)')
                db.commit()
            (databases / 'n8n-config.json').write_text(json.dumps({'encryptionKey': 'synthetic-test-key'}))
            for name in recovery.POSTGRES:
                with gzip.open(databases / (name + '-postgres.sql.gz'), 'wb') as stream:
                    stream.write(b'-- synthetic PostgreSQL cluster dump\n')
            for disk in recovery.VM_DISKS:
                p = source / 'state' / disk; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'synthetic-disk')
            seed=source/'state/agents-vm/seed.iso';seed.write_bytes(b'synthetic-seed')
            manifest = {'schema_version': 1, 'postgres': sorted(recovery.POSTGRES),
                        'vms': ['agents-vm', 'codex-vm'], 'sqlite': ['n8n'],
                        'agents_seed':{'archive_path':'state/agents-vm/seed.iso','source':'/nix/store/'+'a'*32+'-homecompute-agents-seed.iso',
                                       'sha256':hashlib.sha256(seed.read_bytes()).hexdigest()}}
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
            self.assertTrue(result['agents_seed_verified'])

    def test_seed_is_derived_only_from_exact_readonly_owning_drive(self):
        value='/nix/store/'+'a'*32+'-homecompute-agents-seed.iso'
        self.assertEqual(recovery.seed_path(' -drive file='+value+',if=virtio,format=raw,readonly=on'),Path(value))
        for body in ['', 'file=/tmp/seed.iso,if=virtio,format=raw,readonly=on',
                     'file='+value+',if=virtio,format=raw',
                     ('file='+value+',if=virtio,format=raw,readonly=on ')*2]:
            with self.subTest(body=body),self.assertRaises(ValueError):recovery.seed_path(body)

    def test_incidental_live_seed_cannot_override_captured_owner_seed(self):
        self.assertIsNone(recovery.archive_filter(tarfile.TarInfo('state/agents-vm/seed.iso')))

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

    def test_transitional_unloaded_and_uncertain_unit_states_are_refused(self):
        for state, pid, loaded in [('activating','123','loaded'),('deactivating','123','loaded'),
                                   ('reloading','123','loaded'),('inactive','123','loaded'),
                                   ('failed','123','loaded'),('inactive','0','not-found'),('active','','loaded')]:
            output=f'LoadState={loaded}\nActiveState={state}\nMainPID={pid}\n'
            with self.subTest(state=state,pid=pid,loaded=loaded), \
                    patch.object(recovery,'run',return_value=subprocess.CompletedProcess([],0,output,'')), self.assertRaises(ValueError):
                recovery.unit_state('homecompute-agents-vm.service')

    def test_failed_unit_inspection_aborts_and_resumes_earlier_resources(self):
        with patch.object(recovery,'UNITS',('synthetic.service',)), \
                patch.object(recovery,'run',side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CalledProcessError(1,['systemctl'])]):
            with self.assertRaises(subprocess.CalledProcessError):
                with recovery.quiesced():self.fail('must refuse a failed inspection')

    def test_unknown_container_running_state_is_refused(self):
        with patch.object(recovery,'UNITS',()), patch.object(recovery,'CONTAINERS',('synthetic',)), \
                patch.object(recovery,'run',side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,'unknown\n','')]):
            with self.assertRaisesRegex(ValueError,'running state'):
                with recovery.quiesced():self.fail('must refuse unknown Docker state')

    def test_worker_idle_allows_pending_review_but_defers_active_and_unknown(self):
        for state in ['pending','queued','review','running','claimed','publishing','unreviewed']:
            with closing(sqlite3.connect(recovery.BROKER_DATABASE)) as db:
                db.execute('DELETE FROM tasks');db.execute('INSERT INTO tasks VALUES(?)',(state,));db.commit()
            with self.subTest(state=state):
                if state in {'pending','queued','review'}:recovery.worker_idle()
                else:
                    with self.assertRaises(ValueError):recovery.worker_idle()

    def test_worker_state_read_failure_never_creates_database(self):
        path=recovery.BROKER_DATABASE;path.unlink()
        with self.assertRaisesRegex(ValueError,'unavailable'):recovery.worker_idle()
        self.assertFalse(path.exists())

    def test_claim_handoff_race_defers_before_stopping_vm_and_resumes_api(self):
        units=('homecompute-codex-worker-api.service','homecompute-codex-broker.service','homecompute-codex-vm.service')
        calls=[];checks=[]
        def idle():
            checks.append(True)
            if len(checks)==2:raise ValueError('worker activity')
        def run(arguments,**kwargs):
            calls.append(arguments);return subprocess.CompletedProcess(arguments,0,'','')
        with patch.object(recovery,'UNITS',units),patch.object(recovery,'worker_idle',side_effect=idle), \
                patch.object(recovery,'unit_state',side_effect=['active','inactive','active','inactive']),patch.object(recovery,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'worker activity'):
                with recovery.quiesced():self.fail('must defer the racing claim')
        self.assertNotIn(['systemctl','stop','homecompute-codex-vm.service'],calls)
        self.assertIn(['systemctl','start','homecompute-codex-worker-api.service'],calls)


if __name__ == '__main__':
    unittest.main()
