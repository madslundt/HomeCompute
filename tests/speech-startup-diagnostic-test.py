#!/usr/bin/env python3
"""A failed first replacement must retain evidence and report its internal restore."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import uuid
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
q = load('startup_qualifier', ROOT / 'scripts/qualify-speech-capacity.py')
r = load('startup_rollout', ROOT / 'scripts/spark-rollout.py')
d = load('startup_diagnostics', ROOT / 'scripts/speech_startup_diagnostics.py')
w = load('startup_wrapper', ROOT / 'scripts/diagnose-speech-startup.py')
b = load('startup_bootstrap', ROOT / 'scripts/bootstrap-speech-startup.py')

class StartupDiagnosticTest(unittest.TestCase):
    def test_main_reports_success_only_for_verified_startup_and_restore(self):
        flags=SimpleNamespace(**({key:getattr(sys.flags,key) for key in dir(sys.flags) if not key.startswith('_')}|{'isolated':True}))
        cases = [
            ({'state':'startup-passed-baseline-restored','rollback_state':'restored','failure':None},0),
            ({'state':'startup-failed','rollback_state':'restored','failure':None},1),
            ({'state':'guard-or-recovery-failed','rollback_state':'restored','failure':None},1),
            ({'state':'unknown-operation','rollback_state':'not-required','failure':None},1),
            ({'state':'startup-passed-baseline-restored','rollback_state':'incomplete','failure':None},1),
            ({'state':'startup-passed-baseline-restored','rollback_state':'restored','failure':{'reason':'startup-operation'}},1),
            (RuntimeError('unknown operation'),1),
        ]
        for result,expected in cases:
            with self.subTest(result=result),tempfile.TemporaryDirectory() as temporary:
                target=Path(temporary)/'result.json';publisher=SimpleNamespace(publish_result=lambda path,data:path.write_text(json.dumps(data)))
                outcome={'side_effect':result} if isinstance(result,BaseException) else {'return_value':result}
                with patch.object(w,'verify'),patch.object(w,'report_target',return_value=target),patch.dict(w.os.environ,w.os.environ.copy(),clear=True), \
                        patch.object(w.sys,'flags',flags),patch.object(w.sys,'path',list(sys.path)), \
                        patch.object(w.sys,'argv',['diagnose','--revision','a'*40,'--plan-sha256','b'*64,'--result-directory',temporary]), \
                        patch.object(w,'load',side_effect=[q,publisher]),patch.object(w.signal,'signal'),patch.object(w,'execute',**outcome):
                    self.assertEqual(w.main(),expected)
                self.assertTrue(target.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'plan.json';publisher=SimpleNamespace(publish_result=lambda path,data:path.write_text(json.dumps(data)))
            with patch.object(w,'verify'),patch.object(w,'report_target',return_value=target),patch.dict(w.os.environ,w.os.environ.copy(),clear=True), \
                    patch.object(w.sys,'flags',flags),patch.object(w.sys,'path',list(sys.path)), \
                    patch.object(w.sys,'argv',['diagnose','--revision','a'*40,'--plan','--result-directory',temporary]), \
                    patch.object(w,'load',side_effect=[q,publisher]),patch.object(q,'prepare',return_value=({'plan_sha256':'b'*64},{},{})),patch.object(w,'execute') as execute:
                self.assertEqual(w.main(),0)
            execute.assert_not_called();self.assertTrue(json.loads(target.read_text())['read_only'])

    def test_first_replacement_failure_is_captured_before_internal_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); marker = directory / 'active.json'; marker.write_text('{}')
            events = []
            def start(path, key, profile):
                events.append((key, profile))
                if profile == 'candidate':
                    (path / 'failed-start-fixture.json').write_text('{"reason":"startup-memory"}')
                    raise q.PlanFailure('startup', key, 'startup-memory')
                self.assertTrue((path / 'failed-start-fixture.json').exists())
            plan = {'plan_sha256': 'a' * 64, 'source_revision': 'b' * 40}
            with patch.object(q, 'STATE', directory), patch.object(q, 'start', side_effect=start), \
                    patch.object(q, 'baseline_restored', return_value=True):
                with self.assertRaises(q.PlanFailure) as caught:
                    q.perform_attempt(directory, plan, {}, diagnose=False)
            self.assertEqual(events, [('hviske', 'candidate'), ('hviske', 'baseline')])
            self.assertFalse(marker.exists())
            self.assertEqual(caught.exception.attempt['rollback_state'], 'restored')
            outcome = json.loads((directory / 'attempt-result.json').read_text())
            self.assertEqual(outcome['plan_sha256'], plan['plan_sha256'])
            self.assertEqual(outcome['source_revision'], plan['source_revision'])
            self.assertFalse(outcome['capacity_qualified'])
    def test_rollout_reports_qualifier_internal_restore_without_repeating_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            failure = r.CapacityOperationFailure({'stage':'operation','service':None,'reason':'guard-or-operation'})
            failure.attempt = {'plan_sha256':'a'*64,'rollback_state':'restored',
                               'service':'hviske','reason':'startup-memory','attempt_sha256':'c'*64}
            def helper(name, action, *args, **kwargs):
                if action == 'plan': return {'plan_sha256':'a'*64}
                if action == 'apply': raise failure
                self.fail('Must not launch a second rollback after verified internal restore')
            baseline = {'asr':{'trials':18,'failures':0},'tts_model_local':{'trials':20,'failures':0}}
            with patch.object(r,'CAPACITY_STATE',directory/'absent'),patch.object(r,'helper',side_effect=helper), \
                    patch.object(r,'idle'),self.assertRaises(r.CapacityOperationFailure):
                r.trial(directory, confirmer=lambda _:True, measure=lambda *_:baseline)
            result = json.loads((directory/'result.json').read_text())
            self.assertEqual(result['state'],'baseline-restored')
            self.assertEqual(r.public_result(directory,'b'*40,result['state'])['rollback_state'],'restored')

    def test_host_oom_cuda_oom_and_namespace_echo_are_distinct(self):
        cuda = 'torch.OutOfMemoryError: CUDA out of memory'
        self.assertEqual(d.classify(cuda), 'startup-cuda-oom')
        self.assertEqual(d.classify(cuda, {'oom_killed':True}), 'startup-host-oom')
        self.assertEqual(d.classify("Namespace(api_key='private CUDA out of memory')"), 'startup-operation')
        self.assertEqual(d.classify('ValueError: Free memory on device (3 GiB) is less than desired GPU memory utilization'), 'startup-memory')
        self.assertEqual(d.classify('Error response from daemon: invalid mount config for type bind: private-path'), 'startup-mount')
    def test_numeric_projection_never_returns_whole_lines_or_private_content(self):
        text = 'private=do-not-retain Model loading took 3.87 GiB other=secret\nGPU KV cache size: 108,496 tokens'
        self.assertEqual(d.numeric_fields(text), {'model_loading_gib':[3.87], 'kv_tokens':[108496.0]})
        self.assertNotIn('private', json.dumps(d.numeric_fields(text)))
    def test_malformed_metadata_is_unavailable_not_false_exit(self):
        valid = {'Image':'fixed','Config':{'Cmd':['arg']},'State':{'Running':True,'OOMKilled':False,'ExitCode':0},'Id':'a'*64}
        values = [None, {}, [None], [dict(valid, State=None)], [dict(valid, Config=[])],
                  [dict(valid, State={'Running':'false','OOMKilled':False})]]
        for value in values:
            with self.subTest(value=value),patch.object(d,'bounded_output',return_value=(0,json.dumps(value))):
                result=d.snapshot('fixed',{'image':'fixed','command':['arg']})
            self.assertFalse(result['available']); self.assertEqual(result['reason'],'startup-operation')
    def test_wrong_command_or_image_never_reads_logs(self):
        for field in ('Image','Config'):
            value={'Image':'fixed','Config':{'Cmd':['arg']},'State':{'Running':False,'OOMKilled':False},'Id':'a'*64}
            value[field]='wrong' if field=='Image' else {'Cmd':['wrong']}
            with patch.object(d,'bounded_output',return_value=(0,json.dumps([value]))) as read:
                result=d.snapshot('fixed',{'image':'fixed','command':['arg']})
            self.assertFalse(result['available']); self.assertEqual(result['reason'],'startup-identity')
            self.assertEqual(read.call_count,1)
    def test_stream_reader_caps_bytes_and_terminates_only_owned_cli(self):
        output=d.bounded_output([sys.executable,'-c','import sys; sys.stdout.write("a"*200000)'],maximum=1000)
        self.assertEqual(output,(0,'a'*1000))
        self.assertIsNone(d.bounded_output([sys.executable,'-c','import sys; sys.stdout.write("x"*5000000)']))
        self.assertIsNone(d.bounded_output([sys.executable,'-c','import time; time.sleep(60)'],timeout=.05))
    def test_untrusted_attempt_metadata_cannot_claim_restoration(self):
        good={'plan_sha256':'a'*64,'rollback_state':'restored','service':'hviske','reason':'startup-memory','attempt_sha256':'b'*64}
        self.assertIsNotNone(d.attempt_metadata(good))
        for value in [None,{**good,'private':'secret'},{**good,'rollback_state':'yes'},{**good,'reason':[]},{**good,'plan_sha256':'0c-stale'}]:
            self.assertIsNone(d.attempt_metadata(value))
    def test_full_startup_success_always_restores_both_and_never_qualifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); (directory/'active.json').write_text('{}'); calls=[]
            with patch.object(q,'STATE',directory),patch.object(q,'start',side_effect=lambda _,key,profile:calls.append((key,profile))),patch.object(q,'baseline_restored',return_value=True):
                result=q.perform_attempt(directory,{'plan_sha256':'a'*64,'source_revision':'b'*40},{},diagnose=True)
            self.assertEqual(calls,[('hviske','candidate'),('plapre','candidate'),('plapre','baseline'),('hviske','baseline')])
            self.assertEqual(result['rollback_state'],'restored'); self.assertFalse(result['capacity_qualified'])
            self.assertFalse(result['accepted']); self.assertFalse(result['full_voice_accepted'])
    def test_uncertain_restoration_retains_marker_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); marker=directory/'active.json';marker.write_text('{}')
            with patch.object(q,'STATE',directory),patch.object(q,'start'),patch.object(q,'baseline_restored',return_value=False):
                with self.assertRaises(q.PlanFailure) as caught:q.perform_attempt(directory,{'plan_sha256':'a'*64,'source_revision':'b'*40},{},diagnose=True)
            self.assertTrue(marker.exists()); self.assertEqual(caught.exception.attempt['rollback_state'],'incomplete')
    def test_receipt_failure_cannot_retire_active_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); marker=directory/'active.json';marker.write_text('{}')
            with patch.object(q,'STATE',directory),patch.object(q,'start'),patch.object(q,'baseline_restored',return_value=True),patch.object(q,'write',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):q.perform_attempt(directory,{'plan_sha256':'a'*64,'source_revision':'b'*40},{},diagnose=True)
            self.assertTrue(marker.exists())
    def test_stale_plan_diagnostic_never_pauses_intake_or_starts_model(self):
        with patch.object(q,'prepare',return_value=({'plan_sha256':'a'*64},{},{})),patch.object(r,'paused_ingress') as pause:
            with self.assertRaisesRegex(ValueError,'fresh-reviewed-plan'):w.execute(q,r,'b'*40,'c'*64)
        pause.assert_not_called()
    def test_attempt_subprocess_metadata_survives_and_rejects_extra_fields(self):
        metadata={'plan_sha256':'a'*64,'rollback_state':'restored','service':'hviske','reason':'startup-memory','attempt_sha256':'b'*64}
        stderr='private=secret\n'+d.ATTEMPT_PREFIX+json.dumps(metadata)+'\n'+r.CAPACITY_FAILURE_PREFIX+json.dumps({'stage':'startup','service':'hviske','reason':'startup-memory'})
        with patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'private stdout',stderr)):
            with self.assertRaises(r.CapacityOperationFailure) as caught:r.run(['python3','/published/scripts/qualify-speech-capacity.py','apply'])
        self.assertEqual(caught.exception.attempt,metadata);self.assertNotIn('secret',str(caught.exception))
    def test_bootstrap_rejects_dirty_existing_release(self):
        with patch.object(b,'protected'),patch.object(b,'run',side_effect=['a'*40,' M modified']):
            with self.assertRaisesRegex(ValueError,'nothing-overwritten'):b.verify(Path('/srv/homecompute/releases')/('a'*40),'a'*40)
    def test_isolated_help_needs_no_imports_or_stdin(self):
        for name in ('bootstrap-speech-startup.py','diagnose-speech-startup.py'):
            result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts'/name),'--help'],stdin=subprocess.DEVNULL,capture_output=True,text=True)
            self.assertEqual(result.returncode,0);self.assertIn('--plan-sha256',result.stdout)
    def test_real_failed_compose_start_preserves_evidence_before_baseline_recreate(self):
        image='homecompute/speech-adapters:wyoming-1.10.2'
        response=subprocess.run(['docker','image','inspect',image],capture_output=True,text=True)
        if response.returncode:self.skipTest('Pinned CPU speech test image unavailable')
        image_id=json.loads(response.stdout)[0]['Id'];project='hc-startup-'+uuid.uuid4().hex[:10];name=project+'-fixture'
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            base={'name':project,'services':{'fixture':{'image':image_id,'container_name':name,'entrypoint':['/bin/sh'],
                'command':['-ec','sleep 30'],'network_mode':'none','user':'65532:65532','read_only':True,'cap_drop':['ALL'],
                'mem_limit':'64m','cpus':.25,'pids_limit':32,
                'healthcheck':{'test':['CMD','/bin/sh','-ec','exit 0'],'interval':'1s','timeout':'1s','retries':2}}}}
            candidate=deepcopy(base);candidate['services']['fixture'].pop('healthcheck')
            candidate['services']['fixture']['command']=['-ec','printf "%s\\n" "ValueError: No available memory for the cache blocks" "Model loading took 3.87 GiB"; exit 7']
            for label,value in [('baseline',base),('candidate',candidate)]:
                (directory/('hviske-'+label+'.json')).write_text(json.dumps(value))
            calls=[]
            def start(key,profile):
                calls.append((key,profile))
                if profile=='baseline':
                    receipts=list(directory.glob('start-*.json'));self.assertEqual(len(receipts),1)
                    evidence=json.loads(receipts[0].read_text())
                    self.assertEqual(evidence['reason'],'startup-memory');self.assertEqual(evidence['state']['exit_code'],7)
                    self.assertTrue(evidence['command_matches']);self.assertTrue(evidence['image_matches'])
                q.start(directory,key,profile)
            try:
                with patch.object(q,'SERVICES',{'hviske':'fixture'}),patch.object(q,'CONTAINERS',{'hviske':name}),patch.object(q,'protected'):
                    with self.assertRaises(q.PlanFailure):q.activate(start)
                self.assertEqual(calls,[('hviske','candidate'),('hviske','baseline')])
                actual=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
                self.assertTrue(actual['State']['Running']);self.assertEqual(actual['State']['Health']['Status'],'healthy')
                self.assertEqual(actual['Config']['Cmd'],base['services']['fixture']['command'])
                self.assertNotIn('No available memory', ''.join(path.read_text() for path in directory.glob('start-*.json')))
            finally:subprocess.run(['docker','rm','-f',name],capture_output=True)

    def test_keyboard_interrupt_first_and_second_start_attempts_restore_before_return(self):
        for failed_key in ('hviske','plapre'):
            with self.subTest(service=failed_key),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);marker=directory/'active.json';marker.write_text('{}');calls=[]
                def start(_,key,profile):
                    calls.append((key,profile))
                    if key==failed_key and profile=='candidate': raise KeyboardInterrupt()
                with patch.object(q,'STATE',directory),patch.object(q,'start',side_effect=start),patch.object(q,'baseline_restored',return_value=True):
                    with self.assertRaises(KeyboardInterrupt):q.perform_attempt(directory,{'plan_sha256':'a'*64,'source_revision':'b'*40},{},diagnose=True)
                expected=[('hviske','candidate')]
                if failed_key=='plapre': expected += [('plapre','candidate'),('plapre','baseline')]
                expected += [('hviske','baseline')]
                self.assertEqual(calls,expected);self.assertFalse(marker.exists())
    def test_restore_verifies_running_exact_image_command_before_marker_retirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);marker=directory/'active.json';marker.write_text('{}')
            for key in q.SERVICES:
                (directory/(key+'-baseline.json')).write_text('{}')
            with patch.object(q,'STATE',directory),patch.object(q,'validate_active',return_value=(directory,{'plan_sha256':'a'*64})),patch.object(q,'start'),patch.object(q,'baseline_restored',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'could not be verified'):q.restore()
            self.assertTrue(marker.exists())
    def test_compose_process_is_bounded_and_json_stdout_separate_from_stderr(self):
        response=d.bounded_process([sys.executable,'-c','import sys; print("{} "); print("private stderr",file=sys.stderr)'])
        self.assertEqual(json.loads(response[1]),{});self.assertEqual(response[0],0)
        self.assertIsNone(d.bounded_process([sys.executable,'-c','print("x"*1100000)']))
        self.assertIsNone(d.bounded_process([sys.executable,'-c','import time; time.sleep(60)'],timeout=.05))
    def test_old_or_other_plan_receipt_cannot_claim_rollback_for_current_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            failure=r.CapacityOperationFailure({'stage':'startup','service':'hviske','reason':'startup-memory'},
                {'plan_sha256':'c'*64,'rollback_state':'restored','service':'hviske','reason':'startup-memory','attempt_sha256':'b'*64})
            def helper(_,action,*args,**kwargs):
                if action=='plan':return {'plan_sha256':'a'*64}
                raise failure
            with patch.object(r,'CAPACITY_STATE',directory/'absent'),patch.object(r,'helper',side_effect=helper),patch.object(r,'idle'):
                with self.assertRaises(r.CapacityOperationFailure):r.trial(directory,confirmer=lambda _:True,measure=lambda *_:{'asr':{'trials':18,'failures':0},'tts_model_local':{'trials':20,'failures':0}})
            self.assertEqual(json.loads((directory/'result.json').read_text())['state'],'baseline')

    def test_outer_startup_controller_recovers_interrupted_partial_start_before_intake_resumes(self):
        for error in (InterruptedError('signal'),RuntimeError('unknown operation')):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);capacity=directory/'capacity';rollout=directory/'rollout';events=[]
                @contextmanager
                def paused():
                    events.append('paused')
                    try:yield
                    finally:events.append('resumed')
                def apply():
                    (capacity/'active.json').write_text('{}');events.append('attempted');raise error
                def restore():
                    self.assertNotIn('resumed',events);events.append('restored');(capacity/'active.json').unlink()
                with patch.object(w,'DEPLOY_LOCK',directory/'deploy.lock'),patch.object(q,'STATE',capacity),patch.object(r,'STATE',rollout), \
                        patch.object(q,'prepare',return_value=({'plan_sha256':'a'*64},{},{})),patch.object(q,'protected'), \
                        patch.object(q,'main',side_effect=apply),patch.object(q,'restore',side_effect=restore), \
                        patch.object(r,'paused_ingress',side_effect=paused),patch.object(r,'idle'):
                    result=w.execute(q,r,'b'*40,'a'*64)
                self.assertEqual(events,['paused','attempted','restored','resumed'])
                self.assertEqual(result['rollback_state'],'restored');self.assertEqual(result['state'],'startup-failed')
                self.assertFalse(result['capacity_qualified'])
    def test_diagnostic_controller_failure_keeps_recovery_marker_and_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);capacity=directory/'capacity';rollout=directory/'rollout'
            def apply():
                (capacity/'active.json').write_text('{}');raise RuntimeError('partial startup')
            from contextlib import nullcontext
            with patch.object(w,'DEPLOY_LOCK',directory/'deploy.lock'),patch.object(q,'STATE',capacity),patch.object(r,'STATE',rollout), \
                    patch.object(q,'prepare',return_value=({'plan_sha256':'a'*64},{},{})),patch.object(q,'protected'), \
                    patch.object(q,'main',side_effect=apply),patch.object(q,'restore',side_effect=RuntimeError('not healthy')), \
                    patch.object(r,'paused_ingress',return_value=nullcontext()),patch.object(r,'idle'):
                result=w.execute(q,r,'b'*40,'a'*64)
            self.assertTrue((capacity/'active.json').exists());self.assertEqual(result['rollback_state'],'incomplete')

if __name__ == '__main__': unittest.main()
