#!/usr/bin/env python3
"""Prepare pinned LTX-2.5 NVFP4 assets; never change GPU services or render video."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
from urllib.request import urlopen

SOURCE_URL = 'https://github.com/Lightricks/LTX-2.git'
SOURCE_REVISION = '9ec55f9f22798a3198d9c923856824821bc3317e'
SETUP_REVISION = '55b9939403ac68797e641eefb53156f833601977'
REPOSITORY = 'Lightricks/LTX-2.5'
MODEL_REVISION = '49b093a39c19169a8a81721dcc541e07e945232f'
INPUTS = {
    'uv.lock': '60b6667cb90d77f83f6f62db24bd16f01482cbda733171ec90c9a556959efa62',
    'ltx-kernels-sm121a.patch': '93875d9b46341e7442fae71f7ba491136eb3ef1e8c6173b3a59084f29ee52a5e',
}
SETUP_FILE = 'packages/ltx-kernels/setup.py'
MANIFEST = 'prepared-models.json'


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str


ASSETS = (
    Asset('diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors', 18721548408,
          '4b94231e734c1950f8f6826cb8bd8715d94be5b3e04f8256ee060c5bc3886c30'),
    Asset('text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors', 26263858182,
          'ef7243612fdae7a75cb4d5cee9433e81380675fb6c213bd98ae74a9cd16561d1'),
    Asset('vae/ltx-2.5-video-vae-bf16.safetensors', 1472223346,
          '847e14ca7f3355debca0cea4eaa24ac0fbcdf0061da054ac89ca638a869ddba3'),
    Asset('vae/ltx-2.5-audio-vae-bf16.safetensors', 364866540,
          'c52733d37f6a7fb7949c3dc0fb468c6cb2169e4d836983a73babb9f0d54837a5'),
    Asset('latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors', 995778752,
          'eb5a71fe4068ee87ccdb1c3aa635e547ca76bd2d30ae20ae889f2c325c0677e8'),
)


def safe_path(path: Path, boundary: Path) -> None:
    """Reject symlinks and objects writable by other users, including parents."""
    relative = path.relative_to(boundary)
    for candidate in [boundary, *(boundary / item for item in reversed(relative.parents)), path]:
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise RuntimeError(f'Unsafe installation path: {candidate}')


def mutation_guard(root: Path) -> None:
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' \
            or platform.node().split('.')[0] != 'home-spark' or os.geteuid() == 0:
        raise RuntimeError('Mutations require the nonroot installation user on Linux home-spark aarch64')
    home = Path.home().absolute()
    if root == home or not root.is_relative_to(home):
        raise RuntimeError('Installation root must be below the current user home')
    safe_path(root, home)


@contextmanager
def preparation_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.prepare.lock'
    safe_path(path, root)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def command(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
            input: str | None = None, visible: bool = False) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, input=input, text=True,
                            stdout=None if visible else subprocess.PIPE,
                            stderr=None if visible else subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f'{Path(argv[0]).name} operation failed (exit {result.returncode}); existing files retained')
    return result.stdout or ''


def expected_setup(original: str) -> str:
    return original.replace('_NVFP4_ARCHES = ["100a", "110a", "120a"]',
                            '_NVFP4_ARCHES = ["100a", "110a", "120a", "121a"]  # 121a: GB10 / DGX Spark (local patch)') \
        .replace('            sel.append("120a")\n',
                 '            sel.append("120a")\n        elif tok.startswith("12.1"):\n            sel.append("121a")\n')


def check_source(source: Path) -> tuple[str, bool]:
    safe_path(source, source.parent)
    if command(['git', 'rev-parse', 'HEAD'], cwd=source).strip() != SOURCE_REVISION:
        raise RuntimeError('Existing source has a different revision; refusing to reset it')
    changed = command(['git', 'diff', '--name-only', 'HEAD'], cwd=source).splitlines()
    if set(changed) - {SETUP_FILE}:
        raise RuntimeError('Existing source modifications will not be overwritten')
    original = command(['git', 'show', f'HEAD:{SETUP_FILE}'], cwd=source)
    safe_path(source / SETUP_FILE, source)
    current = (source / SETUP_FILE).read_text()
    if current not in (original, expected_setup(original)):
        raise RuntimeError('Existing kernel source modifications will not be overwritten')
    return original, current != original


def pinned_input(name: str) -> bytes:
    url = f'https://raw.githubusercontent.com/Hitheshkaranth/LTX-2.5_Video_DGX_Spark_Setup/{SETUP_REVISION}/patches/{name}'
    with urlopen(url, timeout=60) as response:
        data = response.read(2 * 1024**2)
    if hashlib.sha256(data).hexdigest() != INPUTS[name]:
        raise RuntimeError(f'Pinned preparation input failed SHA256 verification: {name}')
    return data


def install_runtime(root: Path) -> None:
    source = root / 'LTX-2'
    safe_path(source, root)
    if source.exists():
        check_source(source)  # Reject edits before downloading or changing anything.
    else:
        command(['git', 'clone', '--no-checkout', SOURCE_URL, str(source)], visible=True)
        command(['git', 'checkout', '--detach', SOURCE_REVISION], cwd=source, visible=True)
    original, patched = check_source(source)
    patch_data, lock_data = pinned_input('ltx-kernels-sm121a.patch'), pinned_input('uv.lock')
    lock = source / 'uv.lock'
    safe_path(lock, source)
    if lock.exists() and lock.read_bytes() != lock_data:
        raise RuntimeError('Existing uv.lock differs from the pin; refusing to overwrite it')
    if not patched:
        command(['git', 'apply', '--check', '-'], cwd=source, input=patch_data.decode())
        command(['git', 'apply', '-'], cwd=source, input=patch_data.decode())
    if (source / SETUP_FILE).read_text() != expected_setup(original):
        raise RuntimeError('Kernel patch did not produce the expected source')
    if not lock.exists():
        lock.write_bytes(lock_data)
    env = dict(os.environ, TORCH_CUDA_ARCH_LIST='12.1', MAX_JOBS='4', UV_PYTHON_PREFERENCE='only-managed')
    env.pop('CUDA_HOME', None)
    env.pop('CUDA_PATH', None)
    command(['uv', 'sync', '--locked', '--python', '3.13', '--extra', 'natten', '--group', 'dev',
             '--group', 'kernels'], cwd=source, env=env, visible=True)
    check_runtime(root)


def verify_asset(root: Path, asset: Asset) -> None:
    path = root / asset.name
    safe_path(path, root)
    if not path.is_file() or path.stat().st_size != asset.size:
        raise RuntimeError(f'Model size verification failed; file preserved: {asset.name}')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024**2), b''):
            digest.update(block)
    if digest.hexdigest() != asset.sha256:
        raise RuntimeError(f'Model SHA256 verification failed; file preserved: {asset.name}')


def verify_models(root: Path) -> None:
    for asset in ASSETS:
        print(f'Verifying: {asset.name}', flush=True)
        verify_asset(root / 'models/ltx-2.5', asset)


def download(root: Path) -> None:
    models = root / 'models/ltx-2.5'
    safe_path(models, root)
    manifest = root / MANIFEST
    safe_path(manifest, root)
    manifest.unlink(missing_ok=True)  # A failed retry must never leave a ready marker.
    missing = []
    for asset in ASSETS:
        path = models / asset.name
        safe_path(path, root)
        if path.exists():
            verify_asset(models, asset)  # Preserve an unexpected final checkpoint for review.
        else:
            missing.append(asset.name)
    if missing:
        needed = sum(a.size for a in ASSETS if a.name in missing)
        if shutil.disk_usage(root).free < needed + 10 * 1024**3:
            raise RuntimeError('Insufficient disk space for missing assets and 10 GiB reserve')
        python = root / 'LTX-2/.venv/bin/python'
        if not python.is_file():
            raise RuntimeError('Install the runtime before downloading models')
        print('Downloading pinned models using existing Hugging Face authentication; partial downloads are reusable.', flush=True)
        code = ('import json,sys\nfrom huggingface_hub import snapshot_download\n'
                'try:\n snapshot_download(repo_id=sys.argv[1],revision=sys.argv[2],'
                'local_dir=sys.argv[3],allow_patterns=json.loads(sys.argv[4]))\n'
                'except Exception:\n print("Hugging Face download unavailable; accept LTX-2.5 terms and authenticate "'
                '+"with hf auth login, then retry. Partial downloads retained.",file=sys.stderr);sys.exit(1)\n')
        command([str(python), '-c', code, REPOSITORY, MODEL_REVISION, str(models), json.dumps(missing)], visible=True)
    verify_models(root)
    temporary = root / (MANIFEST + '.tmp')
    safe_path(temporary, root)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600), 'w') as handle:
        handle.write(json.dumps({'state': 'verified', 'repository': REPOSITORY,
                                 'revision': MODEL_REVISION, 'assets': [asdict(a) for a in ASSETS]}, indent=2) + '\n')
    temporary.replace(manifest)


def check_runtime(root: Path, *, cuda_smoke: bool = False) -> None:
    code = ('import torch\nfrom ltx_kernels.nvfp4 import functional as f\n'
            'reason=f.unavailable_reason()\n'
            'if reason: raise SystemExit("NVFP4 unavailable: "+str(reason))\n'
            'print("NVFP4 kernels available")\n')
    if cuda_smoke:
        code += ('assert torch.cuda.get_device_capability()==(12,1)\n'
                 'x=torch.ones((128,64),device="cuda",dtype=torch.bfloat16)\n'
                 's=f.per_tensor_decode_scale(x)\np,b=f.quantize_nvfp4(x,s)\n'
                 'y=f.scaled_mm_nvfp4(p,p,s,s,b,b)\ntorch.cuda.synchronize()\n'
                 'assert y.shape==(128,128) and torch.isfinite(y).all().item()\n'
                 'assert (y.float()-64).abs().max().item()<1.0\n'
                 'print("GB10 NVFP4 quantize/GEMM smoke passed")\n')
    command([str(root / 'LTX-2/.venv/bin/python'), '-c', code], cwd=root / 'LTX-2', visible=True)


def plan(root: Path) -> dict:
    return {'root': str(root), 'source': {'url': SOURCE_URL, 'revision': SOURCE_REVISION},
            'setup_revision': SETUP_REVISION, 'inputs': INPUTS, 'repository': REPOSITORY,
            'model_revision': MODEL_REVISION, 'model_dir': str(root / 'models/ltx-2.5'),
            'assets': [asdict(a) for a in ASSETS], 'model_bytes': sum(a.size for a in ASSETS),
            'services_changed': False, 'video_generated': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan',
                        choices=('plan', 'install-runtime', 'download', 'verify-only', 'check-runtime'))
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/homecompute/ltx25')
    parser.add_argument('--cuda-smoke', action='store_true', help='Also execute a small CUDA computation')
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(args.root.expanduser()))
    try:
        if args.action == 'plan':
            print(json.dumps(plan(root), indent=2))
        elif args.action == 'verify-only':
            verify_models(root)
        elif args.action == 'check-runtime':
            check_runtime(root, cuda_smoke=args.cuda_smoke)
        else:
            previous_umask = os.umask(0o077)
            try:
                mutation_guard(root)
                with preparation_lock(root):
                    (install_runtime if args.action == 'install-runtime' else download)(root)
            finally:
                os.umask(previous_umask)
    except (OSError, RuntimeError) as error:
        print(f'LTX preparation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
