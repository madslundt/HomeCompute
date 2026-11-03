#!/usr/bin/env python3
"""Capture authenticated public inputs for a networkless managed-upgrade test."""
import argparse
from datetime import datetime,timezone
import hashlib
import copy
import io
import re
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
VERSIONS={'0.0.129':'26922313bba96184e65c3663b351683ebae9504d','0.0.140':'88fe14f3fb9a0bf72997cce19ec88b5b2ee08391'}
ARCHIVES={'0.0.129':'add6fe5676da2f73a21fd17b37350c58ba6da417643ba715596ef7b92ac8c514','0.0.140':'55197aa4f462403363f8ec0567ef268aa742e1122bd82d0cecb8693fc0be1cf2'}
IMAGES={'0.0.129':'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a','0.0.140':'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:23b806f148ba3e2c57b923f659fe2e6caa1dded684fc3ae410c41c2e17a73890'}
CATALOG_JS="""const path=require('node:path');const [root,version,revision]=process.argv.slice(2);
const {getBuildIdentity}=require(path.join(root,'dist/lib/core/version.js'));
const identity=getBuildIdentity({rootDir:root});
if(identity.nemoclawVersion!==version||identity.sourceRevision!==revision)throw Error('build identity mismatch');
require(path.join(root,'dist/lib/onboard/managed-image/catalog.js')).resolveManagedImageCatalogFromGhcr({release:'v'+version,revision,platform:'linux/amd64'}).then(c=>process.stdout.write(JSON.stringify(c))).catch(e=>{console.error(e);process.exitCode=1});"""


def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def catalog_valid(data,version):
 if set(data)!={'openclaw','hermes','langchain-deepagents-code'}:raise ValueError('complete public cohort required')
 first=data['openclaw']
 for name,item in data.items():
  if (item['agent']!=name or item['platform']!='linux/amd64' or item['source']['revision']!=VERSIONS[version]
   or item['source']['release']!='v'+version or item['source']['cohort']!=first['source']['cohort']):raise ValueError('catalog cohort mismatch')
 if first['reference']!=IMAGES[version]:raise ValueError('reviewed managed workload mismatch')


def named_archive(source,destination,reference):
 """Name authenticated OCI content for Docker's containerd import lifecycle."""
 if reference!=IMAGES['0.0.140']:raise ValueError('only reviewed candidate transport may be named')
 digest=reference.split('@')[1]
 with tarfile.open(source) as original:
  members=original.getmembers();names=[m.name for m in members]
  if len(names)!=len(set(names)) or len(names)>4096:raise ValueError('duplicate or excessive OCI members')
  for member in members:
   if member.isdir() and member.name in ('blobs','blobs/sha256'):continue
   if not member.isfile() or not re.fullmatch(r'(?:index\.json|manifest\.json|oci-layout|blobs/sha256/[0-9a-f]{64})',member.name):raise ValueError('unsafe OCI archive member')
  raw=original.extractfile('index.json').read(65537)
  if len(raw)>65536:raise ValueError('OCI index bound')
  index=json.loads(raw)
  if len(index.get('manifests',[]))!=1:raise ValueError('one authenticated workload descriptor required')
  descriptor=index['manifests'][0]
  if descriptor.get('digest')!=digest or descriptor.get('size')!=20193:raise ValueError('authenticated manifest descriptor mismatch')
  blob_names={m.name for m in members if m.isfile() and m.name.startswith('blobs/')}
  expected=set();pending=[descriptor]
  while pending:
   row=pending.pop();name='blobs/'+row['digest'].replace(':','/',1)
   if name in expected:continue
   if name not in blob_names:raise ValueError('referenced OCI blob missing')
   expected.add(name)
   stream=original.extractfile(name)
   h=hashlib.sha256();size=0;content=bytearray() if name=='blobs/'+digest.replace(':','/',1) else None
   while chunk:=stream.read(1024*1024):
    size+=len(chunk);h.update(chunk)
    if content is not None:content.extend(chunk)
   if 'sha256:'+h.hexdigest()!=row['digest'] or size!=row['size']:raise ValueError('OCI blob content mismatch')
   if content is not None:
    manifest=json.loads(content)
    pending.extend([manifest['config'],*manifest['layers']])
  if expected!=blob_names:raise ValueError('unreferenced OCI blob refused')
  descriptor.setdefault('annotations',{})['io.containerd.image.name']=reference
  rendered=json.dumps(index,sort_keys=True,separators=(',',':')).encode()
  with destination.open('xb') as file,tarfile.open(fileobj=file,mode='w') as output:
   for member in members:
    info=copy.copy(member)
    if member.name=='index.json':info.size=len(rendered);output.addfile(info,io.BytesIO(rendered))
    else:
     stream=original.extractfile(member) if member.isfile() else None
     output.addfile(info,stream)
     if stream is not None:stream.close()
 return {'original_archive_sha256':sha(source),'named_archive_sha256':sha(destination),
  'original_index_sha256':hashlib.sha256(raw).hexdigest(),'named_index_sha256':hashlib.sha256(rendered).hexdigest(),
  'verified_unchanged_blobs':len(expected),'manifest_digest':digest,'reference':reference}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-dir',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
 if os.getuid()!=1000 or socket.gethostname()!='agents' or ROOT!=Path.home()/'.local/share/homecompute/releases'/a.revision:raise ValueError('published guest operator release required')
 subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',a.revision,'origin/master'],check=True,capture_output=True)
 name='scripts/prepare-openclaw-upgrade-assets.py'
 if subprocess.check_output(['git','-C',str(ROOT),'show',a.revision+':'+name])!=(ROOT/name).read_bytes():raise ValueError('asset source mismatch')
 if a.build_dir.resolve().parent!=Path.home()/'.local/share/homecompute/openclaw-cli-builds':raise ValueError('reviewed build directory required')
 for v,h in ARCHIVES.items():
  if sha(a.build_dir/('nemoclaw-'+v+'.tar.gz'))!=h:raise ValueError('CLI archive changed')
 if shutil.disk_usage(a.build_dir).free<3*1024**3:raise ValueError('asset headroom unavailable')
 os.umask(0o077);out=a.build_dir/('upgrade-assets-'+uuid.uuid4().hex);out.mkdir(mode=0o700)
 node=Path.home()/'.nvm/versions/node/v22.23.3/bin/node'
 env={'HOME':str(Path.home()),'PATH':str(node.parent)+':/usr/bin:/bin','CI':'1','NO_COLOR':'1'}
 for v,revision in VERSIONS.items():
  with tempfile.TemporaryDirectory(prefix='catalog-artifact-',dir=a.build_dir) as extracted:
   with tarfile.open(a.build_dir/('nemoclaw-'+v+'.tar.gz')) as tar:tar.extractall(extracted,filter='data')
   data=subprocess.check_output([str(node),'-',str(Path(extracted)/('nemoclaw-'+v)),v,revision],input=CATALOG_JS.encode(),env=env,timeout=300)
  if not 2<=len(data)<=65536:raise ValueError('catalog bound exceeded')
  catalog_valid(json.loads(data),v);(out/('catalog-'+v+'.json')).write_bytes(data)
  shutil.copy2(a.build_dir/('nemoclaw-'+v+'.tar.gz'),out)
 subprocess.run(['ionice','-c','3','nice','-n','19','docker','image','save','--output',str(out/'candidate-image.tar'),IMAGES['0.0.140']],check=True,env=env,timeout=600)
 manifest={'source_revision':a.revision,'captured_at':datetime.now(timezone.utc).isoformat(),'registry_metadata_replay':True,
  'versions':VERSIONS,'images':IMAGES,'files':{f.name:{'sha256':sha(f),'bytes':f.stat().st_size} for f in out.iterdir()}}
 (out/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
 print(json.dumps({'assets':str(out),'manifest_sha256':sha(out/'manifest.json'),'files':len(manifest['files'])}))


if __name__=='__main__':main()
