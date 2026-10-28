#!/usr/bin/env python3
"""Exercise dedicated video chat through real HTTP without Open WebUI or a GPU."""
import asyncio
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('video_pipe', ROOT / 'deploy/open-webui/functions/homecompute_videos.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
FILE_ID = '00000000-0000-4000-8000-000000000001'


class VideoPipeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode, self.target, self.unified = 'text', 'text', True
        self.busy, self.failed, self.foreign, self.missing_id = False, False, False, False
        self.malformed_history, self.supersede, self.redirect = False, False, False
        self.output_name, self.output_folder = 'our-clip.mp4', 'video'
        self.payload = b'\x00\x00\x00\x18ftypisom' + b'fixture MP4 bytes'
        self.header_length = None
        self.status_polls, self.history_polls = 0, 0
        self.requests, self.submissions, self.stored, self.events, self.uploads = [], [], [], [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, value, code=200):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.requests.append(('GET', self.path))
                owner.assertIsNone(self.headers.get('Origin'))
                if self.path == '/control/api/status':
                    owner.status_polls += 1
                    if owner.mode == 'starting' and owner.status_polls > 1:
                        owner.mode, owner.target = ('image', 'image') if owner.supersede else ('video', 'video')
                    state = dict(mode=owner.mode, comfy_ready=owner.mode == 'video',
                                 text_running=owner.mode == 'text', error=None,
                                 transition={'phase': 'waiting_workflows', 'workflows': 1, 'agent_turns': 2})
                    if owner.unified:
                        state['target_mode'] = owner.target
                    self.reply(state)
                elif self.path == '/history/our-prompt':
                    owner.history_polls += 1
                    if owner.malformed_history:
                        self.reply({'our-prompt': {'status': 'invalid'}})
                        return
                    entry = {'status': {'completed': True, 'status_str': 'error' if owner.failed else 'success',
                                        'messages': []},
                             'outputs': {'75': {'images': [{'filename': owner.output_name,
                                         'subfolder': owner.output_folder, 'type': 'output'}]}}}
                    self.reply({'some-previous-prompt': entry} if owner.foreign else
                               {'our-prompt': entry} if owner.history_polls > 1 else {'our-prompt': {}})
                elif self.path == '/queue':
                    self.reply({'queue_running': [], 'queue_pending': [[1, 'our-prompt']]})
                elif self.path.startswith('/view?'):
                    if owner.redirect:
                        self.send_response(302)
                        self.send_header('Location', '/untrusted-redirect')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    query = parse_qs(urlsplit(self.path).query)
                    owner.assertEqual(query, {'filename': ['our-clip.mp4'], 'subfolder': ['video'], 'type': ['output']})
                    self.send_response(200)
                    self.send_header('Content-Length', str(owner.header_length or len(owner.payload)))
                    self.end_headers()
                    try:
                        self.wfile.write(owner.payload)
                    except BrokenPipeError:
                        pass

            def do_POST(self):
                owner.requests.append(('POST', self.path))
                if self.path == '/upload/image':
                    owner.assertIsNone(self.headers.get('Origin'))
                    raw = self.rfile.read(int(self.headers['Content-Length']))
                    message = BytesParser(policy=email_policy).parsebytes(
                        b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + raw)
                    fields = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
                    image = fields['image']
                    owner.uploads.append({'name': image.get_filename(), 'bytes': image.get_payload(decode=True),
                                          'content_type': image.get_content_type()})
                    self.reply({'name': image.get_filename(), 'subfolder': 'homecompute-videos', 'type': 'input'})
                    return
                owner.assertEqual(self.headers.get('Origin'), 'https://models.home.arpa')
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/control/api/start/video':
                    if owner.busy:
                        self.reply({'error': 'Image jobs are active'}, 409)
                    else:
                        owner.mode, owner.target, owner.status_polls = 'starting', 'video', 0
                        self.reply({'target_mode': 'video'}, 202)
                elif self.path == '/prompt':
                    owner.assertEqual(owner.mode, 'video')
                    owner.submissions.append(body)
                    self.reply({} if owner.missing_id else {'prompt_id': 'our-prompt'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.patches = [patch.object(m, 'API_BASE', f'http://127.0.0.1:{self.server.server_port}'),
                        patch.object(m, 'POLL_SECONDS', 0.001), patch.object(m, 'WAIT_SECONDS', 0.2)]
        for item in self.patches:
            item.start()
        self.pipe = m.Pipe()

        async def store(data, filename, user, request):
            self.stored.append((data, filename, user['id']))
            return FILE_ID

        self.pipe._store = store

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()

    async def run_video(self, body=None, **kwargs):
        async def emit(event):
            self.events.append(event)

        return await self.pipe.pipe(body or {'messages': [{'role': 'user', 'content': 'A fox in fresh snow'}]},
                                    __user__={'id': 'our-user'}, __request__=object(),
                                    __event_emitter__=emit, **kwargs)

    async def test_success_uses_only_fixed_graph_and_own_video_and_emits_private_native_player(self):
        text = await self.run_video()
        url = '/api/v1/files/' + FILE_ID + '/content'
        self.assertEqual(text, '<video>\n' + url + '\n</video>\n\n[Download video](' + url + ')')
        self.assertEqual(self.stored, [(self.payload, 'LTX-2.5-our-prompt.mp4', 'our-user')])
        expected = json.loads((ROOT / 'deploy/comfyui-control/workflows/ltx25-text-api.json').read_text())
        expected['364']['inputs']['text'] = 'A fox in fresh snow'
        self.assertEqual(self.submissions[0]['prompt'], expected)
        self.assertTrue(any(e['type'] == 'files' and e['data']['files'][0]['id'] == FILE_ID for e in self.events))
        self.assertTrue(any('Workflows: 1; agent turns: 2' in e['data'].get('description', '') for e in self.events))
        self.assertTrue(self.events[-1]['data']['done'])
        self.assertFalse(any('/stop' in path or '/interrupt' in path or '/v1/' in path for _, path in self.requests))

    async def test_existing_video_transition_is_joined_without_second_start(self):
        self.mode, self.target = 'starting', 'video'
        self.assertIn('<video>\n/api/v1/files/' + FILE_ID + '/content\n</video>', await self.run_video())
        self.assertNotIn(('POST', '/control/api/start/video'), self.requests)

    async def test_busy_image_or_competing_transition_is_never_interrupted_or_enqueued(self):
        self.mode, self.target, self.busy = 'image', 'image', True
        self.assertIn('active image jobs', await self.run_video())
        self.assertEqual(self.submissions, [])
        self.assertEqual(self.mode, 'image')
        self.requests.clear()
        self.mode, self.target, self.status_polls = 'starting', 'text', -5
        self.assertIn('Another model switch', await self.run_video())
        self.assertFalse(any(method == 'POST' for method, _ in self.requests))

    async def test_superseded_video_start_fails_instead_of_waiting_indefinitely(self):
        self.supersede = True
        self.assertIn('superseded', await self.run_video())
        self.assertEqual(self.submissions, [])

    async def test_old_controller_and_auxiliary_tasks_have_no_gpu_side_effects(self):
        self.unified = False
        self.assertIn('not installed yet', await self.run_video())
        self.assertFalse(any(method == 'POST' for method, _ in self.requests))
        self.requests.clear()
        for task in ('title_generation', 'tags_generation', 'follow_up_generation'):
            json.loads(await self.run_video(__task__=task))
        self.assertEqual(self.requests, [])

    async def test_empty_prior_or_malformed_history_never_reuses_another_video(self):
        self.foreign = True
        self.assertIn('no completed history', await self.run_video())
        self.assertEqual(self.stored, [])
        self.assertFalse(any(path.startswith('/view') for _, path in self.requests))
        self.foreign, self.malformed_history = False, True
        self.assertIn('malformed history', await self.run_video())
        self.assertEqual(self.stored, [])

    async def test_execution_failure_or_missing_prompt_id_never_downloads_a_video(self):
        self.failed = True
        self.assertIn('could not finish', await self.run_video())
        self.failed, self.missing_id = False, True
        self.assertIn('no valid prompt ID', await self.run_video())
        self.assertEqual(self.stored, [])
        self.assertFalse(any(path.startswith('/view') for _, path in self.requests))

    async def test_forged_paths_are_rejected_before_any_download(self):
        for name, folder in (('../other.mp4', 'video'), ('our-clip.mp4', '../../secret'),
                             ('our-clip.mp4', '/absolute')):
            self.output_name, self.output_folder = name, folder
            self.assertIn('no valid output MP4', await self.run_video())
        self.assertEqual(self.stored, [])
        self.assertFalse(any(path.startswith('/view') for _, path in self.requests))

    async def test_oversize_and_redirected_downloads_are_not_stored(self):
        self.header_length = m.MAX_VIDEO_BYTES + 1
        self.assertIn('size limit', await self.run_video())
        self.header_length, self.redirect = None, True
        self.assertIn('unexpected redirect', await self.run_video())
        self.assertEqual(self.stored, [])
        self.assertNotIn(('GET', '/untrusted-redirect'), self.requests)

    async def test_multipart_empty_overlong_and_nonuser_messages_never_reach_gpu(self):
        for content in ('', ' ', 'x' * 4001, [{'type': 'input_audio', 'audio': 'unsupported'}]):
            self.assertIn('failed', await self.run_video({'messages': [{'role': 'user', 'content': content}]}))
        self.assertIn('failed', await self.run_video({'messages': [{'role': 'assistant', 'content': 'old reply'}]}))
        self.assertEqual(self.requests, [])

    async def test_cancelled_browser_request_does_not_interrupt_the_accepted_gpu_job(self):
        self.foreign = True
        task = asyncio.create_task(self.run_video())
        while not self.submissions:
            await asyncio.sleep(0.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.mode, 'video')
        self.assertFalse(any('/stop' in path or '/interrupt' in path for _, path in self.requests))

    async def test_real_storage_helper_passes_plain_metadata_and_disables_ingestion_for_the_owner(self):
        owner, request, received = SimpleNamespace(id='our-user'), object(), []

        class Upload:
            def __init__(self, **values):
                self.__dict__.update(values)

        async def get_user(user_id):
            self.assertEqual(user_id, owner.id)
            return owner

        async def upload(request_arg, file, metadata=object(), process=True,
                         process_in_background=True, user=None):
            self.assertTrue(metadata is None or isinstance(metadata, dict), 'FastAPI Form defaults must not leak into direct calls')
            self.assertFalse(process)
            self.assertFalse(process_in_background)
            self.assertIs(user, owner)
            self.assertIs(request_arg, request)
            received.append((file.filename, file.headers['content-type'], file.file.read()))
            return SimpleNamespace(id=FILE_ID)

        modules = {name: ModuleType(name) for name in (
            'fastapi', 'starlette', 'starlette.datastructures', 'open_webui',
            'open_webui.models', 'open_webui.models.users', 'open_webui.routers', 'open_webui.routers.files')}
        modules['fastapi'].UploadFile = Upload
        modules['starlette.datastructures'].Headers = dict
        modules['open_webui.models.users'].Users = SimpleNamespace(get_user_by_id=get_user)
        modules['open_webui.routers.files'].upload_file_handler = upload
        with patch.dict('sys.modules', modules):
            file_id = await m.Pipe._store(self.pipe, self.payload, 'our-video.mp4', {'id': owner.id}, request)
        self.assertEqual(file_id, FILE_ID)
        self.assertEqual(received, [('our-video.mp4', 'video/mp4', self.payload)])


if __name__ == '__main__':
    unittest.main()
