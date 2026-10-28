#!/usr/bin/env python3
"""Targeted NAS staging, qualification gates and non-destructive rollback."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nas_setup', ROOT / 'scripts/setup-nas-recovery-backup.py')
setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)


class NASRecoverySetupTest(unittest.TestCase):
    def test_only_dedicated_smb_destination_and_account_are_allowed(self):
        value = json.loads((ROOT / 'config/synology-backup.json').read_bytes())
        for changes in [{'share':'immich-library'}, {'username':'immich-svc'}, {'restic_encryption_required':False}, {'protocol':'NFS'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                setup.policy(json.dumps({**value, **changes}).encode())

    def test_bundle_contains_only_fixed_recovery_dependencies(self):
        files, digest = setup.bundle(ROOT)
        self.assertEqual(len(digest), 64)
        self.assertIn('scripts/application-recovery.py', files)
        self.assertIn('scripts/provision-nas-backup-credentials.py', files)
        self.assertNotIn('deploy/control-plane/compose.yaml', files)

    def test_mount_unit_escape_and_private_smb_settings(self):
        self.assertEqual(setup.MOUNT_UNIT, 'mnt-homecompute' + chr(92) + 'x2dbackups')
        settings = setup.policy((ROOT / 'config/synology-backup.json').read_bytes())
        units = setup.definitions(settings, '/nix/store/synthetic/bin')
        mount = units[setup.ATTACHED / (setup.MOUNT_UNIT + '.mount')]
        for item in [b'//192.168.30.236/homecompute-backups', b'vers=3.1.1', b'file_mode=0600', b'dir_mode=0700', b'nosuid,nodev,noexec']:
            self.assertIn(item, mount)
        backup = units[setup.ATTACHED / (setup.PREFIX + 'backup.service')]
        self.assertIn(b'ConditionPathExists=', backup)
        self.assertIn(b'CAP_DAC_READ_SEARCH', backup)
        self.assertIn(b'ExecStopPost=', backup)
        self.assertIn(b' resume\n', backup)
        self.assertIn(b'RestrictAddressFamilies=AF_UNIX', backup)
        self.assertIn(b'ProtectHome=tmpfs\nBindReadOnlyPaths=/home/mads/projects/chessmate/config/certificates\n', backup)
        self.assertIn(b'ProtectHome=yes\n', units[setup.ATTACHED / (setup.PREFIX + 'check.service')])
        self.assertNotIn(b'BindReadOnlyPaths=', units[setup.ATTACHED / (setup.PREFIX + 'check.service')])
        self.assertIn(b'ExecCondition=', units[setup.GUARD])

    def test_parent_modes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / 'private'; parent.mkdir(mode=0o700)
            setup.directory(parent / 'child')
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)

    def test_wrong_hash_is_rejected_before_credential_reads(self):
        with patch.object(setup, 'credentials') as credentials:
            with self.assertRaisesRegex(ValueError, 'bundle hash'): setup.apply(ROOT, 'a'*40, 'b'*64)
            credentials.assert_not_called()

    def test_unpublished_checkout_is_rejected(self):
        with patch.object(setup, 'run') as run, self.assertRaisesRegex(ValueError, 'published release'):
            setup.published(ROOT, 'a'*40, {})
        run.assert_not_called()

    def test_foreign_mount_is_rejected(self):
        settings=setup.policy((ROOT/'config/synology-backup.json').read_bytes())
        value={'filesystems':[{'target':str(setup.MOUNT),'source':'//192.168.30.236/immich-library','fstype':'cifs'}]}
        with patch.object(setup,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(value),'')), self.assertRaisesRegex(ValueError,'foreign filesystem'):
            setup.compatible_mount(settings)

    def test_only_exact_declared_staging_or_mounted_stacks_are_admitted(self):
        settings=setup.policy((ROOT/'config/synology-backup.json').read_bytes())
        target=str(setup.MOUNT)
        underlay={'target':target,'source':'systemd-1','fstype':'autofs'}
        cifs={'target':target,'source':'//192.168.30.236/homecompute-backups','fstype':'cifs'}
        for entries in [[underlay],[cifs],[underlay,cifs]]:
            with self.subTest(entries=entries), patch.object(setup,'run',return_value=
                    subprocess.CompletedProcess([],0,json.dumps({'filesystems':entries}),'')):
                setup.compatible_mount(settings)
        for entries in [[],[cifs,underlay],[cifs,cifs],[underlay,underlay,cifs],
                        [{**underlay,'source':'foreign'},cifs],
                        [underlay,{**cifs,'source':'//192.168.30.236/immich-library'}],
                        [{**underlay,'target':'/'},cifs],
                        [{'target':'/','source':'/dev/nvme0','fstype':'ext4'}]]:
            with self.subTest(entries=entries), patch.object(setup,'run',return_value=
                    subprocess.CompletedProcess([],0,json.dumps({'filesystems':entries}),'')):
                with self.assertRaisesRegex(ValueError,'foreign filesystem'):setup.compatible_mount(settings)

    def test_initial_backup_requires_explicit_pause_acknowledgment(self):
        record = {'version':'/reviewed', 'qemu_bin':'/nix/store/synthetic/bin'}
        with patch.object(setup, 'run'), self.assertRaisesRegex(ValueError, 'acknowledge-pause'):
            setup.operation('backup-now', record, SimpleNamespace(acknowledge_pause=False))

    def test_busy_guard_refuses_unknown_or_failed_inspection(self):
        for result in [subprocess.CompletedProcess([],1,'','bus unavailable'),
                       subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=activating\n',''),
                       subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=unknown\n','')]:
            with self.subTest(result=result), patch.object(setup,'run',return_value=result), self.assertRaises(ValueError):setup.busy()

    def test_local_resume_is_possible_when_nas_is_offline(self):
        with patch.object(setup, 'run', return_value=subprocess.CompletedProcess([],0,'','')) as run:
            result = setup.operation('resume', {'version':'/reviewed','qemu_bin':'/nix/store/synthetic/bin'}, SimpleNamespace())
        self.assertEqual(result['state'], 'sources-resumed')
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0][-1],'resume')

    def test_schedule_cannot_be_enabled_from_a_mismatched_restore_receipt(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(setup,'STATE',Path(temporary)), patch.object(setup,'run'):
            root=Path(temporary)
            (root/'last-backup.json').write_text(json.dumps({'snapshot':'a'*64}))
            (root/'last-application-verification.json').write_text(json.dumps({'snapshot':'b'*64,'verification':{'sqlite_integrity':'ok','qcow2_disks_checked':3,'gzip_dumps_checked':4}}))
            with self.assertRaisesRegex(ValueError,'matching first application backup'):
                setup.operation('enable-schedule',{'version':'/reviewed','qemu_bin':'/nix/store/synthetic/bin'},SimpleNamespace())
            self.assertFalse((root/'backup-authorized').exists())

    def test_schedule_requires_all_four_postgres_dump_proofs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(setup,'STATE',Path(temporary)), patch.object(setup,'run') as run:
            root=Path(temporary)
            (root/'last-backup.json').write_text(json.dumps({'snapshot':'a'*64}))
            verification={'sqlite_integrity':'ok','qcow2_disks_checked':3,'gzip_dumps_checked':3}
            proof={'snapshot':'a'*64,'verification':verification}
            target=root/'last-application-verification.json';target.write_text(json.dumps(proof))
            record={'version':'/reviewed','qemu_bin':'/nix/store/synthetic/bin'}
            with self.assertRaisesRegex(ValueError,'matching first application backup'):
                setup.operation('enable-schedule',record,SimpleNamespace())
            self.assertFalse((root/'backup-authorized').exists())
            # An existing reviewed marker avoids interacting with host timer
            # timestamps; only mocked systemctl calls are permitted here.
            (root/'backup-authorized').write_text('synthetic reviewed marker')
            verification['gzip_dumps_checked']=4;target.write_text(json.dumps(proof))
            result=setup.operation('enable-schedule',record,SimpleNamespace())
            self.assertEqual(result['state'],'scheduled')
            self.assertFalse(result['postgres_import_verified'])
            self.assertFalse(result['vm_boot_verified'])
            run.assert_any_call(['systemctl','start',setup.PREFIX+'backup.timer',setup.PREFIX+'check.timer'])

    def staging(self, root: Path, *, fail_mount=False):
        paths={'DEST':root/'install','STATE':root/'state','ATTACHED':root/'attached','CONFIG':root/'runtime.json','LOCK':root/'deploy.lock',
               'GUARD':root/'attached/local.service.d/90-homecompute-nas-recovery.conf'}
        calls=[]
        def run(arguments, **kwargs):
            calls.append(arguments)
            if arguments[0]=='findmnt':return subprocess.CompletedProcess(arguments,1,'','')
            if arguments[:2]==['systemctl','is-active']:
                return subprocess.CompletedProcess(arguments,3,'inactive\n','')
            if arguments[:2]==['systemctl','show']:
                name=arguments[2]
                text='' if arguments[-1]=='--value' else ''
                candidate=paths['ATTACHED']/name
                if '-p' in arguments and arguments[arguments.index('-p')+1]=='FragmentPath' and candidate.exists():text=str(candidate)+'\n'
                if '-p' in arguments and arguments[arguments.index('-p')+1]=='LoadState':text='LoadState=not-found\nActiveState=inactive\n'
                return subprocess.CompletedProcess(arguments,0,text,'')
            if arguments[:2]==['systemctl','start'] and setup.MOUNT_UNIT+'.mount' in arguments and fail_mount:
                raise subprocess.CalledProcessError(1,arguments)
            return subprocess.CompletedProcess(arguments,0,'','')
        actual_directory=setup.directory
        def directory(path,mode=0o755):
            if str(path).startswith(str(root)): actual_directory(path,mode)
        stack=ExitStack()
        for name,path in paths.items():stack.enter_context(patch.object(setup,name,path))
        stack.enter_context(patch.object(setup,'published'))
        stack.enter_context(patch.object(setup,'credentials'))
        stack.enter_context(patch.object(setup,'qemu_directory',return_value='/nix/store/synthetic/bin'))
        stack.enter_context(patch.object(setup,'run',side_effect=run))
        stack.enter_context(patch.object(setup,'directory',side_effect=directory))
        return stack,paths,calls

    def test_apply_stages_mount_and_gated_timers_without_cold_backup_or_repo_init(self):
        with tempfile.TemporaryDirectory() as temporary:
            stack,paths,calls=self.staging(Path(temporary))
            with stack:
                _,digest=setup.bundle(ROOT); result=setup.apply(ROOT,'a'*40,digest)
                self.assertEqual(result['state'],'staged-first-backup-pending')
                self.assertFalse((paths['STATE']/'backup-authorized').exists())
                self.assertFalse(any(args[:2]==['systemctl','start'] and setup.PREFIX+'backup.service' in args for args in calls))
                self.assertFalse(any('init' in args for args in calls))
                self.assertTrue(all(path.is_symlink() for path in setup.links()))
                self.assertEqual(paths['STATE'].stat().st_mode&0o777,0o700)

    def test_failed_mount_restores_configuration_and_never_deletes_repository_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            stack,paths,calls=self.staging(Path(temporary),fail_mount=True)
            with stack:
                _,digest=setup.bundle(ROOT)
                with self.assertRaises(subprocess.CalledProcessError):setup.apply(ROOT,'a'*40,digest)
                self.assertFalse(paths['CONFIG'].exists())
                self.assertFalse((paths['DEST']/'current').exists())
                self.assertTrue(all(not path.exists() for path in setup.links()))
                self.assertFalse(any(args and args[0] in {'rm','restic'} for args in calls))


if __name__=='__main__':unittest.main()
