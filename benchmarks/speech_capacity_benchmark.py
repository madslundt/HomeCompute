#!/usr/bin/env python3
"""Metadata-only concurrent public-Danish ASR/TTS and LLM capacity qualification.

This runs bounded speech requests, never changes a service, and never downloads
data. The model-local TTS timing is not an end-to-end Home Assistant voice result.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from speech_capacity import CONTAINERS, capacity_result, compare

FLEURS_REVISION = '168de341b3db6859a9bac1c50a2ef5e3b47647e0'
TEXTS = ['Dette er en syntetisk prøve af tale.', 'Lyset i det fiktive køkken er tændt.',
         'Den næste test starter om fem minutter.', 'Det er en rolig dag i det fiktive testhus.']
PROBE = r'''
import base64, http.client, json, re, sys, time
from pathlib import Path
value=json.load(sys.stdin)
def distance(a,b):
 previous=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  current=[i]
  for j,y in enumerate(b,1): current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
  previous=current
 return previous[-1]
try:
 started=time.perf_counter()
 connection=http.client.HTTPConnection('127.0.0.1',8000 if value['kind']=='asr' else 8004,timeout=120)
 if value['kind']=='asr':
  boundary='homecompute-public-capacity-fixture'
  body=b''
  for k,v in {'model':'stt-danish','language':'da','temperature':'0','response_format':'json'}.items():
   body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
  body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
  body+=base64.b64decode(value['audio'])+f'\r\n--{boundary}--\r\n'.encode()
  headers={'Content-Type':'multipart/form-data; boundary='+boundary,
           'Authorization':'Bearer '+Path('/run/secrets/vllm_api_key').read_text().strip()}
  connection.request('POST','/v1/audio/transcriptions',body,headers)
 else:
  body=json.dumps({'text':value['text'],'speaker':'tor'}).encode()
  connection.request('POST','/v1/audio/speech',body,{'Content-Type':'application/json'})
 response=connection.getresponse()
 first=response.read(2)
 first_seconds=time.perf_counter()-started
 result=first+response.read(48000001)
 elapsed=time.perf_counter()-started
 if response.status!=200 or len(result)>48000000: raise ValueError('response_refused')
 if value['kind']=='asr':
  actual=json.loads(result)['text']
  words=lambda s: re.findall(r'\w+',s.casefold())
  reference=words(value['reference'])
  report={'errors':distance(reference,words(actual)),'words':len(reference),
          'elapsed_seconds':elapsed,'rtf':elapsed/value['seconds']}
 else:
  if (response.getheader('Content-Type','').partition(';')[0]!='audio/pcm' or
      response.getheader('X-Sample-Rate')!='24000' or response.getheader('X-Channels')!='1' or
      response.getheader('X-Bit-Depth')!='16' or not result or len(result)%2):
   raise ValueError('pcm_contract_refused')
  # The approved adapter shortens audio by 1.20x without changing the voice.
  seconds=len(result)/(24000*2*1.20)
  report={'elapsed_seconds':elapsed,'first_audio_seconds':first_seconds,
          'rtf':elapsed/seconds,'audio_seconds_after_tempo':seconds,'pcm_bytes':len(result)}
 report['error']=None
 print(json.dumps(report))
except Exception as error:
 print(json.dumps({'error':type(error).__name__}))
finally:
 if 'connection' in locals(): connection.close()
'''


def fixtures(root: Path) -> list[dict]:
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('source') != 'google/fleurs' or manifest.get('revision') != FLEURS_REVISION:
        raise ValueError('The pinned public FLEURS corpus is required')
    result = []
    for row in manifest['fixtures']:
        if row['language'] != 'da' or row['condition'] not in {'clean', 'noise10db'}:
            continue
        name = row['file']
        if Path(name).name != name or (root / name).is_symlink():
            raise ValueError('Unsafe corpus audio path')
        audio = (root / name).read_bytes()
        if len(audio) > 10_000_000 or hashlib.sha256(audio).hexdigest() != row['sha256']:
            raise ValueError('Public audio checksum or bound failed')
        with wave.open(str(root / name)) as source:
            seconds = source.getnframes() / source.getframerate()
        if not 0 < seconds <= 60 or not math.isclose(seconds, row['seconds'], abs_tol=.01):
            raise ValueError('Public audio duration differs or exceeds the serving limit')
        result.append({'kind': 'asr', 'id': row['id'], 'condition': row['condition'],
                       'reference': row['reference'], 'seconds': seconds,
                       'audio': base64.b64encode(audio).decode(), 'sha256': row['sha256']})
    if len(result) != 18:
        raise ValueError('Expected 12 clean and 6 noisy Danish fixtures')
    return result


def probe(value: dict) -> dict:
    key = 'hviske' if value['kind'] == 'asr' else 'plapre'
    python = 'python3' if key == 'hviske' else '/opt/plapre/venv/bin/python'
    result = subprocess.run(['docker', 'exec', '-i', CONTAINERS[key], python, '-c', PROBE],
                            input=json.dumps(value), capture_output=True, text=True, timeout=150)
    if result.returncode:
        return {'error': 'probe_process_failed'}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {'error': 'probe_metadata_invalid'}


def memory() -> dict:
    values = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB$',
                            Path('/proc/meminfo').read_text(), re.M))
    with urlopen('http://127.0.0.1:18300/metrics', timeout=3) as response:
        metrics = response.read(4 * 1024 * 1024 + 1).decode()
    if len(metrics) > 4 * 1024 * 1024:
        raise ValueError('Oversized runtime metrics')
    active = re.findall(r'^vllm:num_requests_running\{[^\n]*\}\s+([0-9.e+]+)$', metrics, re.M)
    if not active or any(not float(value).is_integer() for value in active):
        raise ValueError('Active LLM request count unavailable')
    return {'total_bytes': int(values['MemTotal']) * 1024,
            'available_bytes': int(values['MemAvailable']) * 1024,
            'llm_running': sum(int(float(value)) for value in active)}


def runtime() -> dict:
    images, budgets, contracts = {}, {}, {}
    for key, name in CONTAINERS.items():
        value = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        images[key] = value['Image']
        command = value['Config']['Cmd']
        if key == 'hviske':
            found = re.findall(r'--gpu-memory-utilization\s+[\"\x27]?([0-9.]+)', command[0])
            if len(found) != 1:
                raise ValueError('Actual Hviske budget is unavailable')
            budgets[key] = found[0]
            normalized = [re.sub(r'(--gpu-memory-utilization\s+[\"\x27]?)[0-9.]+',
                                 r'\1CAPACITY_BUDGET', command[0])]
        else:
            if command.count('--gpu-mem') != 1:
                raise ValueError('Actual Plapre budget is unavailable')
            budgets[key] = command[command.index('--gpu-mem') + 1]
            normalized = list(command)
            normalized[command.index('--gpu-mem') + 1] = 'CAPACITY_BUDGET'
        contract = {'command': normalized, 'entrypoint': value['Config']['Entrypoint'],
                    'environment': dict(item.split('=', 1) for item in value['Config']['Env']),
                    'speaker': 'tor', 'tempo': 1.20}
        contracts[key] = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    return {'images': images, 'budgets': budgets, 'contract_sha256': contracts}


def summary(rows: list[dict]) -> dict:
    good = [row for row in rows if row.get('error') is None]
    result = {'trials': len(rows), 'failures': len(rows) - len(good)}
    for key in ['elapsed_seconds', 'rtf', 'first_audio_seconds']:
        values = sorted(row[key] for row in good if key in row)
        if values:
            result['p95_' + key] = values[math.ceil(.95 * len(values)) - 1]
    if good and 'words' in good[0]:
        result['wer'] = sum(row['errors'] for row in good) / max(1, sum(row['words'] for row in good))
    return result


def benchmark(corpus: Path) -> dict:
    audio = fixtures(corpus)
    launched = runtime()
    samples, errors, stop = [], [], threading.Event()
    def observe():
        while not stop.is_set():
            try:
                samples.append(memory())
            except Exception as error:
                errors.append(type(error).__name__)
            stop.wait(1)
    # Separate cold/first inference from the warm qualification trials.
    warmup = {'asr': probe(audio[0]), 'tts': probe({'kind': 'tts', 'text': TEXTS[0]})}
    if any(value.get('error') for value in warmup.values()):
        raise ValueError('Speech warmup failed')
    thread = threading.Thread(target=observe, daemon=True)
    thread.start()
    def asr():
        # Exercise the existing four-sequence contract, rather than proving a
        # smaller cache only at one request and quietly losing concurrency.
        with ThreadPoolExecutor(max_workers=4) as workers:
            return list(workers.map(probe, audio))
    def tts():
        return [probe({'kind': 'tts', 'text': text}) for text in TEXTS * 5]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            asr_result, tts_result = pool.submit(asr), pool.submit(tts)
            asr_rows, tts_rows = asr_result.result(), tts_result.result()
    finally:
        stop.set()
        thread.join(5)
    capacity = capacity_result(samples)
    if runtime() != launched:
        raise ValueError('Speech runtime changed during qualification')
    if errors:
        capacity['passed'] = False
    asr_summary, tts_summary = summary(asr_rows), summary(tts_rows)
    return {'schema_version': 1, 'capacity': capacity, 'observation_failures': len(errors),
            'runtime': launched, 'asr_concurrency': 4, 'tts_concurrency': 1,
            'asr': asr_summary, 'tts_model_local': tts_summary,
            'warmup': warmup, 'sample_minimum_available_bytes': min(
                (row['available_bytes'] for row in samples), default=None),
            'corpus_revision': FLEURS_REVISION,
            'corpus_sha256': hashlib.sha256(json.dumps(
                [row['sha256'] for row in audio], sort_keys=True).encode()).hexdigest(),
            'asr_latency_pass': asr_summary.get('p95_rtf', math.inf) <= .5 and not asr_summary['failures'],
            'tts_model_local_latency_pass': tts_summary.get('p95_rtf', math.inf) <= .5
                and tts_summary.get('p95_first_audio_seconds', math.inf) <= .75 and not tts_summary['failures'],
            'accepted': False,
            'remaining_gates': ['Compare ASR accuracy against the same-fixture baseline',
                                'Verify the complete Wyoming/HA voice path and recovery',
                                'Reconcile the selected budget into canonical source and runtime env']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--label', required=True, choices=['baseline', 'candidate'])
    parser.add_argument('--baseline', type=Path, help='Required baseline receipt for candidate comparison')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('Run as root on home-spark')
    if args.label == 'candidate' and args.baseline is None:
        raise ValueError('Candidate qualification requires the same-corpus baseline receipt')
    result = benchmark(args.corpus)
    result['label'] = args.label
    result['finished_unix'] = time.time()
    if args.label == 'candidate':
        result.update(compare(json.loads(args.baseline.read_text()), result))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as target:
        json.dump(result, target, sort_keys=True, indent=2)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
