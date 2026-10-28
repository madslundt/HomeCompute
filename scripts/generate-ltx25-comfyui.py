#!/usr/bin/env python3
"""Run the prepared native LTX graph through existing drained ComfyUI admission."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

HERE = Path(__file__).resolve().parent
WORKFLOWS = HERE.parent / 'deploy/comfyui-control/workflows'
ORIGIN = 'https://models.home.arpa'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeError('Unexpected redirect from a fixed local service')


class Client:
    def __init__(self, controller_port: int = 8189, comfy_port: int = 8188):
        self.controller_port, self.comfy_port = controller_port, comfy_port
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path: str, body: dict | None = None, *, comfy: bool = False) -> dict:
        port = self.comfy_port if comfy else self.controller_port
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': 'application/json'}
        if not comfy:
            headers['Origin'] = ORIGIN
        request = Request(f'http://127.0.0.1:{port}{path}', data, headers)
        try:
            with self.opener.open(request, timeout=35) as response:
                raw = response.read(16 * 1024**2 + 1)
        except HTTPError as error:
            error.close()
            raise RuntimeError(f'Local service returned HTTP {error.code}') from None
        if len(raw) > 16 * 1024**2:
            raise RuntimeError('Unexpectedly large local service response')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError('Unexpected local service response')
        return value

    def status(self) -> dict:
        return self.request('/control/api/status')

    def idle(self) -> bool:
        queue = self.request('/queue', comfy=True)
        if any(not isinstance(queue.get(key), list) for key in ('queue_running', 'queue_pending')):
            raise RuntimeError('Cannot verify the ComfyUI queue')
        return not queue['queue_running'] and not queue['queue_pending']


def text_ready(state: dict) -> bool:
    return state.get('mode') == 'text' and state.get('text_ready') is True and not state.get('comfy_running')


def wait_video(client: Client, timeout: float, interval: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.status()
        if state.get('mode') == 'video' and state.get('comfy_ready') is True and not state.get('text_running'):
            return
        if state.get('mode') == 'error' or state.get('error') and text_ready(state):
            raise RuntimeError('Controller could not enter video mode')
        time.sleep(interval)
    raise RuntimeError('Timed out waiting for drained video readiness')


def restore_text(client: Client, timeout: float, interval: float) -> None:
    deadline, empty_observations = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        try:
            state = client.status()
            if text_ready(state):
                return
            if state.get('mode') in {'starting', 'stopping'}:
                time.sleep(interval)
                continue
            # Queue checks remain authoritative after history failures or lost submissions.
            idle = client.idle() if state.get('comfy_running') else True
        except (OSError, RuntimeError, ValueError):
            empty_observations = 0
            time.sleep(interval)
            continue
        empty_observations = empty_observations + 1 if idle else 0
        if empty_observations >= 2:
            try:
                client.request('/control/api/stop', {})
            except RuntimeError:
                empty_observations = 0  # The controller may have admitted another job meanwhile.
        time.sleep(interval)
    raise RuntimeError('Text restoration remains pending; active or unverifiable work was not interrupted')


def mp4_outputs(history: dict) -> list[dict]:
    result = []
    for output in history.get('outputs', {}).values():
        if not isinstance(output, dict):
            continue
        for kind in ('images', 'videos', 'gifs'):
            for item in output.get(kind, []) if isinstance(output.get(kind, []), list) else []:
                if not isinstance(item, dict):
                    continue
                filename, subfolder = item.get('filename'), item.get('subfolder', '')
                if (isinstance(filename, str) and filename.lower().endswith('.mp4')
                        and Path(filename).name == filename and isinstance(subfolder, str)
                        and not Path(subfolder).is_absolute() and '..' not in Path(subfolder).parts
                        and item.get('type', 'output') == 'output'):
                    result.append({'filename': filename, 'subfolder': subfolder, 'type': 'output',
                                   'host_path': str(Path('/srv/gb10-ai/qwen-image-edit-comfyui/output') / subfolder / filename)})
    return result


def save_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def generate(root: Path, client: Client, *, timeout: float = 21600, interval: float = 5,
             prompt: str | None = None, frames: int | None = None,
             width: int | None = None, height: int | None = None) -> dict:
    graph = json.loads((WORKFLOWS / 'ltx25-text-api.json').read_text())
    workflow = json.loads((WORKFLOWS / 'ltx25-text-nodes.json').read_text())
    changes = {}
    if prompt is not None:
        if not prompt.strip() or len(prompt) > 20000:
            raise ValueError('Prompt must contain between 1 and 20000 characters')
        changes['364'] = {'text': prompt}
    if frames is not None:
        if not 9 <= frames <= 241 or (frames - 1) % 8:
            raise ValueError('Frames must be 8n+1 between 9 and 241')
        changes['356'] = {'length': frames}
        changes['366'] = {'frames_number': frames}
    for field, value in (('width', width), ('height', height)):
        if value is not None:
            if not 128 <= value <= 1536 or value % 64:
                raise ValueError('Output dimensions must be multiples of 64 between 128 and 1536')
            changes.setdefault('356', {})[field] = value // 2
    for key, values in changes.items():
        graph[key]['inputs'].update(values)
        node = next(n for n in workflow['nodes'] if str(n['id']) == key)
        if key == '364':
            node['widgets_values'] = [graph[key]['inputs']['text']]
        elif key == '356':
            node['widgets_values'] = [graph[key]['inputs'][k] for k in ('width', 'height', 'length', 'batch_size')]
        else:
            node['widgets_values'] = [graph[key]['inputs'][k] for k in ('frames_number', 'frame_rate', 'batch_size')]
    initial = client.status()
    if initial.get('target_mode') not in {'text', 'image', 'video'}:
        raise RuntimeError('Unified controls not installed; install text/image/video controls before generation')
    if initial.get('mode') not in {'text', 'image', 'video'}:
        raise RuntimeError('Spark already has a service transition or error; no job was submitted')
    report = {'status': 'starting', 'started_at': time.time(), 'initial_mode': initial['mode'],
              'owns_video_mode': False, 'mode_switch_accepted': False, 'prompt_id': None, 'history': None,
              'mp4': [], 'restored_text': False, 'error': None, 'restoration_error': None}
    outputs = root / 'outputs'
    outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path = outputs / f'ltx25-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json'
    report['report_path'] = str(report_path)
    save_report(report_path, report)
    began = time.monotonic()
    try:
        if initial['mode'] != 'video':
            client.request('/control/api/start/video', {})
            report['mode_switch_accepted'] = report['owns_video_mode'] = True
            save_report(report_path, report)
        wait_video(client, timeout, interval)
        report['video_ready_seconds'] = round(time.monotonic() - began, 3)
        submitted = client.request('/prompt', {'prompt': graph, 'client_id': str(uuid.uuid4()),
                                             'extra_data': {'extra_pnginfo': {'workflow': workflow}}})
        prompt_id = submitted.get('prompt_id')
        if not isinstance(prompt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', prompt_id):
            raise RuntimeError('Submission returned no valid prompt ID; success cannot be confirmed')
        report['prompt_id'], report['status'] = prompt_id, 'submitted'
        save_report(report_path, report)
        print(f'LTX prompt submitted: {prompt_id}', flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = client.request(f'/history/{prompt_id}', comfy=True).get(prompt_id)
            if history:
                report['history'] = history
                status = history.get('status', {})
                messages = status.get('messages', [])
                if status.get('status_str') == 'error' or any(
                        isinstance(message, list) and message and message[0] in {'execution_error', 'execution_interrupted'}
                        for message in messages):
                    raise RuntimeError('ComfyUI node execution failed; inspect the private history report')
                if status.get('completed') is True and status.get('status_str') == 'success':
                    report['mp4'] = mp4_outputs(history)
                    if not report['mp4']:
                        raise RuntimeError('Completed history contains no MP4 output')
                    report['status'] = 'completed'
                    break
            time.sleep(interval)
        else:
            raise RuntimeError('Timed out waiting for this prompt history; success cannot be confirmed')
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
        report['status'], report['error'] = 'failed', str(error) or 'Generation interrupted'
    finally:
        report['generation_seconds'] = round(time.monotonic() - began, 3)
        if report['owns_video_mode'] and report['mode_switch_accepted']:
            try:
                restore_text(client, timeout, interval)
                report['restored_text'] = True
            except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
                report['restoration_error'] = str(error) or 'Restoration interrupted; service state is unconfirmed'
        report['finished_at'] = time.time()
        save_report(report_path, report)
        print(f'LTX result: {report["status"]}; report: {report_path}', flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/homecompute/ltx25')
    parser.add_argument('--prompt')
    parser.add_argument('--frames', type=int)
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--wait-seconds', type=int, default=21600)
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(args.root.expanduser()))
    try:
        if args.wait_seconds < 60:
            raise ValueError('Wait must allow at least 60 seconds')
        spec = importlib.util.spec_from_file_location('ltx25_prepare', HERE / 'prepare-ltx25-spark.py')
        prepare = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = prepare
        spec.loader.exec_module(prepare)
        prepare.mutation_guard(root)
        previous_umask = os.umask(0o077)
        try:
            with prepare.preparation_lock(root):
                report = generate(root, Client(), timeout=args.wait_seconds, prompt=args.prompt,
                                  frames=args.frames, width=args.width, height=args.height)
        finally:
            os.umask(previous_umask)
        return int(report['status'] != 'completed' or bool(report['restoration_error']))
    except (OSError, RuntimeError, ValueError) as error:
        print(f'LTX generation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
