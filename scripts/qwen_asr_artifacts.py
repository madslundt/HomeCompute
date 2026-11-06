"""Pinned public model provisioning for the isolated Qwen ASR experiment."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request


def runtime(source: Path):
    spec = importlib.util.spec_from_file_location('shadow_model_verifier', source / 'model_files.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(directory: Path, source: Path, manifest: dict) -> dict:
    runtime(source).verify_model(directory, manifest)
    files = []
    for entry in manifest['files']:
        digest = hashlib.sha256()
        with (directory / entry['path']).open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        files.append({'path': entry['path'], 'size': entry['size'], 'sha256': digest.hexdigest()})
    return {'model_snapshot': manifest['model_snapshot'], 'files': files}


def provision(state: Path, source: Path, manifest: dict) -> dict:
    """Download only the eight pinned public artifacts; never use a user token."""
    destination = state / ('model-' + manifest['model_snapshot'])
    if destination.exists() or destination.is_symlink():
        return verify(destination, source, manifest)
    required = sum(item['size'] for item in manifest['files']) + 1024 ** 3
    if shutil.disk_usage(state).free < required:
        raise ValueError('Insufficient space for the pinned model')
    deadline = time.monotonic() + 1800
    staging = Path(tempfile.mkdtemp(prefix='model-staging-', dir=state))
    try:
        for entry in manifest['files']:
            name = entry['path']
            if Path(name).name != name:
                raise ValueError('Invalid model artifact name')
            url = ('https://huggingface.co/' + manifest['model_repository'] + '/resolve/'
                   + manifest['model_snapshot'] + '/' + name)
            request = urllib.request.Request(url, headers={'User-Agent': 'HomeCompute-shadow'})
            fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output, urllib.request.urlopen(request, timeout=60) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('Non-HTTPS model response')
                remaining = entry['size']
                while chunk := response.read(min(1024 * 1024, remaining + 1)):
                    if time.monotonic() > deadline:
                        raise ValueError("Model provisioning deadline exceeded")
                    from qwen_asr_guard import memory
                    memory()
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError('Oversize model artifact')
                    output.write(chunk)
                if remaining:
                    raise ValueError('Truncated model artifact')
                output.flush()
                os.fsync(output.fileno())
            os.chmod(staging / name, 0o444)
        result = verify(staging, source, manifest)
        os.chmod(staging, 0o555)
        staging.rename(destination)
        return result
    finally:
        if staging.exists():
            os.chmod(staging, 0o700)
            shutil.rmtree(staging)
