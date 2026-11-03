#!/usr/bin/env python3
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=load('qualification','scripts/qualify-openclaw-restored-upgrade.py')
w=load('worker','deploy/openclaw/qualification/restored-upgrade.py')


class RestoredUpgrade(unittest.TestCase):
 def test_no_acceptance_without_actual_handoff_and_rollback(self):
  data={k:True for k in ('native_upgrade_verified','native_rollback_verified','state_preserved','authenticated_gateway_verified','task_delivery_verified','agent_handoff_verified','installed_mattermost_ingress_verified','registry_metadata_replay')};data['roles']=12
  self.assertTrue(m.accepted(data))
  for key in ('native_rollback_verified','agent_handoff_verified','state_preserved'):
   with self.subTest(key=key):self.assertFalse(m.accepted({**data,key:False}))

 def test_serial_result_must_have_the_selected_nonce_once(self):
  self.assertIsNone(m.result_from_log(b'HC_OPENCLAW_UPGRADE_other {}\n','selected'))
  raw=b'HC_OPENCLAW_UPGRADE_selected {"native_upgrade_verified":true}\n'
  self.assertEqual(m.result_from_log(raw,'selected'),{'native_upgrade_verified':True})
  self.assertIsNone(m.result_from_log(raw+raw,'selected'))

 def test_seed_disables_network_and_requires_ephemeral_permit(self):
  data=json.loads(m.seed('selected','policyhash','assetshash').split('\n',1)[1])
  self.assertEqual(data['network'],{'config':'disabled'})
  self.assertFalse(data['package_update'])
  self.assertEqual(data['write_files'][0]['path'],'/run/hc-openclaw-qualification-permit')
  self.assertNotIn('mounts',data)

 def test_catalog_rejects_mixed_cohort(self):
  source={'revision':m.ASSETS.VERSIONS['0.0.140'],'release':'v0.0.140','cohort':'same'}
  data={name:{'agent':name,'platform':'linux/amd64','source':dict(source),'reference':m.ASSETS.IMAGES['0.0.140']} for name in ('openclaw','hermes','langchain-deepagents-code')}
  m.ASSETS.catalog_valid(data,'0.0.140')
  data['hermes']['source']['cohort']='different'
  with self.assertRaisesRegex(ValueError,'cohort mismatch'):m.ASSETS.catalog_valid(data,'0.0.140')

 def test_stop_accepts_only_independent_exact_stopped_and_exited_evidence(self):
  identifier='aabbccdd-0000-4000-8000-000000000001'
  ready=json.dumps({'name':'agent-openclaw','id':identifier,'phase':'Ready'}).encode()
  stopped=json.dumps({'name':'agent-openclaw','id':identifier,'phase':'Stopped'}).encode()
  for status,accepted in ((b'exited',True),(b'running',False)):
   with self.subTest(status=status),patch.object(w,'run',side_effect=[ready,b'native command error',stopped]),patch.object(w.subprocess,'check_output',return_value=status):
    if accepted:w.stop_native('0.0.129',{},'synthetic-stop')
    else:
     with self.assertRaisesRegex(ValueError,'not-quiescent'):w.stop_native('0.0.129',{},'synthetic-stop')
  changed=json.dumps({'name':'agent-openclaw','id':'aabbccdd-0000-4000-8000-000000000002','phase':'Stopped'}).encode()
  with patch.object(w,'run',side_effect=[ready,b'error',changed]),patch.object(w.subprocess,'check_output') as inspect:
   with self.assertRaisesRegex(ValueError,'identity-changed'):w.stop_native('0.0.129',{},'synthetic-stop')
   inspect.assert_not_called()

 def test_handoff_fixture_requires_received_child_evidence(self):
  def request(message,tools=()):
   data={'messages':[{'role':'user','content':message}],'tools':[{'function':{'name':name}} for name in tools]}
   raw=json.dumps(data).encode();handler=object.__new__(w.Provider);handler.headers={'Content-Length':str(len(raw))};handler.rfile=io.BytesIO(raw)
   with patch.object(handler,'reply') as reply,patch.object(w.time,'sleep'):handler.do_POST()
   return reply.call_args.args[0]['choices'][0]
  with patch.multiple(w.Provider,child_called=False,parent_received=False,spawn_requested=False,yield_requested=False):
   tools=('sessions_spawn','sessions_yield')
   first=request('HC_NATIVE_HANDOFF_CHECK',tools)
   self.assertEqual(first['message']['tool_calls'][0]['function']['name'],'sessions_spawn')
   self.assertFalse(w.Provider.parent_received)
   second=request('HC_NATIVE_HANDOFF_CHECK',tools)
   self.assertEqual(second['message']['tool_calls'][0]['function']['name'],'sessions_yield')
   child=request('HC_NATIVE_CHILD_TASK')
   self.assertGreater(child['message']['content'].index(w.Provider.child_tail),512)
   self.assertFalse(w.Provider.parent_received)
   request('HC_NATIVE_HANDOFF_CHECK '+child['message']['content'],tools)
   self.assertTrue(w.Provider.parent_received)

 def test_clone_environment_does_not_inherit_external_credentials(self):
  with patch.object(w,'ROOT',ROOT),patch.dict('os.environ',{'COMPATIBLE_API_KEY':'private','OPENAI_API_KEY':'private'}):
   env=w.environment('0.0.140',Path('/readonly/assets'))
  self.assertEqual(env['COMPATIBLE_API_KEY'],'synthetic-fixture-only')
  self.assertNotIn('OPENAI_API_KEY',env)
  self.assertNotIn('E2E_MANAGED_IMAGE_REVISION',env)
  self.assertEqual(env['NEMOCLAW_E2E_MANAGED_IMAGE_REVISION'],m.ASSETS.VERSIONS['0.0.140'])


if __name__=='__main__':unittest.main()
