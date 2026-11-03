#!/usr/bin/env python3
"""Build pinned public NemoClaw CLI artifacts without installing global shims."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {'0.0.129':'26922313bba96184e65c3663b351683ebae9504d',
            '0.0.140':'88fe14f3fb9a0bf72997cce19ec88b5b2ee08391'}
NODE = Path('/home/hermes-operator/.nvm/versions/node/v22.23.3/bin')
FILES = ('scripts/build-openclaw-managed-cli.py',)


def bundle():
    result=hashlib.sha256()
    for name in FILES:result.update(name.encode()+b'\0'+hashlib.sha256((ROOT/name).read_bytes()).digest())
    return result.hexdigest()


def commands():
    return [['node','scripts/check-node-version.js'],['node','scripts/lib/openshell-sdk-install.mts','prepare'],
        ['npm','ci','--ignore-scripts','--include=dev','--include=optional','--prefer-offline','--@nvidia:registry=https://npm.pkg.github.com'],
        ['node','scripts/lib/openshell-sdk-install.mts','check'],
        ['npm','--prefix','nemoclaw','ci','--ignore-scripts','--include=dev'],
        ['npm','run','build:cli'],['npm','--prefix','nemoclaw','run','build']]


def source_guard(revision, expected):
    if (not re.fullmatch('[0-9a-f]{40}',revision or '') or os.getuid()!=1000 or socket.gethostname()!='agents'
            or ROOT != Path.home()/'.local/share/homecompute/releases'/revision or bundle()!=expected):
        raise ValueError('exact published operator release/hash required')
    subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',revision,'origin/master'],check=True,capture_output=True)
    for name in FILES:
        if (ROOT/name).is_symlink() or subprocess.check_output(['git','-C',str(ROOT),'show',revision+':'+name])!=(ROOT/name).read_bytes():
            raise ValueError('published build source mismatch')


def archive(source, destination, prefix):
    for path in source.rglob('*'):
        if '.git' in path.relative_to(source).parts:continue
        if path.is_symlink() and not path.resolve().is_relative_to(source):
            raise ValueError('public CLI bundle symlink escapes source')
    def metadata(info):
        if '.git' in Path(info.name).parts:return None
        info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
        return info
    with destination.open('xb') as out,gzip.GzipFile(filename='',fileobj=out,mode='wb',mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w|') as tar:
        tar.add(source,arcname=prefix,filter=metadata)
    with destination.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare(revision):
    available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    if available<4*1024**3 or shutil.disk_usage(Path.home()).free<8*1024**3:
        raise ValueError('public build headroom unavailable')
    if pwd.getpwuid(os.getuid()).pw_dir!=str(Path.home()) or subprocess.check_output([str(NODE/'node'),'--version'],text=True).strip()!='v22.23.3':
        raise ValueError('reviewed operator home/Node version required')
    work=Path.home()/'.local/share/homecompute/openclaw-cli-builds'/uuid.uuid4().hex
    work.mkdir(parents=True,mode=0o700);work.chmod(0o700)
    print(json.dumps({'workspace':str(work),'runtime_installed':False}),flush=True)
    env={'HOME':str(Path.home()),'USER':'hermes-operator','LOGNAME':'hermes-operator',
        'PATH':str(NODE)+':/usr/bin:/bin','CI':'1','NO_COLOR':'1',
        'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1',
        'NPM_CONFIG_USERCONFIG':'/dev/null','NPM_CONFIG_CACHE':str(work/'npm-cache'),
        'NODE_OPTIONS':'--max-old-space-size=1536'}
    # Build scripts are reviewed public code. Never run package prepare/link,
    # invoke a CLI runtime, change an alias, or inherit provider credentials.
    receipt={'source_revision':revision,'created_at':datetime.now(timezone.utc).isoformat(),'artifacts':{}}
    for version,commit in VERSIONS.items():
        source=work/('source-'+version)
        with (work/(version+'.log')).open('xb') as log:
            subprocess.run(['git','-c','core.hooksPath=/dev/null','clone','--quiet','--depth','1','--branch','v'+version,
                            'https://github.com/NVIDIA/NemoClaw.git',str(source)],env=env,stdout=log,stderr=log,check=True,timeout=300)
            if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],env=env,text=True).strip()!=commit:
                raise ValueError('vendor tag/commit mismatch')
            (source/'.version').write_text(version+'\n');(source/'.source-revision').write_text(commit+'\n')
            for command in commands():
                subprocess.run(['taskset','-c',str(min(os.sched_getaffinity(0))),*command],cwd=source,env=env,
                               stdin=subprocess.DEVNULL,stdout=log,stderr=log,check=True,timeout=900)
                if log.tell()>16*1024**2:raise ValueError('public build log exceeds bound')
        identity=json.loads((source/'dist/build-identity.json').read_bytes())
        if commit not in json.dumps(identity) or version not in json.dumps(identity):
            raise ValueError('built CLI identity mismatch')
        target=work/('nemoclaw-'+version+'.tar.gz')
        receipt['artifacts'][version]={'path':str(target),'sha256':archive(source,target,'nemoclaw-'+version),
            'commit':commit,'build_identity':identity,'gateway_runtime_sha256':hashlib.sha256((source/'dist/lib/gateway-runtime-action.js').read_bytes()).hexdigest()}
        (work/'receipt.json').write_text(json.dumps(receipt,sort_keys=True));(work/'receipt.json').chmod(0o600)
        print(json.dumps({'built':version,'receipt':str(work/'receipt.json')}),flush=True)
    print(json.dumps(receipt,sort_keys=True))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('plan','prepare'),default='plan',nargs='?')
    parser.add_argument('--revision');parser.add_argument('--expected-hash');args=parser.parse_args()
    if args.action=='plan':print(json.dumps({'bundle_hash':bundle(),'versions':VERSIONS,'commands':commands(),'runtime_installed':False}));return
    if not args.revision or not args.expected_hash:parser.error('prepare requires published revision/hash')
    os.umask(0o077);source_guard(args.revision,args.expected_hash);prepare(args.revision)


if __name__=='__main__':main()
