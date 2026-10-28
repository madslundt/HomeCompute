#!/usr/bin/env python3
"""Capacity candidates preserve the serving contract and restore partial failures."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('capacity', ROOT / 'scripts/speech_capacity.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixtures():
    return {
        'hviske': {'name': 'hviske-stt', 'services': {'hviske-primary': {
            'image': 'pinned-hviske@sha256:' + 'a' * 64,
            'command': ['exec vllm serve syvai/hviske-v5.3 --revision pinned '
                        '--gpu-memory-utilization "0.10" --max-num-seqs "4"'],
            'environment': {'DO_NOT_TRACK': '1'}, 'secrets': [{'source': 'vllm_api_key'}]}}},
        'plapre': {'name': 'homecompute-plapre', 'services': {'plapre-primary': {
            'image': 'pinned-plapre', 'command': ['--host', '0.0.0.0', '--gpu-mem', '0.06',
                                               '--max-model-len', '512'],
            'environment': {'PLAPRE_TEMPO': '1.20'}}}},
    }


class CapacityTest(unittest.TestCase):
    def test_capacity_activation_preserves_existing_license_and_voice_gates(self):
        for decision in ['personal-noncommercial', 'syvai-commercial-license-approved']:
            m.activation_approved('hviske', f'HVISKE_LICENSE_DECISION="{decision}" # reviewed\n')
        m.activation_approved('plapre', 'PLAPRE_VOICE_APPROVAL=approved-household-use\n')
        for key, setting in [('hviske', 'HVISKE_LICENSE_DECISION'), ('plapre', 'PLAPRE_VOICE_APPROVAL')]:
            for invalid in ['', f'{setting}=review-required', f'{setting}=$(unsafe-command)',
                            f'{setting}=review-required\n{setting}=approved-household-use']:
                with self.assertRaises(ValueError):
                    m.activation_approved(key, invalid)

    def test_only_the_two_gpu_budgets_change(self):
        original = fixtures()
        saved = deepcopy(original)
        candidate = m.candidate(original)
        self.assertEqual(original, saved)
        expected = deepcopy(saved)
        expected['hviske']['services']['hviske-primary']['command'][0] = (
            expected['hviske']['services']['hviske-primary']['command'][0].replace('"0.10"', '"0.05"'))
        expected['plapre']['services']['plapre-primary']['command'][3] = '0.05'
        self.assertEqual(candidate, expected)

    def test_unrecognized_or_repeated_budget_is_refused_before_effects(self):
        for command in [['--gpu-mem', '0.07'], ['--gpu-mem', '0.06', '--gpu-mem', '0.06']]:
            value = fixtures()
            value['plapre']['services']['plapre-primary']['command'] = command
            with self.assertRaises(ValueError):
                m.candidate(value)
        value = fixtures()
        value['hviske']['services']['hviske-primary']['command'] = ['exec vllm serve changed']
        with self.assertRaises(ValueError):
            m.candidate(value)

    def test_receipt_requires_exact_actual_images_and_commands(self):
        original = fixtures()
        running = {key: {'image': data['services'][m.SERVICES[key]]['image'],
                         'command': data['services'][m.SERVICES[key]]['command']}
                   for key, data in original.items()}
        m.verify_baseline(original, running)
        for field in ['image', 'command']:
            changed = deepcopy(running)
            changed['hviske'][field] = 'unexpected'
            with self.assertRaisesRegex(ValueError, 'differs'):
                m.verify_baseline(original, changed)

    def test_partial_activation_rolls_back_every_started_candidate(self):
        events = []
        def start(key, profile):
            events.append((key, profile))
            if key == 'plapre' and profile == 'candidate':
                raise RuntimeError('readiness failed')
        with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
            m.activate(start)
        self.assertEqual(events, [('hviske', 'candidate'), ('plapre', 'candidate'),
                                  ('plapre', 'baseline'), ('hviske', 'baseline')])

    def test_rollback_failure_is_reported_and_does_not_skip_other_service(self):
        events = []
        def start(key, profile):
            events.append((key, profile))
            if key == 'plapre':
                raise RuntimeError('start failed')
        with self.assertRaisesRegex(RuntimeError, 'rollback failed: plapre'):
            m.activate(start)
        self.assertEqual(events[-1], ('hviske', 'baseline'))

    def test_capacity_gate_requires_headroom_during_concurrent_load(self):
        good = [dict(total_bytes=1000, available_bytes=110, llm_running=1)] * 5
        self.assertTrue(m.capacity_result(good)['passed'])
        self.assertFalse(m.capacity_result([*good, dict(total_bytes=1000, available_bytes=99, llm_running=1)])['passed'])
        self.assertFalse(m.capacity_result([dict(total_bytes=1000, available_bytes=120, llm_running=0)])['passed'])
        self.assertFalse(m.capacity_result([])['passed'])

    def test_capacity_gate_rejects_invalid_or_missing_metrics(self):
        for samples in [[{}], [dict(total_bytes=0, available_bytes=0, llm_running=1)],
                        [dict(total_bytes=1000, available_bytes=True, llm_running=1)],
                        [dict(total_bytes=1000, available_bytes=110, llm_running=-1)]]:
            with self.assertRaises(ValueError):
                m.capacity_result(samples)

    def test_memory_savings_do_not_waive_quality_latency_or_runtime_identity(self):
        before = {'label': 'baseline', 'corpus_sha256': 'fixture',
                  'asr_concurrency': 4, 'tts_concurrency': 1,
                  'runtime': {'images': {'hviske': 'pinned', 'plapre': 'pinned'},
                              'budgets': m.BUDGETS['baseline'],
                              'contract_sha256': {'hviske': 'pinned', 'plapre': 'pinned'}},
                  'asr': {'trials': 18, 'failures': 0, 'wer': .10,
                          'p95_rtf': .2, 'p95_elapsed_seconds': .5},
                  'tts_model_local': {'trials': 20, 'failures': 0, 'p95_rtf': .8,
                                      'p95_elapsed_seconds': 6., 'p95_first_audio_seconds': 5.5}}
        after = deepcopy(before)
        after.update(label='candidate', capacity={'passed': True},
                     asr_latency_pass=True, tts_model_local_latency_pass=False)
        after['runtime']['budgets'] = m.BUDGETS['candidate']
        self.assertTrue(m.compare(before, after)['capacity_qualified'])
        self.assertFalse(m.compare(before, after)['accepted'])
        self.assertFalse(after['tts_model_local_latency_pass'])
        for invalid in [False, 'true', 1, None]:
            changed = deepcopy(after)
            changed['capacity']['passed'] = invalid
            self.assertFalse(m.compare(before, changed)['capacity_qualified'])
        for service, metric in [('asr', 'p95_rtf'), ('tts_model_local', 'p95_first_audio_seconds')]:
            changed = deepcopy(after)
            changed[service][metric] *= 1.20
            self.assertFalse(m.compare(before, changed)['capacity_qualified'])
        changed = deepcopy(after)
        changed['asr']['wer'] = .12
        self.assertFalse(m.compare(before, changed)['capacity_qualified'])
        changed = deepcopy(after)
        changed['runtime']['images']['plapre'] = 'different'
        with self.assertRaises(ValueError):
            m.compare(before, changed)
        changed = deepcopy(after)
        changed['runtime']['contract_sha256']['plapre'] = 'different-voice-or-context'
        with self.assertRaises(ValueError):
            m.compare(before, changed)
        changed = deepcopy(after)
        changed['asr_concurrency'] = 1
        with self.assertRaises(ValueError):
            m.compare(before, changed)


if __name__ == '__main__':
    unittest.main()
