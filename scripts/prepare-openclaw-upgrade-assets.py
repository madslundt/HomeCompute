#!/usr/bin/env python3
"""Capture authenticated public inputs for a networkless managed-upgrade test."""
import argparse
from datetime import datetime,timezone
import hashlib
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
