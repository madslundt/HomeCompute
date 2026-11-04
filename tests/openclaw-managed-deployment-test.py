#!/usr/bin/env python3
import copy
import fcntl
import os
import sys
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deployment',ROOT/'scripts/upgrade-openclaw-nemoclaw.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ManagedDeployment(unittest.TestCase):
 def test_retained_repair_uses_transaction_locks_without_enabling_upgrade(self):
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp);state=home/'.local/state/homecompute-openclaw-supervisor';state.mkdir(parents=True)
   def mutation(*args):
    for name in ('error-recovery.lock','managed-upgrade.lock'):
     fd=os.open(state/name,os.O_WRONLY)
     try:
      with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
     finally:os.close(fd)
    return {'version_changed':False,'verified':True}
   argv=['tool','repair','--revision','a'*40,'--expected-hash','b'*64,'--expected-config-sha256','c'*64]
   with patch.object(m,'HOME',home),patch.object(m,'declaration',return_value={'production_qualified':False}),patch.object(m,'source_guard') as guard,patch.object(m,'qualified') as qualified,patch.object(m,'apply') as upgrade,patch.object(m,'repair_runtime',side_effect=mutation),patch.object(sys,'argv',argv),patch('builtins.print'):m.main()
   guard.assert_called_once_with('a'*40,'b'*64);qualified.assert_not_called();upgrade.assert_not_called()

 def test_retained_repair_refuses_a_new_managed_tuple_before_mutation(self):
  with patch.object(m,'environment',return_value={}),patch.object(m.SUP,'Supervisor',return_value=SimpleNamespace(revision=m.ASSETS.VERSIONS['0.0.140'])),patch.object(m,'user_service') as service:
   with self.assertRaisesRegex(ValueError,'retained 0.0.129'):m.repair_runtime(SimpleNamespace(),{})
   service.assert_not_called()

 def test_authenticated_mcp_probe_requires_resolution_and_complete_discovery(self):
  report={'adapter':{'registered':True},'provider':{'present':True,'attached':True,'credentialReady':True,'credentialResolution':{'ok':True}},'policy':{'present':True},'toolDiscovery':{'ok':True,'count':69,'truncated':False}}
  ctx=SimpleNamespace(cli='nemoclaw',run=lambda *a,**k:(0,json.dumps(report).encode()))
  self.assertEqual(m.verify_mcp_client(ctx),69)
  for section,key,value in (('adapter','registered',False),('toolDiscovery','count',68),('toolDiscovery','truncated',True)):
   original=report[section][key];report[section][key]=value
   with self.subTest(section=section,key=key),self.assertRaises(ValueError):m.verify_mcp_client(ctx)
   report[section][key]=original
  report['provider']['credentialResolution']['ok']=None
  with self.assertRaises(ValueError):m.verify_mcp_client(ctx)

 def test_journal_fences_uuid_and_generation_together(self):
  identifier='aabbccdd-0000-4000-8000-000000000001';generation='aabbccdd-0000-4000-8000-000000000002'
  record={'newId':identifier,'newGeneration':generation}
  self.assertEqual(m.recorded_generation(record,identifier),generation)
  with self.assertRaises(ValueError):m.recorded_generation(record,generation)
  with self.assertRaises(KeyError):m.recorded_generation({'newId':identifier},identifier)

 def test_nonzero_native_rebuild_preserves_verified_replacement_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   revision=m.ASSETS.VERSIONS['0.0.140'];profile=m.SUP.PROFILES[revision]
   old='aabbccdd-0000-4000-8000-000000000001';new='aabbccdd-0000-4000-8000-000000000002';generation='aabbccdd-0000-4000-8000-000000000003'
   registry=Path(tmp)/'registry.json';registry.write_text(json.dumps({'sandboxes':{m.SUP.NAME:{'lifecycleGeneration':generation,'agentVersion':profile['core'],'workload':{'reference':profile['image'],'sourceRevision':revision}}}}))
   observations=[(1,b'native finalization error'),(0,json.dumps({'id':new,'name':m.SUP.NAME,'phase':'Ready'}).encode())]
   ctx=SimpleNamespace(uuid=old,registry=registry,openshell='openshell',run=lambda *a,**k:observations.pop(0))
   stages=[];tx=SimpleNamespace(ctx=ctx,record={},stage=stages.append)
   with self.assertRaisesRegex(ValueError,'reviewed continuation'):m.rebuild_observed(tx,Path(tmp),revision,'native-rebuild','newId','newGeneration')
   self.assertEqual(tx.record['newId'],new);self.assertEqual(tx.record['newGeneration'],generation)
   self.assertEqual(tx.record['nativeRebuildExitCode'],1);self.assertEqual(stages,['native-rebuild-identity-observed'])

 def test_pending_probe_or_active_turn_blocks_before_stop(self):
  class Context:
   cli='nemoclaw'
   def check_registry(self):pass
   def check_container(self):pass
   def observe(self):return 'Ready'
   def ready(self):return True
   def run(self,*a,**k):return 0,b'{"active_turns":1}'
  with self.assertRaisesRegex(ValueError,'unresolved model probe'):m.require_idle(None,{'model_smoke_pending':True})
  with self.assertRaisesRegex(ValueError,'idle'):m.require_idle(Context(),{})

 def test_unqualified_or_unbound_proof_never_enables_deployment(self):
  policy=m.declaration()
  with self.assertRaises(ValueError):m.qualified(policy,{})
  policy.update(production_qualified=True,qualification_revision='a'*40,mattermost_version='2026.9.1')
  proof={k:True for k in ('accepted','native_upgrade_verified','native_rollback_verified','state_preserved','authenticated_gateway_verified','task_delivery_verified','agent_handoff_verified','native_yield_verified','mcp_binding_verified','operator_archives_verified','native_backup_verified','preparation_preserved','upgrade_state_preserved','rollback_state_preserved','installed_mattermost_ingress_verified','oci_transport_name_verified','native_sqlite_integrity','source_disk_read_only','registry_metadata_replay')}
  proof.update(network=False,production_modified=False,roles=12,native_sqlite_databases=39,source_revision='a'*40,assets_manifest_sha256=policy['qualification_assets_sha256'],mattermost_version='2026.9.1',policy_sha256=policy['qualification_policy_sha256'])
  m.qualified(policy,proof)
  for key,value in [('native_rollback_verified',False),('network',True),('roles',11),('mattermost_version','2026.9.2'),('source_revision','b'*40)]:
   with self.subTest(key=key),self.assertRaises(ValueError):m.qualified(policy,{**proof,key:value})
  with self.assertRaises(ValueError):m.qualified({**policy,'qualification_revision':None},proof)

 def config(self):
  return {'gateway':{'auth':{'token':'old'},'mode':'local'},'proxy':{'native':'old'},
   'models':{'providers':{'inference':{'baseUrl':'https://inference.local/v1','apiKey':'unused'},'retained':{'baseUrl':'local'}}},
   'agents':{'entries':{'main':{'tools':{'allow':['sessions_spawn']}}}},'tools':{'allow':['sessions_spawn']},
   'channels':{'mattermost':{'enabled':True,'accounts':{'family':{'enabled':False}}}},'cron':{'enabled':False},
   'hooks':{'enabled':False},'browser':{'enabled':False},'plugins':{'allow':['nemoclaw','homecompute-safety'],'entries':{'nemoclaw':{'enabled':True}},'installs':{'nemoclaw':{'source':'old'}}}}

 def test_native_ownership_rotation_cannot_expand_authority_or_model_routes(self):
  original=self.config();actual=m.TEAM.paused(original);actual['gateway']['auth']['token']='new';actual['proxy']['native']='new';actual['plugins']['installs']['nemoclaw']['source']='new';actual['meta']={'lastTouchedVersion':'2026.9.2'}
  active=m.ownership_merge(original,actual)
  self.assertEqual(active['channels'],original['channels']);self.assertEqual(active['gateway']['auth']['token'],'new')
  for modify in ('tool','plugin','provider','gateway'):
   changed=copy.deepcopy(actual)
   if modify=='tool':changed['tools']['allow'].append('exec')
   elif modify=='plugin':changed['plugins']['allow'].append('unreviewed')
   elif modify=='provider':changed['models']['providers']['inference']['baseUrl']='https://outside.example/v1'
   else:changed['gateway']['mode']='remote'
   with self.subTest(modify=modify),self.assertRaises(ValueError):m.ownership_merge(original,changed)

 def test_transaction_locks_span_the_entire_mutation(self):
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp);state=home/'.local/state/homecompute-openclaw-supervisor';state.mkdir(parents=True)
   raw=b'{}';argv=['tool','apply','--revision','a'*40,'--expected-hash','b'*64,'--qualification-proof',str(home/'proof.json'),'--expected-qualification-sha256',m.digest(raw),'--expected-config-sha256','c'*64,'--build-dir',str(home/'build')]
   def mutation(*args):
    for name in ('error-recovery.lock','managed-upgrade.lock'):
     fd=os.open(state/name,os.O_WRONLY)
     try:
      with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
     finally:os.close(fd)
    return {'verified':True}
   with patch.object(m,'HOME',home),patch.object(m,'declaration',return_value={}),patch.object(m,'source_guard'),patch.object(m.SUP,'private_file',return_value=raw),patch.object(m,'qualified'),patch.object(m,'apply',side_effect=mutation),patch.object(sys,'argv',argv),patch('builtins.print'):m.main()
   for name in ('error-recovery.lock','managed-upgrade.lock'):
    fd=os.open(state/name,os.O_WRONLY)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(fd)

 def test_public_cli_staging_is_idempotent_and_preserves_unknown_drift(self):
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp).resolve();build=home/'.local/share/homecompute/openclaw-cli-builds/fixture';build.mkdir(parents=True)
   source=home/'public';(source/'dist').mkdir(parents=True);(source/'bin').mkdir()
   (source/'dist/build-identity.json').write_text(json.dumps({'sourceRevision':m.ASSETS.VERSIONS['0.0.140'],'nemoclawVersion':'0.0.140'}));(source/'bin/nemoclaw.js').write_text('public fixture')
   archive=build/'nemoclaw-0.0.140.tar.gz'
   with tarfile.open(archive,'w:gz') as tar:tar.add(source,arcname='nemoclaw-0.0.140')
   with patch.object(m,'HOME',home),patch.dict(m.ASSETS.ARCHIVES,{'0.0.140':m.ASSETS.sha(archive)}):
    dest=m.stage_cli(build,'0.0.140');self.assertEqual(m.stage_cli(build,'0.0.140'),dest)
    (dest/'bin/nemoclaw.js').write_text('unexpected edit')
    with self.assertRaisesRegex(ValueError,'drift'):m.stage_cli(build,'0.0.140')
    self.assertEqual((dest/'bin/nemoclaw.js').read_text(),'unexpected edit')

if __name__=='__main__':unittest.main()
