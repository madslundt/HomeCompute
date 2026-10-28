#!/usr/bin/env python3
"""Prepare pinned, full BF16 Qwen Image 2.1 assets without touching GPU services."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = 'Comfy-Org/Qwen-Image-2.1'
REVISION = '0217efd1d9c9452e84f6448903d6e21bfa171ffd'
MODEL_ROOT = Path('/srv/gb10-ai/qwen-image-edit-comfyui/models')
RESERVE_BYTES = 10 * 1024**3
CHUNK_BYTES = 8 * 1024**2


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{self.name}?download=true'


ASSETS = (
    Asset('diffusion_models/qwen_image_2.1_bf16.safetensors', 14230280616,
          '89f4158d066cc33906a199fca85634f766892dd78f49b6698dabf187ac86c4bc'),
    Asset('text_encoders/qwen3vl_8b_bf16.safetensors', 17534334616,
          '68bdc82bc1b66851162ae656225e7e2068166b603db19bd5d5a3b90eb12669a9'),
    Asset('vae/qwen_image_2.1_vae_bf16.safetensors', 675509688,
          'bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9'),
)


class DownloadRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        if urlsplit(url).scheme != 'https':
            raise RuntimeError('Refusing an insecure model download redirect')
        redirected = super().redirect_request(request, response, code, message, headers, url)
        if urlsplit(url).hostname != urlsplit(request.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


def secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError('Refusing a symlinked model directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError('Model directories must be owned by the installer user and not writable by others')


def secure_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError('Refusing an unsafe existing model file')


def verify(path: Path, asset: Asset) -> None:
    secure_file(path)
    if not path.is_file() or path.stat().st_size != asset.size:
        raise RuntimeError(f'Model size verification failed: {asset.name}')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b''):
            digest.update(block)
    if digest.hexdigest() != asset.sha256:
        raise RuntimeError(f'Model SHA256 verification failed: {asset.name}')


def token_from(path: Path | None) -> str | None:
    if path is None:
        return None  # All pinned assets are public and ungated.
    secure_file(path)
    if path.stat().st_mode & 0o077:
        raise RuntimeError('Optional token must be readable only by its owner')
    token = path.read_text().strip()
    if not token or '\n' in token or '\r' in token:
        raise RuntimeError('Invalid optional Hugging Face token')
    return token


def download(root: Path, asset: Asset, *, token: str | None = None, opener=None, report=print) -> Path:
    destination = root / asset.name
    secure_directory(destination.parent)
    secure_file(destination)
    if destination.exists():
        verify(destination, asset)
        report(f'Already verified: {asset.name}', flush=True)
        return destination
    partial = destination.with_suffix(destination.suffix + '.part')
    secure_file(partial)
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > asset.size:
        raise RuntimeError(f'Partial model is larger than its pinned size: {asset.name}')
    if offset < asset.size:
        headers = {'User-Agent': 'HomeCompute-Qwen21-prepare/1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        opener = opener or build_opener(DownloadRedirect())
        report(f'Downloading: {asset.name} ({offset}/{asset.size} bytes)', flush=True)
        try:
            with opener.open(Request(asset.url, headers=headers), timeout=60) as response:
                if response.status == 206:
                    interval = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not interval or tuple(map(int, interval.groups())) != (offset, asset.size - 1, asset.size):
                        raise RuntimeError('Download returned an unexpected byte range')
                elif response.status == 200:
                    offset = 0  # A server ignoring Range must never be appended.
                else:
                    raise RuntimeError('Download returned an unexpected HTTP status')
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise RuntimeError('Download returned an unexpected encoding')
                written, last_report = offset, time.monotonic()
                with partial.open('ab' if offset else 'wb') as handle:
                    os.chmod(partial, 0o600)
                    while block := response.read(CHUNK_BYTES):
                        if written + len(block) > asset.size:
                            raise RuntimeError('Download exceeded its pinned file size')
                        handle.write(block)
                        written += len(block)
                        if time.monotonic() - last_report >= 10:
                            report(f'Downloading: {asset.name} ({written}/{asset.size} bytes)', flush=True)
                            last_report = time.monotonic()
                    handle.flush()
                    os.fsync(handle.fileno())
        except HTTPError as error:
            raise RuntimeError(f'Model download returned HTTP {error.code}; partial file retained for retry') from None
        except (OSError, URLError) as error:
            raise RuntimeError('Model download interrupted; partial file retained for retry') from None
    report(f'Verifying SHA256: {asset.name}', flush=True)
    verify(partial, asset)
    os.chmod(partial, 0o644)
    os.replace(partial, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    report(f'Ready: {asset.name}', flush=True)
    return destination


def prepare(root: Path, *, token: str | None = None) -> None:
    secure_directory(root)
    lock_path = root / '.qwen-image21-prepare.lock'
    secure_file(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        missing = 0
        for asset in ASSETS:
            destination = root / asset.name
            partial = destination.with_suffix(destination.suffix + '.part')
            secure_file(destination)
            secure_file(partial)
            if not destination.exists():
                missing += max(0, asset.size - (partial.stat().st_size if partial.exists() else 0))
        if missing + RESERVE_BYTES > shutil.disk_usage(root).free:
            raise RuntimeError('Insufficient disk space for pinned assets and 10 GiB reserve')
        for asset in ASSETS:
            download(root, asset, token=token)
    print('All Qwen Image 2.1 BF16 assets verified. Services and existing models were not changed.', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=MODEL_ROOT)
    parser.add_argument('--token-file', type=Path, help='Optional protected token; public assets need none')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--download', action='store_true', help='Download and verify assets; never starts GPU services')
    mode.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not args.download and not args.verify_only:
        print(json.dumps(dict(repository=REPOSITORY, revision=REVISION,
                              total_bytes=sum(asset.size for asset in ASSETS),
                              assets=[asdict(asset) for asset in ASSETS]), indent=2))
        return
    if os.geteuid() != 0:
        parser.error('Download and verification must run as root on the model volume')
    try:
        if args.verify_only:
            for asset in ASSETS:
                verify(args.models / asset.name, asset)
        else:
            prepare(args.models, token=token_from(args.token_file))
    except (OSError, RuntimeError) as error:
        parser.exit(1, f'Preparation failed: {error}\n')


if __name__ == '__main__':
    main()
