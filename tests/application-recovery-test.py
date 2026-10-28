#!/usr/bin/env python3
"""Cold recovery boundaries, transactional SQLite copies, and failure resumption."""
import importlib.util
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import socket
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('application_recovery', ROOT / 'scripts/application-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
REAL_HTTP_IDLE = recovery.main_http_idle


class ApplicationRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.journal_root = tempfile.TemporaryDirectory()
        self.journal_patch = patch.object(recovery, 'RESUME_FILE', Path(self.journal_root.name) / 'resume.json')
        self.journal_patch.start()
        broker=Path(self.journal_root.name)/'broker.sqlite'
        with closing(sqlite3.connect(broker)) as db:db.execute('CREATE TABLE tasks(state TEXT)');db.commit()
        self.broker_patch=patch.object(recovery,'BROKER_DATABASE',broker);self.broker_patch.start()
        self.http_patch=patch.object(recovery,'main_http_idle');self.http_patch.start()

    def tearDown(self):
        self.journal_patch.stop()
        self.broker_patch.stop()
        self.http_patch.stop()
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

    def test_resume_reclaims_gateway_ips_and_webui_before_dynamic_clients(self):
        resources=[('unit','homecompute-codex-broker.service'),
                   ('container','homecompute-control-plane-caddy-1'),
                   ('container','homecompute-control-plane-litellm-1'),
                   ('container','homecompute-open-webui-open-webui-1'),
                   ('container','omi-local-backend-1')]
        allocated={4,7};addresses={};calls=[]
        fixed={'homecompute-control-plane-caddy-1':2,'homecompute-control-plane-litellm-1':3}
        def run(arguments,**kwargs):
            name=arguments[-1];calls.append(name)
            if arguments[:2]==['docker','start']:
                address=fixed.get(name,min(set(range(2,255))-allocated))
                if address in allocated:raise subprocess.CalledProcessError(1,arguments)
                allocated.add(address);addresses[name]=address
            return subprocess.CompletedProcess(arguments,0,'','')
        with patch.object(recovery,'run',side_effect=run):recovery.resume(resources)
        self.assertEqual(calls,['homecompute-control-plane-litellm-1','homecompute-control-plane-caddy-1',
                                'homecompute-open-webui-open-webui-1','omi-local-backend-1','homecompute-codex-broker.service'])
        self.assertEqual(addresses,{'homecompute-control-plane-litellm-1':3,'homecompute-control-plane-caddy-1':2,
                                    'homecompute-open-webui-open-webui-1':5,'omi-local-backend-1':6})
        self.assertNotIn('homecompute-image-studio-open-webui-1',calls)

    def test_resume_priority_never_starts_gateway_or_webui_absent_from_journal(self):
        with patch.object(recovery,'run') as run:recovery.resume([('container','omi-local-backend-1')])
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0],['docker','start','omi-local-backend-1'])

    def test_backup_journals_proxy_before_backend_requires_stop_and_rearms_watcher_after_ready(self):
        watcher=recovery.ACCESS_CERTIFICATE_PATH;proxy=recovery.ACCESS_PROXY;backend='homecompute-openclaw-access.service'
        selected={watcher,recovery.ACCESS_CERTIFICATE_SERVICE,proxy,backend}
        units=tuple(name for name in recovery.UNITS if name in selected)
        self.assertEqual(set(units),selected)
        states={name:('inactive' if name==recovery.ACCESS_CERTIFICATE_SERVICE else 'active') for name in units};events=[]
        def run(arguments,**kwargs):
            if arguments[:2]==['systemctl','stop']:
                name=arguments[-1];events.append(('stop',name));states[name]='inactive'
                if name==backend:states[proxy]='inactive'  # PID1 Requires propagation.
            if arguments[:2]==['systemctl','start']:
                name=arguments[-1];events.append(('start',name));states[name]='active'
            return subprocess.CompletedProcess(arguments,0,'','')
        def ready():
            self.assertEqual(states[backend],'active');self.assertEqual(states[proxy],'active');events.append(('ready',proxy))
        with patch.object(recovery,'UNITS',units),patch.object(recovery,'CONTAINERS',()), \
                patch.object(recovery,'unit_state',side_effect=lambda name:states[name]),patch.object(recovery,'run',side_effect=run), \
                patch.object(recovery,'approval_proxy_ready',side_effect=ready):
            with recovery.quiesced():
                journal=json.loads(recovery.RESUME_FILE.read_bytes())
                self.assertIn(['unit',proxy],journal)
                self.assertIn(['unit',watcher],journal)
        self.assertLess(events.index(('stop',watcher)),events.index(('stop',proxy)))
        self.assertLess(events.index(('stop',proxy)),events.index(('stop',backend)))
        self.assertLess(events.index(('start',backend)),events.index(('start',proxy)))
        self.assertLess(events.index(('start',proxy)),events.index(('ready',proxy)))
        self.assertLess(events.index(('ready',proxy)),events.index(('start',watcher)))

    def test_stopped_proxy_and_watcher_remain_stopped_after_backend_backup(self):
        watcher=recovery.ACCESS_CERTIFICATE_PATH;proxy=recovery.ACCESS_PROXY;backend='homecompute-openclaw-access.service'
        units=(watcher,proxy,backend);states={watcher:'inactive',proxy:'inactive',backend:'active'};calls=[]
        def run(arguments,**kwargs):
            calls.append(arguments)
            if arguments[:2]==['systemctl','stop']:states[arguments[-1]]='inactive'
            return subprocess.CompletedProcess(arguments,0,'','')
        with patch.object(recovery,'UNITS',units),patch.object(recovery,'CONTAINERS',()), \
                patch.object(recovery,'unit_state',side_effect=lambda name:states[name]),patch.object(recovery,'run',side_effect=run), \
                patch.object(recovery,'approval_proxy_ready') as ready:
            with recovery.quiesced():pass
        ready.assert_not_called()
        self.assertNotIn(['systemctl','start',proxy],calls);self.assertNotIn(['systemctl','start',watcher],calls)

    def test_unready_restored_proxy_keeps_watcher_paused_and_journal_retained(self):
        resources=[('unit',recovery.ACCESS_CERTIFICATE_PATH),('unit',recovery.ACCESS_PROXY),('unit','homecompute-openclaw-access.service')]
        recovery.save_resume(resources)
        with patch.object(recovery,'run') as run,patch.object(recovery,'approval_proxy_ready',side_effect=RuntimeError('private approval proxy readiness failed')):
            with self.assertRaisesRegex(RuntimeError,'could not resume'):recovery.resume(resources)
        self.assertNotIn(['systemctl','start',recovery.ACCESS_CERTIFICATE_PATH],[call.args[0] for call in run.call_args_list])
        self.assertTrue(recovery.RESUME_FILE.exists())

    def test_proxy_readiness_uses_only_existing_unix_reload_and_retries_startup(self):
        responses=[subprocess.CompletedProcess([],0,'false\n',''),subprocess.CompletedProcess([],0,'true\n',''),subprocess.CompletedProcess([],0,'','')]
        with patch.object(recovery,'run',side_effect=responses) as run,patch.object(recovery.time,'sleep'):
            recovery.approval_proxy_ready()
        command=run.call_args.args[0]
        self.assertEqual(command[:4],['docker','exec',recovery.ACCESS_PROXY_CONTAINER,'caddy'])
        self.assertEqual(command[-2:],['--address','unix//tmp/caddy-admin.sock'])
        self.assertIn('/etc/caddy/Caddyfile',command)

    def test_proxy_readiness_fails_closed_when_state_is_missing_or_unready(self):
        for state in ['false\n','unknown\n','']:
            with self.subTest(state=state),patch.object(recovery,'run',return_value=subprocess.CompletedProcess([],0,state,'')) as run, \
                    patch.object(recovery.time,'monotonic',side_effect=[0,31]),patch.object(recovery.time,'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError,'private approval proxy readiness failed'):recovery.approval_proxy_ready()
                self.assertEqual(run.call_count,1);sleep.assert_not_called()

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
            app=source/'state/open-webui';app.mkdir()
            with closing(sqlite3.connect(app/'webui.db')) as db:
                db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY)');db.commit()
            (app/'arbitrary.db').write_bytes(b'not a SQLite database')
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
            self.assertEqual(result['sqlite_databases_checked'], 2)
            self.assertEqual(result['gzip_dumps_checked'], 4)
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
                    patch.object(recovery, 'private_directory'), patch.object(recovery, 'validate_volumes'), patch.object(recovery, 'space_required', return_value=0), \
                    patch.object(recovery, 'quiesced', side_effect=RuntimeError('synthetic failure')):
                with self.assertRaises(RuntimeError): recovery.prepare()
            self.assertEqual(list(archives.iterdir()), [])
            self.assertFalse((root / 'marker').exists())

    def test_local_vm_backup_defers_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archives = root / 'archives'; archives.mkdir(mode=0o700)
            with patch.object(recovery, 'ARCHIVES', archives), patch.object(recovery, 'DEPLOY_LOCK', root / 'lock'), \
                    patch.object(recovery, 'RECOVERY_MARKER', root / 'marker'), patch.object(recovery, 'private_directory'), patch.object(recovery, 'validate_volumes'), \
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


    def test_private_browser_launch_race_defers_before_vms_and_resumes_owners(self):
        units=('homecompute-openclaw-sites.service','homecompute-openclaw-access.service','homecompute-codex-vm.service')
        calls=[]
        def run(arguments,**kwargs):
            calls.append(arguments);return subprocess.CompletedProcess(arguments,0,'','')
        with patch.object(recovery,'UNITS',units),patch.object(recovery,'private_browsers_idle',side_effect=[None,ValueError('browser sessions')]), \
                patch.object(recovery,'unit_state',side_effect=['active','inactive','active','inactive']),patch.object(recovery,'run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'browser sessions'):
                with recovery.quiesced():self.fail('must defer the racing browser')
        self.assertNotIn(['systemctl','stop','homecompute-codex-vm.service'],calls)
        self.assertIn(['systemctl','start','homecompute-openclaw-sites.service'],calls)
        self.assertIn(['systemctl','start','homecompute-openclaw-access.service'],calls)

    def test_only_exact_local_omi_volume_and_binding_are_admitted(self):
        volume={'Name':'omi-local_omi-data','Driver':'local','Scope':'local',
                'Mountpoint':str(recovery.DIRECTORIES['omi-data']),'Options':None}
        mount={'Name':volume['Name'],'Source':volume['Mountpoint'],'Destination':'/data','Type':'volume'}
        def inspect(v,m):
            return [subprocess.CompletedProcess([],0,json.dumps([v]),''),subprocess.CompletedProcess([],0,json.dumps([m]),'')]
        with patch.object(recovery,'run',side_effect=inspect(volume,mount)):recovery.validate_volumes()
        for field,value in [('Driver','nfs'),('Scope','global'),('Mountpoint','/srv/other'),('Options',{'type':'nfs'})]:
            with self.subTest(field=field),patch.object(recovery,'run',side_effect=inspect({**volume,field:value},mount)),self.assertRaises(ValueError):
                recovery.validate_volumes()
        for field,value in [('Source','/srv/other'),('Name','other'),('Type','bind'),('Destination','/other')]:
            with self.subTest(field=field),patch.object(recovery,'run',side_effect=inspect(volume,{**mount,field:value})),self.assertRaises(ValueError):
                recovery.validate_volumes()

    def test_nas_audio_and_diary_are_excluded_but_local_omi_state_is_retained(self):
        for relative in ['audio','audio/file.wav','diary','diary/day.json']:
            self.assertIsNone(recovery.archive_filter(tarfile.TarInfo('state/omi-data/'+relative)))
        self.assertIsNotNone(recovery.archive_filter(tarfile.TarInfo('state/omi-data/local.sqlite3')))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);omi=root/'omi';omi.mkdir();n8n=root/'n8n';n8n.mkdir()
            for name in ['audio','diary']:
                path=omi/name;path.mkdir();(path/'excluded').write_bytes(b'large synthetic NAS file')
            (omi/'local').write_bytes(b'abc')
            with patch.object(recovery,'DIRECTORIES',{'omi-data':omi}),patch.object(recovery,'N8N',n8n),patch.object(recovery,'EXTRA_FILES',{}), \
                    patch.object(recovery,'MIN_FREE_BYTES',0):
                self.assertEqual(recovery.space_required(),1024**3+3)

    def test_new_local_applications_have_fixed_writer_and_archive_coverage(self):
        self.assertEqual(recovery.POSTGRES['chessmate'],'chessmate-db-1')
        self.assertEqual(recovery.EXTRA_FILES['chessmate/runtime.env'],Path('/home/mads/projects/chessmate/config/.env'))
        self.assertEqual(recovery.DIRECTORIES['open-webui'],Path('/srv/state/open-webui/data'))
        self.assertEqual(recovery.DIRECTORIES['books-importer'],Path('/srv/state/books_importer'))
        self.assertEqual(recovery.DIRECTORIES['image-studio'],Path('/srv/state/image-studio/data'))
        for name in ['homecompute-open-webui-open-webui-1','homecompute-image-studio-open-webui-1','omi-local-backend-1','chessmate-backend-1',
                     'homecompute-books_importer-shelfmark-1','homecompute-books_importer-shelfmark-automated-1','homecompute-books_importer-cwa-1']:
            self.assertIn(name,recovery.CONTAINERS)

    def test_main_http_parser_matches_exact_ports_and_conservative_states(self):
        header='  sl local_address rem_address st\n'
        def row(address='172.28.200.1',port=18005,state='01'):
            encoded=socket.inet_aton(address)[::-1].hex().upper()
            return f' 0: {encoded}:{port:04X} 00000000:0000 {state}\n'
        for port in recovery.MAIN_HTTP_PORTS:
            for state in ['01','02','03','04','05','08','09','0B','FF']:
                self.assertEqual(recovery.main_http_connections(header+row(port=port,state=state)),1)
        for value in [row(state='06'),row(state='07'),row(state='0A'),row(address='172.28.200.2'),row(port=19000)]:
            self.assertEqual(recovery.main_http_connections(header+value),0)
        for body in ['', 'unavailable\n',header+'bad row\n',header+row(state='invalid')]:
            with self.assertRaises(ValueError):recovery.main_http_connections(body)

    def test_main_http_idle_requires_three_quiet_samples_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'tcp';path.write_text('sl local_address rem_address st\n')
            with patch.object(recovery,'MAIN_HTTP_TABLE',path),patch.object(recovery.time,'sleep') as sleep, \
                    patch.object(recovery,'main_http_connections',side_effect=[0,0,0]) as parser:
                REAL_HTTP_IDLE()
                self.assertEqual(parser.call_count,3);self.assertEqual(sleep.call_count,2)
            for counts in [[1],[0,1],[0,0,1]]:
                with patch.object(recovery,'MAIN_HTTP_TABLE',path),patch.object(recovery.time,'sleep'), \
                        patch.object(recovery,'main_http_connections',side_effect=counts),self.assertRaises(ValueError):REAL_HTTP_IDLE()
            path.write_text('malformed')
            with patch.object(recovery,'MAIN_HTTP_TABLE',path),self.assertRaises(ValueError):REAL_HTTP_IDLE()
            path.write_text('sl local_address rem_address st\n'+' '*1024**2)
            with patch.object(recovery,'MAIN_HTTP_TABLE',path),self.assertRaises(ValueError):REAL_HTTP_IDLE()
            path.unlink()
            with patch.object(recovery,'MAIN_HTTP_TABLE',path),self.assertRaises(FileNotFoundError):REAL_HTTP_IDLE()

    def test_busy_main_http_defers_before_any_source_stops(self):
        with patch.object(recovery,'main_http_idle',side_effect=ValueError('main HTTP activity')),patch.object(recovery,'run') as run:
            with self.assertRaisesRegex(ValueError,'main HTTP activity'):
                with recovery.quiesced():self.fail('must defer before sources stop')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
