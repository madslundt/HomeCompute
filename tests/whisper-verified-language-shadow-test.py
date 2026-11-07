#!/usr/bin/env python3
"""Synthetic guard/artifact tests. No model, Docker, network or private payloads."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'scripts/whisper-verified-language-shadow.py'
spec=importlib.util.spec_from_file_location('combined_guard',SOURCE)
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
UNCHANGED={'Refused': '059772d163dfb2db39e9b7c0cae2806ab71c5682b35f0a2e68518aaf044c7f48', 'MonitorUnavailable': '1f405c24750e869d89bf5155c733a0ea19f9f0505b5ddface8ebaa8315b05cba', 'MemoryFloor': '8f1951238e848cfdb80ef7e0dc48a51a600de33469387fcddc33c57ead88098a', 'command': '195cb66c71d0d6ad6d91ec6781679706e49dfb42be6ffdb852b9de07df332eaf', 'private_directory': 'acbe173ff8b6f31ba9999ccdd873858849c7e4587e81cf2cf4a6f710e43181b5', 'write_json': '5c253564b1a20b04604786a08e781cf9615197d50ccd0721e83b6ea0d27d4209', 'read_json': '8cf3d8b1db8b7bb34d41af1912dcdca369c0abd6cfdaa5928bba809120963410', 'lifecycle_lock': '0c57abfdaacd608432ff432b0f62499922f115232c5cb4d94a39ea38c2c7a8b0', 'same_trial': '743c00fb32ddb4b03b9aa2355e1a4c0c003091a5d43eb4cc2b4e6a8b3c8c21fe', 'remove_active': '10f15f5a49cbffd2523cc66a924209a04ebbd5aeda02428c3f390df35aadaa7c', 'memory': '5791fe607b4fc1b05b6101ed8be2c2a0a9a05348e3cbbdba42ea58a76f40c9e9', 'latch_resource_failure': '2efd80e7cc569bf976fff78c2ee04297cee8bda751820d13f3819884ff9c6cfb', 'include_latched_failure': '9d8d62c6dc7abc7bc840cdb52d4d9426d5a739f0f7c165076e8709850d8856fc', 'emergency_reason': '97a2d84f549a201c2cea723197b8802279e5744491198ffe05ceccd9d275e1de', 'terminate_owned': '277777ab7064c871367b66ffb69d5d93eae5581c86022d5c81c0c558d6682ce0', 'emergency_abort': 'b358997c00f6b14a136895b4a455f94752c6b0288a8480a8c5177d8c698d27a0', 'monitor_sample': 'f6fed4490b2d70df542c68c87736e4e51ccfe60e0fa4d06e432e0f1524ac2a83', 'guard': '8e720c18af16611d90d3ad96ed6b9ed8b9ce214778e0931dd648c65aa28f3853', 'request_mark': 'b240d7fef86855656936ed82f66a919db90848168a290f751eb6e746f45330d7', 'verify_image': '65806c8d280755546174cc7c6405eed7f8ff47d3a2d4315a481fcfb3ebf8e305', 'busy': '65a865b1c6c04ba8a58c1cae796beaf4c8bfc183739d617da46d17657b00aa1a', 'trace': '18243351631110b960d840a7170bb4ed696bff01c6dd4d2c8fb8649b423a812d', 'resource': 'a4ff38f6cad15c2df098e1f76e493bf3053c997482cefa8121108e672ca0726b', 'alive_guard': '1f40040579e9968b337c03e9903550070ced00cf26df3e187c762bb47f8d6510', 'wait_ready': '9eb781aa125719ba29081ffa0aed8215645f17983257e6037edb076207c8e2c8', 'main': '4e1d867280ba6bd388336e9c83f36b099af763369f67651c87ae40a6713624b8'}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def item(raw):return {'bytes':len(raw),'sha256':sha(raw)}
def dump(value):return (json.dumps(value,sort_keys=True)+'\n').encode()
def observer():return {'directory':'/srv/gb10-ai/cache/language-observer/export',
    'files':{n:{'bytes':1,'sha256':'a'*64} for n in M.OBSERVER_FILES},
    'bundle_pin':{'bytes':1,'sha256':'b'*64},'confirmation_prescreen_sha256':'c'*64}
def record():return {'owner':'a'*24,'name':'hc-whisper-nonspeech-hc17-'+'a'*24,
    'container_id':'d'*64,'image_id':'sha256:'+'e'*64,'observer':observer(),
    'source_revision':'f'*40,'patcher_sha256':'1'*64,'phase':'awaiting_guard'}
def container(r):
 return {'Id':r['container_id'],'Name':'/'+r['name'],'Image':r['image_id'],
    'Config':{'User':'10001:10001','Labels':{M.OWNER_LABEL:r['owner']},
              'Env':['WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=true','WHISPER_CPP_REPAIR_BOUNDARY_OVERLAP=true']},
    'State':{'Running':True},'HostConfig':{'NetworkMode':'none','Memory':M.LIMIT,'MemorySwap':M.LIMIT,
      'NanoCpus':4000000000,'ReadonlyRootfs':True,'Privileged':False,'PortBindings':{},'PidsLimit':128,
      'LogConfig':{'Type':'none'},'RestartPolicy':{'Name':'no'},'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},
    'Mounts':[{'Type':'bind','Source':M.MODEL_PATH,'Destination':'/models/ggml-large-v3.bin','RW':False},
              {'Type':'bind','Source':r['observer']['directory'],'Destination':'/models/language-observer','RW':False}]}

class Tests(unittest.TestCase):
 def test_every_unchanged_safety_function_matches_reviewed_cold_helper(self):
  text=SOURCE.read_text();nodes={n.name:n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
  self.assertEqual({n:sha(ast.get_source_segment(text,nodes[n]).encode()) for n in UNCHANGED},UNCHANGED)
 def test_unsealed_artifact_pins_refused(self):
  for value in ({},{'observer':None},{'observer':{'directory':None}}):
   with self.assertRaises(M.Refused):M.observer_pins(value)
 def test_exact_file_set_types_and_paths(self):
  good={'observer':observer()};self.assertEqual(M.observer_pins(good),good['observer'])
  for key,value in [('directory','/'),('directory','/tmp/../graph'),('directory','/tmp/graph,readonly'),('confirmation_prescreen_sha256',None)]:
   bad=copy.deepcopy(good);bad['observer'][key]=value
   with self.assertRaises((M.Refused,TypeError)):M.observer_pins(bad)
  for name in M.OBSERVER_FILES:
   for field,value in [('bytes',True),('bytes',M.OBSERVER_FILES[name]+1),('sha256','x'*64)]:
    bad=copy.deepcopy(good);bad['observer']['files'][name][field]=value
    with self.assertRaises(M.Refused):M.observer_pins(bad)
  bad=copy.deepcopy(good);bad['observer']['files']['code.py']=item(b'source')
  with self.assertRaises(M.Refused):M.observer_pins(bad)
 def test_effective_observer_mount_and_flag_checked(self):
  r=record();actual=container(r)
  with patch.object(M,'command',return_value=json.dumps([actual])):self.assertEqual(M.owned_container(r)['Id'],r['container_id'])
  variants=[]
  for field,value in [('RW',True),('Destination','/opt/homecompute'),('Source','/other')]:
   bad=copy.deepcopy(actual);bad['Mounts'][1][field]=value;variants.append(bad)
  bad=copy.deepcopy(actual);bad['Mounts'].append(dict(bad['Mounts'][1]));variants.append(bad)
  bad=copy.deepcopy(actual);bad['Config']['Env'].remove('WHISPER_CPP_REPAIR_BOUNDARY_OVERLAP=true');variants.append(bad)
  for env in ([],['WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=false'],['WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=true']*2):
   bad=copy.deepcopy(actual);bad['Config']['Env']=env;variants.append(bad)
  for bad in variants:
   with patch.object(M,'command',return_value=json.dumps([bad])):
    with self.assertRaises(M.Refused):M.owned_container(r)
 def test_release_requires_artifacts_after_live_guard_before_flag(self):
  r=record();events=[]
  with patch.object(M,'alive_guard',side_effect=lambda r:events.append('guard') or {'qualification_failed':False}),patch.object(M,'verify_observer_artifacts',side_effect=lambda r:events.append('artifacts') or {'verified':True}),patch.object(M,'emergency_reason',return_value=None),patch.object(M,'memory',side_effect=lambda **kw:events.append('memory')),patch.object(M,'command',side_effect=lambda args:events.append('flag')),patch.object(M,'write_json'):
   M.release_load(r)
  self.assertEqual(events,['guard','artifacts','guard','memory','flag'])
 def test_artifact_failure_or_guard_failure_never_release_load(self):
  for fail_guard in (False,True):
   with patch.object(M,'alive_guard',side_effect=[{'qualification_failed':False},{'qualification_failed':True}]),patch.object(M,'verify_observer_artifacts',side_effect=None if fail_guard else M.Refused,return_value={'verified':True}),patch.object(M,'command') as command,patch.object(M,'memory'):
    with self.assertRaises(M.Refused):M.release_load(record())
    command.assert_not_called()
 def test_post_probe_expired_deadline_does_not_release_load(self):
  now=[100.0];r={**record(),'boot_id':'synthetic-boot','loading_deadline_monotonic':105.0,'trial_deadline_monotonic':3600.0}
  def artifact_check(_):
   now[0]=106.0
   return {'verified':True}
  with patch.object(M,'alive_guard',return_value={'qualification_failed':False}),patch.object(M,'verify_observer_artifacts',side_effect=artifact_check),patch.object(M,'memory',return_value={}),patch.object(M.time,'monotonic',side_effect=lambda:now[0]),patch.object(M.Path,'read_text',return_value='synthetic-boot'),patch.object(M,'command') as command:
   with self.assertRaisesRegex(M.Refused,'crossed'):M.release_load(r)
   command.assert_not_called()
 def test_changed_companion_refused_before_docker_probe(self):
  with patch.object(M,'owned_container'),patch.object(M,'OBSERVER_PROBE_SHA256','0'*64),patch.object(M,'command') as command:
   with self.assertRaises(M.Refused):M.verify_observer_artifacts(record())
   command.assert_not_called()
 def test_start_only_adds_readonly_mount_and_observer_flag(self):
  r=record();value={k:v for k,v in r.items() if k not in ('owner','name','container_id','phase')};calls=[]
  with tempfile.TemporaryDirectory() as d,patch.object(M,'STATE',Path(d)),patch.object(M,'pins',return_value=value),patch.object(M,'verify_image'),patch.object(M,'memory',return_value={}),patch.object(M,'owned_container'),patch.object(M,'write_json'),patch.object(M.Path,'read_text',return_value='synthetic-boot'),patch.object(M,'command',side_effect=lambda args:calls.append(args) or 'd'*64):
   got=M.start()
  args=calls[0];self.assertEqual(args[:2],['docker','run'])
  for flag,val in [('--memory',str(6*1024**3)),('--memory-swap',str(6*1024**3)),('--cpus','4'),('--gpus','device=0'),('--network','none')]:self.assertEqual(args[args.index(flag)+1],val)
  self.assertEqual([args[i+1] for i,v in enumerate(args) if v=='--mount'],['type=bind,src='+M.MODEL_PATH+',dst=/models/ggml-large-v3.bin,readonly','type=bind,src='+value['observer']['directory']+',dst=/models/language-observer,readonly'])
  self.assertIn('WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=true',args);self.assertIn('WHISPER_CPP_NATIVE_TIMEOUT_SECONDS=600',args)
  self.assertEqual(args[args.index('--tmpfs')+1],'/tmp:rw,noexec,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700')
  self.assertEqual(got['phase'],'awaiting_guard');self.assertIn('/tmp/load-enabled',args[-1])
 def fixtures(self,d):
  root=Path(d)/'graph';root.mkdir();baked=Path(d)/'baked';baked.mkdir();code=baked/'language-observer';code.mkdir()
  model={'repository':'speechbrain/lang-id-voxlingua107-ecapa','revision':'0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9','runtime_torch':'synthetic','runtime_torch_module':'synthetic-raw'}
  sources={n:(dump(model) if n=='model.json' else b'synthetic-public-source') for n in M.OBSERVER_CODE}
  for n,raw in sources.items():(code/n).write_bytes(raw)
  samples=[{'frames':48000+i,'kind':'synthetic'} for i in range(10)];graph=b'not-deserialized-synthetic-graph'
  manifest={'source_revision':'a'*40,'code_sha256':{n:sha(v) for n,v in sources.items()},'model':model,'private_audio_used':False,'speaker_or_asr_output':False,'exporter_parity_passed':True,'duration_discrimination_passed':True,'graph_sha256':sha(graph),'graph_bytes':len(graph),'trace_warnings':[],'samples':samples}
  mr=dump(manifest);proof={'passed':True,'scope':'public-synthetic-runtime-parity-only','classification_authorized':False,'manifest_sha256':sha(mr),'graph_sha256':sha(graph),'source_revision':'a'*40,'runtime_torch':'synthetic','runtime_torch_module':'synthetic-raw','cuda_initialized':False,'torch_threads':1,'samples':samples};pr=dump(proof)
  pin={'source_revision':'a'*40,'code_sha256':manifest['code_sha256'],'manifest_sha256':sha(mr),'parity_sha256':sha(pr),'approved_warning_sha256':sha(json.dumps([],sort_keys=True).encode())};pinraw=dump(pin)
  files={'observer.ts':graph,'manifest.json':mr,'parity.json':pr}
  for n,raw in files.items():(root/n).write_bytes(raw)
  (baked/'language-observer-pin.json').write_bytes(pinraw)
  expected={'files':{n:item(raw) for n,raw in files.items()},'bundle_pin':item(pinraw)}
  program=M.observer_probe_source().replace("'/models/language-observer'",repr(str(root))).replace("'/opt/homecompute/language-observer'",repr(str(code))).replace("'/opt/homecompute'",repr(str(baked)))
  return root,baked,expected,program
 def probe(self,expected,program):
  return subprocess.run([sys.executable,'-I','-B','-c',program,json.dumps(expected)],capture_output=True,text=True,timeout=5,check=False)
 def test_actual_probe_hashes_without_model_import_or_deserialization(self):
  with tempfile.TemporaryDirectory() as d:
   _root,_baked,p,program=self.fixtures(d);r=self.probe(p,program)
   self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout);self.assertFalse(v['model_deserialized']);self.assertTrue(v['public_synthetic_artifacts_only'])
 def test_probe_refuses_extra_file_wrong_hash_and_symlink(self):
  for variant in ('extra','hash','symlink','directory','hardlink'):
   with tempfile.TemporaryDirectory() as d:
    root,_b,p,program=self.fixtures(d)
    if variant=='extra':(root/'private.wav').write_bytes(b'not-audio')
    elif variant=='hash':p['files']['observer.ts']['sha256']='f'*64
    elif variant=='symlink':(root/'observer.ts').rename(root.parent/'outside');(root/'observer.ts').symlink_to(root.parent/'outside')
    elif variant=='directory':(root/'observer.ts').unlink();(root/'observer.ts').mkdir()
    else:os.link(root/'observer.ts',root.parent/'linked')
    self.assertNotEqual(self.probe(p,program).returncode,0)
 def test_probe_rejects_runtime_module_distribution_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   root,baked,p,program=self.fixtures(d)
   proof=json.loads((root/'parity.json').read_bytes());proof['runtime_torch_module']='synthetic';raw=dump(proof);(root/'parity.json').write_bytes(raw);p['files']['parity.json']=item(raw)
   pin=json.loads((baked/'language-observer-pin.json').read_bytes());pin['parity_sha256']=sha(raw);raw=dump(pin);(baked/'language-observer-pin.json').write_bytes(raw);p['bundle_pin']=item(raw)
   self.assertNotEqual(self.probe(p,program).returncode,0)
 def test_probe_rejects_private_artifacts_even_when_hashes_are_updated(self):
  with tempfile.TemporaryDirectory() as d:
   root,baked,p,program=self.fixtures(d)
   manifest=json.loads((root/'manifest.json').read_bytes());manifest['private_audio_used']=True;raw=dump(manifest);(root/'manifest.json').write_bytes(raw);p['files']['manifest.json']=item(raw)
   pin=json.loads((baked/'language-observer-pin.json').read_bytes());pin['manifest_sha256']=sha(raw);raw=dump(pin);(baked/'language-observer-pin.json').write_bytes(raw);p['bundle_pin']=item(raw)
   self.assertNotEqual(self.probe(p,program).returncode,0)

if __name__=='__main__':unittest.main()
