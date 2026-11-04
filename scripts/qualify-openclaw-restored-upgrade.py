#!/usr/bin/env python3
"""Qualify native upgrade/rollback against retained data in a networkless COW VM."""
from contextlib import ExitStack
from datetime import datetime,timezone
import argparse
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
FILES=('scripts/qualify-openclaw-restored-upgrade.py','scripts/prepare-openclaw-upgrade-assets.py',
 'scripts/supervise-openclaw-nemoclaw.py','scripts/restore-openclaw-nemoclaw.py','scripts/setup-openclaw-child-results.py','scripts/setup-openclaw-llm-queue.py',
 'deploy/openclaw/recovery/inspect-restored-state.py','deploy/openclaw/qualification/restored-upgrade.py',
 'deploy/openclaw/safety-plugin/llm-queue.mjs','deploy/openclaw/qualification/installed-mattermost.mjs')
PROBE='deploy/openclaw/qualification/restored-upgrade.py'
spec=importlib.util.spec_from_file_location('engine',ROOT/'scripts/verify-application-recovery.py');ENGINE=importlib.util.module_from_spec(spec);spec.loader.exec_module(ENGINE)
spec=importlib.util.spec_from_file_location('assets',ROOT/'scripts/prepare-openclaw-upgrade-assets.py');ASSETS=importlib.util.module_from_spec(spec);spec.loader.exec_module(ASSETS)
XORRISO=Path('/nix/var/nix/gcroots/homecompute-codex-vm/xorriso/bin/xorriso')


def accepted(data):
 return all(data.get(key) is True for key in ('native_upgrade_verified','native_rollback_verified','state_preserved','authenticated_gateway_verified','task_delivery_verified','agent_handoff_verified','native_yield_verified','mcp_binding_verified','preparation_preserved','upgrade_state_preserved','rollback_state_preserved','oci_transport_name_verified','native_sqlite_integrity','installed_mattermost_ingress_verified','registry_metadata_replay')) and data.get('roles')==12 and type(data.get('native_sqlite_databases')) is int and data['native_sqlite_databases']>=39


def result_from_log(raw,nonce):
 prefix=('HC_OPENCLAW_UPGRADE_'+nonce+' ').encode();matches=[line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
 if len(matches)!=1:return None
 if len(matches[0])>32768:raise ValueError('qualification-result-bound')
 return json.loads(matches[0])


def seed(nonce,expected,assets_sha):
 data={'hostname':'agents','preserve_hostname':True,'ssh_deletekeys':False,'network':{'config':'disabled'},'package_update':False,'package_upgrade':False,
  'write_files':[{'path':'/run/hc-openclaw-qualification-permit','owner':'root:root','permissions':'0400','content':nonce},
   {'path':'/run/hc-openclaw-qualify-upgrade.py','owner':'root:root','permissions':'0500','encoding':'b64','content':base64.b64encode((ROOT/PROBE).read_bytes()).decode()}],
  'runcmd':[['python3','/run/hc-openclaw-qualify-upgrade.py','--nonce',nonce,'--expected-policy',expected,'--manifest-sha256',assets_sha]]}
 return '#cloud-config\n'+json.dumps(data)


def validate_assets(directory,expected):
 if directory.is_symlink() or not directory.is_absolute() or directory.parent!=ENGINE.SCRATCH:raise ValueError('private scratch asset directory required')
 ENGINE.protected(directory,directory=True);ENGINE.protected(directory/'manifest.json',limit=65536)
 if ASSETS.sha(directory/'manifest.json')!=expected:raise ValueError('selected asset manifest mismatch')
 manifest=json.loads((directory/'manifest.json').read_bytes())
 names={'nemoclaw-0.0.129.tar.gz','nemoclaw-0.0.140.tar.gz','catalog-0.0.129.json','catalog-0.0.140.json','candidate-image.tar'}
 if set(manifest['files'])!=names or manifest.get('registry_metadata_replay') is not True:raise ValueError('finite public assets required')
 total=0
 for name,row in manifest['files'].items():
  path=directory/name;ENGINE.protected(path,limit=3*1024**3)
  if path.stat().st_size!=row['bytes'] or ASSETS.sha(path)!=row['sha256']:raise ValueError('public asset content mismatch')
  total+=row['bytes']
 if total>5*1024**3:raise ValueError('public asset size bound')
 for version in ASSETS.VERSIONS:
  if ASSETS.sha(directory/('nemoclaw-'+version+'.tar.gz'))!=ASSETS.ARCHIVES[version]:raise ValueError('CLI artifact mismatch')
  ASSETS.catalog_valid(json.loads((directory/('catalog-'+version+'.json')).read_bytes()),version)
 return manifest


def memory_state():
 available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
 pressure=Path('/proc/pressure/memory').read_text()
 values={name:float(value) for name,value in re.findall(r'^(some|full) avg10=([0-9]+(?:\.[0-9]+)?)',pressure,re.MULTILINE)}
 if set(values)!={'some','full'}:raise ValueError('memory pressure observation unavailable')
 return available,values


def memory_admitted(available,pressure,*,running=False):
 # The scope's 12 GiB maximum plus a 5 GiB host reserve. A separate 4 GiB
 # live floor leaves time to terminate the owned VM during another workload's
 # burst; any sustained pressure also aborts instead of paging production.
 floor=(4 if running else 17)*1024**3
 return available>=floor and pressure['full']<=(1.0 if running else 0.1) and pressure['some']<=(5.0 if running else 1.0)


def verify(args,revision):
 manifest=ENGINE.restored_input(args.restore_directory,args.snapshot,args.archive)
 ENGINE.protected(ENGINE.STATE/'last-openclaw-verification.json',limit=65536)
 previous=json.loads((ENGINE.STATE/'last-openclaw-verification.json').read_bytes())
 if previous.get('accepted') is not True or any(previous.get(k)!=v for k,v in {'snapshot':args.snapshot,'archive':args.archive,'manifest_sha256':manifest}.items()):raise ValueError('accepted matching native recovery proof required')
 validate_assets(args.assets,args.expected_assets_sha256)
 available,pressure=memory_state()
 if not memory_admitted(available,pressure) or shutil.disk_usage(ENGINE.SCRATCH).free<32*1024**3:raise ValueError('isolated native headroom unavailable')
 executable=ENGINE.QEMU_ROOT.resolve(strict=True)/'bin/qemu-system-x86_64'
 for tool in (executable,XORRISO.resolve(strict=True)):
  if not str(tool).startswith('/nix/store/') or tool.stat().st_uid!=0 or tool.stat().st_mode&0o022:raise ValueError('protected Nix tool required')
 work=Path(tempfile.mkdtemp(prefix='openclaw-upgrade-',dir=ENGINE.SCRATCH));work.chmod(0o700)
 stage=work/'iso';stage.mkdir();(stage/'assets').mkdir()
 for name in json.loads((args.assets/'manifest.json').read_bytes())['files']:
  os.link(args.assets/name,stage/'assets'/name)
 os.link(args.assets/'manifest.json',stage/'assets/manifest.json')
 for name in FILES:
  target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
 nonce=str(uuid.uuid4())
 for name,data in {'user-data':seed(nonce,args.expected_policy_sha256,args.expected_assets_sha256),
  'meta-data':json.dumps({'instance-id':'hc-upgrade-'+nonce,'local-hostname':'agents','dsmode':'local'}),
  'network-config':json.dumps({'version':2,'ethernets':{}})}.items():(stage/name).write_text(data)
 iso=work/'qualification.iso'
 ENGINE.run([str(XORRISO),'-as','mkisofs','-quiet','-r','-joliet','-volid','cidata','-output',str(iso),str(stage)],timeout=300)
 account,kvm=ENGINE.vm_identity({'scope':'agents-vm'});guest=work/'guest';guest.mkdir(mode=0o700);os.chown(guest,account.pw_uid,account.pw_gid)
 disk=args.restore_directory/'state/agents-vm/agents.qcow2';ENGINE.engine_input(disk,args.restore_directory);before=disk.stat()
 log=work/'serial.log';errors=work/'qemu.stderr';result=None
 with ExitStack() as stack:
  streams=[stack.enter_context(p.open('rb')) for p in (disk,iso)];descriptors=[s.fileno() for s in streams]
  output=stack.enter_context(log.open('xb'));error=stack.enter_context(errors.open('xb'))
  command=ENGINE.vm_command(str(executable),'agents-vm',descriptors);command[command.index('-m')+1]='10240';command[command.index('-smp')+1]='4'
  def child():ENGINE.vm_child(account.pw_uid,account.pw_gid,kvm,memory_limit=32*1024**3,cpu_seconds=2250,file_limit=12*1024**3,cpu_count=2)
  with ENGINE.owned_process(command,stdin=subprocess.DEVNULL,stdout=output,stderr=error,cwd=guest,env={**ENGINE.ENV,'TMPDIR':'.'},pass_fds=descriptors,preexec_fn=child,cleanup_options={'graceful_timeout':60,'kill_timeout':60}) as process:
   deadline=time.monotonic()+2100;pressure_samples=0
   while time.monotonic()<deadline:
    available,pressure=memory_state()
    pressure_samples=pressure_samples+1 if not memory_admitted(available,pressure,running=True) else 0
    observation={'available_bytes':available,'pressure':pressure,'consecutive_pressure_samples':pressure_samples}
    (work/'memory-observation.json').write_text(json.dumps(observation,sort_keys=True))
    if available<4*1024**3 or pressure_samples>=3:raise ValueError('owned qualification stopped to preserve host memory')
    ENGINE.private_logs_bounded(log,errors);result=result_from_log(log.read_bytes(),nonce)
    if result is not None:break
    if process.poll() is not None:raise ValueError('owned qualification VM exited')
    time.sleep(2)
   if result is None:raise ValueError('owned qualification deadline exceeded')
 after=disk.stat()
 if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('restored source disk changed')
 report={**result,'accepted':accepted(result),'source_revision':revision,'created_at':datetime.now(timezone.utc).isoformat(),
  'source_disk_read_only':True,'network':False,'production_modified':False,'manifest_sha256':manifest,'assets_manifest_sha256':args.expected_assets_sha256,'policy_sha256':args.expected_policy_sha256}
 receipt=work/'receipt.json';receipt.write_text(json.dumps(report,sort_keys=True))
 print(json.dumps({'accepted':report['accepted'],'receipt':str(receipt),'error_category':report.get('error_category'),'production_modified':False}))
 return report['accepted']


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--restore-directory',type=Path,required=True);p.add_argument('--snapshot',required=True);p.add_argument('--archive',required=True)
 p.add_argument('--assets',type=Path,required=True);p.add_argument('--expected-assets-sha256',required=True);p.add_argument('--expected-policy-sha256',required=True);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
 if os.geteuid()!=0 or socket.gethostname()!='home-core' or any(not re.fullmatch('[0-9a-f]{64}',v) for v in (a.expected_assets_sha256,a.expected_policy_sha256)):raise ValueError('trusted core operator and exact hashes required')
 os.umask(0o077);revision=ENGINE.published_source()
 for name in (*FILES,'scripts/verify-application-recovery.py'):
  if ENGINE.run(['git','-C',str(ROOT),'show',revision+':'+name]).stdout!=(ROOT/name).read_bytes():raise ValueError('published qualification source mismatch')
 def interrupted(*_):raise KeyboardInterrupt
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
 if not a.worker:
  unit='homecompute-openclaw-upgrade-'+uuid.uuid4().hex+'.scope';dispatch=Path(tempfile.mkdtemp(prefix='openclaw-upgrade-dispatch-',dir=ENGINE.SCRATCH));dispatch.chmod(0o700)
  command=['systemd-run','--scope','--collect','--quiet','--unit',unit,'--property=MemoryHigh=11G','--property=MemoryMax=12G','--property=IOWeight=10',
   '--property=IOReadBandwidthMax=/srv/restore-tests 40M','--property=IOWriteBandwidthMax=/srv/restore-tests 20M',sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--worker']
  try:
   result=ENGINE.run(command,timeout=2550,check=False);(dispatch/'worker.stdout').write_bytes(result.stdout);(dispatch/'worker.stderr').write_bytes(result.stderr)
  finally:
   ENGINE.run(['systemctl','stop',unit],check=False,timeout=60)
   members=Path('/sys/fs/cgroup/system.slice')/unit/'cgroup.procs'
   if members.exists() and members.read_text().strip():raise ValueError('owned qualification cleanup unverified')
  sys.stdout.write(result.stdout.decode())
  if result.returncode and not result.stdout:print(json.dumps({'accepted':False,'error_category':'qualification-worker-failed','private_diagnostics':str(dispatch),'production_modified':False}))
  raise SystemExit(result.returncode)
 group=Path('/proc/self/cgroup').read_text().strip().split('::')[-1]
 if not re.fullmatch('/system.slice/homecompute-openclaw-upgrade-[0-9a-f]{32}\\.scope',group):raise ValueError('owned resource scope required')
 with (ENGINE.SCRATCH/'.engine-verification.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if not verify(a,revision):raise SystemExit(1)


if __name__=='__main__':main()
