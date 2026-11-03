#!/usr/bin/env python3
"""Offline candidate isolation and ownership-fenced timeout cleanup."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('candidate',ROOT/'scripts/qualify-openclaw-managed-candidate.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class CandidateTest(unittest.TestCase):
    def image_metadata(self):
        data=m.policy();proof=m.manifest_proof(data)
        return {'Id':data['image_config'],'Architecture':'amd64','Os':'linux','RepoDigests':[data['image']],
          'Config':{'User':'sandbox','Labels':{'org.opencontainers.image.revision':data['nemoclaw_commit'],
          'org.opencontainers.image.version':data['nemoclaw_tag'],'io.nvidia.nemoclaw.agent':'openclaw',
          'io.nvidia.nemoclaw.managed-image.cohort':data['cohort'],
          'io.nvidia.nemoclaw.managed-image.platform':'linux/amd64',
          'io.nvidia.nemoclaw.managed-image.contract':'1','io.nvidia.nemoclaw.managed-image.startup-profile':'1',
          'io.nvidia.nemoclaw.managed-image.capabilities':'1'}}}

    def test_classic_and_containerd_ids_require_the_same_exact_manifest_config_chain(self):
        data=m.policy();image=self.image_metadata();proof=m.manifest_proof(data)
        self.assertEqual(m.provenance(data,image)['image_id_kind'],'config')
        image.update(Id=proof['manifest_digest'],Descriptor={'digest':proof['manifest_digest'],
            'mediaType':'application/vnd.oci.image.manifest.v1+json','size':proof['manifest_size']})
        self.assertEqual(m.provenance(data,image)['image_id_kind'],'manifest')
        for field,value in [('Id','sha256:'+'0'*64),('RepoDigests',[]),('Descriptor',None),('Architecture','arm64')]:
            with self.assertRaisesRegex(ValueError,'provenance'):m.provenance(data,{**image,field:value})
        image['Descriptor']['digest']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'provenance'):m.provenance(data,image)

    def test_altered_manifest_or_config_pin_never_gets_blessed(self):
        data=m.policy();raw=(ROOT/m.MANIFEST).read_bytes()
        with self.assertRaisesRegex(ValueError,'content digest'):m.manifest_proof(data,raw+b' ')
        with self.assertRaisesRegex(ValueError,'config chain'):m.manifest_proof({**data,'image_config':'sha256:'+'0'*64},raw)
        image=self.image_metadata();image['Config']['Labels']['io.nvidia.nemoclaw.managed-image.cohort']='unreviewed'
        with self.assertRaisesRegex(ValueError,'provenance'):m.provenance(data,image)

    def test_temporary_overlay_reuses_tracked_changes_and_pins_both_hashes(self):
        payload=m.child_overlay(m.policy())
        self.assertEqual(len(payload['changes']),3)
        self.assertEqual(payload['before_sha256'],m.policy()['runtime_modules']['child_result']['sha256'])
        self.assertEqual(payload['after_sha256'],m.policy()['child_result_overlay_sha256'])
        self.assertIn('children.length === 1 ? 3072',payload['changes'][-1][1])
        self.assertIn('scripts/setup-openclaw-child-results.py',m.FILES)

    def test_unexecuted_empty_or_truncated_child_fixture_never_passes_child_gate(self):
        keys=('executed','single_child_retains_evidence','aggregate_bound',
              'aggregate_retains_evidence','aggregate_reports_omission')
        fixture={key:True for key in keys}
        self.assertTrue(m.child_evidence_ok({'native_child_fixture':fixture}))
        self.assertFalse(m.child_evidence_ok({}))
        for key in keys:
            for value in (False,None,'true'):
                self.assertFalse(m.child_evidence_ok({'native_child_fixture':{**fixture,key:value}}))

    def test_missing_or_partial_native_regressions_cannot_pass(self):
        keys=('executed','native_loader','native_safety_policy','native_hook_failure_closed',
              'native_hook_veto_sticky','native_ingress_gate','native_ingress_shutdown',
              'native_yield_acknowledgment','ordinary_warning_preserved',
              'terminal_answer_preserved','quiet_ack_rules_preserved')
        fixture={key:True for key in keys}
        self.assertTrue(m.native_regressions_ok({'native_regression_fixture':fixture}))
        self.assertFalse(m.native_regressions_ok({}))
        for key in keys:
            for value in (False,None,'true'):
                self.assertFalse(m.native_regressions_ok({'native_regression_fixture':{**fixture,key:value}}))

    def test_offline_probe_cannot_mount_credentials_publish_ports_or_contact_network(self):
        data=m.policy();args=m.command(data,'synthetic')
        for key,value in [('--network','none'),('--cap-drop','ALL'),('--entrypoint','/usr/local/bin/node')]:
            self.assertEqual(args[args.index(key)+1],value)
        self.assertIn('--read-only',args)
        for key in ['--volume','-v','--mount','--env-file','--privileged','--publish','-p']:
            self.assertNotIn(key,args)
        self.assertEqual(args[args.index('--memory')+1],args[args.index('--memory-swap')+1])
        self.assertFalse(data['production_qualified'])

    def test_mutable_image_is_refused_before_execution(self):
        value=m.policy();value['image']='ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.140'
        with patch.object(m.json,'loads',return_value=value),self.assertRaisesRegex(ValueError,'candidate policy'):
            m.policy()

    def test_cleanup_removes_only_exact_owned_container_id(self):
        name='hc-oc140-synthetic';identifier='a'*64;calls=[]
        def call(args,**kwargs):
            calls.append(args)
            if args[1]=='ps':text=identifier[:12]+'\n'
            elif args[1]=='inspect':text=json.dumps([{'Id':identifier,'Name':'/'+name,'Config':{'Labels':{'org.homecompute.candidate':name}}}])
            else:text=''
            return subprocess.CompletedProcess(args,0,text,'')
        m.cleanup(call,name)
        self.assertEqual(calls[-1],['docker','rm','--force',identifier])

    def test_unknown_or_ambiguous_container_is_never_removed(self):
        for ids in ['a'*12+'\n'+'b'*12,'a'*12]:
            calls=[]
            def call(args,**kwargs):
                calls.append(args)
                text=ids if args[1]=='ps' else json.dumps([{'Name':'/production','Config':{'Labels':{}}}])
                return subprocess.CompletedProcess(args,0,text,'')
            with self.assertRaises(RuntimeError):m.cleanup(call,'synthetic')
            self.assertFalse(any(a[1]=='rm' for a in calls))

    def test_unpublished_checkout_is_refused(self):
        with self.assertRaisesRegex(ValueError,'published candidate'):
            m.published('a'*40,m.bundle())

    def test_heavy_restore_pressure_or_unknown_metrics_refuse_candidate_admission(self):
        data=m.policy()
        m.admit(data,8*1024**3,20*1024**3,'some avg10=0.00\nfull avg10=0.20 avg60=0.10\n')
        for available,free,pressure in [(8*1024**3,20*1024**3,'full avg10=96.00\n'),
                (1024**3,20*1024**3,'full avg10=0.00\n'),(8*1024**3,1024**3,'full avg10=0.00\n'),
                (8*1024**3,20*1024**3,'unavailable'),(8*1024**3,20*1024**3,'full avg10=101.00\n')]:
            with self.assertRaises(ValueError):m.admit(data,available,free,pressure)


if __name__=='__main__':unittest.main()
