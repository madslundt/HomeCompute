#!/usr/bin/env python3
"""Preserve retired operator artifacts outside native home before native backup.

Never make credential-bearing before-images readable to the agent. Default
plans; apply requires an exact published root-protected guest release.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
ARCHIVES=Path('/var/lib/homecompute-openclaw-operator-archives')
CONTAINER_CODE=r'''
import hashlib,json,os,pathlib,re,shutil,stat,sys,tempfile
BASE=pathlib.Path('/sandbox/.openclaw')
ROOTS=('tool-install-backups','child-result-backups','yield-acknowledgment-backups',
 'llm-queue-backups','search-fallback-backups','lyra-browser-backups','mattermost-setup/__pycache__')
def selected():
 out=[BASE/name for name in ROOTS if (BASE/name).exists() or (BASE/name).is_symlink()]
 office=BASE/'homecompute-agent-office'
 out.extend(p for p in office.glob('config-before-*.json') if re.fullmatch(r'config-before-[a-f0-9]{64}\.json',p.name))
 out.extend(p for p in pathlib.Path('/sandbox').glob('homecompute-office-install-*') if re.fullmatch('homecompute-office-install-[0-9]+',p.name))
 return sorted(out)
def inventory(root):
 result={};total=0;stack=[root];seen=1
 while stack:
  p=stack.pop();info=p.lstat();key=str(p.relative_to(root))
  if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise ValueError('operator-artifact-link-or-special-file')
  directory=stat.S_ISDIR(info.st_mode)
  row={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'kind':'directory' if directory else 'file'}
  if directory:
   with os.scandir(p) as children:
    for child in children:
     seen+=1
     if seen>2048:raise ValueError('operator-artifact-budget')
     stack.append(pathlib.Path(child.path))
  else:
   total+=info.st_size
   if total>64*1024**2:raise ValueError('operator-artifact-budget')
   with p.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
   if p.stat().st_size!=info.st_size:raise ValueError('operator-artifact-changed-during-read')
   row.update(bytes=info.st_size,sha256=checksum)
  result[key]=row
 return result
def referenced(root,config):
 if isinstance(config,str):
  aliases=[str(root),str(root).replace('/sandbox/','~/',1)]
  if BASE in root.parents:aliases.extend([str(root.relative_to(BASE)),'./'+str(root.relative_to(BASE))])
  return any(config==alias or config.startswith(alias+'/') or config.startswith('file://'+alias) for alias in aliases)
 if isinstance(config,dict):return any(referenced(root,v) for v in config.values())
 if isinstance(config,list):return any(referenced(root,v) for v in config)
 return False
def collect():
 cfg=json.loads((BASE/'openclaw.json').read_bytes());result={}
 for root in selected():
  for parent in (root,*root.parents):
   if parent.is_symlink():raise ValueError('operator-artifact-root-link')
   if parent==pathlib.Path('/sandbox'):break
  if referenced(root,cfg):raise ValueError('operator-artifact-is-runtime-input')
  if root.name.startswith('homecompute-office-install-'):
   info=root.stat()
   if not root.is_dir() or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('retired-root-install-stage-required')
   for process in pathlib.Path('/proc').glob('[0-9]*'):
    if process.name==str(os.getpid()):continue
    try:
     args=(process/'cmdline').read_bytes().split(b'\0')
     if any(str(root).encode() in arg for arg in args):raise ValueError('operator-install-stage-in-use')
     try:cwd=(process/'cwd').readlink()
     except OSError:cwd=None
     if cwd is not None and (cwd==root or root in cwd.parents):raise ValueError('operator-install-stage-in-use')
    except (FileNotFoundError,ProcessLookupError):pass
  rows=inventory(root)
  if root.name.startswith('config-before-') and rows['.']['sha256']!=root.name[14:-5]:raise ValueError('office-before-image-name-digest-mismatch')
  result[str(root)]=rows
  if len(result)>128 or sum(r.get('bytes',0) for rr in result.values() for r in rr.values())>256*1024**2:raise ValueError('operator-archive-total-budget')
 return result
def paused():
 cfg=json.loads((BASE/'openclaw.json').read_bytes())
 if cfg.get('cron',{}).get('enabled') is not False:raise ValueError('operator-archive-requires-paused-cron')
 if any(v.get('enabled') is not False for k,v in cfg.get('channels',{}).items() if k!='defaults' and isinstance(v,dict)):raise ValueError('operator-archive-requires-paused-channels')
def remove(expected):
 paused()
 if collect()!=expected:raise ValueError('operator-artifacts-changed-before-removal')
 # Root-only quarantine and descriptor-relative deletion prevent agent-writable
 # parent directories from redirecting deletion after the verified move.
 for name,rows in expected.items():
  root=pathlib.Path(name);parent=BASE if BASE==root.parent or BASE in root.parents else pathlib.Path('/sandbox')
  quarantine=pathlib.Path(tempfile.mkdtemp(prefix='.homecompute-operator-quarantine-',dir=parent));os.chmod(quarantine,0o700)
  fd=os.open(quarantine,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:
   os.rename(root,'retired',dst_dir_fd=fd)
   moved=pathlib.Path('/proc/self/fd')/str(fd)/'retired'
   if inventory(moved)!=rows:raise ValueError('quarantined-artifact-drift-retained-for-review')
   if rows['.']['kind']=='directory':shutil.rmtree('retired',dir_fd=fd)
   else:os.unlink('retired',dir_fd=fd)
  finally:os.close(fd)
  quarantine.rmdir()
 if collect():raise ValueError('operator-artifact-removal-incomplete')
request=json.load(sys.stdin)
try:
 if request['action']=='inventory':print(json.dumps(collect(),sort_keys=True))
 elif request['action']=='remove':remove(request['entries']);print(json.dumps({'removed':len(request['entries'])}))
 else:raise ValueError('operator-artifact-action')
except Exception as error:
 message=str(error)
 category=message if isinstance(error,ValueError) and message.startswith(('operator-','office-','retired-','quarantined-')) else type(error).__name__
 print(json.dumps({'error_category':category}))
'''


def run(argv,*,data=None,timeout=90):
 result=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if result.returncode:raise ValueError('operator-artifact-command-failed')
 if len(result.stdout)>4*1024**2:raise ValueError('operator-artifact-output-budget')
 return result.stdout


def container_inventory(container,action='inventory',entries=None):
 request={'action':action}
 if entries is not None:request['entries']=entries
 result=json.loads(run(['docker','exec','-i','--user','0',container,'python3','-c',CONTAINER_CODE],data=json.dumps(request).encode()))
 if 'error_category' in result:raise ValueError('operator-archive-container:'+result['error_category'])
 return result


def host_inventory(root):
 result={}
 for path in [root,*sorted(root.rglob('*'))] if root.is_dir() else [root]:
  info=path.lstat()
  if path.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise ValueError('archive-link-or-special-file')
  row={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),'kind':'directory' if path.is_dir() else 'file'}
  if path.is_file():
   with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest();os.fsync(stream.fileno())
   row.update(bytes=info.st_size,sha256=checksum)
  result[str(path.relative_to(root))]=row
 # Files were fsynced above; persist nested directory entries before the
 # external manifest can authorize deletion of the original tree.
 for path in sorted((p for p in [root,*root.rglob('*')] if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
  fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(fd)
  finally:os.close(fd)
 return result


def protected(path):
 for p in (path,*path.parents):
  s=p.lstat()
  if s.st_uid!=0 or s.st_mode&0o022 or stat.S_ISLNK(s.st_mode):raise ValueError('root-protected-operator-archive-required')


def atomic(path,value):
 temporary=path.with_suffix('.new');fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as stream:json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
 temporary.replace(path);fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)


def archive_owned(container,guard,*,apply=False,archive_root=ARCHIVES):
 if os.geteuid()!=0 or not re.fullmatch(r'openshell-default--agent-openclaw-[a-f0-9-]{36}',container):raise ValueError('root-exact-native-archive-required')
 guard();entries=container_inventory(container)
 pending=archive_root/'pending.json'
 if not apply:return {'apply':False,'targets':len(entries),'pending':pending.exists(),'files':sum(row['kind']=='file' for rows in entries.values() for row in rows.values())}
 if pending.exists():raise ValueError('operator-archive-needs-reviewed-continuation')
 if not entries:return {'apply':True,'targets':0,'verified':True}
 archive_root.mkdir(mode=0o700,parents=True,exist_ok=True);protected(archive_root)
 parent_fd=os.open(archive_root.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(parent_fd)
 finally:os.close(parent_fd)
 if stat.S_IMODE(archive_root.stat().st_mode)!=0o700:raise ValueError('private-operator-archive-required')
 fd=os.open(archive_root/'.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if pending.exists():raise ValueError('operator-archive-needs-reviewed-continuation')
  destination=Path(tempfile.mkdtemp(prefix='native-home-',dir=archive_root));destination.chmod(0o700)
  record={'schema_version':1,'container':container,'created_at':time.time(),'status':'copying','entries':entries,'copies':{}}
  atomic(pending,{'archive':str(destination),'container':container})
  try:
   for i,(source,expected) in enumerate(entries.items()):
    target=destination/('entry-'+str(i));run(['docker','cp','--archive',container+':'+source,str(target)])
    if host_inventory(target)!=expected:raise ValueError('external-operator-archive-verification-failed')
    record['copies'][source]=target.name
   record['status']='copied-verified';atomic(destination/'manifest.json',record)
   guard()
   if container_inventory(container)!=entries:raise ValueError('operator-artifacts-changed-after-copy')
   container_inventory(container,'remove',entries)
   record['status']='complete';atomic(destination/'manifest.json',record)
   pending.unlink();fd_dir=os.open(archive_root,os.O_RDONLY)
   try:os.fsync(fd_dir)
   finally:os.close(fd_dir)
   return {'apply':True,'targets':len(entries),'verified':True,'private_archive':str(destination)}
  except Exception:
   record['status']='failed';atomic(destination/'manifest.json',record);raise
 finally:os.close(fd)


def current_guard():
 home=Path('/home/hermes-operator');etc=Path('/etc/homecompute-openclaw');settings={}
 for name in ('agent-openclaw.env','supervisor-identity.env'):
  path=etc/name;s=path.lstat()
  if s.st_uid!=1000 or stat.S_IMODE(s.st_mode)!=0o600 or not stat.S_ISREG(s.st_mode):raise ValueError('owned-native-identity-input-required')
  for line in path.read_text().splitlines():
   if line and not line.startswith('#'):
    key,value=line.split('=',1);settings[key]=value.strip('"')
 spec=importlib.util.spec_from_file_location('archive_profiles',ROOT/'scripts/supervise-openclaw-nemoclaw.py');supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
 profile=supervisor.PROFILES[settings['NEMOCLAW_INSTALL_REF']];identifier=settings['OPENCLAW_EXPECTED_SANDBOX_ID'];generation=settings['OPENCLAW_EXPECTED_GENERATION']
 container='openshell-default--agent-openclaw-'+identifier
 def guard():
  registry=json.loads((home/'.nemoclaw/gateways/9123/sandboxes.json').read_bytes())['sandboxes']['agent-openclaw']
  if registry['lifecycleGeneration']!=generation or registry['workload']['reference']!=profile['image'] or registry['workload']['sourceRevision']!=settings['NEMOCLAW_INSTALL_REF']:raise ValueError('operator-archive-registry-drift')
  metadata=json.loads(run(['runuser','-u','hermes-operator','--','env','HOME='+str(home),'PATH=/usr/bin:/bin',str(home/'.local/bin/openshell'),'sandbox','get','-g','nemoclaw-9123','agent-openclaw','-o','json']))
  if metadata.get('id')!=identifier or metadata.get('name')!='agent-openclaw' or metadata.get('phase')!='Ready':raise ValueError('operator-archive-native-identity-drift')
  image=run(['docker','inspect','--format','{{.Config.Image}}',container]).decode().strip()
  if image not in (profile['image'],profile['image'].split('@')[1]):raise ValueError('operator-archive-image-drift')
 return container,guard


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--revision',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
 if os.geteuid()!=0 or socket.gethostname()!='agents' or not re.fullmatch('[a-f0-9]{40}',args.revision) or ROOT!=Path('/srv/homecompute/releases')/args.revision:raise ValueError('published-guest-root-lifecycle-required')
 protected(ROOT)
 for name in ('scripts/archive-openclaw-operator-artifacts.py','scripts/supervise-openclaw-nemoclaw.py'):
  if run(['git','-C',str(ROOT),'show',args.revision+':'+name])!=(ROOT/name).read_bytes():raise ValueError('published-operator-archive-source-mismatch')
 run(['git','-C',str(ROOT),'merge-base','--is-ancestor',args.revision,'origin/master'])
 os.umask(0o077);container,guard=current_guard();print(json.dumps(archive_owned(container,guard,apply=args.apply),sort_keys=True))


if __name__=='__main__':main()
