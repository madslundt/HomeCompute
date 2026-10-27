"""
title: Videos
author: HomeCompute
description: Local LTX-2.5 video with audio through the guarded Spark video studio.
version: 1.0.0
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

API_BASE = 'http://caddy:8188'
ORIGIN = 'https://models.home.arpa'
WAIT_SECONDS = 21600
POLL_SECONDS = 3
MAX_VIDEO_BYTES = 128 * 1024**2
MAX_IMAGE_BYTES = 20 * 1024**2
GRAPH = {
    "384": {"class_type":"UNETLoader","inputs":{"unet_name":"ltx-2.5-22b-distilled-transformer-nvfp4.safetensors","weight_dtype":"default"}},
    "387": {"class_type":"CLIPLoader","inputs":{"clip_name":"gemma4-12b-with-proj-ltx-2.5-bf16.safetensors","type":"ltxv","device":"default"}},
    "385": {"class_type":"VAELoader","inputs":{"vae_name":"ltx-2.5-video-vae-bf16.safetensors"}},
    "386": {"class_type":"VAELoader","inputs":{"vae_name":"ltx-2.5-audio-vae-bf16.safetensors"}},
    "371": {"class_type":"LatentUpscaleModelLoader","inputs":{"model_name":"ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"}},
    "364": {"class_type":"CLIPTextEncode","inputs":{"clip":["387",0],"text":"Red fox walking through fresh snow at golden hour, camera tracks smoothly, natural footsteps and wind, no text"}},
    "373": {"class_type":"CLIPTextEncode","inputs":{"clip":["387",0],"text":"pc game, console game, video game, cartoon, childish, ugly"}},
    "365": {"class_type":"LTXVConditioning","inputs":{"positive":["364",0],"negative":["373",0],"frame_rate":24}},
    "356": {"class_type":"EmptyLTXVLatentVideo","inputs":{"width":384,"height":256,"length":49,"batch_size":1}},
    "366": {"class_type":"LTXVEmptyLatentAudio","inputs":{"audio_vae":["386",0],"frames_number":49,"frame_rate":24,"batch_size":1}},
    "377": {"class_type":"LTXVConcatAVLatent","inputs":{"video_latent":["356",0],"audio_latent":["366",0]}},
    "339": {"class_type":"RandomNoise","inputs":{"noise_seed":42}},
    "388": {"class_type":"LTXVDualCFGGuider","inputs":{"model":["384",0],"positive":["365",0],"negative":["365",1],"video_cfg":1,"audio_cfg":1}},
    "352": {"class_type":"KSamplerSelect","inputs":{"sampler_name":"euler_ancestral"}},
    "404": {"class_type":"ManualSigmas","inputs":{"sigmas":"1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
    "344": {"class_type":"SamplerCustomAdvanced","inputs":{"noise":["339",0],"guider":["388",0],"sampler":["352",0],"sigmas":["404",0],"latent_image":["377",0]}},
    "367": {"class_type":"LTXVSeparateAVLatent","inputs":{"av_latent":["344",0]}},
    "348": {"class_type":"LTXVLatentUpsampler","inputs":{"samples":["367",0],"upscale_model":["371",0],"vae":["385",0]}},
    "340": {"class_type":"LTXVConcatAVLatent","inputs":{"video_latent":["348",0],"audio_latent":["367",1]}},
    "338": {"class_type":"RandomNoise","inputs":{"noise_seed":42}},
    "391": {"class_type":"LTXVDualCFGGuider","inputs":{"model":["384",0],"positive":["365",0],"negative":["365",1],"video_cfg":1,"audio_cfg":1}},
    "341": {"class_type":"KSamplerSelect","inputs":{"sampler_name":"euler_ancestral"}},
    "395": {"class_type":"ManualSigmas","inputs":{"sigmas":"0.85, 0.7250, 0.4219, 0.0"}},
    "368": {"class_type":"SamplerCustomAdvanced","inputs":{"noise":["338",0],"guider":["391",0],"sampler":["341",0],"sigmas":["395",0],"latent_image":["340",0]}},
    "369": {"class_type":"LTXVSeparateAVLatent","inputs":{"av_latent":["368",0]}},
    "374": {"class_type":"VAEDecodeTiled","inputs":{"samples":["369",0],"vae":["385",0],"tile_size":512,"overlap":64,"temporal_size":64,"temporal_overlap":16}},
    "358": {"class_type":"LTXVAudioVAEDecode","inputs":{"samples":["369",1],"audio_vae":["386",0]}},
    "370": {"class_type":"CreateVideo","inputs":{"images":["374",0],"audio":["358",0],"fps":24,"bit_depth":8}},
    "75": {"class_type":"SaveVideo","inputs":{"video":["370",0],"filename_prefix":"video/LTX25-text","format":"mp4","format.codec":"h264"}}
}


class RequestFailure(RuntimeError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f'The local video service returned HTTP {code}')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeError('The local video service returned an unexpected redirect')


def file_id(reference: str) -> str:
    if len(reference) > 128:
        raise ValueError('Attach your own chat image; external URLs and local filesystem paths are not supported')
    match = re.fullmatch(r'/api/v1/files/([a-f0-9-]{36})/content', reference)
    candidate = match[1] if match else reference
    try:
        if str(uuid.UUID(candidate)) == candidate:
            return candidate
    except ValueError:
        pass
    raise ValueError('Attach your own chat image; external URLs and local filesystem paths are not supported')


def parse_content(body: dict, files=None) -> tuple[str, str | None]:
    if not isinstance(body, dict):
        raise ValueError('A text chat message is required')
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        raise ValueError('Describe the video in a new text message')
    last = messages[-1]
    content, images, texts = last.get('content'), [], []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str):
                texts.append(part['text'])
            elif isinstance(part, dict) and part.get('type') == 'image_url':
                image = part.get('image_url')
                images.append(image.get('url') if isinstance(image, dict) else image)
            else:
                raise ValueError('Use text and at most one PNG, JPEG or WebP image')
    text = '\n'.join(texts).strip()
    if last.get('role') != 'user' or not text:
        raise ValueError('Video generation requires a nonempty text message from you')
    if len(text) > 4000:
        raise ValueError('Keep the video description within 4000 characters')
    if len(images) > 1:
        raise ValueError('Attach exactly one starting image for image-to-video')
    if not images:
        attached = files if files is not None else last.get('files', body.get('files', []))
        if not isinstance(attached, list):
            raise ValueError('The attached image list is malformed')
        for item in attached:
            reference = item
            if isinstance(item, dict):
                nested = item.get('file')
                reference = item.get('url') or item.get('id') or (nested.get('id') if isinstance(nested, dict) else None)
            if not isinstance(reference, str):
                raise ValueError('The attached image reference is malformed')
            images.append(reference if reference.startswith('data:') else file_id(reference))
    images = list(dict.fromkeys(images)) if all(isinstance(image, str) for image in images) else images
    if len(images) > 1:
        raise ValueError('Attach exactly one starting image for image-to-video')
    if images and not isinstance(images[0], str):
        raise ValueError('The attached image reference is malformed')
    return text, images[0] if images else None


def prompt_text(body: dict) -> str:
    return parse_content(body)[0]


def validate_image(data: bytes) -> tuple[bytes, str, str]:
    from PIL import Image

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('The starting image must be at most 20 MiB')
    try:
        with Image.open(BytesIO(data)) as image:
            formats = {'PNG': ('image/png', 'png'), 'JPEG': ('image/jpeg', 'jpg'), 'WEBP': ('image/webp', 'webp')}
            if image.format not in formats or getattr(image, 'is_animated', False):
                raise ValueError('Use a still PNG, JPEG or WebP starting image')
            if not all(0 < dimension <= 8192 for dimension in image.size):
                raise ValueError('Starting image dimensions must not exceed 8192 pixels')
            mime, extension = formats[image.format]
            image.load()
    except ValueError:
        raise
    except Exception:
        raise ValueError('The starting image could not be decoded as PNG, JPEG or WebP') from None
    return data, mime, extension


def image_graph(graph: dict, image_path: str) -> dict:
    graph = deepcopy(graph)
    graph['900'] = {'class_type': 'LoadImage', 'inputs': {'image': image_path}}
    graph['901'] = {'class_type': 'LTXVPreprocess', 'inputs': {'image': ['900', 0], 'img_compression': 18}}
    for key, latent, strength in (('902', '356', 0.7), ('903', '348', 1.0)):
        graph[key] = {'class_type': 'LTXVImgToVideoInplace', 'inputs': {
            'vae': ['385', 0], 'image': ['901', 0], 'latent': [latent, 0], 'strength': strength, 'bypass': False}}
    graph['377']['inputs']['video_latent'], graph['340']['inputs']['video_latent'] = ['902', 0], ['903', 0]
    graph['75']['inputs']['filename_prefix'] = 'video/LTX25-image'
    return graph


def video_output(history: dict) -> dict:
    outputs = history.get('outputs')
    output = outputs.get('75') if isinstance(outputs, dict) else None
    if not isinstance(output, dict):
        raise RuntimeError('This video job completed without its expected MP4 output')
    for kind in ('images', 'videos', 'gifs'):
        entries = output.get(kind, [])
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            name, folder = item.get('filename'), item.get('subfolder', '')
            if (isinstance(name, str) and 0 < len(name) <= 255 and name.lower().endswith('.mp4')
                    and not any(char in name for char in '/\\\x00') and name not in {'.', '..'}
                    and isinstance(folder, str) and len(folder) <= 1024
                    and not any(char in folder for char in '\\\x00')
                    and not PurePosixPath(folder).is_absolute() and '..' not in PurePosixPath(folder).parts
                    and item.get('type') == 'output'):
                return {'filename': name, 'subfolder': folder, 'type': 'output'}
    raise RuntimeError('This video job returned no valid output MP4; no file was downloaded')


class Pipe:
    def __init__(self):
        self._submission_lock = asyncio.Lock()
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    async def _input_image(self, reference: str, user: dict) -> tuple[bytes, str, str]:
        if reference.startswith('data:'):
            if len(reference) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 128:
                raise ValueError('The starting image must be at most 20 MiB')
            match = re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', reference)
            if not match:
                raise ValueError('The starting image data URL is malformed or unsupported')
            try:
                data = base64.b64decode(match[1], validate=True)
            except ValueError:
                raise ValueError('The starting image data could not be decoded') from None
        else:
            identifier = file_id(reference)
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage

            record = await Files.get_file_by_id(identifier)
            field = lambda key: record.get(key) if isinstance(record, dict) else getattr(record, key, None)
            if record is None or field('user_id') != user['id']:
                raise ValueError('The starting image must belong to your chat account')
            metadata = field('meta')
            if not isinstance(metadata, dict) or not str(metadata.get('content_type', '')).startswith('image/'):
                raise ValueError('The attached file is not a starting image')
            if isinstance(metadata.get('size'), int) and metadata['size'] > MAX_IMAGE_BYTES:
                raise ValueError('The starting image must be at most 20 MiB')
            try:
                path = await asyncio.to_thread(Storage.get_file, field('path'))
                def read():
                    with Path(path).open('rb') as source:
                        return source.read(MAX_IMAGE_BYTES + 1)
                data = await asyncio.to_thread(read)
            except Exception:
                raise ValueError('Your starting image could not be read from chat storage') from None
        return await asyncio.to_thread(validate_image, data)

    def _upload_image(self, image: tuple[bytes, str, str]) -> str:
        data, mime, extension = image
        name, boundary = f'homecompute-{uuid.uuid4().hex}.{extension}', uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="subfolder"\r\n\r\nhomecompute-videos\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\nfalse\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        request = Request(API_BASE + '/upload/image', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
        try:
            with self._opener.open(request, timeout=60) as response:
                raw = response.read(64 * 1024 + 1)
            uploaded = json.loads(raw) if len(raw) <= 64 * 1024 else None
        except Exception:
            raise RuntimeError('The starting image could not be uploaded to the video studio') from None
        if not isinstance(uploaded, dict) or uploaded.get('name') != name or uploaded.get('subfolder') != 'homecompute-videos' or uploaded.get('type') != 'input':
            raise RuntimeError('The video studio returned an unexpected starting-image reference')
        return 'homecompute-videos/' + name

    def _read(self, path: str, body: dict | None = None, *, video: bool = False) -> bytes:
        headers = {'Content-Type': 'application/json'}
        if body is not None:
            headers['Origin'] = ORIGIN
        request = Request(API_BASE + path, json.dumps(body).encode() if body is not None else None, headers)
        try:
            with self._opener.open(request, timeout=35) as response:
                maximum = MAX_VIDEO_BYTES if video else 8 * 1024**2
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) > maximum):
                    raise RuntimeError('The local video response exceeds its size limit')
                data = response.read(maximum + 1)
                if len(data) > maximum:
                    raise RuntimeError('The local video response exceeds its size limit')
                if video and (len(data) < 12 or data[4:8] != b'ftyp'):
                    raise RuntimeError('The completed output is not an MP4 file')
                return data
        except HTTPError as error:
            code = error.code
            error.close()
            raise RequestFailure(code) from None
        except (URLError, TimeoutError) as error:
            raise RuntimeError('The local video service is unreachable; any accepted job continues on Spark') from error

    async def _json(self, path: str, body: dict | None = None) -> dict:
        data = await asyncio.to_thread(self._read, path, body)
        value = json.loads(data)
        if not isinstance(value, dict):
            raise RuntimeError('The local video service returned malformed JSON')
        return value

    @staticmethod
    async def _emit(emitter, kind: str, data: dict) -> None:
        if emitter is not None:
            try:
                await emitter({'type': kind, 'data': data})
            except Exception:
                pass  # A disconnected browser must not interrupt the GPU job.

    async def _activate(self, status) -> None:
        deadline, joining = time.monotonic() + WAIT_SECONDS, False
        while time.monotonic() < deadline:
            state = await self._json('/control/api/status')
            if state.get('target_mode') not in {'text', 'image', 'video'}:
                raise RuntimeError('Video controls are not installed yet. Finish the model-controls update on Spark first')
            mode, target = state.get('mode'), state['target_mode']
            if mode == 'error' or state.get('error'):
                raise RuntimeError('Spark controls report an error. Check the model-controls page before retrying')
            if mode == 'video' and state.get('comfy_ready') is True and not state.get('text_running'):
                return
            if mode in {'starting', 'stopping'}:
                if target != 'video':
                    raise RuntimeError('Another model switch is in progress. Wait for it to finish before requesting video')
                joining = True
            elif joining and mode in {'text', 'image'} and target != 'video':
                raise RuntimeError('Video activation was superseded by another model selection')
            elif mode in {'text', 'image'} and not joining:
                await status('Requesting video mode; active text work finishes before startup.')
                try:
                    await self._json('/control/api/start/video', {})
                except RequestFailure as error:
                    if error.code == 409:
                        raise RuntimeError('Spark has active image jobs or another model switch. Wait for that work to finish') from None
                    raise
                joining = True
                continue
            elif mode not in {'text', 'image', 'video'}:
                raise RuntimeError('Spark returned an unknown model state')
            transition = state.get('transition') or {}
            phase = transition.get('phase') if isinstance(transition, dict) else None
            descriptions = {
                'waiting_workflows': 'Waiting for active workflows and agent turns to finish. Startup has not begun.',
                'draining_text': 'Waiting for active text responses to finish. Startup has not begun.',
                'stopping_text': 'Releasing text-model memory for video.',
                'stopping_comfyui': 'Unloading the previous image or video model.',
                'starting_comfyui': 'Starting the LTX-2.5 video studio.',
                'waiting_comfyui': 'Checking video studio readiness.',
            }
            description = descriptions.get(phase, 'Waiting for video studio readiness.')
            if phase == 'waiting_workflows' and all(type(transition.get(key)) is int
                                                    for key in ('workflows', 'agent_turns')):
                description += f' Workflows: {transition["workflows"]}; agent turns: {transition["agent_turns"]}.'
            await status(description)
            await asyncio.sleep(POLL_SECONDS)
        raise RuntimeError('Video mode is still waiting for active work or startup; no job was submitted')

    async def _history(self, prompt_id: str, status) -> dict:
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            response = await self._json('/history/' + prompt_id)
            history = response.get(prompt_id)
            if history is not None and not isinstance(history, dict):
                raise RuntimeError('This video job returned malformed history; no earlier output was used')
            if history:
                if not isinstance(history, dict) or not isinstance(history.get('status'), dict):
                    raise RuntimeError('This video job returned malformed history; no earlier output was used')
                state = history['status']
                messages = state.get('messages', [])
                if not isinstance(messages, list):
                    raise RuntimeError('This video job returned malformed execution status')
                if state.get('status_str') == 'error' or any(
                        isinstance(message, list) and message and message[0] in {'execution_error', 'execution_interrupted'}
                        for message in messages):
                    raise RuntimeError('LTX-2.5 could not finish this video. Check Spark models and the ComfyUI error log')
                if state.get('completed') is True and state.get('status_str') == 'success':
                    return history
            queue = await self._json('/queue')
            running, pending = queue.get('queue_running'), queue.get('queue_pending')
            if not isinstance(running, list) or not isinstance(pending, list):
                raise RuntimeError('The video queue cannot be verified; any accepted job continues on Spark')
            queued = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in pending)
            await status('Video queued on Spark; the current job finishes first.' if queued else
                         'Generating your video with synchronized audio on Spark.')
            await asyncio.sleep(POLL_SECONDS)
        raise RuntimeError('This video has no completed history yet. Any accepted job continues on Spark')

    async def _store(self, data: bytes, filename: str, user: dict, request) -> str:
        from fastapi import UploadFile
        from starlette.datastructures import Headers
        from open_webui.models.users import Users
        from open_webui.routers.files import upload_file_handler

        owner = await Users.get_user_by_id(user['id'])
        if owner is None:
            raise RuntimeError('Your chat account is unavailable; the completed video was not published')
        upload = UploadFile(filename=filename, file=BytesIO(data), headers=Headers({'content-type': 'video/mp4'}))
        result = await upload_file_handler(request, upload, metadata={}, process=False,
                                           process_in_background=False, user=owner)
        file_id = result.get('id') if isinstance(result, dict) else getattr(result, 'id', None)
        if not isinstance(file_id, str) or str(uuid.UUID(file_id)) != file_id:
            raise RuntimeError('Chat storage did not return a valid video file ID')
        return file_id

    async def pipe(self, body: dict, __user__: dict | None = None, __request__=None,
                   __event_emitter__=None, __task__: str | None = None, __files__=None) -> str:
        if __task__:
            if __task__ == 'title_generation':
                return json.dumps({'title': 'Video generation'})
            if __task__ == 'tags_generation':
                return json.dumps({'tags': ['video']})
            return json.dumps({'follow_ups': []})
        last_status = None

        async def status(description: str, done: bool = False):
            nonlocal last_status
            if description != last_status or done:
                await self._emit(__event_emitter__, 'status', {'description': description, 'done': done})
                last_status = description

        try:
            if not isinstance(__user__, dict) or not isinstance(__user__.get('id'), str) or __request__ is None:
                raise ValueError('A signed-in chat account is required to save your video privately')
            text, reference = parse_content(body, __files__)
            image = await self._input_image(reference, __user__) if reference else None
            graph = deepcopy(GRAPH)
            graph['364']['inputs']['text'] = text
            async with self._submission_lock:
                await self._activate(status)
                if image:
                    await status('Using your image as the first frame, fitted to 768×512 in both rendering passes.')
                    image_path = await asyncio.to_thread(self._upload_image, image)
                    graph = image_graph(graph, image_path)
                await status('Submitting a 768×512 video, 49 frames at 24 fps, with synchronized audio.')
                response = await self._json('/prompt', {'prompt': graph, 'client_id': str(uuid.uuid4())})
                prompt_id = response.get('prompt_id')
                if not isinstance(prompt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', prompt_id):
                    raise RuntimeError('The video job returned no valid prompt ID; success cannot be confirmed')
            history = await self._history(prompt_id, status)
            output = video_output(history)
            await status('Saving the completed video to your private chat files.')
            data = await asyncio.to_thread(self._read, '/view?' + urlencode(output), video=True)
            filename = f'LTX-2.5-{prompt_id}.mp4'
            file_id = await self._store(data, filename, __user__, __request__)
            url = f'/api/v1/files/{file_id}/content'
            await self._emit(__event_emitter__, 'files', {'files': [
                {'type': 'file', 'id': file_id, 'name': filename, 'url': url, 'content_type': 'video/mp4'}]})
            await status('Video ready. Spark restores the text model after 30 minutes idle.', done=True)
            return '<video>\n' + url + '\n</video>\n\n[Download video](' + url + ')'
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            await status(str(error), done=True)
            return 'Video generation failed: ' + str(error)
