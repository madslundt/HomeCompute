"""Allowlisted public artifact preparation; never imports model packages."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/whisper-language-observer'


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    with path.open('rb') as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('Bounded JSON required')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def pins() -> tuple[dict, dict]:
    return read_json(SOURCE / 'model.json'), read_json(SOURCE / 'dependencies.json')


def catalog() -> list[dict]:
    model, lock = pins()
    roster = read_json(ROOT / 'config/gb10-model-roster.json')
    entry = roster['supporting_services']['language_observer_experiment']
    if (entry['model_id'] != model['repository'] or entry['revision'] != model['revision'] or
            entry['license_id'] != 'apache-2.0' or entry['activation'] != 'disabled' or
            entry['promotion_authorized'] is not False or entry['disposition'] != 'experimental-staging-only-unqualified'):
        raise ValueError('Source-owned staging inventory differs; activation is not authorized')
    base = 'https://huggingface.co/' + model['repository'] + '/resolve/' + model['revision'] + '/'
    items = [{'group': 'model', **row, 'url': base + row['name']} for row in model['files']]
    items.extend({'group': 'wheels', **row, 'name': row['filename']} for row in lock['wheels'])
    if sum(row['size'] for row in items) > model['download_cap_bytes']:
        raise ValueError('Public artifact lock exceeds the512MiB download cap')
    if sum(row['size'] for row in lock['wheels']) != lock['wheel_bytes']:
        raise ValueError('Wheel byte accounting differs')
    for row in items:
        url = urllib.parse.urlsplit(row['url'])
        expected_host = 'huggingface.co' if row['group'] == 'model' else 'files.pythonhosted.org'
        if (url.scheme != 'https' or url.netloc != expected_host or url.query or url.fragment or
                Path(row['name']).name != row['name'] or type(row['size']) is not int or row['size'] < 1):
            raise ValueError('Artifact URL, name or size differs from the allowlist')
    return items


def verify_file(path: Path, item: dict) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != item['size']:
            raise ValueError('Artifact type or size differs')
        digest = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        after = os.fstat(fd)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if identity(before) != identity(after) or digest.hexdigest() != item['sha256']:
            raise ValueError('Artifact changed or SHA256 differs')
    finally:
        os.close(fd)


def allowed_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and
            not parsed.password and (host in ('huggingface.co', 'files.pythonhosted.org') or
            host.endswith('.hf.co') or host.endswith('.huggingface.co')))


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('Unexpected artifact redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def write(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError('Symlink state is forbidden')
    fd, temporary = tempfile.mkstemp(prefix='.observer-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def create(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def verify(directory: Path) -> dict:
    items = catalog()
    for group in ('wheels', 'model'):
        root = directory / group
        expected = {row['name'] for row in items if row['group'] == group}
        if root.is_symlink() or {path.name for path in root.iterdir()} != expected:
            raise ValueError('Exact artifact directory allowlist required')
    for item in items:
        verify_file(directory / item['group'] / item['name'], item)
    return {'artifact_count': len(items), 'artifact_bytes': sum(x['size'] for x in items),
            'model_manifest_sha256': sha((SOURCE / 'model.json').read_bytes()),
            'dependencies_sha256': sha((SOURCE / 'dependencies.json').read_bytes())}


def fetch(directory: Path) -> dict:
    """Explicit authorized caller only. Failed attempts are preserved, never retried."""
    model, _ = pins()
    items = catalog()
    journal = directory / 'download.json'
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or read_json(journal).get('state') != 'complete':
            raise ValueError('Previous artifact attempt requires review; no retry')
        return verify(directory)
    parent = directory.parent.resolve(strict=True)
    required = sum(row['size'] for row in items)
    if shutil.disk_usage(parent).free < required + model['disk_reserve_bytes']:
        raise ValueError('Public artifact disk reserve is insufficient')
    directory.mkdir(mode=0o700)
    for group in ('model', 'wheels'):
        (directory / group).mkdir(mode=0o700)
    state = {'state': 'fetching', 'downloaded_bytes': 0, 'expected_bytes': required,
             'maximum_bytes': model['download_cap_bytes'], 'completed': []}
    write(journal, state)
    try:
        for item in items:
            target = directory / item['group'] / item['name']
            temporary = target.with_name(target.name + '.part')
            digest, count = hashlib.sha256(), 0
            request = urllib.request.Request(item['url'], headers={'User-Agent': 'HomeCompute-language-observer/1'})
            with urllib.request.build_opener(Redirect()).open(request, timeout=30) as response:
                if not allowed_url(response.geturl()):
                    raise ValueError('Unexpected artifact redirect')
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    while True:
                        remaining = model['download_cap_bytes'] - state['downloaded_bytes']
                        if remaining <= 0:
                            raise ValueError('Artifact cumulative hard byte cap reached')
                        chunk = response.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        count += len(chunk)
                        state['downloaded_bytes'] += len(chunk)
                        write(journal, state)
                        if count > item['size'] or state['downloaded_bytes'] > model['download_cap_bytes']:
                            raise ValueError('Artifact download exceeds its hard byte cap')
                        if shutil.disk_usage(directory).free < model['disk_reserve_bytes'] + len(chunk):
                            raise ValueError('Artifact disk reserve breached')
                        stream.write(chunk)
                        digest.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if count != item['size'] or digest.hexdigest() != item['sha256']:
                raise ValueError('Downloaded artifact does not match the frozen lock')
            os.chmod(temporary, 0o444)
            temporary.rename(target)
            state['completed'].append(item['name'])
            write(journal, state)
        result = verify(directory)
        for group in ('model', 'wheels'):
            os.chmod(directory / group, 0o555)
        state.update(state='complete', **result)
        write(journal, state)
        return result
    except BaseException as error:
        state.update(state='failed', failure_class=type(error).__name__)
        write(journal, state)
        raise
