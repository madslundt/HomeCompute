#!/usr/bin/env python3
"""Pinned local Danish/English OmniVoice inference; content is never logged."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from pathlib import Path
import resource
import threading
import time
import wave

PHRASES = (
    ('da', 'Lyset i køkkenet er nu tændt.'),
    ('da', 'Pakken leveres til Nørrebrogade toogfyrre i morgen.'),
    ('en', 'The kitchen light is now on.'),
)


def pcm(audio):
    import numpy as np
    samples = np.asarray(audio)
    if samples.ndim != 1 or not 0 < len(samples) <= 24000 * 120 or not np.isfinite(samples).all():
        raise ValueError('Invalid or oversized synthesis')
    return (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()


class Engine:
    def __init__(self, device='cpu', stable_voices=False):
        import torch
        from omnivoice import OmniVoice
        self.config = json.loads(Path('/opt/omnivoice/config.json').read_text())
        self.torch = torch
        torch.set_num_threads(self.config['cpu_threads'])
        torch.set_num_interop_threads(1)
        self.device = device
        self.model = OmniVoice.from_pretrained('/opt/omnivoice/model', device_map=device,
            dtype=torch.float32 if device == 'cpu' else torch.float16,
            attn_implementation='sdpa', local_files_only=True).eval()
        self.prompts = {}
        if stable_voices:
            for language, text in (
                ('da', 'Hej. Jeg er den lokale stemme til hjemmet.'),
                ('en', 'Hello. I am the local voice for this home.'),
            ):
                torch.manual_seed(self.config['seed'])
                audio = self.model.generate(text=text, language=language,
                    instruct=self.config['voice_design'], num_step=32)[0]
                self.prompts[language] = self.model.create_voice_clone_prompt(
                    ref_audio=(torch.from_numpy(audio), 24000), ref_text=text)

    def resources(self):
        return {'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'cuda_allocated_bytes': self.torch.cuda.memory_allocated() if self.device == 'cuda' else 0,
                'cuda_reserved_bytes': self.torch.cuda.memory_reserved() if self.device == 'cuda' else 0,
                'cuda_peak_allocated_bytes': self.torch.cuda.max_memory_allocated() if self.device == 'cuda' else 0}

    def synthesize(self, text, language='da'):
        if (language not in self.config['languages'] or not isinstance(text, str)
                or not text.strip() or len(text) > self.config['maximum_input_characters']):
            raise ValueError('Unsupported language or text')
        self.torch.manual_seed(self.config['seed'])
        # Danish integers use the installed num2words path. Upstream English
        # normalization requires a separate optional WeTextProcessing runtime.
        settings = dict(language=language, num_step=self.config['steps'], normalize_text=language == 'da')
        if language in self.prompts:
            audio = self.model.generate(text=text, voice_clone_prompt=self.prompts[language], **settings)[0]
        else:
            # Placement tests need neither household audio nor an ASR download.
            audio = self.model.generate(text=text, instruct=self.config['voice_design'], **settings)[0]
        return pcm(audio)


def request_fields(value):
    if (not isinstance(value, dict) or set(value) != {'text', 'speaker'}
            or value.get('speaker') not in {'da', 'en'}
            or not isinstance(value.get('text'), str) or not value['text'].strip()
            or len(value['text']) > 2000 or '\0' in value['text']):
        raise ValueError('Bounded text and fixed language required')
    return value['text'], value['speaker']


def serve(device):
    logging.disable(logging.CRITICAL)
    engine = Engine(device, stable_voices=True)
    slot = threading.BoundedSemaphore(1)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, payload=b'', audio=False):
            self.send_response(status)
            self.send_header('Content-Type', 'audio/pcm' if audio else 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            if audio:
                for key, value in [('X-Sample-Rate', '24000'), ('X-Channels', '1'), ('X-Bit-Depth', '16')]:
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def do_GET(self):
            self.reply(200 if self.path == '/health' else 404,
                       json.dumps({'ready': True, **engine.resources()}).encode() if self.path == '/health' else b'{}')

        def do_POST(self):
            if self.path != '/v1/audio/speech':
                self.reply(404)
                return
            acquired = False
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                        or not 0 < int(lengths[0]) <= 16384
                        or self.headers.get_content_type() != 'application/json'):
                    raise ValueError('Invalid body')
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]):
                    raise ValueError('Incomplete body')
                text, language = request_fields(json.loads(raw))
                acquired = slot.acquire(blocking=False)
                if not acquired:
                    self.reply(503)
                    return
                self.reply(200, engine.synthesize(text, language), audio=True)
            except (ValueError, UnicodeError):
                self.reply(400)
            except Exception:
                self.reply(503)
            finally:
                if acquired:
                    slot.release()
    server = ThreadingHTTPServer(('0.0.0.0', 8004), Handler)
    server.daemon_threads = True
    print(json.dumps({'event': 'ready', 'model': 'omnivoice', 'device': device,
                      'languages': ['da', 'en'], 'voice_origin': 'seeded-synthetic'}), flush=True)
    server.serve_forever()


def benchmark(device):
    started = time.monotonic()
    engine = Engine(device)
    print(json.dumps({'event': 'loaded', 'seconds': round(time.monotonic()-started, 3)}), flush=True)
    for trial in range(3):
        for language, text in PHRASES:
            started = time.monotonic()
            audio = engine.synthesize(text, language)
            elapsed = time.monotonic()-started
            duration = len(audio)/48000
            print(json.dumps({'event': 'measurement', 'trial': trial,
                'language': language, 'synthesis_seconds': round(elapsed, 3),
                'audio_seconds': round(duration, 3), 'rtf': round(elapsed/duration, 3),
                'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--benchmark', action='store_true')
    action.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    if args.benchmark:
        benchmark(args.device)
    else:
        serve(args.device)


if __name__ == '__main__':
    main()
