#!/usr/bin/env python3
"""Build the pinned pure-Python OMH wheel with an offline pinned backend."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def fetch(pin, path):
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(pin['url'], timeout=90) as response, temporary.open('xb') as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                if out.tell() > 180 * 1024 * 1024:
                    raise ValueError('artifact too large')
        if digest(temporary) != pin['sha256']:
            raise ValueError('artifact checksum mismatch')
        temporary.rename(path)
    if path.is_symlink() or digest(path) != pin['sha256']:
        raise ValueError('cached artifact differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    cache = args.cache.absolute()
    if any(p.is_symlink() for p in (cache, *cache.parents)):
        raise ValueError('real build cache path required')
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.stat().st_uid != os.getuid() or cache.stat().st_mode & 0o077:
        raise ValueError('private owned cache required')
    pin = json.loads((ROOT / 'config/hermes-omh.json').read_text())
    source = cache / 'oh-my-hermes.tar.gz'
    backend = cache / 'setuptools-84.1.0-py3-none-any.whl'
    fetch(pin['archive'], source); fetch(pin['build_backend'], backend)
    with tarfile.open(source) as archive:
        members = [m for m in archive.getmembers() if m.name.count('/') == 1 and m.name.endswith('/install.sh')]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('one root installer required')
        installer = archive.extractfile(members[0]).read()
    if hashlib.sha256(installer).hexdigest() != pin['installer_sha256']:
        raise ValueError('installer differs from source pin')
    target = cache / 'install.sh'
    if target.is_symlink():
        raise ValueError('real installer asset required')
    target.write_bytes(installer); target.chmod(0o600)
    env_dir = cache / 'build-env'
    if not env_dir.exists():
        subprocess.run([sys.executable, '-I', '-m', 'venv', str(env_dir)], check=True)
    python = env_dir / 'bin/python'
    env = {**os.environ, 'PIP_NO_INDEX': '1', 'PIP_FIND_LINKS': str(cache),
           'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'SOURCE_DATE_EPOCH': str(pin['source_date_epoch'])}
    out = cache / 'wheels'; out.mkdir(exist_ok=True)
    log = cache / 'build.log'
    with log.open('w') as stream:
        result = subprocess.run([str(python), '-I', '-m', 'pip', 'wheel', '--no-deps',
                                 '--no-index', '--find-links', str(cache), '--wheel-dir', str(out), str(source)],
                                env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('wheel build failed; inspect ' + str(log))
    wheels = list(out.glob('oh_my_hermes-*.whl'))
    if len(wheels) != 1:
        raise ValueError('one OMH wheel required')
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        commit = archive.read('omh/_archive_commit.txt').decode().strip()
        if commit != pin['commit']:
            raise ValueError('wheel source provenance differs')
    if digest(wheel) != pin['wheel']['sha256']:
        raise ValueError('wheel differs from the reviewed reproducible artifact')
    print(json.dumps({'wheel': str(wheel), 'sha256': digest(wheel), 'commit': commit}))


if __name__ == '__main__':
    main()
