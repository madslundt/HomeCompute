#!/usr/bin/env python3
"""Source-owned, user-scoped Parakeet shadow lifecycle; never production routing."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/parakeet-shadow'
STATE = Path.home() / '.local/share/homecompute/parakeet-shadow'
GIB = 1024 ** 3
LIMIT = 6 * GIB
OWNER_LABEL = 'io.homecompute.parakeet.owner'
SOURCE_LABEL = 'io.homecompute.source-revision'
IMAGE_PATTERN = r'sha256:[0-9a-f]{64}'
KEY_PROGRAM = """import os,secrets
p='/tmp/shadow.key'
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f: f.write(secrets.token_hex(32))
"""
HEALTH_PROGRAM = """import urllib.request,urllib.error
try: r=urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)
except urllib.error.HTTPError as error:
 if error.code!=503: raise SystemExit(1)
 r=error
with r:
 b=r.read(16385)
 if len(b)>16384: raise SystemExit(1)
 print(b.decode())
"""


class Refused(RuntimeError): pass
class MonitorUnavailable(Refused): pass


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


def published_source(revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision or ''):
        raise Refused('A full published source revision is required')
    if command(['git', 'rev-parse', 'HEAD']) != revision or command(['git', 'status', '--porcelain']):
        raise Refused('Use the clean checkout of the requested source revision')
    advertised = command(['git', 'ls-remote', 'https://github.com/madslundt/HomeCompute.git',
                          'refs/heads/codex/parakeet-shadow'])
    if advertised.split() != [revision, 'refs/heads/codex/parakeet-shadow']:
        raise Refused('Source must be the published shadow branch head')


def memory(*, loading=False):
    try:
        fields = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB$',
                                 Path('/proc/meminfo').read_text(), re.M))
        total, available = (int(fields[key]) * 1024 for key in ('MemTotal', 'MemAvailable'))
        if not 0 <= available <= total or total <= 0:
            raise ValueError
    except (OSError, KeyError, ValueError):
        raise MonitorUnavailable('Host memory counters unavailable') from None
    if available < total / 10 + (LIMIT if loading else 0):
        raise Refused('Host memory guard refused the shadow trial')
    return {'total_bytes': total, 'available_bytes': available, 'trial_limit_bytes': LIMIT}


def emergency_reason(record):
    try:
        memory()
    except MonitorUnavailable:
        return 'monitor_unavailable'
    except Refused:
        return 'host_memory_floor'
    if record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise Refused('Trial monotonic clock belongs to another boot')
    now = time.monotonic()
    if now >= record['trial_deadline_monotonic']:
        return 'trial_deadline'
    if record.get('request_deadline_monotonic') is not None and now >= record['request_deadline_monotonic']:
        return 'request_deadline'
    if record['phase'] == 'loading' and now >= record['loading_deadline_monotonic']:
        return 'startup_deadline'
    return None


def manifest():
    return json.loads((SOURCE / 'model-manifest.json').read_text())


def artifacts():
    spec = importlib.util.spec_from_file_location('shadow_artifacts', Path(__file__).with_name('parakeet-shadow-artifacts.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def require_no_active():
    if (STATE / 'active.json').exists() or (STATE / 'active.json').is_symlink():
        raise Refused('An active shadow journal already exists; inspect/stop it first')


def expected_labels(revision, model):
    return {SOURCE_LABEL: revision, 'org.opencontainers.image.revision': revision,
            'io.homecompute.parakeet.scope': 'isolated-shadow-only',
            'io.homecompute.parakeet.model-snapshot': model['model_snapshot'],
            'io.homecompute.parakeet.model-sha256': model['model_sha256'],
            'io.homecompute.parakeet.runtime-commit': model['runtime_commit']}


def verify_image(image_id, revision, model):
    if not re.fullmatch(IMAGE_PATTERN, image_id or ''):
        raise Refused('An immutable image ID is required')
    value = json.loads(command(['docker', 'image', 'inspect', image_id]))[0]
    labels = value['Config'].get('Labels') or {}
    if (value['Id'] != image_id or value['Architecture'] != 'arm64'
            or value['Config']['User'] != '10001:10001'
            or any(labels.get(key) != item for key, item in expected_labels(revision, model).items())):
        raise Refused('Actual image does not match the pinned shadow source/model')
    return value['Id']


def build(revision, *, allow_base_pull=False):
    require_no_active()
    model = manifest(); memory(loading=True)
    try:
        raw = command(['docker', 'image', 'inspect', model['base_manifest']])
    except Refused:
        if not allow_base_pull:
            raise Refused('Pinned base is not cached; explicit --allow-base-pull is required') from None
        command(['docker', 'pull', '--platform', 'linux/arm64', model['base_manifest']], timeout=1800)
        raw = command(['docker', 'image', 'inspect', model['base_manifest']])
    base = json.loads(raw)[0]
    if base['Id'] != model['base_arm64_image'] or base['Architecture'] != 'arm64':
        raise Refused('Pinned ARM64 base image differs')
    tag = 'homecompute/parakeet-shadow:' + revision
    log_path = STATE / ('build-' + revision + '-' + secrets.token_hex(4) + '.log')
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as log:
        command(['docker', 'build', '--platform', 'linux/arm64', '--memory', str(LIMIT),
                 '--memory-swap', str(LIMIT), '--cpu-period', '100000', '--cpu-quota', '400000', '--build-arg',
                 'HOME_COMPUTE_REVISION=' + revision, '-t', tag, str(SOURCE)], timeout=3600, log=log,
                env={**os.environ, 'DOCKER_BUILDKIT': '0'})
    image_id = json.loads(command(['docker', 'image', 'inspect', tag]))[0]['Id']
    verify_image(image_id, revision, model)
    return {'source_revision': revision, 'image_id': image_id, 'build_log': log_path.name}


def run_arguments(record, model_path):
    args = ['docker', 'run', '--detach', '--name', record['name'], '--label', OWNER_LABEL + '=' + record['owner'],
            '--network', 'none', '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', str(LIMIT), '--memory-swap', str(LIMIT),
            '--cpus', '4', '--pids-limit', '128', '--shm-size', '64m', '--log-driver', 'none',
            '--restart', 'no', '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=67108864,uid=10001,gid=10001,mode=0700',
            '--mount', 'type=bind,src=' + str(model_path) + ',dst=/models,readonly',
            '--env', 'SHADOW_MODEL_DIR=/models', '--env', 'SHADOW_KEY_FILE=/tmp/shadow.key',
            '--env', 'SHADOW_DEVICE=' + record['device'], '--env', 'SHADOW_IMAGE_ID=' + record['image_id']]
    if record['device'] == 'cuda':
        args += ['--gpus', 'device=0']
    return args + [record['image_id']]


def owned_container(record):
    if (not re.fullmatch(r'[0-9a-f]{24}', record.get('owner', ''))
            or record.get('name') != 'hc-parakeet-shadow-' + record['owner']
            or (record.get('container_id') and not re.fullmatch(r'[0-9a-f]{64}', record['container_id']))):
        raise Refused('Invalid shadow container journal')
    value = json.loads(command(['docker', 'container', 'inspect', record.get('container_id') or record['name']]))[0]
    host = value['HostConfig']
    mounts = [item for item in value.get('Mounts', []) if item['Type'] != 'tmpfs']
    expected_model = str(STATE / ('model-' + manifest()['model_snapshot']))
    if (value['Name'] != '/' + record['name'] or value['Image'] != record['image_id']
            or (record.get('container_id') and value['Id'] != record['container_id'])
            or (value['Config'].get('Labels') or {}).get(OWNER_LABEL) != record['owner']
            or host['NetworkMode'] != 'none' or value['Config']['User'] != '10001:10001'
            or host.get('Memory') != LIMIT or host.get('MemorySwap') != LIMIT
            or host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False
            or host.get('PidsLimit') != 128 or host.get('LogConfig', {}).get('Type') != 'none'
            or host.get('PortBindings') or 'ALL' not in (host.get('CapDrop') or [])
            or 'no-new-privileges' not in (host.get('SecurityOpt') or [])
            or host.get('RestartPolicy', {}).get('Name') != 'no'
            or len(mounts) != 1 or mounts[0].get('Type') != 'bind'
            or mounts[0].get('Source') != expected_model or mounts[0].get('Destination') != '/models'
            or mounts[0].get('RW') is not False):
        raise Refused('Container identity does not match this isolated shadow journal')
    return value


def health(record, *, require_ready=True):
    value = json.loads(command(['docker', 'exec', record['container_id'], 'python3', '-I', '-B', '-c', HEALTH_PROGRAM], timeout=8))
    model = manifest(); identity = value.get('identity', {})
    expected = {key: model[key] for key in ('model_repository', 'model_snapshot', 'model_sha256', 'runtime', 'runtime_commit', 'dtype')}
    expected['device'] = 'cuda:0' if record['device'] == 'cuda' else 'cpu'
    if (value.get('status') not in (('ready',) if require_ready else ('ready', 'unavailable')) or type(value.get('inference_busy')) is not bool
            or value.get('image_id') != record['image_id'] or value.get('homecompute_revision') != record['source_revision']
            or any(identity.get(key) != item for key, item in expected.items())
            or identity.get('package_versions') != model['packages']
            or type(identity.get('parameter_bytes')) is not int or identity['parameter_bytes'] <= 0):
        raise Refused('Loaded shadow runtime identity/readiness differs')
    return value


def terminate_owned(record, *, loading=False):
    actual = owned_container(record)
    if actual['State']['Running']:
        if not loading and health(record, require_ready=False)['inference_busy']:
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


def guard():
    """Foreground companion, required while the private trial submits requests."""
    record = read_json(STATE / 'active.json')
    owned_container(record)
    while (STATE / 'active.json').exists():
        try:
            current = read_json(STATE / 'active.json')
        except FileNotFoundError:
            break
        if not same_trial(record, current):
            break
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
    if emergency_reason(record):
        raise Refused('Trial already exceeds a resource or deadline guard')
    if health(record, require_ready=finish_id is None)['inference_busy']:
        raise Refused('Request marker changes require an idle adapter')
    if finish_id is None:
        if record.get('request_id'):
            raise Refused('Previous request remains armed; no deadline extension allowed')
        record['request_id'] = secrets.token_hex(12)
        record['request_deadline_monotonic'] = time.monotonic() + 630
        result = {key: record[key] for key in ('request_id', 'request_deadline_monotonic')}
    else:
        if record.get('request_id') != finish_id:
            raise Refused('Request marker identity mismatch')
        del record['request_id']; del record['request_deadline_monotonic']
        result = {'request_id': finish_id, 'status': 'request_finished_idle'}
    write_json(STATE / 'active.json', record, replace=True)
    return result


def start(revision, image_id, device, trial_seconds=3600):
    require_no_active()
    model = manifest(); verify_image(image_id, revision, model)
    model_path = STATE / ('model-' + model['model_snapshot'])
    files = artifacts().verify(model_path, SOURCE, model)
    before = memory(loading=True)
    owner = secrets.token_hex(12)
    record = {'name': 'hc-parakeet-shadow-' + owner, 'owner': owner, 'source_revision': revision,
              'image_id': image_id, 'device': device, 'model': files, 'memory_before': before, 'phase': 'creating'}
    record.update(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  trial_deadline_monotonic=time.monotonic() + trial_seconds,
                  loading_deadline_monotonic=time.monotonic() + 300)
    write_json(STATE / 'active.json', record)
    try:
        record['container_id'] = command(run_arguments(record, model_path))
        record['phase'] = 'loading'; write_json(STATE / 'active.json', record, replace=True)
        owned_container(record)
        command(['docker', 'exec', record['container_id'], 'python3', '-I', '-B', '-c', KEY_PROGRAM])
        deadline = record['loading_deadline_monotonic']
        while time.monotonic() < deadline:
            record['memory_loaded'] = memory()
            if not owned_container(record)['State']['Running']:
                raise Refused('Shadow exited during loading; retain the lifecycle failure receipt')
            try:
                ready = health(record)
            except Refused:
                time.sleep(1); continue
            if ready['inference_busy']:
                raise Refused('Unexpected inference during startup')
            record['health'] = ready; record['phase'] = 'ready'
            write_json(STATE / 'active.json', record, replace=True)
            return record
        raise Refused('Bounded shadow startup deadline exceeded')
    except BaseException:
        # No request has been admitted by this launcher; loading can be cooperatively aborted.
        try:
            record['cleanup'] = emergency_abort(record) if emergency_reason(record) else terminate_owned(record, loading=True)
        except (Refused, KeyError, ValueError):
            record['cleanup'] = {'status': 'needs_operator_inspection', 'force_kill': False}
        write_json(STATE / ('failed-start-' + owner + '.json'), record)
        if record['cleanup']['status'] in ('stopped', 'failed_emergency'):
            remove_active(record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('provision', 'build', 'start', 'status', 'stop', 'guard', 'emergency-abort', 'request-start', 'request-end'))
    parser.add_argument('--revision'); parser.add_argument('--image-id')
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--trial-seconds', type=int, default=3600)
    parser.add_argument('--allow-base-pull', action='store_true')
    parser.add_argument('--request-id')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise Refused('Run as the ordinary Docker-authorized user; no sudo lifecycle')
    private_directory(STATE)
    if not 60 <= args.trial_seconds <= 7200:
        raise Refused('Trial deadline must be between 60 and 7200 seconds')
    if args.action == 'guard':
        print(json.dumps(guard())); return
    with lifecycle_lock():
        if args.action in ('provision', 'build'):
            require_no_active()
        if args.action in ('provision', 'build', 'start'):
            published_source(args.revision)
        if args.action == 'provision':
            result = artifacts().provision(STATE, SOURCE, manifest())
        elif args.action == 'build':
            result = build(args.revision, allow_base_pull=args.allow_base_pull)
        elif args.action == 'start':
            result = start(args.revision, args.image_id, args.device, args.trial_seconds)
        else:
            record = read_json(STATE / 'active.json'); actual = owned_container(record)
            if args.action == 'status':
                result = {'container_id': actual['Id'], 'running': actual['State']['Running'],
                          'image_id': actual['Image'], 'memory': memory()}
                if actual['State']['Running']:
                    result['health'] = health(record, require_ready=False)
            elif args.action == 'emergency-abort':
                result = emergency_abort(record)
            elif args.action == 'request-start':
                result = request_mark(record)
            elif args.action == 'request-end':
                if not args.request_id:
                    raise Refused('Finishing a request requires its exact marker ID')
                result = request_mark(record, finish_id=args.request_id)
            else:
                result = terminate_owned(record)
                remove_active(record)
        write_json(STATE / (args.action + '-' + secrets.token_hex(8) + '.json'), result)
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never echo external command errors, URLs, environment or credentials.
        print(json.dumps({'status': 'refused', 'failure_class': type(error).__name__}))
        raise SystemExit(1) from None
