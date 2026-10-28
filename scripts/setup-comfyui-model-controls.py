#!/usr/bin/env python3
"""Plan or apply the reviewed model-controls update as root on home-spark."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path('/opt/homecompute/comfyui-control')
BACKUPS = Path('/var/lib/homecompute-qwen-image/control-updates')
UNIT = 'homecompute-comfyui-control.service'
# The installer also talks to the old controller before publishing the new one.
ORIGIN = 'https://comfyui.home.arpa'
API = 'http://127.0.0.1:8189/control/api'
FILES = {name: f'scripts/{name}' for name in (
    'comfyui_lifecycle.py', 'comfyui_control_http.py', 'comfyui_progress.py', 'llm_admission.py')}
FILES['index.html'] = 'deploy/comfyui-control/index.html'
MODES = {'text', 'image', 'video'}
PHASES = {'waiting_workflows', 'draining_text', 'stopping_text', 'starting_comfyui',
          'waiting_comfyui', 'stopping_comfyui', 'starting_text', 'waiting_text'}
DRAIN_PHASES = PHASES - {'stopping_comfyui', 'starting_text', 'waiting_text'}


def progress(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
    print(f'[{stamp}] {message}', file=sys.stderr, flush=True)


def wait_snapshot(value: dict) -> str:
    """Log only fixed status fields, never request bodies or backend error text."""
    mode = value.get('mode')
    parts = ['mode=' + (mode if mode in MODES | {'starting', 'stopping', 'error'} else 'unknown')]
    transition = value.get('transition')
    if isinstance(transition, dict):
        phase = transition.get('phase')
        parts.append('phase=' + (phase if phase in PHASES else 'unknown'))
        groups = [('workflows', transition, 'workflows'), ('agent_turns', transition, 'agent_turns'),
                  ('text_running', transition.get('text_requests'), 'running'),
                  ('text_queued', transition.get('text_requests'), 'waiting')]
    else:
        groups = []
    groups += [('response_connections', value.get('llm'), 'active_requests'),
               ('media_running', value, 'queue_running'), ('media_queued', value, 'queue_pending')]
    for label, source, key in groups:
        count = source.get(key) if isinstance(source, dict) else None
        parts.append(f'{label}={count if type(count) is int and count >= 0 else "unknown"}')
    return '; '.join(parts)


def host_check(apply: bool) -> None:
    if platform.system() != 'Linux' or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('This updater is restricted to Linux home-spark')
    if apply and os.geteuid() != 0:
        raise ValueError('Apply requires root on home-spark; the default plan is read-only')


def root_owned(path: Path, info) -> None:
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f'Expected a root-owned, non-writable deployment path: {path}')


def safe(path: Path, *, directory=False, source=False) -> None:
    for ancestor in reversed(path.absolute().parents):
        if ancestor.is_symlink():
            raise ValueError(f'Symlink path refused: {ancestor}')
        if ancestor.exists() and not source:
            root_owned(ancestor, ancestor.stat())
    if path.is_symlink():
        raise ValueError(f'Symlink path refused: {path}')
    if path.exists():
        info = path.stat()
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError(f'Unexpected deployment path type: {path}')
        if not source:
            root_owned(path, info)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def installed() -> dict:
    safe(ROOT, directory=True)
    if not ROOT.is_dir():
        raise ValueError('The existing model-controls installation is required')
    result = {}
    for name in FILES:
        path = ROOT / name
        safe(path)
        result[name] = digest(path.read_bytes()) if path.exists() else None
    return result


def prepare(source: Path) -> tuple[dict, dict]:
    safe(source, directory=True, source=True)
    payload = {}
    for name, relative in FILES.items():
        path = source / relative
        safe(path, source=True)
        payload[name] = path.read_bytes()
        if name.endswith('.py'):
            compile(ast.parse(payload[name], filename=relative), relative, 'exec')
    baseline = installed()
    # The backup parent may intentionally be root-only. A non-root plan reads
    # only the public installed files; root validates the full backup path.
    if os.geteuid() == 0:
        safe(BACKUPS, directory=True)
    hashes = {name: digest(value) for name, value in payload.items()}
    fingerprint = digest(json.dumps({'source': hashes, 'installed': baseline}, sort_keys=True).encode())
    return {'plan_sha256': fingerprint, 'source_sha256': hashes, 'installed_sha256': baseline,
            'destination': str(ROOT), 'unit': UNIT, 'backup_root': str(BACKUPS),
            'preserved': ['/etc/homecompute/image-mode.json', 'model containers and configuration'],
            'actions': ['If text is active, drain safely into idle image mode using the existing controller',
                        'Back up and atomically publish the five reviewed files',
                        'Restart only the model-controls service and verify the three-mode contract',
                        'If originally in text mode, qualify idle image-to-video-to-image switching before restoring text',
                        'Request the original mode; the controller completes restoration asynchronously']}, payload


def api(path: str, *, post=False) -> dict:
    headers = {'Origin': ORIGIN, 'Content-Type': 'application/json'} if post else {}
    request = urllib.request.Request(API + path, data=b'{}' if post else None,
                                     headers=headers, method='POST' if post else 'GET')
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError('The controller returned an invalid status')
    return value.get('status', value)


def healthy(value: dict) -> None:
    mode = value.get('mode')
    if mode not in MODES or value.get('transition') or value.get('error'):
        raise ValueError('The controller must be healthy and in a stable mode')
    text, media = value.get('text_running'), value.get('comfy_running')
    if mode == 'text':
        if text is not True or media is not False or value.get('text_ready', text) is not True:
            raise ValueError('The text model must be healthy before updating')
    elif media is not True or text is not False or value.get('comfy_ready') is not True:
        raise ValueError('The media model must be healthy before updating')
    if media and any(type(value.get(key)) is not int or value[key] != 0
                     for key in ('queue_running', 'queue_pending')):
        raise ValueError('The media queue is busy or unknown; no update was performed')


def text_drain(value: dict) -> dict:
    transition = value.get('transition')
    if (value.get('mode') != 'starting' or value.get('error')
            or value.get('target_mode', 'image') != 'image' or not isinstance(transition, dict)
            or transition.get('action') != 'start' or transition.get('phase') not in DRAIN_PHASES
            or type(value.get('text_running')) is not bool or type(value.get('comfy_running')) is not bool):
        raise ValueError('Expected the approved text-to-image drain, not another service transition')
    phase = transition['phase']
    if ((phase in {'waiting_workflows', 'draining_text'} and not value['text_running'])
            or (phase in {'starting_comfyui', 'waiting_comfyui'} and value['text_running'])
            or (phase not in {'starting_comfyui', 'waiting_comfyui'} and value['comfy_running'])):
        raise ValueError('The text-drain phase disagrees with the running services')
    return transition


def active_drain(value: dict, transition: dict) -> bool:
    if transition['phase'] == 'waiting_workflows':
        counts = [transition.get('workflows'), transition.get('agent_turns')]
    elif transition['phase'] == 'draining_text':
        requests = transition.get('text_requests') or {}
        counts = [requests.get('running'), requests.get('waiting'), (value.get('llm') or {}).get('active_requests')]
    else:
        return False
    return any(type(count) is int and count > 0 for count in counts)


def wait_for(mode: str, *, upgraded=False, seconds=1800) -> dict:
    if seconds is None and (mode != 'image' or upgraded):
        raise ValueError('An extended wait is only permitted for the initial text-to-image drain')
    started = time.monotonic()
    deadline, last_message, last_logged = started + (seconds if seconds is not None else 1800), None, started
    limit = f'up to {seconds}s' if seconds is not None else 'active text work may finish; other waits are bounded to 1800s'
    progress(f'Waiting for healthy {mode} mode ({limit}).')
    while deadline is None or time.monotonic() < deadline:
        try:
            value = api('/status')
        except (OSError, ValueError):
            message = 'Status unavailable; retrying the controller.'
            now = time.monotonic()
            if seconds is None and deadline is None:
                deadline = now + 1800
            if message != last_message or now - last_logged >= 30:
                progress(f'{int(now - started)}s elapsed: {message}')
                last_message, last_logged = message, now
            time.sleep(2)
            continue
        message, now = wait_snapshot(value), time.monotonic()
        if message != last_message or now - last_logged >= 30:
            progress(f'{int(now - started)}s elapsed: {message}')
            last_message, last_logged = message, now
        if seconds is None and (value.get('mode') != mode or value.get('transition')):
            transition = text_drain(value)
            if active_drain(value, transition):
                deadline = None
            elif deadline is None:
                deadline = now + 1800
        if value.get('mode') == 'error':
            raise RuntimeError('The controller reported an error while waiting for readiness')
        if value.get('mode') == mode and not value.get('transition'):
            healthy(value)
            if not upgraded or (value.get('target_mode') in MODES and value.get('idle_timeout_seconds') == 1800):
                progress(f'Healthy {mode} mode confirmed after {int(now - started)}s.')
                return value
        time.sleep(2)
    raise TimeoutError(f'Timed out waiting for healthy {mode} mode')


def publish(path: Path, data: bytes, mode=0o644) -> None:
    safe(path.parent, directory=True)
    safe(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            os.fchown(handle.fileno(), 0, 0)
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def mkdir(path: Path) -> None:
    safe(path, directory=True)
    if path.exists():
        return
    mkdir(path.parent)
    path.mkdir(mode=0o755)
    os.chown(path, 0, 0)
    path.chmod(0o755)


def restart() -> None:
    subprocess.run(['/usr/bin/systemctl', 'restart', UNIT], check=True, timeout=60,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def restore(mode: str) -> dict:
    try:
        return api('/start/' + mode, post=True)
    except urllib.error.HTTPError as error:
        if error.code != 404 or mode == 'video':
            raise
        error.close()
        return api('/stop' if mode == 'text' else '/start', post=True)


def apply(plan: dict, payload: dict, *, resume_text_drain=False) -> dict:
    progress('Preflight: checking the controller, current model and media queue.')
    original = api('/status')
    if resume_text_drain and original.get('mode') == 'starting':
        text_drain(original)
        if original.get('text_running') is not True or original.get('comfy_running') is not False:
            raise ValueError('Resume requires the existing text drain before the media service starts')
    else:
        healthy(original)
        if resume_text_drain and original['mode'] != 'image':
            raise ValueError('Resume requires an approved text drain or already parked idle image mode')
    initial = 'text' if resume_text_drain else original['mode']
    parked, backup, originals = initial, None, {}
    changed = False
    directory_mode = stat.S_IMODE(ROOT.stat().st_mode)
    try:
        progress(f'Initial mode: {initial}.')
        if initial == 'text':
            if resume_text_drain:
                progress('Resuming the approved text drain; the original text mode will be restored.')
            else:
                progress('Draining LLM work safely into image mode; active workflows and responses finish first.')
                api('/start', post=True)
            parked = 'image'
            wait_for(parked, seconds=None)
        progress('Rechecking the empty media queue and reviewed installed-file hashes.')
        current = api('/status')
        healthy(current)
        if current['mode'] != parked or installed() != plan['installed_sha256']:
            raise ValueError('Deployment changed since the plan; review a fresh plan')
        for name in FILES:
            path = ROOT / name
            originals[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
        mkdir(BACKUPS)
        backup = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        mkdir(backup)
        progress(f'Backing up the current files and manifest to {backup}.')
        manifest = {**plan, 'initial_mode': initial,
                    'original_modes': {name: value[1] if value else None for name, value in originals.items()}}
        for name, value in originals.items():
            if value is not None:
                publish(backup / name, value[0])
        publish(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
        changed = True
        ROOT.chmod(0o755)
        progress('Publishing the five reviewed controls files atomically.')
        for name, data in payload.items():
            publish(ROOT / name, data)
        progress(f'Restarting {UNIT}.')
        restart()
        wait_for(parked, upgraded=True, seconds=120)
        qualification = {'performed': False, 'reason': 'Preserved the initial media mode'}
        if initial == 'text':
            progress('Qualifying image-to-video switching while the LLM is already paused.')
            api('/start/video', post=True)
            wait_for('video', upgraded=True, seconds=120)
            progress('Qualifying video-to-image switching.')
            api('/start/image', post=True)
            wait_for('image', upgraded=True, seconds=120)
            qualification = {'performed': True, 'image_to_video': 'passed', 'video_to_image': 'passed'}
        progress(f'Requesting restoration of the original {initial} mode.')
        accepted = restore(initial)
        if accepted.get('target_mode', accepted.get('mode')) != initial:
            raise ValueError('The controller did not accept restoration of the original mode')
        progress('Update complete. The controller will finish any remaining model startup asynchronously.')
        return {'backup': str(backup), 'qualification': qualification,
                'restore_requested': initial, 'status': accepted}
    except Exception as error:
        progress('Update failed; recovering the original files, service and model mode.')
        problems = []
        if changed:
            progress('Restoring the backed-up controls files.')
            for name, value in originals.items():
                try:
                    if value is None:
                        safe(ROOT / name)
                        (ROOT / name).unlink(missing_ok=True)
                    else:
                        publish(ROOT / name, value[0], value[1])
                except Exception as rollback_error:
                    problems.append(f'{name}: {rollback_error}')
            ROOT.chmod(directory_mode)
            try:
                progress(f'Restarting the original {UNIT}.')
                restart()
                wait_for(parked, seconds=120)
            except Exception as rollback_error:
                problems.append(f'service: {rollback_error}')
        try:
            progress(f'Requesting recovery of the original {initial} mode.')
            restore(initial)
        except Exception as restore_error:
            problems.append(f'original-mode restoration: {restore_error}')
        detail = '; '.join(problems) if problems else 'original files/service restored; original mode requested'
        raise RuntimeError(f'Update failed: {error}. Recovery: {detail}. Backup: {backup or "not yet created"}') from error


def run(source: Path, *, do_apply=False, expected_plan=None, resume_text_drain=False) -> dict:
    host_check(do_apply)
    plan, payload = prepare(source.absolute())
    plan['resume_text_drain'] = resume_text_drain
    if resume_text_drain:
        plan['actions'][0] = 'Resume the approved text-to-image drain or idle image mode; restore original text after qualification'
    if expected_plan and plan['plan_sha256'] != expected_plan:
        raise ValueError('The source or installed files differ from the reviewed plan')
    if not do_apply:
        return {'read_only': True, **plan}
    return apply(plan, payload, resume_text_drain=resume_text_drain)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Publish only after reviewing the read-only plan')
    parser.add_argument('--expected-plan', help='SHA256 from the reviewed plan; refuses changed source or installed files')
    parser.add_argument('--resume-text-drain', action='store_true',
                        help='Resume the existing approved text drain or parked idle image mode, then restore text')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.source, do_apply=args.apply, expected_plan=args.expected_plan,
                             resume_text_drain=args.resume_text_drain), indent=2))
    except Exception as failure:
        parser.exit(1, f'{failure}\n')
