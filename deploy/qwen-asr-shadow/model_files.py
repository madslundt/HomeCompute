"""Strict public checkpoint verification; no model, Torch or network imports."""
import hashlib
import os
import stat
from pathlib import Path
from typing import Any

class RuntimeLoadError(RuntimeError):
    pass

def _verify_file(directory: Path, expected: dict[str, Any]) -> None:
    name = expected['path']
    if Path(name).name != name:
        raise RuntimeLoadError('Invalid model artifact name')
    descriptor = None
    try:
        descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected['size']:
            raise RuntimeLoadError('Model artifact size or type mismatch')
        sha256 = hashlib.sha256()
        git_blob = hashlib.sha1(f'blob {before.st_size}\0'.encode())
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > expected['size']:
                raise RuntimeLoadError('Model artifact changed during verification')
            sha256.update(chunk)
            git_blob.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(directory / name, follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if total != expected['size'] or identity(before) != identity(after) or identity(after) != identity(path_after):
            raise RuntimeLoadError('Model artifact changed during verification')
        if expected.get('sha256', sha256.hexdigest()) != sha256.hexdigest():
            raise RuntimeLoadError('Model artifact SHA256 mismatch')
        if expected.get('git_blob_sha1', git_blob.hexdigest()) != git_blob.hexdigest():
            raise RuntimeLoadError('Model artifact Git identity mismatch')
    except OSError:
        raise RuntimeLoadError('Model artifact unavailable or unsafe') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_model(directory: Path, manifest: dict[str, Any]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeLoadError('Model directory unavailable or unsafe')
    expected = {entry['path'] for entry in manifest['files']}
    if {entry.name for entry in directory.iterdir()} != expected:
        raise RuntimeLoadError('Model directory differs from pinned allowlist')
    for entry in manifest['files']:
        _verify_file(directory, entry)
