#!/usr/bin/env python3
"""Guarded Large-v3 replacement; preserve stopped STT containers for rollback.

Run only from a clean published release. This lifecycle does not modify TTS,
text engines, recordings, credentials, host release pointers or capacity receipts.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import signal
import shutil
import socket
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/whisper-stt')
OLD = ('gb10-hviske-http', 'gb10-whisper-http', 'gb10-hviske-primary', 'gb10-whisper-primary')
NEW = ('gb10-whisper-primary', 'gb10-whisper-http')
CANDIDATE = 'gb10-whisper-candidate'
CANDIDATE_IMAGE = 'homecompute/whisper-vllm-candidate:0.19.0-hc1'
REVISION = '06f233fe06e710322aca913c1bc4249a0d71fce1'
MODEL = 'openai/whisper-large-v3'
LEGACY_REPOSITORIES = ('syvai/hviske-v5.3', 'openai/whisper-large-v3-turbo')


def run(args, *, timeout=60, check=True, environment=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=environment)
    if check and result.returncode:
        raise RuntimeError('Managed command failed: ' + args[0])
    return result


def protected(path, directory=False):
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Untrusted managed path')
    if directory and not path.is_dir():
        raise ValueError('Expected managed directory')


def published(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise ValueError('Run from the selected immutable release')
    for path in [*reversed(ROOT.parents), ROOT]:
        protected(path, True)
    if (run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip() != revision or
            run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']).stdout.strip()):
        raise ValueError('Dirty or mismatched release')
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])


def save(value):
    target = STATE / 'transaction.json'
    temporary = STATE / 'transaction.tmp'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Incomplete journal write requires inspection')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def inspect(name):
    result = run(['docker', 'inspect', name], check=False)
    if result.returncode:
        # Distinguish an absent container from unavailable Docker.
        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
        if not any(reason in result.stderr.lower() for reason in ('no such object', 'no such container')):
            raise ValueError('Container state is unavailable')
        return None
    return json.loads(result.stdout)[0]


def compose(*arguments, timeout=1200):
    identity = pwd.getpwnam('gb10-ai')
    environment = {**os.environ, 'GB10_ROOT': '/srv/gb10-ai',
                   'GB10_RUNTIME_UID': str(identity.pw_uid), 'GB10_RUNTIME_GID': str(identity.pw_gid),
                   'VLLM_API_KEY_FILE': '/etc/gb10-ai/secrets/vllm_api_key'}
    return run(['docker', 'compose', '--project-name', 'whisper-large-v3-stt',
                '-f', str(ROOT / 'deploy/whisper-stt/compose.yaml'),
                *arguments], timeout=timeout, environment=environment)


def guards():
    for path in (Path('/var/lib/homecompute-speech-capacity/active.json'),
                 Path('/var/lib/homecompute-speech-finalization/active.json')):
        if path.exists() or path.is_symlink():
            raise ValueError('Reconcile the active speech capacity candidate through its owning lifecycle first')
    if list(Path('/var/lib/homecompute-docker-maintenance').glob('*.active')):
        raise ValueError('Reconcile Docker maintenance overrides first')
    for path in Path('/var/lib/homecompute-speech-finalization').glob('*/journal.json'):
        protected(path)
        if json.loads(path.read_text()).get('phase') != 'completed':
            raise ValueError('Recover the pending speech finalization transaction first')


def prepare():
    guards()
    print('Preparing pinned full Whisper cache, relay, and opt-in runtime candidate; GPU services remain unchanged', flush=True)
    for path in ('/srv/gb10-ai/cache/huggingface', '/srv/gb10-ai/manifests'):
        if Path(path).is_symlink() or not Path(path).is_dir():
            raise ValueError('Existing cache and manifest directories are required')
    compose('--profile', 'prepare', 'run', '--rm', 'whisper-fetch', timeout=3600)
    compose('--profile', 'prepare', 'run', '--rm', 'whisper-accept')
    compose('build', 'whisper-http')
    print('Whisper cache accepted and relay built', flush=True)


def prepare_runtime_candidate():
    guards()
    cache = Path('/srv/gb10-ai/manifests/accepted-whisper-large-v3-cache.json')
    protected(cache)
    manifest = json.loads(cache.read_text())
    if manifest.get('repo_id') != MODEL or manifest.get('revision') != REVISION:
        raise ValueError('Accepted full Whisper cache is required before candidate preparation')
    compose('--profile', 'candidate', 'build', 'whisper-candidate')
    candidate_image_id()
    print('Pinned Whisper runtime candidate image built and verified; no model was started', flush=True)


def gate(chain, install):
    if not re.fullmatch('HC-STT-[0-9a-f]{12}', chain):
        raise ValueError('Invalid intake gate')
    if install:
        run(['iptables', '-w', '-N', chain])
        for port in ('8003', '8007'):
            # conntrack retains the requested host address/port after Docker DNAT.
            run(['iptables', '-w', '-A', chain, '-p', 'tcp', '-m', 'conntrack', '--ctstate', 'NEW',
                 '--ctorigdst', '127.0.0.1', '--ctorigdstport', port, '-j', 'REJECT'])
        run(['iptables', '-w', '-I', 'OUTPUT', '1', '-j', chain])
    else:
        # Delete only our exact nonce-named chain and jump; never flush a shared chain.
        if run(['iptables', '-w', '-S', chain], check=False).returncode:
            return
        rules = run(['iptables', '-w', '-S', chain]).stdout.splitlines()
        if len(rules) != 3 or any(not all(token in rule for token in
                ('--ctstate NEW', '--ctorigdst 127.0.0.1', '--ctorigdstport ' + port, '-j REJECT'))
                for rule, port in zip(rules[1:], ('8003', '8007'))):
            raise ValueError('Intake gate drift requires inspection')
        run(['iptables', '-w', '-D', 'OUTPUT', '-j', chain], check=False)
        run(['iptables', '-w', '-F', chain]); run(['iptables', '-w', '-X', chain])


def quiet(containers):
    probe = '''import json,urllib.request
raw=urllib.request.urlopen('http://127.0.0.1:8000/metrics',timeout=10).read().decode()
rows=[float(line.split()[-1]) for line in raw.splitlines()
      if line.startswith(('vllm:num_requests_running{','vllm:num_requests_waiting{'))]
assert len(rows)>=2
print(json.dumps({'idle':all(v==0 for v in rows)}))
'''
    stable = 0
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        idle = True
        for item in containers:
            if item['State']['Running'] and item['Name'].endswith(('-primary', '-candidate')):
                result = run(['docker', 'exec', item['Id'], 'python3', '-c', probe])
                idle = idle and json.loads(result.stdout) == {'idle': True}
        connections = run(['ss', '-Htn', 'state', 'established', '( sport = :8003 or sport = :8007 )']).stdout
        stable = stable + 1 if idle and not connections.strip() else 0
        if stable >= 3:
            return
        time.sleep(1)
    raise ValueError('STT requests did not drain; no model may be replaced')


def candidate_image_id():
    result = run(['docker', 'image', 'inspect', CANDIDATE_IMAGE])
    image = json.loads(result.stdout)[0]
    labels = image.get('Config', {}).get('Labels') or {}
    if (labels.get('io.homecompute.whisper-runtime-patch') != '0.19.0-hc1' or
            labels.get('io.homecompute.whisper-runtime-patcher-sha256') !=
            'fed6ae01930b2689fbfdcb039ffdb024778f84dc67ba61d0d84fb7fc54935c74' or
            labels.get('io.homecompute.whisper-source-prompt-sha256') !=
            '44d196b1d3ec3182de51f4b5f87764a33a1b58c83e6afb5e452f52f4bb653dcf' or
            labels.get('io.homecompute.whisper-source-serving-sha256') !=
            '7b9b47511ba3643ccae14ae8a4095153959a35a671eb3cc07707dfd1f10ebfa3' or
            labels.get('org.opencontainers.image.base.name') != \
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Patched candidate image identity or base pin differs')
    return image['Id']


def candidate_rollback(tx):
    candidate = inspect(CANDIDATE)
    if candidate:
        labels = candidate.get('Config', {}).get('Labels') or {}
        if (candidate['Image'] != tx['candidate_image_id'] or
                labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or
                labels.get('com.docker.compose.service') != 'whisper-candidate'):
            raise ValueError('Candidate container ownership changed; rollback held')
        if candidate['State']['Running']:
            run(['docker', 'update', '--restart', 'no', candidate['Id']])
            run(['docker', 'stop', '--time', '180', candidate['Id']], timeout=210)
        run(['docker', 'rm', candidate['Id']])
    original = inspect(tx['primary_id'])
    if not original or original['Image'] != tx['primary_image'] or \
            original['Name'] != '/gb10-whisper-primary':
        raise ValueError('Original Whisper primary changed or disappeared; rollback held')
    run(['docker', 'update', '--restart', tx['primary_restart'], tx['primary_id']])
    if not original['State']['Running']:
        run(['docker', 'start', tx['primary_id']])
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        current = inspect(tx['primary_id'])
        if current and current['State']['Running'] and \
                current.get('State', {}).get('Health', {}).get('Status') == 'healthy':
            tx['state'] = 'rolled-back'
            save_candidate(tx)
            return
        time.sleep(2)
    raise ValueError('Original Whisper primary did not become healthy; rollback is incomplete')


def candidate_start(revision):
    guards()
    base_tx_path = STATE / 'transaction.json'
    protected(base_tx_path)
    base_tx = json.loads(base_tx_path.read_text())
    if base_tx.get('state') != 'applied':
        raise ValueError('The full Whisper lifecycle must be applied before runtime qualification')
    primary = inspect(NEW[0])
    if (not primary or not primary['State']['Running'] or
            primary.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
            MODEL not in ' '.join(primary['Config'].get('Cmd') or []) or
            REVISION not in ' '.join(primary['Config'].get('Cmd') or []) or
            primary.get('Config', {}).get('Image') !=
            'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'):
        raise ValueError('Expected healthy pinned NVIDIA Whisper primary before candidate qualification')
    image_id = candidate_image_id()
    if inspect(CANDIDATE):
        raise ValueError('A candidate container already exists; inspect it before qualification')
    journal = STATE / 'runtime-candidate.json'
    if journal.exists() or journal.is_symlink():
        protected(journal)
        previous = json.loads(journal.read_text())
        if previous.get('state') != 'rolled-back':
            raise ValueError('Existing runtime candidate transaction requires rollback/status review')
        journal.rename(STATE / ('runtime-candidate-rolled-back-' + os.urandom(6).hex() + '.json'))
    chain = 'HC-STT-' + os.urandom(6).hex()
    tx = {'revision': revision, 'state': 'prepared', 'chain': chain, 'candidate_image_id': image_id,
          'primary_id': primary['Id'], 'primary_image': primary['Image'],
          'primary_restart': restart_policy(primary)}
    save_candidate(tx)
    try:
        gate(chain, True)
        quiet([primary])
        run(['docker', 'update', '--restart', 'no', primary['Id']])
        run(['docker', 'stop', '--time', '180', primary['Id']], timeout=210)
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) < 12 * 1024 * 1024:
            raise ValueError('Less than 12 GiB available before candidate model load')
        tx['state'] = 'primary-stopped'; save_candidate(tx)
        compose('--profile', 'candidate', 'up', '-d', '--no-build', '--pull', 'never', '--wait',
                '--wait-timeout', '900', 'whisper-candidate')
        candidate = inspect(CANDIDATE)
        if (not candidate or candidate['Image'] != image_id or not candidate['State']['Running'] or
                candidate.get('State', {}).get('Health', {}).get('Status') != 'healthy' or
                MODEL not in ' '.join(candidate['Config'].get('Cmd') or []) or
                REVISION not in ' '.join(candidate['Config'].get('Cmd') or []) or
                candidate.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') !=
                'whisper-large-v3-stt' or
                candidate.get('Config', {}).get('Labels', {}).get('com.docker.compose.service') !=
                'whisper-candidate'):
            raise ValueError('Patched candidate failed image, model or health verification')
        smoke(CANDIDATE)
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) / int(memory['MemTotal'].split()[0]) < 0.10:
            raise ValueError('Candidate failed the 10 percent host headroom gate')
        tx['state'] = 'candidate-running'; save_candidate(tx)
    except BaseException:
        candidate_rollback(tx)
        raise
    finally:
        gate(chain, False)
    print('Patched Whisper candidate active for qualification; run runtime-candidate-rollback afterward', flush=True)


def save_candidate(value):
    target = STATE / 'runtime-candidate.json'
    temporary = STATE / 'runtime-candidate.tmp'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Incomplete candidate journal write requires inspection')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def candidate_stop(revision):
    journal = STATE / 'runtime-candidate.json'; protected(journal)
    tx = json.loads(journal.read_text())
    if tx.get('revision') != revision or tx.get('state') == 'rolled-back':
        raise ValueError('No matching recoverable runtime candidate transaction')
    chain = tx['chain']
    gate(chain, True)
    try:
        candidate = inspect(CANDIDATE)
        if candidate and candidate['State']['Running']:
            quiet([candidate])
        candidate_rollback(tx)
    finally:
        gate(chain, False)


def smoke(container=NEW[0]):
    item = inspect(container)
    if not item or MODEL not in ' '.join(item['Config'].get('Cmd') or []) or \
            REVISION not in ' '.join(item['Config'].get('Cmd') or []):
        raise ValueError('Full Whisper model/pin is not installed')
    code = '''import io,json,urllib.request,wave
key=open('/run/secrets/vllm_api_key').read().strip()
audio=io.BytesIO()
with wave.open(audio,'wb') as w:
 w.setparams((1,2,16000,0,'NONE','PCM'));w.writeframes(b'\\0\\0'*16000)
body=(b'--smoke\\r\\nContent-Disposition: form-data; name="model"\\r\\n\\r\\nwhisper-large-v3\\r\\n'
      b'--smoke\\r\\nContent-Disposition: form-data; name="file"; filename="audio.wav"\\r\\nContent-Type: audio/wav\\r\\n\\r\\n'
      +audio.getvalue()+b'\\r\\n--smoke--\\r\\n')
r=urllib.request.Request('http://whisper-http:8007/v1/audio/transcriptions',body,
 headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary=smoke'})
d=json.load(urllib.request.urlopen(r,timeout=120));assert isinstance(d.get('text'),str)
print('Authenticated relay transcription passed; content withheld')
'''
    run(['docker', 'exec', container, 'python3', '-c', code], timeout=150)


def rollback(tx):
    # Only remove replacement containers owned by this lifecycle's exact release.
    for name in reversed(NEW):
        item = inspect(name)
        if item and item['Id'] not in {row['id'] for row in tx['old']}:
            labels = item['Config'].get('Labels', {})
            if labels.get('com.docker.compose.project') != 'whisper-large-v3-stt' or \
                    labels.get('com.docker.compose.project.config_files') != str(ROOT / 'deploy/whisper-stt/compose.yaml'):
                raise ValueError('Replacement ownership changed; rollback held')
            run(['docker', 'rm', '-f', item['Id']])
    for row in reversed(tx['old']):
        item = inspect(row['id'])
        if not item or item['Image'] != row['image']:
            raise ValueError('Retained rollback container changed or disappeared')
        if item['Name'].lstrip('/') != row['name']:
            run(['docker', 'rename', row['id'], row['name']])
        run(['docker', 'update', '--restart', row['restart'], row['id']])
        if row['running']:
            run(['docker', 'start', row['id']])
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if all(not row['running'] or inspect(row['id'])['State'].get('Health', {}).get('Status') == 'healthy'
               for row in tx['old']):
            break
        time.sleep(2)
    else:
        raise ValueError('Previous STT containers did not become healthy; rollback is incomplete')
    tx['state'] = 'rolled-back'; save(tx)


def apply(revision):
    guards()
    journal = STATE / 'transaction.json'
    if journal.exists():
        protected(journal)
        tx = json.loads(journal.read_text())
        if tx['revision'] == revision and tx['state'] == 'applied':
            smoke(); return
        if tx['state'] != 'rolled-back':
            raise ValueError('Existing transaction requires status/rollback review')
        if run(['iptables', '-w', '-C', 'OUTPUT', '-j', tx['chain']], check=False).returncode == 0:
            raise ValueError('Rollback intake gate still exists; recover before retrying')
        for row in tx['old']:
            current = inspect(row['id'])
            if (not current or current['Name'] != '/' + row['name'] or current['Image'] != row['image'] or
                    current['State']['Running'] != row['running'] or restart_policy(current) != row['restart'] or
                    row['running'] and current['State'].get('Health', {}).get('Status') != 'healthy'):
                raise ValueError('Previous rollback is not fully restored; retry refused')
        journal.rename(STATE / ('rolled-back-' + tx['revision'] + '-' + os.urandom(6).hex() + '.json'))
    containers = [inspect(name) for name in OLD]
    if not all(containers):
        raise ValueError('Expected both existing STT stacks; inspect drift before migration')
    for item in containers:
        command = ' '.join(item['Config'].get('Cmd') or [])
        if item['Name'].endswith('-primary') and not any(pin in command for pin in
                ('5d1a09822018702dc51d763e3a867b62d26b3501', '41f01f3fe87f28c78e2fbf8b568835947dd65ed9')):
            raise ValueError('Unexpected previous STT model pin')
    tx = {'revision': revision, 'state': 'prepared', 'chain': 'HC-STT-' + os.urandom(6).hex(),
          'old': [{'id': c['Id'], 'name': c['Name'].lstrip('/'), 'image': c['Image'],
                   'running': c['State']['Running'], 'restart': restart_policy(c)} for c in containers]}
    save(tx)
    try:
        print('Draining existing STT requests before replacement', flush=True)
        gate(tx['chain'], True)
        quiet(containers)
        tx['state'] = 'replacing'; save(tx)
        for row in tx['old']:
            run(['docker', 'update', '--restart', 'no', row['id']])
            if row['running']:
                run(['docker', 'stop', '--time', '180', row['id']], timeout=210)
            if row['name'] in NEW:
                run(['docker', 'rename', row['id'], row['name'] + '-before-large-v3-' + revision[:12]])
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) < 12 * 1024 * 1024:
            raise ValueError('Less than 12 GiB available after retiring old STT engines')
        compose('up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '900',
                'whisper-primary', 'whisper-http')
        smoke()
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(memory['MemAvailable'].split()[0]) / int(memory['MemTotal'].split()[0]) < 0.10:
            raise ValueError('Full Whisper failed the post-start 10 percent host headroom gate')
        for row in tx['old']:
            if inspect(row['id'])['State']['Running']:
                raise ValueError('Retired STT engine is still running')
        tx['state'] = 'verified'; save(tx)
    except BaseException:
        rollback(tx)
        raise
    finally:
        gate(tx['chain'], False)
    tx['state'] = 'applied'; save(tx)
    print('Full Whisper applied; previous STT containers retained stopped for rollback', flush=True)


def restart_policy(item):
    policy = item['HostConfig']['RestartPolicy']
    name = policy['Name'] or 'no'
    return name + (':' + str(policy['MaximumRetryCount']) if name == 'on-failure' and policy['MaximumRetryCount'] else '')


def retirement_paths():
    cache = Path('/srv/gb10-ai/cache/huggingface/hub')
    return [cache / ('models--' + repo.replace('/', '--')) for repo in LEGACY_REPOSITORIES] + [
        cache / '.locks' / ('models--' + repo.replace('/', '--')) for repo in LEGACY_REPOSITORIES
    ] + [Path('/srv/gb10-ai/cache/vllm/hviske')]


def retire(revision):
    """Explicit owner-requested deletion, independent of current inference intake."""
    guards()
    journal = STATE / 'transaction.json'; protected(journal)
    tx = json.loads(journal.read_text())
    if tx['state'] not in {'applied', 'retiring-legacy', 'legacy-retired'}:
        raise ValueError('Complete or recover the full Whisper migration first')
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', tx['revision'], revision])
    current = inspect(NEW[0])
    if (not current or not current['State']['Running'] or
            current['State'].get('Health', {}).get('Status') != 'healthy' or
            MODEL not in ' '.join(current['Config'].get('Cmd') or []) or
            REVISION not in ' '.join(current['Config'].get('Cmd') or []) or
            current['Config'].get('Labels', {}).get('com.docker.compose.project') != 'whisper-large-v3-stt'):
        raise ValueError('Healthy pinned full Whisper is required before deletion')
    targets = [dict(row) for row in tx['old']]
    adapter = inspect('gb10-hviske-wyoming')
    if adapter:
        labels = adapter['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != 'hviske-stt' or
                labels.get('com.docker.compose.service') != 'hviske-wyoming'):
            raise ValueError('Unknown legacy adapter ownership; deletion refused')
        targets.append({'id': adapter['Id'], 'image': adapter['Image'], 'name': 'gb10-hviske-wyoming'})
    for row in targets:
        actual = inspect(row['id'])
        if actual and (actual['State']['Running'] or actual['Image'] != row['image']):
            raise ValueError('Legacy container is running or changed; deletion refused')
    ids = run(['docker', 'ps', '-aq']).stdout.split()
    containers = json.loads(run(['docker', 'inspect', *ids]).stdout) if ids else []
    for item in containers:
        command = ' '.join(item['Config'].get('Cmd') or [])
        manifests = {Path(mount['Source']).name for mount in item.get('Mounts', [])}
        if (any(repo in command for repo in LEGACY_REPOSITORIES) or
                manifests & {'accepted-hviske-cache.json', 'accepted-whisper-turbo-cache.json'}):
            if item['Id'] not in {row['id'] for row in targets}:
                raise ValueError('Another container references a legacy model; deletion refused')
    paths = retirement_paths()
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Descriptor-safe directory removal is required')
    for path in paths:
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError('Legacy cache path changed type; deletion refused')
    manifests = [Path('/srv/gb10-ai/manifests') / name for name in
                 ('accepted-hviske-cache.json', 'accepted-whisper-turbo-cache.json')]
    for path, repository in zip(manifests, LEGACY_REPOSITORIES):
        if path.exists() or path.is_symlink():
            protected(path)
            if json.loads(path.read_text()).get('repo_id') != repository:
                raise ValueError('Legacy accepted manifest identity differs')
    if tx['state'] == 'legacy-retired':
        if any(inspect(row['id']) for row in targets) or any(path.exists() for path in paths + manifests):
            raise ValueError('Retired legacy assets reappeared; inspect drift')
        print('Hviske and Turbo remain removed'); return
    # Deletion cannot be rolled back without refetching. Journal before removing
    # anything; interruption resumes only these exact finite targets.
    tx.update(state='retiring-legacy', retirement_revision=revision); save(tx)
    for row in targets:
        if inspect(row['id']): run(['docker', 'rm', row['id']])
    for path in paths:
        if path.exists(): shutil.rmtree(path)
    for path in manifests:
        if path.exists(): path.unlink()
    for tag in ('homecompute/hviske-http:1.0.0', 'homecompute/stt-http:1.1.0'):
        # Never force-remove a shared image or touch the NVIDIA runtime used by
        # full Whisper. Docker refuses a still-referenced legacy image.
        run(['docker', 'image', 'rm', tag], check=False)
    tx.update(state='legacy-retired', rollback_containers_retained=False); save(tx)
    print(json.dumps({'state': tx['state'], 'retirement_revision': revision,
                      'removed_models': list(LEGACY_REPOSITORIES), 'full_whisper_running': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'prepare-runtime-candidate', 'apply', 'install', 'retire',
                                           'rollback', 'status', 'smoke', 'runtime-candidate',
                                           'runtime-candidate-rollback'))
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('Run through sudo on home-spark')
    published(args.revision)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700); protected(STATE, True)
    with ExitStack() as stack:
        for name in ('/var/lib/homecompute/home-spark-deploy.lock',
                     '/var/lib/homecompute-spark-rollout/lock',
                     '/var/lib/homecompute-speech-capacity/lock', str(STATE / 'lock')):
            Path(name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = stack.enter_context(open(name, 'a'))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: (_ for _ in ()).throw(InterruptedError('Interrupted')))
        if args.action == 'prepare': prepare()
        elif args.action == 'prepare-runtime-candidate': prepare_runtime_candidate()
        elif args.action == 'apply': apply(args.revision)
        elif args.action == 'install': prepare(); apply(args.revision)
        elif args.action == 'retire': retire(args.revision)
        elif args.action == 'smoke': smoke()
        elif args.action == 'runtime-candidate': candidate_start(args.revision)
        elif args.action == 'runtime-candidate-rollback': candidate_stop(args.revision)
        else:
            journal = STATE / 'transaction.json'; protected(journal)
            tx = json.loads(journal.read_text())
            if args.revision not in {tx['revision'], tx.get('retirement_revision')}:
                raise ValueError('Transaction belongs to another release')
            if args.action == 'rollback':
                if tx['state'] in {'retiring-legacy', 'legacy-retired'}:
                    raise ValueError('Owner retired legacy assets; historical rollback requires an explicit refetch')
                gate(tx['chain'], True) if tx['state'] == 'applied' else None
                try:
                    quiet([inspect(name) for name in NEW if inspect(name)])
                    rollback(tx)
                finally: gate(tx['chain'], False)
            blocked = run(['iptables', '-w', '-C', 'OUTPUT', '-j', tx['chain']], check=False).returncode == 0
            print(json.dumps({'revision': tx['revision'], 'state': tx['state'], 'model': MODEL,
                              'intake_blocked': blocked,
                              'model_revision': REVISION,
                              'rollback_containers_retained': tx['state'] not in {'retiring-legacy', 'legacy-retired'}}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('Whisper lifecycle refused: ' + str(error) if isinstance(error, ValueError)
              else 'Whisper lifecycle failed: ' + type(error).__name__, file=__import__('sys').stderr)
        raise SystemExit(1) from None
