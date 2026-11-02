#!/usr/bin/env python3
"""Read-only local Whisper evaluation using the pinned public speech corpus.

Run inside Daythread's existing backend, supplying the privately staged corpus.
Raw results stay in a fresh private file; stdout contains aggregate metrics only.
The two quiet copies, overlap and silence cases are synthetic stress cases.
"""
from __future__ import annotations
import argparse
import array
import asyncio
import collections
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import wave
from urllib.parse import urlsplit


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (word != actual)))
        previous = current
    return previous[-1]


def samples(data):
    with wave.open(io.BytesIO(data)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        pcm = array.array('h', audio.readframes(audio.getnframes()))
    if sys.byteorder != 'little': pcm.byteswap()
    return pcm


def encode(pcm):
    if sys.byteorder != 'little': pcm.byteswap()
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'PCM')); audio.writeframes(pcm.tobytes())
    return output.getvalue()


def fixtures(root):
    raw = (root / 'manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '2e32b597348dedc849e4e67afb5b1cf86ff8ea27c85844a34c431300c4234aa2'
    manifest = json.loads(raw)
    assert manifest['source'] == 'google/fleurs'
    assert manifest['revision'] == '168de341b3db6859a9bac1c50a2ef5e3b47647e0'
    cases = []
    for row in manifest['fixtures']:
        assert Path(row['file']).name == row['file']
        assert not (root / row['file']).is_symlink()
        data = (root / row['file']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256']
        assert 0 < len(samples(data)) <= 60 * 16000
        cases.append({**row, 'audio': data})
    clean = [row for row in cases if row['language'] == 'da' and row['condition'] == 'clean'][:2]
    for row in clean:
        data = encode(array.array('h', (round(x * 0.1) for x in samples(row['audio']))))
        cases.append({**row, 'id': row['id'] + '-quiet20db', 'condition': 'quiet20db', 'audio': data})
    english = next(row for row in cases if row['language'] == 'en' and row['condition'] == 'clean')
    left, right = samples(clean[0]['audio']), samples(english['audio'])
    mixed = array.array('h', (round(((left[i] if i < len(left) else 0) +
                                   (right[i] if i < len(right) else 0)) * 0.5)
                             for i in range(max(len(left), len(right)))))
    cases.append({'id': 'synthetic-da-en-overlap', 'language': 'mixed', 'condition': 'overlap',
                  'reference': None, 'audio': encode(mixed)})
    cases.append({'id': 'digital-silence', 'language': 'none', 'condition': 'silence',
                  'reference': '', 'audio': encode(array.array('h', [0]) * 16000 * 10)})
    return cases


async def main(args):
    import httpx
    from omi_local.configuration import Configuration
    from omi_local.settings import Settings
    initial = Settings(); settings = Configuration(initial).startup(initial)
    route = settings.stt_route('auto'); token = route.token.get_secret_value()
    origin = urlsplit(route.base_url)
    assert origin.scheme == 'https' and not origin.username and not origin.password
    assert not origin.query and not origin.fragment
    assert origin.hostname in {'ai.home.arpa', 'home-core.tail479ad.ts.net'}
    assert origin.port in {None, 443, 8443}
    groups = collections.defaultdict(list)
    os.umask(0o077)
    with args.output.open('x') as output:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            for row in fixtures(args.corpus):
                started = time.perf_counter()
                response = await client.post(route.base_url + '/audio/transcriptions',
                    headers={'Authorization': 'Bearer ' + token} if token else {},
                    files={'file': ('audio.wav', row['audio'], 'audio/wav')},
                    data={'model': 'whisper', 'response_format': args.response_format,
                          'temperature': str(args.temperature), 'seed': '0'})
                response.raise_for_status(); result = response.json()
                words = lambda text: re.findall(r'\w+', text.casefold())
                reference = words(row['reference']) if row['reference'] is not None else []
                errors = distance(reference, words(result['text'])) if row['reference'] is not None else None
                report = {k: row[k] for k in ('id', 'language', 'condition', 'reference')}
                report.update(seconds=time.perf_counter() - started, reference_words=len(reference),
                              errors=errors, recognized_words=len(words(result['text'])), result=result)
                output.write(json.dumps(report, ensure_ascii=False) + '\n'); output.flush()
                groups[(row['language'], row['condition'])].append(report)
    for (language, condition), rows in sorted(groups.items()):
        count = sum(row['reference_words'] for row in rows)
        print(json.dumps({'language': language, 'condition': condition, 'cases': len(rows),
                          'seconds': round(sum(row['seconds'] for row in rows), 3),
                          'wer': sum(row['errors'] for row in rows) / count if count else None,
                          'recognized_words': sum(row['recognized_words'] for row in rows)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--response-format', choices=('json', 'verbose_json'), default='json')
    parser.add_argument('--temperature', choices=(0.0, 0.2), default=0.0, type=float)
    asyncio.run(main(parser.parse_args()))
