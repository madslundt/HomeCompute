#!/usr/bin/env python3
import hashlib
import importlib.util
import io
import json
import sqlite3
import stat
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('assistant_nas_restore',
    Path(__file__).parents[1] / 'scripts/restore-assistant-context-nas.py')
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

SNAP = 'a' * 64
ARCHIVE = 'homecompute-applications-20261106T120000.123456Z.tar'
DBS = [f'assistant-context/state/{name}.sqlite3' for name in
       ('context', 'attention', 'coverage', 'delivery', 'retrieval', 'routines')]
MANIFEST = {'schema_version': 1, 'assistant_context': {
    'directories': ['assistant-context/collector', 'assistant-context/state'], 'files': [
        'assistant-context/clients.json', 'assistant-context/embedding-credential.json',
        'assistant-context/briefing-credential.json'],
    'sqlite_databases': DBS}}


def archive_bytes(*, unsafe=None, large_vm=0):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:') as archive:
        def add(name, data=b'', kind=tarfile.REGTYPE, mode=0o600):
            if kind == tarfile.DIRTYPE: mode = 0o700
            info = tarfile.TarInfo(name); info.type = kind; info.mode = mode; info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if info.isfile() else None)
        add('manifest.json', json.dumps(MANIFEST, sort_keys=True).encode())
        add('state/', kind=tarfile.DIRTYPE)
        add('state/assistant-context/', kind=tarfile.DIRTYPE)
        add('state/assistant-context/state/', kind=tarfile.DIRTYPE)
        add('state/assistant-context/collector/', kind=tarfile.DIRTYPE)
        add('state/assistant-context/clients.json', b'{"clients":[]}')
        # JSON-named embedding/briefing credentials are opaque bearer bytes.
        add('state/assistant-context/embedding-credential.json', b'opaque-embedding-token')
        add('state/assistant-context/briefing-credential.json', b'opaque-briefing-token')
        add('state/assistant-context/collector/context-sync.json', b'{"schema_version":1}')
        add('state/assistant-context/collector/source-token', b'opaque-source-token')
        add('state/assistant-context/collector/collector-token', b'opaque-collector-token')
        for name in DBS:
            db = sqlite3.connect(':memory:')
            db.execute('create table example(value text)')
            data = io.BytesIO()
            # SQLite serialization avoids filesystem access and creates a valid DB.
            raw = sqlite3.connect(':memory:')
            raw.deserialize(db.serialize()); raw.close(); db.close()
            # A tiny initialized SQLite database can be built in a temporary file below.
            with tempfile.NamedTemporaryFile() as handle:
                conn = sqlite3.connect(handle.name); conn.execute('create table example(value text)'); conn.commit(); conn.close()
                data.write(Path(handle.name).read_bytes())
            add('state/' + name, data.getvalue())
        if large_vm:
            add('state/agents-vm/large.img', b'V' * large_vm)
        if unsafe:
            add(unsafe, b'x', kind=tarfile.SYMTYPE)
    return target.getvalue()


class TrackingBytesIO(io.BytesIO):
    def __init__(self, value, vm_offset, vm_size):
        super().__init__(value); self.vm_offset = vm_offset; self.vm_end = vm_offset + vm_size
        self.vm_bytes_read = 0

    def read(self, size=-1):
        start = self.tell(); result = super().read(size); end = self.tell()
        self.vm_bytes_read += max(0, min(end, self.vm_end) - max(start, self.vm_offset))
        return result


class AssistantNasRestoreTests(unittest.TestCase):
    def test_plan_cli_is_local_read_only_and_emits_bundle_hash_and_file_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            for name in m.FILES:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('value = 1\n' if name.endswith('.py') else 'fixture\n')
            expected_hash = m.source_bundle_hash(source)
            output = io.StringIO()
            with patch.object(sys, 'argv', ['restore-assistant-context-nas.py', 'plan', '--source', str(source)]), \
                 patch.object(m.subprocess, 'Popen', side_effect=AssertionError('plan must not start a process')), \
                 patch.object(m.subprocess, 'check_output', side_effect=AssertionError('plan must not contact NAS')), \
                 redirect_stdout(output):
                m.main()
            result = json.loads(output.getvalue())
            self.assertEqual(result['source_bundle_sha256'], expected_hash)
            self.assertEqual(result['files'], list(m.FILES))
            self.assertFalse(result['applied'])

    def test_published_guard_checks_exact_bundle_and_git_bytes_with_scoped_safe_directory(self):
        revision = 'c' * 40
        with tempfile.TemporaryDirectory() as temporary:
            releases = Path(temporary) / 'releases'; release = releases / revision; release.mkdir(parents=True)
            for name in m.FILES:
                path = release / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('value = 1\n' if name.endswith('.py') else 'fixture\n')
            digest = hashlib.sha256()
            for name in m.FILES:
                content = (release / name).read_bytes()
                digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
            real_lstat = Path.lstat
            def lstat(path):
                if path == release:
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
                return real_lstat(path)
            git_calls = []
            def git_run(command, **kwargs):
                git_calls.append(command)
                if command[-2:] == ['rev-parse', 'HEAD']:
                    stdout = revision.encode()
                elif command[-4:] == ['merge-base', '--is-ancestor', revision, 'origin/master']:
                    stdout = b''
                elif command[-2] == 'show':
                    stdout = (release / command[-1].split(':', 1)[1]).read_bytes()
                else:
                    raise AssertionError('unexpected Git command')
                return SimpleNamespace(stdout=stdout)
            with patch.object(m, 'RELEASES_ROOT', releases), patch.object(Path, 'lstat', lstat), \
                 patch.object(m.subprocess, 'run', side_effect=git_run):
                m.published(release, revision, digest.hexdigest())
                self.assertTrue(all('-c' in command and any(arg.startswith('safe.directory=') for arg in command)
                                    for command in git_calls))
                with self.assertRaises(m.Refusal):
                    m.published(release, revision, '0' * 64)
                (release / m.FILES[0]).write_text('value = 2\n')
                with self.assertRaises(m.Refusal):
                    m.published(release, revision, digest.hexdigest())

    def test_tar_index_skips_unrelated_vm_payload_bytes(self):
        raw = archive_bytes(large_vm=4 * 1024 * 1024)
        # Determine the payload's byte interval from the generated tar headers.
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            vm = next(item for item in archive if item.name == 'state/agents-vm/large.img')
            offset = vm.offset_data
        tracked = TrackingBytesIO(raw, offset, 4 * 1024 * 1024)
        with tarfile.open(fileobj=tracked, mode='r:') as archive:
            _manifest, selected, _hash = m.inspect_archive(archive)
        # CPython tarfile may probe the final byte while seeking to the next
        # header; it must never read the VM payload body.
        self.assertLessEqual(tracked.vm_bytes_read, 1)
        self.assertLess(tracked.vm_bytes_read, (4 * 1024 * 1024) // 1_000_000)
        self.assertFalse(any('agents-vm' in member.name for member in selected))

    def test_extracts_only_declared_context_and_leaves_pending_fence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root / 'snapshot.tar'; archive.write_bytes(archive_bytes())
            target = root / 'target'
            receipt = m.extract_assistant(archive, target, 'b' * 40, SNAP, ARCHIVE)
            fence = target / 'state/assistant-context/state/restore-fence.json'
            self.assertEqual(json.loads(fence.read_bytes())['phase'], 'pending')
            self.assertEqual(receipt['verification_scope'], 'assistant-context-only')
            self.assertFalse(receipt['full_application_verified'])
            self.assertEqual(receipt['sqlite_database_count'], 6)
            self.assertEqual(receipt['assistant_file_count'], len(MANIFEST['assistant_context']['files']))
            self.assertEqual(receipt['member_sha256']['state/assistant-context/briefing-credential.json'],
                             hashlib.sha256(b'opaque-briefing-token').hexdigest())
            self.assertEqual(receipt['manifest_sha256'], hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest())
            self.assertFalse((target / 'state/agents-vm').exists())

    def test_unknown_and_unsafe_paths_are_refused(self):
        for unsafe in ('state/assistant-context/state/../escape', 'state/assistant-context/state/link'):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'bad.tar'; path.write_bytes(archive_bytes(unsafe=unsafe))
                with self.assertRaises(m.Refusal), tarfile.open(path, 'r:') as archive:
                    m.inspect_archive(archive)
        for snapshot, archive_name in (('a' * 8, ARCHIVE), (SNAP, 'latest.tar')):
            with self.assertRaises(m.Refusal): m.extract_assistant(Path('unused'), Path('unused-target'), 'b' * 40,
                                                                    snapshot, archive_name)
        with self.assertRaises(m.Refusal):
            m.exact_snapshot(SNAP, [{'id': 'b' * 64, 'paths': ['/srv/backups/agent-recovery'],
                                    'tags': ['agent-recovery-archives']}])
        with self.assertRaises(m.Refusal):
            m.exact_snapshot(SNAP, [{'id': SNAP, 'paths': ['/srv/backups/unrelated'],
                                     'tags': ['agent-recovery-archives']}])
        self.assertEqual(m.exact_snapshot(SNAP, [{'id': SNAP, 'paths': ['/srv/backups/agent-recovery'],
                                                  'tags': ['agent-recovery-archives']}])['id'], SNAP)

    def test_assistant_data_rejects_executable_and_special_permissions(self):
        for name, mode in (
            ('state/assistant-context/clients.json', 0o4700),
            ('state/assistant-context/clients.json', 0o700),
            ('state/assistant-context/state', 0o2700),
            ('state/assistant-context/state', 0o1700),
        ):
            raw = archive_bytes()
            changed = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as source, \
                    tarfile.open(fileobj=changed, mode='w:') as target:
                for member in source:
                    if member.name.rstrip('/') == name:
                        member.mode = mode
                    target.addfile(member, source.extractfile(member) if member.isfile() else None)
            with self.subTest(name=name, mode=mode), \
                    tarfile.open(fileobj=io.BytesIO(changed.getvalue()), mode='r:') as archive:
                with self.assertRaises(m.Refusal):
                    m.inspect_archive(archive)

    def test_unrelated_archive_symlink_is_skipped(self):
        raw = archive_bytes(unsafe='state/unrelated-app/managed-link')
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            _manifest, selected, _hash = m.inspect_archive(archive)
        self.assertFalse(any(item.name == 'state/unrelated-app/managed-link' for item in selected))

    def test_mount_lifecycle_uses_full_id_and_settles_before_removing_mountpoint(self):
        class Child:
            def __init__(self, command): self.args = command; self.done = False; self.waited = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def wait(self, timeout=None): self.waited = True; self.done = True; return 0
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); mounted = {'value': None}; child = {}
            def popen(command, **_kwargs):
                process = Child(command); child['process'] = process
                mounted['value'] = {'target': command[-1], 'source': 'restic', 'fstype': 'fuse.restic', 'id': '731'}
                archive = Path(command[-1]) / 'ids' / SNAP / 'srv/backups/agent-recovery' / ARCHIVE
                archive.parent.mkdir(parents=True); archive.write_bytes(b'fixture')
                return process
            with patch.object(m, 'MOUNT_PARENT', parent):
                archive, process, identity = m._mount_archive({}, SNAP, ARCHIVE, parent / 'target', popen=popen,
                    find_mount=lambda _path: mounted['value'], sleep=lambda _delay: None)
                mountpoint = archive.parents[5]
                self.assertEqual(archive.name, ARCHIVE)
                self.assertIn('ids/%I', process.args)
                self.assertNotIn('--allow-other', process.args)
                self.assertEqual(identity['id'], '731')
                def unmount(*_args, **_kwargs):
                    mounted['value'] = None
                    # A real unmount removes the FUSE view; model that without
                    # granting the helper permission to recursively erase paths.
                    for path in sorted(mountpoint.rglob('*'), key=lambda item: len(item.parts), reverse=True):
                        if path.is_dir(): path.rmdir()
                        else: path.unlink()
                    return type('Result', (), {'returncode': 0})()
                with patch.object(m.subprocess, 'run', side_effect=unmount):
                    m._cleanup_mount(mountpoint, process, expected_identity=identity,
                                     find_mount=lambda _path: mounted['value'])
                self.assertTrue(process.waited)
                self.assertFalse(mountpoint.exists())

    def test_cleanup_failure_never_reports_success(self):
        class Child:
            def __init__(self):
                self.args = ['restic', 'mount', '/tmp/private-mount']
                self.terminated = False; self.waited = False
            def poll(self): return None
            def terminate(self): self.terminated = True
            def wait(self, timeout=None): self.waited = True; return 0
        child = Child()
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary) / 'mount'; mount.mkdir()
            output = Path(temporary) / 'receipt.json'
            fenced = Path(temporary) / 'target/state/assistant-context/state/restore-fence.json'
            fenced.parent.mkdir(parents=True); fenced.write_text('{"phase":"pending"}')
            identity = {'target': str(mount), 'source': 'restic', 'fstype': 'fuse.restic', 'id': '2'}
            with self.assertRaises(m.Refusal):
                m._cleanup_mount(mount, child, expected_identity=identity, unmount=['false'],
                                 find_mount=lambda _path: identity)
            with patch.object(m.subprocess, 'run') as run:
                with self.assertRaises(m.Refusal):
                    m._cleanup_mount(mount, child, expected_identity=identity,
                        find_mount=lambda _path: {**identity, 'id': '3'})
                run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertTrue(mount.exists())
            self.assertEqual(json.loads(fenced.read_text())['phase'], 'pending')


if __name__ == '__main__': unittest.main()
