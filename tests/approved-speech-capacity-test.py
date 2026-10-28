#!/usr/bin/env python3
"""Exact reviewed approval without stdin; pinned trial still owns rollback."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
c=module('approved_controller',ROOT/'scripts/run-approved-speech-capacity.py')
b=module('approved_bootstrap',ROOT/'scripts/bootstrap-approved-speech-capacity.py')


class ApprovedCapacityTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve();self.source=self.root/'qualification';scripts=self.source/'scripts';scripts.mkdir(parents=True)
        for name in ('spark-rollout.py','speech_capacity.py'):shutil.copy2(ROOT/'scripts'/name,scripts/name)
        self.original_path=list(sys.path);self.original_capacity=sys.modules.pop('speech_capacity',None)
        self.addCleanup(self.restore_imports)
        with patch.object(c,'QUALIFICATION_ROOT',self.source):self.pinned=c.load_pinned()
        self.directory=self.root/'trial';self.directory.mkdir(mode=0o700)
        self.marker=self.root/'active.json';self.calls=[];self.measures=[]
    def restore_imports(self):
        sys.path[:]=self.original_path;sys.modules.pop('speech_capacity',None)
        if self.original_capacity is not None:sys.modules['speech_capacity']=self.original_capacity
        sys.modules.pop('approved_pinned_spark_rollout',None)
    def helper(self,name,*args,**kwargs):
        self.calls.append(args[0])
        if args[0]=='plan':return {'plan_sha256':c.APPROVED_PLAN}
        if args[0]=='apply':
            self.assertEqual(args[1:],('--plan-sha256',c.APPROVED_PLAN,'--speech-idle'))
            self.marker.write_text('{}');return {}
        if args[0]=='rollback':self.marker.unlink();return {}
        self.fail('unexpected lifecycle action')
    def measure(self,label,directory):
        self.measures.append(label)
        if label=='baseline':return {'asr':{'failures':0,'trials':18},'tts_model_local':{'failures':0,'trials':20}}
        return {'capacity_qualified':True,'asr_latency_pass':False,'tts_model_local_latency_pass':False}
    def run_trial(self,helper=None,measure=None):
        c.install_approved_trial(self.pinned,c.APPROVED_PLAN)
        with patch.object(self.pinned,'CAPACITY_STATE',self.marker),patch.object(self.pinned,'helper',side_effect=helper or self.helper), \
             patch.object(self.pinned,'benchmark',side_effect=measure or self.measure),patch.object(self.pinned,'idle'), \
             patch.object(self.pinned,'confirm_plan',side_effect=AssertionError('stdin approval must not run')), \
             patch.object(self.pinned.sys,'stdin',io.StringIO('')):
            return self.pinned.trial(self.directory)

    def test_exact_approved_plan_runs_fresh_baseline_and_candidate_with_eof(self):
        result=self.run_trial()
        self.assertEqual(self.measures,['baseline','candidate'])
        self.assertEqual(self.calls,['plan','plan','apply'])
        self.assertTrue(result['capacity_qualified']);self.assertFalse(result['accepted']);self.assertFalse(result['full_voice_accepted'])
        self.assertTrue(self.marker.exists())

    def test_changed_plan_before_baseline_never_measures_or_applies(self):
        def different(name,*args,**kwargs):self.calls.append(args[0]);return {'plan_sha256':'b'*64}
        with self.assertRaises(c.ApprovedPlanMismatch):self.run_trial(helper=different)
        self.assertEqual(self.calls,['plan']);self.assertEqual(self.measures,[]);self.assertFalse(self.marker.exists())

    def test_changed_plan_after_baseline_never_applies(self):
        def different(name,*args,**kwargs):
            self.calls.append(args[0]);return {'plan_sha256':c.APPROVED_PLAN if len(self.calls)==1 else 'b'*64}
        with self.assertRaisesRegex(ValueError,'changed during the baseline'):self.run_trial(helper=different)
        self.assertEqual(self.calls,['plan','plan']);self.assertEqual(self.measures,['baseline']);self.assertFalse(self.marker.exists())

    def test_unqualified_candidate_uses_pinned_rollback_and_false_acceptance(self):
        def failed(label,directory):return self.measure(label,directory) if label=='baseline' else {'capacity_qualified':False}
        with self.assertRaises(ValueError):self.run_trial(measure=failed)
        self.assertEqual(self.calls,['plan','plan','apply','rollback']);self.assertFalse(self.marker.exists())
        result=json.loads((self.directory/'result.json').read_bytes())
        self.assertEqual(result['state'],'baseline-restored');self.assertFalse(result['full_voice_accepted'])

    def test_signal_during_candidate_uses_original_trial_recovery(self):
        def interrupted(label,directory):
            if label=='candidate':self.pinned.interrupted()
            return self.measure(label,directory)
        with self.assertRaises(InterruptedError):self.run_trial(measure=interrupted)
        self.assertEqual(self.calls,['plan','plan','apply','rollback']);self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads((self.directory/'result.json').read_bytes())['state'],'baseline-restored')

    def test_only_trial_is_wrapped_and_old_main_receives_old_revision(self):
        unchanged={name:getattr(self.pinned,name) for name in ('main','paused_ingress','interrupted','helper','release')}
        c.install_approved_trial(self.pinned,c.APPROVED_PLAN)
        for name,value in unchanged.items():self.assertIs(getattr(self.pinned,name),value)
        received=[];prior=list(sys.argv)
        self.pinned.main=lambda:received.extend(sys.argv)
        with patch.object(c,'QUALIFICATION_ROOT',self.source):c.execute_pinned(self.pinned,Path('/public/corpus.tar'))
        self.assertEqual(received,[str(self.source/'scripts/spark-rollout.py'),'--revision',c.QUALIFICATION_REVISION,'--corpus-archive','/public/corpus.tar'])
        self.assertEqual(sys.argv,prior)
        self.assertEqual(self.pinned.ROOT,self.source)

    def test_another_plan_cannot_be_approved_by_this_controller(self):
        for value in ('','a'*64,c.APPROVED_PLAN+'0'):
            with self.subTest(value=value),self.assertRaises(c.ApprovedPlanMismatch):c.install_approved_trial(self.pinned,value)

    def test_imported_speech_library_from_a_newer_source_is_refused(self):
        with patch.object(c,'QUALIFICATION_ROOT',self.source),patch.dict(sys.modules,{'speech_capacity':SimpleNamespace(__file__='/untrusted/speech_capacity.py')}):
            with self.assertRaisesRegex(ValueError,'another-release'):c.load_pinned()

    def test_release_verification_rejects_dirty_origin_or_revision_without_mutation(self):
        revision='a'*40;path=c.RELEASES/revision
        for index in range(3):
            responses=[revision,'',c.REPOSITORY,''];responses[index]=['b'*40,' M scripts/source.py','https://example.invalid/foreign.git'][index]
            with self.subTest(index=index),patch.object(c,'protected'),patch.object(c,'run',side_effect=responses) as run:
                with self.assertRaises(ValueError):c.verify_release(path,revision)
                self.assertTrue(all(call.args[0][0]=='git' for call in run.call_args_list))
        with patch.object(c,'protected') as guard,self.assertRaises(ValueError):c.verify_release(Path('/tmp/unpublished'),revision)
        guard.assert_not_called()

    def test_bootstrap_never_replaces_an_existing_dirty_release(self):
        releases=self.root/'releases';target=releases/('a'*40);target.mkdir(parents=True)
        retained=target/'retained';retained.write_text('keep')
        with patch.object(b,'RELEASES',releases),patch.object(b,'protected'),patch.object(b,'verify',side_effect=ValueError('dirty')),patch.object(b,'run') as run:
            with self.assertRaises(ValueError):b.materialize('a'*40)
        self.assertEqual(retained.read_text(),'keep');run.assert_not_called()

    def test_both_help_paths_are_noninteractive_and_need_no_privileges(self):
        for name in ('bootstrap-approved-speech-capacity.py','run-approved-speech-capacity.py'):
            result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts'/name),'--help'],stdin=subprocess.DEVNULL,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--approve-plan-sha256',result.stdout)


if __name__=='__main__':unittest.main()
