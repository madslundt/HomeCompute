#!/usr/bin/env python3
"""Pinned local Danish/English OmniVoice inference; content is never logged."""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import resource
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
    def __init__(self, device='cpu'):
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

    def synthesize(self, text, language='da'):
        if (language not in self.config['languages'] or not isinstance(text, str)
                or not text.strip() or len(text) > self.config['maximum_input_characters']):
            raise ValueError('Unsupported language or text')
        self.torch.manual_seed(self.config['seed'])
        settings = dict(language=language, num_step=self.config['steps'], normalize_text=True)
        # Initial placement tests use a reproducible synthetic voice, never a
        # household recording or an implicit ASR/model download.
        audio = self.model.generate(text=text, instruct=self.config['voice_design'], **settings)[0]
        return pcm(audio)


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
    parser.add_argument('--benchmark', action='store_true', required=True)
    args = parser.parse_args()
    benchmark(args.device)


if __name__ == '__main__':
    main()
