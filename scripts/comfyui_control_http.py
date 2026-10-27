#!/usr/bin/env python3
"""Fixed-origin media controls and payload validation for the shared GPU lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from llm_admission import BoundedHTTPServer

ORIGINS = {'https://models.home.arpa', 'https://comfyui.home.arpa', 'https://home-core.tail479ad.ts.net:18188'}
PAGE = Path('/opt/homecompute/comfyui-control/index.html')
ROUTES = {
    '/control/api/start': ('start', 'image'),
    '/control/api/stop': ('stop', 'text'),
    '/control/api/start/image': ('start', 'image'),
    '/control/api/start/video': ('start', 'video'),
    '/control/api/start/text': ('stop', 'text'),
}


def prompt_mode(value: dict) -> str:
    graph = value.get('prompt')
    if not isinstance(graph, dict) or not graph:
        raise ValueError('A nonempty API workflow is required in prompt')
    image, video = False, False
    for node in graph.values():
        if (not isinstance(node, dict) or not isinstance(node.get('class_type'), str)
                or not node['class_type'] or not isinstance(node.get('inputs'), dict)):
            raise ValueError('Workflow nodes require class_type and inputs')
        kind, inputs = node['class_type'], node['inputs']
        video |= (kind.startswith('LTX') or kind in {'SaveVideo', 'CreateVideo'}
                  or str(inputs.get('unet_name', '')).lower().startswith('ltx'))
        image |= ('Qwen' in kind or kind == 'SaveImage' or inputs.get('type') == 'qwen_image'
                  or str(inputs.get('unet_name', '')).lower().startswith('qwen'))
    if video:
        return 'video'
    if image:
        return 'image'
    raise ValueError('Workflow must identify an image or video generation graph')


ControlHTTPServer = BoundedHTTPServer


def handler(controller, page: Path = PAGE):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log image prompts, uploads, URLs, or request bodies.

        def reply(self, status: int, body: bytes, content_type: str = 'application/json'):
            try:
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

        def do_GET(self):
            path = self.path.partition('?')[0]
            if path == '/control/api/llm-status':
                self.reply(200, json.dumps(controller.llm_status()).encode())
            elif path == '/control/api/status':
                self.reply(200, json.dumps(controller.status()).encode())
            elif path in {'/control', '/control/'}:
                self.reply(200, page.read_bytes(), 'text/html; charset=utf-8')
            else:
                self.reply(404, b'{"error":"Unknown resource"}')

        def do_POST(self):
            if self.headers.get('Origin') not in ORIGINS:
                self.reply(403, b'{"error":"Untrusted request origin"}')
                return
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                self.reply(415, b'{"error":"A JSON request is required"}')
                return
            is_prompt = self.path in {'/prompt', '/api/prompt'}
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= (8 * 1024 * 1024 if is_prompt else 1024):
                    raise ValueError('Request body is too large or missing')
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('Incomplete request body')
                value = json.loads(body)
                if is_prompt:
                    if not isinstance(value, dict):
                        raise ValueError('Prompt must be a JSON object')
                    status, content_type, result = controller.prompt(self.path, body)
                    self.reply(status, result, content_type)
                elif self.path in ROUTES:
                    if value != {}:
                        raise ValueError('Service actions accept only an empty object')
                    self.reply(202, json.dumps(controller.submit(*ROUTES[self.path])).encode())
                else:
                    self.reply(404, b'{"error":"Unknown action"}')
            except (ValueError, TypeError) as error:
                self.reply(400 if is_prompt else 409, json.dumps({'error': str(error)}).encode())
            except Exception:
                self.reply(502, b'{"error":"Local service unavailable"}')
    return Handler
