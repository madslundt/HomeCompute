#!/usr/bin/env python3
"""Finalize a measured .05/.05 candidate from a new published canonical release.

No model/voice promotion is implied. Two env files are replaced individually
with a write-ahead journal; this is recoverable, not a cross-file atomic rename.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RELEASES = Path('/srv/homecompute/releases')
STATE = Path('/var/lib/homecompute-speech-finalization')
CAPACITY = Path('/var/lib/homecompute-speech-capacity')
TRIALS = Path('/var/lib/homecompute-spark-rollout')
REPOSITORY = 'https://github.com/madslundt/HomeCompute.git'
QUALIFICATION_REVISION = '9c63ffab299e7325419a4515cb8acb3b4d5b7dce'
ROLES = {
    'hviske': {'container':'gb10-hviske-primary','service':'hviske-primary','port':8000,
        'env':'/etc/gb10-ai/hviske.env','setting':'HVISKE_GPU_MEMORY_UTILIZATION','old':'0.10',
        'compose':'deploy/hviske-stt/compose.yaml','example':'config/hviske-stt.env.example',
        'setup':'scripts/setup-compute-hviske-stt.sh'},
    'plapre': {'container':'homecompute-plapre-plapre-primary-1','service':'plapre-primary','port':8004,
        'env':'/etc/gb10-ai/plapre-tts.env','setting':'PLAPRE_GPU_MEMORY_UTILIZATION','old':'0.06',
        'compose':'deploy/compute-node/plapre/compose.yaml','example':'config/plapre-tts.env.example',
        'setup':'scripts/setup-compute-plapre.sh'},
}
INGRESS = ('gb10-hviske-http','homecompute-plapre-plapre-http-1')
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
PROGRESS = False
ENV = {'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/run/current-system/sw/bin', 'HOME':'/var/empty',
       'DOCKER_CONFIG':'/var/empty','DOCKER_HOST':'unix:///var/run/docker.sock',
       'GIT_TERMINAL_PROMPT':'0','PYTHONDONTWRITEBYTECODE':'1'}
IDLE_PROBE = r'''
import json,re,urllib.request
from pathlib import Path
port=int(__import__('sys').argv[1]); result={'connections':0,'running':0,'waiting':0,'listening':0}
for path in ['/proc/net/tcp','/proc/net/tcp6']:
 for line in Path(path).read_text().splitlines()[1:]:
  row=line.split()
  if int(row[1].rsplit(':',1)[1],16)!=port: continue
  if row[3] in {'01','08'}: result['connections']+=1
  if row[3]=='0A': result['listening']+=1
if port==8000 and result['listening']:
 with urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=5) as response: metrics=response.read(4000000).decode()
 samples=re.findall(r'^vllm:num_requests_(running|waiting)(?:\{[^}]*\})?\s+([0-9.e+]+)$',metrics,re.M)
 if {x for x,_ in samples}!={'running','waiting'}: raise ValueError('unknown_asr_activity')
 for kind,value in samples:
  number=float(value)
  if number<0 or not number.is_integer(): raise ValueError('invalid_asr_activity')
  result[kind]+=int(number)
print(json.dumps(result))
'''


def run(arguments: list[str], *, timeout=60) -> str:
    value=subprocess.run(arguments,capture_output=True,text=True,env=ENV,timeout=timeout)
    if value.returncode: raise RuntimeError('managed-operation-failed')
    return value.stdout.strip()


def digest(value) -> str:
    return hashlib.sha256(value if isinstance(value,bytes) else json.dumps(value,sort_keys=True).encode()).hexdigest()


def protected(path: Path, *, private=False, directory=False):
    info=path.lstat()
    if (info.st_uid!=0 or info.st_mode & (0o077 if private else 0o022) or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError('protected-path-required')
    return info


def read_json(path: Path) -> dict:
    if protected(path,private=True).st_size>8*1024**2: raise ValueError('receipt-size-bound')
    value=json.loads(path.read_bytes())
    if not isinstance(value,dict): raise ValueError('receipt-object-required')
    return value


def atomic(path: Path, body: bytes, *, metadata=None) -> None:
    protected(path.parent,directory=True)
    if path.exists() or path.is_symlink(): protected(path)
    descriptor, name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(body);stream.flush()
            if metadata: os.fchown(stream.fileno(),metadata['uid'],metadata['gid'])
            os.fchmod(stream.fileno(),metadata['mode'] if metadata else 0o600)
            os.fsync(stream.fileno())
        os.replace(name,path)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:Path(name).unlink(missing_ok=True)


@contextmanager
def blocked_signals():
    previous=signal.pthread_sigmask(signal.SIG_BLOCK,SIGNALS)
    try:yield
    finally:signal.pthread_sigmask(signal.SIG_SETMASK,previous)


def published(source: Path, revision: str) -> None:
    if not re.fullmatch('[0-9a-f]{40}',revision) or source!=RELEASES/revision:
        raise ValueError('exact-published-release-required')
    for path in [*reversed(source.parents),source]: protected(path,directory=True)
    if (run(['git','-C',str(source),'rev-parse','HEAD'])!=revision or
            run(['git','-C',str(source),'remote','get-url','origin'])!=REPOSITORY or
            run(['git','-C',str(source),'status','--porcelain','--untracked-files=all'])):
        raise ValueError('published-source-mismatch')
    run(['git','-C',str(source),'merge-base','--is-ancestor',revision,'origin/master'])


def load_module(path: Path, name: str):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def patch_budget(body: bytes, setting: str, old: str) -> bytes:
    pattern=re.compile(rb'(?m)^'+setting.encode()+rb'=([^\r\n]*)')
    matches=list(pattern.finditer(body))
    if len(matches)!=1: raise ValueError('unique-budget-setting-required')
    value=matches[0].group(1)
    parsed=re.fullmatch(rb'([ \t]*)([\"\x27]?)'+re.escape(old.encode())+rb'\2([ \t]*(?:#.*)?)',value)
    if not parsed:raise ValueError('expected-previous-env-budget-required')
    replacement=parsed[1]+parsed[2]+b'0.05'+parsed[2]+parsed[3]
    return body[:matches[0].start(1)]+replacement+body[matches[0].end(1):]


def selected_contract(document: dict, key: str) -> dict:
    service=dict(document['services'][ROLES[key]['service']])
    if service.get('build'):
        service['build']={name:value for name,value in service['build'].items() if name!='context'}
    resources={}
    for kind in ('networks','secrets','configs'):
        refs=service.get(kind,{})
        names=refs if isinstance(refs,dict) else [row if isinstance(row,str) else row['source'] for row in refs]
        resources[kind]={name:document[kind][name] for name in names}
    return {'service':service,'resources':resources,'project':document['name']}


def render(key: str, environment: Path) -> dict:
    arguments=['docker','compose','--env-file',str(environment),'-f',str(ROOT/ROLES[key]['compose'])]
    if key=='hviske':arguments+=['--profile','hviske']
    value=json.loads(run(arguments+['config','--format','json']))
    service=value['services'][ROLES[key]['service']]
    service['image']=json.loads(run(['docker','image','inspect',service['image']]))[0]['Id']
    return value


def inspect(key: str) -> dict:
    return json.loads(run(['docker','inspect',ROLES[key]['container']]))[0]


def runtime_contract(actual: dict, key: str) -> tuple[str,str]:
    command=list(actual['Config']['Cmd'])
    if key=='hviske':
        found=re.findall(r'--gpu-memory-utilization\s+[\"\x27]?([0-9.]+)',command[0])
        if len(found)!=1:raise ValueError('unknown-actual-budget')
        budget=found[0]
        command=[re.sub(r'(--gpu-memory-utilization\s+[\"\x27]?)[0-9.]+',r'\1CAPACITY_BUDGET',command[0])]
    else:
        if command.count('--gpu-mem')!=1:raise ValueError('unknown-actual-budget')
        index=command.index('--gpu-mem')+1;budget=command[index];command[index]='CAPACITY_BUDGET'
    contract={'command':command,'entrypoint':actual['Config']['Entrypoint'],
        'environment':dict(row.split('=',1) for row in actual['Config']['Env']),'speaker':'tor','tempo':1.20}
    return digest(contract),budget


def model_matches(document: dict, key: str, runtime_hash: str, expected_file: Path) -> None:
    actual=inspect(key);service=document['services'][ROLES[key]['service']]
    observed,budget=runtime_contract(actual,key)
    labels=actual['Config'].get('Labels',{})
    if (actual['State'].get('Running') is not True or actual['State'].get('Health',{}).get('Status')!='healthy' or
            actual['Image']!=service['image'] or observed!=runtime_hash or budget!='0.05' or
            labels.get('com.docker.compose.project')!=document['name'] or
            labels.get('com.docker.compose.project.config_files')!=str(expected_file)):
        raise ValueError('actual-qualified-contract-or-source-owner-mismatch')
    if expected_file==ROOT/ROLES[key]['compose'] and labels.get('com.docker.compose.project.environment_file')!=ROLES[key]['env']:
        raise ValueError('canonical-production-env-owner-mismatch')
    host=actual.get('HostConfig',{})
    for declared,field,convert in [('read_only','ReadonlyRootfs',bool),('mem_limit','Memory',int),
                                   ('pids_limit','PidsLimit',int),('shm_size','ShmSize',int),
                                   ('cpus','NanoCpus',lambda v:int(float(v)*1000000000)),('cap_drop','CapDrop',list)]:
        if declared in service and host.get(field)!=convert(service[declared]):
            raise ValueError('actual-model-resource-contract-differs')
    networks=actual['NetworkSettings']['Networks']
    expected_networks={document['networks'][name]['name'] for name in service.get('networks',{})}
    if set(networks)!=expected_networks:raise ValueError('actual-model-network-contract-differs')
    for name,options in service.get('networks',{}).items():
        if options and 'ipv4_address' in options and networks[document['networks'][name]['name']]['IPAddress']!=options['ipv4_address']:
            raise ValueError('actual-model-address-contract-differs')
    expected=[]
    for mount in service.get('volumes',[]):
        if mount.get('type')!='bind':raise ValueError('unreviewed-model-volume')
        expected.append((os.path.realpath(mount['source']),mount['target'],mount.get('read_only',False)))
    for secret in service.get('secrets',[]):
        name=secret if isinstance(secret,str) else secret['source']
        target=('/run/secrets/'+name) if isinstance(secret,str) else secret.get('target','/run/secrets/'+name)
        if not target.startswith('/'):target='/run/secrets/'+target
        expected.append((os.path.realpath(document['secrets'][name]['file']),target,True))
    observed_mounts=[(os.path.realpath(row['Source']),row['Destination'],not row['RW']) for row in actual['Mounts']]
    if sorted(expected)!=sorted(observed_mounts):raise ValueError('actual-model-mounts-differ')


def evidence(trial: Path, expected: str, *, active_bytes: bytes | None = None) -> dict:
    if trial.parent!=TRIALS or not re.fullmatch(r'trial-[A-Za-z0-9_-]+',trial.name):raise ValueError('bounded-trial-required')
    protected(trial,private=True,directory=True)
    result=read_json(trial/'result.json')
    if digest((trial/'result.json').read_bytes())!=expected:raise ValueError('reviewed-result-hash-differs')
    active=read_json(CAPACITY/'active.json') if active_bytes is None else json.loads(active_bytes)
    plan=read_json(trial/'plan.json')
    unsigned={k:v for k,v in active.items() if k!='plan_sha256'}
    if (active!=plan or digest(unsigned)!=active.get('plan_sha256') or result.get('plan_sha256')!=active.get('plan_sha256') or
            result.get('source_revision')!=active.get('source_revision') or active.get('source_revision')!=QUALIFICATION_REVISION or
            result.get('state')!='capacity-candidate' or
            result.get('capacity_qualified') is not True or result.get('accepted') is not False or
            result.get('full_voice_accepted') is not False):raise ValueError('qualified-active-plan-required')
    old=RELEASES/active['source_revision'];published(old,active['source_revision'])
    policy=load_module(old/'scripts/speech_capacity.py','qualified_capacity_policy')
    baseline=read_json(trial/'baseline.json');candidate=read_json(trial/'candidate.json')
    if policy.compare(baseline,candidate).get('capacity_qualified') is not True:raise ValueError('measured-gates-did-not-pass')
    capacity=candidate.get('capacity',{})
    if (capacity.get('passed') is not True or type(capacity.get('samples')) is not int or capacity['samples']<1 or
            type(capacity.get('minimum_available_ratio')) not in (int,float) or
            not math.isfinite(capacity['minimum_available_ratio']) or not .10<=capacity['minimum_available_ratio']<=1 or
            type(capacity.get('minimum_llm_running')) is not int or capacity['minimum_llm_running']<1 or
            type(candidate.get('observation_failures')) is not int or candidate['observation_failures']!=0):raise ValueError('continuous-headroom-evidence-required')
    pin=read_json(trial/'corpus-pin.json');source_pin=json.loads((old/'config/spark-rollout-corpus.json').read_bytes())
    if (pin.get('archive_sha256')!=source_pin['archive_sha256'] or pin.get('revision')!=source_pin['revision'] or
            pin.get('danish_fixtures')!=18):raise ValueError('pinned-public-corpus-required')
    for label in ('baseline','candidate'):
        load=read_json(trial/('load-'+label+'.json'));counts=load.get('accepted_requests_per_worker')
        if (load.get('streams')!=2 or load.get('failed_requests')!=0 or load.get('outputs_retained') is not False or
                load.get('maximum_output_tokens_per_request')!=4096 or load.get('maximum_seconds')!=600 or
                not isinstance(counts,list) or len(counts)!=2 or any(type(n) is not int or n<1 for n in counts)):
            raise ValueError('complete-controlled-load-evidence-required')
    directory=CAPACITY/active['plan_sha256'];protected(directory,private=True,directory=True)
    documents={}
    for label in ('baseline','candidate'):
        values={key:read_json(directory/(key+'-'+label+'.json')) for key in ROLES}
        if digest(values)!=active[label+'_sha256']:raise ValueError('private-candidate-plan-changed')
        for key,document in values.items():
            build=document['services'][ROLES[key]['service']].get('build')
            if build and build.get('context')!=str(old):raise ValueError('qualified-build-source-owner-mismatch')
        if label=='candidate':documents=values
    return {'plan':active,'documents':documents,'directory':directory,'candidate':candidate,
            'runtime_hashes':candidate['runtime']['contract_sha256'],'policy':policy}


class Transaction:
    def __init__(self,directory: Path, journal: dict):self.directory,self.data=directory,journal
    def save(self):atomic(self.directory/'journal.json',json.dumps(self.data,sort_keys=True).encode())
    def phase(self,value):
        self.data['phase']=value;self.save()
        if PROGRESS:print('Speech capacity finalization: '+value,flush=True)
    def addresses(self):return [(row['address'],row['port']) for row in self.data.get('addresses',[])]


def validate_journal(value: dict):
    phases={'preparing','env-updating','canonical-starting','canonical-ready','marker-retired','completed',
            'restoring','candidate-restored','candidate-unchanged','recovery-required'}
    if (value.get('phase') not in phases or not re.fullmatch('[0-9a-f]{32}',value.get('nonce','')) or
            not isinstance(value.get('ingress_active'),list) or
            len(value['ingress_active'])!=len(set(value['ingress_active'])) or set(value['ingress_active'])-set(INGRESS) or
            not re.fullmatch('[0-9a-f]{64}',value.get('active_sha256',''))):raise ValueError('invalid-private-transaction-journal')
    addresses=value.get('addresses',[])
    if not isinstance(addresses,list) or len(addresses)>16:raise ValueError('bounded-gate-scope-required')
    for row in addresses:
        if (not isinstance(row,dict) or set(row)!={'address','port'} or type(row['port']) is not int or
                row['port'] not in {8000,8004} or not ipaddress.IPv4Address(row['address']).is_private):
            raise ValueError('unreviewed-gate-scope')
    env=value.get('env',{})
    if not isinstance(env,dict) or set(env)-set(ROLES):raise ValueError('unreviewed-env-journal')
    for metadata in env.values():
        if (set(metadata)!={'uid','gid','mode','before_sha256','projected_sha256'} or
                any(type(metadata[k]) is not int for k in ('uid','gid','mode')) or metadata['uid']!=0 or metadata['gid']<0 or
                not 0<=metadata['mode']<=0o777 or metadata['mode']&0o022 or
                any(not re.fullmatch('[0-9a-f]{64}',metadata[k]) for k in ('before_sha256','projected_sha256'))):
            raise ValueError('unreviewed-env-ownership-or-hash')
    if (not re.fullmatch('[0-9a-f]{64}',value.get('plan_sha256','')) or
            set(value.get('images',{}))!=set(ROLES) or set(value.get('runtime_hashes',{}))!=set(ROLES) or
            any(not re.fullmatch('sha256:[0-9a-f]{64}',v) for v in value['images'].values()) or
            any(not re.fullmatch('[0-9a-f]{64}',v) for v in value['runtime_hashes'].values())):
        raise ValueError('qualified-runtime-journal-required')


def model_addresses() -> list[tuple[str,int]]:
    names=set(run(['docker','ps','-a','--format','{{.Names}}']).splitlines());result=[]
    for key,role in ROLES.items():
        if role['container'] not in names:continue
        value=inspect(key)
        if type(value['State'].get('Running')) is not bool:raise ValueError('unknown-model-state')
        if not value['State']['Running']:continue
        for row in value['NetworkSettings']['Networks'].values():
            address=ipaddress.IPv4Address(row['IPAddress'])
            if not address.is_private:raise ValueError('private-model-network-required')
            result.append((str(address),role['port']))
    return sorted(set(result))


class Maintenance:
    def __init__(self,tx: Transaction):
        self.tx=tx;self.chain='HC-SF-'+tx.data['nonce'][:16];self.created=False;self.attached=False;self.stopped=[];self.installed=set();self.cleaned=False
    def iptables(self,*args):return run(['iptables','-w','5',*args],timeout=10)
    def rule(self,address,port):return ['-d',address+'/32','-p','tcp','-m','tcp','--dport',str(port),'-m','conntrack','--ctstate','NEW','-j','DROP']
    def jump(self):return ['-m','comment','--comment','homecompute-finalize-'+self.tx.data['nonce'],'-j',self.chain]
    def refresh(self):
        for address,port in model_addresses():
            if (address,port) in self.installed:continue
            if (address,port) not in self.tx.addresses():
                self.tx.data.setdefault('addresses',[]).append({'address':address,'port':port});self.tx.save()
            self.iptables('-I',self.chain,'1',*self.rule(address,port))
            self.installed.add((address,port))
    def drain(self,timeout=120):
        deadline=time.monotonic()+timeout;quiet=0
        while time.monotonic()<deadline:
            active=[];existing=set(run(['docker','ps','-a','--format','{{.Names}}']).splitlines())
            for key,role in ROLES.items():
                if role['container'] not in existing:continue
                actual=inspect(key);state=actual['State'].get('Running')
                if type(state) is not bool:raise ValueError('unknown-model-state')
                if not state:continue
                observed,budget=runtime_contract(actual,key)
                owner=actual['Config'].get('Labels',{}).get('com.docker.compose.project.config_files')
                known={str(ROOT/role['compose']),str(CAPACITY/self.tx.data['plan_sha256']/(key+'-candidate.json'))}
                if (observed!=self.tx.data['runtime_hashes'][key] or budget!='0.05' or
                        actual['Image']!=self.tx.data['images'][key] or owner not in known):
                    raise ValueError('unknown-running-model-owner-or-contract')
                python='python3' if key=='hviske' else '/opt/plapre/venv/bin/python'
                values=json.loads(run(['docker','exec',role['container'],python,'-c',IDLE_PROBE,str(role['port'])],timeout=10))
                if set(values)!={'connections','running','waiting','listening'} or any(type(v) is not int or v<0 for v in values.values()):raise ValueError('unknown-speech-activity')
                active.extend(values[name] for name in ('connections','running','waiting'))
            quiet=quiet+1 if not any(active) else 0
            if quiet>=3:return
            time.sleep(1)
        raise ValueError('speech-remained-busy-no-replacement-permitted')
    def __enter__(self):
        if Path('/proc/sys/net/bridge/bridge-nf-call-iptables').read_text().strip()!='1':
            raise ValueError('bridge-netfilter-required-before-maintenance')
        if '-N '+self.chain in self.iptables('-S').splitlines():raise ValueError('existing-finalization-gate-requires-recovery')
        if not self.tx.data.get('maintenance_started',False):
            running=set(run(['docker','ps','--format','{{.Names}}']).splitlines())
            self.tx.data['ingress_active']=[name for name in INGRESS if name in running]
            self.tx.data['maintenance_started']=True;self.tx.save()
        try:
            with blocked_signals():self.iptables('-N',self.chain);self.created=True
            self.iptables('-A',self.chain,'-j','RETURN');self.refresh()
            with blocked_signals():self.iptables('-I','DOCKER-USER','1',*self.jump());self.attached=True
            self.drain()
            running=set(run(['docker','ps','--format','{{.Names}}']).splitlines())
            for name in INGRESS:
                if name in running:
                    self.stopped.append(name);run(['docker','stop','--time','30',name],timeout=40)
            return self
        except BaseException:self.__exit__();raise
    def __exit__(self,*_):
        failures=[]
        with blocked_signals():
            # Resume exactly the original set, including recovery after SIGKILL.
            for name in self.tx.data['ingress_active']:
                try:run(['docker','start',name],timeout=60)
                except BaseException:failures.append('ingress')
            if self.attached:
                try:self.iptables('-D','DOCKER-USER',*self.jump());self.attached=False
                except BaseException:failures.append('jump')
            if self.created:
                for operation in ('-F','-X'):
                    try:self.iptables(operation,self.chain)
                    except BaseException:failures.append('chain')
        if failures:raise RuntimeError('maintenance-cleanup-incomplete')
        self.cleaned=True


def canonical_preflight(tx: Transaction, proof: dict) -> dict:
    documents={}
    if '${HVISKE_GPU_MEMORY_UTILIZATION:-0.05}' not in (ROOT/ROLES['hviske']['compose']).read_text():
        raise ValueError('publish-qualified-canonical-defaults-first')
    for key,role in ROLES.items():
        example=(ROOT/role['example']).read_text()
        values=[shlex.split(line.partition('=')[2],comments=True) for line in example.splitlines() if line.startswith(role['setting']+'=')]
        if values!=[['0.05']]:raise ValueError('publish-qualified-canonical-defaults-first')
        path=Path(role['env']);info=protected(path);body=path.read_bytes()
        if info.st_size>2*1024**2:raise ValueError('bounded-production-env-required')
        proof['policy'].activation_approved(key,body.decode())
        projected=patch_budget(body,role['setting'],role['old'])
        atomic(tx.directory/(key+'.env.before'),body)
        atomic(tx.directory/(key+'.env.projected'),projected)
        tx.data.setdefault('env',{})[key]={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode),
                                       'before_sha256':digest(body),'projected_sha256':digest(projected)}
        # Read-only owning validator must accept the future canonical budget.
        run(['/bin/bash',str(ROOT/role['setup']),'validate','--env',str(tx.directory/(key+'.env.projected'))])
        if key=='hviske':
            # Prove the effective default through Compose, rather than trusting
            # a textual marker that could occur only in a source comment.
            fallback=b''.join(line for line in projected.splitlines(keepends=True) if not line.startswith(role['setting'].encode()+b'='))
            atomic(tx.directory/'hviske.env.fallback',fallback)
            defaults=render(key,tx.directory/'hviske.env.fallback')
            values=re.findall(r'--gpu-memory-utilization\s+[\"\x27]?([0-9.]+)',defaults['services'][role['service']]['command'][0])
            if values!=['0.05']:raise ValueError('effective-canonical-default-is-not-qualified')
        documents[key]=render(key,tx.directory/(key+'.env.projected'))
        build=documents[key]['services'][role['service']].get('build')
        if build and build.get('context')!=str(ROOT):raise ValueError('canonical-build-source-owner-mismatch')
        if selected_contract(documents[key],key)!=selected_contract(proof['documents'][key],key):
            raise ValueError('canonical-contract-differs-from-qualified-candidate')
        model_matches(proof['documents'][key],key,proof['runtime_hashes'][key],proof['directory']/(key+'-candidate.json'))
    tx.save();return documents


def start_canonical(key: str):
    role=ROLES[key]
    command=['docker','compose','--env-file',role['env'],'-f',str(ROOT/role['compose'])]
    if key=='hviske':command+=['--profile','hviske']
    if PROGRESS:print('Starting canonical '+key+' with its qualified image and contract.',flush=True)
    run(command+['up','-d','--force-recreate','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','900',role['service']],timeout=960)


def restore_candidate(tx: Transaction, proof: dict, maintenance: Maintenance):
    tx.phase('restoring')
    marker=CAPACITY/'active.json';protected(tx.directory/'active.before.json',private=True)
    saved=(tx.directory/'active.before.json').read_bytes()
    if digest(saved)!=tx.data['active_sha256']:raise ValueError('private-active-backup-changed')
    if marker.exists():
        if marker.read_bytes()!=saved:raise ValueError('active-override-was-replaced')
    else:atomic(marker,saved)
    for key,role in ROLES.items():
        current=Path(role['env']).read_bytes();known=tx.data['env'][key]
        if digest(current) not in {known['before_sha256'],known['projected_sha256']}:
            raise ValueError('production-env-changed-outside-transaction')
        proof['policy'].activation_approved(key,current.decode())
    backups={}
    for key,role in ROLES.items():
        body=(tx.directory/(key+'.env.before')).read_bytes()
        if digest(body)!=tx.data['env'][key]['before_sha256']:raise ValueError('private-env-backup-changed')
        backups[key]=body
    failures=[]
    for key,role in ROLES.items():
        try:atomic(Path(role['env']),backups[key],metadata=tx.data['env'][key])
        except Exception:failures.append('env-'+key)
    for key,role in ROLES.items():
        try:
            try:
                model_matches(proof['documents'][key],key,proof['runtime_hashes'][key],proof['directory']/(key+'-candidate.json'))
            except Exception:
                run(['docker','compose','-f',str(proof['directory']/(key+'-candidate.json')),'up','-d','--force-recreate','--no-deps','--no-build',
                     '--pull','never','--wait','--wait-timeout','900',role['service']],timeout=960)
                maintenance.refresh()
                model_matches(proof['documents'][key],key,proof['runtime_hashes'][key],proof['directory']/(key+'-candidate.json'))
        except Exception:failures.append('model-'+key)
    if failures:raise RuntimeError('qualified-candidate-restoration-incomplete')
    tx.phase('candidate-restored')


def recover_gate(tx: Transaction):
    """Remove only this journal's exact restrictive gate after a hard exit."""
    if not re.fullmatch('[0-9a-f]{32}',tx.data.get('nonce','')):raise ValueError('invalid-transaction-nonce')
    maintenance=Maintenance(tx)
    all_rules=maintenance.iptables('-S').splitlines()
    if '-N '+maintenance.chain not in all_rules:return
    allowed={tuple(['-A',maintenance.chain,'-j','RETURN'])}
    if len(tx.data.get('addresses',[]))>16:raise ValueError('bounded-gate-scope-required')
    for address,port in tx.addresses():
        if not ipaddress.IPv4Address(address).is_private or port not in {8000,8004}:raise ValueError('unreviewed-gate-scope')
        allowed.add(tuple(['-A',maintenance.chain,*maintenance.rule(address,port)]))
    rules=[shlex.split(line) for line in all_rules]
    owned=[row for row in rules if len(row)>1 and row[0]=='-A' and row[1]==maintenance.chain]
    references=[row for row in rules if '-j' in row and row[row.index('-j')+1]==maintenance.chain]
    expected=['-A','DOCKER-USER',*maintenance.jump()]
    if any(tuple(row) not in allowed for row in owned) or references not in ([],[expected]):
        raise ValueError('unrecognized-finalization-gate-refused')
    with blocked_signals():
        if references:maintenance.iptables('-D','DOCKER-USER',*maintenance.jump())
        maintenance.iptables('-F',maintenance.chain);maintenance.iptables('-X',maintenance.chain)


def recover(tx: Transaction, proof: dict) -> dict:
    validate_journal(tx.data)
    if tx.data['phase']=='completed':raise ValueError('completed-canonical-release-is-not-a-pending-recovery')
    recover_gate(tx)
    if set(tx.data.get('env',{}))!=set(ROLES):
        # Preflight is the only phase before both backups are journaled; no
        # production file or model has been changed at this point.
        if tx.data['phase']!='preparing':raise ValueError('incomplete-private-env-journal')
        for key in ROLES:model_matches(proof['documents'][key],key,proof['runtime_hashes'][key],proof['directory']/(key+'-candidate.json'))
        tx.phase('candidate-unchanged')
    else:
        with Maintenance(tx) as maintenance:restore_candidate(tx,proof,maintenance)
    return {'state':tx.data['phase'],'source_revision':ROOT.name,'capacity_qualified':True,
            'override_active':True,'accepted':False,'full_voice_accepted':False,'transaction':str(tx.directory)}


def completed_status(tx: Transaction, proof: dict) -> dict:
    if (CAPACITY/'active.json').exists() or digest((tx.directory/'active.retired.json').read_bytes())!=tx.data['active_sha256']:
        raise ValueError('canonical-retirement-state-changed')
    for key,role in ROLES.items():
        path=Path(role['env']);protected(path)
        if digest(path.read_bytes())!=tx.data['env'][key]['projected_sha256']:raise ValueError('canonical-env-changed')
        proof['policy'].activation_approved(key,path.read_text())
        document=render(key,path)
        if selected_contract(document,key)!=selected_contract(proof['documents'][key],key):raise ValueError('canonical-contract-changed')
        model_matches(document,key,proof['runtime_hashes'][key],ROOT/role['compose'])
    return {'state':'canonical-capacity','source_revision':ROOT.name,'capacity_qualified':True,
            'override_active':False,'accepted':False,'full_voice_accepted':False,'already_completed':True,'transaction':str(tx.directory)}


def report_metadata(tx: Transaction, value: dict) -> dict:
    return {**value,'qualification_source_revision':QUALIFICATION_REVISION,
        'qualification_receipt_sha256':tx.data['qualification_sha256'],'plan_sha256':tx.data['plan_sha256'],
        'target_budgets':{'hviske':'0.05','plapre':'0.05'},
        'private_receipt':{'path':str(tx.directory/'journal.json'),
                           'sha256':digest((tx.directory/'journal.json').read_bytes())}}


def finalize(tx: Transaction, proof: dict, documents: dict) -> dict:
    touched=False;maintenance=None
    try:
        with Maintenance(tx) as maintenance:
            try:
                tx.phase('env-updating')
                for key,role in ROLES.items():
                    path=Path(role['env'])
                    if digest(path.read_bytes())!=tx.data['env'][key]['before_sha256']:raise ValueError('production-env-changed-during-preflight')
                    touched=True
                    atomic(path,(tx.directory/(key+'.env.projected')).read_bytes(),metadata=tx.data['env'][key])
                tx.phase('canonical-starting')
                for key,role in ROLES.items():
                    start_canonical(key);maintenance.refresh()
                    model_matches(documents[key],key,proof['runtime_hashes'][key],ROOT/role['compose'])
                    if render(key,Path(role['env']))!=documents[key]:raise ValueError('installed-canonical-render-changed')
                # The first engine may have failed while the second loaded.
                # Retire only after a fresh check of both canonical owners.
                for key,role in ROLES.items():
                    model_matches(documents[key],key,proof['runtime_hashes'][key],ROOT/role['compose'])
                    if digest(Path(role['env']).read_bytes())!=tx.data['env'][key]['projected_sha256']:
                        raise ValueError('production-env-changed-before-retirement')
                tx.phase('canonical-ready')
                with blocked_signals():
                    if digest((CAPACITY/'active.json').read_bytes())!=tx.data['active_sha256']:raise ValueError('active-override-was-replaced')
                    os.link(CAPACITY/'active.json',tx.directory/'active.retired.json')
                    (CAPACITY/'active.json').unlink()
                    tx.phase('marker-retired')
            except BaseException:
                if touched:restore_candidate(tx,proof,maintenance)
                raise
        tx.phase('completed')
        return {'state':'canonical-capacity','source_revision':ROOT.name,'capacity_qualified':True,
                'override_active':False,'accepted':False,'full_voice_accepted':False,'transaction':str(tx.directory)}
    except BaseException:
        # Cleanup can fail after the primary body succeeded. Never clear the
        # override while intake restoration or final receipt is uncertain.
        if touched and (tx.data.get('phase')!='candidate-restored' or maintenance is None or not maintenance.cleaned):
            try:
                marker=CAPACITY/'active.json'
                if not marker.exists():atomic(marker,(tx.directory/'active.before.json').read_bytes())
                with Maintenance(tx) as maintenance:restore_candidate(tx,proof,maintenance)
            except BaseException:tx.phase('recovery-required')
        raise


def abort_completed_transaction(tx: Transaction, proof: dict):
    """Reporting failure is also fail-closed; never force an active request."""
    try:
        saved=(tx.directory/'active.before.json').read_bytes()
        if digest(saved)!=tx.data['active_sha256']:raise ValueError('private-active-backup-changed')
        marker=CAPACITY/'active.json'
        if not marker.exists():atomic(marker,saved)
        recover_gate(tx)
        with Maintenance(tx) as maintenance:restore_candidate(tx,proof,maintenance)
    except BaseException:tx.phase('recovery-required')


def report(path: Path, value: dict):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(descriptor,'w') as stream:
        os.fchmod(stream.fileno(),0o644);json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())


def main():
    global PROGRESS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True);parser.add_argument('--trial',required=True)
    parser.add_argument('--qualification-sha256',required=True);parser.add_argument('--staging-directory',required=True,type=Path)
    parser.add_argument('--recover',action='store_true',help='Restore the qualified candidate and original env from this exact pending transaction')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark' or not sys.flags.isolated:
        raise ValueError('isolated-trusted-home-spark-root-required')
    published(ROOT,args.revision)
    PROGRESS=True
    sys.path.insert(0,str(ROOT/'scripts'))
    for key in list(os.environ):
        if key.startswith('PYTHON'):os.environ.pop(key)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    if not re.fullmatch('[0-9a-f]{64}',args.qualification_sha256):raise ValueError('exact-reviewed-qualification-hash-required')
    staging=args.staging_directory
    if (not re.fullmatch(r'/tmp/homecompute-speech-finalization\.[A-Za-z0-9]+',str(staging)) or staging.is_symlink() or
            not staging.is_dir() or stat.S_IMODE(staging.stat().st_mode)!=0o700 or staging.stat().st_uid!=int(os.environ.get('SUDO_UID','0'))):
        raise ValueError('private-operator-staging-directory-required')
    target=staging/'finalization-result.json'
    if target.exists() or target.is_symlink():raise ValueError('fresh-result-path-required')
    os.umask(0o077)
    def interrupted(*_):raise InterruptedError('operator-interrupted')
    for event in SIGNALS:signal.signal(event,interrupted)
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True);protected(STATE,private=True,directory=True)
    tx=None
    with ExitStack() as stack:
        for path in (Path('/var/lib/homecompute/home-spark-deploy.lock'),TRIALS/'lock',CAPACITY/'lock'):
            protected(path.parent,directory=True);lock=stack.enter_context(path.open('a'));fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directory=STATE/(args.qualification_sha256+'-'+args.revision)
        already=False
        if directory.exists() or directory.is_symlink():
            protected(directory,private=True,directory=True);tx=Transaction(directory,read_json(directory/'journal.json'))
            validate_journal(tx.data)
            if any(tx.data.get(key)!=value for key,value in {'revision':args.revision,'trial':args.trial,'qualification_sha256':args.qualification_sha256}.items()):
                raise ValueError('pending-transaction-identity-mismatch')
            protected(directory/'active.before.json',private=True);saved=(directory/'active.before.json').read_bytes()
            if digest(saved)!=tx.data['active_sha256']:raise ValueError('private-active-backup-changed')
            if tx.data['phase']=='completed':
                if args.recover:raise ValueError('completed-canonical-release-is-not-a-pending-recovery')
                proof=evidence(TRIALS/args.trial,args.qualification_sha256,active_bytes=saved);already=True
            elif args.recover:
                if not (CAPACITY/'active.json').exists():
                    if not (directory/'active.retired.json').exists() or (directory/'active.retired.json').read_bytes()!=saved:
                        raise ValueError('pending-retirement-proof-required')
                    atomic(CAPACITY/'active.json',saved)
                proof=evidence(TRIALS/args.trial,args.qualification_sha256)
            else:
                if tx.data['phase'] not in {'candidate-restored','candidate-unchanged'}:
                    raise ValueError('recover-pending-transaction-before-retrying')
                proof=evidence(TRIALS/args.trial,args.qualification_sha256)
                if (CAPACITY/'active.json').read_bytes()!=saved:
                    raise ValueError('pending-retirement-proof-required')
                for key,metadata in tx.data.get('env',{}).items():
                    if digest(Path(ROLES[key]['env']).read_bytes())!=metadata['before_sha256']:
                        raise ValueError('recovered-production-env-changed')
                atomic(directory/('attempt-'+uuid.uuid4().hex+'.json'),json.dumps(tx.data,sort_keys=True).encode())
                tx.data.update(phase='preparing',nonce=uuid.uuid4().hex,addresses=[],env={},maintenance_started=False)
                tx.data['ingress_active']=[name for name in INGRESS if name in set(run(['docker','ps','--format','{{.Names}}']).splitlines())]
                (directory/'active.retired.json').unlink(missing_ok=True);tx.save()
        else:
            if args.recover:raise ValueError('pending-transaction-required')
            proof=evidence(TRIALS/args.trial,args.qualification_sha256)
            directory.mkdir(mode=0o700)
            active=(CAPACITY/'active.json').read_bytes();atomic(directory/'active.before.json',active)
            running=set(run(['docker','ps','--format','{{.Names}}']).splitlines())
            tx=Transaction(directory,{'phase':'preparing','revision':args.revision,'trial':args.trial,
                'qualification_sha256':args.qualification_sha256,'active_sha256':digest(active),'nonce':uuid.uuid4().hex,
                'plan_sha256':proof['plan']['plan_sha256'],'runtime_hashes':proof['runtime_hashes'],
                'images':{key:value['services'][ROLES[key]['service']]['image'] for key,value in proof['documents'].items()},
                'ingress_active':[name for name in INGRESS if name in running]});tx.save()
        try:
            if already:result=completed_status(tx,proof)
            elif args.recover:result=recover(tx,proof)
            else:
                documents=canonical_preflight(tx,proof);result=finalize(tx,proof,documents)
            result=report_metadata(tx,result);report(target,result);print(json.dumps(result,sort_keys=True))
        except BaseException as error:
            if not already and tx.data.get('phase')=='completed':abort_completed_transaction(tx,proof)
            outcome={'state':tx.data['phase'],'source_revision':args.revision,'capacity_qualified':True,
                     'override_active':(CAPACITY/'active.json').exists(),'accepted':False,'full_voice_accepted':False,
                     'error_category':type(error).__name__,'transaction':str(tx.directory)}
            if not target.exists() and not target.is_symlink():report(target,report_metadata(tx,outcome))
            raise


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'state':'not-finalized','error_category':type(error).__name__,'accepted':False}),file=sys.stderr)
        raise SystemExit(1) from None
