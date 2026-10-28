#!/usr/bin/env python3
"""Capacity candidates preserve the serving contract and restore partial failures."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('capacity', ROOT / 'scripts/speech_capacity.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
QUALIFIER_SPEC = importlib.util.spec_from_file_location('qualifier', ROOT / 'scripts/qualify-speech-capacity.py')
q = importlib.util.module_from_spec(QUALIFIER_SPEC)
QUALIFIER_SPEC.loader.exec_module(q)


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
    def test_real_render_includes_only_the_explicit_hviske_profile(self):
        # Exercise the same Compose filtering as the root plan without needing
        # GPU images or reading a real secret/activation environment.
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / 'hviske.env'
            template = (ROOT / 'config/hviske-stt.env.example').read_text()
            environment.write_text(template.replace('HVISKE_LICENSE_DECISION=review-required',
                                                     'HVISKE_LICENSE_DECISION=personal-noncommercial'))
            def run(arguments, **kwargs):
                if arguments[:3] == ['docker', 'image', 'inspect']:
                    return json.dumps([{'Id': 'sha256:' + 'a' * 64}])
                return subprocess.check_output(arguments, text=True)
            with patch.object(q, 'PATHS', {'hviske': ('deploy/hviske-stt/compose.yaml', str(environment))}), \
                    patch.object(q, 'protected'), patch.object(q, 'run', side_effect=run):
                rendered = q.render('hviske')
            self.assertIn('hviske-primary', rendered['services'])
            self.assertEqual(rendered['services']['hviske-primary']['profiles'], ['hviske'])
            self.assertEqual(set(rendered['services']), {'hviske-primary'})

    def test_failure_metadata_is_finite_and_rejects_private_or_malformed_fields(self):
        good = {'stage': 'approval', 'service': 'hviske', 'reason': 'approval-required'}
        self.assertEqual(m.failure_metadata(good), good)
        for value in [None, [], {**good, 'output': 'private'}, {**good, 'stage': []},
                      {**good, 'service': {}}, {**good, 'reason': 'private environment value'}]:
            self.assertIsNone(m.failure_metadata(value))
        with self.assertRaises(q.PlanFailure) as caught:
            q.guarded('approval', 'hviske', 'approval-required',
                      lambda: (_ for _ in ()).throw(ValueError('private token and environment')))
        self.assertEqual(caught.exception.diagnostic, good)
        self.assertNotIn('private', str(caught.exception))

    def test_real_render_still_requires_current_activation_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / 'revoked.env'
            environment.write_text('HVISKE_LICENSE_DECISION=review-required\nPRIVATE=do-not-relay\n')
            with patch.object(q, 'PATHS', {'hviske': ('deploy/hviske-stt/compose.yaml', str(environment))}), \
                    patch.object(q, 'protected'), patch.object(q, 'run') as run:
                with self.assertRaises(q.PlanFailure) as caught:
                    q.render('hviske')
            self.assertEqual(caught.exception.diagnostic,
                             {'stage': 'approval', 'service': 'hviske', 'reason': 'approval-required'})
            run.assert_not_called()

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

    def test_expected_compose_dollar_projection_preserves_every_other_byte(self):
        original=fixtures()
        rendered='export VLLM_API_KEY="$$(cat /run/secrets/vllm_api_key)"\n'
        original['hviske']['services']['hviske-primary']['command'][0]=rendered+original['hviske']['services']['hviske-primary']['command'][0]
        running={key:{'image':value['services'][m.SERVICES[key]]['image'],
                      'command':list(value['services'][m.SERVICES[key]]['command'])} for key,value in original.items()}
        running['hviske']['command'][0]=running['hviske']['command'][0].replace('$$(cat','$(cat')
        m.verify_baseline(original,running)
        valid=running['hviske']['command'][0]
        for changed in [valid.replace('$(cat','$$(cat'),valid.replace('vllm_api_key','other_key'),
                        valid.replace('"4"','"8"'),valid.replace('"0.10"','"0.05"'),valid+' ',
                        valid.replace('--revision pinned','--revision different')]:
            running['hviske']['command']=[changed]
            with self.subTest(changed=changed[-25:]),self.assertRaises(m.BaselineMismatch):
                m.verify_baseline(original,running)
        self.assertEqual(m.engine_command(['$$$$','$$','$']),['$$','$','$'])

    def test_actual_profile_uses_engine_projection_for_baseline_and_candidate(self):
        original=fixtures()
        original['hviske']['services']['hviske-primary']['command'][0]='export VLLM_API_KEY="$$(cat /run/secrets/vllm_api_key)"\n'+original['hviske']['services']['hviske-primary']['command'][0]
        staged=m.candidate(original)
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            for label,values in [('baseline',original),('candidate',staged)]:
                for key,value in values.items():(directory/(key+'-'+label+'.json')).write_text(json.dumps(value))
            for label,values in [('baseline',original),('candidate',staged)]:
                observed={}
                for key,value in values.items():
                    service=value['services'][m.SERVICES[key]]
                    command=list(service['command'])
                    if key=='hviske':command[0]=command[0].replace('$$(cat','$(cat')
                    observed[q.CONTAINERS[key]]={'Image':service['image'],'Config':{'Cmd':command},'State':{'Running':True,'Health':{'Status':'healthy'}}}
                with patch.object(q,'inspect',side_effect=lambda name:observed[name]):
                    result=q.actual_profiles(directory)
                self.assertEqual({row['profile'] for row in result.values()},{label})
                observed[q.CONTAINERS['hviske']]['State']['Running']=False
                with patch.object(q,'inspect',side_effect=lambda name:observed[name]):
                    self.assertFalse(q.actual_profiles(directory)['hviske']['healthy'])
                observed[q.CONTAINERS['hviske']]['State']['Running']=True
                observed[q.CONTAINERS['hviske']]['Image']='sha256:'+'f'*64
                with patch.object(q,'inspect',side_effect=lambda name:observed[name]):
                    result=q.actual_profiles(directory)['hviske']
                self.assertEqual(result,{'profile':'unrecognized','healthy':False})

    def test_failed_apply_keeps_marker_for_stale_health_wrong_image_or_unreadable_state(self):
        original=fixtures();staged=m.candidate(original);plan={'plan_sha256':'a'*64}
        observed={q.CONTAINERS[key]:{'Image':value['services'][m.SERVICES[key]]['image'],
                    'Config':{'Cmd':m.engine_command(value['services'][m.SERVICES[key]]['command'])},
                    'State':{'Running':True,'Health':{'Status':'healthy'}}} for key,value in original.items()}
        for case in ['healthy','stopped','wrong-image','unreadable']:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as temporary:
                data=deepcopy(observed)
                if case=='stopped':data[q.CONTAINERS['hviske']]['State']['Running']=False
                if case=='wrong-image':data[q.CONTAINERS['hviske']]['Image']='sha256:'+'f'*64
                def inspect(name):
                    if case=='unreadable':raise RuntimeError('inspection unavailable')
                    return data[name]
                state=Path(temporary)/'state'
                with patch.object(q,'STATE',state),patch.object(q,'release',return_value='b'*40), \
                        patch.object(q,'protected'),patch.object(q,'prepare',return_value=(plan,original,staged)), \
                        patch.object(q,'activate',side_effect=RuntimeError('activation failed')), \
                        patch.object(q,'inspect',side_effect=inspect), \
                        patch.object(sys,'argv',['qualifier','apply','--plan-sha256','a'*64,'--speech-idle']):
                    with self.assertRaisesRegex(RuntimeError,'activation failed'):q.main()
                self.assertEqual((state/'active.json').exists(),case!='healthy')

    def test_real_compose_create_projects_dollars_once_for_original_and_rendered_json(self):
        image='homecompute/speech-adapters:wyoming-1.10.2'
        if subprocess.run(['docker','image','inspect',image],capture_output=True).returncode:
            self.skipTest('Build the pinned CPU speech test image first')
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);project='hc-capacity-projection-'+uuid.uuid4().hex[:12]
            declaration={'name':project,'services':{'fixture':{'image':image,'entrypoint':['/bin/sh','-ec'],
                'command':['PUBLIC_VALUE="$$(printf fixture)"; printf "%s" "$$PUBLIC_VALUE"; printf "%s" \'$$$$\''],
                'network_mode':'none','read_only':True,'cap_drop':['ALL'],'user':'65532:65532',
                'mem_limit':'32m','pids_limit':16}}}
            source=directory/'source.json';source.write_text(json.dumps(declaration))
            def compose(path,*args):
                return subprocess.check_output(['docker','compose','--project-name',project,'--env-file','/dev/null','-f',str(path),*args],text=True,stderr=subprocess.PIPE,timeout=30)
            rendered=json.loads(compose(source,'config','--format','json'))
            copy=directory/'rendered.json';copy.write_text(json.dumps(rendered))
            try:
                for path in (source,copy):
                    compose(path,'create','--no-build','--pull','never','--force-recreate','fixture')
                    identifier=compose(path,'ps','-a','-q','fixture').strip()
                    self.assertRegex(identifier,r'^[0-9a-f]{64}$')
                    actual=json.loads(subprocess.check_output(['docker','inspect',identifier]))[0]
                    self.assertEqual(actual['State']['Status'],'created')
                    self.assertFalse(actual['State']['Running'])
                    self.assertEqual(actual['Config']['Cmd'],m.engine_command(rendered['services']['fixture']['command']))
            finally:
                compose(source,'down','--remove-orphans')

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
