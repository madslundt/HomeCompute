#!/usr/bin/env python3
"""Stage verified LTX-2.5 artifacts for stopped native ComfyUI; never switch services."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('ltx25_prepare', HERE / 'prepare-ltx25-spark.py')
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)
IMAGE = 'sha256:22ee54634e1445e0c2a449bd3b72216c007f39787d97b6191ca4ad8a5af040f7'
COMFY = 'homecompute-qwen-image-edit-comfyui'
DESTINATION = Path('/srv/gb10-ai/qwen-image-edit-comfyui/models')
WORKFLOWS = Path('/srv/gb10-ai/qwen-image-edit-comfyui/user/default/workflows')
WORKFLOW = HERE.parent / 'deploy/comfyui-control/workflows/ltx25-text-nodes.json'
WORKFLOW_NAME = 'LTX-2.5-Text-to-Video.json'
RESERVE = 10 * 1024**3


def no_symlinks(path: Path, boundary: Path) -> None:
    relative = path.relative_to(boundary)
    for candidate in [boundary, *(boundary / part for part in reversed(relative.parents)), path]:
        if candidate.is_symlink():
            raise RuntimeError(f'Refusing symlinked artifact path: {candidate}')
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise RuntimeError(f'Refusing nonregular artifact path: {path}')


def verify(path: Path, asset: dict) -> None:
    if not path.is_file() or path.stat().st_size != asset['size']:
        raise RuntimeError(f'Artifact size verification failed; existing file preserved: {asset["name"]}')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024**2), b''):
            digest.update(block)
    if digest.hexdigest() != asset['sha256']:
        raise RuntimeError(f'Artifact SHA256 verification failed; existing file preserved: {asset["name"]}')


def publish(source: Path, destination: Path, asset: dict, uid: int, gid: int) -> None:
    no_symlinks(source, source.parent)
    no_symlinks(destination, destination.parent)
    if destination.exists():
        verify(destination, asset)
    else:
        # New component directories must be traversable by the capability-free ComfyUI process.
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{destination.name}.ltx25-', dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'wb') as handle, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, handle, 8 * 1024**2)
                handle.flush()
                os.fsync(handle.fileno())
            verify(temporary, asset)
            os.chmod(temporary, 0o644)
            # Hard-link publication refuses any file that appeared after the preflight.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    if destination.stat().st_mode & 0o777 != 0o644:
        # Normalize permissions before restoring the runtime user's ownership.
        os.chown(destination, os.geteuid(), os.getegid())
        os.chmod(destination, 0o644)
    os.chown(destination, uid, gid)


def publish_bundle(source: Path, destination: Path, workflows: Path, assets: list[dict],
                   workflow: str, uid: int, gid: int) -> None:
    payload = workflow.encode()
    workflow_asset = {'name': 'LTX-2.5-Text-to-Video.json', 'size': len(payload),
                      'sha256': hashlib.sha256(payload).hexdigest()}
    workflow_path = workflows / workflow_asset['name']
    # Validate every existing final before publishing anything or changing ownership.
    for asset in assets:
        incoming, final = source / asset['name'], destination / asset['name']
        no_symlinks(incoming, source)
        no_symlinks(final, destination)
        verify(incoming, asset)
        if final.exists():
            verify(final, asset)
    no_symlinks(workflow_path, workflows)
    if workflow_path.exists():
        verify(workflow_path, workflow_asset)
    for asset in assets:
        print(f'Staging: {asset["name"]}', flush=True)
        parent = (destination / asset['name']).parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o755)
            os.chmod(parent, 0o755)
        publish(source / asset['name'], destination / asset['name'], asset, uid, gid)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.ltx25-workflow-', dir=workflows)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
        publish(temporary, workflow_path, workflow_asset, uid, gid)
    finally:
        temporary.unlink(missing_ok=True)


def deduplicate(source: Path, destination: Path, assets: list[dict]) -> None:
    # Preflight the entire bundle so an invalid final never replaces a source.
    for asset in assets:
        incoming, final = source / asset['name'], destination / asset['name']
        no_symlinks(incoming, source)
        no_symlinks(final, destination)
        verify(incoming, asset)
        verify(final, asset)
        if final.stat().st_uid != os.geteuid() or incoming.stat().st_uid != os.geteuid():
            raise RuntimeError('Shared model files must belong to the invoking user')
        if incoming.stat().st_dev != final.stat().st_dev:
            raise RuntimeError('Shared model files must be on the same filesystem; source preserved')
        shared = incoming.with_name(incoming.name + '.shared')
        no_symlinks(shared, source)
        if shared.exists():
            raise RuntimeError('Existing shared temporary path preserved; inspect it before retrying')
    for asset in assets:
        incoming, final = source / asset['name'], destination / asset['name']
        if os.path.samefile(incoming, final):
            continue
        shared = incoming.with_name(incoming.name + '.shared')
        try:
            os.link(final, shared)
            os.replace(shared, incoming)
        finally:
            shared.unlink(missing_ok=True)


def docker(*args: str, input: str | None = None, visible: bool = False) -> str:
    result = subprocess.run(['docker', *args], input=input, text=True, check=False,
                            stdout=None if visible else subprocess.PIPE,
                            stderr=None if visible else subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f'Scoped Docker artifact preparation failed (exit {result.returncode})')
    return result.stdout or ''


def ensure_stopped() -> None:
    if docker('inspect', '--format', '{{.State.Running}}', COMFY).strip() != 'false':
        raise RuntimeError('ComfyUI must already be stopped; no service was changed')


def stage(root: Path) -> None:
    prepare.mutation_guard(root)
    source = root / 'models/ltx-2.5'
    assets = [asdict(asset) for asset in prepare.ASSETS]
    ensure_stopped()
    prepare.verify_models(root)
    for directory in (DESTINATION, WORKFLOWS):
        no_symlinks(directory, Path('/srv'))
        if not directory.is_dir():
            raise RuntimeError('Expected existing ComfyUI artifact directories are missing')
    if source.stat().st_dev != DESTINATION.stat().st_dev:
        raise RuntimeError('Model staging and deduplication require one filesystem')
    missing = 0
    for asset in assets:
        final = DESTINATION / asset['name']
        no_symlinks(final, DESTINATION)
        if final.exists():
            verify(final, asset)
        else:
            missing += asset['size']
    if shutil.disk_usage(DESTINATION).free < missing + RESERVE:
        raise RuntimeError('Insufficient disk space for staged copies and 10 GiB reserve')
    workflow = WORKFLOW.read_text()
    json.loads(workflow)
    if docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).strip() != IMAGE:
        raise RuntimeError('Pinned local ComfyUI image is unavailable')
    code = ('import hashlib,json,os,shutil,tempfile\nfrom pathlib import Path\n'
            + '\n'.join(inspect.getsource(function) for function in (no_symlinks, verify, publish, publish_bundle))
            + '\np=json.load(__import__("sys").stdin)\nos.umask(0o077)\n'
            + 'publish_bundle(Path("/source"),Path("/destination"),Path("/workflows"),'
            + 'p["assets"],p["workflow"],p["uid"],p["gid"])\n')
    ensure_stopped()
    docker('run', '--rm', '--pull', 'never', '-i', '--network', 'none', '--read-only',
           '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE',
           '--cap-add', 'FOWNER',
           '--security-opt', 'no-new-privileges',
           '--mount', f'type=bind,src={source},dst=/source,readonly',
           '--mount', f'type=bind,src={DESTINATION},dst=/destination',
           '--mount', f'type=bind,src={WORKFLOWS},dst=/workflows',
           '--entrypoint', 'python3', IMAGE, '-c', code,
           input=json.dumps({'assets': assets, 'workflow': workflow, 'uid': os.geteuid(), 'gid': os.getegid()}),
           visible=True)
    ensure_stopped()
    deduplicate(source, DESTINATION, assets)
    print('Verified native ComfyUI models staged; both runtimes share the same model inodes.', flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'stage'), default='plan', nargs='?')
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/homecompute/ltx25')
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(args.root.expanduser()))
    try:
        if args.action == 'plan':
            print(json.dumps({'source': str(root / 'models/ltx-2.5'), 'destination': str(DESTINATION),
                              'workflows': str(WORKFLOWS), 'image': IMAGE,
                              'services_changed': False, 'artifacts': [asdict(a) for a in prepare.ASSETS]}, indent=2))
        else:
            previous_umask = os.umask(0o077)
            try:
                prepare.mutation_guard(root)
                with prepare.preparation_lock(root):
                    stage(root)
            finally:
                os.umask(previous_umask)
    except (OSError, RuntimeError, ValueError) as error:
        print(f'LTX ComfyUI staging failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
