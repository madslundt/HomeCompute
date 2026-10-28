#!/usr/bin/env python3
"""Isolated restore gates and synthetic PostgreSQL clean-role semantics."""
from contextlib import closing
import gzip
import hashlib
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
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery_engine', ROOT / 'scripts/verify-application-recovery.py')
engine = importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)


class RecoveryEngineTest(unittest.TestCase):
    def test_policy_covers_four_actual_engines_and_both_original_disk_layouts(self):
        settings = engine.policy()
        self.assertEqual(set(settings['postgres']), {'control-plane','mattermost','immich','chessmate'})
        self.assertEqual(settings['postgres']['chessmate']['major'],17)
        self.assertEqual(settings['postgres']['immich']['extensions']['vchord'],'0.4.3')
        self.assertEqual(settings['vms']['codex-vm']['disks'],['root.qcow2','work.qcow2'])

    def test_postgres_isolation_disallows_host_storage_network_ports_privileges_or_pulls(self):
        for item in engine.policy()['postgres'].values():
            command = engine.pg_command(item,'synthetic','n'*32,'hc_restore_synthetic')
            for flag in ['--pull=never','--network=none','--cap-drop=ALL','--read-only','--memory=1g','--cpus=1','--pids-limit=128','--log-driver=none']:
                self.assertIn(flag,command)
            for forbidden in ['--volume','-v','--mount','--publish','-p','--privileged','--pid=host','--network=host']:
                self.assertNotIn(forbidden,command)
            self.assertIn(f"{item['uid']}:{item['gid']}",command)
            self.assertIn(item['image_id'],command)
            self.assertIn('listen_addresses=',command)

    def test_cleanup_cannot_remove_foreign_container_even_with_same_name(self):
        for item in [{'Id':'a'*64,'Config':{'Labels':{engine.LABEL:'foreign'}}},
                     {'Id':'b'*64,'Config':{'Labels':{engine.LABEL:'nonce'}}}]:
            with self.subTest(item=item),patch.object(engine,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(item).encode())) as run:
                with self.assertRaises(engine.CleanupError):engine.remove_owned('test','a'*64,'nonce')
                self.assertEqual(run.call_count,1)

    def test_runtime_container_inspection_rejects_host_mounts_or_network_before_start(self):
        item=engine.policy()['postgres']['control-plane'];identifier='a'*64
        value={'Id':identifier,'Image':item['image_id'],'Config':{'User':'70:70','Labels':{engine.LABEL:'nonce'}},
               'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'CapDrop':['ALL'],
                             'PortBindings':{},'PublishAllPorts':False,'Binds':[],
                             'Tmpfs':{'/var/lib/postgresql/data':'private','/var/run/postgresql':'private','/tmp':'private'}},'Mounts':[]}
        with patch.object(engine,'run',return_value=SimpleNamespace(stdout=json.dumps(value).encode())):
            engine.container_isolated(identifier,item,'nonce')
        for field,change in [('NetworkMode','host'),('Privileged',True),('Binds',['/srv/state:/data']),('CapDrop',[])]:
            bad={**value,'HostConfig':{**value['HostConfig'],field:change}}
            with self.subTest(field=field),patch.object(engine,'run',return_value=SimpleNamespace(stdout=json.dumps(bad).encode())),self.assertRaises(engine.VerificationError):
                engine.container_isolated(identifier,item,'nonce')
        bad={**value,'Mounts':[{'Type':'volume','Destination':'/extra'}]}
        with patch.object(engine,'run',return_value=SimpleNamespace(stdout=json.dumps(bad).encode())),self.assertRaises(engine.VerificationError):
            engine.container_isolated(identifier,item,'nonce')

    def test_ambiguous_create_timeout_can_cleanup_only_exact_nonce_owned_container(self):
        item={'Id':'a'*64,'Config':{'Labels':{engine.LABEL:'nonce'}}}
        with patch.object(engine,'run',side_effect=[SimpleNamespace(returncode=0,stdout=json.dumps(item).encode()),SimpleNamespace(returncode=0)]) as run:
            engine.remove_owned('test',None,'nonce')
        self.assertEqual(run.call_args.args[0],['docker','rm','--force','--volumes','a'*64])

    def test_sql_decompression_is_bounded_and_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'test.gz'
            with gzip.open(source,'wb') as stream:stream.write(b'synthetic SQL')
            with patch.object(engine,'MAX_SQL',3),self.assertRaisesRegex(engine.VerificationError,'size-bound'):
                engine.sql_file(source,root/'bounded.sql')
            target=root/'retained.sql';target.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):engine.sql_file(source,target)
            self.assertEqual(target.read_bytes(),b'keep')

    def test_vm_commands_keep_source_fd_readonly_snapshot_and_exact_disk_identity(self):
        for scope,fds in [('agents-vm',[3,4]),('codex-vm',[3,4,5])]:
            command=engine.vm_command('/nix/store/synthetic/bin/qemu-system-x86_64',scope,fds)
            self.assertEqual(command[command.index('-nic')+1],'none')
            self.assertEqual(command[command.index('-smp')+1],'1')
            self.assertEqual(command[command.index('-m')+1],'2048')
            self.assertNotIn('-netdev',command)
            self.assertNotIn('-qmp',command)
            self.assertEqual(sum('snapshot=on' in part for part in command),len(fds)-1)
            self.assertIn('on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',command)
        codex=engine.vm_command('qemu','codex-vm',[3,4,5])
        self.assertIn('virtio-blk-pci,drive=codexroot,bootindex=1',codex)
        self.assertIn('virtio-blk-pci,drive=codexwork,serial=hc-codex-work',codex)

    def test_boot_proof_requires_fresh_kernel_userspace_and_correct_serial_login(self):
        good=b'Linux version 6.8.0\r\nsystemd[1]: Started system\r\nagents login: '
        self.assertTrue(engine.boot_proven(good,'agents'))
        for bad in [b'{"status":"running"}',b'agents login: ',good.replace(b'Linux version ',b''),
                    good.replace(b'systemd[1]',b'other'),good.replace(b'agents login:',b'codex-worker login:'),
                    b'Linux version 6.8\nsystemd[1] emergency mode']:
            self.assertFalse(engine.boot_proven(bad,'agents'))

    def test_isolated_drive_layout_still_matches_owning_vm_sources(self):
        agents=(ROOT/'modules/nixos/agents-vm.nix').read_text()
        codex=(ROOT/'scripts/setup-codex-vm.py').read_text()
        for owner in [agents,codex]:
            self.assertIn('-machine q35,accel=kvm',owner)
            self.assertIn('-device virtio-rng-pci',owner)
        self.assertIn('file=${guestDisk},if=virtio,format=qcow2',agents)
        self.assertIn('file=${seedImage},if=virtio,format=raw,readonly=on',agents)
        self.assertIn('virtio-blk-pci,drive=codexroot,bootindex=1',codex)
        self.assertIn('virtio-blk-pci,drive=codexwork,serial=hc-codex-work',codex)

    def test_process_cleanup_targets_only_owned_handle_and_escalates_after_timeout(self):
        process=SimpleNamespace(poll=lambda:None,terminate=unittest.mock.Mock(),kill=unittest.mock.Mock(),
                                wait=unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired('owned',10),0]))
        engine.stop_owned(process)
        process.terminate.assert_called_once();process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count,2)

    def test_real_owned_child_cancellation_between_creation_and_handle_return_is_cleaned(self):
        actual_popen=subprocess.Popen;children=[]
        def interrupt_before_return(*args,**kwargs):
            child=actual_popen(*args,**kwargs);children.append(child)
            os.kill(os.getpid(),signal.SIGINT)
            return child
        try:
            with patch.object(engine.subprocess,'Popen',side_effect=interrupt_before_return),self.assertRaises(KeyboardInterrupt):
                with engine.owned_process([sys.executable,'-c','import time; time.sleep(60)'],
                                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL):
                    self.fail('pending cancellation must arrive before entering body')
            self.assertEqual(len(children),1)
            self.assertEqual(children[0].returncode,-signal.SIGTERM)
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.wait(timeout=5)

    def test_second_terminal_signal_is_deferred_until_bounded_cleanup_finishes(self):
        calls=[]
        def terminate():calls.append('terminate');os.kill(os.getpid(),signal.SIGINT)
        process=SimpleNamespace(poll=lambda:None,terminate=terminate,wait=lambda **kwargs:calls.append('wait'))
        with self.assertRaises(KeyboardInterrupt):
            with engine.defer_terminal_signals():engine.stop_owned(process)
        self.assertEqual(calls,['terminate','wait'])

    def test_child_drops_production_identity_and_isolates_network_before_uid(self):
        calls=[]
        with patch.object(engine.os,'unshare',lambda flag:calls.append('network'),create=True),patch.object(engine.os,'CLONE_NEWNET',123,create=True), \
                patch.object(engine,'bounded_output') as limits,patch.object(engine.resource,'setrlimit'), \
                patch.object(engine.os,'sched_setaffinity',lambda *args:None,create=True), \
                patch.object(engine.os,'sched_getaffinity',lambda *args:{0,1},create=True), \
                patch.object(engine.os,'setgroups',lambda values:calls.append(('groups',values))), \
                patch.object(engine.os,'setgid',lambda value:calls.append(('gid',value))), \
                patch.object(engine.os,'setuid',lambda value:calls.append(('uid',value))):
            engine.vm_child(65534,65534,302)
        self.assertEqual(calls,['network',('groups',[302]),('gid',65534),('uid',65534)])
        limits.assert_called_once_with(512*1024**2)

    def test_real_owned_child_can_grow_cow_beyond_log_limit_but_not_vm_file_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'synthetic-cow'
            code='''import errno,json,sys
with open(sys.argv[1],'wb') as f:
 f.truncate(20*1024**2)
 try: f.truncate(513*1024**2)
 except OSError as error:
  if error.errno!=errno.EFBIG:raise
  print(json.dumps({'normal_cow_write':True,'file_bound_enforced':True}))
 else:raise RuntimeError('file bound missing')
'''
            with engine.owned_process([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      preexec_fn=lambda:engine.bounded_output(engine.MAX_VM_FILE)) as process:
                output,errors=process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,errors.decode())
            self.assertEqual(json.loads(output),{'normal_cow_write':True,'file_bound_enforced':True})
            self.assertEqual(path.stat().st_size,20*1024**2)

    def test_serial_and_error_logs_each_keep_explicit_sixteen_mib_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);serial=root/'serial';error=root/'error';serial.touch();error.touch()
            engine.private_logs_bounded(serial,error)
            for path in [serial,error]:
                with path.open('wb') as stream:stream.truncate(16*1024**2+1)
                with self.assertRaisesRegex(engine.VerificationError,'private-log-size-bound'):engine.private_logs_bounded(serial,error)
                path.write_bytes(b'')
        self.assertEqual(engine.bounded_output.__defaults__,(16*1024**2,))

    def test_errors_expose_categories_without_sql_or_stderr(self):
        error=subprocess.CalledProcessError(1,['private'],output=b'private SQL',stderr=b'private values')
        self.assertEqual(engine.error_category(error),'engine-command-failed')
        self.assertEqual(engine.error_category(ValueError('private data')),'engine-verification-error')

    def fixture(self,root):
        scratch=root/'scratch';scratch.mkdir();target=scratch/'restored-application';target.mkdir()
        state=root/'state';state.mkdir()
        archive='homecompute-applications-20261028T000000.123456Z.tar'
        receipt={'snapshot':'a'*64,'archive':archive,'verification':{'gzip_dumps_checked':4,'qcow2_disks_checked':3,'sqlite_integrity':'ok'}}
        (state/'last-application-verification.json').write_text(json.dumps(receipt))
        seed=target/'state/agents-vm/seed.iso';seed.parent.mkdir(parents=True);seed.write_bytes(b'synthetic seed')
        manifest={'postgres':sorted(engine.PG_SCOPES),'vms':sorted(engine.VM_DISKS),'agents_seed':
                  {'archive_path':'state/agents-vm/seed.iso','sha256':hashlib.sha256(seed.read_bytes()).hexdigest()}}
        (target/'manifest.json').write_text(json.dumps(manifest))
        receipt.update(restore_directory=str(target),manifest_sha256=hashlib.sha256((target/'manifest.json').read_bytes()).hexdigest())
        (state/'last-application-verification.json').write_text(json.dumps(receipt))
        return scratch,target,state,archive

    def test_restore_inputs_require_matching_snapshot_receipt_seed_and_no_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch,target,state,archive=self.fixture(Path(temporary))
            with patch.object(engine,'SCRATCH',scratch),patch.object(engine,'STATE',state),patch.object(engine,'protected'):
                self.assertEqual(len(engine.restored_input(target,'a'*64,archive)),64)
                (target/'safe-link').symlink_to('manifest.json')
                self.assertEqual(len(engine.restored_input(target,'a'*64,archive)),64)
                with self.assertRaisesRegex(engine.VerificationError,'matching-archive'):
                    engine.restored_input(target,'b'*64,archive)
                (target/'link').symlink_to('/srv/state')
                with self.assertRaisesRegex(engine.VerificationError,'symlink'):
                    engine.restored_input(target,'a'*64,archive)
                (target/'link').unlink();(target/'state/agents-vm/seed.iso').write_bytes(b'changed')
                with self.assertRaisesRegex(engine.VerificationError,'seed-mismatch'):
                    engine.restored_input(target,'a'*64,archive)

    def test_different_old_restore_tree_cannot_borrow_latest_snapshot_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch,target,state,archive=self.fixture(Path(temporary));old=scratch/'old-application'
            shutil.copytree(target,old)
            with patch.object(engine,'SCRATCH',scratch),patch.object(engine,'STATE',state),patch.object(engine,'protected'), \
                    self.assertRaisesRegex(engine.VerificationError,'target-binding'):
                engine.restored_input(old,'a'*64,archive)

    def test_engine_inputs_never_follow_even_safe_relative_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'real').write_bytes(b'synthetic');(root/'link').symlink_to('real')
            with self.assertRaisesRegex(engine.VerificationError,'regular-restored'):
                engine.engine_input(root/'link',root)
            directory=root/'real-dir';directory.mkdir();(directory/'file').touch();(root/'linked-dir').symlink_to('real-dir')
            with self.assertRaisesRegex(engine.VerificationError,'symlinked-engine-input-parent'):
                engine.engine_input(root/'linked-dir/file',root)

    def test_malformed_create_output_still_cleans_unique_nonce_owned_container(self):
        item=engine.policy()['postgres']['control-plane']
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'databases').mkdir()
            with gzip.open(root/'databases/control-plane-postgres.sql.gz','wb') as stream:stream.write(b'-- synthetic')
            responses=[SimpleNamespace(stdout=json.dumps([{'Id':item['image_id']}]).encode()),SimpleNamespace(stdout=b'truncated id')]
            with patch.object(engine,'run',side_effect=responses),patch.object(engine,'remove_owned') as cleanup:
                with self.assertRaisesRegex(engine.VerificationError,'create-identity'):
                    engine.postgres_verify('control-plane',item,root,root)
            name,identifier,nonce=cleanup.call_args.args
            self.assertIsNone(identifier);self.assertEqual(name,'homecompute-restore-pg-'+nonce)

    def test_cleanup_failure_aborts_remaining_engines_and_never_fake_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch,target,state,archive=self.fixture(Path(temporary))
            with patch.object(engine,'SCRATCH',scratch),patch.object(engine,'restored_input',return_value='f'*64), \
                    patch.object(engine,'postgres_verify',side_effect=engine.CleanupError('owned-container-removal-failed')) as pg, \
                    patch.object(engine,'vm_verify') as vm:
                result,receipt=engine.verify(target,'a'*64,archive,'c'*40)
            self.assertEqual(pg.call_count,1);vm.assert_not_called()
            self.assertFalse(result['postgres_import_verified']);self.assertFalse(result['vm_boot_verified'])
            self.assertTrue(receipt.is_file())
            self.assertTrue(all(not row['verified'] for row in result['vms'].values()))

    def test_corrupt_gzip_never_starts_a_postgres_container(self):
        item=engine.policy()['postgres']['control-plane']
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'databases').mkdir();(root/'databases/control-plane-postgres.sql.gz').write_bytes(b'broken')
            with patch.object(engine,'run',return_value=SimpleNamespace(stdout=json.dumps([{'Id':item['image_id']}]).encode())) as run:
                with self.assertRaises(gzip.BadGzipFile):engine.postgres_verify('control-plane',item,root,root)
            self.assertEqual(run.call_count,1)


class RealSyntheticPostgresTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HOMECOMPUTE_SYNTHETIC_POSTGRES')=='1' and os.geteuid()!=0,
                         'explicit local synthetic PostgreSQL check; never connects to existing servers')
    def test_dumpall_clean_restores_original_role_with_unique_bootstrap_superuser(self):
        binary=Path(shutil.which('postgres') or '/missing').resolve().parent
        for name in ['postgres','initdb','psql','pg_dumpall']:
            if not (binary/name).is_file():self.skipTest('matching local PostgreSQL tools unavailable')
        with tempfile.TemporaryDirectory(prefix='hc-pg-') as temporary:
            root=Path(temporary);processes=[]
            def cluster(name,user):
                data=root/name;socket=root/(name+'-socket');socket.mkdir()
                subprocess.run([str(binary/'initdb'),'-D',str(data),'-U',user,'-A','trust','--no-locale'],capture_output=True,check=True)
                log=(root/(name+'.log')).open('wb')
                process=subprocess.Popen([str(binary/'postgres'),'-D',str(data),'-c','listen_addresses=',
                                          '-c','unix_socket_directories='+str(socket),'-c','max_connections=10'],stdout=log,stderr=log)
                processes.append((process,log))
                command=[str(binary/'psql'),'-X','-v','ON_ERROR_STOP=1','-h',str(socket),'-U',user,'-d','postgres']
                for attempt in range(100):
                    if subprocess.run(command+['-Atc','SELECT 1'],capture_output=True).returncode==0:return socket,command
                    if process.poll() is not None:self.fail('synthetic PostgreSQL failed to start')
                    time.sleep(.05)
                self.fail('synthetic PostgreSQL startup timeout')
            try:
                source,command=cluster('source','original_role')
                subprocess.run(command+['-c','CREATE TABLE fixture(id integer PRIMARY KEY); INSERT INTO fixture VALUES(1);'],capture_output=True,check=True)
                dump=subprocess.run([str(binary/'pg_dumpall'),'-h',str(source),'-U','original_role','--clean','--if-exists'],capture_output=True,check=True).stdout
                bootstrap='hc_restore_'+uuid.uuid4().hex
                target,command=cluster('target',bootstrap)
                # Connect to a unique DB because --clean recreates postgres.
                subprocess.run(command+['-c','CREATE DATABASE '+bootstrap],capture_output=True,check=True)
                command[command.index('-d')+1]=bootstrap
                subprocess.run(command,input=dump,capture_output=True,check=True)
                command[command.index('-d')+1]='postgres'
                output=subprocess.run(command+['-Atc','SELECT count(*) FROM fixture'],capture_output=True,check=True).stdout
                self.assertEqual(output.strip(),b'1')
            finally:
                for process,log in reversed(processes):engine.stop_owned(process);log.close()


if __name__ == '__main__':unittest.main()
