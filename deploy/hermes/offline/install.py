"""Install one immutable offline extension into the selected Hermes profile."""

import fcntl
from fnmatch import fnmatchcase
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import time


MAX_ARCHIVE = 8 * 1024 * 1024
MAX_TREE = 16 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def real(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink in managed path')


def checked_archive(path, pin):
    real(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError('invalid archive size or type')
        data = handle.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or digest(data) != pin['sha256']:
        raise ValueError('pinned archive checksum mismatch')
    return data


def unpack(path, pin, destination):
    data = checked_archive(path, pin)
    subdir = PurePosixPath(pin['subdir']).parts
    seen = set()
    total = 0
    with tarfile.open(fileobj=BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            source = PurePosixPath(member.name)
            parts = source.parts
            if source.is_absolute() or '..' in parts:
                raise ValueError('unsafe archive path')
            if parts[1:1 + len(subdir)] != subdir:
                continue
            relative = parts[1 + len(subdir):]
            if pin.get('include') and relative and not any(fnmatchcase(relative[0], pattern)
                                                           for pattern in pin['include']):
                continue
            if member.isdir():
                continue
            if relative and relative[0] == pin.get('flatten'):
                relative = relative[1:]
            if not relative or not member.isfile() or relative in seen:
                raise ValueError('invalid or duplicate selected archive member')
            total += member.size
            if total > MAX_TREE or len(seen) >= 2000:
                raise ValueError('selected extension exceeds unpacking limits')
            seen.add(relative)
            output = destination.joinpath(*relative)
            output.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            with archive.extractfile(member) as original, output.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o750 if member.mode & 0o111 else 0o640)
                shutil.copyfileobj(original, stream)
    if not seen:
        raise ValueError('pinned extension directory was empty')


def tree(path):
    real(path)
    files = {}
    for current, directories, names in os.walk(path):
        root = Path(current)
        for name in directories + names:
            real(root / name)
        for name in names:
            file = root / name
            if not file.is_file():
                raise ValueError('unexpected file type in extension tree')
            if root.name == '__pycache__' and file.suffix == '.pyc':
                continue
            files[file.relative_to(path).as_posix()] = digest(file.read_bytes())
    return files


def approved_caution(result, pin):
    reviewed = pin.get('reviewed_caution')
    if not reviewed or result.verdict != 'caution':
        return False
    # Low-severity references to configuration files alone do not need admission.
    # Any new high/medium finding, or a dangerous verdict, requires a fresh review.
    findings = [[f.pattern_id, f.severity, f.file, f.line] for f in result.findings
                if f.severity != 'low']
    return sorted(findings) == sorted(reviewed)


def validate(source, name, pin):
    if pin['kind'] == 'skill':
        from tools.skills_guard import scan_skill, should_allow_install
        marker = source / 'SKILL.md'
        if not marker.is_file():
            raise ValueError('missing skill manifest')
        result = scan_skill(source, source=pin['repo'])
        decision, _ = should_allow_install(result)
        if decision is not True:
            raise ValueError('Hermes skill scanner did not approve the pinned skill')
        return
    from hermes_cli.plugins_cmd import _read_manifest, _scan_plugin_tree
    from hermes_cli.plugins_manifest import requires_hermes_error
    manifest = _read_manifest(source)
    if manifest.get('name') != name or requires_hermes_error(manifest):
        raise ValueError('incompatible plugin manifest')
    if any(manifest.get(key) for key in ('requires_env', 'capabilities', 'requires_plugins',
                                        'pip_dependencies', 'dependencies', 'node_dependencies')):
        raise ValueError('plugin needs separate dependency or capability setup')
    if not any((source / marker).is_file() for marker in ('plugin.yaml', '.hermes-plugin/plugin.yaml')):
        raise ValueError('expected a native single-directory plugin')
    verdict = _scan_plugin_tree(source, pin['repo'], force=False,
                                reviewed_pin=pin.get('catalog_pin', False),
                                scan_decision_cb=lambda result: approved_caution(result, pin))
    if verdict is None:
        raise ValueError('Hermes plugin scanning is required')


def config_with_plugin(original, name, pin):
    import yaml
    expected = yaml.safe_load(original)
    plugins = expected.setdefault('plugins', {})
    enabled = plugins.setdefault('enabled', [])
    if name in enabled or name in plugins.get('disabled', []):
        raise ValueError('plugin selection already exists outside this lifecycle')
    if provider := pin.get('memory_provider'):
        memory = expected.setdefault('memory', {})
        if memory.get('provider'):
            raise ValueError('existing memory provider requires explicit reconciliation')
        memory['provider'] = provider
    else:
        enabled.append(name)
    if engine := pin.get('context_engine'):
        context = expected.setdefault('context', {})
        if context.get('engine', 'compressor') not in ('compressor', ''):
            raise ValueError('existing context engine requires explicit reconciliation')
        context['engine'] = engine
    from omh_config import encode_config
    return encode_config(expected, yaml)


def check_selection(name, pin):
    from hermes_cli.config import load_config_readonly

    config = load_config_readonly()
    provider = pin.get('memory_provider')
    if not provider and name not in config['plugins']['enabled']:
        raise ValueError('managed plugin is no longer enabled')
    if provider and config['memory']['provider'] != provider:
        raise ValueError('managed memory provider is no longer selected')
    engine = pin.get('context_engine')
    if engine and config['context']['engine'] != engine:
        raise ValueError('managed context engine is no longer selected')


def write(path, data, mode):
    real(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def apply(home, source, name, pin, revision):
    directory = home / ('plugins' if pin['kind'] == 'plugin' else 'skills')
    target = directory / name
    config = home / 'config.yaml'
    state = home / '.homecompute-offline-extensions'
    receipt = state / (name + '.json')
    for path in (target, config, receipt):
        real(path)
    state.mkdir(mode=0o700, exist_ok=True)
    if state.stat().st_uid != os.getuid() or stat.S_IMODE(state.stat().st_mode) != 0o700:
        raise ValueError('private extension receipts directory required')
    lock_path = state / 'install.lock'
    real(lock_path)
    with lock_path.open('a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return publish(directory, target, config, receipt, source, name, pin, revision)


def publish(directory, target, config, receipt, source, name, pin, revision):
    files = tree(source)
    current = config.read_bytes() if pin['kind'] == 'plugin' else None
    if receipt.exists():
        record = json.loads(receipt.read_bytes())
        if (record['commit'] != pin['commit'] or not target.is_dir()
                or tree(target) != files or record['files'] != files):
            raise ValueError('installed extension differs from its receipt')
        if current is not None:
            check_selection(name, pin)
        return {'name': name, 'result': 'already-installed'}
    if target.exists():
        raise ValueError('existing unmanaged extension requires review')
    selected = config_with_plugin(current, name, pin) if current is not None else None
    backup = receipt.with_suffix('.config-before.yaml')
    if selected is not None:
        if backup.exists() and backup.read_bytes() != current:
            raise ValueError('private config preimage differs')
        write(backup, current, 0o600)
    directory.mkdir(mode=0o750, exist_ok=True)
    mode = stat.S_IMODE(config.stat().st_mode) if selected is not None else None
    with tempfile.TemporaryDirectory(prefix='.homecompute-', dir=directory) as work:
        payload = Path(work) / name
        shutil.copytree(source, payload)
        activated = False
        published = False
        try:
            if selected is not None and config.read_bytes() != current:
                raise ValueError('concurrent configuration change')
            payload.rename(target)
            published = True
            if selected is not None:
                write(config, selected, mode)
                activated = True
            record = {'schema_version': 1, 'revision': revision, 'commit': pin['commit'],
                      'name': name, 'files': files, 'config_before': digest(current) if current else None,
                      'config_after': digest(selected) if selected else None}
            write(receipt, json.dumps(record, sort_keys=True).encode(), 0o600)
        except Exception:
            if activated and config.read_bytes() == selected:
                write(config, current, mode)
            if published and tree(target) == files:
                target.rename(payload)
            raise
    return {'name': name, 'result': 'installed'}


def rollback(home, name, pin):
    directory = home / ('plugins' if pin['kind'] == 'plugin' else 'skills')
    target = directory / name
    state = home / '.homecompute-offline-extensions'
    receipt = state / (name + '.json')
    backup = receipt.with_suffix('.config-before.yaml')
    config = home / 'config.yaml'
    retained = state / (name + '-' + str(time.time_ns()) + '.retained')
    lock_path = state / 'install.lock'
    for path in (receipt, backup, target, config, retained, lock_path):
        real(path)
    with lock_path.open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads(receipt.read_bytes())
        if (record['commit'] != pin['commit'] or not target.is_dir()
                or tree(target) != record['files'] or retained.exists()):
            raise ValueError('unknown extension state requires review')
        if pin['kind'] == 'plugin':
            current, original = config.read_bytes(), backup.read_bytes()
            if digest(current) != record['config_after'] or digest(original) != record['config_before']:
                raise ValueError('config changed after install; reconcile before rollback')
            write(config, original, stat.S_IMODE(config.stat().st_mode))
        target.rename(retained)
        receipt.rename(retained.with_suffix('.rolled-back.json'))
    return {'name': name, 'result': 'rolled-back'}


def run(home, archive, name, pin, revision, operation):
    from hermes_constants import get_hermes_home

    if home != get_hermes_home():
        raise ValueError('unexpected Hermes profile home')
    if operation == 'rollback':
        return rollback(home, name, pin)
    with tempfile.TemporaryDirectory(prefix='homecompute-extension-') as stage:
        source = Path(stage).resolve() / name
        unpack(archive, pin, source)
        validate(source, name, pin)
        if operation == 'verify':
            directory = home / ('plugins' if pin['kind'] == 'plugin' else 'skills')
            if not (directory / name).is_dir() or tree(directory / name) != tree(source):
                raise ValueError('extension tree differs from pinned archive')
            if pin['kind'] == 'plugin':
                check_selection(name, pin)
            return {'name': name, 'result': 'verified'}
        return apply(home, source, name, pin, revision)
