#!/usr/bin/env python3
"""Bounded, content-free startup evidence; never return Docker output lines."""
from __future__ import annotations
import json
import math
import re
import subprocess
import os
import selectors
import time

ATTEMPT_PREFIX = 'SPEECH_CAPACITY_ATTEMPT '
STARTUP_REASONS = {'startup-memory', 'startup-cuda-oom', 'startup-host-oom', 'startup-identity', 'startup-permission',
                   'startup-command', 'startup-mount', 'startup-image', 'startup-port',
                   'startup-health-timeout', 'startup-container-exit', 'startup-operation'}
NUMBERS = {
    'model_loading_gib': r'Model loading took\s+([0-9]+(?:\.[0-9]+)?)\s+GiB',
    'available_kv_gib': r'Available KV cache memory:\s*([0-9]+(?:\.[0-9]+)?)\s*GiB',
    'cuda_graph_gib': r'Graph capturing finished[^\n]{0,100}took\s+([0-9]+(?:\.[0-9]+)?)\s+GiB',
    'kv_tokens': r'GPU KV cache size:\s*([0-9,]+)\s*tokens',
}
# Anchor on exception/error forms, never on Namespace/config echoes that may
# contain arbitrary secret strings with these words embedded in them.
CATEGORIES = {
    'startup-mount': r'(?:^|\n)(?:Error response from daemon: )?(?:invalid mount config|invalid mount path|error while mounting volume)',
    'startup-image': r'(?:^|\n)(?:Error response from daemon: )?(?:No such image|pull access denied)',
    'startup-port': r'(?:^|\n)(?:Error response from daemon: )?[^\n]{0,100}(?:port is already allocated|address already in use)',
    'startup-memory': r'(?:^|\n)(?:[^\n]{0,80}\b)?(?:ValueError|RuntimeError): (?:Free memory on device[^\n]{0,300}less than desired|No available memory for the cache blocks|The model.s max seq len[^\n]{0,300}larger than the maximum number of tokens|Insufficient[^\n]{0,100}KV)',
    'startup-cuda-oom': r'(?:^|\n)(?:[^\n]{0,80}\b)?(?:torch\.OutOfMemoryError|torch\.cuda\.OutOfMemoryError|RuntimeError): CUDA out of memory',
    'startup-permission': r'(?:^|\n)(?:[^\n]{0,80}\b)?PermissionError: \[Errno (?:1|13)\]',
    'startup-command': r'(?:^|\n)(?:[^\n]{0,80}\b)?(?:vllm|api_server\.py|server\.py): error: (?:unrecognized arguments|argument [^\n]{1,80}: invalid choice)',
}


def attempt_metadata(value):
    fields = {'plan_sha256', 'rollback_state', 'service', 'reason', 'attempt_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or
            not isinstance(value.get('plan_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['plan_sha256']) or
            not isinstance(value.get('attempt_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['attempt_sha256']) or
            value.get('rollback_state') not in ('restored', 'incomplete') or
            value.get('service') not in (None, 'hviske', 'plapre') or
            value.get('reason') not in (None, *STARTUP_REASONS)):
        return None
    return {key: value[key] for key in sorted(fields)}


def numeric_fields(text):
    result = {}
    for field, pattern in NUMBERS.items():
        values = []
        for match in re.finditer(pattern, text):
            number = float(match.group(1).replace(',', ''))
            if math.isfinite(number) and 0 <= number <= 1e12:
                values.append(number)
        if values: result[field] = values[-8:]
    return result


def classify(text, state=None):
    if state and state.get('oom_killed') is True: return 'startup-host-oom'
    for reason, pattern in CATEGORIES.items():
        if re.search(pattern, text): return reason
    if state and state.get('running') is False: return 'startup-container-exit'
    if state and state.get('health') == 'unhealthy': return 'startup-health-timeout'
    return 'startup-operation'


def bounded_output(arguments, timeout=15, maximum=262144):
    # Read a finite suffix without writing private raw output to disk. Kill only
    # the read-only Docker CLI on deadline/4 MiB; never signal its containers.
    child = None
    try:
        child = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        retained = bytearray(); total = 0; deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: return None
                if not selector.select(remaining): return None
                part = os.read(child.stdout.fileno(), 65536)
                if not part: break
                total += len(part)
                if total > 4194304: return None
                retained.extend(part)
                if len(retained) > maximum: del retained[:-maximum]
        return child.wait(timeout=max(.01, deadline-time.monotonic())), retained.decode('utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if child is not None:
            if child.poll() is None: child.kill()
            child.wait()
            if child.stdout: child.stdout.close()


def snapshot(name, expected):
    result = {'available': False, 'state': None, 'numeric_memory': {}, 'reason': 'startup-operation', 'image_matches': False, 'command_matches': False}
    response = bounded_output(['docker', 'inspect', name], maximum=1048576)
    if response is None or response[0] != 0: return result
    try:
        values = json.loads(response[1])
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict): return result
        value = values[0]
        if not isinstance(value.get('Config'), dict) or not isinstance(value.get('State'), dict): return result
        if any(type(value['State'].get(field)) is not bool for field in ('Running', 'OOMKilled')): return result
        result['image_matches'] = value.get('Image') == expected['image']
        declared = expected.get('command')
        command = [word.replace('$$', '$') for word in declared] if isinstance(declared, list) else declared
        result['command_matches'] = value.get('Config', {}).get('Cmd') == command
        if not result['image_matches'] or not result['command_matches']:
            result['reason'] = 'startup-identity'
            return result
        state = value['State']; health_data = state.get('Health')
        health = health_data.get('Status') if isinstance(health_data, dict) else None
        code = state.get('ExitCode')
        result.update(available=True, state={
            'running': state.get('Running') is True,
            'oom_killed': state.get('OOMKilled') is True,
            'exit_code': code if type(code) is int and 0 <= code <= 255 else None,
            'health': health if health in ('healthy', 'unhealthy', 'starting') else None})
        # The exact inspected ID prevents a concurrent replacement changing which
        # container's output is read. Never persist the output or arbitrary Error.
        identifier = value.get('Id', '')
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier): return result
        logs = bounded_output(['docker', 'logs', '--tail', '200', identifier])
        text = logs[1] if logs and logs[0] == 0 else ''
        result['numeric_memory'] = numeric_fields(text)
        result['reason'] = classify(text, result['state'])
        return result
    except (ValueError, KeyError, TypeError, IndexError):
        return result


def bounded_process(arguments, timeout=60):
    """Separate stdout JSON and finite stderr; cap total bytes and reap the CLI."""
    child = None
    try:
        child = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, errors = bytearray(), bytearray(); total = 0; deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0: return None
                ready = selector.select(remaining)
                if not ready: return None
                for key, _ in ready:
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        selector.unregister(key.fileobj); continue
                    total += len(part)
                    if total > 4194304: return None
                    if key.data == 'stdout':
                        output.extend(part)
                        if len(output) > 1048576: return None  # Never parse truncated JSON.
                    else:
                        errors.extend(part)
                        if len(errors) > 262144: del errors[:-262144]
        code = child.wait(timeout=max(.01, deadline-time.monotonic()))
        return code, output.decode('utf-8', errors='replace'), errors.decode('utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if child is not None:
            if child.poll() is None: child.kill()
            child.wait()
            if child.stdout: child.stdout.close()
            if child.stderr: child.stderr.close()
