#!/usr/bin/env python3
"""Supervised rollout aborts, exact ingress restoration, and pinned public corpus."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value
m=module('spark_rollout',ROOT/'scripts/spark-rollout.py')
b=module('spark_bootstrap',ROOT/'scripts/bootstrap-spark-rollout.py')


class RolloutTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name); self.marker=self.directory/'active.json'
        self.calls=[]
    def test_actual_release_library_imports_create_no_bytecode(self):
        import shutil
        scripts=self.directory/'scripts'; scripts.mkdir()
        for name in ('spark-rollout.py','qualify-speech-capacity.py','speech_capacity.py'):
            shutil.copy2(ROOT/'scripts'/name,scripts/name)
        code='''import runpy,importlib.util,sys
from pathlib import Path
path=Path(sys.argv[1]); sys.path.insert(0,str(path))
runpy.run_path(str(path/'spark-rollout.py'),run_name='import_regression')
spec=importlib.util.spec_from_file_location('capacity_setup',path/'qualify-speech-capacity.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert not list(path.rglob('*.pyc'))
assert not list(path.rglob('__pycache__'))
'''
        result=subprocess.run(['python3','-c',code,str(scripts)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_boundary_failure_propagates_only_allowlisted_reason(self):
        failure=subprocess.CompletedProcess([],1,'private output',
            'secret=do-not-relay\nSPEECH_BOUNDARY_FAILURE '+json.dumps(
                {'reason':'invalid-mount','error':'OperationError','exit_code':1}))
        with patch.object(m.subprocess,'run',return_value=failure):
            with self.assertRaisesRegex(RuntimeError,'invalid-mount') as caught:
                m.run(['python3','/published/scripts/setup-speech-boundary.py','spark-prepare'])
        self.assertNotIn('do-not-relay',str(caught.exception))
    def baseline(self):
        return {'asr':{'trials':18,'failures':0},'tts_model_local':{'trials':20,'failures':0}}
    def helper(self,name,*args,**kwargs):
        self.calls.append(args[0])
        if args[0]=='plan': return {'plan_sha256':'a'*64}
        if args[0]=='apply':
            self.assertEqual(args[1:],('--plan-sha256','a'*64,'--speech-idle'))
            self.marker.write_text('{}'); return {}
        if args[0]=='rollback': self.marker.unlink(); return {}
        self.fail('Unreviewed helper action')
    @contextmanager
    def mocked(self):
        with patch.object(m,'CAPACITY_STATE',self.marker),patch.object(m,'helper',side_effect=self.helper),patch.object(m,'idle'):
            yield
    def test_failed_or_incomplete_capacity_evidence_rolls_back(self):
        for value in [False,None,'true',1]:
            with self.subTest(value=value):
                target=self.directory/str(value); target.mkdir()
                def measure(label,_): return self.baseline() if label=='baseline' else {'capacity_qualified':value}
                with self.mocked(),self.assertRaises(ValueError): m.trial(target,confirmer=lambda _:True,measure=measure)
                self.assertFalse(self.marker.exists())
                result=json.loads((target/'result.json').read_text())
                self.assertEqual(result['state'],'baseline-restored')
                self.assertFalse(result['accepted']); self.assertFalse(result['full_voice_accepted'])
    def test_approval_timeout_cannot_replace_an_engine(self):
        with self.mocked(),self.assertRaises(ValueError):
            m.trial(self.directory,confirmer=lambda _:False,measure=lambda *_:self.baseline())
        self.assertEqual(self.calls,['plan','plan']); self.assertFalse(self.marker.exists())
    def test_invalid_baseline_fails_before_replacement(self):
        with self.mocked(),self.assertRaises(ValueError):
            m.trial(self.directory,confirmer=lambda _:True,measure=lambda *_:{'asr':{'failures':1}})
        self.assertEqual(self.calls,['plan'])
    def test_failed_preflight_never_runs_an_expensive_baseline(self):
        with patch.object(m,'helper',side_effect=m.CapacityOperationFailure(
                {'stage':'render','service':'hviske','reason':'service-missing'})), \
                patch.object(m,'benchmark') as benchmark, self.assertRaises(m.CapacityOperationFailure):
            m.trial(self.directory,measure=benchmark)
        benchmark.assert_not_called()
    def test_changed_plan_after_baseline_cannot_prompt_or_replace_an_engine(self):
        with patch.object(m,'helper',side_effect=[{'plan_sha256':'a'*64},{'plan_sha256':'b'*64}]), \
                patch.object(m,'confirm_plan') as confirm, self.assertRaisesRegex(ValueError,'changed during'):
            m.trial(self.directory,confirmer=confirm,measure=lambda *_:self.baseline())
        confirm.assert_not_called()
    def test_qualifier_diagnostic_survives_subprocess_and_public_report_without_private_output(self):
        diagnostic={'stage':'render','service':'hviske','reason':'service-missing'}
        result=subprocess.CompletedProcess([],1,'private stdout',
            'private environment\n'+m.CAPACITY_FAILURE_PREFIX+json.dumps(diagnostic))
        with patch.object(m.subprocess,'run',return_value=result):
            with self.assertRaises(m.CapacityOperationFailure) as caught:
                m.run(['python3','/published/scripts/qualify-speech-capacity.py','plan'])
        report=m.public_result(None,'a'*40,'failed-preflight',caught.exception.diagnostic)
        self.assertEqual(report['failure'],diagnostic)
        self.assertNotIn('private',str(caught.exception))
        self.assertFalse(report['accepted']); self.assertFalse(report['full_voice_accepted'])
    def test_rollback_idle_allows_only_authoritatively_stopped_or_absent_models(self):
        names=list(m.MODELS)
        def run(arguments,**kwargs):
            if arguments[:3]==['docker','ps','-a']: return names[0]
            if arguments[:2]==['docker','inspect']: return 'false'
            self.fail('Stopped/absent models must not be exec-probed')
        with patch.object(m,'run',side_effect=run),patch.object(m.time,'sleep'):
            m.idle(restoring=True)
    def test_rollback_idle_refuses_unknown_docker_state_and_probes_running_models(self):
        names=list(m.MODELS)
        for state in ['unknown','true']:
            def run(arguments,**kwargs):
                if arguments[:3]==['docker','ps','-a']: return '\n'.join(names)
                if arguments[:2]==['docker','inspect']: return state
                if arguments[:2]==['docker','exec']:
                    raise ValueError('unknown running activity')
                self.fail('Unreviewed operation')
            with self.subTest(state=state),patch.object(m,'run',side_effect=run),self.assertRaises(ValueError):
                m.idle(restoring=True)
        with patch.object(m,'run',side_effect=RuntimeError('Docker unavailable')),self.assertRaises(RuntimeError):
            m.idle(restoring=True)
    def test_rollback_ingress_gate_handles_stopped_absent_models_and_restores_intake(self):
        model=next(iter(m.MODELS)); ingress=m.INGRESS[0]; calls=[]
        def run(arguments,**kwargs):
            calls.append(arguments)
            if arguments[:3]==['docker','ps','-a']: return model+'\n'+ingress
            if arguments[:2]==['docker','inspect']:
                self.assertNotIn('Networks',arguments[-1])
                return 'false' if arguments[2]==model else 'true'
            if arguments[:3]==['iptables','-w','-L']: return None
            return ''
        with patch.object(m,'run',side_effect=run),patch.object(m,'idle') as idle:
            with m.paused_ingress(restoring=True): pass
        idle.assert_called_once_with(restoring=True)
        self.assertIn(['docker','stop','--time','30',ingress],calls)
        self.assertIn(['docker','start',ingress],calls)
        self.assertIn(['iptables','-w','-D','DOCKER-USER','-j',m.CHAIN],calls)
    def test_interrupted_startup_restores_baseline_when_a_model_is_stopped_or_absent(self):
        model=next(iter(m.MODELS)); original=self.helper
        def helper(name,*arguments,**kwargs):
            result=original(name,*arguments,**kwargs)
            if arguments[0]=='apply': raise InterruptedError('interrupted startup')
            return result
        def run(arguments,**kwargs):
            if arguments[:2]==['docker','exec']:
                return json.dumps({'connections':0,'running':0,'waiting':0})
            if arguments[:3]==['docker','ps','-a']: return model
            if arguments[:2]==['docker','inspect']: return 'false'
            self.fail('Unreviewed operation')
        with patch.object(m,'CAPACITY_STATE',self.marker),patch.object(m,'helper',side_effect=helper), \
                patch.object(m,'run',side_effect=run),patch.object(m.time,'sleep'),self.assertRaises(InterruptedError):
            m.trial(self.directory,confirmer=lambda _:True,measure=lambda *_:self.baseline())
        self.assertEqual(self.calls,['plan','plan','apply','rollback'])
        self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads((self.directory/'result.json').read_text())['state'],'baseline-restored')
    def test_interrupted_candidate_is_restored(self):
        def measure(label,_):
            if label=='baseline': return self.baseline()
            raise InterruptedError('synthetic interruption')
        with self.mocked(),self.assertRaises(InterruptedError):
            m.trial(self.directory,confirmer=lambda _:True,measure=measure)
        self.assertEqual(self.calls,['plan','plan','apply','rollback']); self.assertFalse(self.marker.exists())
    def test_capacity_pass_does_not_accept_voice_or_canonical_promotion(self):
        def measure(label,_):
            return self.baseline() if label=='baseline' else {'capacity_qualified':True,
                'asr_latency_pass':False,'tts_model_local_latency_pass':False}
        with self.mocked(): result=m.trial(self.directory,confirmer=lambda _:True,measure=measure)
        self.assertTrue(result['capacity_qualified']); self.assertTrue(result['reconciliation_required'])
        self.assertFalse(result['accepted']); self.assertFalse(result['full_voice_accepted'])
        self.assertFalse(result['tts_model_local_latency_pass'])
    def test_failed_rollback_is_explicit_and_keeps_recovery_marker(self):
        original=self.helper
        def fail(name,*args,**kwargs):
            if args[0]=='rollback': raise RuntimeError('synthetic rollback failure')
            return original(name,*args,**kwargs)
        with patch.object(m,'CAPACITY_STATE',self.marker),patch.object(m,'helper',side_effect=fail),patch.object(m,'idle'):
            with self.assertRaises(ValueError):
                m.trial(self.directory,confirmer=lambda _:True,
                    measure=lambda label,_:self.baseline() if label=='baseline' else {})
        self.assertTrue(self.marker.exists())
        self.assertEqual(json.loads((self.directory/'result.json').read_text())['state'],'rollback-incomplete')
    def test_pause_gate_drains_and_restores_only_previously_running_ingress(self):
        calls=[]; active=set(m.INGRESS[:2])
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['iptables','-w','-L']: return None
            if args[:2]==['docker','ps']: return '\n'.join(m.INGRESS)
            if args[:2]==['docker','inspect']:
                if 'Networks' in args[-1]: return json.dumps({'private':{'IPAddress':'172.30.1.2'}})
                return 'true' if args[2] in active else 'false'
            return ''
        with patch.object(m,'run',side_effect=run),patch.object(m,'idle') as idle:
            with self.assertRaisesRegex(RuntimeError,'fixture'):
                with m.paused_ingress(): raise RuntimeError('fixture')
        idle.assert_called_once()
        self.assertEqual({x[-1] for x in calls if x[:2]==['docker','stop']},active)
        self.assertEqual({x[-1] for x in calls if x[:2]==['docker','start']},active)
        self.assertIn(['iptables','-w','-D','DOCKER-USER','-j',m.CHAIN],calls)
    def test_unknown_idle_activity_is_refused(self):
        with patch.object(m,'run',return_value=json.dumps({'connections':'0','running':0,'waiting':0})):
            with self.assertRaises(ValueError): m.idle(timeout=1)
    def test_synthetic_load_cancels_actual_sockets_even_after_connection_handoff(self):
        from unittest.mock import MagicMock
        load=m.SyntheticLoad()
        wire=MagicMock(); connection=MagicMock(); connection.sock=None
        thread=MagicMock(); thread.ident=123
        load.sockets=[wire]; load.connections=[connection]; load.threads=[thread]
        load.__exit__()
        self.assertTrue(load.stop_event.is_set())
        wire.shutdown.assert_called_once_with(m.socket.SHUT_RDWR)
        connection.close.assert_called_once(); thread.join.assert_called_once_with(12)
    def test_failed_controlled_load_cannot_borrow_unrelated_production_traffic_for_a_pass(self):
        class Load:
            accepted=[0,0]; completed=0; errors=1
            def __enter__(self): return self
            def __exit__(self,*_): pass
        (self.directory/'candidate.json').write_text(json.dumps({'capacity_qualified':True}))
        with patch.object(m,'SyntheticLoad',return_value=Load()),patch.object(m,'run'):
            with self.assertRaisesRegex(ValueError,'Controlled synthetic'): m.benchmark('candidate',self.directory)
        self.assertEqual(json.loads((self.directory/'load-candidate.json').read_text())['failed_requests'],1)
    def test_cancellation_during_connect_cannot_send_a_late_model_request(self):
        from unittest.mock import MagicMock
        load=m.SyntheticLoad(); connection=MagicMock()
        connection.connect.side_effect=lambda:load.stop_event.set()
        with patch.object(Path,'read_text',return_value='synthetic-key'), \
             patch.object(m.http.client,'HTTPConnection',return_value=connection):
            with self.assertRaises(InterruptedError): load.__enter__()
        connection.request.assert_not_called()
        self.assertTrue(all(not thread.is_alive() for thread in load.threads))
    def test_bootstrap_refuses_arbitrary_revision_and_never_overwrites_dirty_release(self):
        with patch.object(b,'run') as run:
            with self.assertRaises(ValueError): b.materialize('../../unreviewed')
            run.assert_not_called()
        releases=self.directory/'releases'; releases.mkdir(); (releases/('a'*40)).mkdir()
        with patch.object(b,'RELEASES',releases),patch.object(b,'protected'), \
             patch.object(b,'verify',side_effect=ValueError('dirty')),patch.object(b,'run') as run:
            with self.assertRaises(ValueError): b.materialize('a'*40)
            run.assert_not_called(); self.assertTrue((releases/('a'*40)).is_dir())
    def test_reviewed_wizard_library_and_noninteractive_cli_help(self):
        script=ROOT/'scripts/spark-rollout-wizard.sh'
        prefix=script.read_text().split('run_wizard() {',1)[0]
        self.assertEqual(hashlib.sha256(prefix.encode()).hexdigest(),'19be94ff8fdb41ad0a34bc66d6a3b638a46f31ddd3da1376fdbc4cd1f02e7183')
        result=subprocess.run(['bash',str(script),'--help'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0); self.assertIn('published release',result.stdout)
    def test_corpus_pin_is_exactly_the_reviewed_public_danish_selection(self):
        pin=json.loads((ROOT/'config/spark-rollout-corpus.json').read_text())
        self.assertEqual(pin['danish_fixtures'],18); self.assertEqual(pin['conditions'],{'clean':12,'noise10db':6})
        self.assertEqual(pin['archive_sha256'],'d673313799df319cf26873981d017458b58e91cc70320f1d66c3b59c405af06b')
        archive=ROOT/'benchmarks/results/whisper-turbo-cpu-20261027/corpus.tar'
        if not archive.exists(): self.skipTest('Pinned public corpus is an external artifact')
        target=self.directory/'corpus'; result=m.pinned_corpus(archive,target)
        self.assertEqual(result['danish_fixtures'],18)
        self.assertEqual(len(list(target.glob('*.wav'))),18)
        self.assertEqual(len(json.loads((target/'manifest.json').read_text())['fixtures']),18)
    def test_wrong_corpus_hash_is_refused_before_extraction(self):
        archive=self.directory/'corpus.tar'; archive.write_bytes(b'unreviewed')
        target=self.directory/'corpus'
        with self.assertRaises(ValueError): m.pinned_corpus(archive,target)
        self.assertFalse(target.exists())
    def test_shared_result_is_finite_and_contains_no_private_payloads(self):
        private='PRIVATE fixture prompt token audio data'
        (self.directory/'baseline.json').write_text(json.dumps({'prompt':private,'environment':private,
            'capacity':{'passed':False,'minimum_available_ratio':.05},
            'runtime':{'budgets':{'hviske':'0.10','plapre':'0.06'}}}))
        (self.directory/'candidate.json').write_text(json.dumps({'raw_plan':private,'capacity_qualified':True,
            'asr_latency_pass':True,'tts_model_local_latency_pass':False,
            'capacity':{'passed':True,'minimum_available_ratio':.11},
            'runtime':{'budgets':{'hviske':'0.05','plapre':'0.05'}}}))
        (self.directory/'result.json').write_text(json.dumps({'state':'capacity-candidate','private':private}))
        result=m.public_result(self.directory,'a'*40,'capacity-candidate')
        self.assertNotIn(private,json.dumps(result))
        self.assertFalse(result['full_voice_accepted']); self.assertFalse(result['accepted'])
        self.assertEqual(result['candidate']['budgets'],{'hviske':'0.05','plapre':'0.05'})
        self.assertEqual(len(result['private_receipt']['sha256']),64)
        path=self.directory/'rollout-result.json'; m.publish_result(path,result)
        self.assertEqual(path.stat().st_mode&0o777,0o644)
        with self.assertRaises(FileExistsError): m.publish_result(path,result)
        path.unlink(); path.symlink_to(self.directory/'result.json')
        with self.assertRaises(FileExistsError): m.publish_result(path,result)


if __name__=='__main__': unittest.main()
