#!/usr/bin/env python3
"""Synthetic finalization evidence, two-file rollback and ownership boundaries."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
m=module('speech_finalize',ROOT/'scripts/finalize-speech-capacity.py')
b=module('speech_finalize_bootstrap',ROOT/'scripts/bootstrap-speech-finalization.py')


def documents(source: Path):
    result={}
    for key,role in m.ROLES.items():
        command=(['export VLLM_API_KEY="$$(cat /fixture/key)"\nexec vllm serve pinned --gpu-memory-utilization "0.05" --max-num-seqs "4"']
                 if key=='hviske' else ['--gpu-mem','0.05','--max-model-len','512'])
        service={'image':'sha256:'+('a' if key=='hviske' else 'b')*64,'command':command,
                 'entrypoint':['/bin/sh','-ec'] if key=='hviske' else ['plapre-serve'],
                 'environment':{'PUBLIC_SETTING':'preserved'},'networks':{'inference':{}}}
        if key=='plapre':service['build']={'context':str(source),'dockerfile':'pinned.Dockerfile','args':{'MODEL_REVISION':'unchanged'}}
        result[key]={'name':key+'-fixture','services':{role['service']:service},
                     'networks':{'inference':{'name':key+'-fixture_inference','internal':True}}}
    return result


def actuals(values,owner: Path,roles):
    result={}
    for key,document in values.items():
        service=document['services'][roles[key]['service']]
        result[key]={'Image':service['image'],'Config':{'Cmd':[x.replace('$$','$') for x in service['command']],
            'Entrypoint':service['entrypoint'],'Env':['PUBLIC_SETTING=preserved'],
            'Labels':{'com.docker.compose.project':document['name'],
                      'com.docker.compose.project.config_files':str(owner/(key+'-candidate.json'))}},
            'State':{'Running':True,'Health':{'Status':'healthy'}},'Mounts':[],
            'NetworkSettings':{'Networks':{document['networks']['inference']['name']:{'IPAddress':'172.30.1.'+('2' if key=='hviske' else '3')}}}}
    return result


class FinalizationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.capacity=self.root/'capacity';self.capacity.mkdir(mode=0o700)
        self.trials=self.root/'trials';self.trials.mkdir(mode=0o700)
        self.releases=self.root/'releases';self.old=self.releases/m.QUALIFICATION_REVISION
        (self.old/'config').mkdir(parents=True);(self.old/'scripts').mkdir()
        shutil.copy2(ROOT/'scripts/speech_capacity.py',self.old/'scripts/speech_capacity.py')
        shutil.copy2(ROOT/'config/spark-rollout-corpus.json',self.old/'config/spark-rollout-corpus.json')
        self.canonical=self.releases/('c'*40);self.txdir=self.root/'transaction';self.txdir.mkdir(mode=0o700)
        self.roles=deepcopy(m.ROLES)
        for key,role in self.roles.items():role['env']=str(self.root/(key+'.production.env'))
        self.candidate=documents(self.old);self.canon=documents(self.canonical)
        self.plan_dir=self.capacity/('d'*64);self.plan_dir.mkdir(mode=0o700)
        self.observed=actuals(self.candidate,self.plan_dir,self.roles)
        self.hashes={key:m.runtime_contract(value,key)[0] for key,value in self.observed.items()}
        self.active=b'{"synthetic":"qualified-active-marker"}'
        (self.capacity/'active.json').write_bytes(self.active)
        (self.txdir/'active.before.json').write_bytes(self.active)
        meta={}
        for key,role in self.roles.items():
            before=(role['setting']+'='+role['old']+'\nPUBLIC_SETTING=preserved\n').encode()
            projected=m.patch_budget(before,role['setting'],role['old'])
            Path(role['env']).write_bytes(before)
            (self.txdir/(key+'.env.before')).write_bytes(before);(self.txdir/(key+'.env.projected')).write_bytes(projected)
            meta[key]={'uid':0,'gid':0,'mode':0o640,'before_sha256':m.digest(before),'projected_sha256':m.digest(projected)}
        self.tx=m.Transaction(self.txdir,{'phase':'preparing','revision':self.canonical.name,'trial':'trial-fixture',
            'qualification_sha256':'e'*64,'active_sha256':m.digest(self.active),'nonce':'f'*32,
            'plan_sha256':self.plan_dir.name,'runtime_hashes':self.hashes,
            'images':{k:v['services'][self.roles[k]['service']]['image'] for k,v in self.candidate.items()},
            'ingress_active':[m.INGRESS[0]],'env':meta})
        self.proof={'documents':self.candidate,'directory':self.plan_dir,'runtime_hashes':self.hashes,
                    'policy':SimpleNamespace(activation_approved=lambda *_:None)}
        self.events=[]
    def atomic(self,path,body,**kwargs):
        self.assertTrue(path.resolve().is_relative_to(self.root))
        path.write_bytes(body);path.chmod(0o600)
    def protected(self,path,**kwargs):
        self.assertTrue(path.absolute().is_relative_to(self.root))
        info=path.lstat()
        if path.is_symlink():raise ValueError('fixture symlink refused')
        return info
    def canonical_start(self,key):
        self.events.append('canonical-'+key)
        labels=self.observed[key]['Config']['Labels']
        labels['com.docker.compose.project.config_files']=str(self.canonical/self.roles[key]['compose'])
        labels['com.docker.compose.project.environment_file']=self.roles[key]['env']
    def host_run(self,args,**kwargs):
        if args[:2]==['docker','compose']:
            path=Path(args[args.index('-f')+1]);key=next(k for k in self.roles if path.name==k+'-candidate.json')
            self.events.append('restored-'+key)
            self.observed[key]=actuals(self.candidate,self.plan_dir,self.roles)[key]
            return ''
        self.fail('test attempted an unexpected host operation')
    def mocked(self,*,start=None,cleanup_failure=False):
        outer=self
        class FakeMaintenance:
            cleaned=False
            def __init__(self,tx):pass
            def __enter__(self):outer.events.append('gated-and-drained');return self
            def refresh(self):outer.events.append('gate-refreshed')
            def __exit__(self,*_):
                outer.events.append('ingress-restored')
                if cleanup_failure:raise RuntimeError('synthetic intake restoration failure')
                self.cleaned=True
        stack=ExitStack()
        for name,value in [('ROOT',self.canonical),('ROLES',self.roles),('CAPACITY',self.capacity),('atomic',self.atomic),
                           ('protected',self.protected),('inspect',lambda key:self.observed[key]),('run',self.host_run),
                           ('Maintenance',FakeMaintenance),('start_canonical',start or self.canonical_start),
                           ('render',lambda key,path:self.canon[key])]:stack.enter_context(patch.object(m,name,value))
        return stack

    def evidence_fixture(self):
        trial=self.trials/'trial-proof';trial.mkdir(mode=0o700)
        original=deepcopy(self.candidate)
        original['hviske']['services']['hviske-primary']['command'][0]=original['hviske']['services']['hviske-primary']['command'][0].replace('"0.05"','"0.10"')
        original['plapre']['services']['plapre-primary']['command'][1]='0.06'
        plan={'source_revision':m.QUALIFICATION_REVISION,'schema_version':1,'state':'qualification-required',
              'baseline_sha256':m.digest(original),'candidate_sha256':m.digest(self.candidate)}
        plan['plan_sha256']=m.digest(plan);directory=self.capacity/plan['plan_sha256'];directory.mkdir(mode=0o700)
        for label,values in [('baseline',original),('candidate',self.candidate)]:
            for key,document in values.items():self.json(directory/(key+'-'+label+'.json'),document)
        self.json(self.capacity/'active.json',plan);self.json(trial/'plan.json',plan)
        baseline={'label':'baseline','corpus_sha256':'f'*64,'asr_concurrency':4,'tts_concurrency':1,
            'runtime':{'budgets':{'hviske':'0.10','plapre':'0.06'},'images':self.tx.data['images'],'contract_sha256':self.hashes},
            'asr':{'wer':.10,'failures':0,'trials':18,'p95_rtf':.4,'p95_elapsed_seconds':1.0},
            'tts_model_local':{'failures':0,'trials':20,'p95_rtf':.7,'p95_elapsed_seconds':6.0,'p95_first_audio_seconds':5.5}}
        candidate=deepcopy(baseline);candidate.update(label='candidate',capacity_qualified=True,observation_failures=0)
        candidate['runtime']['budgets']={'hviske':'0.05','plapre':'0.05'}
        candidate['capacity']={'passed':True,'samples':10,'minimum_available_ratio':.12,'minimum_llm_running':2}
        self.json(trial/'baseline.json',baseline);self.json(trial/'candidate.json',candidate)
        result={'state':'capacity-candidate','source_revision':m.QUALIFICATION_REVISION,'plan_sha256':plan['plan_sha256'],
                'capacity_qualified':True,'accepted':False,'full_voice_accepted':False,'tts_model_local_latency_pass':False}
        self.json(trial/'result.json',result)
        for label in ('baseline','candidate'):
            self.json(trial/('load-'+label+'.json'),{'streams':2,'failed_requests':0,'outputs_retained':False,
                'maximum_output_tokens_per_request':4096,'maximum_seconds':600,'accepted_requests_per_worker':[1,1]})
        pin=json.loads((self.old/'config/spark-rollout-corpus.json').read_bytes())
        self.json(trial/'corpus-pin.json',{'archive_sha256':pin['archive_sha256'],'revision':pin['revision'],'danish_fixtures':18})
        return trial,m.digest((trial/'result.json').read_bytes())
    def json(self,path,value):path.write_text(json.dumps(value));path.chmod(0o600)
    def read_evidence(self,trial,expected):
        with patch.object(m,'TRIALS',self.trials),patch.object(m,'CAPACITY',self.capacity),patch.object(m,'RELEASES',self.releases), \
             patch.object(m,'protected',side_effect=self.protected),patch.object(m,'published'):
            return m.evidence(trial,expected)

    def test_budget_patch_changes_only_one_value_and_preserves_comments_quotes_and_crlf(self):
        body=b'PUBLIC=preserved\r\nHVISKE_GPU_MEMORY_UTILIZATION="0.10" # reviewed\r\nOTHER=unchanged\r\n'
        self.assertEqual(m.patch_budget(body,'HVISKE_GPU_MEMORY_UTILIZATION','0.10'),body.replace(b'"0.10"',b'"0.05"'))
        for body in (b'K=0.10\nK=0.10\n',b'OTHER=0.10\n',b'K=$(command)\n',b'K=0.1\n'):
            with self.assertRaises(ValueError):m.patch_budget(body,'K','0.10')

    def test_capacity_only_evidence_with_false_voice_acceptance_is_expected(self):
        trial,expected=self.evidence_fixture();proof=self.read_evidence(trial,expected)
        self.assertEqual(proof['plan']['source_revision'],m.QUALIFICATION_REVISION)
        self.assertEqual(proof['documents'],self.candidate)
        self.assertFalse(json.loads((trial/'result.json').read_bytes())['full_voice_accepted'])

    def test_unqualified_or_voice_accepted_receipt_is_refused_even_with_reviewed_hash(self):
        trial,_=self.evidence_fixture()
        original=json.loads((trial/'result.json').read_bytes())
        for updates in ({'capacity_qualified':False},{'accepted':True},{'full_voice_accepted':True},{'state':'baseline-restored'}):
            self.json(trial/'result.json',{**original,**updates});expected=m.digest((trial/'result.json').read_bytes())
            with self.subTest(updates=updates),self.assertRaisesRegex(ValueError,'qualified-active-plan'):self.read_evidence(trial,expected)

    def test_changed_private_candidate_or_reviewed_result_is_refused(self):
        trial,expected=self.evidence_fixture()
        with self.assertRaisesRegex(ValueError,'reviewed-result'):self.read_evidence(trial,'0'*64)
        active=json.loads((self.capacity/'active.json').read_bytes());path=self.capacity/active['plan_sha256']/'hviske-candidate.json'
        value=json.loads(path.read_bytes());value['services']['hviske-primary']['command'][0]+=' --max-num-seqs 1';self.json(path,value)
        with self.assertRaisesRegex(ValueError,'private-candidate-plan-changed'):self.read_evidence(trial,expected)

    def test_headroom_load_and_latency_regression_failures_cannot_be_finalized(self):
        trial,expected=self.evidence_fixture();original=json.loads((trial/'candidate.json').read_bytes())
        for change in ('headroom','nan','latency','observations'):
            value=deepcopy(original)
            if change=='headroom':value['capacity']['minimum_available_ratio']=.09
            if change=='nan':value['capacity']['minimum_available_ratio']=float('nan')
            if change=='latency':value['tts_model_local']['p95_first_audio_seconds']=10
            if change=='observations':value['observation_failures']=1
            self.json(trial/'candidate.json',value)
            with self.subTest(change=change),self.assertRaises(ValueError):self.read_evidence(trial,expected)
        self.json(trial/'candidate.json',original)
        self.json(trial/'load-candidate.json',{'streams':2,'failed_requests':0,'outputs_retained':False,
            'maximum_output_tokens_per_request':4096,'maximum_seconds':600,'accepted_requests_per_worker':[1,0]})
        with self.assertRaisesRegex(ValueError,'controlled-load'):self.read_evidence(trial,expected)

    def test_contract_allows_only_build_context_ownership_change(self):
        self.assertEqual(m.selected_contract(self.candidate['plapre'],'plapre'),m.selected_contract(self.canon['plapre'],'plapre'))
        changed=deepcopy(self.canon['plapre']);changed['services']['plapre-primary']['build']['args']['MODEL_REVISION']='different'
        self.assertNotEqual(m.selected_contract(changed,'plapre'),m.selected_contract(self.candidate['plapre'],'plapre'))
        changed=deepcopy(self.canon['hviske']);changed['services']['hviske-primary']['command'][0]+=' --max-model-len 1'
        self.assertNotEqual(m.selected_contract(changed,'hviske'),m.selected_contract(self.candidate['hviske'],'hviske'))

    def test_current_unpromoted_defaults_refuse_before_production_env_or_commands(self):
        fake=self.root/'unpromoted';path=fake/self.roles['hviske']['compose'];path.parent.mkdir(parents=True)
        path.write_text('${HVISKE_GPU_MEMORY_UTILIZATION:-0.10}')
        with patch.object(m,'ROOT',fake),patch.object(m,'run') as run:
            with self.assertRaisesRegex(ValueError,'publish-qualified-canonical-defaults-first'):m.canonical_preflight(self.tx,{})
            run.assert_not_called()

    def prepare_canonical_source_fixture(self):
        for key,role in self.roles.items():
            path=self.canonical/role['example'];path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(role['setting']+'=0.05\n')
        path=self.canonical/self.roles['hviske']['compose'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('${HVISKE_GPU_MEMORY_UTILIZATION:-0.05}\n')

    def test_canonical_preflight_stages_only_private_env_and_checks_owning_validators(self):
        self.prepare_canonical_source_fixture();before={key:Path(role['env']).read_bytes() for key,role in self.roles.items()};calls=[]
        with self.mocked(),patch.object(m,'run',side_effect=lambda args,**kw:calls.append(args) or ''):
            value=m.canonical_preflight(self.tx,self.proof)
        self.assertEqual(value,self.canon)
        self.assertEqual({key:Path(role['env']).read_bytes() for key,role in self.roles.items()},before)
        self.assertEqual(len(calls),2);self.assertTrue(all(command[0]=='/bin/bash' and 'validate' in command for command in calls))
        self.assertEqual(self.events,[])

    def test_textual_default_marker_does_not_waive_a_different_effective_compose_default(self):
        self.prepare_canonical_source_fixture()
        def render(key,path):
            value=deepcopy(self.canon[key])
            if path.name.endswith('.fallback'):
                value['services'][self.roles[key]['service']]['command'][0]=value['services'][self.roles[key]['service']]['command'][0].replace('"0.05"','"0.10"')
            return value
        before={key:Path(role['env']).read_bytes() for key,role in self.roles.items()}
        with self.mocked(),patch.object(m,'run',return_value=''),patch.object(m,'render',side_effect=render):
            with self.assertRaisesRegex(ValueError,'effective-canonical-default'):m.canonical_preflight(self.tx,self.proof)
        self.assertEqual({key:Path(role['env']).read_bytes() for key,role in self.roles.items()},before)

    def test_runtime_hash_is_identical_to_the_qualification_benchmark_definition(self):
        bench=module('finalization_benchmark_contract',ROOT/'benchmarks/speech_capacity_benchmark.py')
        names={role['container']:key for key,role in self.roles.items()}
        with patch.object(bench.subprocess,'check_output',side_effect=lambda args,**kw:json.dumps([self.observed[names[args[-1]]]])):
            value=bench.runtime()
        self.assertEqual(value['contract_sha256'],self.hashes)
        self.assertEqual(value['budgets'],{'hviske':'0.05','plapre':'0.05'})

    def test_finalization_retires_marker_only_after_both_canonical_owners_and_health(self):
        with self.mocked():result=m.finalize(self.tx,self.proof,self.canon)
        self.assertEqual([x for x in self.events if x.startswith('canonical-')],['canonical-hviske','canonical-plapre'])
        self.assertFalse((self.capacity/'active.json').exists());self.assertEqual((self.txdir/'active.retired.json').read_bytes(),self.active)
        self.assertEqual(self.tx.data['phase'],'completed');self.assertTrue(result['capacity_qualified'])
        self.assertFalse(result['accepted']);self.assertFalse(result['full_voice_accepted'])
        for key,role in self.roles.items():self.assertEqual(Path(role['env']).read_bytes(),(self.txdir/(key+'.env.projected')).read_bytes())

    def test_second_start_failure_restores_prior_env_and_qualified_candidate_only(self):
        def fail(key):
            if key=='plapre':raise RuntimeError('synthetic second startup failure')
            self.canonical_start(key)
        with self.mocked(start=fail),self.assertRaises(RuntimeError):m.finalize(self.tx,self.proof,self.canon)
        self.assertEqual(self.tx.data['phase'],'candidate-restored');self.assertEqual((self.capacity/'active.json').read_bytes(),self.active)
        for key,role in self.roles.items():self.assertEqual(Path(role['env']).read_bytes(),(self.txdir/(key+'.env.before')).read_bytes())
        self.assertIn('restored-hviske',self.events)
        self.assertNotIn('restored-plapre',self.events)  # Already matched its qualified candidate; preserve its process.

    def test_first_engine_failure_during_second_start_is_rechecked_before_retirement(self):
        def start(key):
            self.canonical_start(key)
            if key=='plapre':self.observed['hviske']['State']['Running']=False
        with self.mocked(start=start),self.assertRaisesRegex(ValueError,'contract-or-source-owner'):
            m.finalize(self.tx,self.proof,self.canon)
        self.assertTrue((self.capacity/'active.json').exists());self.assertFalse((self.txdir/'active.retired.json').exists())
        self.assertEqual(self.tx.data['phase'],'candidate-restored')

    def test_real_sigterm_during_canonical_start_restores_candidate_and_keeps_override(self):
        original=signal.getsignal(signal.SIGTERM)
        def interrupted(*_):raise InterruptedError('synthetic SIGTERM')
        def start(key):self.canonical_start(key);signal.raise_signal(signal.SIGTERM)
        signal.signal(signal.SIGTERM,interrupted)
        try:
            with self.mocked(start=start),self.assertRaises(InterruptedError):m.finalize(self.tx,self.proof,self.canon)
        finally:signal.signal(signal.SIGTERM,original)
        self.assertEqual(self.tx.data['phase'],'candidate-restored');self.assertTrue((self.capacity/'active.json').exists())

    def test_wrong_image_stale_health_or_source_owner_cannot_be_finalized(self):
        key='hviske';document=self.candidate[key];owner=self.plan_dir/(key+'-candidate.json')
        for target,replacement in [('image','sha256:'+'0'*64),('running',False),('owner','/unreviewed/project.json')]:
            observed=deepcopy(self.observed[key])
            if target=='image':observed['Image']=replacement
            if target=='running':observed['State']['Running']=replacement
            if target=='owner':observed['Config']['Labels']['com.docker.compose.project.config_files']=replacement
            with patch.object(m,'inspect',return_value=observed),self.assertRaisesRegex(ValueError,'contract-or-source-owner'):
                m.model_matches(document,key,self.hashes[key],owner)

    def test_unknown_external_env_edit_is_preserved_and_override_is_not_retired(self):
        Path(self.roles['hviske']['env']).write_bytes(b'EXTERNAL_CHANGE=preserve\n')
        with self.mocked(),self.assertRaises(ValueError):m.finalize(self.tx,self.proof,self.canon)
        self.assertEqual(Path(self.roles['hviske']['env']).read_bytes(),b'EXTERNAL_CHANGE=preserve\n')
        self.assertTrue((self.capacity/'active.json').exists());self.assertEqual(self.tx.data['phase'],'env-updating')

    def test_cleanup_failure_restores_override_and_reports_recovery_required(self):
        with self.mocked(cleanup_failure=True),self.assertRaises(RuntimeError):m.finalize(self.tx,self.proof,self.canon)
        self.assertTrue((self.capacity/'active.json').exists());self.assertEqual(self.tx.data['phase'],'recovery-required')

    def test_restoration_attempts_the_second_model_even_if_the_first_restore_fails(self):
        for key in self.roles:self.canonical_start(key)
        attempted=[]
        def run(args,**kwargs):
            key='hviske' if args[-1]=='hviske-primary' else 'plapre';attempted.append(key)
            if key=='hviske':raise RuntimeError('synthetic first restore failure')
            return self.host_run(args,**kwargs)
        with self.mocked(),patch.object(m,'run',side_effect=run),self.assertRaisesRegex(RuntimeError,'restoration-incomplete'):
            m.restore_candidate(self.tx,self.proof,SimpleNamespace(refresh=lambda:None))
        self.assertEqual(attempted,['hviske','plapre']);self.assertTrue((self.capacity/'active.json').exists())

    def test_corrupt_backup_is_refused_without_overwriting_any_production_env(self):
        (self.txdir/'hviske.env.before').write_bytes(b'corrupt')
        retained={key:Path(role['env']).read_bytes() for key,role in self.roles.items()}
        with self.mocked(),self.assertRaisesRegex(ValueError,'backup-changed'):
            m.restore_candidate(self.tx,self.proof,SimpleNamespace(refresh=lambda:None))
        self.assertEqual({key:Path(role['env']).read_bytes() for key,role in self.roles.items()},retained)
        self.assertFalse(any(event.startswith('restored-') for event in self.events))

    def test_completed_replay_verifies_owners_without_restarting_models(self):
        with self.mocked():
            m.finalize(self.tx,self.proof,self.canon);before=list(self.events)
            result=m.completed_status(self.tx,self.proof)
        self.assertEqual(self.events,before);self.assertTrue(result['already_completed'])
        self.assertFalse(result['accepted']);self.assertFalse(result['full_voice_accepted'])

    def test_report_failure_can_restore_retired_marker_and_accepted_candidate(self):
        with self.mocked(),patch.object(m,'recover_gate'):
            m.finalize(self.tx,self.proof,self.canon)
            m.abort_completed_transaction(self.tx,self.proof)
        self.assertEqual(self.tx.data['phase'],'candidate-restored')
        self.assertEqual((self.capacity/'active.json').read_bytes(),self.active)

    def test_public_report_is_finite_exclusive_and_contains_no_env_or_plan(self):
        with self.mocked():result=m.finalize(self.tx,self.proof,self.canon)
        value=m.report_metadata(self.tx,result);path=self.root/'finalization-result.json'
        m.report(path,value)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o644)
        self.assertNotIn('PUBLIC_SETTING',path.read_text());self.assertNotIn('command',path.read_text())
        self.assertNotIn('env',value);self.assertNotIn('plan',value)
        self.assertEqual(value['private_receipt']['sha256'],m.digest((self.txdir/'journal.json').read_bytes()))
        with self.assertRaises(FileExistsError):m.report(path,value)

    def test_gate_recovery_refuses_foreign_rules_and_cleans_only_exact_owned_gate(self):
        gate=m.Maintenance(self.tx)
        own=['-N '+gate.chain,'-A '+gate.chain+' -j RETURN','-A DOCKER-USER '+' '.join(gate.jump())]
        calls=[]
        def run(args,**kwargs):calls.append(args);return '\n'.join(own) if args[-1]=='-S' else ''
        with patch.object(m,'run',side_effect=run):m.recover_gate(self.tx)
        self.assertEqual([args[3] for args in calls[1:]],['-D','-F','-X'])
        self.assertTrue(all(gate.chain in args for args in calls[1:]))
        own.append('-A '+gate.chain+' -j ACCEPT');calls.clear()
        with patch.object(m,'run',side_effect=run),self.assertRaisesRegex(ValueError,'unrecognized-finalization-gate'):
            m.recover_gate(self.tx)
        self.assertEqual(len(calls),1)

    def test_bootstrap_does_not_overwrite_a_dirty_release(self):
        releases=self.root/'bootstrap-releases';target=releases/('a'*40);target.mkdir(parents=True)
        retained=target/'retained';retained.write_text('keep')
        with patch.object(b,'RELEASES',releases),patch.object(b,'protected'),patch.object(b,'verify',side_effect=ValueError('dirty')),patch.object(b,'run') as run:
            with self.assertRaises(ValueError):b.materialize('a'*40)
        run.assert_not_called();self.assertEqual(retained.read_text(),'keep')

    def test_canonical_start_is_exactly_two_models_without_dependencies_build_or_pull(self):
        calls=[]
        with patch.object(m,'ROOT',self.canonical),patch.object(m,'run',side_effect=lambda args,**kw:calls.append(args)):
            for key in m.ROLES:m.start_canonical(key)
        self.assertEqual(len(calls),2)
        for command,role in zip(calls,m.ROLES.values()):
            for required in ('--force-recreate','--no-deps','--no-build','--pull','never'):self.assertIn(required,command)
            self.assertEqual(command[-1],role['service']);self.assertIn(str(self.canonical/role['compose']),command)

    def test_gate_reentry_reinstalls_recorded_addresses_and_only_drops_new_target_flows(self):
        self.tx.data['addresses']=[{'address':'172.30.1.2','port':8000}]
        gate=m.Maintenance(self.tx);calls=[]
        with patch.object(m,'model_addresses',return_value=[('172.30.1.2',8000)]),patch.object(gate,'iptables',side_effect=lambda *args:calls.append(args)):
            gate.refresh()
        self.assertEqual(len(calls),1)
        for value in ('172.30.1.2/32','8000','NEW','DROP'):self.assertIn(value,calls[0])
        self.assertNotIn('ESTABLISHED',calls[0]);self.assertNotIn('ACCEPT',calls[0])

    def test_private_journal_cannot_resume_unreviewed_containers_or_env_owners(self):
        m.validate_journal(self.tx.data)
        for field,value in [('ingress_active',['unrelated-service']),('nonce','../../other'),('images',{})]:
            changed=deepcopy(self.tx.data);changed[field]=value
            with self.assertRaises(ValueError):m.validate_journal(changed)
        changed=deepcopy(self.tx.data);changed['env']['hviske']['uid']=1234
        with self.assertRaises(ValueError):m.validate_journal(changed)

    def test_idle_probe_allows_no_listener_startup_without_fabricating_metrics(self):
        import urllib.request
        with patch.object(Path,'read_text',return_value='sl local_address rem_address st\n'), \
             patch.object(sys,'argv',['probe','8000']),patch.object(urllib.request,'urlopen',side_effect=AssertionError('must not fetch absent listener')), \
             patch.object(sys,'stdout',io.StringIO()) as output:
            exec(m.IDLE_PROBE,{})
        self.assertEqual(json.loads(output.getvalue()),{'connections':0,'running':0,'waiting':0,'listening':0})

    def test_both_help_commands_need_no_privilege_or_stdin(self):
        for name in ('finalize-speech-capacity.py','bootstrap-speech-finalization.py'):
            value=subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts'/name),'--help'],stdin=subprocess.DEVNULL,capture_output=True,text=True)
            self.assertEqual(value.returncode,0,value.stderr);self.assertIn('--qualification-sha256',value.stdout)


if __name__=='__main__':unittest.main()
