#!/usr/bin/env python3
"""Ephemeral combined Whisper diagnostic lifecycle; no production routing actions."""
from __future__ import annotations
import argparse,base64,contextlib,fcntl,hashlib,json,os,re,secrets,signal,stat,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state'
GIB=1024**3
LIMIT=6*GIB
OWNER_LABEL='io.homecompute.whisper-decoder-audit.owner'
MODEL_PATH='/srv/gb10-ai/cache/whisper-cpp/ggml-large-v3.bin'
TRACE_PATH='/tmp/whisper-window-trace.jsonl'
RUNTIME='f049fff95a089aa9969deb009cdd4892b3e74916'
WEIGHTS='64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2'
PATCH='strict-gpu-local-eot-ranking-v8'
HEALTH_PROGRAM="import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3))))"


OBSERVER_FILES={'manifest.json':1000000,'observer.ts':134217728,'parity.json':1000000}
OBSERVER_CODE=('common.py','graph.py','export.py','classify.py','installer_check.py',
               'model.json','dependencies.json','requirements.lock')
OBSERVER_PROBE_SHA256='74c2e6420691f80a292e5055eea8fa0cb31a1bd81dea84c7f7a0caa06568477d'

def observer_pins(value):
    observer=value.get('observer')
    if not isinstance(observer,dict) or set(observer)!={'directory','files','bundle_pin','confirmation_prescreen_sha256'}:
        raise Refused('Complete observer artifact pins required')
    directory=observer['directory']
    if (not isinstance(directory,str) or not directory.startswith('/') or directory=='/'
        or not re.fullmatch(r'/[A-Za-z0-9_./-]+',directory) or any(x in ('','..','.') for x in directory.split('/')[1:])
        or directory==MODEL_PATH):raise Refused('Direct approved observer directory required')
    if set(observer['files'])!=set(OBSERVER_FILES):raise Refused('Exact public observer file set required')
    for name,item in [*observer['files'].items(),('bundle_pin',observer['bundle_pin'])]:
        maximum=20000 if name=='bundle_pin' else OBSERVER_FILES[name]
        if (not isinstance(item,dict) or set(item)!={'sha256','bytes'} or type(item['bytes']) is not int
            or not 0<item['bytes']<=maximum or not re.fullmatch('[0-9a-f]{64}',item.get('sha256',''))):
            raise Refused('Bounded observer file identity required')
    if not re.fullmatch('[0-9a-f]{64}',observer.get('confirmation_prescreen_sha256','')):raise Refused('Frozen cut confirmation prescreen required')
    return observer

def observer_probe_source():
    fd=os.open(Path(__file__).with_name('whisper_observer_artifact_probe.py'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=32000:raise Refused('Unsafe observer probe source')
        raw=stream.read(32001)
    if len(raw)!=info.st_size or hashlib.sha256(raw).hexdigest()!=OBSERVER_PROBE_SHA256:raise Refused('Observer probe source changed')
    return raw.decode('utf-8')

def verify_observer_artifacts(record):
    observer=observer_pins(record);owned_container(record);probe=observer_probe_source()
    result=json.loads(command(['docker','exec','--user','10001:10001',record['container_id'],
                              'python3','-I','-B','-c',probe,json.dumps(observer)],timeout=30))
    if (result.get('verified') is not True or result.get('model_deserialized') is not False
        or result.get('public_synthetic_artifacts_only') is not True
        or result.get('file_sha256')!={n:v['sha256'] for n,v in observer['files'].items()}
        or result.get('bundle_pin_sha256')!=observer['bundle_pin']['sha256']):
        raise Refused('Mounted observer artifacts differ')
    return result


class Refused(RuntimeError): pass


class MonitorUnavailable(Refused): pass


class MemoryFloor(Refused):
    def __init__(self, sample):
        super().__init__("Host memory floor observed")
        self.sample=sample

def command(args, *, timeout=30, log=None, env=None):
    try:
        result = subprocess.run(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=log or subprocess.PIPE,
                                stderr=log or subprocess.PIPE, timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Command failed or timed out; no automatic destructive recovery') from None
    if result.returncode:
        raise Refused('Command refused; inspect the private lifecycle receipt')
    return '' if log else result.stdout.decode().strip()

def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused('State directory must be owned by this user with mode 0700')

def write_json(path, value, *, replace=False):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6)) if replace else path
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)

def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_size > 128000):
            raise Refused('Unsafe lifecycle journal')
        return json.load(stream)


@contextlib.contextmanager
def lifecycle_lock():
    fd = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def same_trial(first, second):
    return all(first.get(key) == second.get(key) for key in ('owner', 'name', 'container_id', 'image_id'))

def remove_active(record):
    path = STATE / 'active.json'
    if path.exists():
        if not same_trial(record, read_json(path)):
            raise Refused('Active journal changed; refusing to remove another trial')
        path.unlink()

def memory(*, loading=False):
    try:
        fields = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB$',
                                 Path('/proc/meminfo').read_text(), re.M))
        total, available = (int(fields[key]) * 1024 for key in ('MemTotal', 'MemAvailable'))
        if not 0 <= available <= total or total <= 0:
            raise ValueError
    except (OSError, KeyError, ValueError):
        raise MonitorUnavailable('Host memory counters unavailable') from None
    sample={'total_bytes': total, 'available_bytes': available, 'trial_limit_bytes': LIMIT}
    if available < total / 10 + (LIMIT if loading else 0):
        raise MemoryFloor(sample)
    return sample

def latch_resource_failure(record, failure):
    if not re.fullmatch(r'[0-9a-f]{24}',record['owner']):raise Refused('Invalid failure owner')
    value={**{k:record[k] for k in ('owner','name','container_id','image_id')},'first_observed_failure':failure}
    try:write_json(STATE/('observed-resource-failure-'+record['owner']+'.json'),value)
    except FileExistsError:pass

def include_latched_failure(record, value):
    path=STATE/('observed-resource-failure-'+record['owner']+'.json')
    if path.exists():
        failure=read_json(path)
        if not same_trial(record,failure):raise Refused('Resource failure belongs to another candidate')
        value={**value,'qualification_failed':True,'first_observed_failure':failure['first_observed_failure']}
        sample=failure['first_observed_failure'].get('memory')
        if sample is not None:
            minimum=value.get('minimum_host_available_bytes')
            value['minimum_host_available_bytes']=min(minimum,sample['available_bytes']) if minimum is not None else sample['available_bytes']
    return value

def emergency_reason(record):
    try:
        memory()
    except MonitorUnavailable:
        latch_resource_failure(record,{'reason':'monitor_unavailable','monotonic':time.monotonic(),'memory':None})
        return 'monitor_unavailable'
    except MemoryFloor as error:
        latch_resource_failure(record,{'reason':'host_memory_floor','monotonic':time.monotonic(),'memory':error.sample})
        return 'host_memory_floor'
    if record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise Refused('Trial monotonic clock belongs to another boot')
    now = time.monotonic()
    if now >= record['trial_deadline_monotonic']:
        return 'trial_deadline'
    if record.get('request_deadline_monotonic') is not None and now >= record['request_deadline_monotonic']:
        return 'request_deadline'
    if record['phase'] in ('loading','awaiting_guard') and now >= record['loading_deadline_monotonic']:
        return 'startup_deadline'
    return None


def terminate_owned(record, *, loading=False):
    actual = owned_container(record)
    if actual['State']['Running']:
        if not loading and busy(health(record, require_ready=False)):
            raise Refused('Shadow request is active; stop refused without signalling')
        command(['docker', 'kill', '--signal', 'TERM', actual['Id']])
        for _ in range(60):
            actual = owned_container(record)
            if not actual['State']['Running']:
                break
            time.sleep(1)
        else:
            raise Refused('Cooperative shutdown incomplete; no force kill was attempted')
    command(['docker', 'rm', actual['Id']])
    return {'status': 'stopped', 'container_id': actual['Id'], 'force_kill': False}


def emergency_abort(record):
    reason = emergency_reason(record)
    if reason is None:
        raise Refused('No observed resource or recorded deadline emergency; use idle stop')
    actual = owned_container(record)
    forced = False
    if actual['State']['Running']:
        command(['docker', 'kill', '--signal', 'TERM', actual['Id']])
        for _ in range(10):
            actual = owned_container(record)
            if not actual['State']['Running']:
                break
            time.sleep(1)
        if actual['State']['Running']:
            # A blocked CUDA forward cannot honor a cooperative Python guard.
            command(['docker', 'kill', '--signal', 'KILL', actual['Id']])
            forced = True
            actual = owned_container(record)
            if actual['State']['Running']:
                raise Refused('Candidate emergency termination not confirmed')
    command(['docker', 'rm', actual['Id']])
    result = {'status': 'failed_emergency', 'reason': reason, 'container_id': actual['Id'],
              'force_kill': forced, 'partial_output_valid': False}
    write_json(STATE / ('emergency-' + secrets.token_hex(8) + '.json'), result)
    remove_active(record)
    return result



def monitor_sample(info, previous=None):
    previous=previous or {}
    minimum=previous.get('minimum_host_available_bytes')
    failure=previous.get('first_observed_failure')
    sample=None;reason=None
    try:sample=memory()
    except MemoryFloor as error:sample=error.sample;reason='host_memory_floor'
    except Refused:reason='monitor_unavailable'
    if sample is not None:
        minimum=min(minimum,sample['available_bytes']) if minimum is not None else sample['available_bytes']
    if reason is not None and failure is None:
        failure={'reason':reason,'monotonic':time.monotonic(),'memory':sample}
    return {**info,'last_sample_monotonic':time.monotonic(),'minimum_host_available_bytes':minimum,
            'qualification_failed':failure is not None,'first_observed_failure':failure}


def guard():
    """Foreground companion, required while the private trial submits requests."""
    record = read_json(STATE / 'active.json')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=record['helper_sha256']:raise Refused('Guard helper identity changed')
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    owned_container(record)
    try:alive_guard(record)
    except (Refused,OSError,KeyError):pass
    else:raise Refused('A verified continuous guard is already active')
    info={**{k:record[k] for k in ('owner','name','container_id','image_id')},'pid':os.getpid(),
          'start_ticks':Path('/proc/self/stat').read_text().split()[21],'helper_sha256':record['helper_sha256']}
    previous=None;announced=False
    try:
        prior=read_json(STATE/'guard.json')
        if same_trial(record,prior):previous=prior
    except (OSError,Refused):pass
    while (STATE / 'active.json').exists():
        try:
            current = read_json(STATE / 'active.json')
        except FileNotFoundError:
            break
        if not same_trial(record, current):
            break
        previous=monitor_sample(info,previous)
        if previous['qualification_failed']:latch_resource_failure(record,previous['first_observed_failure'])
        previous=include_latched_failure(record,previous)
        write_json(STATE/'guard.json',previous,replace=True)
        if not announced:print(json.dumps({'guard_ready':True,**info}),flush=True);announced=True
        if emergency_reason(current):
            try:
                with lifecycle_lock():
                    current = read_json(STATE / 'active.json')
                    if not same_trial(record, current):
                        break
                    if emergency_reason(current):
                        emergency_abort(current)
                        raise Refused('Shadow trial aborted; discard any concurrent request result')
            except (BlockingIOError, FileNotFoundError):
                pass
        time.sleep(0.1)
    return {'status': 'guard_finished_after_cleanup'}


def request_mark(record, *, finish_id=None):
    if finish_id is None:
        if record['phase']!='ready':raise Refused('Native startup has not been verified')
        if alive_guard(record).get('qualification_failed') is not False:raise Refused('Observed resource failure invalidates this trial')
    if emergency_reason(record):
        raise Refused('Trial already exceeds a resource or deadline guard')
    if busy(health(record, require_ready=finish_id is None)):
        raise Refused('Request marker changes require an idle adapter')
    if finish_id is None:
        if record.get('request_id'):
            raise Refused('Previous request remains armed; no deadline extension allowed')
        record['request_id'] = secrets.token_hex(12)
        record['request_deadline_monotonic'] = time.monotonic() + 640
        result = {key: record[key] for key in ('request_id', 'request_deadline_monotonic')}
    else:
        if record.get('request_id') != finish_id:
            raise Refused('Request marker identity mismatch')
        del record['request_id']; del record['request_deadline_monotonic']
        result = {'request_id': finish_id, 'status': 'request_finished_idle'}
    write_json(STATE / 'active.json', record, replace=True)
    return result

def pins():
    value=read_json(ROOT/'pins.json')
    for key,pattern in [('source_revision',r'[0-9a-f]{40}'),('image_id',r'sha256:[0-9a-f]{64}'),('patcher_sha256',r'[0-9a-f]{64}')]:
        if not re.fullmatch(pattern,value.get(key,'')):raise Refused('Invalid frozen identity')
    if value.get('runtime_commit')!=RUNTIME or value.get('model_sha256')!=WEIGHTS or value.get('patch_version')!=PATCH:
        raise Refused('Unexpected diagnostic runtime/model')
    if value.get('helper_sha256')!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise Refused('Private helper differs from pins')
    observer_pins(value)
    return value

def verify_image(value):
    image=json.loads(command(['docker','image','inspect',value['image_id']]))[0]
    expected={'io.homecompute.source-revision':value['source_revision'],
              'io.homecompute.whisper-cpp.runtime-commit':RUNTIME,
              'io.homecompute.whisper-cpp.model-sha256':WEIGHTS,
              'io.homecompute.whisper-cpp.cuda-patch':PATCH,
              'io.homecompute.whisper-cpp.cuda-patcher-sha256':value['patcher_sha256']}
    if image['Id']!=value['image_id'] or image['Architecture']!='arm64' or any(image['Config']['Labels'].get(k)!=v for k,v in expected.items()):
        raise Refused('Actual image differs from frozen decoder audit pins')
    return image

def owned_container(record):
    owner=record.get('owner','')
    if not re.fullmatch(r'[0-9a-f]{24}',owner) or record.get('name')!='hc-whisper-nonspeech-hc17-'+owner:
        raise Refused('Invalid candidate ownership journal')
    cid=record.get('container_id')
    if cid and not re.fullmatch(r'[0-9a-f]{64}',cid):raise Refused('Invalid container ID')
    value=json.loads(command(['docker','container','inspect',cid or record['name']]))[0]
    host=value['HostConfig'];mounts=[x for x in value.get('Mounts',[]) if x['Type']!='tmpfs']
    observer=observer_pins(record)
    expected_mounts={(MODEL_PATH,'/models/ggml-large-v3.bin'),(observer['directory'],'/models/language-observer')}
    actual_mounts={(x.get('Source'),x.get('Destination')) for x in mounts}
    if (value['Name']!='/'+record['name'] or value['Image']!=record['image_id']
        or (cid and value['Id']!=cid) or value['Config']['Labels'].get(OWNER_LABEL)!=owner
        or value['Config']['User']!='10001:10001' or host['NetworkMode']!='none'
        or host.get('Memory')!=LIMIT or host.get('MemorySwap')!=LIMIT or host.get('NanoCpus')!=4000000000
        or host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False
        or host.get('PortBindings') or host.get('PidsLimit')!=128
        or host.get('LogConfig',{}).get('Type')!='none' or host.get('RestartPolicy',{}).get('Name')!='no'
        or 'ALL' not in (host.get('CapDrop') or []) or 'no-new-privileges' not in (host.get('SecurityOpt') or [])
        or len(mounts)!=2 or actual_mounts!=expected_mounts
        or any(x.get('Type')!='bind' or x.get('RW') is not False for x in mounts)
        or [x for x in value['Config'].get('Env',[]) if x.startswith('WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=')]!=['WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS=true']):
        raise Refused('Candidate identity or isolation differs')
    return value

def health(record,*,require_ready=True):
    value=json.loads(command(['docker','exec',record['container_id'],'python3','-I','-B','-c',HEALTH_PROGRAM],timeout=8))
    if (value.get('status')!='ok' or value.get('image_id')!=record['image_id']
        or value.get('homecompute_revision')!=record['source_revision']
        or value.get('runtime_commit')!=RUNTIME or value.get('model_sha256')!=WEIGHTS
        or value.get('runtime')!='whisper.cpp' or value.get('model_snapshot')!='c521a4b02f422512d734391fdf08bb08c0862f68'
        or value.get('strict_gpu_patch')!=PATCH or value.get('strict_gpu_patcher_sha256')!=record['patcher_sha256']
        or value.get('suffix_recovery_policy')!={'enabled':True,'version':'silero-v5.1.2-cpu-prob0.5-grace500ms-contiguous512ms-suffix-v1','vad_model_sha256':'29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf','requires':'verbose_json_with_timestamps','max_extra_requests_per_window':1}
        or value.get('language_boundary_policy')!={'enabled': True, 'version': 'whole-source-normalized-vad-1s-rms24dbfs-cap30db-pause128ms-adjacent-language-confidence0.5-v2', 'requires': 'verbose_json_with_timestamps_and_language_candidates', 'language_probe': 'native_detect_language_only', 'asr_audio': 'original_pcm'}
        or value.get('non_speech_policy')!={'enabled': True, 'version': 'exact-zero-or-normalized-vad0.5-native-no-speech0.6-v1', 'requires': 'verbose_json_with_timestamps', 'exact_zero_pcm': True, 'minimum_nonzero_seconds': 3, 'native_no_speech_minimum': 0.6, 'normalized_vad_maximum_exclusive': 0.5}
        or value.get('local_eot_ranking_policy')!={'enabled': True, 'version': 'raw-silero-same-segment-strictly-later-terminal-eot-v1', 'requires': 'verbose_json_with_timestamps_without_translation', 'native_vad_defaults': 'threshold0.5/minSpeech250ms/minSilence100ms/pad0', 'asr_audio': 'original_pcm', 'max_extra_asr_calls': 0}
        or value.get('independent_language_boundary_policy')!={'enabled':True,'version':'original-pcm-singleton-cut-confirmation-v2','observer_manifest_sha256':observer_pins(record)['files']['manifest.json']['sha256'],'requires':'confident_native_singleton_with_equal_flanking_languages','action':'veto_unconfirmed_existing_pair_of_cuts','asr_language_forced':False,'observer_device':'cpu'}
        or value.get('language_boundary_window_reflow_policy')!={'enabled':True,'version':'minimum-original-pcm-context-reflow-v1','requires':'applied_independent_singleton_veto','scope':'island_and_minimum_eligible_original_flank_context','prefers':'observed_pauses','decode_count_baseline':'pre_veto_layout','max_extra_primary_decodes':0}
        or value.get('compute_backend')!='cuda' or type(value.get('gpu_model_buffer_bytes')) is not int
        or value['gpu_model_buffer_bytes']<=0
        or any(type(value.get(k)) is not bool for k in ('inference_busy','native_inference_busy'))):
        raise Refused('Actual native/adapter health identity differs')
    return value

def busy(value):
    return value['inference_busy'] or value['native_inference_busy']

def trace(record):
    program="from pathlib import Path;import sys;p=Path('/tmp/whisper-window-trace.jsonl');assert not p.is_symlink();d=p.open('rb').read(8388609) if p.is_file() else b'';assert len(d)<=8388608;sys.stdout.buffer.write(d)"
    result=subprocess.run(['docker','exec',record['container_id'],'python3','-I','-B','-c',program],capture_output=True,timeout=10)
    if result.returncode:raise Refused('Bounded native trace unavailable')
    return {'bytes':len(result.stdout),'sha256':hashlib.sha256(result.stdout).hexdigest(),'base64':base64.b64encode(result.stdout).decode()}

def resource(record):
    actual=owned_container(record)
    program="from pathlib import Path;import json;r=Path('/sys/fs/cgroup');names=['memory.current','memory.peak','memory.max','memory.events','memory.swap.current','memory.swap.max'];print(json.dumps({n:(r/n).read_text() for n in names if (r/n).is_file()}))"
    values=json.loads(command(['docker','exec',actual['Id'],'python3','-I','-B','-c',program])) if actual['State']['Running'] else {}
    return {'container_id':actual['Id'],'image_id':actual['Image'],'running':actual['State']['Running'],
            'oom_killed':actual['State']['OOMKilled'],'restart_count':actual['RestartCount'],
            'cgroup':values,'memory':memory()}

def start():
    if (STATE/'active.json').exists():raise Refused('Existing candidate journal requires review/cleanup')
    value=pins();verify_image(value);before=memory(loading=True)
    owner=secrets.token_hex(12)
    record={**value,'owner':owner,'name':'hc-whisper-nonspeech-hc17-'+owner,'phase':'creating',
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'trial_deadline_monotonic':time.monotonic()+3600,'loading_deadline_monotonic':time.monotonic()+180,
            'memory_before':before,'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    entry="import os,secrets,time;from pathlib import Path\np='/tmp/diagnostic-api-key';f=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o400);os.write(f,secrets.token_hex(32).encode());os.close(f)\nend=time.monotonic()+180\nwhile not Path('/tmp/load-enabled').is_file():\n if time.monotonic()>end:raise SystemExit(1)\n time.sleep(.1)\nos.execvp('python3',['python3','-I','-B','/opt/homecompute/adapter.py'])"
    args=['docker','run','--detach','--name',record['name'],'--label',OWNER_LABEL+'='+owner,'--init',
          '--network','none','--read-only','--memory',str(LIMIT),'--memory-swap',str(LIMIT),'--cpus','4',
          '--pids-limit','128','--shm-size','128m','--log-driver','none','--restart','no','--cap-drop','ALL',
          '--security-opt','no-new-privileges','--user','10001:10001','--gpus','device=0',
          '--mount','type=bind,src='+MODEL_PATH+',dst=/models/ggml-large-v3.bin,readonly',
          '--mount','type=bind,src='+value['observer']['directory']+',dst=/models/language-observer,readonly',
          '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700','--entrypoint','python3']
    env={'NVIDIA_DRIVER_CAPABILITIES':'compute,utility','WHISPER_CPP_DEVICE':'cuda',
         'WHISPER_CPP_API_KEY_FILE':'/tmp/diagnostic-api-key','WHISPER_CPP_IMAGE_ID':value['image_id'],
         'WHISPER_CPP_MAX_AUDIO_SECONDS':'1200','WHISPER_CPP_MAX_UPLOAD_BYTES':'40000000',
         'WHISPER_CPP_UPLOAD_TIMEOUT_SECONDS':'30','WHISPER_CPP_NATIVE_TIMEOUT_SECONDS':'600',
         'WHISPER_CPP_NATIVE_THREADS':'4','WHISPER_CPP_TRACE_WINDOWS':'1',
         'WHISPER_CPP_RECOVER_TRAILING_SPEECH':'true','WHISPER_CPP_LANGUAGE_BOUNDARIES':'true',
         'WHISPER_CPP_FILTER_NON_SPEECH':'true','WHISPER_CPP_LOCAL_EOT_RANKING':'true',
         'WHISPER_CPP_VERIFY_LANGUAGE_ISLANDS':'true'}
    for key,item in env.items():args+=['--env',key+'='+item]
    args+=[value['image_id'],'-I','-B','-c',entry]
    record['runtime_env']=env;write_json(STATE/'active.json',record)
    try:
        record['container_id']=command(args);record['phase']='awaiting_guard';write_json(STATE/'active.json',record,replace=True)
        owned_container(record)
        return record
    except BaseException:
        try:record['cleanup']=emergency_abort(record) if emergency_reason(record) else terminate_owned(record,loading=True)
        except Exception:record['cleanup']={'status':'needs_operator_inspection','force_kill':False}
        write_json(STATE/('failed-start-'+owner+'.json'),record)
        if record['cleanup']['status'] in ('stopped','failed_emergency'):remove_active(record)
        raise

def alive_guard(record):
    value=read_json(STATE/'guard.json')
    if not same_trial(record,value) or value.get('helper_sha256')!=record['helper_sha256'] or time.monotonic()-value['last_sample_monotonic']>2:
        raise Refused('Continuous guard is not current')
    proc=Path('/proc')/str(value['pid'])
    if proc.stat().st_uid!=os.geteuid() or (proc/'stat').read_text().split()[21]!=value['start_ticks']:
        raise Refused('Continuous guard process identity changed')
    return include_latched_failure(record,value)

def release_load(record):
    if record['phase']!='awaiting_guard':raise Refused('Load was already released')
    if alive_guard(record).get('qualification_failed') is not False:raise Refused('Observed resource failure invalidates this trial')
    record['observer_artifact_preflight']=verify_observer_artifacts(record)
    if alive_guard(record).get('qualification_failed') is not False:raise Refused('Guard changed during artifact verification')
    if emergency_reason(record):raise Refused('Artifact verification crossed a recorded resource/deadline guard')
    memory(loading=True)
    program="import os;f=os.open('/tmp/load-enabled',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(f)"
    command(['docker','exec',record['container_id'],'python3','-I','-B','-c',program])
    record['phase']='loading';write_json(STATE/'active.json',record,replace=True)
    return {'status':'load_released_with_live_guard','container_id':record['container_id']}

def wait_ready():
    record=read_json(STATE/'active.json')
    while time.monotonic()<record['loading_deadline_monotonic']:
        if alive_guard(record).get('qualification_failed') is not False:raise Refused('Observed resource failure invalidates this trial')
        sample=memory();actual=owned_container(record)
        if not actual['State']['Running']:
            write_json(STATE/('failed-load-'+record['owner']+'.json'),{'state':{k:actual['State'].get(k) for k in ('Status','ExitCode','OOMKilled','Error')},'container_id':actual['Id']})
            raise Refused('Native candidate exited during guarded load')
        try:value=health(record)
        except Refused:time.sleep(.25);continue
        if busy(value):raise Refused('Unexpected inference during load')
        with lifecycle_lock():
            current=read_json(STATE/'active.json')
            if not same_trial(record,current):raise Refused('Candidate changed during load')
            current.update(phase='ready',health=value,memory_loaded=sample,monitor=alive_guard(record))
            write_json(STATE/'active.json',current,replace=True)
            write_json(STATE/('ready-'+record['owner']+'.json'),current)
            return current
    raise Refused('Bounded guarded startup deadline exceeded')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('start','release-load','wait-ready','status','trace','stop','guard','guard-status','request-start','request-end','emergency-abort'));parser.add_argument('--request-id');args=parser.parse_args()
    if os.geteuid()==0:raise Refused('Run as ordinary Docker-authorized user')
    private_directory(STATE)
    if args.action=='guard':print(json.dumps(guard()));return
    if args.action=='wait-ready':print(json.dumps(wait_ready()));return
    with lifecycle_lock():
        if args.action=='start':result=start()
        else:
            record=read_json(STATE/'active.json');actual=owned_container(record)
            if args.action=='release-load':result=release_load(record)
            elif args.action=='guard-status':result=alive_guard(record)
            elif args.action=='status':
                result=resource(record);result['monitor']=alive_guard(record)
                if actual['State']['Running']:result['health']=health(record)
            elif args.action=='trace':result=trace(record)
            elif args.action=='request-start':result=request_mark(record)
            elif args.action=='request-end':
                if not args.request_id:raise Refused('Exact request marker required')
                result=request_mark(record,finish_id=args.request_id)
            elif args.action=='emergency-abort':result=emergency_abort(record)
            else:
                loading=record['phase'] in ('creating','awaiting_guard','loading') and not record.get('request_id')
                if actual['State']['Running'] and not loading:
                    if busy(health(record)):raise Refused('Both native and adapter must be idle before cleanup')
                    write_json(STATE/('final-trace-'+record['owner']+'.json'),trace(record))
                result=terminate_owned(record,loading=loading);remove_active(record)
        if args.action!='trace':write_json(STATE/(args.action+'-'+secrets.token_hex(8)+'.json'),result)
        print(json.dumps(result))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'refused','failure_class':type(error).__name__}));raise SystemExit(1) from None
