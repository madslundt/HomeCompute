#!/usr/bin/env python3
"""Private model/windowing comparison, run inside an existing Daythread backend.

Only reads recording APIs. Results remain in a fresh private directory and no
application transcript/recap is saved. Imports use the installed consumer's
preprocessing so both model passes receive identical audio.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import time
import wave


def clip(data, start, seconds):
    with wave.open(io.BytesIO(data), 'rb') as source:
        source.setpos(round(start * source.getframerate()))
        pcm = source.readframes(round(seconds * source.getframerate()))
        params = source.getparams()
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setparams(params); target.writeframes(pcm)
    return output.getvalue()


async def main(args):
    import httpx
    from omi_local.configuration import Configuration
    from omi_local.inference import Inference
    from omi_local.settings import Settings
    from omi_local.audio import amplify_quiet_audio, transcription_chunks
    from omi_local.transcription_filter import filter_transcription_audio

    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    initial = Settings(); settings = Configuration(initial).startup(initial)
    route = settings.stt_route('auto')
    config = settings.model_copy(update={'stt_model': args.model, 'stt_routes': {},
        'stt_base_url': route.base_url, 'stt_api_key': route.token,
        'stt_segment_timestamps': False, 'stt_enhancement_mode': 'off', 'inference_timeout_seconds': 900})
    async with httpx.AsyncClient(follow_redirects=False, timeout=900) as client:
        response = await client.get('http://127.0.0.1:8787/v1/transcripts',
                                    params={'day': args.day, 'limit': 200}); response.raise_for_status()
        rows = sorted([r for r in response.json()['items']
                       if r.get('source_metadata', {}).get('recording_id') == args.recording],
                      key=lambda row: row['started_at'])
        if not rows: raise ValueError('No source parts for selected recording')
        origin = datetime.fromisoformat(rows[0]['started_at'])
        cases = []
        # Freeze request bytes and offsets, not merely the high-level settings.
        for index, row in enumerate(rows):
            response = await client.get('http://127.0.0.1:8787/v1/audio/' + row['id']); response.raise_for_status()
            source = response.content
            offset = (datetime.fromisoformat(row['started_at']) - origin).total_seconds()
            filtered = filter_transcription_audio(source, enabled=settings.stt_filter_speech)
            for span_offset, span in filtered['spans']:
                for chunk_offset, chunk in transcription_chunks(span, settings.stt_chunk_seconds,
                                                               settings.stt_boundary_search_seconds):
                    prepared, gain = amplify_quiet_audio(chunk, settings.stt_max_gain_db)
                    name = 'meeting-' + str(len(cases)) + '.wav'
                    (args.output / name).write_bytes(prepared)
                    cases.append({'name': name, 'source_id': row['id'], 'part': index,
                                  'offset': offset + span_offset + chunk_offset, 'gain_db': gain,
                                  'sha256': hashlib.sha256(prepared).hexdigest(), 'group': 'consumer-baseline'})
        whole = await client.get('http://127.0.0.1:8787/v1/recordings/' + args.recording + '/audio')
        whole.raise_for_status()
        for start in (230, 668, 683):
            for delta in (-5, 0, 5):
                name = f'raw-{start + delta}.wav'; data = clip(whole.content, start + delta, 30)
                (args.output / name).write_bytes(data)
                cases.append({'name': name, 'offset': start + delta, 'group': 'raw-shifted',
                              'sha256': hashlib.sha256(data).hexdigest(), 'repeats': 3})
        silent = io.BytesIO()
        with wave.open(silent, 'wb') as target:
            target.setparams((1, 2, 16000, 0, 'NONE', 'PCM')); target.writeframes(b'\0\0' * 16000 * 30)
        (args.output / 'silence.wav').write_bytes(silent.getvalue())
        cases.append({'name': 'silence.wav', 'group': 'no-speech', 'repeats': 3,
                      'sha256': hashlib.sha256(silent.getvalue()).hexdigest()})
        manifest = {'recording_id': args.recording, 'model': args.model,
                    'consumer_settings': {k: getattr(settings, k) for k in
                        ('stt_chunk_seconds', 'stt_filter_speech', 'stt_max_gain_db', 'stt_boundary_search_seconds')},
                    'cases': cases}
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        await evaluate(args.output, config, client, Inference)


async def evaluate(directory, config, client, Inference):
    manifest = json.loads((directory / 'manifest.json').read_text())
    inference = Inference(config, client)
    # Use the single-request path: bytes are already filtered/gained/windowed.
    with (directory / ('results-' + config.stt_model + '.jsonl')).open('x') as results:
        for case in manifest['cases']:
            data = (directory / case['name']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == case['sha256']
            for repeat in range(case.get('repeats', 1)):
                start = time.perf_counter()
                result = await inference._transcription(data, config.stt_route('auto'),
                                                       config.model_copy(update={'stt_segment_timestamps': True}))
                results.write(json.dumps({'case': case['name'], 'repeat': repeat, 'model': config.stt_model,
                                          'seconds': time.perf_counter() - start, 'result': result}, ensure_ascii=False) + '\n')
                results.flush()
    print(json.dumps({'model': config.stt_model, 'cases': len(manifest['cases']), 'output': str(directory)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recording', required=True)
    parser.add_argument('--day', required=True)
    parser.add_argument('--model', choices=('whisper-turbo', 'whisper'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    if args.replay:
        async def replay():
            import httpx
            from omi_local.settings import Settings
            from omi_local.configuration import Configuration
            from omi_local.inference import Inference
            s = Settings(); s = Configuration(s).startup(s)
            s = s.model_copy(update={'stt_model': args.model, 'stt_routes': {}})
            async with httpx.AsyncClient(timeout=900, follow_redirects=False) as client:
                await evaluate(args.output, s, client, Inference)
        asyncio.run(replay())
    else: asyncio.run(main(args))
