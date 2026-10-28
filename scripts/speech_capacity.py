#!/usr/bin/env python3
"""Exact speech budget candidate and fail-closed qualification primitives."""
from __future__ import annotations

from copy import deepcopy
import math
import re
import shlex

SERVICES = {'hviske': 'hviske-primary', 'plapre': 'plapre-primary'}
CONTAINERS = {'hviske': 'gb10-hviske-primary', 'plapre': 'homecompute-plapre-plapre-primary-1'}
BUDGETS = {'baseline': {'hviske': '0.10', 'plapre': '0.06'},
           'candidate': {'hviske': '0.05', 'plapre': '0.05'}}
FAILURE_PREFIX = 'SPEECH_CAPACITY_FAILURE '
FAILURE_STAGES = {'release', 'inspect', 'approval', 'render', 'image', 'baseline', 'candidate', 'operation', 'startup'}
FAILURE_REASONS = {'release-untrusted', 'inspect-unavailable', 'baseline-unhealthy', 'approval-required',
                   'protected-path', 'render-failed', 'service-missing', 'image-unavailable',
                   'project-mismatch', 'image-mismatch', 'command-mismatch', 'budget-unrecognized',
                   'guard-or-operation', 'startup-memory', 'startup-cuda-oom', 'startup-host-oom', 'startup-identity', 'startup-permission',
                   'startup-command', 'startup-mount', 'startup-image', 'startup-port', 'startup-health-timeout', 'startup-container-exit', 'startup-operation'}


def failure_metadata(value) -> dict | None:
    """Only finite categories cross privileged subprocess/report boundaries."""
    if (not isinstance(value, dict) or set(value) != {'stage', 'service', 'reason'} or
            not isinstance(value.get('stage'), str) or not isinstance(value.get('reason'), str) or
            value.get('service') is not None and not isinstance(value.get('service'), str) or
            value.get('stage') not in FAILURE_STAGES or
            value.get('service') not in {None, *SERVICES} or value.get('reason') not in FAILURE_REASONS):
        return None
    return {key: value[key] for key in ('stage', 'service', 'reason')}


class BaselineMismatch(ValueError):
    def __init__(self, service: str, field: str):
        super().__init__(f'{service} actual {field} differs from the rendered source baseline')
        self.service, self.field = service, field


def activation_approved(key: str, environment: str) -> None:
    """A running baseline is not permission to bypass a revoked runtime gate."""
    if key not in SERVICES:
        raise ValueError('Unknown speech service')
    setting, allowed = (
        ('HVISKE_LICENSE_DECISION', {'personal-noncommercial', 'syvai-commercial-license-approved'})
        if key == 'hviske' else ('PLAPRE_VOICE_APPROVAL', {'approved-household-use'}))
    values = []
    for line in environment.splitlines():
        name, separator, value = line.partition('=')
        if separator and name.strip() == setting:
            values.append(shlex.split(value, comments=True))
    if len(values) != 1 or len(values[0]) != 1 or values[0][0] not in allowed:
        raise ValueError('Existing speech activation approval is required: ' + key)


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


def engine_command(rendered: list[str] | None) -> list[str] | None:
    """Project Compose's serialized literal dollars to the Engine API command.

    `compose config` preserves $$ so its output can be used as Compose input.
    Container Config.Cmd contains the single-dollar value after that boundary.
    Project the expected declaration once; never normalize the observed value.
    All quotes, whitespace, flags, model pins and authentication paths stay exact.
    """
    if rendered is None:
        return None
    if not isinstance(rendered, list) or any(not isinstance(word, str) for word in rendered):
        raise ValueError('A rendered command must be an argument list')
    return [word.replace('$$', '$') for word in rendered]


def verify_baseline(original: dict, running: dict) -> None:
    for key, service in SERVICES.items():
        expected = original[key]['services'][service]
        for field in ('image', 'command'):
            declared = engine_command(expected[field]) if field == 'command' else expected[field]
            if running[key][field] != declared:
                raise BaselineMismatch(key, field)


def activate(start) -> None:
    """The failed replacement also needs rollback: Compose may have stopped it."""
    attempted = []
    try:
        for key in SERVICES:
            attempted.append(key)
            start(key, 'candidate')
    except BaseException as original:
        failed = []
        for key in reversed(attempted):
            try:
                start(key, 'baseline')
            except BaseException:
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
