#!/usr/bin/env python3
"""Private, deterministic OpenAI acknowledgements after native image generation."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import uuid

MODEL = 'homecompute-images'
ACCEPTED_MODELS = {MODEL, 'image-studio'}
ADDRESS = ('172.28.200.1', 18193)
LIMIT = 32 * 1024 * 1024
SUCCESS = ('The requested image has been created by the system successfully',
           'The requested image has been edited and created')
FAILURE = ('Image generation was attempted but failed', 'Image generation was requested but the feature is currently disabled')


def content_for(body: dict) -> str:
    """Inspect server-injected status only; never echo prompts or image data."""
    statuses = []
    for index, message in enumerate(body.get('messages', [])):
        if not isinstance(message, dict) or message.get('role') != 'system':
            continue
        text = message.get('content')
        if not isinstance(text, str):
            continue
        for prefix in SUCCESS:
            at = text.rfind('<context>' + prefix)
            if at >= 0:
                statuses.append((index, at, True))
        for prefix in FAILURE:
            at = text.rfind('<context>' + prefix)
            if at >= 0:
                statuses.append((index, at, False))
    if statuses:
        return ('Your image is ready.' if max(statuses)[2] else
                'The image could not be created. Check that image mode is running, then try again.')
    if body.get('stream'):
        return 'Turn on Image in this chat to create or edit an image.'
    # Open WebUI title/tag/follow-up tasks use the selected model by default.
    # All supported task parsers can consume their own field from this object.
    return json.dumps({'title': 'Images', 'tags': ['Images'], 'follow_ups': [], 'queries': []})


def completion(body: dict) -> dict:
    return {'id': 'chatcmpl-image-' + uuid.uuid4().hex, 'object': 'chat.completion',
            'created': int(time.time()), 'model': MODEL,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content_for(body)},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        request.settimeout(15)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status: int, value: dict):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            self.reply(200, {'status': 'ok'})
        elif self.path == '/v1/models':
            self.reply(200, {'object': 'list', 'data': [{'id': MODEL, 'object': 'model',
                                                       'created': 0, 'owned_by': 'homecompute'}]})
        else:
            self.reply(404, {'error': {'message': 'Unknown image chat resource'}})

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.reply(404, {'error': {'message': 'Unknown image chat resource'}})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < length <= LIMIT:
                self.reply(413, {'error': {'message': 'Unsupported request size'}})
                return
            body = json.loads(self.rfile.read(length))
            if (not isinstance(body, dict) or body.get('model') not in ACCEPTED_MODELS or
                    not isinstance(body.get('messages'), list) or len(body['messages']) > 1000):
                raise ValueError('Invalid image chat envelope')
            response = completion(body)
            if not body.get('stream'):
                self.reply(200, response)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            common = {key: response[key] for key in ('id', 'created', 'model')}
            for delta, finish in ((response['choices'][0]['message'], None), ({}, 'stop')):
                chunk = {**common, 'object': 'chat.completion.chunk',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
        except (ValueError, TypeError, OSError):
            self.reply(400, {'error': {'message': 'Invalid image chat request'}})


if __name__ == '__main__':
    Server(ADDRESS, Handler).serve_forever()
