#!/usr/bin/env python3
"""Exclusive text/image/video profiles with drained intake and a shared media idle lease."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from statistics import median
import threading
import time
from llm_admission import IntakeGate, TextConfig, WorkflowActivity, make_proxy
from comfyui_progress import Transition, text_requests
from comfyui_control_http import ControlHTTPServer, ORIGINS, PAGE, handler, prompt_mode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

COMFY = 'homecompute-qwen-image-edit-comfyui'
STATE = Path('/var/lib/homecompute-qwen-image/controller.json')
IDLE_TIMEOUT = 1800


class Host:
    """Only fixed containers and fixed loopback HTTP resources are reachable."""
    def __init__(self, config: TextConfig, activity: WorkflowActivity | None = None):
        self.config, self.activity = config, activity

    def workflow_activity(self) -> dict:
        if self.activity is None:
            raise RuntimeError('Workflow activity protection is not configured')
        return self.activity.get()

    @staticmethod
    def docker(*args: str) -> str:
        return subprocess.run(['/usr/bin/docker', *args], check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180).stdout.strip()

    def running(self, name: str) -> bool:
        return self.docker('inspect', '--format', '{{.State.Running}}', name) == 'true'

    def start(self, name: str) -> None:
        self.docker('start', name)

    def stop(self, name: str) -> None:
        signal = ['--signal', 'SIGINT'] if name == COMFY else []
        self.docker('stop', *signal, '--time', '120', name)
        if self.running(name):
            raise RuntimeError('Container did not stop')

    @staticmethod
    def get(port: int, path: str, *, raw: bool = False):
        with urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise RuntimeError('Unexpectedly large local response')
        return body.decode() if raw else json.loads(body)

    def ready(self, name: str) -> bool:
        try:
            self.get(8188 if name == COMFY else self.config.port,
                     '/system_stats' if name == COMFY else self.config.health_path, raw=True)
            return True
        except (OSError, ValueError):
            return False

    def queue(self) -> tuple[int, int]:
        queue = self.get(8188, '/queue')
        if not isinstance(queue, dict) or any(not isinstance(queue.get(k), list)
                                              for k in ('queue_running', 'queue_pending')):
            raise RuntimeError('ComfyUI queue is unavailable')
        return len(queue['queue_running']), len(queue['queue_pending'])

    def history(self) -> str:
        history = self.get(8188, '/history?max_items=1')
        if not isinstance(history, dict):
            raise RuntimeError('ComfyUI history is unavailable')
        return hashlib.sha256(json.dumps(sorted(history)).encode()).hexdigest()

    def text_requests(self) -> dict | None:
        try:
            return text_requests(self.get(self.config.port, '/metrics', raw=True))
        except (OSError, ValueError):
            return None

    @staticmethod
    def prompt(path: str, body: bytes) -> tuple[int, str, bytes]:
        request = Request(f'http://127.0.0.1:8188{path}', body,
                          {'Content-Type': 'application/json'}, method='POST')
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            result = response.read(8 * 1024 * 1024 + 1)
            if len(result) > 8 * 1024 * 1024:
                raise RuntimeError('Unexpectedly large prompt response')
            return response.status, response.headers.get('Content-Type', 'application/json'), result


class Controller:
    def __init__(self, host: Host, state: Path = STATE, clock=time.time, sleep=time.sleep):
        self.host, self.path, self.clock, self.sleep = host, state, clock, sleep
        self.text, self.gate = host.config, IntakeGate()
        self.guard, self.admission = threading.RLock(), threading.RLock()
        self.worker = None
        self.progress = Transition(clock)
        self.llm_available = False
        self.last_activity, self.last_history = clock(), None
        self.media_mode = 'image'
        self.startup_seconds = {mode: [] for mode in ('text', 'image', 'video')}
        self.data = dict(mode='text', target_mode='text', text_running=False, text_ready=False,
                         text_display_name=self.text.display_name, image_display_name='Qwen Image 2.1',
                         video_display_name='LTX-2.5', comfy_running=False,
                         comfy_ready=False, queue_running=None, queue_pending=None,
                         idle_seconds=0, idle_timeout_seconds=IDLE_TIMEOUT, error=None)
        try:
            saved = json.loads(state.read_text())
            if not isinstance(saved, dict):
                raise ValueError('Invalid saved state')
            activity = saved.get('last_activity')
            if type(activity) in (int, float) and math.isfinite(activity):
                self.last_activity = min(activity, clock())
            self.last_history = saved.get('last_history')
            if saved.get('media_mode') in {'image', 'video'}:
                self.media_mode = saved['media_mode']
            timings = saved.get('startup_seconds', {})
            if isinstance(timings, dict):
                for mode in self.startup_seconds:
                    values = timings.get(mode, [])
                    if isinstance(values, list):
                        self.startup_seconds[mode] = [v for v in values if type(v) in (int, float)
                            and math.isfinite(v) and 0 <= v <= 86400][-5:]
        except (OSError, ValueError, TypeError):
            pass

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as handle:
            os.chmod(temporary, 0o600)
            json.dump(dict(last_activity=self.last_activity, last_history=self.last_history,
                           media_mode=self.media_mode, startup_seconds=self.startup_seconds), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def status(self) -> dict:
        with self.guard:
            result = dict(self.data)
            result['llm'] = self.llm_status()
            result['transition'] = self.progress.snapshot()
            result['startup_timings'] = {mode: {
                'seconds': round(median(values), 3) if values else None, 'samples': len(values),
                'min_seconds': min(values) if values else None, 'max_seconds': max(values) if values else None
            } for mode, values in self.startup_seconds.items()}
            result['idle_seconds'] = max(0, int(self.clock() - self.last_activity)) if result['comfy_running'] else 0
            return result

    def record_startup(self, mode: str, seconds: float) -> None:
        if math.isfinite(seconds) and 0 <= seconds <= 86400:
            with self.guard:
                self.startup_seconds[mode] = (self.startup_seconds[mode] + [round(seconds, 3)])[-5:]
                self.persist()

    def llm_status(self) -> dict:
        with self.guard:
            result = self.gate.status()
            result['available'] = self.llm_available and result['available']
            if not self.llm_available:
                mode = self.data['mode']
                result['state'] = ('error' if mode == 'error' else 'offline' if mode in {'image', 'video'} else
                                   'draining' if mode == 'starting' and self.data['text_running'] else 'starting')
            return result

    def advertise_text(self, available: bool) -> None:
        with self.guard:
            self.llm_available = available

    def update(self, **values) -> None:
        with self.guard:
            self.data.update(values)
            if values.get('mode') in {'text', 'image', 'video', 'error'}:
                self.data['target_mode'] = values.get('target_mode', values['mode'] if values['mode'] != 'error'
                                                    else self.data['target_mode'])
                self.progress.clear()

    def sample(self) -> bool:
        text, comfy = self.host.running(self.text.container), self.host.running(COMFY)
        ready = comfy and self.host.ready(COMFY)
        running = pending = None
        history = None
        if ready:
            try:
                running, pending = self.host.queue()
                history = self.host.history()
            except (OSError, ValueError, RuntimeError):
                pass
        if ready and history is not None:
            if running or pending or history != self.last_history:
                self.last_activity = self.clock()
                self.last_history = history
                self.persist()
        self.update(text_running=text, text_ready=text and self.host.ready(self.text.container),
                    comfy_running=comfy, comfy_ready=ready,
                    queue_running=running, queue_pending=pending)
        return history is not None

    def recover(self) -> None:
        """Inspect actual containers; resume the idle lease, or restore missing text."""
        with self.admission:
            try:
                self.sample()
                state = self.status()
                if state['text_running'] and state['comfy_running']:
                    self.update(mode='error', error='Both services are running; operator intervention required')
                elif state['comfy_running']:
                    self.update(mode=self.media_mode)
                elif state['text_running'] and state['text_ready']:
                    self.update(mode='text')
                    self.gate.open()
                    self.advertise_text(True)
                else:
                    self.submit('stop')
            except Exception:
                self.update(mode='error', error='Could not inspect the configured services')

    def submit(self, action: str, target: str = 'image') -> dict:
        with self.guard:
            if action not in {'start', 'stop'} or target not in {'text', 'image', 'video'}:
                raise ValueError('Unknown action')
            if action == 'stop' or target == 'text':
                action, target = 'stop', 'text'
            if self.data['mode'] in {'starting', 'stopping'} or self.worker and self.worker.is_alive():
                raise ValueError('A service switch is already in progress')
            if action == 'start' and self.data['mode'] == target and self.data['comfy_ready']:
                return self.status()
            if self.data['comfy_running'] and (
                    self.data['queue_running'] is None or self.data['queue_pending'] is None or
                    self.data['queue_running'] or self.data['queue_pending']):
                raise ValueError('ComfyUI has active or queued work, or its queue cannot be checked')
            self.advertise_text(False)
            if action == 'stop':
                self.gate.close('starting')
            self.data.update(mode='starting' if action == 'start' else 'stopping', target_mode=target, error=None)
            self.progress.begin(action, self.data)
            self.worker = threading.Thread(target=self.switch, args=(action, target), daemon=True)
            self.worker.start()
            return self.status()

    def wait_ready(self, name: str, timeout: int | None = None) -> None:
        self.progress.phase('waiting_comfyui' if name == COMFY else 'waiting_text')
        timeout = timeout if timeout is not None else (600 if name == COMFY else 1800)
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            running = self.host.running(name)
            ready = running and self.host.ready(name)
            if name == COMFY:
                self.update(comfy_running=running, comfy_ready=ready)
            else:
                self.update(text_running=running, text_ready=ready)
            if ready:
                return
            self.sleep(5)
        raise RuntimeError(f'{"ComfyUI" if name == COMFY else self.text.display_name} did not become ready')

    def drain_workflows(self, timeout: float | None = None) -> None:
        self.progress.phase('waiting_workflows')
        deadline = None if timeout is None else self.clock() + timeout
        zero_since, first_observed = None, None
        while deadline is None or self.clock() < deadline:
            try:
                activity = self.host.workflow_activity()
                counts = [activity.get('active_workflows'), activity.get('active_turns')]
                observed = activity.get('observed_at')
                if (any(type(value) is not int or value < 0 for value in counts) or
                        type(observed) not in (int, float) or not math.isfinite(observed) or
                        not -5 <= self.clock() - observed <= 15):
                    raise ValueError('Invalid or stale activity')
            except Exception as error:
                raise RuntimeError('Cannot verify active workflows; text model was left running') from error
            self.progress.phase('waiting_workflows', workflows=counts[0], agent_turns=counts[1])
            if all(value == 0 for value in counts):
                if zero_since is None:
                    zero_since, first_observed = self.clock(), observed
                elif self.clock() - zero_since >= 5 and observed > first_observed:
                    self.gate.close('draining')
                    return
            else:
                zero_since = first_observed = None
            self.sleep(5)
        raise RuntimeError('Workflows did not finish; text model was left running')

    def drain_text(self, timeout: float | None = None) -> None:
        self.progress.phase('draining_text')
        deadline = None if timeout is None else self.clock() + timeout
        while deadline is None or self.clock() < deadline:
            # Request leases last through the complete SSE/JSON body. Metrics
            # additionally cover upstream aborts after a disconnected client.
            requests = self.host.text_requests()
            self.progress.phase('draining_text', text_requests=requests)
            if self.gate.status()['active_requests'] == 0 and requests is not None and not any(requests.values()):
                return
            self.sleep(2)
        raise RuntimeError('Text model did not become idle; it was left running')

    def to_text(self, began: float | None = None) -> None:
        began = self.clock() if began is None else began
        starting = not self.host.running(self.text.container)
        if self.host.running(COMFY):
            if any(self.host.queue()):
                raise RuntimeError('ComfyUI has active or queued work; it was left running')
            self.progress.phase('stopping_comfyui')
            self.host.stop(COMFY)
        self.update(comfy_running=False, comfy_ready=False, queue_running=None, queue_pending=None)
        if not self.host.running(self.text.container):
            self.progress.phase('starting_text')
            self.host.start(self.text.container)
        self.wait_ready(self.text.container)
        if starting:
            self.record_startup('text', self.clock() - began)
        self.update(mode='text', error=None)
        self.gate.open()
        self.advertise_text(True)

    def switch(self, action: str, target: str = 'image') -> None:
        with self.admission:
            try:
                if action == 'stop':
                    self.to_text()
                else:
                    # Inspect both containers before changing either process.
                    # Every operation after stopping the text model belongs to rollback.
                    media_was_running = self.host.running(COMFY)
                    fresh_media = not media_was_running
                    text_was_running = self.host.running(self.text.container)
                    if media_was_running and any(self.host.queue()):
                        raise RuntimeError('ComfyUI has active or queued work; it was left running')
                    if text_was_running:
                        self.drain_workflows()
                        self.drain_text()
                    began = self.clock()
                    try:
                        if media_was_running:
                            self.progress.phase('stopping_comfyui')
                            self.host.stop(COMFY)
                            fresh_media = True
                            self.update(comfy_running=False, comfy_ready=False)
                        if text_was_running:
                            self.progress.phase('stopping_text')
                            self.host.stop(self.text.container)
                            self.update(text_running=False, text_ready=False)
                        self.progress.phase('starting_comfyui')
                        self.host.start(COMFY)
                        self.wait_ready(COMFY)
                        startup_seconds = self.clock() - began
                        self.last_activity, self.last_history = self.clock(), self.host.history()
                        self.media_mode = target
                        self.record_startup(target, startup_seconds)
                        self.update(mode=target, error=None)
                    except Exception:
                        # Admissions stayed closed throughout startup: the fresh
                        # process has no accepted jobs, even if its HTTP server failed.
                        self.progress.reason(f'{target}_start_failed')
                        restoring_began = self.clock()
                        if self.host.running(COMFY):
                            if not fresh_media and any(self.host.queue()):
                                raise RuntimeError('Media activation failed with active work; it was left running')
                            self.progress.phase('stopping_comfyui')
                            self.host.stop(COMFY)
                        self.to_text(restoring_began)
                        raise RuntimeError('ComfyUI failed to start; the text model was restored')
            except Exception as error:
                message = str(error) if isinstance(error, RuntimeError) else 'Service switch failed; inspect the controller log'
                try:
                    self.sample()
                    mode = self.media_mode if self.data['comfy_running'] and not self.data['text_running'] else (
                        'text' if self.data['text_running'] and not self.data['comfy_running'] else 'error')
                except Exception:
                    mode = 'error'
                self.update(mode=mode, error=message)
            finally:
                try:
                    self.sample()
                except Exception:
                    self.update(error='Could not verify the service state', mode='error')
                state = self.status()
                if state['mode'] == 'text' and state['text_ready'] and not state['comfy_running']:
                    self.gate.open()
                    self.advertise_text(True)
                else:
                    self.gate.close(state['mode'])
                    self.advertise_text(False)

    def prompt(self, path: str, body: bytes) -> tuple[int, str, bytes]:
        unavailable = 503, 'application/json', b'{"error":"ComfyUI is not accepting media jobs"}'
        # Never strand a control-server worker behind a long model switch.
        if not self.admission.acquire(timeout=1):
            return unavailable
        try:
            with self.guard:
                if self.data['mode'] not in {'image', 'video'} or not self.data['comfy_ready']:
                    return unavailable
                mode = self.data['mode']
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('Prompt must be a JSON object')
            requested = prompt_mode(value)
            if requested != mode:
                return 409, 'application/json', json.dumps({
                    'error': f'Start {requested} mode before submitting this {requested} workflow',
                    'mode': mode, 'required_mode': requested}).encode()
            result = self.host.prompt(path, body)
            if 200 <= result[0] < 300:
                self.last_activity = self.clock()
                self.persist()
            return result
        finally:
            self.admission.release()

    def tick(self) -> None:
        if not self.admission.acquire(blocking=False):
            return
        try:
            if self.status()['mode'] in {'starting', 'stopping'}:
                return
            known = self.sample()
            with self.guard:
                if self.data['mode'] in {'starting', 'stopping'} or self.worker and self.worker.is_alive():
                    return
                state = self.status()
                if state['text_running'] and state['comfy_running']:
                    self.gate.close('error')
                    self.advertise_text(False)
                    self.update(mode='error', error='Both services are running; operator intervention required')
                    return
                if not state['comfy_running']:
                    if state['text_running'] and state['text_ready']:
                        self.update(mode='text')
                        self.gate.open()
                        self.advertise_text(True)
                    elif state['text_running']:
                        self.gate.close('starting')
                    elif state['mode'] != 'error':
                        self.submit('stop')
                    return
                if (state['mode'] in {'image', 'video'} and state['comfy_running'] and known and
                        state['queue_running'] == 0 and state['queue_pending'] == 0 and
                        self.clock() - self.last_activity >= IDLE_TIMEOUT):
                    self.submit('stop')
        except Exception:
            self.gate.close('unavailable')
            self.advertise_text(False)
            self.update(error='Could not check service activity; automatic stop postponed')
        finally:
            self.admission.release()



def main() -> None:
    os.umask(0o077)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    singleton = (STATE.parent / 'controller.lock').open('a')
    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = TextConfig.load()
    controller = Controller(Host(config, WorkflowActivity.load()))
    controller.recover()
    server = ControlHTTPServer(('127.0.0.1', 8189), handler(controller))
    proxy = make_proxy(config, controller.gate)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    server.timeout = 1
    def monitor():
        while True:
            controller.tick()
            time.sleep(5)
    threading.Thread(target=monitor, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
