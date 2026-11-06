#!/usr/bin/env python3
"""Retained launch checks using the actual published shell-wrapper shape."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('replacement_tests', Path(__file__).with_name('whisper-cuda-replacement-test.py'))
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)

# Sanitized command from the pinned 6b5359f recovery release's Compose render.
# The secret is only a mounted path; no credential is present in this fixture.
COMMAND = r'''python3 /opt/homecompute/model-cache-integrity.py verify --cache-root /var/cache/huggingface \
  --repo-id openai/whisper-large-v3 --revision 06f233fe06e710322aca913c1bc4249a0d71fce1 \
  --manifest /run/accepted-whisper-large-v3-cache.json
export VLLM_API_KEY="$$(cat /run/secrets/vllm_api_key)"
exec vllm serve openai/whisper-large-v3 --revision 06f233fe06e710322aca913c1bc4249a0d71fce1 \
  --served-model-name whisper-large-v3 --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.08 --max-num-seqs 1 --max-num-batched-tokens 2048 --enforce-eager \
  --no-enable-log-requests --disable-uvicorn-access-log
'''


class RetainedCommandTests(unittest.TestCase):
    def setUp(self):
        BASE.ReplacementTests.setUp(self)
        self.declared = {'command': [COMMAND], 'entrypoint': ['/bin/sh', '-ec'],
                         'mem_limit': str(12 * 1024**3)}
        self.primary = self.api.inspect('vllm')
        self.primary['Config'] = {'Cmd': [COMMAND.replace('$$', '$')], 'Entrypoint': ['/bin/sh', '-ec']}
        inspect = self.api.inspect
        self.api.inspect = lambda name: copy.deepcopy(self.primary) if name == 'vllm' else inspect(name)
        self.render_calls = []
        def render(*arguments):
            self.render_calls.append(arguments)
            return SimpleNamespace(stdout=json.dumps({'services': {'whisper-primary': self.declared}}))
        self.api.recovery.compose = render

    def assert_refused_before_mutation(self):
        with self.assertRaisesRegex(ValueError, 'Retained vLLM'):
            BASE.REPLACE.replace(self.api, BASE.NEW_REVISION)
        self.assertFalse(self.api.gates)
        self.assertNotIn('stop-old', self.api.events)
        self.assertFalse((self.api.STATE / BASE.REPLACE.JOURNAL).exists())

    def test_actual_shell_wrapper_matches_rendered_source_and_replaces(self):
        result = BASE.REPLACE.replace(self.api, BASE.NEW_REVISION)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.render_calls, [('--profile', 'primary', 'config', '--format', 'json')])

    def test_command_drift_is_not_accepted_by_a_lookalike_reservation(self):
        original = self.primary['Config']['Cmd'][0]
        commands = (original.replace('0.08', '0.80'), original + 'echo unexpected\n',
                    original + '--gpu-memory-utilization 0.08\n', original.replace('$(cat', '$$(cat'))
        for index, command in enumerate(commands):
            with self.subTest(case=index):
                self.primary['Config']['Cmd'] = [command]
                self.assert_refused_before_mutation()

    def test_entrypoint_or_argv_shape_drift_is_rejected(self):
        for config in ({'Entrypoint': ['/bin/bash', '-ec'], 'Cmd': [COMMAND.replace('$$', '$')]},
                       {'Entrypoint': ['/bin/sh', '-ec'], 'Cmd': ['model', '--gpu-memory-utilization', '0.08']},
                       {'Entrypoint': ['/bin/sh', '-ec'], 'Cmd': []}):
            with self.subTest(entrypoint=config['Entrypoint'], argument_count=len(config['Cmd'])):
                self.primary['Config'] = config
                self.assert_refused_before_mutation()

    def test_actual_and_source_memory_limits_remain_bounded(self):
        self.primary['HostConfig']['Memory'] = 13 * 1024**3
        self.assert_refused_before_mutation()
        self.primary['HostConfig']['Memory'] = 12 * 1024**3
        self.declared['mem_limit'] = str(13 * 1024**3)
        self.assert_refused_before_mutation()


if __name__ == '__main__':
    unittest.main(verbosity=2)
