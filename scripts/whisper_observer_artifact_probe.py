#!/usr/bin/env python3
"""Bounded public observer artifact hashes; no runtime imports or models."""
import hashlib,json,os,stat,sys
expected=json.loads(sys.argv[1]);root='/models/language-observer';code='/opt/homecompute/language-observer'
def require(value):
 if not value:raise ValueError('Observer artifact identity refused')
def unique(items):
 result={}
 for key,value in items:
  require(key not in result);result[key]=value
 return result
def decode(raw):return json.loads(raw,object_pairs_hook=unique)
def identity(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
def read(parent,name,item,retain=False):
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
 try:
  before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size==item['bytes'])
  digest=hashlib.sha256();raw=bytearray();count=0
  while True:
   block=os.read(fd,65536)
   if not block:break
   count+=len(block);require(count<=item['bytes']);digest.update(block)
   if retain:raw.extend(block)
  require(count==item['bytes'] and digest.hexdigest()==item['sha256'])
  require(identity(before)==identity(os.fstat(fd))==identity(os.stat(name,dir_fd=parent,follow_symlinks=False)))
  return bytes(raw)
 finally:os.close(fd)
directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
 before=os.fstat(directory);require(set(os.listdir(directory))==set(expected['files']))
 values={name:read(directory,name,item,name!='observer.ts') for name,item in expected['files'].items()}
 require(identity(before)==identity(os.fstat(directory)))
finally:os.close(directory)
baked=os.open('/opt/homecompute',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:pin=decode(read(baked,'language-observer-pin.json',expected['bundle_pin'],True))
finally:os.close(baked)
require(set(pin)=={'source_revision','code_sha256','manifest_sha256','parity_sha256','approved_warning_sha256'})
require(pin['manifest_sha256']==expected['files']['manifest.json']['sha256'] and pin['parity_sha256']==expected['files']['parity.json']['sha256'])
require(set(pin['code_sha256'])=={'common.py','graph.py','export.py','classify.py','installer_check.py','model.json','dependencies.json','requirements.lock'})
source=os.open(code,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
 model=None
 for name,digest in pin['code_sha256'].items():
  info=os.stat(name,dir_fd=source,follow_symlinks=False);require(0<info.st_size<=2000000)
  raw=read(source,name,{'bytes':info.st_size,'sha256':digest},name=='model.json')
  if name=='model.json':model=decode(raw)
finally:os.close(source)
manifest=decode(values['manifest.json']);proof=decode(values['parity.json'])
require(manifest['source_revision']==pin['source_revision'] and manifest['code_sha256']==pin['code_sha256'])
require(manifest['model']==model and model['repository']=='speechbrain/lang-id-voxlingua107-ecapa' and model['revision']=='0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9')
require(manifest['private_audio_used'] is False and manifest['speaker_or_asr_output'] is False and manifest['exporter_parity_passed'] is True and manifest['duration_discrimination_passed'] is True)
require(manifest['graph_sha256']==expected['files']['observer.ts']['sha256'] and manifest['graph_bytes']==expected['files']['observer.ts']['bytes'])
require(proof['passed'] is True and proof['scope']=='public-synthetic-runtime-parity-only' and proof['classification_authorized'] is False)
require(proof['manifest_sha256']==pin['manifest_sha256'] and proof['graph_sha256']==manifest['graph_sha256'] and proof['source_revision']==pin['source_revision'])
require(proof['runtime_torch']==model['runtime_torch'] and proof['runtime_torch_module']==model['runtime_torch_module'] and proof['cuda_initialized'] is False and proof['torch_threads']==1)
require(len(proof['samples'])==len(manifest['samples'])==10 and [(x['frames'],x['kind']) for x in proof['samples']]==[(x['frames'],x['kind']) for x in manifest['samples']])
require(hashlib.sha256(json.dumps(manifest['trace_warnings'],sort_keys=True).encode()).hexdigest()==pin['approved_warning_sha256'])
print(json.dumps({'verified':True,'file_sha256':{n:v['sha256'] for n,v in expected['files'].items()},'bundle_pin_sha256':expected['bundle_pin']['sha256'],'model_deserialized':False,'public_synthetic_artifacts_only':True}))
