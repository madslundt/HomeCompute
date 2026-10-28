#!/usr/bin/env python3
"""Exact speech budget candidate and fail-closed qualification primitives."""
from __future__ import annotations

from copy import deepcopy
import math
import re

SERVICES = {'hviske': 'hviske-primary', 'plapre': 'plapre-primary'}
CONTAINERS = {'hviske': 'gb10-hviske-primary', 'plapre': 'homecompute-plapre-plapre-primary-1'}
BUDGETS = {'baseline': {'hviske': '0.10', 'plapre': '0.06'},
           'candidate': {'hviske': '0.05', 'plapre': '0.05'}}


def candidate(original: dict) -> dict:
    result = deepcopy(original)
    for key, service in SERVICES.items():
        command = result[key]['services'][service]['command']
        if key == 'hviske':
            if not isinstance(command, list) or len(command) != 1 or not isinstance(command[0], str):
                raise ValueError('Unrecognized Hviske launch command')
            pattern = r'(--gpu-memory-utilization\s+)([\"\x27]?)0\.10\2(?=\s|$)'
            if len(re.findall(r'--gpu-memory-utilization(?=\s|$)', command[0])) != 1:
                raise ValueError('Unexpected Hviske GPU budget flags')
            command[0], count = re.subn(pattern, r'\g<1>\g<2>0.05\g<2>', command[0])
            if count != 1:
                raise ValueError('Hviske baseline must have the known 0.10 GPU budget')
        else:
            if not isinstance(command, list) or command.count('--gpu-mem') != 1:
                raise ValueError('Unrecognized Plapre GPU budget flags')
            index = command.index('--gpu-mem') + 1
            if index >= len(command) or command[index] != '0.06':
                raise ValueError('Plapre baseline must have the known 0.06 GPU budget')
            command[index] = '0.05'
    return result


def verify_baseline(original: dict, running: dict) -> None:
    for key, service in SERVICES.items():
        expected = original[key]['services'][service]
        if any(running[key][field] != expected[field] for field in ('image', 'command')):
            raise ValueError(f'{key} actual image/command differs from the rendered source baseline')


def activate(start) -> None:
    """The failed replacement also needs rollback: Compose may have stopped it."""
    attempted = []
    try:
        for key in SERVICES:
            attempted.append(key)
            start(key, 'candidate')
    except Exception as original:
        failed = []
        for key in reversed(attempted):
            try:
                start(key, 'baseline')
            except Exception:
                failed.append(key)
        if failed:
            raise RuntimeError('Speech capacity activation and rollback failed: ' + ', '.join(failed)) from original
        raise


def capacity_result(samples: list[dict]) -> dict:
    ratios, active = [], []
    for sample in samples:
        values = [sample.get(k) for k in ('total_bytes', 'available_bytes', 'llm_running')]
        if any(type(value) is not int for value in values):
            raise ValueError('Capacity observations require integer metrics')
        total, available, running = values
        if total <= 0 or not 0 <= available <= total or running < 0:
            raise ValueError('Invalid capacity observations')
        ratios.append(available / total)
        active.append(running)
    return {'passed': bool(samples) and min(ratios) >= 0.10 and min(active) >= 1,
            'samples': len(samples), 'minimum_available_ratio': min(ratios) if ratios else None,
            'minimum_llm_running': min(active) if active else None,
            'required_available_ratio': 0.10, 'required_llm_running': 1}


def compare(baseline: dict, observed: dict) -> dict:
    """Never promote a memory win by hiding ASR quality or native speech latency."""
    if (baseline.get('label') != 'baseline' or observed.get('label') != 'candidate' or
            baseline.get('corpus_sha256') != observed.get('corpus_sha256') or
            not observed.get('corpus_sha256') or
            baseline.get('runtime', {}).get('budgets') != BUDGETS['baseline'] or
            observed.get('runtime', {}).get('budgets') != BUDGETS['candidate'] or
            baseline.get('runtime', {}).get('images') != observed.get('runtime', {}).get('images') or
            not baseline.get('runtime', {}).get('contract_sha256') or
            baseline.get('runtime', {}).get('contract_sha256') != observed.get('runtime', {}).get('contract_sha256') or
            any(row.get('asr_concurrency') != 4 or row.get('tts_concurrency') != 1
                for row in [baseline, observed])):
        raise ValueError('Baseline/candidate corpus, runtime images or actual budgets differ')
    before, after = baseline.get('asr', {}), observed.get('asr', {})
    if any(type(row.get('wer')) not in (float, int) or not 0 <= row['wer'] <= 10
           or row.get('failures') != 0 or row.get('trials', 0) < 18 for row in [before, after]):
        raise ValueError('Complete ASR baseline and candidate evidence is required')
    quality = after['wer'] <= before['wer'] + .01
    latency = []
    for name, count, metrics in [('asr', 18, ['p95_rtf', 'p95_elapsed_seconds']),
                                 ('tts_model_local', 20, ['p95_rtf', 'p95_elapsed_seconds', 'p95_first_audio_seconds'])]:
        for row in [baseline.get(name, {}), observed.get(name, {})]:
            if row.get('failures') != 0 or row.get('trials', 0) < count:
                raise ValueError('Complete error-free speech baseline and candidate evidence is required')
        for metric in metrics:
            old, new = baseline[name].get(metric), observed[name].get(metric)
            if any(type(value) not in (float, int) or not math.isfinite(value) or value <= 0 for value in [old, new]):
                raise ValueError('Finite measured speech latency is required')
            # Ten percent, with a 100 ms timer margin (0.01 for RTF), is the
            # explicit no-regression gate; absolute voice SLOs remain separate.
            maximum = max(old * 1.10, old + (.01 if metric == 'p95_rtf' else .10))
            latency.append({'service': name, 'metric': metric, 'baseline': old,
                            'observed': new, 'maximum': maximum, 'passed': new <= maximum})
    no_regression = all(row['passed'] for row in latency)
    passed = quality and no_regression and observed.get('capacity', {}).get('passed') is True
    return {'capacity_qualified': passed, 'latency_regression_pass': no_regression,
            'latency_comparison': latency,
            'asr_quality_pass': quality, 'maximum_wer_regression': .01,
            'actual_wer_regression': after['wer'] - before['wer'], 'accepted': False}
