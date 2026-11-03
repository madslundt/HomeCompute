#!/usr/bin/env python3
import importlib.util
import io
import hashlib
import tarfile
import json
import sqlite3
import struct
from contextlib import closing
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
 def test_local_fixture_dns_only_denies_the_reserved_native_probe_name(self):
  def query(name):
   labels=b''.join(bytes([len(label)])+label.encode() for label in name.split('.'))+b'\0'
   return struct.pack('!6H',123,0x0100,1,0,0,0)+labels+struct.pack('!2H',1,1)
  packet=query('nemoclaw-dns-probe-123abcdef.invalid');response=w.fixture_dns_response(packet)
  self.assertEqual(struct.unpack('!6H',response[:12]),(123,0x8183,1,0,0,0))
  self.assertEqual(response[12:],packet[12:])
  self.assertEqual(struct.unpack('!6H',w.fixture_dns_response(query('private.example'))[:12])[1]&15,2)
  self.assertIsNone(w.fixture_dns_response(b'bad'))
  self.assertIsNone(w.fixture_dns_response(struct.pack('!6H',1,0,1,0,0,0)+b'\xc0\x0c\0\1\0\1'))

 def test_actual_native_delivery_and_task_state_are_preserved(self):
  with closing(sqlite3.connect(':memory:')) as db:
   db.execute('create table delivery_queue_entries(queue_name text,id text,status text,entry_json text)')
   db.execute('create table task_delivery_state(task_id text,requester_origin_json text,last_notified_event_at integer)')
   db.execute('insert into delivery_queue_entries values(?,?,?,?)',('fixture','delivery-1','completed','{"text":"synthetic fixture"}'))
   db.execute('insert into task_delivery_state values(?,?,?)',('task-1','{"channel":"synthetic"}',123))
   original={t:w.durable_table_digest(db,t) for t in ('delivery_queue_entries','task_delivery_state')}
   db.execute('update task_delivery_state set last_notified_event_at=124')
   self.assertNotEqual(original['task_delivery_state'],w.durable_table_digest(db,'task_delivery_state'))
   db.execute('delete from delivery_queue_entries')
   self.assertNotEqual(original['delivery_queue_entries'],w.durable_table_digest(db,'delivery_queue_entries'))
   db.execute('create table cron_job_runtime_authorities(store_key text,job_id text,authority_json text)')
   db.execute('insert into cron_job_runtime_authorities values(?,?,?)',('fixture','job-1','old-native-authority'))
   before=w.durable_table_digest(db,'cron_job_runtime_authorities')
   db.execute('update cron_job_runtime_authorities set authority_json=?',('native-revalidated-authority',))
   self.assertEqual(before,w.durable_table_digest(db,'cron_job_runtime_authorities'))
   db.execute('delete from cron_job_runtime_authorities')
   self.assertNotEqual(before,w.durable_table_digest(db,'cron_job_runtime_authorities'))

 def test_no_acceptance_without_actual_handoff_and_rollback(self):
  data={k:True for k in ('native_upgrade_verified','native_rollback_verified','state_preserved','authenticated_gateway_verified','task_delivery_verified','agent_handoff_verified','native_yield_verified','oci_transport_name_verified','native_sqlite_integrity','installed_mattermost_ingress_verified','registry_metadata_replay')};data['roles']=12;data['native_sqlite_databases']=39
  self.assertTrue(m.accepted(data))
  for key in ('native_rollback_verified','agent_handoff_verified','state_preserved'):
   with self.subTest(key=key):self.assertFalse(m.accepted({**data,key:False}))

 def test_memory_admission_and_runtime_pressure_preserve_host_reserve(self):
  calm={'full':0.0,'some':0.0}
  self.assertTrue(m.memory_admitted(17*1024**3,calm))
  self.assertFalse(m.memory_admitted(17*1024**3-1,calm))
  self.assertTrue(m.memory_admitted(5*1024**3,calm,running=True))
  self.assertFalse(m.memory_admitted(4*1024**3-1,calm,running=True))
  self.assertFalse(m.memory_admitted(20*1024**3,{'full':1.1,'some':1.1},running=True))
  self.assertFalse(m.memory_admitted(20*1024**3,{'full':0.0,'some':1.1}))

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

 def test_paused_start_requires_same_identity_running_and_authenticated_health(self):
  identifier='aabbccdd-0000-4000-8000-000000000001'
  stopped=json.dumps({'name':'agent-openclaw','id':identifier,'phase':'Stopped'}).encode()
  ready=json.dumps({'name':'agent-openclaw','id':identifier,'phase':'Ready'}).encode()
  with patch.object(w,'run',side_effect=[stopped,b'optional finalization error',ready]),patch.object(w.subprocess,'check_output',return_value=b'running'),patch.object(w,'health') as health:
   w.start_native('0.0.129',{},'paused-start');health.assert_called_once_with('0.0.129',{})
  changed=json.dumps({'name':'agent-openclaw','id':'aabbccdd-0000-4000-8000-000000000002','phase':'Ready'}).encode()
  with patch.object(w,'run',side_effect=[stopped,b'error',changed]),patch.object(w,'health') as health:
   with self.assertRaisesRegex(ValueError,'exact-ready'):w.start_native('0.0.129',{},'paused-start')
   health.assert_not_called()

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

 def test_oci_transport_naming_keeps_every_blob_unchanged_and_rejects_corruption(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);config=b'{"os":"linux","architecture":"amd64"}';layer=b'synthetic layer'
   def descriptor(raw):return {'digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'size':len(raw)}
   manifest=json.dumps({'config':descriptor(config),'layers':[descriptor(layer)]}).encode();manifest+=b' '*(20193-len(manifest))
   desc=descriptor(manifest);reference='ghcr.io/nvidia/nemoclaw/openclaw-sandbox@'+desc['digest']
   entries={'oci-layout':b'{"imageLayoutVersion":"1.0.0"}','index.json':json.dumps({'schemaVersion':2,'manifests':[desc]}).encode(),'manifest.json':b'[]'}
   blobs={raw: 'blobs/sha256/'+hashlib.sha256(raw).hexdigest() for raw in (manifest,config,layer)}
   entries.update({name:raw for raw,name in blobs.items()})
   def archive(path,values):
    with tarfile.open(path,'w') as tar:
     for name,raw in values.items():
      info=tarfile.TarInfo(name);info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
   source=root/'original.tar';archive(source,entries)
   with patch.dict(m.ASSETS.IMAGES,{'0.0.140':reference}):
    proof=m.ASSETS.named_archive(source,root/'named.tar',reference)
    self.assertEqual(proof['verified_unchanged_blobs'],3)
    with tarfile.open(root/'named.tar') as tar:
     for raw,name in blobs.items():self.assertEqual(tar.extractfile(name).read(),raw)
     index=json.load(tar.extractfile('index.json'))
     self.assertEqual(index['manifests'][0]['digest'],desc['digest'])
     self.assertEqual(index['manifests'][0]['annotations']['io.containerd.image.name'],reference)
    entries[blobs[layer]]=b'changed';archive(root/'changed.tar',entries)
    with self.assertRaisesRegex(ValueError,'blob content mismatch'):m.ASSETS.named_archive(root/'changed.tar',root/'refused.tar',reference)
    self.assertFalse((root/'refused.tar').exists())

 def test_clone_environment_does_not_inherit_external_credentials(self):
  with patch.object(w,'ROOT',ROOT),patch.object(Path,'read_text',return_value='NEMOCLAW_CONTEXT_WINDOW=32768\nCOMPATIBLE_API_KEY=private\nOPENAI_API_KEY=private'),patch.dict('os.environ',{'COMPATIBLE_API_KEY':'private','OPENAI_API_KEY':'private'}):
   env=w.environment('0.0.140',Path('/readonly/assets'))
  self.assertEqual(env['COMPATIBLE_API_KEY'],'synthetic-fixture-only')
  self.assertNotIn('OPENAI_API_KEY',env)
  self.assertNotIn('E2E_MANAGED_IMAGE_REVISION',env)
  self.assertEqual(env['NEMOCLAW_E2E_MANAGED_IMAGE_REVISION'],m.ASSETS.VERSIONS['0.0.140'])


if __name__=='__main__':unittest.main()
