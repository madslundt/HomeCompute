#!/usr/bin/env python3
"""Payload-free transition progress and validated text-engine queue metrics."""
from __future__ import annotations

import math
import re
import threading


def text_requests(metrics: str) -> dict | None:
    samples = re.findall(r'^vllm:num_requests_(running|waiting)(?:\{[^}]*\})?\s+(\S+)$',
                         metrics, re.M)
    if {name for name, _ in samples} != {'running', 'waiting'}:
        return None
    result = {'running': 0, 'waiting': 0}
    for name, value in samples:
        try:
            number = float(value)
        except ValueError:
            return None
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            return None
        result[name] += int(number)
    return result


class Transition:
    def __init__(self, clock):
        self.clock, self.lock = clock, threading.Lock()
        self.value = None

    def begin(self, action: str, state: dict) -> None:
        phase = ('waiting_workflows' if state['text_running'] else
                 'stopping_comfyui' if state['comfy_running'] else 'starting_comfyui') if action == 'start' else (
            'stopping_comfyui' if state['comfy_running'] else 'starting_text')
        with self.lock:
            self.started = self.phase_started = self.clock()
            self.value = dict(action=action, target_mode=state.get('target_mode', 'image' if action == 'start' else 'text'),
                              phase=phase, workflows=None, agent_turns=None,
                              text_requests=None, reason=None)

    def phase(self, phase: str, **counts) -> None:
        with self.lock:
            if self.value is None:
                return
            if phase != self.value['phase']:
                self.phase_started = self.clock()
                self.value.update(phase=phase, workflows=None, agent_turns=None, text_requests=None)
            self.value.update(counts)

    def reason(self, reason: str) -> None:
        with self.lock:
            if self.value is not None:
                self.value['reason'] = reason

    def clear(self) -> None:
        with self.lock:
            self.value = None

    def snapshot(self) -> dict | None:
        with self.lock:
            if self.value is None:
                return None
            result, now = dict(self.value), self.clock()
            result['elapsed_seconds'] = max(0, int(now - self.started))
            result['phase_elapsed_seconds'] = max(0, int(now - self.phase_started))
            if result['text_requests'] is not None:
                result['text_requests'] = dict(result['text_requests'])
            return result
