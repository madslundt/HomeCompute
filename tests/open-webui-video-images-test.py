#!/usr/bin/env python3
"""Real image uploads and first-frame conditioning with strict chat-file ownership."""
import base64
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('video_images_fixture', ROOT / 'tests/open-webui-videos-test.py')
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
m, FILE_ID = fixture.m, fixture.FILE_ID


def picture(format='PNG', size=(4, 3)):
    stream = BytesIO()
    Image.new('RGB', size, 'red').save(stream, format=format)
    return stream.getvalue()


class ImageVideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.VideoPipeTests.asyncSetUp(self)
        self.image = picture()
        self.data_url = 'data:image/png;base64,' + base64.b64encode(self.image).decode()
        self.file_lookups, self.storage_reads = [], []

    async def asyncTearDown(self):
        await fixture.VideoPipeTests.asyncTearDown(self)

    async def run_video(self, body=None, **kwargs):
        return await fixture.VideoPipeTests.run_video(self, body, **kwargs)

    def body(self, reference):
        return {'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Animate this photograph'},
            {'type': 'image_url', 'image_url': {'url': reference}}]}]}

    def files(self, *, owner='our-user', content_type='image/png'):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'trusted-upload.png'
        path.write_bytes(self.image)
        record = SimpleNamespace(user_id=owner, path=str(path), meta={'content_type': content_type, 'size': len(self.image)})

        async def get(identifier):
            self.file_lookups.append(identifier)
            return record

        def read(value):
            self.assertEqual(value, str(path), 'Only the database-owned storage path may be opened')
            self.storage_reads.append(value)
            return value

        modules = {name: ModuleType(name) for name in (
            'open_webui', 'open_webui.models', 'open_webui.models.files',
            'open_webui.storage', 'open_webui.storage.provider')}
        modules['open_webui.models.files'].Files = SimpleNamespace(get_file_by_id=get)
        modules['open_webui.storage.provider'].Storage = SimpleNamespace(get_file=read)
        return patch.dict('sys.modules', modules)

    def assert_image_conditioned(self):
        self.assertEqual(len(self.uploads), 1)
        uploaded = self.uploads[0]
        self.assertEqual(uploaded['bytes'], self.image)
        self.assertEqual(uploaded['content_type'], 'image/png')
        graph = self.submissions[0]['prompt']
        self.assertEqual(graph['900']['inputs']['image'], 'homecompute-videos/' + uploaded['name'])
        self.assertEqual(graph['377']['inputs']['video_latent'], ['902', 0])
        self.assertEqual(graph['340']['inputs']['video_latent'], ['903', 0])
        self.assertEqual(graph['902']['inputs'], {'vae': ['385', 0], 'image': ['901', 0],
            'latent': ['356', 0], 'strength': 0.7, 'bypass': False})
        self.assertEqual(graph['903']['inputs'], {'vae': ['385', 0], 'image': ['901', 0],
            'latent': ['348', 0], 'strength': 1.0, 'bypass': False})
        self.assertEqual(graph['340']['inputs']['audio_latent'], ['367', 1])
        self.assertEqual(graph['365'], m.GRAPH['365'])
        self.assertEqual(m.GRAPH['377']['inputs']['video_latent'], ['356', 0])

    async def test_data_url_picture_is_uploaded_and_conditions_both_native_passes(self):
        result = await self.run_video(self.body(self.data_url))
        self.assertIn('<video>\n', result)
        self.assert_image_conditioned()

    async def test_chat_image_graph_matches_the_reviewed_native_i2v_artifact(self):
        expected = json.loads((ROOT / 'deploy/comfyui-control/workflows/ltx25-image-api.json').read_text())
        self.assertEqual(m.image_graph(m.GRAPH, 'homecompute-start.png'), expected)

    async def test_actual_owui_file_attachment_uuid_fallback_resolves_only_owned_storage(self):
        with self.files():
            result = await self.run_video(__files__=[{'type': 'file', 'url': FILE_ID}])
        self.assertIn('<video>\n', result)
        self.assertEqual(self.file_lookups, [FILE_ID])
        self.assertEqual(len(self.storage_reads), 1)
        self.assert_image_conditioned()

    async def test_protected_file_url_and_body_file_metadata_are_supported(self):
        with self.files():
            result = await self.run_video(self.body('/api/v1/files/' + FILE_ID + '/content'))
        self.assertIn('<video>\n', result)
        self.assert_image_conditioned()
        self.uploads.clear()
        self.submissions.clear()
        with self.files():
            result = await self.run_video({'messages': [{'role': 'user', 'content': 'Animate my picture'}],
                                           'files': [{'type': 'file', 'url': FILE_ID}]})
        self.assertIn('<video>\n', result)
        self.assert_image_conditioned()

    async def test_inline_image_takes_precedence_over_duplicate_attachment_metadata(self):
        result = await self.run_video(self.body(self.data_url), __files__=[{'type': 'file', 'url': FILE_ID}])
        self.assertIn('<video>\n', result)
        self.assertEqual(self.file_lookups, [])
        self.assert_image_conditioned()

    async def test_another_users_file_or_nonimage_is_rejected_before_reading_or_starting(self):
        with self.files(owner='another-user'):
            self.assertIn('must belong to your chat account', await self.run_video(__files__=[{'url': FILE_ID}]))
        with self.files(content_type='application/pdf'):
            self.assertIn('not a starting image', await self.run_video(__files__=[{'url': FILE_ID}]))
        self.assertEqual(self.storage_reads, [])
        self.assertEqual(self.requests, [])

    async def test_external_urls_and_local_paths_never_get_fetched(self):
        for reference in ('https://evil.example/image.png', 'http://169.254.169.254/latest/meta-data',
                          'file:///etc/passwd', '/etc/passwd', '/api/v1/files/not-a-uuid/content'):
            result = await self.run_video(self.body(reference))
            self.assertIn('external URLs and local filesystem paths', result)
            self.assertNotIn(reference, result)
        self.assertEqual(self.requests, [])

    async def test_multiple_or_malformed_image_parts_fail_without_dumping_data_urls(self):
        body = self.body(self.data_url)
        body['messages'][0]['content'].append({'type': 'image_url', 'image_url': {'url': self.data_url}})
        self.assertIn('exactly one', await self.run_video(body))
        for reference in ('data:image/png;base64,not-valid!', 'data:image/svg+xml;base64,AAAA'):
            result = await self.run_video(self.body(reference))
            self.assertIn('malformed or unsupported', result)
            self.assertNotIn(reference, result)
        self.assertEqual(self.requests, [])

    async def test_image_size_and_dimensions_are_bounded_before_any_gpu_request(self):
        with patch.object(m, 'MAX_IMAGE_BYTES', 32):
            self.assertIn('20 MiB', await self.run_video(self.body(self.data_url)))
        oversized = 'data:image/png;base64,' + base64.b64encode(picture(size=(8193, 1))).decode()
        self.assertIn('8192', await self.run_video(self.body(oversized)))
        self.assertEqual(self.requests, [])

    async def test_text_content_parts_without_an_image_keep_the_text_graph(self):
        result = await self.run_video({'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'A fox'}, {'type': 'text', 'text': 'in snow'}]}]})
        self.assertIn('<video>\n', result)
        self.assertEqual(self.uploads, [])
        self.assertEqual(set(self.submissions[0]['prompt']), set(m.GRAPH))
        self.assertEqual(self.submissions[0]['prompt']['364']['inputs']['text'], 'A fox\nin snow')


if __name__ == '__main__':
    unittest.main()
