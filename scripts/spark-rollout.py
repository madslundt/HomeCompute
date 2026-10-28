#!/usr/bin/env python3
"""One supervised Spark speech capacity trial with fail-closed rollback receipts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute-spark-rollout')
CAPACITY_STATE = Path('/var/lib/homecompute-speech-capacity/active.json')
INGRESS = ('gb10-hviske-http', 'gb10-hviske-wyoming',
           'homecompute-plapre-plapre-http-1', 'homecompute-plapre-plapre-edge-1',
           'homecompute-plapre-plapre-wyoming-1')
MODELS = {'gb10-hviske-primary': 8000, 'homecompute-plapre-plapre-primary-1': 8004}
CHAIN = 'HC-SPEECH-ROLLOUT'
IDLE_PROBE = r'''
import json,re,urllib.request
from pathlib import Path
port=int(__import__('sys').argv[1]); result={'connections':0,'running':0,'waiting':0}
if port==8000:
 with urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=5) as r: metrics=r.read(4000000).decode()
 samples=re.findall(r'^vllm:num_requests_(running|waiting)(?:\{[^}]*\})?\s+([0-9.e+]+)$',metrics,re.M)
 if {x for x,_ in samples}!={'running','waiting'}: raise ValueError('unknown_asr_activity')
 for kind,value in samples:
  number=float(value)
  if number<0 or not number.is_integer(): raise ValueError('invalid_asr_activity')
  result[kind]+=int(number)
for path in ['/proc/net/tcp','/proc/net/tcp6']:
 for line in Path(path).read_text().splitlines()[1:]:
  row=line.split()
  if int(row[1].rsplit(':',1)[1],16)==port and row[3] in {'01','08'}: result['connections']+=1
print(json.dumps(result))
'''


def run(arguments: list[str], *, timeout=240, missing=False) -> str | None:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        if missing: return None
        operation=Path(arguments[1]).name if Path(arguments[0]).name.startswith('python') and len(arguments)>1 else ' '.join(arguments[:2])
        raise RuntimeError('Operation failed: '+operation+' (exit '+str(result.returncode)+')')
    return result.stdout.strip()


def helper(name: str, *arguments: str, timeout=240) -> dict:
    output = run([sys.executable, str(ROOT / 'scripts' / name), *arguments], timeout=timeout)
    return json.loads(output)


def receipt(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush(); os.fsync(stream.fileno())


def pinned_corpus(archive: Path, destination: Path) -> dict:
    pin = json.loads((ROOT / 'config/spark-rollout-corpus.json').read_text())
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        if os.fstat(source.fileno()).st_size != pin['archive_size_bytes']:
            raise ValueError('Public corpus archive size differs')
        snapshot=source.read(pin['archive_size_bytes']+1)
        if hashlib.sha256(snapshot).hexdigest() != pin['archive_sha256']:
            raise ValueError('Public corpus archive hash differs')
        # Parse the verified bounded snapshot, not a user-writable inode that
        # could change between hashing and extraction after sudo elevation.
        with tarfile.open(fileobj=io.BytesIO(snapshot)) as package:
            member = package.getmember(pin['manifest_path'])
            if not member.isfile() or member.size > 1000000:
                raise ValueError('Invalid public manifest')
            raw = package.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != pin['manifest_sha256']:
                raise ValueError('Public manifest hash differs')
            manifest = json.loads(raw)
            rows = [r for r in manifest['fixtures'] if r['language']=='da' and r['condition'] in pin['conditions']]
            if (manifest['revision'] != pin['revision'] or len(rows) != pin['danish_fixtures'] or
                    {kind:sum(r['condition']==kind for r in rows) for kind in pin['conditions']} != pin['conditions']):
                raise ValueError('Unexpected public corpus selection')
            destination.mkdir(mode=0o700)
            for row in rows:
                name = row['file']
                if Path(name).name != name: raise ValueError('Unsafe public audio path')
                member = package.getmember('corpus/' + name)
                if not member.isfile() or not 0 < member.size <= 10000000:
                    raise ValueError('Unsafe public audio archive member')
                body = package.extractfile(member).read()
                if hashlib.sha256(body).hexdigest() != row['sha256']:
                    raise ValueError('Public audio hash differs')
                target = destination / name
                with target.open('xb') as output: output.write(body)
                target.chmod(0o600)
            receipt(destination / 'manifest.json', {**manifest, 'fixtures': rows})
    return {'archive_sha256': pin['archive_sha256'], 'revision': pin['revision'], 'danish_fixtures': len(rows)}


def idle(timeout=60) -> None:
    deadline, quiet = time.monotonic()+timeout, 0
    while time.monotonic() < deadline:
        values = []
        for name, port in MODELS.items():
            python = 'python3' if port==8000 else '/opt/plapre/venv/bin/python'
            value = json.loads(run(['docker','exec',name,python,'-c',IDLE_PROBE,str(port)], timeout=10))
            if set(value) != {'connections','running','waiting'} or any(type(v) is not int or v<0 for v in value.values()):
                raise ValueError('Unknown speech activity')
            values.extend(value.values())
        quiet = quiet+1 if not any(values) else 0
        if quiet >= 3: return
        time.sleep(1)
    raise ValueError('Speech did not drain; no engine replacement is permitted')


@contextmanager
def paused_ingress():
    # Reject NEW model-bound connections while existing requests finish; the
    # model-local benchmark uses container loopback and cannot cross this gate.
    addresses = []
    for name, port in MODELS.items():
        networks = json.loads(run(['docker','inspect',name,'--format','{{json .NetworkSettings.Networks}}']))
        for network in networks.values():
            address = ipaddress.IPv4Address(network['IPAddress'])
            if not address.is_private: raise ValueError('Unexpected speech inference network')
            addresses.append((str(address),port))
    if run(['iptables','-w','-L',CHAIN,'-n'], missing=True) is not None:
        raise ValueError('A prior speech rollout gate exists; inspect its recovery receipt')
    existing=set(run(['docker','ps','-a','--format','{{.Names}}']).splitlines())
    active=[]
    for name in INGRESS:
        if name not in existing: continue
        running=run(['docker','inspect',name,'--format','{{.State.Running}}'])
        if running not in {'true','false'}: raise ValueError('Unknown speech ingress state')
        if running=='true': active.append(name)
    stopped, attached, created = [], False, False
    try:
        run(['iptables','-w','-N',CHAIN]); created=True
        for address,port in addresses:
            run(['iptables','-w','-A',CHAIN,'-d',address+'/32','-p','tcp','--dport',str(port),
                 '-m','conntrack','--ctstate','NEW','-j','REJECT'])
        run(['iptables','-w','-A',CHAIN,'-j','RETURN'])
        run(['iptables','-w','-I','DOCKER-USER','1','-j',CHAIN]); attached=True
        idle()
        for name in active:
            stopped.append(name)
            run(['docker','stop','--time','30',name],timeout=40)
        yield active
    finally:
        failures=[]
        for name in stopped:
            try: run(['docker','start',name],timeout=60)
            except Exception: failures.append(name)
        if attached: run(['iptables','-w','-D','DOCKER-USER','-j',CHAIN])
        if created:
            run(['iptables','-w','-F',CHAIN]); run(['iptables','-w','-X',CHAIN])
        if failures: raise RuntimeError('Speech ingress could not resume: '+','.join(failures))


class SyntheticLoad:
    def __init__(self):
        self.stop_event=threading.Event(); self.connections=[]; self.sockets=[]; self.lock=threading.Lock(); self.completed=0; self.accepted=[0,0]; self.errors=0
    def __enter__(self):
        key=Path('/etc/gb10-ai/secrets/vllm_api_key').read_text().strip()
        if not key or '\n' in key or '\r' in key: raise ValueError('Invalid local inference credential')
        self.deadline=time.monotonic()+600
        def worker(index):
            self.stop_event.wait(index*.5)
            while not self.stop_event.is_set() and time.monotonic()<self.deadline:
                c=http.client.HTTPConnection('127.0.0.1',18300,timeout=120)
                with self.lock: self.connections.append(c)
                wire=None
                try:
                    c.connect(); wire=c.sock
                    with self.lock: self.sockets.append(wire)
                    if self.stop_event.is_set(): break
                    body=json.dumps({'model':'automation-qualification','stream':True,'max_tokens':4096,
                        'temperature':0,'messages':[{'role':'user','content':
                        'Synthetic capacity test. List the integers from 1 onward, one per line, until the token budget ends.'}]})
                    c.request('POST','/v1/chat/completions',body,{'Content-Type':'application/json','Authorization':'Bearer '+key})
                    response=c.getresponse()
                    if response.status!=200: raise ValueError('Local synthetic load refused')
                    with self.lock: self.accepted[index]+=1
                    while not self.stop_event.is_set() and time.monotonic()<self.deadline and response.read(4096): pass
                    self.completed+=1
                except Exception:
                    if not self.stop_event.is_set():
                        with self.lock: self.errors+=1
                    self.stop_event.wait(.25)
                finally:
                    c.close()
                    with self.lock:
                        self.connections.remove(c)
                        if wire is not None: self.sockets.remove(wire)
        self.threads=[threading.Thread(target=worker,args=(i,),daemon=True) for i in range(2)]
        try:
            for thread in self.threads: thread.start()
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if self.stop_event.is_set(): raise InterruptedError('Synthetic load startup canceled')
                with self.lock: accepted=all(self.accepted)
                if accepted: break
                self.stop_event.wait(.1)
            else: raise ValueError('Both controlled synthetic LLM streams must be accepted')
        except BaseException:
            self.__exit__(); raise
        return self
    def __exit__(self,*_):
        self.stop_event.set()
        with self.lock:
            # Keep the socket object even when HTTPConnection hands a
            # close-delimited response to HTTPResponse and clears c.sock.
            for wire in self.sockets:
                try: wire.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            for c in self.connections: c.close()
        for thread in self.threads:
            if thread.ident is not None: thread.join(12)


def benchmark(label: str, directory: Path) -> dict:
    output=directory/(label+'.json')
    command=[sys.executable,str(ROOT/'benchmarks/speech_capacity_benchmark.py'),'--corpus',str(directory/'corpus'),
             '--label',label,'--output',str(output)]
    if label=='candidate': command+=['--baseline',str(directory/'baseline.json')]
    print('Running '+label+' benchmark: 18 Danish ASR clips, 20 TTS trials, two bounded synthetic LLM streams.',flush=True)
    load=SyntheticLoad()
    try:
        with load: run(command,timeout=600)
    finally:
        receipt(directory/('load-'+label+'.json'),{'streams':2,'maximum_output_tokens_per_request':4096,
            'maximum_seconds':600,'completed_requests':load.completed,'accepted_requests_per_worker':load.accepted,
            'failed_requests':load.errors,'outputs_retained':False})
    if load.errors or not all(load.accepted): raise ValueError('Controlled synthetic LLM load failed')
    return json.loads(output.read_text())


def confirm_plan(plan: dict, timeout=120) -> bool:
    print(json.dumps(plan,sort_keys=True,indent=2),flush=True)
    print('Type APPLY to replace only the two speech engines with this exact plan (120s timeout): ',end='',flush=True)
    ready,_,_=select.select([sys.stdin],[],[],timeout)
    return bool(ready) and sys.stdin.readline().strip()=='APPLY'


def trial(directory: Path, confirmer=confirm_plan, measure=benchmark) -> dict:
    result={'source_revision':ROOT.name,'accepted':False,'full_voice_accepted':False,
            'synthetic_llm_streams':2,'state':'baseline','capacity_qualified':False}
    attempted=False
    try:
        baseline=measure('baseline',directory)
        if (baseline.get('asr',{}).get('failures') != 0 or baseline.get('asr',{}).get('trials') != 18 or
                baseline.get('tts_model_local',{}).get('failures') != 0 or baseline.get('tts_model_local',{}).get('trials',0) < 20):
            raise ValueError('Complete error-free baseline evidence is required before replacement')
        plan=helper('qualify-speech-capacity.py','plan')
        receipt(directory/'plan.json',plan)
        result['plan_sha256']=plan['plan_sha256']
        if not confirmer(plan): raise ValueError('Plan was not approved before timeout')
        idle(); attempted=True
        print('Applying reviewed capacity plan; only two speech engines restart. Health waits are bounded.',flush=True)
        helper('qualify-speech-capacity.py','apply','--plan-sha256',plan['plan_sha256'],'--speech-idle',timeout=2000)
        candidate=measure('candidate',directory)
        # The benchmark owns all gates; never turn truthy strings into acceptance.
        if candidate.get('capacity_qualified') is not True:
            raise ValueError('The measured capacity gates did not pass')
        result.update(state='capacity-candidate',capacity_qualified=True,
            asr_latency_pass=candidate.get('asr_latency_pass') is True,
            tts_model_local_latency_pass=candidate.get('tts_model_local_latency_pass') is True,
            reconciliation_required=True)
        return result
    except BaseException as error:
        result['error']=type(error).__name__
        if attempted and CAPACITY_STATE.exists():
            try:
                idle(); helper('qualify-speech-capacity.py','rollback','--speech-idle',timeout=2000)
                result['state']='baseline-restored'
            except BaseException:
                result['state']='rollback-incomplete'
        raise
    finally:
        receipt(directory/'result.json',result)


def staging_result(archive: Path) -> Path:
    directory=archive.absolute().parent
    if (not re.fullmatch(r'/tmp/homecompute-spark-rollout\.[A-Za-z0-9]+',str(directory)) or
            directory.is_symlink() or directory.stat().st_mode&0o077 or
            directory.stat().st_uid!=int(os.environ.get('SUDO_UID',str(os.getuid())))):
        raise ValueError('A private reviewed rollout staging directory is required')
    target=directory/'rollout-result.json'
    if target.exists() or target.is_symlink():
        raise ValueError('Use a fresh staging directory; never overwrite a prior result')
    return target


def public_result(directory: Path | None, revision: str, state: str) -> dict:
    report={'schema_version':1,'source_revision':revision,'state':state,
            'accepted':False,'full_voice_accepted':False,'baseline':None,'candidate':None,
            'rollback_state':'restored' if state=='baseline-restored' else
                'incomplete' if state=='rollback-incomplete' else 'not-required',
            'private_receipt':None}
    if directory is None: return report
    for label in ['baseline','candidate']:
        path=directory/(label+'.json')
        if not path.is_file(): continue
        data=json.loads(path.read_text())
        fields={name:data.get(name) if type(data.get(name)) is bool else None for name in
                ['capacity_qualified','asr_latency_pass','tts_model_local_latency_pass','asr_quality_pass','latency_regression_pass']}
        capacity=data.get('capacity',{})
        fields['headroom_pass']=capacity.get('passed') if type(capacity.get('passed')) is bool else None
        ratio=capacity.get('minimum_available_ratio')
        fields['minimum_available_ratio']=ratio if type(ratio) in {int,float} and 0<=ratio<=1 else None
        budgets=data.get('runtime',{}).get('budgets',{})
        fields['budgets']={name:budgets.get(name) if budgets.get(name) in {'0.10','0.06','0.05'} else None
                          for name in ['hviske','plapre']}
        load_path=directory/('load-'+label+'.json')
        fields['controlled_llm_load_pass']=None
        if load_path.is_file():
            load=json.loads(load_path.read_text()); accepted=load.get('accepted_requests_per_worker')
            fields['controlled_llm_load_pass']=(load.get('streams')==2 and type(load.get('failed_requests')) is int
                and load['failed_requests']==0 and isinstance(accepted,list) and len(accepted)==2
                and all(type(n) is int and n>0 for n in accepted))
        report[label]=fields
    for name in ['orchestration-failure.json','result.json']:
        path=directory/name
        if path.is_file():
            report['private_receipt']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            break
    return report


def publish_result(target: Path, data: dict) -> None:
    # Parent SSH owns the enclosing 0700 staging directory; the finite report
    # alone is root-owned/readable. Never expose the private plans or env files.
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,'w') as stream:
        os.fchmod(stream.fileno(),0o644)
        json.dump(data,stream,sort_keys=True,indent=2)
        stream.flush(); os.fsync(stream.fileno())


def release(revision: str) -> None:
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='home-spark':
        raise ValueError('Run with sudo on home-spark')
    if not re.fullmatch(r'[0-9a-f]{40}',revision) or ROOT!=Path('/srv/homecompute/releases')/revision:
        raise ValueError('Use the exact published immutable release')
    spec=importlib.util.spec_from_file_location('capacity_setup',ROOT/'scripts/qualify-speech-capacity.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if module.release()!=revision: raise ValueError('Release mismatch')
    run(['git','-C',str(ROOT),'merge-base','--is-ancestor',revision,'origin/master'])


def interrupted(*_) -> None:
    raise InterruptedError('Rollout interrupted')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True); parser.add_argument('--corpus-archive',type=Path,required=True)
    parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--record-declined',action='store_true')
    args=parser.parse_args(); release(args.revision)
    shared=staging_result(args.corpus_archive)
    if args.record_declined:
        publish_result(shared,public_result(None,args.revision,'declined')); return
    if CAPACITY_STATE.exists(): raise ValueError('An earlier capacity override needs review or rollback first')
    if args.preflight_only:
        pin=json.loads((ROOT/'config/spark-rollout-corpus.json').read_text())
        with args.corpus_archive.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=pin['archive_sha256']: raise ValueError('Corpus hash differs')
        print(json.dumps({'source_revision':args.revision,'corpus_sha256':pin['archive_sha256'],'accepted':False})); return
    for event in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
        signal.signal(event,interrupted)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if STATE.is_symlink() or STATE.stat().st_uid!=0 or STATE.stat().st_mode&0o077: raise ValueError('Unsafe rollout state')
    with (STATE/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directory=Path(tempfile.mkdtemp(prefix='trial-',dir=STATE)); directory.chmod(0o700)
        try:
            metadata=pinned_corpus(args.corpus_archive,directory/'corpus'); receipt(directory/'corpus-pin.json',metadata)
            run([sys.executable,str(ROOT/'scripts/setup-speech-boundary.py'),'spark-prepare','--revision',args.revision],timeout=300)
            with paused_ingress(): result=trial(directory)
            print(json.dumps(result,sort_keys=True),flush=True)
            print('Rollout receipts: '+str(directory),flush=True)
            publish_result(shared,public_result(directory,args.revision,result['state']))
        except BaseException as error:
            outcome={'state':'failed-before-candidate','error':type(error).__name__,
                     'accepted':False,'full_voice_accepted':False}
            if CAPACITY_STATE.exists():
                try:
                    with paused_ingress():
                        idle(); helper('qualify-speech-capacity.py','rollback','--speech-idle',timeout=2000)
                    outcome['state']='baseline-restored'
                except BaseException: outcome['state']='rollback-incomplete'
            elif (directory/'result.json').exists():
                prior=json.loads((directory/'result.json').read_text())
                if prior.get('state')=='baseline-restored': outcome['state']='baseline-restored'
            receipt(directory/'orchestration-failure.json',outcome)
            # If publication itself failed (e.g. disk error), leave its existing
            # file untouched; the SSH reader treats malformed/missing metadata
            # as unverified and the private recovery receipt remains authoritative.
            if not shared.exists() and not shared.is_symlink():
                publish_result(shared,public_result(directory,args.revision,outcome['state']))
            raise



if __name__=='__main__': main()
