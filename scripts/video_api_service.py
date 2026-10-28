#!/usr/bin/env python3
"""Private, durable OpenAI video jobs backed by the existing guarded Spark workflow."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default as email_policy
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

BIND = ('172.28.200.1', 18194)
ALLOWED_PEER = '172.28.200.2'
ENGINE_BASE = 'http://172.28.200.2:8188'
MODEL = 'ltx-2.5'
MAX_PENDING = 8
MAX_REQUEST_BYTES = 64 * 1024
UUID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


class QueueFull(RuntimeError):
    pass


def validate_request(value: dict) -> str:
    if not isinstance(value, dict) or set(value) - {'model', 'prompt', 'seconds', 'size'}:
        raise ValueError('Only model, prompt, seconds and size are supported; image references and remix are unavailable')
    if value.get('model', MODEL) != MODEL:
        raise ValueError('The only supported model is ltx-2.5')
    prompt = value.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 or '\x00' in prompt:
        raise ValueError('Prompt must contain 1 to 4000 text characters')
    seconds = value.get('seconds', '2')
    if not (type(seconds) is int and seconds == 2 or type(seconds) is str and seconds == '2'):
        raise ValueError('Only seconds=2 is supported')
    if value.get('size', '768x512') != '768x512':
        raise ValueError('Only size=768x512 is supported')
    return prompt.strip()


def parse_request(body: bytes, content_type: str) -> str:
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError('Video request is too large')
    message = BytesParser(policy=email_policy).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    if message.get_content_type() == 'application/json':
        return validate_request(json.loads(body.decode('utf-8')))
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart() or message.defects:
        raise ValueError('Send application/json or text-only multipart/form-data')
    fields = {}
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if (part.is_multipart() or part.defects or part.get_content_disposition() != 'form-data'
                or part.get_filename() is not None or name not in {'model', 'prompt', 'seconds', 'size'}
                or name in fields or part.get_content_type() != 'text/plain'
                or part.get('Content-Transfer-Encoding', '').lower() not in {'', '7bit', '8bit', 'binary'}):
            raise ValueError('Multipart accepts each supported text field once; file uploads are unavailable')
        fields[name] = part.get_payload(decode=True).decode('utf-8')
    return validate_request(fields)


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root, self.lock = root.resolve(), threading.RLock()
        info = self.root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Video state must be an owner-private directory')
        self.database = self.root / 'jobs.sqlite3'
        if self.database.is_symlink():
            raise ValueError('A symlinked video database is not allowed')
        with self.lock, self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, prompt TEXT NOT NULL,
                created_at INTEGER NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
                backend_id TEXT, completed_at INTEGER, error TEXT)''')
            db.execute("UPDATE jobs SET status='failed', progress=0, completed_at=?, error=? WHERE status='in_progress'",
                       (int(time.time()), 'Video service restarted; any accepted Spark job continues and was not resubmitted'))
        self.database.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self) -> int:
        with self.lock, self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','in_progress')").fetchone()[0]

    def create(self, prompt: str) -> dict:
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','in_progress')").fetchone()[0] >= MAX_PENDING:
                raise QueueFull('The video queue is full; retry after existing jobs finish')
            identifier = str(uuid.uuid4())
            db.execute('INSERT INTO jobs (id,prompt,created_at,status,progress) VALUES (?,?,?,?,?)',
                       (identifier, prompt, int(time.time()), 'queued', 0))
        return self.get(identifier)

    def get(self, identifier: str) -> dict | None:
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            return None
        return {'id': row['id'], 'object': 'video', 'created_at': row['created_at'], 'status': row['status'],
                'progress': row['progress'], 'model': MODEL, 'seconds': '2', 'size': '768x512',
                'completed_at': row['completed_at'], 'error':
                {'code': 'video_generation_failed', 'message': row['error']} if row['error'] else None}

    def claim(self) -> dict | None:
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id,prompt FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if row is not None:
                db.execute("UPDATE jobs SET status='in_progress',progress=5 WHERE id=?", (row['id'],))
                return dict(row)
        return None

    def update(self, identifier: str, *, progress: int | None = None, backend_id: str | None = None,
               error: str | None = None, complete: bool = False) -> None:
        with self.lock, self.connect() as db:
            if backend_id:
                db.execute('UPDATE jobs SET backend_id=? WHERE id=?', (backend_id, identifier))
            if progress is not None:
                db.execute('UPDATE jobs SET progress=? WHERE id=?', (progress, identifier))
            if error is not None or complete:
                db.execute('UPDATE jobs SET status=?,completed_at=?,error=?,progress=? WHERE id=?',
                           ('failed' if error is not None else 'completed', int(time.time()), error,
                            0 if error is not None else 100, identifier))

    def content(self, identifier: str) -> Path:
        path = self.root / (identifier + '.mp4')
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('The completed video file is unavailable')
        return path


class Engine:
    def __init__(self):
        import homecompute_videos as videos
        videos.API_BASE = ENGINE_BASE
        self.videos, self.pipe = videos, videos.Pipe()

    async def generate(self, prompt: str, accepted, status) -> bytes:
        await self.pipe._activate(status)
        graph = deepcopy(self.videos.GRAPH)
        graph['364']['inputs']['text'] = prompt
        response = await self.pipe._json('/prompt', {'prompt': graph, 'client_id': str(uuid.uuid4())})
        identifier = response.get('prompt_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
            raise RuntimeError('Spark returned no valid prompt ID; any accepted job was not resubmitted')
        accepted(identifier)
        history = await self.pipe._history(identifier, status)
        output = self.videos.video_output(history)
        return await asyncio.to_thread(self.pipe._read, '/view?' + urlencode(output), video=True)


class Worker:
    def __init__(self, store: Store, engine):
        self.store, self.engine = store, engine
        self.wake, self.stopping = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name='video-worker', daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        self.thread.join(timeout=1)

    def run(self) -> None:
        while not self.stopping.is_set():
            job = self.store.claim()
            if job is None:
                self.wake.wait(1)
                self.wake.clear()
                continue
            identifier = job['id']
            temporary = self.store.root / (identifier + '.part')
            try:
                async def status(description):
                    progress = 50 if 'Generating' in description else 10 if 'queued' in description else 5
                    self.store.update(identifier, progress=progress)

                data = asyncio.run(self.engine.generate(job['prompt'],
                    lambda value: self.store.update(identifier, backend_id=value), status))
                if not isinstance(data, bytes) or not 12 <= len(data) <= 128 * 1024**2 or data[4:8] != b'ftyp':
                    raise RuntimeError('Spark returned an invalid or oversized MP4')
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.store.root / (identifier + '.mp4'))
                self.store.update(identifier, complete=True)
            except Exception as error:
                temporary.unlink(missing_ok=True)
                self.store.update(identifier, error=str(error)[:1000] or 'Video generation failed')


def handler(store: Store, worker: Worker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log prompts, URLs, authorization headers, or request bodies.

        def reply(self, status: int, value: dict) -> None:
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def error(self, status: int, message: str) -> None:
            self.reply(status, {'error': {'message': message, 'type': 'invalid_request_error' if status < 500 else 'server_error'}})

        def admitted(self) -> bool:
            if self.client_address[0] != ALLOWED_PEER:
                self.error(403, 'Only the fixed private Caddy peer may access video jobs')
                return False
            return True

        def do_POST(self):
            if not self.admitted():
                return
            if self.path != '/v1/videos':
                self.error(404, 'Unknown video API resource')
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if self.headers.get('Transfer-Encoding') or not 0 <= length <= MAX_REQUEST_BYTES:
                    raise ValueError('A bounded Content-Length is required')
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('Incomplete video request')
                prompt = parse_request(body, self.headers.get('Content-Type', ''))
                value = store.create(prompt)
                worker.wake.set()
                self.reply(202, value)
            except QueueFull as error:
                self.error(429, str(error))
            except (ValueError, UnicodeError, OSError) as error:
                self.error(400, str(error))
            except Exception:
                self.error(500, 'Video request could not be persisted')

        def do_GET(self):
            if not self.admitted():
                return
            path = urlsplit(self.path)
            if path.query:
                self.error(400, 'Video query variants are not supported')
                return
            if path.path == '/health':
                self.reply(200, {'status': 'ok', 'active_jobs': store.active()})
                return
            match = re.fullmatch(r'/v1/videos/(' + UUID_PATTERN + r')(/content)?', path.path)
            if not match:
                self.error(404, 'Unknown video API resource')
                return
            value = store.get(match[1])
            if value is None:
                self.error(404, 'Unknown video job')
            elif not match[2]:
                self.reply(200, value)
            elif value['status'] != 'completed':
                self.error(409, 'Video content is available only after this job completes')
            else:
                try:
                    content = store.content(match[1])
                    with content.open('rb') as source:
                        self.send_response(200)
                        self.send_header('Content-Type', 'video/mp4')
                        self.send_header('Content-Length', str(content.stat().st_size))
                        self.send_header('Content-Disposition', f'attachment; filename="{match[1]}.mp4"')
                        self.send_header('Cache-Control', 'private, no-store')
                        self.end_headers()
                        while block := source.read(64 * 1024):
                            self.wfile.write(block)
                except OSError:
                    self.close_connection = True
                except RuntimeError as error:
                    self.error(500, str(error))
    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(15)
        return request, address


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/homecompute-video-api'))
    args = parser.parse_args()
    os.umask(0o077)
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    singleton = (args.state / 'service.lock').open('a')
    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = Store(args.state)
    worker = Worker(store, Engine())
    server = Server(BIND, handler(store, worker))
    worker.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        worker.stop()


if __name__ == '__main__':
    main()
